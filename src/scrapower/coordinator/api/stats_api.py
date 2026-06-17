"""Infrastructure statistics endpoint — worker count, capacity, throughput."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("")
async def get_stats(request: Request):
    """Return infrastructure capacity and health metrics.

    Workers from the same IP are grouped as a single machine to avoid
    double-counting CPU/RAM/GPU from multiple browser tabs.
    """
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

    # Aggregate by type
    by_type: dict[str, dict] = {}
    total_machines = 0
    total_cpu = 0
    total_ram_mb = 0
    total_gpu = 0
    total_tasks = 0
    total_completed = 0

    for ip, ip_sessions in by_ip.items():
        # For a single machine: use max values, not sum
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
            tab_count += 1

            # Determine primary type
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

    # Get completed task count from DB
    db = request.app.state.db if hasattr(request.app.state, "db") else None
    if db:
        cursor = await db.execute("SELECT COUNT(*) as n FROM tasks WHERE state = ?", ("validated",))
        row = await cursor.fetchone()
        if row:
            total_completed = row["n"]

    n_workers = max(len(sessions), 1)
    est_tps = n_workers / 5.0  # conservative: 1 task every 5s per worker

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
            "workers": worker_list,
        }
    )
