"""Client API endpoints for task submission and result retrieval."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..security import verify_api_key
from ..task_manager import TaskState


def create_client_router(require_auth: Callable | None = None) -> APIRouter:
    router = APIRouter()

    def _check_auth(request: Request) -> None:
        """Raise HTTPException if API key is invalid."""
        if not verify_api_key(request):
            raise HTTPException(
                status_code=401,
                detail={"error": "UNAUTHORIZED", "hint": "Add X-API-Key header"},
            )

    @router.post("/tasks")
    async def create_task(request: Request):
        """Submit a new task. Requires API key."""
        if require_auth:
            _check_auth(request)
        body = await request.json()
        task_id = body.get("task_id", uuid.uuid4().hex)

        task_service = request.app.state.task_service
        await task_service.submit(
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
        if require_auth:
            _check_auth(request)
        task_service = request.app.state.task_service
        task = await task_service.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
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
        if require_auth:
            _check_auth(request)
        task_service = request.app.state.task_service
        ok = await task_service.cancel(task_id)
        if not ok:
            raise HTTPException(status_code=400, detail={"error": "NOT_FOUND_OR_TERMINAL"})
        return JSONResponse({"task_id": task_id, "status": "cancelled"})

    @router.get("/results/{task_id}")
    async def get_result(task_id: str, request: Request):
        """Get task result. Requires API key."""
        if require_auth:
            _check_auth(request)

        task_service = request.app.state.task_service
        task = await task_service.get(task_id)
        if task is None or task.state != TaskState.VALIDATED:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND_OR_NOT_READY"})

        if not task.output_hash:
            raise HTTPException(status_code=404, detail={"error": "NO_RESULT"})

        from ..blob_store import get_blob

        config = request.app.state.config
        data = await get_blob(None, config.blob_dir, task.output_hash)  # type: ignore[arg-type]
        if data is None:
            raise HTTPException(status_code=404, detail={"error": "BLOB_NOT_FOUND"})
        return Response(content=data, media_type="application/octet-stream")

    return router
