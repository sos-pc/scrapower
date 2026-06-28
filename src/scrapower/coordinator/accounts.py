"""Account filter — enable/disable providers and individual accounts.

Minimal filter. v0.7 will merge with quota tracking into AccountRegistry.
"""

from __future__ import annotations


class AccountFilter:
    """Wraps a list of account dicts, filtering by enabled flags.

    Two levels:
      - provider_enabled: master switch for the entire provider
      - account["enabled"]: per-account flag (default: True)

    Exposes two lists:
      - .enabled: accounts that pass both filters (use for round-robin, launch)
      - .all:     all accounts, unfiltered (use for cleanup, quota check)
    """

    def __init__(self, accounts: list[dict], *, provider_enabled: bool = True):
        self._all = accounts
        self._provider_enabled = provider_enabled

    @property
    def enabled(self) -> list[dict]:
        """Accounts usable for launching workers."""
        if not self._provider_enabled:
            return []
        return [a for a in self._all if a.get("enabled", True)]

    @property
    def all(self) -> list[dict]:
        """All accounts, including disabled ones (cleanup, quota, stats)."""
        return self._all

    def __len__(self) -> int:
        return len(self.enabled)

    def __bool__(self) -> bool:
        return len(self.enabled) > 0

    def get(self, index: int) -> dict | None:
        """Round-robin access into enabled accounts. Returns None if empty."""
        enabled = self.enabled
        if not enabled:
            return None
        return enabled[index % len(enabled)]
