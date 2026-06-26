"""Scrapower worker — Mode B HTTP pull/submit client.

Connects to a Scrapower coordinator, pulls tasks, executes them
in sandboxed runtimes, and submits results.

Usage:
    from scrapower.worker.entry import main
    asyncio.run(main())
"""

from .loop import WorkerLoop

__all__ = ["WorkerLoop"]
