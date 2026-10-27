from django.contrib.auth.mixins import LoginRequiredMixin


class OwnerQuerysetMixin(LoginRequiredMixin):
    """Restrict a view's queryset to the signed-in user's own records.

    Detail/update/delete views built on this return 404 (not 403) for other
    users' objects, so object IDs cannot be probed.

    WARNING: a subclass that overrides get_queryset() replaces this filter.
    Either set `queryset`/`model` and leave get_queryset alone, or filter by
    `owner=self.request.user` yourself. tests/test_ownership_phase2.py walks
    every detail/update/delete URL as another user to catch this mistake.
    """

    owner_field = "owner"

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(**{self.owner_field: self.request.user})
