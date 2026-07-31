"""Structure + written rendering: content-driven sectioning, single-hop
translation, and a full rewrite -- not a summary -- of each section.

The whole point of this pipeline is a discipline already used for the whisper
task and the delivery glossary: the model never gets to invent a timestamp.
It references a segment *index*; the render layer looks up the real time. So
the tests that matter most are the validators (index gaps/overlaps/out-of-range
must be rejected) and the fallback (a grouping step must never be able to fail
delivery outright, unlike the rewrite, which is allowed to and should retry).

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


# ── _validate_rewrite ────────────────────────────────────────────────────────

_SECTION = {"start_index": 5, "end_index": 10, "title_fr": "Une section"}


def test_a_single_untitled_subsection_is_accepted():
    """The common case: one subject, no sub-heading needed."""
    raw = {"subsections": [{"start_index": 5, "title_fr": "", "text_fr": "Texte réécrit."}]}
    out = syn._validate_rewrite(raw, _SECTION, n_segments=20)
    assert out == [{"start_index": 5, "title_fr": "", "text_fr": "Texte réécrit."}]


def test_multiple_titled_subsections_are_accepted_in_order():
    raw = {
        "subsections": [
            {"start_index": 5, "title_fr": "Premier point", "text_fr": "A"},
            {"start_index": 8, "title_fr": "Second point", "text_fr": "B"},
        ]
    }
    out = syn._validate_rewrite(raw, _SECTION, n_segments=20)
    assert [s["start_index"] for s in out] == [5, 8]


def test_multiple_subsections_with_any_untitled_are_rejected():
    """Once concatenated, an untitled block among titled ones is ambiguous --
    the reader can't tell where one ends and the next begins."""
    raw = {
        "subsections": [
            {"start_index": 5, "title_fr": "Premier point", "text_fr": "A"},
            {"start_index": 8, "title_fr": "", "text_fr": "B"},
        ]
    }
    assert syn._validate_rewrite(raw, _SECTION, n_segments=20) is None


def test_a_start_index_before_the_section_is_rejected():
    raw = {"subsections": [{"start_index": 4, "title_fr": "", "text_fr": "A"}]}
    assert syn._validate_rewrite(raw, _SECTION, n_segments=20) is None


def test_a_start_index_after_the_section_is_rejected():
    raw = {"subsections": [{"start_index": 11, "title_fr": "", "text_fr": "A"}]}
    assert syn._validate_rewrite(raw, _SECTION, n_segments=20) is None


def test_subsections_out_of_reading_order_are_rejected():
    raw = {
        "subsections": [
            {"start_index": 8, "title_fr": "Second", "text_fr": "B"},
            {"start_index": 5, "title_fr": "Premier", "text_fr": "A"},
        ]
    }
    assert syn._validate_rewrite(raw, _SECTION, n_segments=20) is None


def test_empty_text_is_rejected():
    raw = {"subsections": [{"start_index": 5, "title_fr": "", "text_fr": "   "}]}
    assert syn._validate_rewrite(raw, _SECTION, n_segments=20) is None


def test_no_subsections_at_all_is_rejected():
    assert syn._validate_rewrite({"subsections": []}, _SECTION, n_segments=20) is None


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"subsections": "not a list"},
        {"subsections": [{"title_fr": "", "text_fr": "A"}]},  # missing start_index
        {"subsections": [{"start_index": 5, "text_fr": "A"}]},  # missing title_fr is tolerated...
    ],
)
def test_malformed_rewrite_shapes_degrade_to_none_where_expected(raw):
    """The one exception: title_fr is read with .get, since an implementation
    that omits an empty title_fr entirely is still an untitled subsection."""
    out = syn._validate_rewrite(raw, _SECTION, n_segments=20)
    if raw == {"subsections": [{"start_index": 5, "text_fr": "A"}]}:
        assert out == [{"start_index": 5, "title_fr": "", "text_fr": "A"}]
    else:
        assert out is None


# ── _warn_if_short: heuristic, log-only ─────────────────────────────────────


def test_a_short_rewrite_logs_a_warning(caplog):
    import logging

    source = _segments("x" * 1000)
    subs = [{"start_index": 0, "title_fr": "", "text_fr": "y" * 50}]
    with caplog.at_level(logging.WARNING, logger="scrapower.coordinator.channel.synthesis"):
        syn._warn_if_short("Une section", source, subs)
    assert "looks short" in caplog.text


def test_a_normal_length_rewrite_does_not_warn(caplog):
    import logging

    source = _segments("x" * 1000)
    subs = [{"start_index": 0, "title_fr": "", "text_fr": "y" * 1200}]
    with caplog.at_level(logging.WARNING, logger="scrapower.coordinator.channel.synthesis"):
        syn._warn_if_short("Une section", source, subs)
    assert "looks short" not in caplog.text


def test_no_warning_without_a_reference_length():
    """No source text (degenerate case) means no ratio to compute -- must not
    raise a ZeroDivisionError."""
    syn._warn_if_short("S", [{"text": ""}], [{"start_index": 0, "title_fr": "", "text_fr": "y"}])


# ── render_course_markdown ───────────────────────────────────────────────────


def _structure_and_rewrites():
    structure = [
        {"start_index": 0, "end_index": 1, "title_fr": "Introduction"},
        {"start_index": 2, "end_index": 2, "title_fr": "Conclusion"},
    ]
    rewrites = [
        [{"start_index": 0, "title_fr": "", "text_fr": "Texte intégral de l'introduction."}],
        [{"start_index": 2, "title_fr": "", "text_fr": "Texte intégral de la conclusion."}],
    ]
    return structure, rewrites


def test_render_includes_the_full_text_of_every_section():
    segs = _segments("a", "b", "c")
    structure, rewrites = _structure_and_rewrites()
    md = syn.render_course_markdown({"title": "T", "url": "u"}, segs, structure, rewrites)
    assert "## Introduction" in md
    assert "## Conclusion" in md
    assert "Texte intégral de l'introduction." in md
    assert "Texte intégral de la conclusion." in md


def test_titled_subsections_get_their_own_heading_and_timestamp():
    segs = _segments("a", "b", "c", start=0.0, step=90.0)
    structure = [{"start_index": 0, "end_index": 2, "title_fr": "Section"}]
    rewrites = [
        [
            {"start_index": 0, "title_fr": "Premier point", "text_fr": "Texte A."},
            {"start_index": 2, "title_fr": "Second point", "text_fr": "Texte B."},
        ]
    ]
    md = syn.render_course_markdown({"title": "T", "url": "u"}, segs, structure, rewrites)
    assert "### Premier point · 00:00:00" in md
    assert "### Second point · 00:03:00" in md
    assert "Texte A." in md
    assert "Texte B." in md


def test_an_untitled_single_subsection_gets_no_h3():
    """The common case: prose sits directly under the section's own H2."""
    segs = _segments("a", "b", "c")
    structure, rewrites = _structure_and_rewrites()
    md = syn.render_course_markdown({"title": "T", "url": "u"}, segs, structure, rewrites)
    assert "###" not in md


def test_section_timestamp_comes_from_the_real_segment_not_the_model():
    """The model supplies an index; every displayed timestamp must be looked
    up from the segment it points at, never trusted as free text."""
    segs = _segments("a", "b", "c", start=0.0, step=90.0)  # section 2 starts at 180s
    structure, rewrites = _structure_and_rewrites()
    md = syn.render_course_markdown({"title": "T", "url": "u"}, segs, structure, rewrites)
    assert "## Conclusion · 00:03:00" in md


def test_render_does_not_crash_on_an_out_of_range_section_index():
    """render_course_markdown is pure and doesn't re-validate its input --
    structure is expected to already be validated or fallback-generated. But a
    bounds check stays cheap insurance rather than an IndexError reaching the
    delivery sweep."""
    segs = _segments("a", "b")
    structure = [{"start_index": 99, "end_index": 99, "title_fr": "Hors limites"}]
    rewrites = [[{"start_index": 99, "title_fr": "", "text_fr": "Texte."}]]
    md = syn.render_course_markdown({"title": "T", "url": "u"}, segs, structure, rewrites)
    assert "## Hors limites · ?" in md


def test_original_title_and_url_are_kept():
    segs = _segments("a", "b", "c")
    structure, rewrites = _structure_and_rewrites()
    md = syn.render_course_markdown(
        {"title": "EN Title", "title_original": "中文标题", "url": "https://x/y"},
        segs,
        structure,
        rewrites,
    )
    assert md.startswith("# EN Title")
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


# ── build_rewrite / build_full_rewrite: raises rather than falling back ─────


async def test_build_rewrite_raises_on_invalid_output(monkeypatch):
    async def fake_generate(api_key, model, prompt, schema):
        return {"subsections": []}

    monkeypatch.setattr(syn, "_generate", fake_generate)
    section = {"start_index": 0, "end_index": 0, "title_fr": "S"}
    with pytest.raises(syn.GeminiError):
        await syn.build_rewrite("key", _segments("a"), section)


async def test_build_rewrite_returns_validated_output(monkeypatch):
    async def fake_generate(api_key, model, prompt, schema):
        return {"subsections": [{"start_index": 0, "title_fr": "", "text_fr": "Texte réécrit."}]}

    monkeypatch.setattr(syn, "_generate", fake_generate)
    section = {"start_index": 0, "end_index": 0, "title_fr": "S"}
    out = await syn.build_rewrite("key", _segments("a"), section)
    assert out[0]["text_fr"] == "Texte réécrit."


async def test_build_rewrite_slices_only_its_own_section(monkeypatch):
    """A section covering indices 2-3 of a longer transcript must only see
    (and only be allowed to anchor to) those two segments."""
    captured = {}

    async def fake_generate(api_key, model, prompt, schema):
        captured["prompt"] = prompt
        return {"subsections": [{"start_index": 2, "title_fr": "", "text_fr": "Texte."}]}

    monkeypatch.setattr(syn, "_generate", fake_generate)
    segs = _segments("s0", "s1", "s2", "s3", "s4")
    section = {"start_index": 2, "end_index": 3, "title_fr": "S"}
    await syn.build_rewrite("key", segs, section)
    assert "[2]" in captured["prompt"] and "[3]" in captured["prompt"]
    assert "[0]" not in captured["prompt"] and "[4]" not in captured["prompt"]


async def test_build_full_rewrite_calls_once_per_section_in_order(monkeypatch):
    calls = []

    async def fake_build_rewrite(api_key, segments, section, language="?"):
        calls.append(section["title_fr"])
        return [{"start_index": section["start_index"], "title_fr": "", "text_fr": "x"}]

    monkeypatch.setattr(syn, "build_rewrite", fake_build_rewrite)
    structure = [
        {"start_index": 0, "end_index": 1, "title_fr": "Un"},
        {"start_index": 2, "end_index": 3, "title_fr": "Deux"},
    ]
    out = await syn.build_full_rewrite("key", _segments(*["x"] * 4), structure)
    assert calls == ["Un", "Deux"]
    assert len(out) == 2


async def test_build_full_rewrite_stops_at_the_first_failure(monkeypatch):
    """No sensible fallback for 'write the content' -- raises rather than
    delivering a partial course (see module docstring)."""
    calls = []

    async def failing_build_rewrite(api_key, segments, section, language="?"):
        calls.append(section["title_fr"])
        if section["title_fr"] == "Deux":
            raise syn.GeminiError("boom")
        return [{"start_index": section["start_index"], "title_fr": "", "text_fr": "x"}]

    monkeypatch.setattr(syn, "build_rewrite", failing_build_rewrite)
    structure = [
        {"start_index": 0, "end_index": 1, "title_fr": "Un"},
        {"start_index": 2, "end_index": 3, "title_fr": "Deux"},
        {"start_index": 4, "end_index": 5, "title_fr": "Trois"},
    ]
    with pytest.raises(syn.GeminiError):
        await syn.build_full_rewrite("key", _segments(*["x"] * 6), structure)
    assert calls == ["Un", "Deux"], "must not attempt sections after the failure"
