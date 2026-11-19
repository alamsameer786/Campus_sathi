import json
from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import ai, catalog
from .forms import AccountForm
from .models import (ChatMessage, Question, Quiz, QuizAttempt, QuizQuestion, StudyKit, StudyMaterial,
                     StudyPlan, StudyTask, Subject, TopicStat, UserProfile, VideoLesson, VideoProgress)
from .services import ai_rate_limited, extract_text, validate_upload


# ----------------------------------------------------------------- helpers
def visible_materials(user):
    return StudyMaterial.objects.filter(Q(is_public=True) | Q(owner=user)).select_related('subject', 'owner')


def get_visible_material(user, pk):
    m = get_object_or_404(StudyMaterial.objects.select_related('subject', 'owner'), pk=pk)
    if not m.can_view(user):
        raise Http404
    return m


def ok(**extra):
    return JsonResponse({'ok': True, **extra})


def fail(msg, status=400):
    return JsonResponse({'ok': False, 'error': msg}, status=status)


def to_int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def weak_topics_for(user, subject=None, limit=6):
    qs = TopicStat.objects.filter(user=user, total__gte=2)
    if subject:
        qs = qs.filter(subject=subject)
    weak = [t for t in qs if t.accuracy < 60]
    weak.sort(key=lambda t: t.accuracy)
    return weak[:limit]


# -------------------------------------------------------------------- home
def home(request):
    if not request.user.is_authenticated:
        return render(request, 'core/landing.html', {
            'stats': {
                'materials': StudyMaterial.objects.filter(is_public=True).count(),
                'videos': VideoLesson.objects.count(),
                'subjects': Subject.objects.count(),
            },
            'tracks': catalog.TRACKS,
        })
    return dashboard(request)


def _track_progress(user):
    from .models import TopicProgress
    done = {}
    for tr in TopicProgress.objects.filter(user=user).values_list('track', flat=True):
        done[tr] = done.get(tr, 0) + 1
    return [{**t, 'done': done.get(t['slug'], 0), 'percent': round(done.get(t['slug'], 0) * 100 / t['topic_count'])}
            for t in catalog.TRACKS]


@login_required
def dashboard(request):
    user = request.user
    today = timezone.localdate()
    profile = user.profile

    attempts = QuizAttempt.objects.filter(user=user)
    total_q = sum(a.total for a in attempts)
    accuracy = round(sum(a.score for a in attempts) * 100 / total_q) if total_q else None

    mastery = defaultdict(lambda: [0, 0])
    for t in TopicStat.objects.filter(user=user).select_related('subject'):
        name = t.subject.name if t.subject else 'General'
        mastery[name][0] += t.correct
        mastery[name][1] += t.total
    mastery = sorted(
        ({'name': k, 'percent': round(c * 100 / n) if n else 0, 'total': n} for k, (c, n) in mastery.items()),
        key=lambda m: m['percent'])

    next_plan = StudyPlan.objects.filter(user=user, exam_date__gte=today).first()
    ctx = {
        'profile': profile,
        'materials_count': StudyMaterial.objects.filter(owner=user).count(),
        'videos_watched': VideoProgress.objects.filter(user=user).count(),
        'quizzes_taken': attempts.count(),
        'accuracy': accuracy,
        'mastery': mastery,
        'weak_topics': weak_topics_for(user),
        'recent_materials': visible_materials(user).exclude(material_type='paper')[:4],
        'recent_attempts': attempts.select_related('quiz')[:3],
        'today_tasks': StudyTask.objects.filter(plan__user=user, date=today).select_related('plan'),
        'next_plan': next_plan,
        'recent_videos': VideoLesson.objects.select_related('subject')[:3],
        'tracks': _track_progress(user),
        'leaders': UserProfile.objects.filter(karma__gt=0).select_related('user').order_by('-karma')[:5],
        'resumes_count': user.resumes.count(),
        'decks_count': user.presentations.count(),
    }
    return render(request, 'core/dashboard.html', ctx)


@login_required
def search(request):
    q = request.GET.get('q', '').strip()
    materials, videos = [], []
    if q:
        materials = visible_materials(request.user).filter(
            Q(title__icontains=q) | Q(topic__icontains=q) | Q(subject__name__icontains=q) | Q(content__icontains=q)
        ).distinct()[:30]
        videos = VideoLesson.objects.filter(
            Q(title__icontains=q) | Q(description__icontains=q) | Q(subject__name__icontains=q)
        ).select_related('subject').distinct()[:20]
    return render(request, 'core/search.html', {'q': q, 'materials': materials, 'videos': videos,
                                                'topics': catalog.search_topics(q)})


# ---------------------------------------------------------------- materials
@login_required
def notes(request):
    qs = visible_materials(request.user).exclude(material_type='paper')
    q = request.GET.get('q', '').strip()
    subject = request.GET.get('subject', '').strip()
    mine = request.GET.get('mine') == '1'
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(topic__icontains=q) | Q(content__icontains=q))
    if subject:
        qs = qs.filter(subject__name__iexact=subject)
    if mine:
        qs = qs.filter(owner=request.user)
    return render(request, 'core/notes.html', {
        'materials': qs[:60],
        'subjects': Subject.objects.annotate(n=Count('materials')).filter(n__gt=0),
        'q': q, 'active_subject': subject, 'mine': mine,
        'videos': VideoLesson.objects.select_related('subject')[:6],
    })


@login_required
def upload(request):
    if request.method == 'GET':
        return render(request, 'core/upload.html', {
            'subjects': Subject.objects.all(),
            'preset_type': request.GET.get('type', 'notes'),
        })

    title = request.POST.get('title', '').strip()
    pasted = request.POST.get('content', '').strip()
    f = request.FILES.get('file')
    material_type = request.POST.get('material_type', 'notes')
    if material_type not in dict(StudyMaterial.TYPES):
        material_type = 'notes'
    if not title:
        return fail('Please give the material a title.')
    if not f and not pasted:
        return fail('Upload a file or paste some text.')

    content, warning = pasted, ''
    if f:
        err = validate_upload(f)
        if err:
            return fail(err)
        extracted, warning = extract_text(f)
        content = f'{pasted}\n\n{extracted}'.strip() if pasted else extracted
    if len(content) < 40:
        return fail(warning or 'Not enough readable text was found. Paste the text or upload a clearer file.')

    year = request.POST.get('year', '').strip()
    material = StudyMaterial.objects.create(
        owner=request.user,
        subject=Subject.resolve(request.POST.get('subject'), request.user),
        title=title[:200],
        topic=request.POST.get('topic', '').strip()[:200],
        semester=request.POST.get('semester', '').strip()[:50],
        year=to_int(year, None, 1950, 2100) if year else None,
        material_type=material_type,
        content=content,
        file=f,
        is_public=request.POST.get('is_public') == 'on',
    )
    request.user.profile.add_karma(10)
    request.user.profile.record_activity()

    if request.POST.get('auto_ai') == 'on' and ai.is_configured():
        try:
            _generate(material, 'questions' if material_type == 'paper' else 'kit')
        except ai.AIError as e:
            warning = f'Saved, but AI could not process it yet: {e}'
    elif request.POST.get('auto_ai') == 'on' and not ai.is_configured():
        warning = 'Saved. Add GEMINI_API_KEY to enable AI summaries and answers.'

    if warning:
        messages.warning(request, warning)
    else:
        messages.success(request, 'Material added.')
    return ok(redirect=reverse('material_detail', args=[material.pk]))


def _generate(material, kind):
    subject = material.subject.name if material.subject else ''
    if kind == 'kit':
        data = ai.build_study_kit(material.title, subject, material.content)
        StudyKit.objects.update_or_create(
            material=material, defaults={**data, 'model_name': ai.settings.GEMINI_MODEL})
    elif kind == 'questions':
        items = ai.build_questions(material.title, subject, material.content,
                                   is_paper=material.material_type == 'paper')
        with transaction.atomic():
            material.questions.all().delete()
            Question.objects.bulk_create([Question(material=material, **i) for i in items])
    else:
        raise ValueError(kind)


@login_required
def material_detail(request, pk):
    m = get_visible_material(request.user, pk)
    return render(request, 'core/material_detail.html', {
        'm': m,
        'kit': getattr(m, 'kit', None),
        'questions': m.questions.all(),
        'is_owner': m.owner_id == request.user.id,
        'history': ChatMessage.objects.filter(user=request.user, material=m),
    })


@login_required
@require_POST
@ai_rate_limited
def material_generate(request, pk):
    m = get_visible_material(request.user, pk)
    kind = request.POST.get('kind')
    if kind not in ('kit', 'questions'):
        return fail('Unknown action.')
    if not m.content.strip():
        return fail('This material has no readable text to work with.')
    _generate(m, kind)
    request.user.profile.record_activity()
    return ok(redirect=reverse('material_detail', args=[m.pk]) + ('#questions' if kind == 'questions' else ''))


@login_required
@require_POST
@ai_rate_limited
def material_ask(request, pk):
    m = get_visible_material(request.user, pk)
    question = request.POST.get('message', '').strip()[:2000]
    if not question:
        return fail('Type a question first.')
    history = [(c.role, c.text) for c in ChatMessage.objects.filter(user=request.user, material=m).order_by('-id')[:8]][::-1]
    reply = ai.answer_question(question, context=m.content, history=history)
    ChatMessage.objects.create(user=request.user, material=m, role='user', text=question)
    ChatMessage.objects.create(user=request.user, material=m, role='model', text=reply)
    request.user.profile.record_activity()
    return ok(reply=reply)


@login_required
def material_file(request, pk):
    m = get_visible_material(request.user, pk)
    if not m.file:
        raise Http404
    return FileResponse(m.file.open('rb'), as_attachment=True, filename=m.file.name.rsplit('/', 1)[-1])


@login_required
@require_POST
def material_delete(request, pk):
    m = get_object_or_404(StudyMaterial, pk=pk, owner=request.user)
    paper = m.material_type == 'paper'
    if m.file:
        m.file.delete(save=False)
    m.delete()
    messages.success(request, 'Deleted.')
    return redirect('exams' if paper else 'notes')


# ------------------------------------------------------------------- videos
@login_required
def videos(request):
    subject = request.GET.get('subject', '').strip()
    qs = VideoLesson.objects.select_related('subject')
    if subject:
        qs = qs.filter(subject__name__iexact=subject)
    watched = set(VideoProgress.objects.filter(user=request.user).values_list('video_id', flat=True))
    return render(request, 'core/videos.html', {
        'videos': qs, 'watched': watched, 'subjects': Subject.objects.all(), 'active_subject': subject,
    })


@login_required
@require_POST
def video_add(request):
    url = request.POST.get('url', '').strip()
    title = request.POST.get('title', '').strip()
    yt = VideoLesson.parse_youtube_id(url)
    if not yt:
        return fail('Paste a valid YouTube link (youtube.com or youtu.be).')
    if not title:
        return fail('Give the video a title.')
    v = VideoLesson.objects.create(
        added_by=request.user, title=title[:200], url=url, youtube_id=yt,
        subject=Subject.resolve(request.POST.get('subject'), request.user),
        description=request.POST.get('description', '').strip()[:1000])
    request.user.profile.add_karma(5)
    return ok(redirect=reverse('video_detail', args=[v.pk]))


@login_required
def video_detail(request, pk):
    v = get_object_or_404(VideoLesson.objects.select_related('subject', 'added_by'), pk=pk)
    return render(request, 'core/video_detail.html', {
        'v': v,
        'is_watched': VideoProgress.objects.filter(user=request.user, video=v).exists(),
        'related': VideoLesson.objects.filter(subject=v.subject).exclude(pk=v.pk)[:4] if v.subject else [],
    })


@login_required
@require_POST
@ai_rate_limited
def video_explain(request, pk):
    v = get_object_or_404(VideoLesson, pk=pk)
    v.ai_explainer = ai.explain_video(v.title, v.subject.name if v.subject else '', v.description)
    v.save(update_fields=['ai_explainer'])
    return ok(redirect=reverse('video_detail', args=[v.pk]))


@login_required
@require_POST
def video_watched(request, pk):
    v = get_object_or_404(VideoLesson, pk=pk)
    _, created = VideoProgress.objects.get_or_create(user=request.user, video=v)
    if created:
        request.user.profile.record_activity()
    return ok()


@login_required
@require_POST
def video_delete(request, pk):
    v = get_object_or_404(VideoLesson, pk=pk, added_by=request.user)
    v.delete()
    messages.success(request, 'Video removed.')
    return redirect('videos')


# -------------------------------------------------------------------- exams
@login_required
def exams(request):
    papers = visible_materials(request.user).filter(material_type='paper')
    subject = request.GET.get('subject', '').strip()
    if subject:
        papers = papers.filter(subject__name__iexact=subject)
    return render(request, 'core/exams.html', {
        'papers': papers[:40],
        'quizzes': Quiz.objects.filter(user=request.user).select_related('subject').prefetch_related('attempts')[:12],
        'subjects': Subject.objects.all(),
        'notes': visible_materials(request.user).exclude(material_type='paper')[:50],
        'weak_topics': weak_topics_for(request.user),
        'active_subject': subject,
    })


@login_required
@require_POST
@ai_rate_limited
def quiz_create(request):
    material = None
    mid = request.POST.get('material')
    if mid:
        material = get_visible_material(request.user, mid)
    topic = request.POST.get('topic', '').strip()[:200]
    subject = Subject.resolve(request.POST.get('subject'), request.user) or (material.subject if material else None)
    if not material and not topic:
        return fail('Choose a note to quiz yourself on, or type a topic.')
    difficulty = request.POST.get('difficulty', 'medium')
    if difficulty not in ('easy', 'medium', 'hard'):
        difficulty = 'medium'
    count = to_int(request.POST.get('count'), 8, 3, 15)

    items = ai.build_quiz(topic or material.title, subject.name if subject else '', difficulty, count,
                          context=material.content if material else '')
    with transaction.atomic():
        quiz = Quiz.objects.create(user=request.user, subject=subject, material=material,
                                   title=(topic or material.title)[:200], difficulty=difficulty)
        QuizQuestion.objects.bulk_create([QuizQuestion(quiz=quiz, **i) for i in items])
    return ok(redirect=reverse('quiz_take', args=[quiz.pk]))


@login_required
def quiz_take(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk, user=request.user)
    return render(request, 'core/quiz_take.html', {'quiz': quiz, 'questions': quiz.questions.all()})


@login_required
@require_POST
def quiz_submit(request, pk):
    quiz = get_object_or_404(Quiz, pk=pk, user=request.user)
    questions = list(quiz.questions.all())
    answers, score = {}, 0
    with transaction.atomic():
        stats = {}
        for q in questions:
            raw = request.POST.get(f'q_{q.pk}')
            chosen = to_int(raw, None, 0, 3) if raw not in (None, '') else None
            answers[str(q.pk)] = chosen
            right = chosen == q.correct_index
            score += right
            key = q.topic or quiz.title
            stat = stats.get(key) or TopicStat.objects.get_or_create(user=request.user, subject=quiz.subject, topic=key)[0]
            stat.total += 1
            stat.correct += right
            stats[key] = stat
        for stat in stats.values():
            stat.save()
        attempt = QuizAttempt.objects.create(quiz=quiz, user=request.user, score=score, total=len(questions), answers=answers)
    request.user.profile.record_activity()
    request.user.profile.add_karma(5)
    return redirect('quiz_result', pk=quiz.pk, attempt_id=attempt.pk)


@login_required
def quiz_result(request, pk, attempt_id):
    quiz = get_object_or_404(Quiz, pk=pk, user=request.user)
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id, quiz=quiz)
    review = []
    for q in quiz.questions.all():
        chosen = attempt.answers.get(str(q.pk))
        review.append({'q': q, 'chosen': chosen, 'correct': chosen == q.correct_index})
    return render(request, 'core/quiz_result.html', {'quiz': quiz, 'attempt': attempt, 'review': review})


# ------------------------------------------------------------------ planner
@login_required
def planner(request):
    today = timezone.localdate()
    plans = StudyPlan.objects.filter(user=request.user).select_related('subject')
    active = None
    pid = request.GET.get('plan')
    if pid:
        active = plans.filter(pk=pid).first()
    active = active or plans.filter(exam_date__gte=today).first() or plans.first()
    days = []
    if active:
        grouped = defaultdict(list)
        for t in active.tasks.all():
            grouped[t.date].append(t)
        days = [{'date': d, 'tasks': ts, 'is_today': d == today, 'is_past': d < today}
                for d, ts in sorted(grouped.items())]
    return render(request, 'core/planner.html', {
        'plans': plans, 'active': active, 'days': days, 'today': today,
        'subjects': Subject.objects.all(),
        'weak_topics': weak_topics_for(request.user),
    })


@login_required
@require_POST
@ai_rate_limited
def plan_create(request):
    today = timezone.localdate()
    subject_name = request.POST.get('subject', '').strip()
    exam_date = parse_date(request.POST.get('exam_date', '') or '')
    if not subject_name:
        return fail('Which subject are you preparing for?')
    if not exam_date or exam_date < today:
        return fail('Pick an exam date that is today or later.')
    if (exam_date - today).days > 120:
        return fail('Plans can cover at most 120 days. Pick a nearer date.')
    hours = to_int(request.POST.get('hours'), 2, 1, 10)
    topics = request.POST.get('topics', '').strip()[:2000]
    subject = Subject.resolve(subject_name, request.user)

    material_titles = list(visible_materials(request.user).filter(subject=subject).values_list('title', flat=True)[:20])
    weak = [t.topic for t in weak_topics_for(request.user, subject)]
    if not topics and not material_titles:
        return fail('List the topics to cover (or upload notes for this subject first) so the plan is accurate.')

    tasks = ai.build_plan(subject.name, exam_date, hours, topics, weak, material_titles)
    with transaction.atomic():
        plan = StudyPlan.objects.create(user=request.user, subject=subject, exam_date=exam_date,
                                        hours_per_day=hours, title=f'{subject.name} exam prep')
        StudyTask.objects.bulk_create([StudyTask(plan=plan, **t) for t in tasks])
    return ok(redirect=reverse('planner') + f'?plan={plan.pk}')


@login_required
@require_POST
def task_toggle(request, pk):
    t = get_object_or_404(StudyTask, pk=pk, plan__user=request.user)
    t.done = not t.done
    t.save(update_fields=['done'])
    if t.done:
        request.user.profile.record_activity()
    return ok(done=t.done, progress=t.plan.progress)


@login_required
@require_POST
def plan_delete(request, pk):
    get_object_or_404(StudyPlan, pk=pk, user=request.user).delete()
    messages.success(request, 'Plan deleted.')
    return redirect('planner')


# ----------------------------------------------------------------- AI tutor
@login_required
def ask(request):
    history = ChatMessage.objects.filter(user=request.user, material__isnull=True).order_by('-id')[:30]
    return render(request, 'core/ask.html', {'history': list(history)[::-1]})


@login_required
@require_POST
@ai_rate_limited
def ask_send(request):
    message = request.POST.get('message', '').strip()[:3000]
    if not message:
        return fail('Type a question first.')
    prior = ChatMessage.objects.filter(user=request.user, material__isnull=True).order_by('-id')[:10]
    history = [(c.role, c.text) for c in prior][::-1]
    reply = ai.answer_question(message, history=history, search=True)
    ChatMessage.objects.create(user=request.user, role='user', text=message)
    ChatMessage.objects.create(user=request.user, role='model', text=reply)
    request.user.profile.record_activity()
    return ok(reply=reply)


@login_required
@require_POST
def ask_clear(request):
    ChatMessage.objects.filter(user=request.user, material__isnull=True).delete()
    return redirect('ask')


# ------------------------------------------------------------------ account
@login_required
def account(request):
    user = request.user
    profile = user.profile
    initial = {'first_name': user.first_name, 'last_name': user.last_name, 'email': user.email,
               'university': profile.university, 'target_exam': profile.target_exam, 'bio': profile.bio}
    form = AccountForm(initial=initial, user=user)
    pw_form = PasswordChangeForm(user)

    if request.method == 'POST':
        if request.POST.get('form') == 'password':
            pw_form = PasswordChangeForm(user, request.POST)
            if pw_form.is_valid():
                pw_form.save()
                update_session_auth_hash(request, pw_form.user)
                messages.success(request, 'Password changed.')
                return redirect('account')
        else:
            form = AccountForm(request.POST, request.FILES, user=user)
            if form.is_valid():
                cd = form.cleaned_data
                user.first_name, user.last_name, user.email = cd['first_name'], cd['last_name'], cd['email']
                user.save()
                profile.university, profile.target_exam, profile.bio = cd['university'], cd['target_exam'], cd['bio']
                if cd['avatar']:
                    profile.avatar = cd['avatar']
                profile.save()
                messages.success(request, 'Profile updated.')
                return redirect('account')

    return render(request, 'core/account.html', {
        'form': form, 'pw_form': pw_form, 'profile': profile,
        'stats': {
            'materials': StudyMaterial.objects.filter(owner=user).count(),
            'quizzes': QuizAttempt.objects.filter(user=user).count(),
            'videos': VideoProgress.objects.filter(user=user).count(),
            'plans': StudyPlan.objects.filter(user=user).count(),
        },
    })
