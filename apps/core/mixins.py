from django.contrib.auth.mixins import LoginRequiredMixin


class OwnerQuerysetMixin(LoginRequiredMixin):
    """Restrict a view's queryset to the signed-in user's own records.

    Detail/update/delete views built on this return 404 (not 403) for other
    users' objects, so object IDs cannot be probed.
    """

    owner_field = "owner"

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(**{self.owner_field: self.request.user})
