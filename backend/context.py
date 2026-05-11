"""ServiceContext — the principal + surface info every service call receives.

Transports build a ServiceContext at the request boundary (after auth resolves)
and pass it as the first argument to every service-layer function. Services
never read HTTP/CLI/MCP request objects directly; ctx is the single seam.
"""
import uuid
from dataclasses import dataclass, field
from typing import Optional


VALID_SURFACES = {"ui", "rest", "cli", "mcp", "cron", "plugin", "webhook", "system"}
VALID_ROLES = {"admin", "user", "readonly", "system"}
VALID_SCOPES = {"read", "write", "admin"}


@dataclass
class ServiceContext:
    user_id: Optional[int] = None
    api_key_id: Optional[int] = None
    role: str = "system"
    scope: str = "admin"
    surface: str = "system"
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self):
        if self.surface not in VALID_SURFACES:
            raise ValueError(f"invalid surface: {self.surface!r}")
        if self.role not in VALID_ROLES:
            raise ValueError(f"invalid role: {self.role!r}")
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"invalid scope: {self.scope!r}")
        # System surface bypasses principal check (used for setup, migrations, internal cron).
        if self.surface != "system" and self.user_id is None and self.api_key_id is None:
            raise ValueError(
                f"ServiceContext on surface={self.surface!r} requires user_id or api_key_id"
            )

    def is_admin(self) -> bool:
        return self.role == "admin" or self.scope == "admin"

    def can_write(self) -> bool:
        return self.scope in ("write", "admin")

    def can_read(self) -> bool:
        return self.scope in ("read", "write", "admin")


def system_context(request_id: Optional[str] = None) -> ServiceContext:
    """Context for system-initiated work (setup, migrations, internal jobs)."""
    return ServiceContext(
        role="system",
        scope="admin",
        surface="system",
        request_id=request_id or str(uuid.uuid4()),
    )
