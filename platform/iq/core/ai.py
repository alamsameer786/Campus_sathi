"""
Gemini integration for Campus IQ.

Talks to the Gemini REST API directly with `requests` (no SDK needed).
Every public function returns plain Python data and raises `AIError` with a
message that is safe to show to the student.

Accuracy rules baked into every prompt:
  * answer from the supplied material when there is one, and say when it is
    not covered instead of guessing;
  * low temperature;
  * structured JSON output validated before it touches the database.
"""
import base64
import json
import re
import time
from datetime import date

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

API_ROOT = 'https://generativelanguage.googleapis.com/v1beta/models'
MAX_CONTEXT_CHARS = 60000

SYSTEM_TUTOR = (
    "You are Campus IQ, an expert university tutor. Be accurate above all: "
    "never invent facts, formulas, citations or numbers. If you are unsure, say so. "
    "When study material is supplied, base your answer on it and say clearly when "
    "something is not covered there; you may add well-established background but label it. "
    "Write math as LaTeX between $...$ (inline) or $$...$$ (block). "
    "Use clear Markdown: short paragraphs, bullet lists, and code blocks for code."
)


class AIError(Exception):
    """User-presentable AI failure."""


# --------------------------------------------------------------------- core
def is_configured():
    return bool(settings.GEMINI_API_KEY)


# Free-tier text models, best first. Used only when the configured model is not
# available to the key (Google retires model names over time).
FALLBACK_MODELS = ['gemini-3.5-flash', 'gemini-3.1-flash-lite', 'gemini-3-flash-preview',
                   'gemini-2.5-flash', 'gemini-2.5-flash-lite']
_NOT_TEXT = ('tts', 'live', 'audio', 'image', 'embedding', 'robotics', 'computer-use', 'native', 'veo', 'imagen')


def _headers():
    return {'x-goog-api-key': settings.GEMINI_API_KEY, 'Content-Type': 'application/json'}


def _discover_models():
    """Ask Google which text models this key can call (cached for an hour)."""
    key = 'gemini_models:' + settings.GEMINI_API_KEY[-8:]
    found = cache.get(key)
    if found is not None:
        return found
    found = []
    try:
        resp = requests.get(API_ROOT, headers=_headers(), params={'pageSize': 200}, timeout=20)
        if resp.status_code == 200:
            for m in resp.json().get('models', []):
                name = m.get('name', '').split('/')[-1]
                if ('generateContent' in (m.get('supportedGenerationMethods') or [])
                        and name.startswith('gemini') and not any(x in name for x in _NOT_TEXT)):
                    found.append(name)
    except (requests.RequestException, ValueError):
        pass
    if found:
        cache.set(key, found, 3600)
    return found


def _rank(name, available):
    """Lower is better: known free models first, then other 'flash' models, newest names first."""
    if name in FALLBACK_MODELS:
        return (0, FALLBACK_MODELS.index(name))
    return (1, 0) if 'flash' in name and 'preview' not in name else (2, 0)


def _post(payload):
    if not is_configured():
        raise AIError('AI is not set up yet. Add GEMINI_API_KEY to your .env file '
                      '(free key: https://aistudio.google.com/apikey) and restart the server.')
    configured = settings.GEMINI_MODEL
    ok_key = f'gemini_ok:{configured}:{settings.GEMINI_API_KEY[-8:]}'
    model = cache.get(ok_key) or configured
    tried = set()
    while True:
        tried.add(model)
        result = _call_model(model, payload)
        if result is not None:
            cache.set(ok_key, model, 24 * 3600)
            return result
        # 404: this model name is not available to the key. Pick another one.
        available = [m for m in _discover_models() if m not in tried]
        pool = sorted(available, key=lambda n: _rank(n, available)) or [m for m in FALLBACK_MODELS if m not in tried]
        if not pool:
            raise AIError(f'None of the Gemini models I tried ({", ".join(sorted(tried))}) are available to your key. '
                          'Open https://aistudio.google.com, check which models you can use, and set GEMINI_MODEL in .env.')
        model = pool[0]


def _call_model(model, payload):
    """POST to one model. Returns the JSON, or None if the model does not exist (404)."""
    url = f'{API_ROOT}/{model}:generateContent'
    last = None
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=_headers(), json=payload, timeout=90)
        except requests.RequestException:
            last = 'Could not reach Gemini. Check your internet connection and try again.'
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return None
        if resp.status_code in (429, 500, 503) and attempt < 2:
            last = ('Gemini is busy or the free-tier rate limit was hit. Wait a minute and try again.'
                    if resp.status_code == 429 else 'Gemini is temporarily unavailable. Try again shortly.')
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code == 429:
            raise AIError('Gemini free-tier rate limit reached. Wait a minute and try again.')
        if resp.status_code in (400, 401, 403):
            msg = ''
            try:
                msg = resp.json().get('error', {}).get('message', '')
            except ValueError:
                pass
            if 'API key' in msg or resp.status_code in (401, 403):
                raise AIError('The Gemini API key was rejected. Check GEMINI_API_KEY in your .env file.')
            raise AIError(f'Gemini could not process this request. {msg[:200]}'.strip())
        last = f'Gemini returned an unexpected error ({resp.status_code}).'
    raise AIError(last or 'Gemini request failed.')


def _text_of(data):
    cands = data.get('candidates') or []
    if not cands:
        reason = (data.get('promptFeedback') or {}).get('blockReason')
        raise AIError('The request was blocked by Gemini safety filters.' if reason
                      else 'Gemini returned an empty answer. Please try again.')
    parts = (cands[0].get('content') or {}).get('parts') or []
    text = ''.join(p.get('text', '') for p in parts if not p.get('thought')).strip()
    if not text:
        raise AIError('Gemini returned an empty answer. Please try again.')
    return text


def generate_text(prompt, system=SYSTEM_TUTOR, history=None, temperature=0.3, search=False):
    contents = []
    for role, text in (history or []):
        contents.append({'role': role, 'parts': [{'text': text}]})
    contents.append({'role': 'user', 'parts': [{'text': prompt}]})
    payload = {
        'systemInstruction': {'parts': [{'text': system}]},
        'contents': contents,
        'generationConfig': {'temperature': temperature},
    }
    if search and settings.GEMINI_USE_SEARCH:
        payload['tools'] = [{'google_search': {}}]
    return _text_of(_post(payload))


def generate_json(prompt, schema, system=SYSTEM_TUTOR, temperature=0.2):
    payload = {
        'systemInstruction': {'parts': [{'text': system}]},
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'generationConfig': {
            'temperature': temperature,
            'responseMimeType': 'application/json',
            'responseSchema': schema,
        },
    }
    raw = _text_of(_post(payload))
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
    try:
        return json.loads(raw)
    except ValueError:
        raise AIError('Gemini returned a malformed answer. Please try again.')


def _clip(text, limit=MAX_CONTEXT_CHARS):
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit] + '\n[...material truncated...]'


def _str(v, limit=4000):
    return str(v or '').strip()[:limit]


# Schema helpers -----------------------------------------------------------
S = {'type': 'STRING'}
I = {'type': 'INTEGER'}


def _obj(**props):
    return {'type': 'OBJECT', 'properties': props, 'required': list(props)}


def _arr(items):
    return {'type': 'ARRAY', 'items': items}


# ------------------------------------------------------- text from uploads
def extract_text_with_ai(raw_bytes, mime_type):
    """OCR for scanned PDFs and images, using Gemini's multimodal input."""
    payload = {
        'contents': [{'role': 'user', 'parts': [
            {'inline_data': {'mime_type': mime_type, 'data': base64.b64encode(raw_bytes).decode()}},
            {'text': 'Transcribe all text in this document exactly as written, in reading order. '
                     'Keep headings, numbering, formulas (as LaTeX in $...$) and tables (as Markdown). '
                     'Do not summarise, add or correct anything. Output only the transcription.'},
        ]}],
        'generationConfig': {'temperature': 0},
    }
    return _text_of(_post(payload))


# ---------------------------------------------------------------- materials
KIT_SCHEMA = _obj(
    summary=S, one_line=S,
    key_points=_arr(S),
    flashcards=_arr(_obj(front=S, back=S)),
)


def build_study_kit(title, subject, content):
    prompt = f"""Create a study kit from the material below.

Title: {title}
Subject: {subject or 'not specified'}

Rules:
- summary: a faithful Markdown summary (150-350 words) with short headings. Only use facts present in the material.
- one_line: one sentence, max 25 words.
- key_points: 6-10 concise, exam-relevant points taken from the material (definitions, formulas, rules, steps).
- flashcards: 8-12 cards. front = a precise question or term, back = the correct, concise answer from the material.

MATERIAL:
{_clip(content)}"""
    data = generate_json(prompt, KIT_SCHEMA)
    cards = [{'front': _str(c.get('front'), 500), 'back': _str(c.get('back'), 1000)}
             for c in data.get('flashcards', []) if _str(c.get('front')) and _str(c.get('back'))]
    points = [_str(p, 500) for p in data.get('key_points', []) if _str(p)]
    summary = _str(data.get('summary'), 8000)
    if not summary:
        raise AIError('Gemini could not summarise this material.')
    return {'summary': summary, 'one_line': _str(data.get('one_line'), 500),
            'key_points': points[:12], 'flashcards': cards[:15]}


QUESTIONS_SCHEMA = _arr(_obj(question=S, answer=S, difficulty=S, marks=I, topic=S))


def build_questions(title, subject, content, is_paper, count=8):
    if is_paper:
        task = ("This is a past exam paper. Extract each distinct question exactly as set "
                "(keep its numbering and marks if shown). For every question write a correct, "
                "complete model answer that would earn full marks, with the key marking points. "
                "For numerical problems show every step and the final answer.")
    else:
        task = (f"Write {count} likely exam questions (mix of short and long answer, easy to hard) "
                "that this material can answer, each with a correct, complete model answer "
                "based only on the material.")
    prompt = f"""{task}

Return difficulty as easy, medium or hard. marks = expected marks (0 if unknown). topic = short topic label.

Title: {title}
Subject: {subject or 'not specified'}

MATERIAL:
{_clip(content)}"""
    data = generate_json(prompt, QUESTIONS_SCHEMA)
    out = []
    for q in data[:40]:
        text, ans = _str(q.get('question')), _str(q.get('answer'), 8000)
        if not text or not ans:
            continue
        diff = _str(q.get('difficulty')).lower()
        out.append({'text': text, 'answer': ans,
                    'difficulty': diff if diff in ('easy', 'medium', 'hard') else 'medium',
                    'marks': q.get('marks') if isinstance(q.get('marks'), int) and q['marks'] > 0 else None,
                    'topic': _str(q.get('topic'), 200)})
    if not out:
        raise AIError('Gemini could not find questions in this material.')
    return out


def answer_question(question, context='', history=None, search=False):
    if context:
        prompt = (f"Study material:\n\"\"\"\n{_clip(context)}\n\"\"\"\n\n"
                  f"Student question: {question}\n\n"
                  "Answer from the material. If it is not covered there, say so first, "
                  "then give a clearly labelled general answer if you are confident.")
    else:
        prompt = question
    return generate_text(prompt, history=history, search=search)


# ------------------------------------------------------------------- videos
def explain_video(title, subject, description):
    prompt = f"""A student is about to watch a video lesson. Write a bite-sized companion explainer.

Video title: {title}
Subject: {subject or 'not specified'}
Notes from the student: {description or 'none'}

You cannot see the video, so explain the topic named in the title from well-established knowledge.
Do not claim to describe what the video says. Structure (Markdown):
## What you'll learn (3 bullets)
## Core idea (one short paragraph)
## Key terms (term: definition)
## Worked example (one small, correct example)
## Check yourself (3 short questions, answers at the end)"""
    return generate_text(prompt)


# ------------------------------------------------------------------ quizzes
QUIZ_SCHEMA = _arr(_obj(question=S, options=_arr(S), correct_index=I, explanation=S, topic=S))


def build_quiz(topic, subject, difficulty, count, context=''):
    source = (f"Base every question strictly on this material:\n\"\"\"\n{_clip(context)}\n\"\"\"" if context
              else f"Use well-established, standard {subject or 'university'} knowledge about: {topic}.")
    prompt = f"""Write {count} multiple-choice questions ({difficulty} difficulty).

{source}

Rules:
- exactly 4 options per question, one unambiguously correct, plausible distractors, no "all of the above".
- correct_index is the 0-based index of the correct option.
- explanation: 1-3 sentences explaining why the correct option is right.
- topic: a short sub-topic label (2-4 words) for tracking weak spots.
- Double-check each correct answer before finalising; skip any question you are not sure about."""
    data = generate_json(prompt, QUIZ_SCHEMA, temperature=0.3)
    out = []
    for q in data:
        opts = [_str(o, 300) for o in q.get('options', []) if _str(o)]
        idx = q.get('correct_index')
        if len(opts) != 4 or not isinstance(idx, int) or not 0 <= idx < 4 or not _str(q.get('question')):
            continue
        out.append({'text': _str(q['question']), 'options': opts, 'correct_index': idx,
                    'explanation': _str(q.get('explanation'), 1500), 'topic': _str(q.get('topic'), 100) or topic})
    if len(out) < 2:
        raise AIError('Gemini could not produce a reliable quiz. Try a more specific topic.')
    return out[:count]


# ------------------------------------------------------------------ planner
PLAN_SCHEMA = _arr(_obj(date=S, title=S, detail=S, topic=S, minutes=I))


def build_plan(subject, exam_date, hours_per_day, topics, weak_topics, materials):
    today = timezone.localdate()
    days = (exam_date - today).days + 1
    prompt = f"""Create a day-by-day exam revision plan.

Today: {today.isoformat()}
Exam date: {exam_date.isoformat()} ({days} days including today)
Subject: {subject}
Study time per day: {hours_per_day} hours (max total minutes per day = {int(hours_per_day * 60)})
Topics to cover: {topics or 'not provided - infer a sensible syllabus from the subject and materials'}
Student's weak topics (give these extra, earlier and repeated time): {', '.join(weak_topics) or 'none recorded'}
Student's uploaded materials: {', '.join(materials) or 'none'}

Rules:
- Dates must be YYYY-MM-DD between today and the exam date inclusive.
- 1-3 tasks per day, each with a concrete title (what to study/practise), a one-sentence detail, a topic label and minutes.
- Use spaced repetition: revisit earlier topics, schedule quizzes/past-paper practice in the last third, and light revision the day before the exam.
- Be realistic; do not exceed the daily limit."""
    data = generate_json(prompt, PLAN_SCHEMA, temperature=0.4)
    tasks = []
    for t in data[:240]:
        try:
            d = date.fromisoformat(_str(t.get('date'))[:10])
        except ValueError:
            continue
        if not today <= d <= exam_date or not _str(t.get('title')):
            continue
        mins = t.get('minutes') if isinstance(t.get('minutes'), int) else 45
        tasks.append({'date': d, 'title': _str(t['title'], 200), 'detail': _str(t.get('detail'), 500),
                      'topic': _str(t.get('topic'), 200), 'minutes': min(max(mins, 10), 240)})
    if not tasks:
        raise AIError('Gemini could not build a plan. Add some topics and try again.')
    tasks.sort(key=lambda x: x['date'])
    return tasks


# =================================================================== hubs
def topic_notes(track, topic):
    is_code = track['slug'] == 'dsa'
    prompt = f"""Write complete study notes for the topic "{topic['name']}" (section: {topic['group']}, course: {track['title']}).

Context: {track['context']}

Use this Markdown structure:
## Overview (what it is and why it matters)
## Key concepts (clear definitions; bullet lists)
## {'Algorithms & code (Python, with time and space complexity for each)' if is_code else 'Important formulas, laws and procedures (LaTeX for math)'}
## Worked examples (2 fully solved, step by step)
## Common mistakes
## Quick revision sheet (8-10 one-line bullets)
## How this is tested (typical exam or interview question styles)

Rules: be accurate; never invent facts, numbers or citations; if a detail varies by university or country, say so.
Length: about 900-1300 words."""
    return generate_text(prompt, temperature=0.3)


def topic_practice(track, topic):
    is_code = track['slug'] == 'dsa'
    prompt = f"""Create 8 practice questions with full answers for "{topic['name']}" ({track['title']}).

Context: {track['context']}

Mix easy, medium and hard. {'Make most of them coding problems: state the problem with an example, then give the approach, a correct Python solution and its complexity.' if is_code else 'Include conceptual questions and numerical or short-answer problems where relevant, each with a worked solution.'}
Format each as:
### Q1. (difficulty) question text
**Answer:** the worked answer

Verify every answer before writing it. Skip anything you cannot answer with confidence."""
    return generate_text(prompt, temperature=0.3)


# ================================================================== resume
def _resume_text(d):
    """Flatten resume JSON into plain text for prompts."""
    lines = [f"Name: {_str(d.get('name'), 120)}", f"Target title: {_str(d.get('title'), 120)}"]
    if d.get('summary'):
        lines.append('Summary: ' + _str(d['summary'], 1200))
    if d.get('skills'):
        lines.append('Skills: ' + _str(d['skills'], 800))
    for e in d.get('education') or []:
        lines.append(f"Education: {_str(e.get('degree'), 120)}, {_str(e.get('school'), 120)} ({_str(e.get('period'), 40)}) {_str(e.get('details'), 300)}")
    for e in d.get('experience') or []:
        lines.append(f"Experience: {_str(e.get('role'), 120)} at {_str(e.get('company'), 120)} ({_str(e.get('period'), 40)})\n{_str(e.get('bullets'), 1500)}")
    for e in d.get('projects') or []:
        lines.append(f"Project: {_str(e.get('name'), 120)} [{_str(e.get('tech'), 120)}]\n{_str(e.get('bullets'), 1200)}")
    if d.get('certifications'):
        lines.append('Certifications: ' + _str(d['certifications'], 800))
    if d.get('achievements'):
        lines.append('Achievements: ' + _str(d['achievements'], 800))
    return '\n'.join(lines)


RESUME_RULES = ("Use ONLY facts the student provided. Never invent employers, dates, metrics, percentages, tools "
                "or achievements. If a number would make a bullet stronger but none was given, leave it out.")


def resume_summary(d):
    prompt = f"""Write a professional resume summary (2-3 sentences, max 55 words, no first-person pronouns, no clichés like "hard-working" or "team player") for this candidate.

{RESUME_RULES}

{_resume_text(d)}

Return only the summary text."""
    return generate_text(prompt, temperature=0.4).strip().strip('"')


BULLETS_SCHEMA = _arr(S)


def resume_bullets(kind, heading, raw):
    prompt = f"""Rewrite these rough notes about a {kind} ("{heading}") into 3-5 strong resume bullets.

Rough notes:
\"\"\"{_str(raw, 2000)}\"\"\"

Rules: start each bullet with a different strong past-tense action verb (present tense if the work is ongoing),
keep each under 22 words, mention technologies where given, show impact only if the notes state it.
{RESUME_RULES}
Return a JSON array of strings."""
    data = generate_json(prompt, BULLETS_SCHEMA, temperature=0.4)
    out = [_str(b, 250).lstrip('-•* ').strip() for b in data if _str(b)]
    if not out:
        raise AIError('Could not improve these bullets. Add a few more details and try again.')
    return out[:6]


ATS_SCHEMA = _obj(score=I, matched=_arr(S), missing=_arr(S), suggestions=_arr(S))


def resume_ats(d, job_description):
    prompt = f"""You are an applicant-tracking-system (ATS) reviewer. Compare this resume to the job description.

JOB DESCRIPTION:
\"\"\"{_clip(job_description, 8000)}\"\"\"

RESUME:
\"\"\"{_resume_text(d)}\"\"\"

Return JSON:
- score: 0-100 fit estimate (be honest and conservative)
- matched: up to 12 important keywords/skills from the job description that the resume already shows
- missing: up to 12 important keywords/skills from the job description that the resume lacks
- suggestions: 4-6 specific, actionable edits. Only suggest adding keywords the candidate could truthfully claim; never suggest fabricating experience."""
    data = generate_json(prompt, ATS_SCHEMA, temperature=0.2)
    score = data.get('score')
    return {
        'score': max(0, min(100, score)) if isinstance(score, int) else 0,
        'matched': [_str(x, 60) for x in data.get('matched', [])][:12],
        'missing': [_str(x, 60) for x in data.get('missing', [])][:12],
        'suggestions': [_str(x, 300) for x in data.get('suggestions', [])][:6],
    }


# ============================================================ presentation
DECK_SCHEMA = _obj(title=S, subtitle=S, slides=_arr(_obj(title=S, bullets=_arr(S), notes=S)))


def build_presentation(topic, audience, count, tone, context=''):
    source = (f"Base the content strictly on this material:\n\"\"\"\n{_clip(context, 30000)}\n\"\"\"\n" if context else
              "Use well-established, accurate knowledge. Do not invent statistics, quotes or sources.\n")
    prompt = f"""Create a presentation on: {topic}
Audience: {audience or 'general audience'}
Tone: {tone}
Number of content slides: {count} (do not count the title slide)

{source}
Rules:
- Logical flow: context/problem -> core ideas -> examples -> summary/takeaways (and a final "Questions?" or references-free closing idea).
- Each slide: a short title (max 8 words) and 3-5 concise bullets (max 18 words each, no full paragraphs).
- notes: 2-3 sentences of speaker notes per slide that expand on the bullets.
- title/subtitle: for the title slide."""
    data = generate_json(prompt, DECK_SCHEMA, temperature=0.5)
    slides = []
    for s in data.get('slides', [])[:count + 4]:
        bullets = [_str(b, 220) for b in s.get('bullets', []) if _str(b)][:6]
        if _str(s.get('title')) and bullets:
            slides.append({'title': _str(s['title'], 120), 'bullets': bullets, 'notes': _str(s.get('notes'), 800)})
    if len(slides) < 2:
        raise AIError('Gemini could not build the slides. Try a more specific topic.')
    return {'title': _str(data.get('title'), 200) or topic[:200], 'subtitle': _str(data.get('subtitle'), 300), 'slides': slides}


# ============================================================== summarizer
SUMMARY_STYLES = {
    'concise': 'A concise summary in 1-2 short paragraphs.',
    'bullets': 'A bullet-point summary grouped under short bold headings.',
    'detailed': 'A detailed structured summary with headings, preserving important definitions, formulas and examples.',
    'exam': 'An exam-revision sheet: key definitions, formulas, must-remember points and 5 likely exam questions with brief answers.',
    'eli5': 'An explanation a 12-year-old could follow, using simple words and one analogy.',
    'outline': 'A hierarchical outline (mind-map style) using nested bullets.',
    'flashcards': 'Ten Q&A flashcards formatted as **Q:** ... / **A:** ...',
}


def summarize_text(text, style, language='English'):
    prompt = f"""Summarize the text below.

Format: {SUMMARY_STYLES.get(style, SUMMARY_STYLES['concise'])}
Write in {language}. Use only information in the text; do not add outside facts. Keep math as LaTeX in $...$.

TEXT:
\"\"\"
{_clip(text)}
\"\"\""""
    return generate_text(prompt, temperature=0.2)


# ================================================================ interview
def interview_turn(role, level, kind, history, finish=False):
    system = (f"You are a professional interviewer conducting a {kind} interview for a {level} {role} position. "
              "Ask ONE question at a time. After the candidate answers, give 1-2 sentences of specific, honest feedback "
              "(what was good, what was missing), then ask the next question. Mix easy and harder questions, and ask a "
              "natural follow-up when an answer is vague. Keep every message under 120 words. Never answer your own questions.")
    if finish:
        prompt = ("The interview is over. Evaluate the whole conversation. Give a Markdown scorecard: overall score /10, "
                  "scores /10 for Communication, Technical/Role knowledge, Problem solving and Confidence, then "
                  "'Strengths', 'Improve next', and a 5-day practice plan. Be honest and specific, quoting the candidate's own words.")
    elif not history:
        prompt = "Greet the candidate in one sentence and ask the first question."
    else:
        prompt = None
    hist = list(history)
    if hist and hist[0][0] == 'model':
        hist.insert(0, ('user', 'Begin the interview.'))
    if prompt is None:
        # the last turn must be the candidate's answer
        prompt = hist.pop()[1]
    return generate_text(prompt, system=system, history=hist, temperature=0.6)


# ================================================================ assistant
APP_GUIDE = """Campus IQ features (tell students where to find things; paths are in brackets):
- Dashboard [/]: streak, karma, today's plan, mastery and weak-spot radar.
- Notes [/notes/]: upload PDF/DOCX/TXT/photos or paste text [/notes/upload/] to get an AI summary, key points, flashcards, and an 'Ask this note' chat.
- Past papers [/exams/]: upload a paper to get questions extracted and answered step by step. Quizzes: pick a topic or note, choose difficulty and count.
- Videos [/videos/]: save YouTube lessons, get an AI companion explainer, mark watched.
- Planner [/planner/]: enter an exam date and topics to get a day-by-day revision plan that gives weak topics extra time.
- Learn hubs [/learn/]: DSA Mastery, Engineering Library (all branches), Nepal Engineering Licence prep, Job Preparation. Each topic has AI notes, practice questions, video links and a quiz.
- Timed mock tests: on a hub page choose a section, question count and minutes.
- Resume Maker [/tools/resume/]: build a resume with live preview, AI summary and bullet improver, ATS check against a job description, download PDF or Word.
- Presentation Maker [/tools/presentations/]: enter any topic and get slides with speaker notes, preview them, download PowerPoint (.pptx).
- Summarizer [/tools/summarize/]: paste text or upload a file; choose concise, bullets, detailed, exam sheet, ELI5, outline or flashcards.
- Mock Interview [/tools/interview/]: practise HR, technical or behavioural interviews and get a scorecard.
- AI Tutor [/ask/]: ask any study question.
- Account [/account/]: edit profile, change password. Click the avatar (top right) to upload a photo, switch dark mode, send feedback or log out.
"""

SYSTEM_ASSISTANT = (
    "You are the Campus IQ assistant: a friendly help guide and study helper inside the Campus IQ app. "
    "You can (1) explain how to use the app's features, and (2) answer study, career and exam questions accurately. "
    "Never invent facts; say when you are unsure. Keep answers short and practical (under 180 words unless the student asks for depth). "
    "Write math as LaTeX in $...$. When pointing to a feature, name it and give its path.\n\n" + APP_GUIDE
)


def assistant_reply(message, history, page=''):
    prompt = message if not page else f"(The student is currently on page: {page})\n{message}"
    return generate_text(prompt, system=SYSTEM_ASSISTANT, history=history, temperature=0.4, search=True)