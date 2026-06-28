"""Infrastructure statistics endpoint — accounts, quotas, workers."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/stats", tags=["stats"])
log = logging.getLogger(__name__)


@router.get("")
async def get_stats(request: Request):
    """Return infrastructure capacity and health metrics."""
    import scrapower.coordinator.worker_gateway.router as router_mod

    sm = getattr(router_mod, "session_manager", None)
    mode_b_active = sm.mode_b_active_count() if sm else 0

    # DB queries
    db = request.app.state.db if hasattr(request.app.state, "db") else None
    total_completed = 0
    gpu_tasks_queued = 0
    if db:
        cursor = await db.execute("SELECT COUNT(*) as n FROM tasks WHERE state = ?", ("completed",))
        row = await cursor.fetchone()
        if row:
            total_completed = row["n"]
        cursor = await db.execute("SELECT COUNT(*) as n FROM tasks WHERE state = ?", ("validated",))
        row = await cursor.fetchone()
        if row:
            total_completed += row["n"]
        cursor = await db.execute(
            "SELECT COUNT(*) as n FROM tasks WHERE gpu_required = 1 AND state = 'queued'"
        )
        row = await cursor.fetchone()
        if row:
            gpu_tasks_queued = row["n"]

    # Account details from registry
    registry = getattr(request.app.state, "registry", None)
    accounts = []
    if registry:
        for a in registry.all:
            accounts.append(
                {
                    "id": a.id,
                    "provider": a.provider,
                    "lifecycle": a.lifecycle,
                    "gpu_type": a.gpu_type,
                    "enabled": a.enabled,
                    "remaining_pct": round(a.remaining_pct, 1),
                    "workers_active": a.workers_active,
                    "quota_detail": a.quota_detail,
                }
            )

    return JSONResponse(
        {
            "mode_b_workers_active": mode_b_active,
            "completed_tasks": total_completed,
            "gpu_tasks_queued": gpu_tasks_queued,
            "accounts": accounts,
        }
    )
