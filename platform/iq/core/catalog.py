"""
Curriculum catalog for the learning hubs (DSA, Engineering, Nepal licence, Jobs).

This is *structure* only (track -> group -> topic names). The actual lesson
text, practice questions and quizzes are written by Gemini on demand and cached
in the database, so there is no hardcoded lesson content to go stale.
"""
from urllib.parse import quote_plus

from django.utils.text import slugify


def _topics(*names):
    return list(names)


TRACKS = [
    {
        'slug': 'dsa',
        'title': 'DSA Mastery',
        'icon': 'account_tree',
        'tone': 'primary',
        'tagline': 'Data structures & algorithms from zero to interview-ready',
        'context': 'Computer science data structures and algorithms for coding interviews. '
                   'Use Python for code examples and state time and space complexity.',
        'video_suffixes': ['explained with visualization', 'full tutorial', 'interview problems'],
        'groups': [
            ('Foundations', _topics('Time & Space Complexity (Big-O)', 'Arrays', 'Strings', 'Recursion', 'Hashing & Hash Maps', 'Bit Manipulation')),
            ('Linear structures', _topics('Linked Lists', 'Stacks', 'Queues & Deques', 'Monotonic Stack & Queue')),
            ('Searching & sorting', _topics('Sorting Algorithms', 'Binary Search', 'Two Pointers', 'Sliding Window', 'Prefix Sums')),
            ('Trees & heaps', _topics('Binary Trees & Traversals', 'Binary Search Trees', 'Heaps & Priority Queues', 'Tries', 'Segment Trees & Fenwick Trees')),
            ('Graphs', _topics('Graph Representations', 'BFS & DFS', 'Shortest Paths (Dijkstra, Bellman-Ford)', 'Topological Sort', 'Union-Find (DSU)', 'Minimum Spanning Tree')),
            ('Problem-solving paradigms', _topics('Greedy Algorithms', 'Backtracking', 'Dynamic Programming', 'Divide and Conquer', 'String Algorithms (KMP, Rabin-Karp)')),
        ],
    },
    {
        'slug': 'engineering',
        'title': 'Engineering Library',
        'icon': 'engineering',
        'tone': 'secondary',
        'tagline': 'Notes, question practice and videos for every engineering branch',
        'context': 'Bachelor-level engineering coursework. Be rigorous with units, formulas and worked numericals.',
        'video_suffixes': ['lecture', 'one shot revision', 'solved numericals'],
        'groups': [
            ('Common first-year', _topics('Engineering Mathematics', 'Engineering Physics', 'Engineering Chemistry', 'Engineering Drawing', 'Engineering Mechanics', 'Basic Electrical Engineering', 'Engineering Economics')),
            ('Computer Engineering', _topics('Operating Systems', 'Database Management Systems', 'Computer Networks', 'Computer Organization & Architecture', 'Object-Oriented Programming', 'Software Engineering', 'Theory of Computation', 'Compiler Design', 'Digital Logic Design', 'Artificial Intelligence', 'Machine Learning Basics', 'Web Technology')),
            ('Electrical & Electronics', _topics('Circuit Theory', 'Electrical Machines', 'Power Systems', 'Control Systems', 'Analog Electronics', 'Digital Electronics', 'Signals and Systems', 'Microprocessors & Microcontrollers', 'Communication Systems', 'Power Electronics', 'Electromagnetic Theory')),
            ('Civil Engineering', _topics('Strength of Materials', 'Structural Analysis', 'Fluid Mechanics', 'Hydrology & Water Resources', 'Geotechnical Engineering', 'Surveying', 'Concrete Technology', 'Design of RCC Structures', 'Design of Steel Structures', 'Transportation Engineering', 'Environmental Engineering', 'Estimation & Costing')),
            ('Mechanical Engineering', _topics('Thermodynamics', 'Heat & Mass Transfer', 'Fluid Machinery', 'Machine Design', 'Manufacturing Processes', 'Theory of Machines', 'Internal Combustion Engines', 'Refrigeration & Air Conditioning')),
        ],
    },
    {
        'slug': 'nec',
        'title': 'Nepal Engineering Licence',
        'icon': 'verified',
        'tagline': 'Prepare for the Nepal Engineering Council (NEC) licence exam',
        'context': 'Preparation for the Nepal Engineering Council (NEC) licence examination for Nepali engineers. '
                   'Frame content around Nepali engineering practice, Nepali laws and standards, and the typical '
                   'multiple-choice exam style. Only state specific Act sections, years, clause numbers or figures '
                   'when you are certain; otherwise describe the idea in general terms and say the student should '
                   'confirm against the official text.',
        'disclaimer': 'Content is AI-generated study help. Always confirm the current syllabus, exam pattern and '
                      'laws (Acts, Building Code, procurement rules) against official Nepal Engineering Council '
                      'notices and government publications.',
        'video_suffixes': ['Nepal engineering licence exam preparation', 'NEC license exam class', 'Nepali lecture'],
        'groups': [
            ('Exam strategy', _topics('NEC Licence Exam Overview & Strategy', 'How to Plan 90 Days of Preparation', 'Time Management & Negative-Marking Tactics')),
            ('Professional & legal knowledge', _topics('Nepal Engineering Council Act & Registration Process', 'Engineering Ethics & Professional Practice', 'Public Procurement Act & Regulations', 'Nepal National Building Code (NBC)', 'Construction Business & Contract Management', 'Constitution of Nepal & Local Government (Basics)', 'Environmental Protection & EIA in Nepal', 'Occupational Safety on Construction Sites')),
            ('Management & general engineering', _topics('Engineering Mathematics (NEC level)', 'Engineering Economics & Project Appraisal', 'Project Management (CPM & PERT)', 'Quality Control & Quality Assurance', 'Disaster Risk Reduction & Earthquake-Resistant Practice')),
            ('Civil discipline', _topics('NEC Civil: Structural Engineering', 'NEC Civil: Geotechnical & Foundation Engineering', 'NEC Civil: Water Resources & Irrigation', 'NEC Civil: Highway & Transportation', 'NEC Civil: Construction Materials & Estimation')),
            ('Computer discipline', _topics('NEC Computer: Programming & Data Structures', 'NEC Computer: Databases & Information Systems', 'NEC Computer: Networks & Security', 'NEC Computer: Operating Systems & Architecture', 'NEC Computer: Software Engineering')),
            ('Electrical & electronics discipline', _topics('NEC Electrical: Power Generation, Transmission & Distribution', 'NEC Electrical: Machines & Protection', 'NEC Electronics: Analog & Digital Electronics', 'NEC Electronics: Communication & Control')),
            ('Mechanical discipline', _topics('NEC Mechanical: Thermodynamics & Energy Systems', 'NEC Mechanical: Machine Design & Manufacturing', 'NEC Mechanical: Hydropower Mechanical Equipment')),
        ],
    },
    {
        'slug': 'jobs',
        'title': 'Job Preparation',
        'icon': 'work',
        'tone': 'tertiary',
        'tagline': 'Interviews, aptitude, resumes and career strategy',
        'context': 'Job and campus-placement preparation for fresh engineering and IT graduates, with practical, honest advice.',
        'video_suffixes': ['tutorial', 'interview preparation', 'tips for freshers'],
        'groups': [
            ('Get noticed', _topics('Writing an ATS-Friendly Resume', 'LinkedIn Profile & Networking', 'Cover Letters & Job Emails', 'Building a Portfolio & GitHub Profile')),
            ('Aptitude & reasoning', _topics('Quantitative Aptitude', 'Logical Reasoning', 'Verbal Ability & Reading Comprehension', 'Data Interpretation')),
            ('Interview skills', _topics('Tell Me About Yourself', 'Behavioural Interviews (STAR Method)', 'HR Interview Questions', 'Group Discussions', 'Salary Negotiation & Offer Evaluation')),
            ('Technical interviews', _topics('Coding Interview Strategy', 'System Design Basics', 'SQL & Database Interview Questions', 'Core CS Interview Questions', 'Project Discussion & Deep-Dive Questions')),
            ('Career paths', _topics('Government & Public Service Exams (Engineering)', 'Higher Studies & Scholarships', 'Freelancing & Remote Work', 'Starting Your First 90 Days at Work')),
        ],
    },
]

_BY_SLUG = {}


def _build():
    for t in TRACKS:
        groups = []
        flat = {}
        for gtitle, names in t['groups']:
            items = []
            for name in names:
                slug = slugify(name)[:80]
                item = {'slug': slug, 'name': name, 'group': gtitle}
                items.append(item)
                flat[slug] = item
            groups.append({'title': gtitle, 'topics': items})
        t['groups'] = groups
        t.setdefault('tone', 'primary')
        t.setdefault('disclaimer', '')
        t['topic_map'] = flat
        t['topic_count'] = len(flat)
        _BY_SLUG[t['slug']] = t


_build()


def get_track(slug):
    return _BY_SLUG.get(slug)


def get_topic(track_slug, topic_slug):
    t = get_track(track_slug)
    return (t, t['topic_map'].get(topic_slug)) if t else (None, None)


def video_links(track, topic_name):
    """YouTube search links for a topic (we never hardcode video IDs that could rot)."""
    out = []
    short = topic_name.split(':')[-1].strip()
    for suffix in track['video_suffixes']:
        q = f'{short} {suffix}'
        out.append({'label': suffix.capitalize(), 'query': q,
                    'url': 'https://www.youtube.com/results?search_query=' + quote_plus(q)})
    return out


def search_topics(query, limit=12):
    q = (query or '').lower().strip()
    if not q:
        return []
    hits = []
    for t in TRACKS:
        for item in t['topic_map'].values():
            if q in item['name'].lower():
                hits.append({'track': t, 'topic': item})
    return hits[:limit]
