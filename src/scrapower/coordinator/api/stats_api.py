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
    if sm is None:
        return JSONResponse({"error": "no session manager"}, status_code=500)

    sessions = sm.active_sessions

    # Group workers by IP to deduplicate shared resources
    by_ip: dict[str, list] = {}
    for s in sessions:
        ip = s.peer_ip if hasattr(s, "peer_ip") and s.peer_ip else "unknown"
        by_ip.setdefault(ip, []).append(s)

    by_type: dict[str, dict] = {}
    total_machines = 0
    total_cpu = 0
    total_ram_mb = 0
    total_gpu = 0
    gpu_worker_connected = False

    for ip, ip_sessions in by_ip.items():
        max_cpu = 0
        max_ram = 0
        has_gpu = False
        tab_count = 0
        wtype = "other"

        for s in ip_sessions:
            wid = s.worker_id
            caps = s.capabilities or {}
            resources = caps.get("resources", {})
            cpu = resources.get("cpu_cores", 1)
            ram = resources.get("ram_mb", 128)
            gpu = resources.get("gpu", {}).get("supported", False)

            max_cpu = max(max_cpu, cpu)
            max_ram = max(max_ram, ram)
            if gpu:
                has_gpu = True
                gpu_worker_connected = True
            tab_count += 1

            if wid.startswith("browser-"):
                wtype = "browser"
            elif wid.startswith("gh-"):
                wtype = "github"
            elif wid == "_embedded":
                wtype = "embedded"

        total_machines += 1
        total_cpu += max_cpu
        total_ram_mb += max_ram
        if has_gpu:
            total_gpu += 1

        if wtype not in by_type:
            by_type[wtype] = {"machines": 0, "tabs": 0, "cpu": 0, "ram_mb": 0, "gpu": 0}

        by_type[wtype]["machines"] += 1
        by_type[wtype]["tabs"] += tab_count
        by_type[wtype]["cpu"] += max_cpu
        by_type[wtype]["ram_mb"] += max_ram
        if has_gpu:
            by_type[wtype]["gpu"] += 1

    # Individual worker details
    worker_list = []
    total_tasks = 0
    for s in sessions:
        caps = s.capabilities or {}
        resources = caps.get("resources", {})
        worker_list.append(
            {
                "id": s.worker_id[:20],
                "ip": s.peer_ip if hasattr(s, "peer_ip") else "?",
                "cpu": resources.get("cpu_cores", 1),
                "ram_mb": resources.get("ram_mb", 128),
                "gpu": resources.get("gpu", {}).get("supported", False),
                "tasks_in_progress": s.tasks_in_progress,
            }
        )
        total_tasks += s.tasks_in_progress

    # DB queries
    db = request.app.state.db if hasattr(request.app.state, "db") else None
    total_completed = 0
    gpu_tasks_queued = 0
    if db:
        cursor = await db.execute("SELECT COUNT(*) as n FROM tasks WHERE state = ?", ("completed",))
        row = await cursor.fetchone()
        if row:
            total_completed = row["n"]
        # Also count old "validated" state for backward compat
        cursor = await db.execute("SELECT COUNT(*) as n FROM tasks WHERE state = ?", ("validated",))
        row = await cursor.fetchone()
        if row:
            total_completed += row["n"]
        # GPU tasks waiting in queue
        cursor = await db.execute(
            "SELECT COUNT(*) as n FROM tasks WHERE gpu_required = 1 AND state = 'queued'"
        )
        row = await cursor.fetchone()
        if row:
            gpu_tasks_queued = row["n"]

    n_workers = max(len(sessions), 1)
    est_tps = n_workers / 5.0

    return JSONResponse(
        {
            "workers_total": n_workers,
            "machines_total": total_machines,
            "by_type": {
                k: {
                    "machines": v["machines"],
                    "tabs": v["tabs"],
                    "cpu_cores": v["cpu"],
                    "ram_mb": v["ram_mb"],
                    "gpu": v["gpu"],
                }
                for k, v in by_type.items()
            },
            "total_capacity": {
                "cpu_cores": total_cpu,
                "ram_mb": total_ram_mb,
                "ram_gb": round(total_ram_mb / 1024, 1),
                "gpu_workers": total_gpu,
            },
            "estimated_throughput": {
                "tasks_per_second": round(est_tps, 2),
                "tasks_per_minute": round(est_tps * 60, 0),
                "tasks_per_hour": round(est_tps * 3600, 0),
            },
            "active_tasks": total_tasks,
            "completed_tasks": total_completed,
            "gpu": {
                "gpu_worker_connected": gpu_worker_connected,
                "gpu_tasks_queued": gpu_tasks_queued,
                "needs_worker": gpu_tasks_queued > 0 and not gpu_worker_connected,
            },
            "kaggle_quota": await _get_kaggle_quota(os.environ.get("KAGGLE_ACCOUNTS", "")),
            "workers": worker_list,
        }
    )
