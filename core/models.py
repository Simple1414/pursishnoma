from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """Platform staff account. Participants never need an account."""

    class Role(models.TextChoices):
        ADMIN = "admin", _("Administrator")
        RESEARCHER = "researcher", _("Researcher")

    role = models.CharField(_("role"), max_length=20, choices=Role.choices, default=Role.RESEARCHER)
    affiliation = models.CharField(_("affiliation"), max_length=255, blank=True)
    preferred_language = models.CharField(
        _("preferred language"), max_length=5, choices=settings.LANGUAGES, default="tg"
    )

    @property
    def is_platform_admin(self) -> bool:
        return self.is_active and (self.is_superuser or self.role == self.Role.ADMIN)

    @property
    def is_staff_member(self) -> bool:
        return self.is_active and (self.is_superuser or self.role in {self.Role.ADMIN, self.Role.RESEARCHER})

    def __str__(self) -> str:
        return self.get_full_name() or self.username


class AuditLog(models.Model):
    """Append-only security/audit trail."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    action = models.CharField(max_length=64, db_index=True)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["object_type", "object_id"])]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"


class SiteSettings(models.Model):
    """Singleton with public-facing organisation details."""

    organization_name = models.CharField(_("organization name"), max_length=255, default="ResearchSurvey")
    tagline = models.CharField(
        _("tagline"), max_length=255, blank=True, help_text=_("Leave empty to use the translated default tagline.")
    )
    about_text = models.TextField(_("about the platform"), blank=True)
    contact_email = models.EmailField(_("contact e-mail"), blank=True)
    contact_phone = models.CharField(_("contact phone"), max_length=64, blank=True)
    address = models.CharField(_("address"), max_length=255, blank=True)
    show_public_statistics = models.BooleanField(_("show aggregate statistics on homepage"), default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("site settings")
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="site_settings_singleton")]

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "SiteSettings":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:
        return self.organization_name
