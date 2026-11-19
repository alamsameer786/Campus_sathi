from django.conf import settings

from . import ai, catalog

# (path prefix, nav key) — first match wins
_TABS = [
    ('/learn', 'learn'), ('/notes', 'notes'), ('/videos', 'videos'), ('/exams', 'exams'),
    ('/planner', 'planner'), ('/tools/resume', 'resume'), ('/tools/presentations', 'ppt'),
    ('/tools/summarize', 'summarize'), ('/tools/interview', 'interview'),
    ('/account', 'account'), ('/ask', 'ask'), ('/search', 'search'),
]


def site(request):
    path = request.path
    tab = 'home' if path == '/' else next((key for prefix, key in _TABS if path.startswith(prefix)), '')
    if path.startswith('/learn/'):
        parts = [p for p in path.split('/') if p]
        if len(parts) > 1:
            tab = 'learn:' + parts[1]
    return {
        'nav_tab': tab,
        'AI_ENABLED': ai.is_configured(),
        'MAX_UPLOAD_MB': settings.MAX_UPLOAD_MB,
        'NAV_TRACKS': [{'slug': t['slug'], 'title': t['title'], 'icon': t['icon']} for t in catalog.TRACKS],
    }
