"""Runtime configuration for kanbantool-mcp."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Narrow on purpose so typo'd values (``ture``) fail closed.
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def env_flag(name: str) -> bool:
    """Parse a boolean env var with the project-wide truthy convention."""
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
