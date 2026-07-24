"""Channel discovery — enumerate a channel's playlists and videos via yt-dlp.

The subprocess call needs yt-dlp + the WireGuard proxy (same path the workers
use). The pure parsing/dedup logic is separated out so it can be unit-tested
without any network access.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

log = logging.getLogger(__name__)

# A video at or under this duration (seconds) is treated as a Short.
SHORTS_MAX_SEC = 60


def parse_flat_entries(stdout_text: str) -> list[dict]:
    """Parse yt-dlp ``--flat-playlist -j`` output (one JSON object per line)."""
    entries: list[dict] = []
    for line in stdout_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _is_short(video: dict) -> bool:
    url = (video.get("url") or "") + (video.get("webpage_url") or "")
    if "/shorts/" in url:
        return True
    dur = video.get("duration")
    return isinstance(dur, (int, float)) and 0 < dur <= SHORTS_MAX_SEC


def build_video_manifest(
    playlists: list[tuple[str, list[dict]]], include_shorts: bool = False
) -> list[dict]:
    """Deduplicate videos across playlists into a single manifest.

    ``playlists`` is ``[(playlist_title, [flat_video_entry, ...]), ...]``.
    A video appearing in several playlists is kept once, with every playlist
    it belongs to recorded in ``playlists`` (delivery copies it into each).
    Returns ``[{video_id, url, title, duration, playlists:[...]}, ...]``.
    """
    videos: dict[str, dict] = {}
    for playlist_title, entries in playlists:
        for v in entries:
            vid = v.get("id")
            if not vid:
                continue
            if not include_shorts and _is_short(v):
                continue
            if vid not in videos:
                url = (
                    v.get("url")
                    or v.get("webpage_url")
                    or f"https://www.youtube.com/watch?v={vid}"
                )
                videos[vid] = {
                    "video_id": vid,
                    "url": url,
                    "title": v.get("title", "") or vid,
                    "duration": v.get("duration"),
                    "playlists": [],
                }
            if playlist_title and playlist_title not in videos[vid]["playlists"]:
                videos[vid]["playlists"].append(playlist_title)
    return list(videos.values())


async def _yt_dlp_flat(url: str, proxy: str, timeout: float = 90.0) -> list[dict]:
    args = ["yt-dlp", "--flat-playlist", "-j", "--no-warnings"]
    if proxy:
        args += ["--proxy", proxy]
    args.append(url)
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        log.warning("yt-dlp timed out for %s", url)
        return []
    if proc.returncode != 0:
        log.warning("yt-dlp failed for %s: %s", url, stderr.decode()[:200])
        return []
    return parse_flat_entries(stdout.decode())


async def discover_channel(
    channel_url: str, include_shorts: bool = False, proxy: str | None = None
) -> list[dict]:
    """Enumerate the channel's playlists -> videos, deduplicated.

    Reads the WireGuard proxy from ``SCRAPOWER_WG_PROXY`` when not supplied.
    """
    if proxy is None:
        proxy = os.environ.get("SCRAPOWER_WG_PROXY", "") or os.environ.get(
            "SCRAPOWER_VPN_PROXY", ""
        )

    playlist_entries = await _yt_dlp_flat(channel_url, proxy)
    playlists = [
        (e.get("title", "") or "", e["id"])
        for e in playlist_entries
        if str(e.get("id", "")).startswith("PL")
    ]
    log.info("channel discovery: %d playlists on %s", len(playlists), channel_url)

    collected: list[tuple[str, list[dict]]] = []
    for title, pid in playlists:
        if not include_shorts and title.strip().lower() == "shorts":
            continue
        videos = await _yt_dlp_flat(f"https://www.youtube.com/playlist?list={pid}", proxy)
        collected.append((title, videos))
        await asyncio.sleep(2)  # be polite to YouTube

    manifest = build_video_manifest(collected, include_shorts=include_shorts)
    log.info("channel discovery: %d unique videos", len(manifest))
    return manifest
