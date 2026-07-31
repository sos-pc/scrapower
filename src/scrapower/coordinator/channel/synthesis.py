"""Structure + written rendering -- one source-language transcript in, one
full written-French version out, via the Gemini API.

Two Gemini calls, not a chain: both read the *same* original-language transcript
directly. A rewrite-of-a-translation would compound two lossy transformations
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
  2. Rewrite, once per section: given that section's segments, produces a full
     written-French rendering of its content -- every argument, every example,
     every step of the speaker's reasoning -- with the oral disfluencies
     (repetitions, false starts, filler) removed and the register converted
     from spoken to written. This is deliberately NOT a summary: the output is
     expected to run close to the length of the source, not a fraction of it.
     There is no way to mechanically prove no content was dropped -- unlike a
     timestamp or a section boundary, "did this preserve every argument" isn't
     checkable by code. build_rewrite logs a warning when a section's output
     looks suspiciously short relative to its source (see _warn_if_short), but
     that is a heuristic signal, not a guarantee. A malformed response is not
     patched over: it raises, so the caller (delivery.deliver_completed) leaves
     the video undelivered and retries the whole thing on the next sweep.
"""

from __future__ import annotations

import datetime
import json
import logging

import httpx

log = logging.getLogger(__name__)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# flash-lite for structure: pure format-following, no judgement needed.
# flash for the rewrite: benefits from the reasoning a "thinking" model does --
# telling substance from oral filler is a harder call than assigning a title.
# Both free-tier as of testing (2026-07).
STRUCTURE_MODEL = "gemini-flash-lite-latest"
REWRITE_MODEL = "gemini-flash-latest"

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


def _segments_block(segments: list[dict], indices: range | list[int] | None = None) -> str:
    """``indices`` lets a section slice keep its *absolute* index (into the
    full transcript), since that's what start_index in the model's response
    must be validated against."""
    idx_iter = indices if indices is not None else range(len(segments))
    lines = []
    for i, seg in zip(idx_iter, segments):
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

    A grouping step has no reason to ever block delivery, unlike the rewrite
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


# ── Rewrite (one call per section) ──────────────────────────────────────────


_REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "subsections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_index": {"type": "integer"},
                    "title_fr": {"type": "string"},
                    "text_fr": {"type": "string"},
                },
                "required": ["start_index", "title_fr", "text_fr"],
            },
        }
    },
    "required": ["subsections"],
}


def _rewrite_prompt(segments: list[dict], indices: range, section_title: str, language: str) -> str:
    return (
        "Tu vas lire un extrait d'un cours magistral, à l'oral, segment par "
        "segment (chaque segment a un index entre crochets, [i]).\n\n"
        f"Cet extrait correspond à la section : {section_title}\n\n"
        "Ta tâche : réécrire INTÉGRALEMENT ce contenu en français écrit, comme "
        "un chapitre de manuel universitaire -- surtout PAS un résumé, PAS un "
        "abstract.\n\n"
        "Règles strictes :\n"
        "- Conserve absolument tous les arguments, tous les exemples, tous les "
        "raisonnements développés par l'intervenant. Si un point est développé "
        "en plusieurs étapes à l'oral, développe-le en plusieurs étapes en "
        "français aussi -- ne condense rien.\n"
        "- Supprime uniquement les tics propres à l'oral : répétitions, "
        "hésitations, reformulations, digressions sans contenu. Le résultat "
        "doit se lire comme un texte écrit, jamais comme une transcription.\n"
        "- Si cet extrait couvre plusieurs sous-sujets distincts, découpe-le en "
        "plusieurs sous-parties, chacune avec un titre court en français "
        "(\"title_fr\"). S'il ne traite que d'un seul sujet, une seule "
        'sous-partie suffit, avec title_fr vide ("").\n'
        "- Chaque sous-partie commence à l'index de segment où elle démarre "
        "réellement (start_index) -- jamais un horodatage inventé.\n\n"
        f"Transcription (langue source : {language}) :\n"
        f"{_segments_block(segments, indices)}\n"
    )


def _validate_rewrite(raw: dict, section: dict, n_segments: int) -> list[dict] | None:
    """None unless every subsection stays within the section's own segment
    range, in reading order, with real content -- and, once concatenated,
    unambiguous (an untitled block only makes sense when it's the only one)."""
    try:
        subs = raw["subsections"]
        if not isinstance(subs, list) or not subs:
            return None
        out = []
        prev_idx = section["start_index"]
        for i, s in enumerate(subs):
            idx = int(s["start_index"])
            title = str(s.get("title_fr", "")).strip()
            text = str(s["text_fr"]).strip()
            if not text or idx < section["start_index"] or idx > section["end_index"]:
                return None
            if i > 0 and idx < prev_idx:
                return None
            out.append({"start_index": idx, "title_fr": title, "text_fr": text})
            prev_idx = idx
        if len(out) > 1 and any(not s["title_fr"] for s in out):
            return None
        return out
    except (KeyError, TypeError, ValueError, IndexError):
        return None


# French renders longer than Chinese hanzi for equivalent content, so a much
# shorter output is a signal -- not proof -- that the model compressed instead
# of rewriting despite the instruction. Uncalibrated: log-only, never blocks.
_MIN_CHAR_RATIO = 0.6


def _warn_if_short(
    section_title: str, source_segments: list[dict], subsections: list[dict]
) -> None:
    source_len = sum(len(str(s.get("text", ""))) for s in source_segments)
    out_len = sum(len(s["text_fr"]) for s in subsections)
    if source_len and out_len < source_len * _MIN_CHAR_RATIO:
        log.warning(
            "rewrite for section %r looks short (%d chars out vs %d in, ratio %.2f)"
            " -- may have been compressed instead of fully rewritten",
            section_title,
            out_len,
            source_len,
            out_len / source_len,
        )


async def build_rewrite(
    api_key: str, segments: list[dict], section: dict, language: str = "?"
) -> list[dict]:
    start, end = section["start_index"], section["end_index"]
    slice_segments = segments[start : end + 1]
    prompt = _rewrite_prompt(slice_segments, range(start, end + 1), section["title_fr"], language)
    raw = await _generate(api_key, REWRITE_MODEL, prompt, _REWRITE_SCHEMA)
    validated = _validate_rewrite(raw, section, len(segments))
    if validated is None:
        raise GeminiError(f"rewrite output failed validation for section {section['title_fr']!r}")
    _warn_if_short(section["title_fr"], slice_segments, validated)
    return validated


async def build_full_rewrite(
    api_key: str, segments: list[dict], structure: list[dict], language: str = "?"
) -> list[list[dict]]:
    """One written rendering per section, in order. Raises on the first section
    that fails -- there's no sensible deterministic fallback for "write the
    content" (see module docstring) -- so this bubbles up to the caller's
    retry-next-sweep behaviour rather than delivering a partial course."""
    return [await build_rewrite(api_key, segments, section, language) for section in structure]


# ── Rendering (pure, deterministic) ─────────────────────────────────────────


def render_course_markdown(
    meta: dict,
    segments: list[dict],
    structure: list[dict],
    rewrites: list[list[dict]],
    language: str = "?",
) -> str:
    """The model never supplies a timestamp; every one shown here is looked up
    from the real segment its index points at."""
    title = meta.get("title", "?")
    original = meta.get("title_original") or ""
    today = datetime.date.today().isoformat()

    out = [f"# {title}", ""]
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
    out += ["", "---", ""]

    for section, subs in zip(structure, rewrites):
        start_idx = section["start_index"]
        ts = _fmt_ts(segments[start_idx].get("start", 0)) if start_idx < len(segments) else "?"
        out.append(f"## {section['title_fr']} · {ts}")
        out.append("")
        for sub in subs:
            if sub["title_fr"]:
                sub_idx = sub["start_index"]
                sub_ts = (
                    _fmt_ts(segments[sub_idx].get("start", 0)) if sub_idx < len(segments) else "?"
                )
                out.append(f"### {sub['title_fr']} · {sub_ts}")
                out.append("")
            out.append(sub["text_fr"])
            out.append("")

    return "\n".join(out)
