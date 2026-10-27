from django.apps import AppConfig


class GoalsConfig(AppConfig):
    name = "apps.goals"
    label = "goals"

    def ready(self):
        from . import signals  # noqa: F401
