import json

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, PasswordResetView
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import throttle
from .forms import (
    DeleteAccountForm,
    EmailAuthenticationForm,
    LifeAreasForm,
    PreferencesForm,
    ProfileForm,
    RegistrationForm,
)
from .models import Profile
from .services import delete_account, export_user_data


class SignInView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        email = (request.POST.get("username") or "").strip().lower()
        if throttle.login_blocked(request, email):
            form = self.get_form()
            form.full_clean = lambda: None          # do not even test the password while locked out
            form._errors, form.cleaned_data = {}, {}
            form.add_error(None, "Too many sign-in attempts. Please wait a few minutes and try again.")
            resp = self.render_to_response(self.get_context_data(form=form), status=429)
            resp["Retry-After"] = str(throttle.login_retry_after(request, email))
            return resp
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        throttle.record_login_failure(self.request, (self.request.POST.get("username") or "").strip().lower())
        return super().form_invalid(form)

    def form_valid(self, form):
        throttle.clear_login_failures(self.request, form.cleaned_data["username"])
        return super().form_valid(form)


class ThrottledPasswordResetView(PasswordResetView):
    """Same page and same response whether or not the email exists; only the request rate is limited."""

    def post(self, request, *args, **kwargs):
        if throttle.reset_blocked(request):
            form = self.get_form()
            form.full_clean = lambda: None
            form._errors, form.cleaned_data = {}, {}
            form.add_error(None, "Too many reset requests. Please wait a few minutes and try again.")
            resp = self.render_to_response(self.get_context_data(form=form), status=429)
            resp["Retry-After"] = str(throttle.WINDOW)
            return resp
        throttle.record_reset_request(request)
        return super().post(request, *args, **kwargs)


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Welcome! Your account is ready.")
        return redirect("accounts:onboarding", step=1)
    return render(request, "accounts/register.html", {"form": form})


@require_POST
def sign_out(request):
    logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("accounts:login")


ONBOARDING_STEPS = {
    1: {"title": "What should we call you?", "form": ProfileForm},
    2: {"title": "Your region and preferences", "form": PreferencesForm},
    3: {"title": "What matters in your life right now?", "form": LifeAreasForm},
}


@login_required
def onboarding(request, step):
    if step not in ONBOARDING_STEPS:
        return redirect("accounts:onboarding", step=1)
    profile = request.user.profile
    cfg = ONBOARDING_STEPS[step]
    form_cls = cfg["form"]
    is_last = step == max(ONBOARDING_STEPS)

    def advance():
        if is_last:
            profile.onboarding_completed = True
            profile.save(update_fields=["onboarding_completed", "updated_at"])
            messages.success(request, "You're all set. You can change any of this in Settings.")
            return redirect("dashboard:home")
        return redirect("accounts:onboarding", step=step + 1)

    if request.method == "POST":
        if "skip" in request.POST:
            return advance()
        if form_cls is LifeAreasForm:
            form = LifeAreasForm(request.POST)
            if form.is_valid():
                profile.life_areas = form.cleaned_data["life_areas"]
                profile.save(update_fields=["life_areas", "updated_at"])
                return advance()
        else:
            form = form_cls(request.POST, instance=profile)
            if form.is_valid():
                form.save()
                return advance()
    else:
        if form_cls is LifeAreasForm:
            form = LifeAreasForm(initial={"life_areas": profile.life_areas})
        else:
            form = form_cls(instance=profile)

    return render(
        request,
        "accounts/onboarding.html",
        {"form": form, "step": step, "total": len(ONBOARDING_STEPS), "title": cfg["title"], "page_title": "Welcome"},
    )


@login_required
def settings_view(request):
    profile = request.user.profile
    profile_form = ProfileForm(instance=profile, prefix="profile")
    prefs_form = PreferencesForm(instance=profile, prefix="prefs")
    areas_form = LifeAreasForm(initial={"life_areas": profile.life_areas}, prefix="areas")

    if request.method == "POST":
        section = request.POST.get("section")
        if section == "profile":
            profile_form = ProfileForm(request.POST, instance=profile, prefix="profile")
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Profile updated.")
                return redirect("accounts:settings")
        elif section == "prefs":
            prefs_form = PreferencesForm(request.POST, instance=profile, prefix="prefs")
            if prefs_form.is_valid():
                prefs_form.save()
                messages.success(request, "Preferences saved.")
                return redirect("accounts:settings")
        elif section == "areas":
            areas_form = LifeAreasForm(request.POST, prefix="areas")
            if areas_form.is_valid():
                profile.life_areas = areas_form.cleaned_data["life_areas"]
                profile.save(update_fields=["life_areas", "updated_at"])
                messages.success(request, "Life areas updated.")
                return redirect("accounts:settings")

    return render(
        request,
        "accounts/settings.html",
        {
            "page_title": "Settings",
            "profile_form": profile_form,
            "prefs_form": prefs_form,
            "areas_form": areas_form,
        },
    )


@login_required
@require_POST
def set_theme(request):
    theme = request.POST.get("theme")
    if theme not in Profile.Theme.values:
        return JsonResponse({"error": "Invalid theme."}, status=400)
    request.user.profile.theme = theme
    request.user.profile.save(update_fields=["theme", "updated_at"])
    return JsonResponse({"theme": theme})


@login_required
def export_data(request):
    payload = export_user_data(request.user)
    stamp = timezone.localdate().isoformat()
    resp = HttpResponse(json.dumps(payload, indent=2), content_type="application/json")
    resp["Content-Disposition"] = f'attachment; filename="lifeos-export-{stamp}.json"'
    return resp


@login_required
def delete_account_view(request):
    form = DeleteAccountForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        user = request.user
        logout(request)
        delete_account(user)
        messages.success(request, "Your account and data have been deleted.")
        return redirect("accounts:login")
    return render(request, "accounts/delete_account.html", {"form": form, "page_title": "Delete account"})


CSV_KINDS = ("tasks", "goals", "habit_logs", "journal")


@login_required
def export_csv(request, kind):
    """Simple spreadsheet-friendly exports of the user's own records. Text cells are formula-injection safe."""
    import csv

    from django.http import Http404

    from apps.core.csv_utils import csv_safe
    from apps.goals.models import Goal
    from apps.planner.models import HabitLog, Task
    from apps.reflections.models import JournalEntry

    if kind not in CSV_KINDS:
        raise Http404
    user = request.user
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="lifeos-{kind}.csv"'
    w = csv.writer(resp)
    iso = lambda v: v.isoformat() if v else ""  # noqa: E731
    if kind == "tasks":
        w.writerow(["title", "status", "priority", "due_date", "estimated_minutes", "goal", "completed_at", "description"])
        for t in Task.objects.filter(owner=user).select_related("goal").iterator():
            w.writerow([csv_safe(t.title), t.status, t.priority, iso(t.due_date), t.estimated_minutes or "", csv_safe(t.goal.title if t.goal else ""),
                        iso(t.completed_at), csv_safe(t.description)])
    elif kind == "goals":
        w.writerow(["title", "life_area", "status", "priority", "progress_mode", "manual_progress", "start_date", "target_date", "completed_at", "description"])
        for g in Goal.objects.filter(owner=user).select_related("life_area").iterator():
            w.writerow([csv_safe(g.title), csv_safe(g.life_area.name if g.life_area else ""), g.status, g.priority, g.progress_mode, g.manual_progress,
                        iso(g.start_date), iso(g.target_date), iso(g.completed_at), csv_safe(g.description)])
    elif kind == "habit_logs":
        w.writerow(["habit", "date", "note"])
        for l in HabitLog.objects.filter(owner=user).select_related("habit").order_by("date").iterator():
            w.writerow([csv_safe(l.habit.name), l.date.isoformat(), csv_safe(l.note)])
    else:
        w.writerow(["date", "title", "body"])
        for j in JournalEntry.objects.filter(owner=user).order_by("date").iterator():
            w.writerow([j.date.isoformat(), csv_safe(j.title), csv_safe(j.body)])
    return resp
