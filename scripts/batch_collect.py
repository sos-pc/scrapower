"""Batch collector — poll and save transcriptions to files.

Usage:
  python scripts/batch_collect.py <batch_output.json> [--output-dir ./transcripts]

1. Submit a batch:  curl -X POST .../transcribe/batch -d '{...}' > batch.json
2. Collect results: python scripts/batch_collect.py batch.json
3. Poll status:     python scripts/batch_collect.py batch.json --poll --interval 30

Saves each transcript to {output_dir}/{task_id}.json and {task_id}.txt.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

COORDINATOR = os.environ.get("SCRAPOWER_COORDINATOR_URL")
if not COORDINATOR:
    raise RuntimeError("SCRAPOWER_COORDINATOR_URL environment variable is required")
API_KEY = os.environ.get("SCRAPOWER_API_KEY", "")


def poll_task(task_id: str) -> dict | None:
    """Get task result. Returns None if not ready."""
    req = urllib.request.Request(
        f"{COORDINATOR}/results/{task_id}",
        headers={"X-API-Key": API_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # not ready yet
        raise
    return None


def get_status(task_id: str) -> str:
    """Get task status text."""
    req = urllib.request.Request(
        f"{COORDINATOR}/tasks/{task_id}",
        headers={"X-API-Key": API_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            return data.get("state", "unknown")
    except Exception:
        return "error"


def save_transcript(task_id: str, title: str, data: dict, output_dir: str):
    """Save transcript to files."""
    os.makedirs(output_dir, exist_ok=True)

    # JSON (full)
    json_path = os.path.join(output_dir, f"{task_id}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # TXT (text only)
    segments = data.get("segments", [])
    text = " ".join(s.get("text", "") for s in segments)
    txt_path = os.path.join(output_dir, f"{task_id}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n")
        f.write(f"# Language: {data.get('language', '?')}\n")
        f.write(f"# Duration: {data.get('duration', 0):.0f}s\n\n")
        f.write(text)

    return json_path, txt_path


def main():
    parser = argparse.ArgumentParser(description="Collect batch transcription results")
    parser.add_argument("batch_file", help="JSON file from POST /transcribe/batch")
    parser.add_argument("--output-dir", default="./transcripts", help="Output directory")
    parser.add_argument("--poll", action="store_true", help="Poll until all complete")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval (seconds)")
    args = parser.parse_args()

    # Load batch data
    with open(args.batch_file) as f:
        batch = json.load(f)

    tasks = batch.get("tasks", [])
    if not tasks:
        print("No tasks in batch file")
        return

    print(f"Batch: {batch.get('batch_id', '?')} — {len(tasks)} videos")

    completed = set()
    failed = set()
    total = len(tasks)

    while True:
        for t in tasks:
            tid = t["task_id"]
            if tid in completed or tid in failed:
                continue

            status = get_status(tid)
            if status == "completed":
                data = poll_task(tid)
                if data:
                    json_path, txt_path = save_transcript(
                        tid, t.get("title", ""), data, args.output_dir
                    )
                    completed.add(tid)
                    title_short = t.get("title", "")[:60]
                    print(f"  ✅ {title_short} → {txt_path}")
            elif status in ("failed", "cancelled"):
                failed.add(tid)
                print(f"  ❌ {t.get('title', '')[:60]} — {status}")
            elif not args.poll:
                print(f"  ⏳ {t.get('title', '')[:60]} — {status}")

        done = len(completed) + len(failed)
        print(f"\nProgress: {done}/{total} ({len(completed)} done, {len(failed)} failed)")

        if done >= total:
            break

        if not args.poll:
            break

        print(f"Waiting {args.interval}s...")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
