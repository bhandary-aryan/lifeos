from .base import *  # noqa: F401,F403
from .base import MIDDLEWARE as _BASE_MIDDLEWARE
from .base import env

DEBUG = True
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
# Password reset emails print to the console in development.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "LifeOS <noreply@localhost>"
# Dev uses the plain storage so collectstatic is not needed.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Django's own staticfiles handler serves assets while DEBUG is on, so the production static-file middleware
# (WhiteNoise) is not needed here, and it only warns that `staticfiles/` has not been collected.
MIDDLEWARE = [m for m in _BASE_MIDDLEWARE if "whitenoise" not in m]
