"""Provider interface implemented by every device family."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from homekit_one.core.model import Command, Device
from homekit_one.secrets import SecretsStore


class ProviderError(RuntimeError):
    """A provider could not reach or understand a device."""


class AuthError(ProviderError):
    """Credentials are missing, rejected, or expired beyond refresh."""


class Provider(ABC):
    name: str = "base"

    def __init__(self, config: Any, secrets: SecretsStore, state_dir: Path) -> None:
        self.config = config
        self.secrets = secrets
        self.state_dir = state_dir

    async def start(self) -> None:  # noqa: B027 - optional hook
        """Connect, log in, and prepare any shared client."""

    async def stop(self) -> None:  # noqa: B027 - optional hook
        """Close network clients."""

    @abstractmethod
    async def list_devices(self) -> list[Device]:
        """Return the devices this provider currently exposes."""

    @abstractmethod
    async def refresh(self, device: Device) -> dict[Any, Any]:
        """Read the latest state. Keys are Capability values."""

    @abstractmethod
    async def execute(self, device: Device, command: Command) -> None:
        """Apply one command. Raise on failure; the manager rolls back."""
