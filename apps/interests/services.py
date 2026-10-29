import datetime as dt
import hashlib

from django.db.models import F, Q

from apps.core.services import PRIORITY_RANK

from .models import InterestItem

SORTS = {"name": ["name"], "recent": ["-created_at"], "priority": ["priority_rank", "name"],
         "target": [F("target_date").asc(nulls_last=True), "name"]}


def filtered_items(user, g):
    qs = InterestItem.objects.filter(owner=user).annotate(priority_rank=PRIORITY_RANK)
    if g.get("category") in InterestItem.Category.values:
        qs = qs.filter(category=g["category"])
    if g.get("status") in InterestItem.Status.values:
        qs = qs.filter(status=g["status"])
    if g.get("bucket") == "1":
        qs = qs.filter(is_bucket_list=True)
    if g.get("tag", "").strip():
        qs = qs.filter(tags__contains=[g["tag"].strip().lower()])
    if q := g.get("q", "").strip():
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q) | Q(notes__icontains=q))
    return qs.order_by(*SORTS.get(g.get("sort"), SORTS["name"]))


def all_tags(user):
    seen = {}
    for tags in InterestItem.objects.filter(owner=user).exclude(tags=[]).values_list("tags", flat=True):
        for t in tags:
            seen[t] = seen.get(t, 0) + 1
    return sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))


def dashboard_block(user, today):
    """A gentle 'look forward to' card. No streaks, no pressure; empty when there is nothing to show."""
    base = InterestItem.objects.filter(owner=user)
    bucket_open = base.filter(is_bucket_list=True).exclude(status__in=[InterestItem.Status.DONE, InterestItem.Status.NOT_FOR_ME])
    soon = list(bucket_open.filter(target_date__gte=today, target_date__lte=today + dt.timedelta(days=60)).order_by("target_date")[:3])
    ideas = list(base.filter(status=InterestItem.Status.WANT).order_by("id").values_list("pk", "name"))
    idea = None
    if ideas:                     # stable for a whole day, then changes: deterministic, not random
        digest = int(hashlib.sha256(f"{user.pk}:{today.isoformat()}".encode()).hexdigest(), 16)
        idea = ideas[digest % len(ideas)]
    return {"soon": soon, "idea": idea, "bucket_open_count": bucket_open.count(), "has_any": base.exists()}
