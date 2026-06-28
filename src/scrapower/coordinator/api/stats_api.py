"""Infrastructure statistics endpoint — worker count, capacity, throughput."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/stats", tags=["stats"])
log = logging.getLogger(__name__)


async def _get_kaggle_quota(accounts_json: str) -> list[dict]:
    """Fetch GPU quota for each Kaggle account. Returns list of {username, used_h, remaining_h, total_h}."""
    if not accounts_json:
        return []
    try:
        accounts = json.loads(accounts_json)
    except json.JSONDecodeError:
        return []

    results = []
    for account in accounts:
        try:
            env = os.environ.copy()
            env["KAGGLE_API_TOKEN"] = account["token"]
            proc = await asyncio.create_subprocess_exec(
                "kaggle",
                "quota",
                "--csv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode != 0:
                continue
            # Parse CSV: resource,used,remaining,total,refreshAt
            lines = stdout.decode().strip().split("\n")
            for line in lines[1:]:  # skip header
                parts = line.split(",")
                if len(parts) < 4 or parts[0] != "GPU":
                    continue
                used = parts[1].rstrip("h")
                remaining = parts[2].rstrip("h")
                total = parts[3].rstrip("h")
                results.append(
                    {
                        "username": account["username"],
                        "used_h": float(used),
                        "remaining_h": float(remaining),
                        "total_h": float(total),
                    }
                )
        except Exception:
            pass
    return results


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

    # Worker details from providers
    workers = []
    providers = getattr(request.app.state, "providers", None) or []
    for p in providers:
        try:
            status = await p.status()
            workers.append(
                {
                    "provider": status.name,
                    "gpu_type": status.gpu_type,
                    "remaining_pct": round(status.remaining_pct, 1),
                    "workers_active": status.workers_active,
                    "quota_detail": status.quota_detail,
                }
            )
        except Exception:
            pass

    return JSONResponse(
        {
            "mode_b_workers_active": mode_b_active,
            "completed_tasks": total_completed,
            "gpu_tasks_queued": gpu_tasks_queued,
            "workers": workers,
            "kaggle_quota": await _get_kaggle_quota(os.environ.get("KAGGLE_ACCOUNTS", "")),
            "modal_billing": await _get_modal_billing(os.environ.get("MODAL_ACCOUNTS", "")),
        }
    )


async def _get_modal_billing(accounts_json: str) -> dict:
    """Fetch billing info for Modal accounts. Returns {total_cost, monthly_budget, remaining_pct}."""
    if not accounts_json:
        return {"accounts": [], "total_cost": 0, "monthly_budget": 0, "remaining_pct": 100}
    try:
        accounts = json.loads(accounts_json)
    except json.JSONDecodeError:
        return {"accounts": [], "error": "invalid JSON"}

    total_cost = 0.0
    monthly_budget = 30.0 * len(accounts)
    acct_list = []
    for a in accounts:
        try:
            import modal

            client = modal.Client.from_credentials(a["token_id"], a["token_secret"])
            ws = modal.Workspace.from_context(client=client)
            import datetime

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            start = now_utc - datetime.timedelta(days=30)
            report = await ws.billing.report.aio(start=start, end=now_utc, resolution="d")
            cost = sum(float(item.get("cost", 0)) for item in report)
            total_cost += cost
            acct_list.append({"token_id": a["token_id"][:8] + "...", "cost_30d": round(cost, 2)})
        except Exception:
            acct_list.append({"token_id": a.get("token_id", "?")[:8] + "...", "cost_30d": None})

    remaining = max(0.0, monthly_budget - total_cost)
    return {
        "accounts": acct_list,
        "total_cost_30d": round(total_cost, 2),
        "monthly_budget": monthly_budget,
        "remaining": round(remaining, 2),
        "remaining_pct": round(remaining / monthly_budget * 100, 1) if monthly_budget > 0 else 0,
    }
