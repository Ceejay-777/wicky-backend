from .base import *

SECRET_KEY = 'django-insecure-test-key-that-is-long-enough-for-hmac-sha256'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
    # A separate in-memory database: SQLite cannot give a second connection
    # to the same in-memory DB its own transaction. Tests that touch
    # AIGenerationLog need django_db(databases=["default", "ai_log"]).
    'ai_log': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

MIGRATION_MODULES = {
    'ai': None,
}

# Admin pages render static tags; the manifest storage would need collectstatic.
STORAGES = {
    **STORAGES,
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_THROTTLE_CLASSES': [],
    'DEFAULT_THROTTLE_RATES': {},
}

# Keep SSE publishing in-process during tests (no Redis).
del EVENTSTREAM_REDIS
