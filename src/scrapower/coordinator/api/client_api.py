"""Client API endpoints for task submission and result retrieval."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..security import verify_api_key
from ..task_manager import TaskState


def _get_client_id(request: Request) -> str:
    """Extract client_id from header, default to anonymous."""
    return request.headers.get("X-Client-ID", "anonymous")


def create_client_router(require_auth: Callable | None = None) -> APIRouter:
    router = APIRouter()

    def _check_auth(request: Request) -> None:
        if not verify_api_key(request):
            raise HTTPException(
                status_code=401,
                detail={"error": "UNAUTHORIZED", "hint": "Add X-API-Key header"},
            )

    def _check_owner(task, request: Request) -> None:
        """Verify the requester owns this task (inter-client isolation)."""
        client_id = _get_client_id(request)
        if task and task.client_id != client_id and client_id != "anonymous":
            raise HTTPException(
                status_code=403,
                detail={"error": "FORBIDDEN", "hint": f"Task belongs to {task.client_id}"},
            )

    @router.post("/tasks")
    async def create_task(request: Request):
        """Submit a new task. Requires API key."""
        if require_auth:
            _check_auth(request)
        body = await request.json()
        task_id = body.get("task_id", uuid.uuid4().hex)
        client_id = body.get("client_id", _get_client_id(request))

        task_service = request.app.state.task_service
        await task_service.submit(
            task_id=task_id,
            client_id=client_id,
            runtime=body.get("runtime", "wasm"),
            executable_hash=body.get("executable_hash", ""),
            input_hash=body.get("input_hash", ""),
            gpu_required=body.get("gpu_required", False),
        )

        return JSONResponse({"task_id": task_id, "status": "queued", "client_id": client_id})

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str, request: Request):
        """Get task status. Requires API key and ownership."""
        if require_auth:
            _check_auth(request)
        task_service = request.app.state.task_service
        task = await task_service.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
        _check_owner(task, request)
        return JSONResponse(
            {
                "task_id": task.id,
                "client_id": task.client_id,
                "status": task.state,
                "assigned_worker_id": task.assigned_worker_id,
                "runtime": task.runtime,
            }
        )

    @router.delete("/tasks/{task_id}")
    async def cancel_task(task_id: str, request: Request):
        """Cancel a task. Requires API key and ownership."""
        if require_auth:
            _check_auth(request)
        task_service = request.app.state.task_service
        task = await task_service.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
        _check_owner(task, request)
        ok = await task_service.cancel(task_id)
        if not ok:
            raise HTTPException(status_code=400, detail={"error": "NOT_FOUND_OR_TERMINAL"})
        return JSONResponse({"task_id": task_id, "status": "cancelled"})

    @router.get("/results/{task_id}")
    async def get_result(task_id: str, request: Request):
        """Get task result. Requires API key and ownership."""
        if require_auth:
            _check_auth(request)

        task_service = request.app.state.task_service
        task = await task_service.get(task_id)
        if task is None or task.state != TaskState.COMPLETED:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND_OR_NOT_READY"})
        _check_owner(task, request)

        if not task.output_hash:
            raise HTTPException(status_code=404, detail={"error": "NO_RESULT"})

        from ..blob_store import get_blob

        config = request.app.state.config
        data = await get_blob(None, config.blob_dir, task.output_hash)  # type: ignore[arg-type]
        if data is None:
            raise HTTPException(status_code=404, detail={"error": "BLOB_NOT_FOUND"})
        return Response(content=data, media_type="application/octet-stream")

    return router
