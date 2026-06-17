#!/usr/bin/env python3
"""Vanity Hash Finder — distributed SHA-256 brute force demo.

Usage:
    scrapower submit --wasm finder.py --input '{"prefix":"0000","seed":0,"range":100000}'

Finds a string whose SHA-256 starts with the given prefix.
Each worker searches a different seed range in parallel.
"""

import hashlib
import struct
import sys


def compute(seed: int, prefix: str, range_size: int = 100_000) -> dict:
    """Search for a seed where SHA-256(seed) starts with `prefix`.

    Returns:
        {"found": True, "seed": 12345, "hash": "0000abc..."}
        or
        {"found": False, "tested": 100000}
    """
    prefix_len = len(prefix)
    prefix_bytes = prefix.encode()

    for i in range(range_size):
        candidate = seed + i
        h = hashlib.sha256(str(candidate).encode()).hexdigest()
        if h.startswith(prefix):
            return {"found": True, "seed": candidate, "hash": h}

    return {"found": False, "tested": range_size}


if __name__ == "__main__":
    # Read input from stdin (passed by coordinator as bytes)
    import json

    input_data = sys.stdin.buffer.read().decode()
    params = json.loads(input_data)

    prefix = params.get("prefix", "000")
    seed = params.get("seed", 0)
    range_size = params.get("range", 100_000)

    result = compute(seed, prefix, range_size)

    # Write output as bytes
    sys.stdout.buffer.write(json.dumps(result).encode())
