import datetime as dt
import io
import json
import os
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from PIL import Image

from apps.core.services import local_today
from apps.interests.forms import parse_tags
from apps.interests.models import InterestItem
from apps.interests.services import dashboard_block, filtered_items
from apps.planner.models import Task

pytestmark = pytest.mark.django_db
DAY = dt.timedelta(days=1)


@pytest.fixture(autouse=True)
def media_dir(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    return tmp_path / "media"


@pytest.fixture
def make_item(db):
    def _make(owner, **kw):
        kw.setdefault("name", "Item")
        return InterestItem.objects.create(owner=owner, **kw)
    return _make


def image_bytes(fmt="PNG", size=(20, 20)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 50, 50)).save(buf, fmt)
    return buf.getvalue()


def upload(name="pic.png", data=None, ctype="image/png"):
    return SimpleUploadedFile(name, data if data is not None else image_bytes(), content_type=ctype)


def item_data(**over):
    d = {"name": "Rock climbing", "category": "hobby", "status": "want", "priority": "medium", "currency": "GBP"}
    d.update(over)
    return d


def files_on_disk(root):
    return [os.path.join(dp, f) for dp, _, fs in os.walk(root) for f in fs] if os.path.isdir(root) else []


# ------------------------------------------------------------------ tags
@pytest.mark.parametrize("raw,expected", [("Rock Climbing, outdoors,OUTDOORS", ["rock climbing", "outdoors"]), ("", []), (None, []), (" , ,, ", []),
                                          ("a\nb", ["a", "b"]), ("  spaced   out  ", ["spaced out"]), ("x" * 50, ["x" * 30])])
def test_parse_tags(raw, expected):
    assert parse_tags(raw) == expected


def test_parse_tags_caps_at_twelve():
    assert len(parse_tags(",".join(f"t{i}" for i in range(30)))) == 12


# ----------------------------------------------------------------- model
def test_status_done_sets_and_clears_completed_on(user, make_item):
    i = make_item(user)
    assert i.completed_on is None
    i.status = "done"; i.save()
    assert i.completed_on is not None
    i.status = "enjoy"; i.save()
    assert i.completed_on is None


def test_bucket_fields_require_bucket_flag(user, make_item):
    for kw in ({"target_date": dt.date(2030, 1, 1)}, {"estimated_cost": D("10")}):
        with pytest.raises(ValidationError):
            InterestItem(owner=user, name="x", **kw).full_clean()
    InterestItem(owner=user, name="x", is_bucket_list=True, target_date=dt.date(2030, 1, 1), estimated_cost=D("0")).full_clean()


def test_negative_cost_rejected_by_database(user):
    with pytest.raises(IntegrityError), transaction.atomic():
        InterestItem.objects.create(owner=user, name="x", is_bucket_list=True, estimated_cost=D("-1"))


def test_invalid_tags_rejected(user):
    with pytest.raises(ValidationError):
        InterestItem(owner=user, name="x", tags="not-a-list").full_clean()
    with pytest.raises(ValidationError):
        InterestItem(owner=user, name="x", tags=[1, 2]).full_clean()


# ------------------------------------------------------------------ views
def test_empty_state_has_no_pressure_wording(auth_client):
    h = auth_client.get(reverse("interests:list")).content.decode()
    assert "A place for the things you enjoy" in h and "No targets, no streaks" in h


def test_create_edit_delete_flow(auth_client, user):
    r = auth_client.post(reverse("interests:create"), item_data(tags_text="Outdoors, Fitness", description="Indoor wall first"))
    i = InterestItem.objects.get(owner=user)
    assert r.status_code == 302 and r.url == reverse("interests:detail", args=[i.pk]) and i.tags == ["outdoors", "fitness"]
    auth_client.post(reverse("interests:update", args=[i.pk]), item_data(name="Bouldering", tags_text="outdoors", status="enjoy"))
    i.refresh_from_db(); assert (i.name, i.tags, i.status) == ("Bouldering", ["outdoors"], "enjoy")
    assert auth_client.get(reverse("interests:delete", args=[i.pk])).status_code == 200 and InterestItem.objects.exists()
    auth_client.post(reverse("interests:delete", args=[i.pk]))
    assert not InterestItem.objects.exists()


def test_everything_except_name_is_optional(auth_client, user):
    assert auth_client.post(reverse("interests:create"), {"name": "Pottery", "category": "hobby", "status": "want", "priority": "medium", "currency": "GBP"}).status_code == 302
    assert auth_client.post(reverse("interests:create"), item_data(name="")).status_code == 200


def test_creating_items_never_creates_tasks_or_schedules(auth_client, user):
    auth_client.post(reverse("interests:create"), item_data(is_bucket_list="on", target_date="2030-05-01"))
    assert not Task.objects.exists()


def test_bucket_details_without_flag_rejected_by_form(auth_client, user):
    r = auth_client.post(reverse("interests:create"), item_data(target_date="2030-05-01", estimated_cost="100"))
    assert r.status_code == 200 and "bucket-list" in r.content.decode().lower() and not InterestItem.objects.exists()


def test_bucket_item_with_details_and_plan_link(auth_client, user):
    from apps.future_plans.models import FuturePlan
    plan = FuturePlan.objects.create(owner=user, title="Japan trip")
    auth_client.post(reverse("interests:create"), item_data(name="See Mount Fuji", category="travel", is_bucket_list="on", target_date="2030-05-01",
                                                            estimated_cost="1800", linked_plan=plan.pk))
    i = InterestItem.objects.get(owner=user)
    assert i.is_bucket_list and i.estimated_cost == D("1800.00") and i.linked_plan == plan
    h = auth_client.get(reverse("interests:detail", args=[i.pk])).content.decode()
    assert "£1,800.00" in h and "Japan trip" in h and "1 May 2030" in h
    assert reverse("future_plans:detail", args=[plan.pk]) in auth_client.get(reverse("interests:detail", args=[i.pk])).content.decode()


def test_list_filters_search_sort_tags_pagination(auth_client, user, make_item):
    make_item(user, name="Chess", category="game", status="enjoy", tags=["mind"], priority="low")
    make_item(user, name="Kayaking", category="activity", status="want", tags=["outdoors", "water"], priority="high")
    make_item(user, name="Dune", category="book", status="done", tags=["scifi"], description="desert planet")
    make_item(user, name="Aurora trip", category="travel", status="want", is_bucket_list=True, target_date=dt.date(2031, 2, 1))
    url = reverse("interests:list")
    def seen(**q):
        h = auth_client.get(url, q).content.decode()
        return {n for n in ("Chess", "Kayaking", "Dune", "Aurora trip") if n in h}
    assert seen() == {"Chess", "Kayaking", "Dune", "Aurora trip"}
    assert seen(category="game") == {"Chess"} and seen(status="want") == {"Kayaking", "Aurora trip"} and seen(bucket="1") == {"Aurora trip"}
    assert seen(tag="outdoors") == {"Kayaking"} and seen(tag="OUTDOORS") == {"Kayaking"} and seen(tag="nothing") == set()
    assert seen(q="desert") == {"Dune"} and seen(q="kayak") == {"Kayaking"}
    assert seen(category="bogus", status="bogus") == {"Chess", "Kayaking", "Dune", "Aurora trip"}
    h = auth_client.get(url, {"sort": "priority"}).content.decode()
    assert h.index("Kayaking") < h.index("Chess")
    h = auth_client.get(url).content.decode()
    assert h.index("Aurora trip") < h.index("Chess") < h.index("Dune")                        # alphabetical by default
    for i in range(30):
        make_item(user, name=f"Bulk {i:02d}")
    assert "Page 1 of 2" in auth_client.get(url).content.decode()


def test_tag_cloud_counts_only_own_tags(auth_client, user, other_user, make_item):
    make_item(user, tags=["a", "b"]); make_item(user, name="two", tags=["a"]); make_item(other_user, name="theirs", tags=["secret-tag"])
    from apps.interests.services import all_tags
    assert all_tags(user) == [("a", 2), ("b", 1)]
    assert "secret-tag" not in auth_client.get(reverse("interests:list")).content.decode()


def test_quick_status_changes(auth_client, user, make_item):
    i = make_item(user)
    auth_client.post(reverse("interests:status", args=[i.pk]), {"status": "done"})
    i.refresh_from_db(); assert i.status == "done" and i.completed_on
    auth_client.post(reverse("interests:status", args=[i.pk]), {"status": "bogus"})
    i.refresh_from_db(); assert i.status == "done"
    assert auth_client.get(reverse("interests:status", args=[i.pk])).status_code == 405


def test_add_bucket_item_to_planner_is_optional_and_once(auth_client, user, make_item):
    i = make_item(user, name="Skydive", is_bucket_list=True, target_date=dt.date(2030, 6, 1))
    auth_client.post(reverse("interests:to_task", args=[i.pk]))
    i.refresh_from_db(); t = Task.objects.get()
    assert i.linked_task == t and t.title == "Skydive" and t.due_date == dt.date(2030, 6, 1) and t.owner == user
    auth_client.post(reverse("interests:to_task", args=[i.pk]))
    assert Task.objects.count() == 1
    plain = make_item(user, name="Plain hobby")
    auth_client.post(reverse("interests:to_task", args=[plain.pk]))
    assert Task.objects.count() == 1
    assert auth_client.get(reverse("interests:to_task", args=[i.pk])).status_code == 405


def test_deleting_linked_task_keeps_item(auth_client, user, make_item):
    i = make_item(user, is_bucket_list=True)
    auth_client.post(reverse("interests:to_task", args=[i.pk]))
    Task.objects.all().delete()
    i.refresh_from_db(); assert i.linked_task is None and InterestItem.objects.filter(pk=i.pk).exists()


# ----------------------------------------------------------------- images
def test_upload_serve_and_headers(auth_client, user, media_dir):
    auth_client.post(reverse("interests:create"), item_data(image=upload()))
    i = InterestItem.objects.get(owner=user)
    assert i.image and i.image.name.startswith(f"interests/{user.pk}/") and "pic" not in i.image.name       # random name, not the upload's
    r = auth_client.get(reverse("interests:image", args=[i.pk]))
    assert r.status_code == 200 and r["Content-Type"] == "image/png" and r["X-Content-Type-Options"] == "nosniff"
    assert "private" in r["Cache-Control"] and "default-src 'none'" in r["Content-Security-Policy"]
    assert b"".join(r.streaming_content)[:8] == b"\x89PNG\r\n\x1a\n"
    assert reverse("interests:image", args=[i.pk]) in auth_client.get(reverse("interests:list")).content.decode()
    assert len(files_on_disk(media_dir)) == 1


def test_item_without_image_returns_404(auth_client, user, make_item):
    assert auth_client.get(reverse("interests:image", args=[make_item(user).pk])).status_code == 404


@pytest.mark.parametrize("name,data,ctype", [
    ("notes.png", b"this is just text", "image/png"),                                           # wrong content, right extension
    ("evil.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml"),
    ("page.html", b"<html><script>alert(1)</script></html>", "text/html"),
    ("empty.png", b"", "image/png"),
    ("bmp.bmp", image_bytes("BMP"), "image/bmp"),                                               # real image, format not allowed
])
def test_invalid_uploads_rejected_and_nothing_stored(auth_client, user, media_dir, name, data, ctype):
    r = auth_client.post(reverse("interests:create"), item_data(image=upload(name, data, ctype)))
    assert r.status_code == 200 and not InterestItem.objects.exists() and files_on_disk(media_dir) == []


def test_oversized_upload_rejected(auth_client, user, media_dir, settings):
    settings.MAX_UPLOAD_BYTES = 500
    data = image_bytes("PNG", (200, 200)) + b"\0" * 1000
    assert len(data) > settings.MAX_UPLOAD_BYTES
    r = auth_client.post(reverse("interests:create"), item_data(image=upload("big.png", data)))
    assert r.status_code == 200 and "smaller than" in r.content.decode() and not InterestItem.objects.exists()


@pytest.mark.parametrize("fmt,ext,ctype", [("JPEG", "jpg", "image/jpeg"), ("GIF", "gif", "image/gif"), ("WEBP", "webp", "image/webp")])
def test_allowed_formats_accepted(auth_client, user, fmt, ext, ctype):
    auth_client.post(reverse("interests:create"), item_data(image=upload(f"p.{ext}", image_bytes(fmt), ctype)))
    i = InterestItem.objects.get(owner=user)
    assert auth_client.get(reverse("interests:image", args=[i.pk]))["Content-Type"] == ctype


def test_replacing_image_removes_old_file(auth_client, user, media_dir):
    auth_client.post(reverse("interests:create"), item_data(image=upload()))
    i = InterestItem.objects.get(owner=user); old = i.image.name
    auth_client.post(reverse("interests:update", args=[i.pk]), item_data(image=upload("new.png", image_bytes(size=(30, 30)))))
    i.refresh_from_db()
    assert i.image.name != old and len(files_on_disk(media_dir)) == 1


def test_editing_without_new_upload_keeps_image(auth_client, user, media_dir):
    auth_client.post(reverse("interests:create"), item_data(image=upload()))
    i = InterestItem.objects.get(owner=user); name = i.image.name
    auth_client.post(reverse("interests:update", args=[i.pk]), item_data(name="Renamed"))
    i.refresh_from_db(); assert i.image.name == name and len(files_on_disk(media_dir)) == 1


def test_clear_image_checkbox_removes_file(auth_client, user, media_dir):
    auth_client.post(reverse("interests:create"), item_data(image=upload()))
    i = InterestItem.objects.get(owner=user)
    auth_client.post(reverse("interests:update", args=[i.pk]), item_data(clear_image="on"))
    i.refresh_from_db(); assert not i.image and files_on_disk(media_dir) == []


def test_deleting_item_removes_file(auth_client, user, media_dir):
    auth_client.post(reverse("interests:create"), item_data(image=upload()))
    i = InterestItem.objects.get(owner=user)
    auth_client.post(reverse("interests:delete", args=[i.pk]))
    assert files_on_disk(media_dir) == []


def test_account_deletion_removes_files_of_that_user_only(client, user, other_user, password, media_dir, make_item):
    for owner in (user, other_user):
        c = Client(); c.force_login(owner)
        c.post(reverse("interests:create"), item_data(image=upload()))
    assert len(files_on_disk(media_dir)) == 2
    client.force_login(user)
    client.post(reverse("accounts:delete"), {"password": password, "confirm": "on"})
    left = files_on_disk(media_dir)
    assert len(left) == 1 and f"interests{os.sep}{other_user.pk}{os.sep}" in left[0]


def test_other_user_cannot_fetch_anothers_image(other_user, auth_client, user, make_item):
    auth_client.post(reverse("interests:create"), item_data(image=upload()))
    i = InterestItem.objects.get(owner=user)
    bob = Client(); bob.force_login(other_user)
    assert bob.get(reverse("interests:image", args=[i.pk])).status_code == 404
    anon = Client()
    r = anon.get(reverse("interests:image", args=[i.pk]))
    assert r.status_code == 302 and r.url.startswith(reverse("accounts:login"))
    assert "/media/" not in auth_client.get(reverse("interests:list")).content.decode()           # no public media URL is ever rendered


# ---------------------------------------------------------------- dashboard
def test_dashboard_block_absent_when_nothing_saved(auth_client):
    assert "Something to look forward to" not in auth_client.get(reverse("dashboard:home")).content.decode()


def test_dashboard_block_soon_window_and_only_open_bucket_items(user, make_item):
    t = local_today(user)
    make_item(user, name="Soon", is_bucket_list=True, target_date=t + dt.timedelta(days=20))
    make_item(user, name="Far", is_bucket_list=True, target_date=t + dt.timedelta(days=200))
    make_item(user, name="DoneSoon", is_bucket_list=True, status="done", target_date=t + dt.timedelta(days=5))
    make_item(user, name="NotBucket", target_date=None)
    b = dashboard_block(user, t)
    assert [i.name for i in b["soon"]] == ["Soon"] and b["bucket_open_count"] == 2 and b["has_any"]


def test_idea_of_the_day_is_stable_per_day_and_only_from_want_items(user, make_item):
    t = local_today(user)
    for n in ("A", "B", "C", "D", "E"):
        make_item(user, name=n, status="want")
    make_item(user, name="Enjoyed", status="enjoy"); make_item(user, name="Finished", status="done")
    first = dashboard_block(user, t)["idea"]
    assert dashboard_block(user, t)["idea"] == first and first[1] in {"A", "B", "C", "D", "E"}
    ideas = {dashboard_block(user, t + dt.timedelta(days=i))["idea"][1] for i in range(40)}
    assert len(ideas) > 1 and ideas <= {"A", "B", "C", "D", "E"}


def test_dashboard_renders_block_without_nagging(auth_client, user, make_item):
    make_item(user, name="Learn pottery", status="want")
    h = auth_client.get(reverse("dashboard:home")).content.decode()
    assert "Something to look forward to" in h and "Learn pottery" in h
    assert "overdue" not in h.lower().split("something to look forward to")[1][:600]


# ---------------------------------------------------------------- ownership
@pytest.fixture
def alice_item(user, make_item):
    from apps.future_plans.models import FuturePlan
    plan = FuturePlan.objects.create(owner=user, title="AlicePlan")
    return make_item(user, name="AliceSecretHobby", is_bucket_list=True, linked_plan=plan, notes="AliceNotes", tags=["alicetag"])


def test_other_user_gets_404_everywhere_and_nothing_changes(other_user, alice_item):
    bob = Client(); bob.force_login(other_user)
    pk = alice_item.pk
    for name in ("detail", "update", "delete", "image"):
        assert bob.get(reverse(f"interests:{name}", args=[pk])).status_code == 404, name
    for name in ("update", "delete", "status", "to_task", "image"):
        assert bob.post(reverse(f"interests:{name}", args=[pk]), {"name": "HACKED", "status": "done", "confirm": "on"}).status_code == 404, name
    alice_item.refresh_from_db()
    assert alice_item.name == "AliceSecretHobby" and alice_item.status == "want" and not Task.objects.exists()
    for name in ("interests:list", "dashboard:home"):
        body = bob.get(reverse(name)).content.decode()
        assert "AliceSecretHobby" not in body and "alicetag" not in body


def test_bob_cannot_link_alices_plan_or_see_it(other_user, alice_item):
    bob = Client(); bob.force_login(other_user)
    r = bob.post(reverse("interests:create"), item_data(is_bucket_list="on", linked_plan=alice_item.linked_plan.pk))
    assert r.status_code == 200 and not InterestItem.objects.filter(owner=other_user).exists()
    assert "AlicePlan" not in bob.get(reverse("interests:create")).content.decode()
    assert "AliceSecretHobby" not in bob.get(reverse("interests:list"), {"q": "Alice", "tag": "alicetag"}).content.decode()


def test_model_level_ownership(other_user, alice_item, make_item):
    with pytest.raises(ValidationError):
        InterestItem(owner=other_user, name="x", is_bucket_list=True, linked_plan=alice_item.linked_plan).full_clean()
    t = Task.objects.create(owner=alice_item.owner, title="AliceTask")
    with pytest.raises(ValidationError):
        InterestItem(owner=other_user, name="x", is_bucket_list=True, linked_task=t).full_clean()


def test_anonymous_and_csrf(client, user, alice_item):
    for name, args in (("list", []), ("create", []), ("detail", [alice_item.pk])):
        r = client.get(reverse(f"interests:{name}", args=args))
        assert r.status_code == 302 and r.url.startswith(reverse("accounts:login")), name
    c = Client(enforce_csrf_checks=True); c.force_login(user)
    for name in ("delete", "status", "to_task", "update"):
        assert c.post(reverse(f"interests:{name}", args=[alice_item.pk]), {}).status_code == 403, name
    assert c.post(reverse("interests:create"), {}).status_code == 403


def test_export_covers_interests_without_image_bytes(auth_client, user, other_user, make_item):
    make_item(user, name="Mine", tags=["t"]); make_item(other_user, name="BobSecret")
    data = json.loads(auth_client.get(reverse("accounts:export")).content)
    assert [i["name"] for i in data["interests"]] == ["Mine"] and data["interests"][0]["tags"] == ["t"] and "BobSecret" not in json.dumps(data)


def test_done_date_uses_the_users_local_calendar_date(auth_client, user, make_item, monkeypatch):
    """23:30 UTC on 1 March is already 2 March in Auckland; the stored date must follow the user, not the server."""
    from django.utils import timezone
    monkeypatch.setattr(timezone, "now", lambda: dt.datetime(2026, 3, 1, 23, 30, tzinfo=dt.timezone.utc))
    user.profile.timezone = "Pacific/Auckland"; user.profile.save()
    i = make_item(user)
    auth_client.post(reverse("interests:status", args=[i.pk]), {"status": "done"})
    i.refresh_from_db()
    assert i.completed_on == dt.date(2026, 3, 2)
