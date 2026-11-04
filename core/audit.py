from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.conf import settings
from django.db import models

from .models import AuditLog

logger = logging.getLogger(__name__)


def client_ip(request) -> str | None:
    """Best-effort client IP. Behind nginx we trust the first X-Forwarded-For hop."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return ip or None


def hash_ip(ip: str | None) -> str:
    """One-way, salted hash so raw participant IPs are never stored."""
    if not ip:
        return ""
    return hashlib.sha256(f"{settings.SECRET_KEY}:{ip}".encode()).hexdigest()[:32]


def log_action(
    request,
    action: str,
    obj: models.Model | None = None,
    user=None,
    **details: Any,
) -> AuditLog:
    if user is None and request is not None and getattr(request, "user", None) is not None:
        user = request.user if request.user.is_authenticated else None
    entry = AuditLog.objects.create(
        user=user,
        action=action,
        object_type=obj._meta.label if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        details=details,
        ip_address=client_ip(request),
    )
    logger.info("audit %s by %s on %s:%s", action, getattr(user, "pk", None), entry.object_type, entry.object_id)
    return entry
