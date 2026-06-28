"""Account registry — single source of truth for all worker accounts.

Replaces AccountFilter (v0.6). Manages per-account quota, GPU capabilities,
and enable/disable state. The harvester iterates accounts directly,
not providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Account:
    """A single worker account on a provider platform."""

    id: str  # "kaggle:piotjeremie", "modal:ak-GsE4d...", "hf:methammer/scrapower"
    provider: str  # "kaggle", "modal", "hf"
    lifecycle: str  # "ephemeral" | "persistent"
    gpu_type: str  # "T4" | "L4" | "L40S" | "A100" | "none"
    gpu_vram_mb: int
    enabled: bool
    max_concurrent: int  # max simultaneous workers from this account

    # Quota (refreshed per tick by provider)
    remaining_pct: float = 100.0
    quota_detail: dict = field(default_factory=dict)

    # Active workers (refreshed per tick by provider)
    workers_active: int = 0

    # Provider-specific credentials
    credentials: dict = field(default_factory=dict)

    @property
    def has_gpu(self) -> bool:
        return self.gpu_type != "none"

    @property
    def is_exhausted(self) -> bool:
        return self.remaining_pct < 0

    @property
    def can_launch(self) -> bool:
        """Ready to receive a new worker."""
        if not self.enabled:
            return False
        if self.is_exhausted:
            return False
        if self.lifecycle == "ephemeral" and self.workers_active >= self.max_concurrent:
            return False
        return True


class AccountRegistry:
    """Central registry of all worker accounts across all providers.

    The harvester queries this directly instead of iterating providers.
    Providers call update_quota() and update_workers() each tick.
    """

    def __init__(self):
        self._accounts: dict[str, Account] = {}

    def add(self, account: Account) -> None:
        self._accounts[account.id] = account

    def get(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    @property
    def all(self) -> list[Account]:
        return list(self._accounts.values())

    @property
    def enabled(self) -> list[Account]:
        return [a for a in self._accounts.values() if a.enabled]

    def by_provider(self, provider: str) -> list[Account]:
        return [a for a in self._accounts.values() if a.provider == provider]

    def update_quota(
        self, account_id: str, remaining_pct: float, detail: dict | None = None
    ) -> None:
        a = self._accounts.get(account_id)
        if a:
            a.remaining_pct = remaining_pct
            if detail is not None:
                a.quota_detail = detail

    def update_workers(self, account_id: str, count: int) -> None:
        a = self._accounts.get(account_id)
        if a:
            a.workers_active = count

    def best_for_task(
        self, *, gpu_required: bool = False, min_quota_pct: float = 5.0
    ) -> Account | None:
        """Return the best eligible account for a task.

        Sorts by remaining quota (descending), GPU capability (GPU first).
        Accounts with quota < min_quota_pct are excluded.
        """
        candidates = [
            a
            for a in self._accounts.values()
            if a.can_launch and a.remaining_pct >= min_quota_pct and (not gpu_required or a.has_gpu)
        ]
        if not candidates:
            return None
        # Sort: highest quota first, GPU accounts before CPU
        candidates.sort(key=lambda a: (a.remaining_pct, int(a.has_gpu)), reverse=True)
        return candidates[0]

    def __len__(self) -> int:
        return len(self._accounts)

    def __bool__(self) -> bool:
        return len(self._accounts) > 0
