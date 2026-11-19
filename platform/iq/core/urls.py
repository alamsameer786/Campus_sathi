from django.urls import path

from . import views, views_tools as t

urlpatterns = [
    path('', views.home, name='home'),
    path('search/', views.search, name='search'),

    path('notes/', views.notes, name='notes'),
    path('notes/upload/', views.upload, name='upload'),
    path('notes/<int:pk>/', views.material_detail, name='material_detail'),
    path('notes/<int:pk>/generate/', views.material_generate, name='material_generate'),
    path('notes/<int:pk>/ask/', views.material_ask, name='material_ask'),
    path('notes/<int:pk>/file/', views.material_file, name='material_file'),
    path('notes/<int:pk>/delete/', views.material_delete, name='material_delete'),

    path('videos/', views.videos, name='videos'),
    path('videos/add/', views.video_add, name='video_add'),
    path('videos/<int:pk>/', views.video_detail, name='video_detail'),
    path('videos/<int:pk>/explain/', views.video_explain, name='video_explain'),
    path('videos/<int:pk>/watched/', views.video_watched, name='video_watched'),
    path('videos/<int:pk>/delete/', views.video_delete, name='video_delete'),

    path('exams/', views.exams, name='exams'),
    path('exams/quiz/create/', views.quiz_create, name='quiz_create'),
    path('exams/quiz/<int:pk>/', views.quiz_take, name='quiz_take'),
    path('exams/quiz/<int:pk>/submit/', views.quiz_submit, name='quiz_submit'),
    path('exams/quiz/<int:pk>/result/<int:attempt_id>/', views.quiz_result, name='quiz_result'),

    path('planner/', views.planner, name='planner'),
    path('planner/create/', views.plan_create, name='plan_create'),
    path('planner/task/<int:pk>/toggle/', views.task_toggle, name='task_toggle'),
    path('planner/<int:pk>/delete/', views.plan_delete, name='plan_delete'),

    path('ask/', views.ask, name='ask'),
    path('ask/send/', views.ask_send, name='ask_send'),
    path('ask/clear/', views.ask_clear, name='ask_clear'),

    path('account/', views.account, name='account'),
    path('account/avatar/', t.avatar_upload, name='avatar_upload'),
    path('account/avatar/remove/', t.avatar_remove, name='avatar_remove'),
    path('feedback/', t.feedback_send, name='feedback_send'),

    path('learn/', t.learn_index, name='learn_index'),
    path('learn/<slug:track>/', t.learn_track, name='learn_track'),
    path('learn/<slug:track>/mock/', t.mock_create, name='mock_create'),
    path('learn/<slug:track>/<slug:topic>/', t.learn_topic, name='learn_topic'),
    path('learn/<slug:track>/<slug:topic>/generate/', t.learn_generate, name='learn_generate'),
    path('learn/<slug:track>/<slug:topic>/done/', t.learn_done, name='learn_done'),
    path('learn/<slug:track>/<slug:topic>/quiz/', t.learn_quiz, name='learn_quiz'),

    path('tools/resume/', t.resume_list, name='resume_list'),
    path('tools/resume/new/', t.resume_create, name='resume_create'),
    path('tools/resume/<int:pk>/', t.resume_edit, name='resume_edit'),
    path('tools/resume/<int:pk>/save/', t.resume_save, name='resume_save'),
    path('tools/resume/<int:pk>/ai/', t.resume_ai, name='resume_ai'),
    path('tools/resume/<int:pk>/docx/', t.resume_docx, name='resume_docx'),
    path('tools/resume/<int:pk>/delete/', t.resume_delete, name='resume_delete'),

    path('tools/presentations/', t.ppt_list, name='ppt_list'),
    path('tools/presentations/create/', t.ppt_create, name='ppt_create'),
    path('tools/presentations/<int:pk>/', t.ppt_view, name='ppt_view'),
    path('tools/presentations/<int:pk>/save/', t.ppt_save, name='ppt_save'),
    path('tools/presentations/<int:pk>/download/', t.ppt_download, name='ppt_download'),
    path('tools/presentations/<int:pk>/delete/', t.ppt_delete, name='ppt_delete'),

    path('tools/summarize/', t.summarize_page, name='summarize'),
    path('tools/summarize/run/', t.summarize_run, name='summarize_run'),
    path('tools/summarize/save/', t.summarize_save, name='summarize_save'),

    path('tools/interview/', t.interview_page, name='interview'),
    path('tools/interview/turn/', t.interview_turn, name='interview_turn'),

    path('assistant/send/', t.assistant_send, name='assistant_send'),

    path('privacy/', t.privacy, name='privacy'),
    path('terms/', t.terms, name='terms'),
    path('help/ai-setup/', t.ai_setup_help, name='ai_setup'),
    path('healthz/', t.healthz, name='healthz'),
    path('robots.txt', t.robots, name='robots'),
]
