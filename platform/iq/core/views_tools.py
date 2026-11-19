"""
Learning hubs and tools: DSA / Engineering / Nepal licence / Jobs, resume maker,
presentation maker, summarizer, mock interview, assistant, avatar, feedback.
"""
import io
import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import ai, catalog
from .models import (Feedback, Presentation, Quiz, QuizQuestion, Resume, StudyMaterial, Subject,
                     TopicContent, TopicProgress, VideoLesson)
from .services import ai_rate_limited, extract_text, validate_upload
from .views import fail, get_visible_material, ok, to_int, visible_materials


def _json_payload(request, limit=200_000):
    raw = request.POST.get('payload', '')
    if len(raw) > limit:
        raise ValueError('Too much data.')
    try:
        return json.loads(raw or '{}')
    except ValueError:
        raise ValueError('Invalid data.')


# =================================================================== hubs
@login_required
def learn_index(request):
    done = {}
    for row in TopicProgress.objects.filter(user=request.user).values_list('track', flat=True):
        done[row] = done.get(row, 0) + 1
    tracks = [{**t, 'done': done.get(t['slug'], 0),
               'percent': round(done.get(t['slug'], 0) * 100 / t['topic_count'])} for t in catalog.TRACKS]
    return render(request, 'learn/index.html', {'tracks': tracks})


@login_required
def learn_track(request, track):
    t = catalog.get_track(track)
    if not t:
        raise Http404
    done = set(TopicProgress.objects.filter(user=request.user, track=track).values_list('topic', flat=True))
    cached = set(TopicContent.objects.filter(track=track, kind='notes').values_list('topic', flat=True))
    groups = [{'title': g['title'], 'topics': [{**i, 'done': i['slug'] in done, 'ready': i['slug'] in cached}
                                                for i in g['topics']]} for g in t['groups']]
    recent = Quiz.objects.filter(user=request.user, track=track).prefetch_related('attempts')[:5]
    return render(request, 'learn/track.html', {
        'track': t, 'groups': groups, 'done_count': len(done),
        'percent': round(len(done) * 100 / t['topic_count']), 'recent_quizzes': recent,
    })


@login_required
def learn_topic(request, track, topic):
    t, item = catalog.get_topic(track, topic)
    if not item:
        raise Http404
    contents = {c.kind: c for c in TopicContent.objects.filter(track=track, topic=topic)}
    flat = [i for g in t['groups'] for i in g['topics']]
    idx = next(n for n, i in enumerate(flat) if i['slug'] == topic)
    short = item['name'].split(':')[-1].strip()
    saved = VideoLesson.objects.filter(title__icontains=short[:40]).select_related('subject')[:4]
    return render(request, 'learn/topic.html', {
        'track': t, 'topic': item, 'notes': contents.get('notes'), 'practice': contents.get('practice'),
        'videos': catalog.video_links(t, item['name']), 'saved_videos': saved,
        'is_done': TopicProgress.objects.filter(user=request.user, track=track, topic=topic).exists(),
        'prev': flat[idx - 1] if idx else None, 'next': flat[idx + 1] if idx + 1 < len(flat) else None,
        'my_quizzes': Quiz.objects.filter(user=request.user, track=track, title__icontains=short[:40])[:3],
    })


@login_required
@require_POST
@ai_rate_limited
def learn_generate(request, track, topic):
    t, item = catalog.get_topic(track, topic)
    if not item:
        raise Http404
    kind = request.POST.get('kind')
    if kind not in ('notes', 'practice'):
        return fail('Unknown content type.')
    regenerate = request.POST.get('regenerate') == '1'
    if not regenerate and TopicContent.objects.filter(track=track, topic=topic, kind=kind).exists():
        return ok()
    text = ai.topic_notes(t, item) if kind == 'notes' else ai.topic_practice(t, item)
    TopicContent.objects.update_or_create(track=track, topic=topic, kind=kind,
                                          defaults={'content': text, 'generated_by': request.user})
    request.user.profile.record_activity()
    return ok()


@login_required
@require_POST
def learn_done(request, track, topic):
    t, item = catalog.get_topic(track, topic)
    if not item:
        raise Http404
    obj, created = TopicProgress.objects.get_or_create(user=request.user, track=track, topic=topic)
    if created:
        request.user.profile.add_karma(3)
        request.user.profile.record_activity()
        return ok(done=True)
    obj.delete()
    return ok(done=False)


def _make_quiz(user, *, title, subject_name, topic_prompt, difficulty, count, track='', time_limit=None):
    subject = Subject.resolve(subject_name, user)
    items = ai.build_quiz(topic_prompt, subject_name, difficulty, count)
    with transaction.atomic():
        quiz = Quiz.objects.create(user=user, subject=subject, title=title[:200], difficulty=difficulty,
                                   track=track, time_limit=time_limit)
        QuizQuestion.objects.bulk_create([QuizQuestion(quiz=quiz, **i) for i in items])
    return quiz


@login_required
@require_POST
@ai_rate_limited
def learn_quiz(request, track, topic):
    t, item = catalog.get_topic(track, topic)
    if not item:
        raise Http404
    difficulty = request.POST.get('difficulty', 'medium')
    if difficulty not in ('easy', 'medium', 'hard'):
        difficulty = 'medium'
    count = to_int(request.POST.get('count'), 8, 3, 15)
    quiz = _make_quiz(request.user, title=item['name'], subject_name=t['title'],
                      topic_prompt=f"{item['name']} ({t['context']})", difficulty=difficulty, count=count, track=track)
    return ok(redirect=reverse('quiz_take', args=[quiz.pk]))


@login_required
@require_POST
@ai_rate_limited
def mock_create(request, track):
    """Timed mock test over one section (or the whole track)."""
    t = catalog.get_track(track)
    if not t:
        raise Http404
    section = request.POST.get('section', 'all')
    group = next((g for g in t['groups'] if g['title'] == section), None)
    names = [i['name'] for i in group['topics']] if group else [i['name'] for g in t['groups'] for i in g['topics']]
    count = to_int(request.POST.get('count'), 20, 5, 30)
    minutes = to_int(request.POST.get('minutes'), max(10, count), 5, 180)
    difficulty = request.POST.get('difficulty', 'medium')
    if difficulty not in ('easy', 'medium', 'hard'):
        difficulty = 'medium'
    label = group['title'] if group else 'Full syllabus'
    quiz = _make_quiz(request.user, title=f"{t['title']}: {label} mock test", subject_name=t['title'],
                      topic_prompt=f"a balanced mix across these topics: {'; '.join(names[:14])}. Context: {t['context']}",
                      difficulty=difficulty, count=count, track=track, time_limit=minutes)
    return ok(redirect=reverse('quiz_take', args=[quiz.pk]))


# ================================================================= resume
def _s(v, n):
    return str(v or '').strip()[:n]


def clean_resume(d):
    if not isinstance(d, dict):
        d = {}

    def items(key, fields, limit):
        out = []
        for it in (d.get(key) or [])[:limit]:
            if isinstance(it, dict):
                row = {f: _s(it.get(f), n) for f, n in fields.items()}
                if any(row.values()):
                    out.append(row)
        return out

    out = {k: _s(d.get(k), 200) for k in ('name', 'title', 'email', 'phone', 'location', 'linkedin', 'github', 'website')}
    out['summary'] = _s(d.get('summary'), 1200)
    out['skills'] = _s(d.get('skills'), 1000)
    out['certifications'] = _s(d.get('certifications'), 1200)
    out['achievements'] = _s(d.get('achievements'), 1200)
    out['education'] = items('education', {'school': 150, 'degree': 150, 'period': 40, 'details': 400}, 8)
    out['experience'] = items('experience', {'company': 150, 'role': 150, 'period': 40, 'bullets': 1500}, 10)
    out['projects'] = items('projects', {'name': 150, 'tech': 150, 'link': 200, 'bullets': 1200}, 10)
    return out


@login_required
def resume_list(request):
    return render(request, 'tools/resume_list.html', {'resumes': Resume.objects.filter(user=request.user)})


@login_required
@require_POST
def resume_create(request):
    u = request.user
    title = _s(request.POST.get('title'), 120) or 'My resume'
    data = clean_resume({'name': u.get_full_name() or u.username, 'email': u.email,
                         'education': [{'school': u.profile.university}] if u.profile.university else []})
    r = Resume.objects.create(user=u, title=title, data=data)
    return redirect('resume_edit', pk=r.pk)


@login_required
def resume_edit(request, pk):
    r = get_object_or_404(Resume, pk=pk, user=request.user)
    return render(request, 'tools/resume_edit.html', {'resume': r, 'resume_json': r.data})


@login_required
@require_POST
def resume_save(request, pk):
    r = get_object_or_404(Resume, pk=pk, user=request.user)
    try:
        p = _json_payload(request)
    except ValueError as e:
        return fail(str(e))
    r.title = _s(p.get('title'), 120) or r.title
    if p.get('template') in dict(Resume.TEMPLATES):
        r.template = p['template']
    if re.fullmatch(r'#[0-9a-fA-F]{6}', p.get('accent') or ''):
        r.accent = p['accent']
    r.data = clean_resume(p.get('data'))
    r.save()
    return ok()


@login_required
@require_POST
@ai_rate_limited
def resume_ai(request, pk):
    get_object_or_404(Resume, pk=pk, user=request.user)
    try:
        p = _json_payload(request)
    except ValueError as e:
        return fail(str(e))
    action = p.get('action')
    data = clean_resume(p.get('data'))
    if action == 'summary':
        if not (data['experience'] or data['projects'] or data['education'] or data['skills']):
            return fail('Add some education, skills, projects or experience first so the summary has facts to use.')
        return ok(summary=ai.resume_summary(data))
    if action == 'bullets':
        raw = _s(p.get('raw'), 2000)
        if len(raw) < 10:
            return fail('Write a few rough notes about what you did first.')
        kind = 'project' if p.get('kind') == 'project' else 'job'
        return ok(bullets=ai.resume_bullets(kind, _s(p.get('heading'), 150), raw))
    if action == 'ats':
        jd = _s(p.get('jd'), 8000)
        if len(jd) < 40:
            return fail('Paste the full job description (at least a few lines).')
        return ok(result=ai.resume_ats(data, jd))
    return fail('Unknown action.')


@login_required
@require_POST
def resume_delete(request, pk):
    get_object_or_404(Resume, pk=pk, user=request.user).delete()
    messages.success(request, 'Resume deleted.')
    return redirect('resume_list')


@login_required
def resume_docx(request, pk):
    r = get_object_or_404(Resume, pk=pk, user=request.user)
    import docx
    from docx.shared import Pt, RGBColor

    d = r.data
    doc = docx.Document()
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Pt(36)
        s.left_margin = s.right_margin = Pt(46)
    doc.styles['Normal'].font.name = 'Calibri'
    doc.styles['Normal'].font.size = Pt(10.5)
    accent = RGBColor.from_string(r.accent.lstrip('#').upper())

    p = doc.add_paragraph()
    run = p.add_run(d.get('name') or 'Your Name')
    run.bold = True
    run.font.size = Pt(22)
    run.font.color.rgb = accent
    if d.get('title'):
        doc.add_paragraph(d['title'])
    contact = [d.get(k) for k in ('email', 'phone', 'location', 'linkedin', 'github', 'website') if d.get(k)]
    if contact:
        doc.add_paragraph('  |  '.join(contact))

    def heading(text):
        para = doc.add_paragraph()
        para.paragraph_format.space_before = Pt(10)
        run = para.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(11)
        run.font.color.rgb = accent

    def bullets(text):
        for line in (text or '').splitlines():
            line = line.strip().lstrip('-•* ').strip()
            if line:
                doc.add_paragraph(line, style='List Bullet')

    if d.get('summary'):
        heading('Summary')
        doc.add_paragraph(d['summary'])
    if d.get('skills'):
        heading('Skills')
        doc.add_paragraph(d['skills'])
    if d.get('experience'):
        heading('Experience')
        for e in d['experience']:
            para = doc.add_paragraph()
            para.add_run(e.get('role', '')).bold = True
            para.add_run(f"  {e.get('company', '')}   {e.get('period', '')}".rstrip())
            bullets(e.get('bullets'))
    if d.get('projects'):
        heading('Projects')
        for e in d['projects']:
            para = doc.add_paragraph()
            para.add_run(e.get('name', '')).bold = True
            extra = '  ' + ' | '.join(x for x in (e.get('tech'), e.get('link')) if x)
            para.add_run(extra.rstrip())
            bullets(e.get('bullets'))
    if d.get('education'):
        heading('Education')
        for e in d['education']:
            para = doc.add_paragraph()
            para.add_run(e.get('degree', '')).bold = True
            para.add_run(f"  {e.get('school', '')}   {e.get('period', '')}".rstrip())
            if e.get('details'):
                doc.add_paragraph(e['details'])
    for key, label in (('certifications', 'Certifications'), ('achievements', 'Achievements')):
        if d.get(key):
            heading(label)
            bullets(d[key])

    buf = io.BytesIO()
    doc.save(buf)
    name = re.sub(r'[^A-Za-z0-9_-]+', '_', r.title) or 'resume'
    resp = HttpResponse(buf.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    resp['Content-Disposition'] = f'attachment; filename="{name}.docx"'
    return resp


# ============================================================ presentations
THEMES = {
    'indigo': {'title_bg': '3525CD', 'bg': 'FFFFFF', 'title_fg': 'FFFFFF', 'fg': '131B2E', 'accent': '4F46E5'},
    'dark': {'title_bg': '0F1424', 'bg': '0F1424', 'title_fg': 'FFFFFF', 'fg': 'EEF0FF', 'accent': '4CD7F6'},
    'emerald': {'title_bg': '047857', 'bg': 'F4FBF7', 'title_fg': 'FFFFFF', 'fg': '0D2A1E', 'accent': '059669'},
    'sunset': {'title_bg': 'DB2777', 'bg': 'FFF8F3', 'title_fg': 'FFFFFF', 'fg': '2B140A', 'accent': 'EA580C'},
}


@login_required
def ppt_list(request):
    return render(request, 'tools/ppt_list.html', {
        'decks': Presentation.objects.filter(user=request.user)[:30],
        'notes': visible_materials(request.user).exclude(material_type='paper')[:50],
        'themes': list(THEMES),
    })


@login_required
@require_POST
@ai_rate_limited
def ppt_create(request):
    topic = _s(request.POST.get('topic'), 300)
    material = None
    if request.POST.get('material'):
        material = get_visible_material(request.user, request.POST['material'])
        topic = topic or material.title
    if len(topic) < 3:
        return fail('Tell me the topic of your presentation.')
    count = to_int(request.POST.get('count'), 8, 4, 15)
    tone = request.POST.get('tone', 'professional')
    if tone not in ('professional', 'academic', 'friendly', 'persuasive', 'simple'):
        tone = 'professional'
    theme = request.POST.get('theme', 'indigo')
    if theme not in THEMES:
        theme = 'indigo'
    deck = ai.build_presentation(topic, _s(request.POST.get('audience'), 120), count, tone,
                                 context=material.content if material else '')
    pres = Presentation.objects.create(user=request.user, title=deck['title'], topic=topic, theme=theme,
                                       subtitle=deck['subtitle'], slides=deck['slides'])
    request.user.profile.record_activity()
    return ok(redirect=reverse('ppt_view', args=[pres.pk]))


@login_required
def ppt_view(request, pk):
    pres = get_object_or_404(Presentation, pk=pk, user=request.user)
    return render(request, 'tools/ppt_view.html', {'pres': pres, 'themes': list(THEMES),
                                                   'slides_json': pres.slides})


@login_required
@require_POST
def ppt_save(request, pk):
    pres = get_object_or_404(Presentation, pk=pk, user=request.user)
    try:
        p = _json_payload(request)
    except ValueError as e:
        return fail(str(e))
    slides = []
    for s in (p.get('slides') or [])[:40]:
        if not isinstance(s, dict):
            continue
        bullets = [_s(b, 300) for b in (s.get('bullets') or [])[:8] if _s(b, 300)]
        title = _s(s.get('title'), 150)
        if title or bullets:
            slides.append({'title': title, 'bullets': bullets, 'notes': _s(s.get('notes'), 1000)})
    if not slides:
        return fail('A presentation needs at least one slide.')
    pres.slides = slides
    pres.title = _s(p.get('title'), 200) or pres.title
    pres.subtitle = _s(p.get('subtitle'), 300)
    if p.get('theme') in THEMES:
        pres.theme = p['theme']
    pres.save()
    return ok()


@login_required
@require_POST
def ppt_delete(request, pk):
    get_object_or_404(Presentation, pk=pk, user=request.user).delete()
    messages.success(request, 'Presentation deleted.')
    return redirect('ppt_list')


@login_required
def ppt_download(request, pk):
    pres = get_object_or_404(Presentation, pk=pk, user=request.user)
    from pptx import Presentation as PptxPresentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR
    from pptx.util import Inches, Pt

    th = THEMES.get(pres.theme, THEMES['indigo'])
    rgb = lambda h: RGBColor.from_string(h)  # noqa: E731
    prs = PptxPresentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    def rect(slide, x, y, w, h, color):
        shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(color)
        shp.line.fill.background()
        return shp

    def text(slide, x, y, w, h, value, size, color, bold=False, anchor=MSO_ANCHOR.TOP):
        box = slide.shapes.add_textbox(x, y, w, h)
        tf = box.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        para = tf.paragraphs[0]
        run = para.add_run()
        run.text = value
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = rgb(color)
        return tf

    # title slide
    s = prs.slides.add_slide(blank)
    rect(s, 0, 0, prs.slide_width, prs.slide_height, th['title_bg'])
    rect(s, Inches(0.9), Inches(3.55), Inches(1.6), Inches(0.09), th['accent'] if pres.theme == 'dark' else 'FFFFFF')
    text(s, Inches(0.9), Inches(1.5), Inches(11.5), Inches(2), pres.title, 44, th['title_fg'], True, MSO_ANCHOR.BOTTOM)
    if pres.subtitle:
        text(s, Inches(0.9), Inches(3.85), Inches(11), Inches(1.5), pres.subtitle, 22, th['title_fg'])

    for n, sl in enumerate(pres.slides, 1):
        s = prs.slides.add_slide(blank)
        rect(s, 0, 0, prs.slide_width, prs.slide_height, th['bg'])
        rect(s, 0, 0, Inches(0.25), prs.slide_height, th['accent'])
        text(s, Inches(0.9), Inches(0.55), Inches(11.5), Inches(1.2), sl['title'], 34, th['fg'], True, MSO_ANCHOR.MIDDLE)
        rect(s, Inches(0.95), Inches(1.75), Inches(1.2), Inches(0.07), th['accent'])
        total = sum(len(b) for b in sl['bullets'])
        size = 22 if total < 380 else 20 if total < 560 else 17
        box = s.shapes.add_textbox(Inches(0.95), Inches(2.1), Inches(11.4), Inches(4.7))
        tf = box.text_frame
        tf.word_wrap = True
        for i, b in enumerate(sl['bullets']):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.space_after = Pt(12)
            run = para.add_run()
            run.text = '•  ' + b
            run.font.size = Pt(size)
            run.font.color.rgb = rgb(th['fg'])
        text(s, Inches(12.0), Inches(6.95), Inches(1), Inches(0.4), str(n), 12, th['accent'])
        if sl.get('notes'):
            s.notes_slide.notes_text_frame.text = sl['notes']

    buf = io.BytesIO()
    prs.save(buf)
    name = re.sub(r'[^A-Za-z0-9_-]+', '_', pres.title)[:60] or 'presentation'
    resp = HttpResponse(buf.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation')
    resp['Content-Disposition'] = f'attachment; filename="{name}.pptx"'
    return resp


# =============================================================== summarizer
@login_required
def summarize_page(request):
    return render(request, 'tools/summarize.html', {
        'styles': [('concise', 'Concise'), ('bullets', 'Bullet points'), ('detailed', 'Detailed'),
                   ('exam', 'Exam revision sheet'), ('eli5', 'Explain simply (ELI5)'),
                   ('outline', 'Mind-map outline'), ('flashcards', 'Flashcards')],
        'notes': visible_materials(request.user)[:50],
    })


@login_required
@require_POST
@ai_rate_limited
def summarize_run(request):
    text = request.POST.get('text', '').strip()
    f = request.FILES.get('file')
    if request.POST.get('material'):
        text = get_visible_material(request.user, request.POST['material']).content
    if f:
        err = validate_upload(f)
        if err:
            return fail(err)
        text, note = extract_text(f)
        if not text:
            return fail(note or 'No readable text found in that file.')
    if len(text) < 80:
        return fail('Paste at least a few sentences, or choose a file or note to summarize.')
    style = request.POST.get('style', 'concise')
    language = _s(request.POST.get('language'), 30) or 'English'
    summary = ai.summarize_text(text, style, language)
    request.user.profile.record_activity()
    return ok(summary=summary, words=len(text.split()))


@login_required
@require_POST
def summarize_save(request):
    title = _s(request.POST.get('title'), 200) or 'Summary'
    content = request.POST.get('content', '').strip()[:100000]
    if not content:
        return fail('Nothing to save.')
    m = StudyMaterial.objects.create(owner=request.user, title=title, material_type='notes', content=content,
                                     is_public=False)
    return ok(redirect=reverse('material_detail', args=[m.pk]))


# ================================================================ interview
@login_required
def interview_page(request):
    return render(request, 'tools/interview.html')


@login_required
@require_POST
@ai_rate_limited
def interview_turn(request):
    role = _s(request.POST.get('role'), 80)
    if len(role) < 2:
        return fail('Enter the job role you are preparing for.')
    level = request.POST.get('level') if request.POST.get('level') in ('fresher', 'junior', 'mid-level', 'senior') else 'fresher'
    kind = request.POST.get('kind') if request.POST.get('kind') in ('HR', 'technical', 'behavioural', 'mixed') else 'mixed'
    try:
        raw = json.loads(request.POST.get('history') or '[]')
    except ValueError:
        return fail('Invalid history.')
    history = []
    for m in raw[-30:]:
        if isinstance(m, dict) and m.get('role') in ('user', 'model') and _s(m.get('text'), 2500):
            history.append((m['role'], _s(m['text'], 2500)))
    finish = request.POST.get('finish') == '1'
    if history and history[-1][0] != 'user' and not finish:
        return fail('Answer the last question first.')
    reply = ai.interview_turn(role, level, kind, history, finish=finish)
    request.user.profile.record_activity()
    return ok(reply=reply)


# ================================================================ assistant
@login_required
@require_POST
@ai_rate_limited
def assistant_send(request):
    message = request.POST.get('message', '').strip()[:2000]
    if not message:
        return fail('Type a question first.')
    try:
        raw = json.loads(request.POST.get('history') or '[]')
    except ValueError:
        raw = []
    history = [(m['role'], _s(m['text'], 2000)) for m in raw[-8:]
               if isinstance(m, dict) and m.get('role') in ('user', 'model') and _s(m.get('text'), 2000)]
    while history and history[0][0] != 'user':
        history.pop(0)
    return ok(reply=ai.assistant_reply(message, history, page=_s(request.POST.get('page'), 120)))


# ===================================================================== avatar
MAX_AVATAR_BYTES = 5 * 1024 * 1024


@login_required
@require_POST
def avatar_upload(request):
    f = request.FILES.get('avatar')
    if not f:
        return fail('Choose an image first.')
    if f.size > MAX_AVATAR_BYTES:
        return fail('Image is larger than 5 MB.')
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        img = Image.open(f)
        img.verify()
        f.seek(0)
        img = ImageOps.exif_transpose(Image.open(f)).convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError):
        return fail('That file is not a valid image (use JPG, PNG or WEBP).')
    img = ImageOps.fit(img, (400, 400))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=88)
    profile = request.user.profile
    if profile.avatar:
        profile.avatar.delete(save=False)
    profile.avatar.save(f'u{request.user.pk}.jpg', ContentFile(buf.getvalue()), save=True)
    return ok(url=profile.avatar.url)


@login_required
@require_POST
def avatar_remove(request):
    profile = request.user.profile
    if profile.avatar:
        profile.avatar.delete(save=False)
        profile.avatar = None
        profile.save(update_fields=['avatar'])
    return ok()


# =================================================================== misc
@login_required
@require_POST
def feedback_send(request):
    msg = request.POST.get('message', '').strip()[:2000]
    if len(msg) < 5:
        return fail('Please write a little more.')
    Feedback.objects.create(user=request.user, message=msg, page=_s(request.POST.get('page'), 200))
    return ok()


def privacy(request):
    return render(request, 'legal/privacy.html')


def terms(request):
    return render(request, 'legal/terms.html')


def ai_setup_help(request):
    return render(request, 'legal/ai_setup.html')


def healthz(request):
    return JsonResponse({'status': 'ok'})


def robots(request):
    body = 'User-agent: *\nAllow: /\nDisallow: /admin/\nDisallow: /account/\n'
    return HttpResponse(body, content_type='text/plain')
