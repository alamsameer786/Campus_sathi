from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import ai
from .models import (Question, Quiz, QuizAttempt, QuizQuestion, StudyKit, StudyMaterial, StudyPlan, Subject,
                     TopicStat, VideoLesson)

NOTE = ('Photosynthesis converts light energy into chemical energy stored in glucose. '
        'It happens in chloroplasts and needs water, carbon dioxide and light.')


class BaseCase(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # the per-user AI throttle lives in the cache
        self.user = User.objects.create_user('sam', password='pass-12345-xyz')
        self.client.login(username='sam', password='pass-12345-xyz')


class AuthAndPages(BaseCase):
    def test_landing_for_anonymous_and_dashboard_for_user(self):
        self.client.logout()
        self.assertContains(self.client.get('/'), 'Ace every exam')
        self.client.login(username='sam', password='pass-12345-xyz')
        self.assertContains(self.client.get('/'), 'Welcome back')

    def test_all_pages_render_empty(self):
        for name in ['notes', 'videos', 'exams', 'planner', 'ask', 'account', 'upload', 'search', 'learn_index',
                     'resume_list', 'ppt_list', 'summarize', 'interview', 'privacy', 'terms', 'ai_setup']:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('notes')).status_code, 302)
        self.assertEqual(self.client.post(reverse('ask_send'), {'message': 'hi'}).status_code, 302)

    def test_signup_validates_and_logs_in(self):
        self.client.logout()
        r = self.client.post(reverse('signup'), {'username': 'new', 'password1': '12345678', 'password2': '12345678'})
        self.assertEqual(r.status_code, 200)  # numeric/common password rejected
        r = self.client.post(reverse('signup'), {'username': 'new', 'password1': 'Tr1cky-pass-9', 'password2': 'Tr1cky-pass-9'})
        self.assertRedirects(r, '/')
        self.assertTrue(User.objects.get(username='new').profile)


class MaterialFlow(BaseCase):
    def test_upload_text_and_ai_kit(self):
        kit = {'summary': '## Summary\nPlants make glucose.', 'one_line': 'Plants make food from light.',
               'key_points': ['Occurs in chloroplasts'], 'flashcards': [{'front': 'Where?', 'back': 'Chloroplasts'}]}
        with override_settings(GEMINI_API_KEY='x'), mock.patch('core.ai.build_study_kit', return_value=kit):
            r = self.client.post(reverse('upload'), {'title': 'Bio', 'subject': 'biology', 'content': NOTE,
                                                     'material_type': 'notes', 'auto_ai': 'on', 'is_public': 'on'})
        self.assertTrue(r.json()['ok'])
        m = StudyMaterial.objects.get()
        self.assertEqual(m.subject.name, 'biology')
        self.assertEqual(m.kit.flashcards[0]['back'], 'Chloroplasts')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.karma, 10)
        self.assertContains(self.client.get(reverse('material_detail', args=[m.pk])), 'Chloroplasts')

    def test_upload_rejects_bad_input(self):
        self.assertFalse(self.client.post(reverse('upload'), {'title': 'x'}).json()['ok'])
        bad = SimpleUploadedFile('virus.exe', b'MZ')
        r = self.client.post(reverse('upload'), {'title': 'x', 'file': bad})
        self.assertIn('Unsupported', r.json()['error'])

    def test_txt_file_upload_extracts_text(self):
        f = SimpleUploadedFile('n.txt', NOTE.encode())
        r = self.client.post(reverse('upload'), {'title': 'File', 'file': f})
        self.assertTrue(r.json()['ok'])
        self.assertIn('chloroplasts', StudyMaterial.objects.get().content)

    def test_private_material_hidden_from_others(self):
        other = User.objects.create_user('amy', password='pass-12345-xyz')
        m = StudyMaterial.objects.create(owner=other, title='Secret', content=NOTE, is_public=False)
        self.assertEqual(self.client.get(reverse('material_detail', args=[m.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('material_delete', args=[m.pk])).status_code, 404)

    def test_generate_questions_and_ai_error_is_friendly(self):
        m = StudyMaterial.objects.create(owner=self.user, title='P', content=NOTE, material_type='paper')
        items = [{'text': 'Q1', 'answer': 'A1', 'difficulty': 'easy', 'marks': 2, 'topic': 't'}]
        with mock.patch('core.ai.build_questions', return_value=items):
            self.assertTrue(self.client.post(reverse('material_generate', args=[m.pk]), {'kind': 'questions'}).json()['ok'])
        self.assertEqual(Question.objects.get().answer, 'A1')
        with mock.patch('core.ai.build_questions', side_effect=ai.AIError('quota')):
            r = self.client.post(reverse('material_generate', args=[m.pk]), {'kind': 'questions'})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()['error'], 'quota')

    def test_ask_material_uses_context(self):
        m = StudyMaterial.objects.create(owner=self.user, title='B', content=NOTE)
        with mock.patch('core.ai.answer_question', return_value='In chloroplasts.') as f:
            r = self.client.post(reverse('material_ask', args=[m.pk]), {'message': 'Where?'})
        self.assertEqual(r.json()['reply'], 'In chloroplasts.')
        self.assertEqual(f.call_args.kwargs['context'], NOTE)


class VideoFlow(BaseCase):
    def test_youtube_parsing(self):
        for url in ['https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'https://youtu.be/dQw4w9WgXcQ?t=5',
                    'https://www.youtube.com/embed/dQw4w9WgXcQ', 'https://youtube.com/shorts/dQw4w9WgXcQ']:
            self.assertEqual(VideoLesson.parse_youtube_id(url), 'dQw4w9WgXcQ', url)
        self.assertEqual(VideoLesson.parse_youtube_id('https://evil.com/watch?v=dQw4w9WgXcQ'), '')

    def test_add_watch_explain(self):
        self.assertFalse(self.client.post(reverse('video_add'), {'url': 'https://example.com', 'title': 'x'}).json()['ok'])
        r = self.client.post(reverse('video_add'), {'url': 'https://youtu.be/dQw4w9WgXcQ', 'title': 'Trees', 'subject': 'CS'})
        v = VideoLesson.objects.get()
        self.assertTrue(r.json()['ok'])
        self.assertContains(self.client.get(reverse('video_detail', args=[v.pk])), 'youtube-nocookie.com/embed/dQw4w9WgXcQ')
        self.assertTrue(self.client.post(reverse('video_watched', args=[v.pk])).json()['ok'])
        with mock.patch('core.ai.explain_video', return_value='## Core idea'):
            self.client.post(reverse('video_explain', args=[v.pk]))
        v.refresh_from_db()
        self.assertEqual(v.ai_explainer, '## Core idea')


class QuizAndPlanner(BaseCase):
    def test_quiz_scoring_and_weak_topics(self):
        items = [{'text': f'Q{i}', 'options': ['a', 'b', 'c', 'd'], 'correct_index': 1, 'explanation': 'e', 'topic': 'Cells'}
                 for i in range(3)]
        with mock.patch('core.ai.build_quiz', return_value=items):
            r = self.client.post(reverse('quiz_create'), {'topic': 'Cells', 'subject': 'Bio', 'count': '3'})
        quiz = Quiz.objects.get()
        self.assertTrue(r.json()['ok'])
        qs = list(quiz.questions.all())
        data = {f'q_{qs[0].pk}': 1, f'q_{qs[1].pk}': 0}  # 1 right, 1 wrong, 1 skipped
        r = self.client.post(reverse('quiz_submit', args=[quiz.pk]), data)
        attempt = QuizAttempt.objects.get()
        self.assertEqual((attempt.score, attempt.total), (1, 3))
        stat = TopicStat.objects.get()
        self.assertEqual((stat.correct, stat.total, stat.accuracy), (1, 3, 33))
        self.assertContains(self.client.get(r['Location']), '1/3')
        self.assertContains(self.client.get(reverse('exams')), 'Cells')  # weak-spot chip
        self.assertEqual(self.client.get(reverse('quiz_take', args=[quiz.pk])).status_code, 200)

    def test_ai_quiz_validation_drops_bad_questions(self):
        good = {'question': 'Q', 'options': ['a', 'b', 'c', 'd'], 'correct_index': 2, 'explanation': 'e', 'topic': 't'}
        bad = [{'question': 'Q', 'options': ['a', 'b'], 'correct_index': 0},
               {'question': 'Q', 'options': ['a', 'b', 'c', 'd'], 'correct_index': 9}]
        with mock.patch('core.ai.generate_json', return_value=[good, good] + bad):
            self.assertEqual(len(ai.build_quiz('t', 's', 'easy', 5)), 2)
        with mock.patch('core.ai.generate_json', return_value=bad):
            with self.assertRaises(ai.AIError):
                ai.build_quiz('t', 's', 'easy', 5)

    def test_plan_creation_and_toggle(self):
        today = timezone.localdate()
        tasks = [{'date': today, 'title': 'Read ch1', 'detail': '', 'topic': 'A', 'minutes': 30},
                 {'date': today + timedelta(days=1), 'title': 'Quiz', 'detail': '', 'topic': 'A', 'minutes': 30}]
        url = reverse('plan_create')
        self.assertFalse(self.client.post(url, {'subject': 'OS', 'exam_date': str(today - timedelta(days=1))}).json()['ok'])
        self.assertIn('topics', self.client.post(url, {'subject': 'OS', 'exam_date': str(today + timedelta(days=3))}).json()['error'])
        with mock.patch('core.ai.build_plan', return_value=tasks):
            r = self.client.post(url, {'subject': 'OS', 'exam_date': str(today + timedelta(days=3)), 'topics': 'Scheduling'})
        self.assertTrue(r.json()['ok'])
        plan = StudyPlan.objects.get()
        t = plan.tasks.first()
        self.assertEqual(self.client.post(reverse('task_toggle', args=[t.pk])).json()['progress'], 50)
        self.assertContains(self.client.get(reverse('planner')), 'Read ch1')

    def test_plan_sanitises_ai_dates(self):
        today = timezone.localdate()
        exam = today + timedelta(days=2)
        raw = [{'date': today.isoformat(), 'title': 'ok', 'detail': '', 'topic': '', 'minutes': 999},
               {'date': (exam + timedelta(days=5)).isoformat(), 'title': 'late', 'detail': '', 'topic': '', 'minutes': 30},
               {'date': 'garbage', 'title': 'bad', 'detail': '', 'topic': '', 'minutes': 30}]
        with mock.patch('core.ai.generate_json', return_value=raw):
            out = ai.build_plan('OS', exam, 2, 'x', [], [])
        self.assertEqual([t['title'] for t in out], ['ok'])
        self.assertEqual(out[0]['minutes'], 240)


class AIClient(BaseCase):
    @override_settings(GEMINI_API_KEY='')
    def test_missing_key_message(self):
        with self.assertRaisesMessage(ai.AIError, 'GEMINI_API_KEY'):
            ai.generate_text('hi')

    @override_settings(GEMINI_API_KEY='k', GEMINI_MODEL='gemini-test')
    def test_request_shape_and_parsing(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {'candidates': [{'content': {'parts': [{'text': '```json\n{"a": 1}\n```'}]}}]}
        with mock.patch('core.ai.requests.post', return_value=resp) as post:
            self.assertEqual(ai.generate_json('p', {'type': 'OBJECT'}), {'a': 1})
        args, kw = post.call_args
        self.assertIn('gemini-test:generateContent', args[0])
        self.assertEqual(kw['headers']['x-goog-api-key'], 'k')
        self.assertEqual(kw['json']['generationConfig']['responseMimeType'], 'application/json')

    @override_settings(GEMINI_API_KEY='k')
    def test_rate_limit_and_bad_key_errors(self):
        resp = mock.Mock(status_code=403)
        resp.json.return_value = {'error': {'message': 'API key not valid'}}
        with mock.patch('core.ai.requests.post', return_value=resp):
            with self.assertRaisesMessage(ai.AIError, 'rejected'):
                ai.generate_text('hi')

    @override_settings(AI_RATE_LIMIT_PER_MINUTE=2)
    def test_per_user_throttle(self):
        with mock.patch('core.ai.answer_question', return_value='ok'):
            codes = [self.client.post(reverse('ask_send'), {'message': 'x'}).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


class NewTools(BaseCase):
    def test_learning_hubs_render_and_generate_cached(self):
        for slug in ['dsa', 'engineering', 'nec', 'jobs']:
            self.assertEqual(self.client.get(reverse('learn_track', args=[slug])).status_code, 200, slug)
        self.assertEqual(self.client.get('/learn/nope/').status_code, 404)
        self.assertContains(self.client.get(reverse('learn_topic', args=['dsa', 'arrays'])), 'Arrays')
        with mock.patch('core.ai.topic_notes', return_value='## Arrays\nNotes') as m:
            url = reverse('learn_generate', args=['dsa', 'arrays'])
            self.assertTrue(self.client.post(url, {'kind': 'notes'}).json()['ok'])
            self.client.post(url, {'kind': 'notes'})  # cached: no second AI call
            self.assertEqual(m.call_count, 1)
        self.assertContains(self.client.get(reverse('learn_topic', args=['dsa', 'arrays'])), 'Notes')
        done = self.client.post(reverse('learn_done', args=['dsa', 'arrays'])).json()
        self.assertTrue(done['done'])
        self.assertEqual(self.user.profile.__class__.objects.get(user=self.user).karma, 3)

    def test_nec_track_carries_disclaimer(self):
        self.assertContains(self.client.get(reverse('learn_track', args=['nec'])), 'Nepal Engineering Council')

    def test_resume_flow_and_exports(self):
        r = self.client.post(reverse('resume_create'), {'title': 'CV'})
        self.assertEqual(r.status_code, 302)
        from .models import Resume
        res = Resume.objects.get(user=self.user)
        self.assertEqual(self.client.get(reverse('resume_edit', args=[res.pk])).status_code, 200)
        import json
        body = json.dumps({'title': 'CV2', 'template': 'modern', 'accent': '#0f766e',
                           'data': {'name': 'Sam', 'skills': ['Python'], 'experience': []}})
        self.assertTrue(self.client.post(reverse('resume_save', args=[res.pk]), {'payload': body}).json()['ok'])
        res.refresh_from_db()
        self.assertEqual(res.title, 'CV2')
        docx = self.client.get(reverse('resume_docx', args=[res.pk]))
        self.assertEqual(docx.status_code, 200)
        self.assertTrue(docx.content.startswith(b'PK'))
        with mock.patch('core.ai.resume_summary', return_value='Great summary'):
            out = self.client.post(reverse('resume_ai', args=[res.pk]), {'payload': json.dumps(
                {'action': 'summary', 'data': {'name': 'Sam', 'skills': ['Python']}})}).json()
        self.assertEqual(out['summary'], 'Great summary')

    def test_resume_is_private(self):
        from .models import Resume
        other = User.objects.create_user('eve', password='pass-12345-xyz')
        res = Resume.objects.create(user=other, title='x', data={})
        self.assertEqual(self.client.get(reverse('resume_edit', args=[res.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('resume_docx', args=[res.pk])).status_code, 404)

    def test_presentation_generate_edit_download(self):
        deck = {'title': 'AI Basics', 'subtitle': 'Intro',
                'slides': [{'title': 'What is AI', 'bullets': ['a', 'b'], 'notes': 'say hi'}]}
        with mock.patch('core.ai.build_presentation', return_value=deck):
            out = self.client.post(reverse('ppt_create'), {'topic': 'AI basics', 'count': '6'}).json()
        self.assertTrue(out['ok'])
        from .models import Presentation
        pres = Presentation.objects.get(user=self.user)
        self.assertEqual(self.client.get(out['redirect']).status_code, 200)
        import json
        self.assertTrue(self.client.post(reverse('ppt_save', args=[pres.pk]), {'payload': json.dumps(
            {'title': 'T', 'theme': 'dark', 'slides': [{'title': 'S', 'bullets': ['x'], 'notes': ''}]})}).json()['ok'])
        r = self.client.get(reverse('ppt_download', args=[pres.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b'PK'))
        self.assertIn('.pptx', r['Content-Disposition'])
        self.assertFalse(self.client.post(reverse('ppt_create'), {'topic': 'a'}).json()['ok'])

    def test_summarizer_and_save(self):
        text = 'Photosynthesis converts light into chemical energy. ' * 4
        with mock.patch('core.ai.summarize_text', return_value='- short') as m:
            out = self.client.post(reverse('summarize_run'), {'text': text, 'style': 'bullets'}).json()
        self.assertEqual(out['summary'], '- short')
        self.assertEqual(m.call_args[0][1], 'bullets')
        self.assertFalse(self.client.post(reverse('summarize_run'), {'text': 'too short'}).json()['ok'])
        saved = self.client.post(reverse('summarize_save'), {'title': 'S', 'content': '- short'}).json()
        self.assertIn('/notes/', saved['redirect'])

    def test_interview_and_assistant(self):
        with mock.patch('core.ai.interview_turn', return_value='Tell me about yourself.') as m:
            out = self.client.post(reverse('interview_turn'), {'role': 'Python developer', 'history': '[]'}).json()
        self.assertEqual(out['reply'], 'Tell me about yourself.')
        self.assertEqual(m.call_args[0][:3], ('Python developer', 'fresher', 'mixed'))
        self.assertFalse(self.client.post(reverse('interview_turn'), {'role': '', 'history': '[]'}).json()['ok'])
        with mock.patch('core.ai.assistant_reply', return_value='Hello!'):
            self.assertEqual(self.client.post(reverse('assistant_send'), {'message': 'help'}).json()['reply'], 'Hello!')

    def test_avatar_upload_and_remove(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (40, 40), 'red').save(buf, 'PNG')
        up = SimpleUploadedFile('a.png', buf.getvalue(), content_type='image/png')
        out = self.client.post(reverse('avatar_upload'), {'avatar': up}).json()
        self.assertTrue(out['ok'])
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.avatar)
        bad = SimpleUploadedFile('a.png', b'not an image', content_type='image/png')
        self.assertFalse(self.client.post(reverse('avatar_upload'), {'avatar': bad}).json()['ok'])
        self.assertTrue(self.client.post(reverse('avatar_remove')).json()['ok'])

    def test_ops_endpoints(self):
        self.client.logout()
        self.assertEqual(self.client.get('/healthz/').status_code, 200)
        self.assertContains(self.client.get('/robots.txt'), 'User-agent')
