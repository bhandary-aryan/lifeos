import re

from django import forms
from django.conf import settings
from PIL import Image

from apps.future_plans.models import FuturePlan
from apps.goals.forms import INPUT, SELECT, TEXTAREA, date_input
from apps.planner.models import Task

from .models import InterestItem

ALLOWED_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}


def parse_tags(raw):
    """'Rock climbing, outdoors,OUTDOORS' -> ['rock climbing', 'outdoors'] (lower-cased, de-duplicated, max 12)."""
    seen, out = set(), []
    for t in re.split(r"[,\n]", raw or ""):
        t = re.sub(r"\s+", " ", t).strip().lower()[:30]
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out[:12]


class InterestForm(forms.ModelForm):
    tags_text = forms.CharField(required=False, label="Tags", help_text="Separate with commas.", widget=forms.TextInput(attrs={"class": INPUT}))
    clear_image = forms.BooleanField(required=False, label="Remove current image")

    class Meta:
        model = InterestItem
        fields = ["name", "category", "status", "priority", "description", "image", "notes", "is_bucket_list", "target_date", "estimated_cost", "currency",
                  "linked_plan"]
        widgets = {"name": forms.TextInput(attrs={"class": INPUT}), "category": forms.Select(attrs={"class": SELECT}),
                   "status": forms.Select(attrs={"class": SELECT}), "priority": forms.Select(attrs={"class": SELECT}),
                   "description": forms.Textarea(attrs={"class": TEXTAREA, "rows": 3}), "notes": forms.Textarea(attrs={"class": TEXTAREA, "rows": 3}),
                   "image": forms.ClearableFileInput(attrs={"class": "file-input file-input-bordered w-full", "accept": "image/jpeg,image/png,image/gif,image/webp"}),
                   "target_date": date_input(), "estimated_cost": forms.NumberInput(attrs={"class": INPUT, "step": "0.01", "min": "0"}),
                   "currency": forms.Select(attrs={"class": SELECT}), "linked_plan": forms.Select(attrs={"class": SELECT})}
        labels = {"is_bucket_list": "On my bucket list", "linked_plan": "Linked future plan (optional)", "image": "Picture (optional)"}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance.owner = user
        self.fields["is_bucket_list"].widget.attrs["x-model"] = "bucket"
        self.fields["linked_plan"].queryset = FuturePlan.objects.filter(owner=user)
        self.fields["linked_plan"].required = False
        if self.instance.pk:
            self.initial["tags_text"] = ", ".join(self.instance.tags)
        else:
            self.fields["currency"].initial = user.profile.currency
        # Django's ClearableFileInput renders its own clear checkbox; use ours for a simpler, consistent form.
        self.fields["image"].widget = forms.FileInput(attrs=self.fields["image"].widget.attrs)

    def clean_image(self):
        img = self.cleaned_data.get("image")
        if not img or not hasattr(img, "content_type"):      # nothing uploaded, or the already-stored file on edit
            return img
        if getattr(img, "size", 0) > settings.MAX_UPLOAD_BYTES:
            raise forms.ValidationError(f"Images must be smaller than {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        try:
            img.seek(0)
            with Image.open(img) as im:
                if im.format not in ALLOWED_FORMATS:
                    raise forms.ValidationError("Use a JPEG, PNG, GIF or WebP image.")
                if im.width * im.height > 40_000_000:
                    raise forms.ValidationError("That image is too large in pixels.")
            img.seek(0)
        except forms.ValidationError:
            raise
        except Exception:                   # unreadable / not an image
            raise forms.ValidationError("That file isn't a valid image.")
        return img

    def clean(self):
        cleaned = super().clean()
        self.instance.tags = parse_tags(cleaned.get("tags_text", ""))
        if not cleaned.get("is_bucket_list"):
            for f in ("target_date", "estimated_cost", "linked_plan"):
                if cleaned.get(f) not in (None, ""):
                    self.add_error("is_bucket_list", "Target date, cost and plan links apply to bucket-list items. Tick 'On my bucket list' or clear them.")
                    break
        return cleaned

    def save(self, commit=True):
        old_name = None
        if self.instance.pk and "image" in self.files:      # a replacement upload: remember the file to remove afterwards
            old_name = InterestItem.objects.filter(pk=self.instance.pk).values_list("image", flat=True).first()
        item = super().save(commit=False)
        if self.cleaned_data.get("clear_image") and item.image and "image" not in self.files:
            item.image.delete(save=False)
            item.image = None
        if commit:
            item.save()
            if old_name and old_name != item.image.name:
                item.image.storage.delete(old_name)
        return item
