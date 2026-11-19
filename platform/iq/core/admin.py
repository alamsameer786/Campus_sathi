from django.contrib import admin

from . import models


@admin.register(models.UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'karma', 'streak', 'last_active']
    search_fields = ['user__username']


@admin.register(models.Subject)
class SubjectAdmin(admin.ModelAdmin):
    search_fields = ['name']


@admin.register(models.StudyMaterial)
class StudyMaterialAdmin(admin.ModelAdmin):
    list_display = ['title', 'material_type', 'subject', 'owner', 'is_public', 'created_at']
    list_filter = ['material_type', 'is_public', 'subject']
    search_fields = ['title', 'topic']


@admin.register(models.VideoLesson)
class VideoLessonAdmin(admin.ModelAdmin):
    list_display = ['title', 'subject', 'added_by', 'created_at']
    list_filter = ['subject']
    search_fields = ['title']


class QuizQuestionInline(admin.TabularInline):
    model = models.QuizQuestion
    extra = 0


@admin.register(models.Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'subject', 'difficulty', 'created_at']
    inlines = [QuizQuestionInline]


admin.site.register([models.StudyKit, models.Question, models.QuizAttempt, models.TopicStat,
                     models.StudyPlan, models.StudyTask, models.VideoProgress, models.ChatMessage])


@admin.register(models.TopicContent)
class TopicContentAdmin(admin.ModelAdmin):
    list_display = ['track', 'topic', 'kind', 'created_at']
    list_filter = ['track', 'kind']
    search_fields = ['topic', 'content']


@admin.register(models.Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'template', 'updated_at']


@admin.register(models.Presentation)
class PresentationAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'theme', 'created_at']


@admin.register(models.Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user', 'page', 'message']
    readonly_fields = ['created_at']
