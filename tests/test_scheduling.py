"""Capability matching and per-account quota accounting.

These decide which worker gets which task, and how much quota the harvester
believes each account has left.
"""

from __future__ import annotations

import datetime
import types

import pytest

from scrapower.coordinator.accounts import Account, AccountRegistry
from scrapower.coordinator.domain import _match_capabilities
from scrapower.coordinator.harvester.modal import ModalHarvester

GPU_WORKER = {
    "task_types": ["whisper", "python"],
    "runtimes": ["python"],
    "resources": {"cpu_cores": 4, "ram_mb": 30720, "gpu": {"supported": True, "vram_mb": 16384}},
    "network": {"connectivity": "outgoing_only"},
    "lifecycle": {"mode": "ephemeral"},
}
CPU_WORKER = {
    "task_types": ["python"],
    "runtimes": ["python"],
    "resources": {"cpu_cores": 2, "ram_mb": 16384, "gpu": {"supported": False}},
    "network": {"connectivity": "outgoing_only"},
    "lifecycle": {"mode": "persistent"},
}


def _task(**kw):
    base = dict(
        task_type="whisper",
        runtime="python",
        gpu_required=True,
        requirements_json="{}",
        deadline_ms=900000,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_gpu_worker_matches_whisper_task():
    assert _match_capabilities(_task(), GPU_WORKER) is True


def test_cpu_worker_refuses_gpu_task():
    assert _match_capabilities(_task(), CPU_WORKER) is False


def test_worker_refuses_unsupported_task_type():
    assert _match_capabilities(_task(task_type="whisper"), CPU_WORKER) is False
    assert _match_capabilities(_task(task_type="python", gpu_required=False), CPU_WORKER) is True


def test_worker_refuses_unknown_runtime():
    assert _match_capabilities(_task(runtime="wasm"), GPU_WORKER) is False


def test_ram_requirement_is_enforced():
    assert _match_capabilities(
        _task(requirements_json='{"ram_mb": 60000}'), GPU_WORKER
    ) is False
    assert _match_capabilities(_task(requirements_json='{"ram_mb": 8192}'), GPU_WORKER) is True


def test_outbound_network_requirement_is_enforced():
    isolated = dict(GPU_WORKER, network={"connectivity": "none"})
    needs_net = _task(requirements_json='{"network": "outbound"}')
    assert _match_capabilities(needs_net, isolated) is False
    assert _match_capabilities(needs_net, GPU_WORKER) is True


def test_malformed_requirements_json_does_not_crash_dispatch():
    assert _match_capabilities(_task(requirements_json="{not json"), GPU_WORKER) is True


def test_worker_near_end_of_life_refuses_long_task():
    dying = dict(GPU_WORKER, lifecycle={"mode": "ephemeral", "expected_remaining_sec": 60})
    assert _match_capabilities(_task(deadline_ms=900000), dying) is False


# ── Per-account quota / worker counting ────────────────────────────────────


def _registry(*accounts: Account) -> AccountRegistry:
    reg = AccountRegistry()
    for a in accounts:
        reg.add(a)
    return reg


def _modal_account(aid: str, token_id: str) -> Account:
    return Account(
        id=aid,
        provider="modal",
        lifecycle="ephemeral",
        gpu_type="T4",
        gpu_vram_mb=16384,
        enabled=True,
        max_concurrent=3,
        credentials={"token_id": token_id, "token_secret": "s"},
    )


async def test_modal_counts_sandboxes_per_account_not_globally():
    """Assigning the provider-wide total to every account made the harvester
    believe it had N x the workers, so it under-launched."""
    h = ModalHarvester(account_ids=["modal:a", "modal:b"], budget_monthly_usd=30.0)
    h._sandbox_ids = ["sb-1", "sb-2", "sb-3"]
    h._sandbox_tokens = {"sb-1": ("tid-a", "s"), "sb-2": ("tid-a", "s"), "sb-3": ("tid-b", "s")}
    reg = _registry(_modal_account("modal:a", "tid-a"), _modal_account("modal:b", "tid-b"))

    assert h._active_for("modal:a", reg) == 2
    assert h._active_for("modal:b", reg) == 1


async def test_modal_billing_window_is_the_calendar_month():
    """Modal's free credit resets on the calendar month; a rolling 30-day window
    double-counted spend already covered by a fresh credit."""
    h = ModalHarvester(account_ids=["modal:a"], budget_monthly_usd=30.0)
    seen: list[datetime.datetime] = []

    async def fake_billing(account, start, end):
        seen.append(start)
        return 12.0

    h._billing_for_account = fake_billing  # type: ignore[assignment]
    reg = _registry(_modal_account("modal:a", "tid-a"))

    await h.refresh(reg)

    assert len(seen) == 1
    start = seen[0]
    assert (start.day, start.hour, start.minute, start.second) == (1, 0, 0, 0)
    now = datetime.datetime.now(datetime.UTC)
    assert (start.year, start.month) == (now.year, now.month)

    account = reg.get("modal:a")
    assert account.quota_detail["cost_mtd"] == 12.0
    assert account.remaining_pct == pytest.approx((30.0 - 12.0) / 30.0 * 100)


def test_exhausted_account_is_not_a_launch_candidate():
    poor = _modal_account("modal:poor", "t1")
    rich = _modal_account("modal:rich", "t2")
    reg = _registry(poor, rich)
    reg.update_quota("modal:poor", 1.0)
    reg.update_quota("modal:rich", 90.0)

    candidates = reg.candidates_for_task(gpu_required=True, min_quota_pct=5.0)

    assert [a.id for a in candidates] == ["modal:rich"]


def test_account_at_concurrency_limit_cannot_launch():
    a = _modal_account("modal:a", "t1")
    reg = _registry(a)
    reg.update_workers("modal:a", a.max_concurrent)

    assert a.can_launch is False
    assert reg.candidates_for_task(gpu_required=True) == []


def test_cpu_account_excluded_when_gpu_required():
    cpu = Account(
        id="hf:space",
        provider="hf",
        lifecycle="persistent",
        gpu_type="none",
        gpu_vram_mb=0,
        enabled=True,
        max_concurrent=1,
    )
    reg = _registry(cpu)

    assert reg.candidates_for_task(gpu_required=True) == []
    assert [a.id for a in reg.candidates_for_task(gpu_required=False)] == ["hf:space"]
