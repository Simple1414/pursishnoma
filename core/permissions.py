"""Server-side authorization helpers. Never rely on hiding UI elements."""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

ViewFunc = Callable[..., Any]


def is_staff_member(user) -> bool:
    return bool(user and user.is_authenticated and getattr(user, "is_staff_member", False))


def is_platform_admin(user) -> bool:
    return bool(user and user.is_authenticated and getattr(user, "is_platform_admin", False))


def staff_required(view: ViewFunc) -> ViewFunc:
    """Logged-in administrator or researcher."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_staff_member(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def admin_required(view: ViewFunc) -> ViewFunc:
    """Logged-in platform administrator only."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_platform_admin(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


class IsStaffMember(BasePermission):
    def has_permission(self, request, view) -> bool:
        return is_staff_member(request.user)
