import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.services import local_today
from apps.core.views import confirm_and_delete, safe_next
from apps.planner.models import Task

from .forms import InterestForm
from .models import InterestItem
from .services import all_tags, filtered_items


def _item(request, pk):
    return get_object_or_404(InterestItem.objects.select_related("linked_plan", "linked_task"), pk=pk, owner=request.user)


@login_required
def item_list(request):
    g = request.GET
    page = Paginator(filtered_items(request.user, g), 24).get_page(g.get("page"))
    params = g.copy(); params.pop("page", None)
    ctx = {"page_title": "Hobbies & Bucket List", "page_obj": page, "qs": params.urlencode(), "f": g, "categories": InterestItem.Category.choices,
           "statuses": InterestItem.Status.choices, "tags": all_tags(request.user)[:20], "has_any": InterestItem.objects.filter(owner=request.user).exists(),
           "today": local_today(request.user)}
    return render(request, "interests/item_list.html", ctx)


@login_required
def item_form(request, pk=None):
    item = _item(request, pk) if pk else None
    initial = {}
    if request.GET.get("bucket") == "1":
        initial.update(is_bucket_list=True, status=InterestItem.Status.WANT)
    form = InterestForm(request.POST or None, request.FILES or None, instance=item, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(request, "Saved.")
        return redirect("interests:detail", pk=saved.pk)
    return render(request, "interests/item_form.html", {"form": form, "page_title": "Edit item" if item else "Add something", "item": item,
                                                        "cancel_url": reverse("interests:detail", args=[item.pk]) if item else reverse("interests:list")})


@login_required
def item_detail(request, pk):
    item = _item(request, pk)
    return render(request, "interests/item_detail.html", {"page_title": item.name, "item": item, "today": local_today(request.user)})


@login_required
def item_delete(request, pk):
    item = _item(request, pk)
    return confirm_and_delete(request, item, "item", reverse("interests:detail", args=[item.pk]), reverse("interests:list"), lambda o: o.delete(), "Deleted.",
                              "Any picture is deleted too. A linked task or plan is kept." )


@login_required
@require_POST
def item_status(request, pk):
    item = _item(request, pk)
    new = request.POST.get("status")
    if new in InterestItem.Status.values:
        item.status = new
        item.save()
    return redirect(safe_next(request, reverse("interests:detail", args=[item.pk])))


@login_required
@require_POST
def item_to_task(request, pk):
    """Optional: put a bucket-list item in the planner. Nothing is scheduled unless the user asks."""
    item = _item(request, pk)
    if item.linked_task_id:
        messages.info(request, "This item already has a task.")
    elif not item.is_bucket_list:
        messages.error(request, "Only bucket-list items can be added to the planner.")
    else:
        task = Task.objects.create(owner=request.user, title=item.name, due_date=item.target_date, description=f"From your bucket list: {item.name}")
        item.linked_task = task
        item.save(update_fields=["linked_task", "updated_at"])
        messages.success(request, "Added to your planner.")
    return redirect("interests:detail", pk=item.pk)


@login_required
def item_image(request, pk):
    """Authenticated, owner-checked image delivery. Uploaded files are never reachable by URL guessing."""
    item = get_object_or_404(InterestItem, pk=pk, owner=request.user)
    if not item.image:
        raise Http404
    try:
        handle = item.image.open("rb")
    except FileNotFoundError:
        raise Http404
    ctype = mimetypes.guess_type(item.image.name)[0] or "application/octet-stream"
    if not ctype.startswith("image/"):
        ctype = "application/octet-stream"
    resp = FileResponse(handle, content_type=ctype)
    resp["Cache-Control"] = "private, max-age=3600"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return resp
