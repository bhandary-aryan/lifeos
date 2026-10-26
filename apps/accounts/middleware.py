from django.utils import timezone

from apps.core.services import user_zone


class UserTimezoneMiddleware:
    """Activate the signed-in user's timezone for the duration of the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            timezone.activate(user_zone(user))
        else:
            timezone.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
