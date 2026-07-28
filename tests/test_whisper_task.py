"""The Whisper `task` selector.

`language` is a hint about the language *spoken in the audio*, not an output
language: Whisper always transcribes in the spoken language. `task="translate"`
is the only way to change the output — and it only ever targets English (the
model has no other translation direction). `turbo` is not trained for it at all.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from scrapower.coordinator.api.transcribe_api import VALID_TASKS, _validate_task


def test_defaults_to_transcribe():
    assert _validate_task(None, "large-v3") == "transcribe"
    assert _validate_task("", "large-v3") == "transcribe"


@pytest.mark.parametrize("task", VALID_TASKS)
def test_accepts_the_two_real_whisper_tasks(task):
    assert _validate_task(task, "large-v3") == task


@pytest.mark.parametrize("given", ["TRANSLATE", " Translate ", "Transcribe"])
def test_normalises_case_and_spacing(given):
    assert _validate_task(given, "large-v3") == given.strip().lower()


@pytest.mark.parametrize("bad", ["summarize", "french", "fr", "translate-to-french", "x"])
def test_rejects_anything_whisper_cannot_do(bad):
    """A target language is not a task: asking for French must fail loudly rather
    than silently returning the source language."""
    with pytest.raises(HTTPException) as exc:
        _validate_task(bad, "large-v3")
    assert exc.value.status_code == 400


def test_turbo_cannot_translate():
    """Documented upstream: the turbo model is not trained for translation, so
    accepting the combination would silently return untranslated text."""
    with pytest.raises(HTTPException) as exc:
        _validate_task("translate", "turbo")
    assert exc.value.status_code == 400
    assert "not trained for translation" in str(exc.value.detail)


def test_turbo_can_still_transcribe():
    assert _validate_task("transcribe", "turbo") == "transcribe"


def test_large_v3_can_translate():
    assert _validate_task("translate", "large-v3") == "translate"


async def test_task_reaches_the_worker_config(tmp_path, db):
    """The runner reads `task` out of the input blob, so it must be stored there."""
    import json

    from scrapower.coordinator.api.transcribe_api import _prepare_whisper_input
    from scrapower.coordinator.blob_store import get_blob

    blob_dir = str(tmp_path / "blobs")
    (tmp_path / "blobs").mkdir()

    h = await _prepare_whisper_input(
        "https://www.bilibili.com/video/BV1xK4y1J77Q/",
        "large-v3",
        "zh",
        "json",
        "",
        "http://localhost:8777",
        db,
        blob_dir,
        task="translate",
    )

    config = json.loads((await get_blob(db, blob_dir, h)).decode())
    assert config["task"] == "translate"
    assert config["model"] == "large-v3"
    assert config["language"] == "zh", "the spoken language stays a decoding hint"


async def test_task_defaults_in_the_worker_config(tmp_path, db):
    import json

    from scrapower.coordinator.api.transcribe_api import _prepare_whisper_input
    from scrapower.coordinator.blob_store import get_blob

    blob_dir = str(tmp_path / "blobs")
    (tmp_path / "blobs").mkdir()

    h = await _prepare_whisper_input(
        "https://example.com/v", "turbo", None, "json", "", "http://localhost:8777", db, blob_dir
    )

    assert json.loads((await get_blob(db, blob_dir, h)).decode())["task"] == "transcribe"
