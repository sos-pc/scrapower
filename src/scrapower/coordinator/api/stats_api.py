"""Infrastructure statistics endpoint — worker count, capacity, throughput."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("")
async def get_stats(request: Request):
    """Return infrastructure capacity and health metrics."""
    import scrapower.coordinator.worker_gateway.router as router_mod

    sm = getattr(router_mod, "session_manager", None)
    if sm is None:
        return JSONResponse({"error": "no session manager"}, status_code=500)

    sessions = sm.active_sessions

    # Aggregate by worker type
    by_type: dict[str, dict] = {}
    total_cpu = 0
    total_ram_mb = 0
    total_gpu = 0
    total_tasks = 0
    total_completed = 0

    for s in sessions:
        wid = s.worker_id
        if wid.startswith("browser-"):
            wtype = "browser"
        elif wid.startswith("gh-"):
            wtype = "github"
        elif wid == "_embedded":
            wtype = "embedded"
        else:
            wtype = "other"

        if wtype not in by_type:
            by_type[wtype] = {"count": 0, "cpu": 0, "ram_mb": 0, "gpu": 0, "workers": []}

        caps = s.capabilities or {}
        resources = caps.get("resources", {})
        cpu = resources.get("cpu_cores", 1)
        ram = resources.get("ram_mb", 128)
        gpu = 1 if resources.get("gpu", {}).get("supported") else 0

        by_type[wtype]["count"] += 1
        by_type[wtype]["cpu"] += cpu
        by_type[wtype]["ram_mb"] += ram
        by_type[wtype]["gpu"] += gpu
        by_type[wtype]["workers"].append(
            {
                "id": wid[:20],
                "cpu": cpu,
                "ram_mb": ram,
                "gpu": bool(gpu),
                "tasks_in_progress": s.tasks_in_progress,
            }
        )

        total_cpu += cpu
        total_ram_mb += ram
        total_gpu += gpu
        total_tasks += s.tasks_in_progress

    # Get completed task count from DB
    db = request.app.state.db if hasattr(request.app.state, "db") else None
    if db:
        cursor = await db.execute("SELECT COUNT(*) as n FROM tasks WHERE state = ?", ("validated",))
        row = await cursor.fetchone()
        if row:
            total_completed = row["n"]

    # Throughput estimate based on actual workers
    n_workers = max(len(sessions), 1)
    est_tps = n_workers / 5.0  # conservative: 1 task every 5s per worker

    return JSONResponse(
        {
            "workers_total": n_workers,
            "by_type": {
                k: {
                    "count": v["count"],
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
            "workers": [w for v in by_type.values() for w in v["workers"]],
        }
    )
