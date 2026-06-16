"""Client API endpoints for task submission and result retrieval."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..security import verify_api_key
from ..task_manager import TaskState


def create_client_router(require_auth: Callable | None = None) -> APIRouter:
    router = APIRouter()

    @router.post("/tasks")
    async def create_task(request: Request):
        """Submit a new task. Requires API key."""
        if require_auth and not verify_api_key(request):
            return JSONResponse(
                {"error": "UNAUTHORIZED", "hint": "Add X-API-Key header"}, status_code=401
            )
        body = await request.json()
        task_id = body.get("task_id", uuid.uuid4().hex)

        task_manager = request.app.state.task_manager
        await task_manager.create(
            task_id=task_id,
            client_id=body.get("client_id", "anonymous"),
            runtime=body.get("runtime", "wasm"),
            executable_hash=body.get("executable_hash", ""),
            input_hash=body.get("input_hash", ""),
            gpu_required=body.get("gpu_required", False),
        )

        return JSONResponse({"task_id": task_id, "status": "queued"})

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str, request: Request):
        """Get task status. Requires API key."""
        if require_auth and not verify_api_key(request):
            return JSONResponse({"error": "UNAUTHORIZED"}, status_code=401)
        task_manager = request.app.state.task_manager
        task = await task_manager.get(task_id)
        if task is None:
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        return JSONResponse(
            {
                "task_id": task.id,
                "status": task.state,
                "assigned_worker_id": task.assigned_worker_id,
                "runtime": task.runtime,
            }
        )

    @router.delete("/tasks/{task_id}")
    async def cancel_task(task_id: str, request: Request):
        """Cancel a task. Requires API key."""
        if require_auth and not verify_api_key(request):
            return JSONResponse({"error": "UNAUTHORIZED"}, status_code=401)
        task_manager = request.app.state.task_manager
        ok = await task_manager.transition(task_id, TaskState.CANCELLED)
        if not ok:
            return JSONResponse({"error": "NOT_FOUND_OR_TERMINAL"}, status_code=400)
        return JSONResponse({"task_id": task_id, "status": "cancelled"})

    @router.get("/results/{task_id}")
    async def get_result(task_id: str, request: Request):
        """Get task result. Requires API key."""
        if require_auth and not verify_api_key(request):
            return JSONResponse({"error": "UNAUTHORIZED"}, status_code=401)
        from fastapi.responses import Response

        task_manager = request.app.state.task_manager
        task = await task_manager.get(task_id)
        if task is None or task.state != TaskState.VALIDATED:
            return JSONResponse({"error": "NOT_FOUND_OR_NOT_READY"}, status_code=404)

        if not task.output_hash:
            return JSONResponse({"error": "NO_RESULT"}, status_code=404)

        from ..blob_store import get_blob

        config = request.app.state.config
        data = await get_blob(None, config.blob_dir, task.output_hash)  # type: ignore[arg-type]
        if data is None:
            return JSONResponse({"error": "BLOB_NOT_FOUND"}, status_code=404)
        return Response(content=data, media_type="application/octet-stream")

    return router
