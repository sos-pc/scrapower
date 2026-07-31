"""Delivery — render transcripts to Markdown and push them to Drive / local dir.

Gated: if no Drive token is configured (or the Google libs are missing),
delivery still writes to the local staging dir, so the whole pipeline works
without Drive. Google API calls are synchronous and run in a thread executor
so they never block the event loop.
"""

from __future__ import annotations

import asyncio
import datetime
import io
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
_FOLDER_MIME = "application/vnd.google-apps.folder"

# Markdown is what gets read; the raw whisper JSON doubles the file count for
# something nobody opens. Ask for it explicitly (`"formats": ["md", "json"]`)
# when the segment timings are actually needed.
DEFAULT_FORMATS = ["md"]


# ── Pure rendering helpers (unit-tested) ───────────────────────────────────


def _col(row, name: str, default=""):
    """Read a column that a migration may not have added yet (see db.py)."""
    try:
        return (row[name] if name in row.keys() else default) or default
    except (AttributeError, IndexError, KeyError):
        return default


def sanitize_name(name: str, max_len: int = 120) -> str:
    """Make a title safe as a file/folder name on disk and Drive."""
    name = name.strip().replace("/", "-").replace("\\", "-")
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:max_len].rstrip() or "sans-titre"


def _fmt_ts(sec: float) -> str:
    sec = int(sec or 0)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _fmt_duration(sec) -> str:
    if not isinstance(sec, (int, float)) or sec <= 0:
        return "?"
    sec = int(sec)
    h, m = sec // 3600, (sec % 3600) // 60
    return f"{h}h {m:02d}min" if h else f"{m}min"


def apply_glossary(text: str, glossary: dict[str, str] | None) -> str:
    """Replace known mistranslations, longest key first.

    Whisper renders some entities literally rather than as their established
    name — a Chinese lecture came back with BRICS spelled five different ways
    ("gold brick country", "Jinzhan", "Briggs"...). Those errors are systematic,
    so a substitution pass fixes them without re-transcribing anything.

    Longest-first matters: "gold brick country" must be consumed before
    "gold brick" so the shorter key can't leave "BRICS country" behind.
    Matching is case-insensitive but only on whole words, so a key can't
    corrupt the inside of a longer word.
    """
    if not glossary:
        return text
    for wrong in sorted(glossary, key=len, reverse=True):
        right = glossary[wrong]
        text = re.sub(rf"\b{re.escape(wrong)}\b", right, text, flags=re.IGNORECASE)
    return text


def render_markdown(
    meta: dict,
    transcript_json: str,
    model: str = "turbo",
    glossary: dict[str, str] | None = None,
) -> str:
    """Render one transcript (whisper JSON output) to Markdown with timestamps.

    ``meta["title"]`` may be a translated title; ``meta["title_original"]`` (when
    different) is kept in the header alongside the URL so the source is always
    identifiable.
    """
    try:
        data = json.loads(transcript_json)
    except (json.JSONDecodeError, TypeError):
        data = {}
    segments = data.get("segments", []) if isinstance(data, dict) else []
    duration = data.get("duration") if isinstance(data, dict) else None
    duration = duration or meta.get("duration")
    today = datetime.date.today().isoformat()

    title = meta.get("title", "?")
    original = meta.get("title_original") or ""

    out = [f"# {title}", ""]
    if original and original != title:
        out.append(f"- **Titre original** : {original}")
    out.append(f"- **URL** : {meta.get('url', '?')}")
    if meta.get("playlists"):
        out.append(f"- **Playlists** : {', '.join(meta['playlists'])}")
    line = (
        f"- **Durée** : {_fmt_duration(duration)}"
        f" · **Langue source** : {data.get('language', '?') if isinstance(data, dict) else '?'}"
        f" · **Modèle** : {model}"
    )
    if meta.get("task") and meta["task"] != "transcribe":
        line += f" · **Tâche** : {meta['task']}"
    out.append(line)
    out.append(f"- **Transcrit le** : {today}")
    if glossary:
        out.append(f"- **Glossaire appliqué** : {len(glossary)} terme(s) corrigé(s)")
    out += ["", "---", ""]

    if segments:
        for seg in segments:
            text = apply_glossary(str(seg.get("text", "")).strip(), glossary)
            if text:
                out.append(f"**[{_fmt_ts(seg.get('start', 0))}]** {text}")
                out.append("")
    else:
        # Fallback: not the segmented JSON we expected — embed the raw payload.
        out.append(transcript_json.strip())
    return "\n".join(out)


# ── Drive client (lazy import, gated) ──────────────────────────────────────


class DriveClient:
    """Thin idempotent Drive wrapper using OAuth user credentials (token.json)."""

    def __init__(self, token_path: str, root_folder_id: str):
        from google.oauth2.credentials import Credentials  # lazy
        from googleapiclient.discovery import build

        self._creds = Credentials.from_authorized_user_file(token_path, DRIVE_SCOPES)
        self._svc = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        self._root = root_folder_id
        self._folder_cache: dict[tuple[str, str], str] = {}

    def _folder(self, name: str, parent_id: str) -> str:
        key = (parent_id, name)
        if key in self._folder_cache:
            return self._folder_cache[key]
        safe = name.replace("'", "\\'")
        q = (
            f"name = '{safe}' and mimeType = '{_FOLDER_MIME}' "
            f"and '{parent_id}' in parents and trashed = false"
        )
        found = (
            self._svc.files().list(q=q, fields="files(id)", pageSize=1).execute().get("files", [])
        )
        if found:
            fid = found[0]["id"]
        else:
            fid = (
                self._svc.files()
                .create(
                    body={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]},
                    fields="id",
                )
                .execute()["id"]
            )
        self._folder_cache[key] = fid
        return fid

    def upload_text(self, folder_id: str, filename: str, content: bytes, mimetype: str) -> None:
        """Create or overwrite ``filename`` in ``folder_id`` (idempotent by name)."""
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype, resumable=False)
        safe = filename.replace("'", "\\'")
        q = f"name = '{safe}' and '{folder_id}' in parents and trashed = false"
        found = (
            self._svc.files().list(q=q, fields="files(id)", pageSize=1).execute().get("files", [])
        )
        if found:
            self._svc.files().update(fileId=found[0]["id"], media_body=media).execute()
        else:
            self._svc.files().create(
                body={"name": filename, "parents": [folder_id]}, media_body=media, fields="id"
            ).execute()

    def deliver(
        self,
        playlist: str,
        base: str,
        md: str,
        raw_json: str,
        formats: list[str],
        synthesis_md: str | None = None,
    ) -> None:
        folder = self._folder(sanitize_name(playlist), self._root)
        if "md" in formats:
            self.upload_text(folder, base + ".md", md.encode("utf-8"), "text/markdown")
        if "json" in formats:
            self.upload_text(folder, base + ".json", raw_json.encode("utf-8"), "application/json")
        if synthesis_md is not None:
            self.upload_text(
                folder, base + " - Synthese.md", synthesis_md.encode("utf-8"), "text/markdown"
            )


def _try_drive(config) -> DriveClient | None:
    token = getattr(config, "drive_token_path", "")
    root = getattr(config, "drive_root_folder_id", "")
    if not token or not root or not Path(token).exists():
        return None
    try:
        return DriveClient(token, root)
    except Exception as e:
        log.warning("drive delivery disabled: %s", str(e)[:200])
        return None


# ── Sweep (async) ──────────────────────────────────────────────────────────


def _write_targets(
    config,
    drive,
    meta,
    md: str,
    raw_json: str,
    formats: list[str],
    synthesis_md: str | None = None,
) -> None:
    """Blocking: write local staging copies + optional Drive copies (per playlist)."""
    base = f"{sanitize_name(meta['title'])} [{meta['video_id']}]"
    staging = Path(getattr(config, "transcripts_dir", "data/transcripts"))
    for playlist in meta.get("playlists") or ["_sans-playlist"]:
        pdir = staging / sanitize_name(playlist)
        pdir.mkdir(parents=True, exist_ok=True)
        if "md" in formats:
            (pdir / (base + ".md")).write_text(md, encoding="utf-8")
        if "json" in formats:
            (pdir / (base + ".json")).write_text(raw_json, encoding="utf-8")
        if synthesis_md is not None:
            (pdir / (base + " - Synthese.md")).write_text(synthesis_md, encoding="utf-8")
        if drive is not None:
            drive.deliver(playlist, base, md, raw_json, formats, synthesis_md=synthesis_md)


async def _maybe_synthesize(config, meta: dict, raw_json: str, cfg: dict) -> str | None:
    """Build the French synthesis document, or None if not requested.

    Raises on failure (missing key, bad Gemini response) rather than
    swallowing it: the caller's per-row try/except already leaves the video
    undelivered on any exception, which is exactly "retry next sweep" --
    no separate retry bookkeeping needed for this stage.
    """
    if not cfg.get("synthesize"):
        return None
    from . import synthesis as synth_mod

    key = getattr(config, "gemini_api_key", "")
    if not key:
        raise RuntimeError("synthesize requested but GEMINI_API_KEY is not configured")

    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    segments = parsed.get("segments") or [] if isinstance(parsed, dict) else []
    if not segments:
        raise RuntimeError("no segments to synthesize")

    structure = await synth_mod.build_structure(key, segments)
    synth = await synth_mod.build_synthesis(key, segments, structure)
    language = parsed.get("language", "?") if isinstance(parsed, dict) else "?"
    return synth_mod.render_synthesis_markdown(meta, segments, structure, synth, language=language)


async def deliver_completed(db, blob_dir: str, config) -> int:
    """Render + deliver every completed-but-undelivered channel video. Returns count."""
    from ..blob_store import get_blob

    cur = await db.execute(
        """SELECT cv.job_id, cv.video_id, cv.url, cv.title, cv.title_original,
                  cv.duration, cv.playlists_json, t.output_hash, j.model, j.config_json
           FROM channel_videos cv
           JOIN tasks t ON cv.task_id = t.id
           JOIN channel_jobs j ON cv.job_id = j.id
           WHERE cv.delivered = 0 AND t.state = 'completed' AND t.output_hash != ''"""
    )
    rows = await cur.fetchall()
    if not rows:
        return 0

    drive = _try_drive(config)
    loop = asyncio.get_event_loop()
    delivered = 0
    for row in rows:
        try:
            data = await get_blob(None, blob_dir, row["output_hash"])  # type: ignore[arg-type]
            if data is None:
                continue
            raw_json = data.decode("utf-8", errors="replace")
            try:
                cfg = json.loads(row["config_json"])
            except (json.JSONDecodeError, TypeError):
                cfg = {}
            formats = cfg.get("formats") or DEFAULT_FORMATS
            glossary = cfg.get("glossary") or None
            meta = {
                "video_id": row["video_id"],
                "url": row["url"],
                "title": row["title"] or row["video_id"],
                "title_original": _col(row, "title_original"),
                "duration": row["duration"],
                "task": cfg.get("task"),
                "playlists": json.loads(row["playlists_json"] or "[]"),
            }
            md = render_markdown(meta, raw_json, model=row["model"], glossary=glossary)
            synthesis_md = await _maybe_synthesize(config, meta, raw_json, cfg)
            await loop.run_in_executor(
                None, _write_targets, config, drive, meta, md, raw_json, formats, synthesis_md
            )
            await db.execute(
                "UPDATE channel_videos SET delivered = 1, delivered_at = ? "
                "WHERE job_id = ? AND video_id = ?",
                (datetime.datetime.now(datetime.UTC).isoformat(), row["job_id"], row["video_id"]),
            )
            delivered += 1
        except Exception as e:
            log.warning("delivery failed for %s: %s", row["video_id"], str(e)[:200])
    if delivered:
        await db.commit()
        log.info("delivered %d transcript(s)", delivered)
    return delivered


async def delivery_loop(db, blob_dir: str, config) -> None:
    """Background sweep: deliver newly-completed transcripts, then finalize jobs."""
    from .job import finalize_jobs  # local import: avoids an import cycle

    interval = getattr(config, "delivery_interval_sec", 30)
    while True:
        await asyncio.sleep(interval)
        try:
            await deliver_completed(db, blob_dir, config)
        except Exception:
            log.exception("delivery sweep failed")
        try:
            await finalize_jobs(db)
        except Exception:
            log.exception("job finalize failed")
