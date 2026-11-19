"""Non-AI helpers: file text extraction, rate limiting, small utilities."""
import io
import os
import time
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from . import ai

ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.txt', '.md', '.png', '.jpg', '.jpeg', '.webp'}
IMAGE_MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
MAX_STORED_CHARS = 150000


def validate_upload(f):
    """Return an error string, or '' if the upload is acceptable."""
    ext = os.path.splitext(f.name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"Unsupported file type {ext or '(none)'}. Use PDF, DOCX, TXT, MD, PNG, JPG or WEBP."
    if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        return f'File is larger than {settings.MAX_UPLOAD_MB} MB.'
    return ''


def extract_text(f):
    """
    Pull text out of an uploaded file. Digital documents are parsed locally;
    scanned PDFs and images are transcribed by Gemini. Returns (text, note)
    where `note` is a warning to show the user, or ''.
    """
    ext = os.path.splitext(f.name)[1].lower()
    f.seek(0)
    raw = f.read()
    f.seek(0)
    text, note = '', ''

    try:
        if ext in ('.txt', '.md'):
            text = raw.decode('utf-8', errors='ignore')
        elif ext == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            text = '\n'.join((p.extract_text() or '') for p in reader.pages)
        elif ext == '.docx':
            import docx
            d = docx.Document(io.BytesIO(raw))
            parts = [p.text for p in d.paragraphs]
            for table in d.tables:
                for row in table.rows:
                    parts.append(' | '.join(c.text.strip() for c in row.cells))
            text = '\n'.join(parts)
    except Exception:
        text = ''
        note = 'Part of the file could not be read directly.'

    text = text.strip()
    needs_ocr = ext in IMAGE_MIME or (ext == '.pdf' and len(text) < 100)
    if needs_ocr:
        try:
            text = ai.extract_text_with_ai(raw, IMAGE_MIME.get(ext, 'application/pdf')).strip()
            note = ''
        except ai.AIError as e:
            note = f'Could not read text from this scan/image: {e}'
    return text[:MAX_STORED_CHARS], note


def ai_rate_limited(view):
    """Cap AI calls per user per minute and always answer with JSON errors."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        key = f'ai-rate:{request.user.pk}:{int(time.time() // 60)}'
        count = cache.get(key, 0) + 1
        cache.set(key, count, 90)
        if count > settings.AI_RATE_LIMIT_PER_MINUTE:
            return JsonResponse({'ok': False, 'error': 'You are going a bit fast. Wait a minute and try again.'}, status=429)
        try:
            return view(request, *args, **kwargs)
        except ai.AIError as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=502)
    return wrapper
