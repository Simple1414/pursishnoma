from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _

from .models import SiteSettings, User


class StyledFormMixin:
    """Applies the design-system classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                widget.attrs.setdefault("class", "rs-checkbox")
            elif isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "rs-input")
            else:
                widget.attrs.setdefault("class", "rs-input")


class LoginForm(StyledFormMixin, AuthenticationForm):
    pass


class UserCreateForm(StyledFormMixin, forms.ModelForm):
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput, strip=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "role", "affiliation", "preferred_language"]

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def save(self, commit: bool = True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class UserUpdateForm(StyledFormMixin, forms.ModelForm):
    new_password = forms.CharField(
        label=_("New password (leave empty to keep)"), widget=forms.PasswordInput, required=False, strip=False
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "role", "affiliation", "preferred_language", "is_active"]

    def clean_new_password(self):
        password = self.cleaned_data.get("new_password")
        if password:
            validate_password(password, self.instance)
        return password

    def save(self, commit: bool = True):
        user = super().save(commit=False)
        if self.cleaned_data.get("new_password"):
            user.set_password(self.cleaned_data["new_password"])
        if commit:
            user.save()
        return user


class SiteSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ["organization_name", "tagline", "about_text", "contact_email", "contact_phone", "address", "show_public_statistics"]
        widgets = {"about_text": forms.Textarea(attrs={"rows": 5})}
