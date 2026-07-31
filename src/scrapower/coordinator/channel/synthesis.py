"""Structure + synthesis — one Chinese/foreign-language transcript in, one
French synthesis document out, via the Gemini API.

Two Gemini calls, not a chain: both read the *same* original-language transcript
directly. A synthesis-of-a-translation would compound two lossy transformations
(source -> English -> French); reading the source once for each target keeps it
to a single hop.

  1. Structure: the model reads every segment and proposes section boundaries
     grounded in what the speaker actually says (topic transitions), never in
     an arbitrary clock grid. It may only reference segment *indices* -- never
     a timestamp -- so the render step, not the model, is what maps a section
     back to a real moment in the video. Structure always produces something
     usable: an invalid model response falls back to a deterministic grouping
     rather than failing the task, because there is no reason a mechanical
     grouping step should ever block delivery.
  2. Synthesis: given the transcript and the accepted structure, produces a
     summary, 2-4 key points per section, and a handful of notable quotes.
     Unlike structure, a malformed synthesis is *not* patched over -- there is
     no sensible deterministic fallback for "write a summary" -- it raises, so
     the caller (delivery.deliver_completed) leaves the video undelivered and
     retries on the next sweep.
"""

from __future__ import annotations

import datetime
import json
import logging

import httpx

log = logging.getLogger(__name__)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# flash-lite for structure: pure format-following, no judgement needed.
# flash for synthesis: benefits from the reasoning a "thinking" model does.
# Both free-tier as of testing (2026-07).
STRUCTURE_MODEL = "gemini-flash-lite-latest"
SYNTHESIS_MODEL = "gemini-flash-latest"

_TIMEOUT_SEC = 180.0
_FALLBACK_GROUP_SIZE = 20


class GeminiError(Exception):
    """The Gemini call failed, or returned something we can't use."""


# ── Tiny local duplicates of delivery.py's formatters ──────────────────────
# Not imported from there: delivery.py calls into this module, so importing
# the other way would create a cycle. Two lines each; not worth the coupling.


def _fmt_ts(sec: float) -> str:
    sec = int(sec or 0)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _fmt_duration(sec) -> str:
    if not isinstance(sec, (int, float)) or sec <= 0:
        return "?"
    sec = int(sec)
    h, m = sec // 3600, (sec % 3600) // 60
    return f"{h}h {m:02d}min" if h else f"{m}min"


# ── Gemini transport ────────────────────────────────────────────────────────


async def _generate(api_key: str, model: str, prompt: str, schema: dict) -> dict:
    """POST to generateContent with JSON-schema-constrained output. Returns the
    parsed JSON body the model produced (not Gemini's own response envelope)."""
    url = GEMINI_ENDPOINT.format(model=model)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.post(url, headers={"x-goog-api-key": api_key}, json=body)
    except httpx.HTTPError as e:
        raise GeminiError(f"request failed: {e}") from e

    if resp.status_code != 200:
        raise GeminiError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        parts = resp.json()["candidates"][0]["content"]["parts"]
        text = next(p["text"] for p in parts if "text" in p)
        return json.loads(text)
    except (KeyError, IndexError, TypeError, StopIteration, json.JSONDecodeError) as e:
        raise GeminiError(f"unparseable response: {e}") from e


# ── Segment formatting for prompts ─────────────────────────────────────────


def _segments_block(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments):
        text = str(seg.get("text", "")).strip()
        if text:
            lines.append(f"[{i}] ({_fmt_ts(seg.get('start', 0))}) {text}")
    return "\n".join(lines)


# ── Structure ────────────────────────────────────────────────────────────


_STRUCTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_index": {"type": "integer"},
                    "end_index": {"type": "integer"},
                    "title_fr": {"type": "string"},
                },
                "required": ["start_index", "end_index", "title_fr"],
            },
        }
    },
    "required": ["sections"],
}


def _structure_prompt(segments: list[dict]) -> str:
    return (
        "Tu vas lire la transcription d'un cours ou d'une conférence, segment par "
        "segment (chaque segment a un index entre crochets, [i]).\n\n"
        "Découpe l'ensemble en sections thématiques, en te basant sur ce que dit "
        "réellement l'intervenant (transitions, changements de sujet), jamais sur "
        "un découpage arbitraire par durée.\n\n"
        "Règles strictes :\n"
        "- Les sections couvrent TOUS les segments, du premier au dernier, sans "
        "trou ni chevauchement (start_index de la première section = 0 ; "
        "end_index de la dernière = le dernier index de la transcription).\n"
        "- Chaque titre est en français, court (moins de 10 mots), et décrit le "
        "sujet réellement traité dans cette section.\n"
        "- Ne réponds jamais par un horodatage : uniquement des index de segment.\n\n"
        f"Transcription :\n{_segments_block(segments)}\n"
    )


def _validate_structure(raw: dict, n_segments: int) -> list[dict] | None:
    """None on anything that isn't a clean, gap-free, in-range partition."""
    try:
        sections = raw["sections"]
        if not isinstance(sections, list) or not sections:
            return None
        out = []
        expected_start = 0
        for s in sections:
            start, end = int(s["start_index"]), int(s["end_index"])
            title = str(s["title_fr"]).strip()
            if not title or start != expected_start or end < start or end >= n_segments:
                return None
            out.append({"start_index": start, "end_index": end, "title_fr": title})
            expected_start = end + 1
        if expected_start != n_segments:
            return None  # doesn't reach the last segment
        return out
    except (KeyError, TypeError, ValueError):
        return None


def _fallback_structure(n_segments: int, group_size: int = _FALLBACK_GROUP_SIZE) -> list[dict]:
    """Mechanical grouping, used only when the model's own output is unusable.

    A grouping step has no reason to ever block delivery, unlike synthesis
    (see module docstring) -- there's always a deterministic answer here.
    """
    sections, i = [], 0
    while i < n_segments:
        end = min(i + group_size - 1, n_segments - 1)
        sections.append(
            {"start_index": i, "end_index": end, "title_fr": f"Partie {len(sections) + 1}"}
        )
        i = end + 1
    return sections


async def build_structure(api_key: str, segments: list[dict]) -> list[dict]:
    n = len(segments)
    try:
        raw = await _generate(
            api_key, STRUCTURE_MODEL, _structure_prompt(segments), _STRUCTURE_SCHEMA
        )
        validated = _validate_structure(raw, n)
        if validated is not None:
            return validated
        log.warning("gemini structure output invalid, falling back to mechanical grouping")
    except GeminiError as e:
        log.warning("gemini structure call failed, falling back to mechanical grouping: %s", e)
    return _fallback_structure(n)


# ── Synthesis ──────────────────────────────────────────────────────────────


_SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "resume": {"type": "string"},
        "section_points": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "string"}},
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text_fr": {"type": "string"},
                },
                "required": ["index", "text_fr"],
            },
        },
    },
    "required": ["resume", "section_points", "citations"],
}


def _sections_block(structure: list[dict]) -> str:
    return "\n".join(
        f"{i}. {s['title_fr']} (segments {s['start_index']}-{s['end_index']})"
        for i, s in enumerate(structure)
    )


def _synthesis_prompt(segments: list[dict], structure: list[dict]) -> str:
    return (
        "Tu vas lire la transcription complète d'un cours, déjà découpée en "
        "sections.\n\n"
        f"Sections identifiées :\n{_sections_block(structure)}\n\n"
        f"Transcription complète (par index de segment) :\n{_segments_block(segments)}\n\n"
        "Ta tâche, entièrement en français :\n"
        '1. "resume" : un résumé général du cours en un paragraphe.\n'
        '2. "section_points" : pour CHAQUE section listée ci-dessus, dans le '
        "même ordre (une entrée de la liste par section), 2 à 4 points clés -- "
        "les idées développées dans cette section, pas un résumé du résumé.\n"
        '3. "citations" : jusqu\'à 6 citations notables, traduites en français, '
        "chacune associée à l'index du segment d'origine.\n\n"
        "N'omets aucune section : chaque section doit apparaître dans "
        "section_points, même brièvement."
    )


def _validate_synthesis(raw: dict, n_sections: int, n_segments: int) -> dict | None:
    """None if the backbone (summary, one point-list per section) is broken.
    Citations are supplementary: bad entries are dropped, not fatal."""
    try:
        resume = str(raw["resume"]).strip()
        if not resume:
            return None
        section_points = raw["section_points"]
        if not isinstance(section_points, list) or len(section_points) != n_sections:
            return None
        clean_points = []
        for pts in section_points:
            if not isinstance(pts, list):
                return None
            clean = [str(p).strip() for p in pts if str(p).strip()]
            if not clean:
                return None
            clean_points.append(clean)
    except (KeyError, TypeError, ValueError):
        return None

    citations = []
    for c in raw.get("citations") or []:
        try:
            idx, text = int(c["index"]), str(c["text_fr"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= idx < n_segments and text:
            citations.append({"index": idx, "text_fr": text})

    return {"resume": resume, "section_points": clean_points, "citations": citations}


async def build_synthesis(api_key: str, segments: list[dict], structure: list[dict]) -> dict:
    raw = await _generate(
        api_key, SYNTHESIS_MODEL, _synthesis_prompt(segments, structure), _SYNTHESIS_SCHEMA
    )
    validated = _validate_synthesis(raw, len(structure), len(segments))
    if validated is None:
        raise GeminiError("synthesis output failed validation")
    return validated


# ── Rendering (pure, deterministic) ─────────────────────────────────────────


def render_synthesis_markdown(
    meta: dict,
    segments: list[dict],
    structure: list[dict],
    synthesis: dict,
    language: str = "?",
) -> str:
    """The model never supplies a timestamp; every one shown here is looked up
    from the real segment its index points at."""
    title = meta.get("title", "?")
    original = meta.get("title_original") or ""
    today = datetime.date.today().isoformat()

    out = [f"# Synthèse — {title}", ""]
    if original and original != title:
        out.append(f"- **Titre original** : {original}")
    out.append(f"- **URL** : {meta.get('url', '?')}")
    if meta.get("playlists"):
        out.append(f"- **Playlists** : {', '.join(meta['playlists'])}")
    out.append(
        f"- **Durée** : {_fmt_duration(meta.get('duration'))}"
        f" · **Langue source** : {language}"
        f" · **Généré le** : {today}"
    )
    out += ["", "---", "", "## Résumé général", "", synthesis["resume"], "", "## Plan du cours", ""]

    section_points = synthesis.get("section_points") or []
    for i, section in enumerate(structure):
        start_idx = section["start_index"]
        ts = _fmt_ts(segments[start_idx].get("start", 0)) if start_idx < len(segments) else "?"
        out.append(f"### {section['title_fr']} · {ts}")
        for point in section_points[i] if i < len(section_points) else []:
            out.append(f"- {point}")
        out.append("")

    citations = synthesis.get("citations") or []
    if citations:
        out.append("## Citations notables")
        out.append("")
        for c in citations:
            idx = c["index"]
            ts = _fmt_ts(segments[idx].get("start", 0)) if 0 <= idx < len(segments) else "?"
            out.append(f"> « {c['text_fr']} » — {ts}")
            out.append("")

    return "\n".join(out)
