import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


# ------------------------------------------------------------------ profile
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    university = models.CharField(max_length=150, blank=True)
    target_exam = models.CharField(max_length=150, blank=True)
    karma = models.PositiveIntegerField(default=0)
    streak = models.PositiveIntegerField(default=0)
    last_active = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username}'s profile"

    def record_activity(self):
        """Keep the daily study streak up to date. Call on any study action."""
        today = timezone.localdate()
        if self.last_active == today:
            return
        if self.last_active == today - timedelta(days=1):
            self.streak += 1
        else:
            self.streak = 1
        self.last_active = today
        self.save(update_fields=['streak', 'last_active'])

    @property
    def live_streak(self):
        """Streak as it should be shown today (broken streaks read as 0)."""
        if self.last_active and self.last_active >= timezone.localdate() - timedelta(days=1):
            return self.streak
        return 0

    def add_karma(self, points):
        self.karma += points
        self.save(update_fields=['karma'])


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


# ------------------------------------------------------------------ subjects
class Subject(models.Model):
    name = models.CharField(max_length=100, unique=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @classmethod
    def resolve(cls, name, user=None):
        """Case-insensitive get-or-create from free text. Returns None for blank."""
        name = re.sub(r'\s+', ' ', (name or '')).strip()[:100]
        if not name:
            return None
        existing = cls.objects.filter(name__iexact=name).first()
        return existing or cls.objects.create(name=name, created_by=user)


# ----------------------------------------------------------------- materials
class StudyMaterial(models.Model):
    TYPES = [
        ('notes', 'Notes'),
        ('paper', 'Past exam paper'),
        ('assignment', 'Assignment'),
        ('reference', 'Reference'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='materials')
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.SET_NULL, related_name='materials')
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=200, blank=True)
    semester = models.CharField(max_length=50, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True, help_text='Exam year (past papers)')
    material_type = models.CharField(max_length=20, choices=TYPES, default='notes')
    content = models.TextField(blank=True, help_text='Extracted or pasted text')
    file = models.FileField(upload_to='materials/', null=True, blank=True)
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def word_count(self):
        return len(self.content.split())

    @property
    def reading_minutes(self):
        return max(1, round(self.word_count / 200))

    def can_view(self, user):
        return self.is_public or self.owner_id == user.id


class StudyKit(models.Model):
    """AI-generated summary, key points and flashcards for one material."""
    material = models.OneToOneField(StudyMaterial, on_delete=models.CASCADE, related_name='kit')
    summary = models.TextField()
    one_line = models.CharField(max_length=500, blank=True)
    key_points = models.JSONField(default=list)
    flashcards = models.JSONField(default=list)
    model_name = models.CharField(max_length=60, blank=True)
    generated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Study kit for {self.material.title}'


class Question(models.Model):
    DIFFICULTY = [('easy', 'Easy'), ('medium', 'Medium'), ('hard', 'Hard')]

    material = models.ForeignKey(StudyMaterial, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField()
    answer = models.TextField(blank=True)
    difficulty = models.CharField(max_length=10, choices=DIFFICULTY, default='medium')
    marks = models.PositiveIntegerField(null=True, blank=True)
    topic = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return self.text[:60]


# -------------------------------------------------------------------- videos
class VideoLesson(models.Model):
    added_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='videos')
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.SET_NULL, related_name='videos')
    title = models.CharField(max_length=200)
    url = models.URLField()
    youtube_id = models.CharField(max_length=20, blank=True)
    description = models.TextField(blank=True)
    ai_explainer = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    YT_RE = re.compile(r'(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/))([A-Za-z0-9_-]{11})')

    @classmethod
    def parse_youtube_id(cls, url):
        m = cls.YT_RE.search(url or '')
        return m.group(1) if m else ''

    @property
    def embed_url(self):
        return f'https://www.youtube-nocookie.com/embed/{self.youtube_id}' if self.youtube_id else ''

    @property
    def thumbnail(self):
        return f'https://i.ytimg.com/vi/{self.youtube_id}/hqdefault.jpg' if self.youtube_id else ''


class VideoProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='video_progress')
    video = models.ForeignKey(VideoLesson, on_delete=models.CASCADE, related_name='progress')
    watched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'video']


# -------------------------------------------------------------------- quizzes
class Quiz(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quizzes')
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.SET_NULL, related_name='quizzes')
    material = models.ForeignKey(StudyMaterial, null=True, blank=True, on_delete=models.SET_NULL, related_name='quizzes')
    title = models.CharField(max_length=200)
    difficulty = models.CharField(max_length=10, default='medium')
    time_limit = models.PositiveSmallIntegerField(null=True, blank=True, help_text='Minutes (timed mock tests)')
    track = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'quizzes'

    def __str__(self):
        return self.title

    @property
    def last_attempt(self):
        return self.attempts.first()


class QuizQuestion(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField()
    options = models.JSONField(default=list)
    correct_index = models.PositiveSmallIntegerField()
    explanation = models.TextField(blank=True)
    topic = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['id']


class QuizAttempt(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='attempts')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quiz_attempts')
    score = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    answers = models.JSONField(default=dict)  # {question_id: chosen_index}
    taken_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-taken_at']

    @property
    def percent(self):
        return round(self.score * 100 / self.total) if self.total else 0


class TopicStat(models.Model):
    """Per-topic quiz accuracy; powers the weak-spot radar and the planner."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='topic_stats')
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.CASCADE)
    topic = models.CharField(max_length=200)
    correct = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ['user', 'subject', 'topic']

    @property
    def accuracy(self):
        return round(self.correct * 100 / self.total) if self.total else 0


# -------------------------------------------------------------------- planner
class StudyPlan(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='study_plans')
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=200)
    exam_date = models.DateField()
    hours_per_day = models.FloatField(default=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['exam_date']

    def __str__(self):
        return self.title

    @property
    def progress(self):
        total = self.tasks.count()
        return round(self.tasks.filter(done=True).count() * 100 / total) if total else 0

    @property
    def days_left(self):
        return (self.exam_date - timezone.localdate()).days


class StudyTask(models.Model):
    plan = models.ForeignKey(StudyPlan, on_delete=models.CASCADE, related_name='tasks')
    date = models.DateField()
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    topic = models.CharField(max_length=200, blank=True)
    minutes = models.PositiveIntegerField(default=45)
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ['date', 'id']


# ------------------------------------------------------------------------ chat
class ChatMessage(models.Model):
    ROLES = [('user', 'User'), ('model', 'AI')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_messages')
    material = models.ForeignKey(StudyMaterial, null=True, blank=True, on_delete=models.CASCADE, related_name='chat_messages')
    role = models.CharField(max_length=10, choices=ROLES)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']


# ------------------------------------------------------------ learning hubs
class TopicContent(models.Model):
    """AI-written lesson content for a catalog topic, cached and shared by everyone."""
    KINDS = [('notes', 'Notes'), ('practice', 'Practice questions')]
    track = models.CharField(max_length=30)
    topic = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, choices=KINDS)
    content = models.TextField()
    generated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['track', 'topic', 'kind']

    def __str__(self):
        return f'{self.track}/{self.topic} ({self.kind})'


class TopicProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='topic_progress')
    track = models.CharField(max_length=30)
    topic = models.CharField(max_length=80)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'track', 'topic']


# -------------------------------------------------------------------- tools
class Resume(models.Model):
    TEMPLATES = [('modern', 'Modern'), ('classic', 'Classic'), ('minimal', 'Minimal')]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='resumes')
    title = models.CharField(max_length=120, default='My resume')
    template = models.CharField(max_length=20, choices=TEMPLATES, default='modern')
    accent = models.CharField(max_length=7, default='#3525cd')
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title


class Presentation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='presentations')
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=300)
    theme = models.CharField(max_length=20, default='indigo')
    subtitle = models.CharField(max_length=300, blank=True)
    slides = models.JSONField(default=list)  # [{title, bullets: [..], notes}]
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Feedback(models.Model):
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='feedback')
    message = models.TextField(max_length=2000)
    page = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
