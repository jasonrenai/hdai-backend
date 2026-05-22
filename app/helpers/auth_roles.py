"""JWT / user role checks shared across controllers."""

from typing import Any, Optional

# Roles that may perform admin-only API actions (list users, speaker profile admin views, etc.)
_ADMIN_ROLE_VALUES = frozenset({"admin", "super_admin"})


def _normalize_role(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value).strip()


def jwt_user_type(jwt_payload: Optional[dict]) -> Optional[str]:
    """Resolve role from JWT payload (supports common key variants)."""
    if not jwt_payload:
        return None
    for key in ("userType", "user_type", "role"):
        role = _normalize_role(jwt_payload.get(key))
        if role:
            return role
    return None


def is_admin_role(user_type: Optional[str]) -> bool:
    """True if user_type is admin or super_admin."""
    return _normalize_role(user_type) in _ADMIN_ROLE_VALUES
