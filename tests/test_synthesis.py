"""Structure + synthesis: content-driven sectioning, single-hop translation.

The whole point of this pipeline is a discipline already used for the whisper
task and the delivery glossary: the model never gets to invent a timestamp.
It references a segment *index*; the render layer looks up the real time. So
the tests that matter most are the validators (index gaps/overlaps/out-of-range
must be rejected) and the fallback (a grouping step must never be able to fail
delivery outright, unlike synthesis, which is allowed to and should retry).

No network: every test either drives the pure validate/fallback/render
functions directly, or patches synthesis._generate to avoid a real HTTP call.
"""

from __future__ import annotations

import pytest

from scrapower.coordinator.channel import synthesis as syn


def _segments(*texts: str, start=0.0, step=30.0) -> list[dict]:
    return [
        {"start": start + i * step, "end": start + (i + 1) * step, "text": t}
        for i, t in enumerate(texts)
    ]


# ── _validate_structure ─────────────────────────────────────────────────────


def test_a_clean_partition_is_accepted():
    raw = {
        "sections": [
            {"start_index": 0, "end_index": 2, "title_fr": "Introduction"},
            {"start_index": 3, "end_index": 5, "title_fr": "Développement"},
        ]
    }
    out = syn._validate_structure(raw, n_segments=6)
    assert out == raw["sections"]


def test_a_gap_between_sections_is_rejected():
    raw = {
        "sections": [
            {"start_index": 0, "end_index": 2, "title_fr": "A"},
            {"start_index": 4, "end_index": 5, "title_fr": "B"},  # skips index 3
        ]
    }
    assert syn._validate_structure(raw, n_segments=6) is None


def test_overlapping_sections_are_rejected():
    raw = {
        "sections": [
            {"start_index": 0, "end_index": 3, "title_fr": "A"},
            {"start_index": 2, "end_index": 5, "title_fr": "B"},  # overlaps A
        ]
    }
    assert syn._validate_structure(raw, n_segments=6) is None


def test_not_starting_at_zero_is_rejected():
    raw = {"sections": [{"start_index": 1, "end_index": 5, "title_fr": "A"}]}
    assert syn._validate_structure(raw, n_segments=6) is None


def test_not_reaching_the_last_segment_is_rejected():
    raw = {"sections": [{"start_index": 0, "end_index": 3, "title_fr": "A"}]}
    assert syn._validate_structure(raw, n_segments=6) is None


def test_an_out_of_range_index_is_rejected():
    raw = {"sections": [{"start_index": 0, "end_index": 99, "title_fr": "A"}]}
    assert syn._validate_structure(raw, n_segments=6) is None


def test_an_empty_title_is_rejected():
    raw = {"sections": [{"start_index": 0, "end_index": 5, "title_fr": "   "}]}
    assert syn._validate_structure(raw, n_segments=6) is None


def test_no_sections_at_all_is_rejected():
    assert syn._validate_structure({"sections": []}, n_segments=6) is None


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"sections": "not a list"},
        {"sections": [{"start_index": "x", "end_index": 1, "title_fr": "A"}]},
        {"sections": [{"end_index": 1, "title_fr": "A"}]},  # missing start_index
    ],
)
def test_malformed_shapes_are_rejected_not_raised(raw):
    """A schema violation must degrade to None, never propagate as an exception
    (that would sink the whole delivery sweep row instead of falling back)."""
    assert syn._validate_structure(raw, n_segments=6) is None


# ── _fallback_structure ─────────────────────────────────────────────────────


def test_fallback_covers_every_segment_with_no_gap_or_overlap():
    out = syn._fallback_structure(n_segments=47, group_size=20)
    assert out[0]["start_index"] == 0
    assert out[-1]["end_index"] == 46
    for a, b in zip(out, out[1:]):
        assert b["start_index"] == a["end_index"] + 1


def test_fallback_output_passes_its_own_validator():
    """The fallback must itself satisfy _validate_structure -- it's the last
    line of defense, so it cannot also be malformed."""
    out = syn._fallback_structure(n_segments=53, group_size=20)
    revalidated = syn._validate_structure({"sections": out}, n_segments=53)
    assert revalidated == out


def test_fallback_handles_a_count_not_divisible_by_group_size():
    out = syn._fallback_structure(n_segments=41, group_size=20)
    assert len(out) == 3
    assert out[-1]["end_index"] == 40


# ── _validate_synthesis ─────────────────────────────────────────────────────


def test_a_clean_synthesis_is_accepted():
    raw = {
        "resume": "Un cours sur X.",
        "section_points": [["point A1", "point A2"], ["point B1"]],
        "citations": [{"index": 0, "text_fr": "citation"}],
    }
    out = syn._validate_synthesis(raw, n_sections=2, n_segments=10)
    assert out["resume"] == "Un cours sur X."
    assert out["section_points"] == [["point A1", "point A2"], ["point B1"]]
    assert out["citations"] == [{"index": 0, "text_fr": "citation"}]


def test_wrong_number_of_section_point_lists_is_rejected():
    raw = {"resume": "R", "section_points": [["a"]], "citations": []}
    assert syn._validate_synthesis(raw, n_sections=2, n_segments=10) is None


def test_an_empty_resume_is_rejected():
    raw = {"resume": "  ", "section_points": [["a"]], "citations": []}
    assert syn._validate_synthesis(raw, n_sections=1, n_segments=10) is None


def test_a_section_with_no_usable_points_is_rejected():
    raw = {"resume": "R", "section_points": [["  ", ""]], "citations": []}
    assert syn._validate_synthesis(raw, n_sections=1, n_segments=10) is None


def test_bad_citations_are_dropped_not_fatal():
    """Citations are supplementary -- unlike the backbone, a bad one just
    disappears rather than failing the whole synthesis."""
    raw = {
        "resume": "R",
        "section_points": [["a"]],
        "citations": [
            {"index": 0, "text_fr": "good"},
            {"index": 999, "text_fr": "out of range"},
            {"index": 1, "text_fr": ""},
            {"index": "not a number", "text_fr": "bad type"},
        ],
    }
    out = syn._validate_synthesis(raw, n_sections=1, n_segments=10)
    assert out["citations"] == [{"index": 0, "text_fr": "good"}]


def test_missing_resume_key_is_rejected_not_raised():
    assert syn._validate_synthesis({"section_points": [], "citations": []}, 0, 10) is None


# ── render_synthesis_markdown ────────────────────────────────────────────────


def _structure_and_synthesis():
    structure = [
        {"start_index": 0, "end_index": 1, "title_fr": "Introduction"},
        {"start_index": 2, "end_index": 2, "title_fr": "Conclusion"},
    ]
    synthesis = {
        "resume": "Résumé général du cours.",
        "section_points": [["point un", "point deux"], ["point trois"]],
        "citations": [{"index": 2, "text_fr": "citation notable"}],
    }
    return structure, synthesis


def test_render_includes_resume_and_all_section_points():
    segs = _segments("a", "b", "c")
    structure, synth = _structure_and_synthesis()
    md = syn.render_synthesis_markdown({"title": "T", "url": "u"}, segs, structure, synth)
    assert "Résumé général du cours." in md
    assert "### Introduction" in md
    assert "### Conclusion" in md
    assert "- point un" in md
    assert "- point deux" in md
    assert "- point trois" in md


def test_section_timestamp_comes_from_the_real_segment_not_the_model():
    """The model supplies an index; every displayed timestamp must be looked
    up from the segment it points at, never trusted as free text."""
    segs = _segments("a", "b", "c", start=0.0, step=90.0)  # section 2 starts at 180s
    structure, synth = _structure_and_synthesis()
    md = syn.render_synthesis_markdown({"title": "T", "url": "u"}, segs, structure, synth)
    assert "### Conclusion · 00:03:00" in md


def test_citation_timestamp_comes_from_the_real_segment():
    segs = _segments("a", "b", "c", start=0.0, step=90.0)
    structure, synth = _structure_and_synthesis()
    md = syn.render_synthesis_markdown({"title": "T", "url": "u"}, segs, structure, synth)
    assert "« citation notable » — 00:03:00" in md


def test_no_citations_section_when_there_are_none():
    segs = _segments("a", "b", "c")
    structure, synth = _structure_and_synthesis()
    synth = {**synth, "citations": []}
    md = syn.render_synthesis_markdown({"title": "T", "url": "u"}, segs, structure, synth)
    assert "Citations notables" not in md


def test_render_does_not_crash_on_an_out_of_range_section_index():
    """render_synthesis_markdown is pure and doesn't re-validate its input --
    structure is expected to already be validated or fallback-generated. But a
    bounds check stays cheap insurance rather than an IndexError reaching the
    delivery sweep."""
    segs = _segments("a", "b")
    structure = [{"start_index": 99, "end_index": 99, "title_fr": "Hors limites"}]
    synth = {"resume": "R", "section_points": [["p"]], "citations": []}
    md = syn.render_synthesis_markdown({"title": "T", "url": "u"}, segs, structure, synth)
    assert "### Hors limites · ?" in md


def test_original_title_and_url_are_kept():
    segs = _segments("a", "b", "c")
    structure, synth = _structure_and_synthesis()
    md = syn.render_synthesis_markdown(
        {"title": "EN Title", "title_original": "中文标题", "url": "https://x/y"},
        segs,
        structure,
        synth,
    )
    assert md.startswith("# Synthèse — EN Title")
    assert "- **Titre original** : 中文标题" in md
    assert "- **URL** : https://x/y" in md


# ── _generate: network mocked, transport-level ──────────────────────────────


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)[:500]

    def json(self):
        return self._payload


def _gemini_envelope(model_json_text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": model_json_text}]}}]}


async def test_generate_returns_the_parsed_model_json(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        return _FakeResponse(200, _gemini_envelope('{"sections": []}'))

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    out = await syn._generate("key", "some-model", "prompt", {"type": "object"})
    assert out == {"sections": []}


async def test_generate_raises_on_non_200(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        return _FakeResponse(429, {"error": "rate limited"})

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    with pytest.raises(syn.GeminiError, match="429"):
        await syn._generate("key", "some-model", "prompt", {"type": "object"})


async def test_generate_raises_on_unparseable_model_output(monkeypatch):
    async def fake_post(self, url, headers=None, json=None):
        return _FakeResponse(200, _gemini_envelope("not valid json"))

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    with pytest.raises(syn.GeminiError):
        await syn._generate("key", "some-model", "prompt", {"type": "object"})


async def test_generate_finds_the_text_part_even_if_not_first(monkeypatch):
    """A thinking model's response can carry a non-text part before the text one."""

    async def fake_post(self, url, headers=None, json=None):
        envelope = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thoughtSignature": "opaque"},
                            {"text": '{"resume": "ok"}'},
                        ]
                    }
                }
            ]
        }
        return _FakeResponse(200, envelope)

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    out = await syn._generate("key", "some-model", "prompt", {"type": "object"})
    assert out == {"resume": "ok"}


# ── build_structure: falls back rather than raising ─────────────────────────


async def test_build_structure_falls_back_when_the_model_is_wrong(monkeypatch):
    async def fake_generate(api_key, model, prompt, schema):
        return {"sections": [{"start_index": 5, "end_index": 9, "title_fr": "bad"}]}  # gap

    monkeypatch.setattr(syn, "_generate", fake_generate)
    out = await syn.build_structure("key", _segments(*["x"] * 10))
    assert out[0]["start_index"] == 0
    assert out[-1]["end_index"] == 9


async def test_build_structure_falls_back_when_gemini_errors(monkeypatch):
    async def fake_generate(api_key, model, prompt, schema):
        raise syn.GeminiError("boom")

    monkeypatch.setattr(syn, "_generate", fake_generate)
    out = await syn.build_structure("key", _segments(*["x"] * 5))
    assert out[-1]["end_index"] == 4


async def test_build_structure_uses_the_models_own_boundaries_when_valid(monkeypatch):
    async def fake_generate(api_key, model, prompt, schema):
        return {"sections": [{"start_index": 0, "end_index": 4, "title_fr": "Tout"}]}

    monkeypatch.setattr(syn, "_generate", fake_generate)
    out = await syn.build_structure("key", _segments(*["x"] * 5))
    assert out == [{"start_index": 0, "end_index": 4, "title_fr": "Tout"}]


# ── build_synthesis: raises rather than falling back ────────────────────────


async def test_build_synthesis_raises_on_invalid_output(monkeypatch):
    async def fake_generate(api_key, model, prompt, schema):
        return {"resume": "", "section_points": [], "citations": []}

    monkeypatch.setattr(syn, "_generate", fake_generate)
    with pytest.raises(syn.GeminiError):
        await syn.build_synthesis(
            "key", _segments("a"), [{"start_index": 0, "end_index": 0, "title_fr": "S"}]
        )


async def test_build_synthesis_returns_validated_output(monkeypatch):
    async def fake_generate(api_key, model, prompt, schema):
        return {"resume": "R", "section_points": [["p"]], "citations": []}

    monkeypatch.setattr(syn, "_generate", fake_generate)
    out = await syn.build_synthesis(
        "key", _segments("a"), [{"start_index": 0, "end_index": 0, "title_fr": "S"}]
    )
    assert out["resume"] == "R"
