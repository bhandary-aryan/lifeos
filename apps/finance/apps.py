from django.apps import AppConfig


class FinanceConfig(AppConfig):
    name = "apps.finance"
    label = "finance"

    def ready(self):
        from . import signals  # noqa: F401
