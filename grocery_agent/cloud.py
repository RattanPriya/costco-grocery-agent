from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class SecretProvider(Protocol):
    def get(self, name: str) -> str | None:
        raise NotImplementedError


@dataclass(slots=True)
class EnvSecretProvider:
    prefix: str = ""

    def get(self, name: str) -> str | None:
        return os.environ.get(f"{self.prefix}{name}")


@dataclass(frozen=True, slots=True)
class CloudBrowserProfile:
    profile_dir: Path
    auth_state_path: Path
    login_url: str = "https://sameday.costco.com/store/costco/storefront"

    @property
    def has_saved_session(self) -> bool:
        return self.auth_state_path.exists() or self.profile_dir.exists()


class CostcoCredentialPolicy:
    """Defines the credential boundary for cloud deployments.

    The grocery agent should persist browser session state after a user-driven
    login. It should not collect or store the user's Costco password.
    """

    def __init__(self, secrets: SecretProvider) -> None:
        self.secrets = secrets

    def raw_password_available(self) -> bool:
        return self.secrets.get("COSTCO_PASSWORD") is not None

    def assert_no_raw_password_storage(self) -> None:
        if self.raw_password_available():
            raise RuntimeError(
                "COSTCO_PASSWORD is present. Remove raw password storage and use a user-authenticated persistent browser session instead."
            )


def default_cloud_browser_profile(base_dir: Path | None = None) -> CloudBrowserProfile:
    root = base_dir or Path(os.environ.get("GROCERY_AGENT_CLOUD_STATE", ".grocery_agent/cloud"))
    return CloudBrowserProfile(profile_dir=root / "chrome-profile", auth_state_path=root / "costco-auth-state.json")
