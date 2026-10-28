from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def seed_categories(sender, instance, created, **kwargs):
    if created:
        from .services import ensure_default_categories

        ensure_default_categories(instance)
