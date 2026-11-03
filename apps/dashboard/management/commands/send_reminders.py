"""Send due email reminder digests. Run on a schedule (see docs/DEPLOYMENT.md), for example every 30 minutes:

    */30 * * * * cd /srv/lifeos && .venv/bin/python manage.py send_reminders

Safe to run as often as you like and from several machines at once: each user gets at most one digest per local day.
"""
from django.core.management.base import BaseCommand

from apps.dashboard.digest import send_due_digests


class Command(BaseCommand):
    help = "Send due email reminder digests (opt-in per user)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be sent; send nothing and change nothing.")

    def handle(self, *args, **opts):
        result = send_due_digests(dry_run=opts["dry_run"])
        prefix = "[dry run] " if opts["dry_run"] else ""
        self.stdout.write(prefix + result.summary())
