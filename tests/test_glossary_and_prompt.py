"""Three ways to fix proper nouns that Whisper gets wrong, and a readable title.

The first Bilibili translation came back with BRICS spelled five different ways
("gold brick country", "Jinzhan", "Briggs"), SCO as "Shanghe" and Abenomics as
"Anbei economy". Two complementary fixes:

  - post-hoc: a glossary applied at render time, so an already-paid transcript
    can be corrected without re-running the GPU;
  - up-front: `hotwords` / `initial_prompt` passed to Whisper, which only helps
    the *next* transcription.

And because the source titles are Chinese, a caller-supplied translated title
names the file while the provider's own title stays in the header next to the
URL, so the source is never lost.
"""

from __future__ import annotations

import json

import pytest

from scrapower.coordinator import blob_store as bs
from scrapower.coordinator.api import transcribe_api
from scrapower.coordinator.channel import delivery

BRICS = {
    "gold brick country": "BRICS",
    "gold brick": "BRICS",
    "Jinzhan": "BRICS",
    "Shanghe": "SCO",
    "Anbei economy": "Abenomics",
}


# ── apply_glossary ─────────────────────────────────────────────────────────


def test_glossary_replaces_a_known_mistranslation():
    assert delivery.apply_glossary("the Shanghe summit", BRICS) == "the SCO summit"


def test_longest_key_wins_so_no_leftover_fragment():
    """If "gold brick" ran first it would leave "BRICS country" behind."""
    out = delivery.apply_glossary("the gold brick country met", BRICS)
    assert out == "the BRICS met"
    assert "country" not in out


def test_matching_is_case_insensitive():
    assert delivery.apply_glossary("Gold Brick Country", BRICS) == "BRICS"


def test_word_boundaries_protect_longer_words():
    """A key must not corrupt the inside of an unrelated word."""
    assert delivery.apply_glossary("Jinzhanist", BRICS) == "Jinzhanist"


def test_no_glossary_is_a_no_op():
    assert delivery.apply_glossary("untouched", None) == "untouched"
    assert delivery.apply_glossary("untouched", {}) == "untouched"


def test_replacement_is_not_reapplied_to_its_own_output():
    """A cycle (right side matching another key) must not loop or double-apply."""
    assert delivery.apply_glossary("BRICS", BRICS) == "BRICS"


# ── render_markdown ────────────────────────────────────────────────────────


def _transcript(*texts: str) -> str:
    return json.dumps(
        {
            "language": "zh",
            "duration": 7965.0,
            "segments": [
                {"start": i * 4, "end": i * 4 + 4, "text": t} for i, t in enumerate(texts)
            ],
        }
    )


def test_segments_are_corrected_by_the_glossary():
    md = delivery.render_markdown(
        {"title": "T", "url": "u"}, _transcript(" the gold brick country"), glossary=BRICS
    )
    assert "**[00:00:00]** the BRICS" in md
    assert "gold brick" not in md


def test_glossary_use_is_disclosed_in_the_header():
    md = delivery.render_markdown({"title": "T", "url": "u"}, _transcript(" x"), glossary=BRICS)
    assert f"**Glossaire appliqué** : {len(BRICS)} terme(s)" in md


def test_no_glossary_line_when_none_applied():
    md = delivery.render_markdown({"title": "T", "url": "u"}, _transcript(" x"))
    assert "Glossaire" not in md


def test_translated_title_is_the_heading_and_the_original_is_kept():
    md = delivery.render_markdown(
        {
            "title": "International Relations Analysis - 01",
            "title_original": "【清华大学】国际关系分析 p01",
            "url": "https://www.bilibili.com/video/BV1xK4y1J77Q/?p=1",
        },
        _transcript(" x"),
    )
    assert md.startswith("# International Relations Analysis - 01")
    assert "- **Titre original** : 【清华大学】国际关系分析 p01" in md
    assert "- **URL** : https://www.bilibili.com/video/BV1xK4y1J77Q/?p=1" in md


def test_no_original_line_when_the_title_was_not_overridden():
    md = delivery.render_markdown(
        {"title": "same", "title_original": "same", "url": "u"}, _transcript(" x")
    )
    assert "Titre original" not in md


def test_translate_task_is_disclosed():
    md = delivery.render_markdown(
        {"title": "T", "url": "u", "task": "translate"}, _transcript(" x")
    )
    assert "**Tâche** : translate" in md
    plain = delivery.render_markdown(
        {"title": "T", "url": "u", "task": "transcribe"}, _transcript(" x")
    )
    assert "Tâche" not in plain, "the default task is noise in the header"


# ── Persistence: what the sweep reads back ─────────────────────────────────


@pytest.fixture
def no_metadata_lookup(monkeypatch):
    async def fake_meta(url, proxy=None):
        return {"id": "BV1xK4y1J77Q_p1", "title": "【清华大学】国际关系分析 p01", "duration": 7965}

    monkeypatch.setattr("scrapower.coordinator.channel.discovery.fetch_video_meta", fake_meta)
    return fake_meta


async def test_registration_stores_both_titles(db, no_metadata_lookup):
    task_id = "a" * 32
    await transcribe_api._register_delivery(
        db,
        task_id,
        "https://www.bilibili.com/video/BV1xK4y1J77Q/?p=1",
        "Cours",
        "large-v3",
        ["md"],
        title_override="International Relations Analysis - 01",
    )
    cur = await db.execute(
        "SELECT title, title_original FROM channel_videos WHERE task_id = ?", (task_id,)
    )
    row = await cur.fetchone()
    assert row["title"] == "International Relations Analysis - 01"
    assert row["title_original"] == "【清华大学】国际关系分析 p01"


async def test_registration_stores_the_glossary_and_task(db, no_metadata_lookup):
    task_id = "b" * 32
    await transcribe_api._register_delivery(
        db,
        task_id,
        "https://example.com/v",
        "Cours",
        "large-v3",
        ["md"],
        glossary=BRICS,
        whisper_task="translate",
    )
    cur = await db.execute(
        "SELECT config_json FROM channel_jobs WHERE id = ?", (f"single-{task_id[:16]}",)
    )
    cfg = json.loads((await cur.fetchone())["config_json"])
    assert cfg["glossary"] == BRICS
    assert cfg["task"] == "translate"


async def test_sweep_applies_the_stored_glossary_and_titles(
    db, blob_dir, config, no_metadata_lookup
):
    """End to end: nothing is corrected at transcription time, only at render."""
    h = await bs.store_blob(db, blob_dir, _transcript(" the gold brick country met").encode())
    task_id = "c" * 32
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db,
        task_id,
        "https://www.bilibili.com/video/BV1xK4y1J77Q/?p=1",
        "Cours",
        "large-v3",
        ["md"],
        title_override="International Relations Analysis - 01",
        glossary=BRICS,
        whisper_task="translate",
    )

    assert await delivery.deliver_completed(db, blob_dir, config) == 1

    from pathlib import Path

    base = f"{delivery.sanitize_name('International Relations Analysis - 01')} [BV1xK4y1J77Q_p1]"
    md = (Path(config.transcripts_dir) / "Cours" / f"{base}.md").read_text(encoding="utf-8")
    assert md.startswith("# International Relations Analysis - 01")
    assert "- **Titre original** : 【清华大学】国际关系分析 p01" in md
    assert "the BRICS met" in md
    assert "gold brick" not in md
    assert "**Tâche** : translate" in md


async def test_raw_json_is_delivered_uncorrected(db, blob_dir, config, no_metadata_lookup):
    """The glossary is a presentation fix; the JSON stays what the model produced."""
    h = await bs.store_blob(db, blob_dir, _transcript(" the gold brick country met").encode())
    task_id = "d" * 32
    await db.execute(
        "INSERT INTO tasks (id, client_id, state, output_hash, created_at, updated_at)"
        " VALUES (?, 'anonymous', 'completed', ?, '1', '1')",
        (task_id, h),
    )
    await db.commit()
    await transcribe_api._register_delivery(
        db, task_id, "https://example.com/v", "Cours", "large-v3", ["json"], glossary=BRICS
    )
    await delivery.deliver_completed(db, blob_dir, config)

    from pathlib import Path

    raw = next((Path(config.transcripts_dir) / "Cours").glob("*.json")).read_text(encoding="utf-8")
    assert "gold brick country" in raw


# ── Up-front hinting: what reaches the worker ──────────────────────────────


async def test_hints_reach_the_worker_config(db, blob_dir):
    h = await transcribe_api._prepare_whisper_input(
        "https://example.com/v",
        "large-v3",
        "zh",
        "json",
        "",
        "http://c:8777",
        db,
        blob_dir,
        task="translate",
        initial_prompt="A lecture on international relations theory.",
        hotwords="BRICS SCO Abenomics",
    )
    cfg = json.loads((await bs.get_blob(db, blob_dir, h)).decode())
    assert cfg["task"] == "translate"
    assert cfg["initial_prompt"] == "A lecture on international relations theory."
    assert cfg["hotwords"] == "BRICS SCO Abenomics"


async def test_hints_default_to_empty(db, blob_dir):
    """Absent hints must be empty strings, not None: the runner treats them as
    falsy and omits the kwargs entirely."""
    h = await transcribe_api._prepare_whisper_input(
        "https://example.com/v", "large-v3", None, "json", "", "http://c:8777", db, blob_dir
    )
    cfg = json.loads((await bs.get_blob(db, blob_dir, h)).decode())
    assert cfg["initial_prompt"] == ""
    assert cfg["hotwords"] == ""


def test_runner_omits_empty_hints():
    """faster-whisper's own defaults must win when the caller sent nothing —
    passing hotwords="" is not the same as not passing it."""
    import inspect

    from scrapower.worker.runtimes import whisper_runner

    src = inspect.getsource(whisper_runner._transcribe_faster_whisper)
    assert "if initial_prompt:" in src
    assert "if hotwords:" in src
    assert "**kwargs" in src
