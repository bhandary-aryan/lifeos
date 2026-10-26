from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class ComingSoonView(LoginRequiredMixin, TemplateView):
    """Honest placeholder for modules that arrive in later phases."""

    template_name = "core/coming_soon.html"
    title = ""
    phase = ""
    description = ""
    icon = "construction"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            page_title=self.title,
            phase=self.phase,
            description=self.description,
            icon=self.icon,
        )
        return ctx
