"""Runtime configuration for kanbantool-mcp."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Accepted truthy spellings for boolean env vars. Kept narrow on purpose:
# anything outside the set is treated as false so a typo'd value (e.g.
# ``KANBANTOOL_READ_ONLY=ture``) fails closed (writes still gated) rather
# than silently disabling the flag. Used for ``KANBANTOOL_READ_ONLY`` (and
# any future boolean env vars).
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def env_flag(name: str) -> bool:
    """Parse a boolean env var with the project-wide truthy convention.

    Returns ``True`` only when the value (case-insensitive, surrounding
    whitespace stripped) is one of ``1``/``true``/``yes``/``on``. Anything
    else — unset, empty, ``0``, ``false``, a typo — returns ``False``."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY_ENV_VALUES


@dataclass(frozen=True)
class Config:
    domain: str
    api_token: str = field(repr=False)

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}.kanbantool.com/api/v3/"

    @classmethod
    def from_env(cls) -> Config:
        domain = os.environ.get("KANBANTOOL_DOMAIN")
        api_token = os.environ.get("KANBANTOOL_API_TOKEN")
        missing = [
            name
            for name, value in (
                ("KANBANTOOL_DOMAIN", domain),
                ("KANBANTOOL_API_TOKEN", api_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Missing required environment variable(s): " + ", ".join(missing))
        assert domain is not None
        assert api_token is not None
        return cls(domain=domain, api_token=api_token)
