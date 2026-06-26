"""Modal worker entrypoint — thin wrapper around scrapower.worker.

The Modal sandbox receives the worker package via image.add_local_dir()
and runs this entrypoint. All worker logic lives in src/scrapower/worker/.
"""

import asyncio
import sys
from pathlib import Path

# Ensure the worker package is importable (mounted at /opt/scrapower-worker)
sys.path.insert(0, "/opt/scrapower-worker")

from scrapower.worker.entry import main

asyncio.run(main())
