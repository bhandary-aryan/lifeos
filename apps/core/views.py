from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError, RestrictedError
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView

from .mixins import OwnerQuerysetMixin


def safe_next(request, default):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return default


class ComingSoonView(LoginRequiredMixin, TemplateView):
    """Honest placeholder for modules that arrive in later phases."""

    template_name = "core/coming_soon.html"
    title = ""
    phase = ""
    description = ""
    icon = "construction"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(page_title=self.title, phase=self.phase, description=self.description, icon=self.icon)
        return ctx


class FormPageMixin:
    """Shared plumbing for owner-scoped create/update pages."""

    template_name = "components/form_page.html"
    page_title = ""
    success_message = "Saved."
    cancel_url = "/"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_cancel_url(self):
        return safe_next(self.request, str(self.cancel_url))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(page_title=self.page_title, cancel_url=self.get_cancel_url())
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, self.success_message)
        return response

    def get_success_url(self):
        return safe_next(self.request, self.get_default_success_url())


class OwnedCreateView(LoginRequiredMixin, FormPageMixin, CreateView):
    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)


class OwnedUpdateView(OwnerQuerysetMixin, FormPageMixin, UpdateView):
    pass


class OwnedDeleteView(OwnerQuerysetMixin, DeleteView):
    template_name = "components/confirm_delete.html"
    success_message = "Deleted."
    cancel_url = "/"
    noun = "item"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(page_title=f"Delete {self.noun}", noun=self.noun, cancel_url=safe_next(self.request, self.get_cancel_url()))
        return ctx

    def get_cancel_url(self):
        return str(self.cancel_url)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, self.success_message)
        return response

    def get_success_url(self):
        return safe_next(self.request, str(self.success_url))


def confirm_and_delete(request, obj, noun, cancel_url, success_url, deleter, message, extra=""):
    """GET shows a confirmation page; POST deletes via `deleter`. In-use records report a clear message, never a crash."""
    if request.method == "POST":
        try:
            deleter(obj)
        except (ProtectedError, RestrictedError, ValidationError) as exc:
            msg = " ".join(exc.messages) if isinstance(exc, ValidationError) else (
                f"This {noun} is still used by other records and can't be deleted. Remove or reassign them first.")
            messages.error(request, msg)
            return redirect(cancel_url)
        messages.success(request, message)
        return redirect(safe_next(request, success_url))
    return render(request, "components/confirm_delete.html", {"object": obj, "noun": noun, "page_title": f"Delete {noun}", "cancel_url": cancel_url, "extra": extra})
