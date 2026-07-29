"""yt-dlp must be upgraded on every worker run, not installed once.

Real failure: every Bilibili download died with "HTTP 412 Precondition Failed"
while the coordinator fetched the same URL through the same proxy in the same
second. The difference was the yt-dlp version -- 2026.02.21 on the worker,
2026.07.04 on the coordinator. Modal caches its image layer, `import yt_dlp`
keeps succeeding, so install-if-missing pinned every worker to a version the
site had learned to reject.

The package lists are patched to stdlib module names so these tests describe the
policy without depending on faster-whisper or yt-dlp being installed here.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from scrapower.worker.runtimes import whisper_runner as wr


@pytest.fixture
def pip_calls(monkeypatch):
    """Record pip invocations instead of running them."""
    calls: list[list[str]] = []

    def fake_check_call(args, *a, **kw):
        calls.append(list(args))
        return 0

    monkeypatch.setattr(wr.subprocess, "check_call", fake_check_call)
    return calls


@pytest.fixture
def importable_lists(monkeypatch):
    """Both lists point at stdlib modules, so every import in _ensure_deps works."""
    monkeypatch.setattr(wr, "ALWAYS_UPGRADE", ("json",))
    monkeypatch.setattr(wr, "INSTALL_IF_MISSING", ("base64",))


def test_the_real_config_upgrades_yt_dlp_and_not_faster_whisper():
    """Guards the policy itself: which package is in which list."""
    assert wr.ALWAYS_UPGRADE == ("yt-dlp",)
    assert "faster-whisper" in wr.INSTALL_IF_MISSING
    assert "yt-dlp" not in wr.INSTALL_IF_MISSING


def test_upgrade_happens_even_though_the_module_imports_fine(pip_calls, importable_lists):
    """The whole bug: yt_dlp imports, so nothing used to happen."""
    wr._ensure_deps()
    upgrades = [c for c in pip_calls if "-U" in c]
    assert len(upgrades) == 1, f"expected exactly one forced upgrade, got {pip_calls}"
    assert upgrades[0] == [sys.executable, "-m", "pip", "install", "-q", "-U", "json"]


def test_stable_deps_are_not_reinstalled_when_present(pip_calls, importable_lists):
    """faster-whisper is ~200MB and does not chase hostile sites."""
    wr._ensure_deps()
    assert not [c for c in pip_calls if "base64" in c], (
        "an importable stable dep must not be reinstalled on every task"
    )


def test_missing_stable_dep_is_installed_without_upgrading(monkeypatch, pip_calls):
    monkeypatch.setattr(wr, "ALWAYS_UPGRADE", ())
    monkeypatch.setattr(wr, "INSTALL_IF_MISSING", ("definitely-not-installed-xyz",))
    wr._ensure_deps()
    installs = [c for c in pip_calls if "definitely-not-installed-xyz" in c]
    assert len(installs) == 1
    assert "-U" not in installs[0], "install-if-missing must not force an upgrade"


def test_a_failing_index_does_not_sink_a_working_install(monkeypatch, importable_lists):
    """Losing PyPI for a moment must not fail a task whose yt-dlp still works."""

    def boom(args, *a, **kw):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(wr.subprocess, "check_call", boom)
    wr._ensure_deps()  # must not raise: the module is importable


def test_upgrade_failure_is_fatal_when_the_package_is_also_unusable(monkeypatch):
    """A worker with no yt-dlp cannot download anything -- fail loudly, not later."""

    def boom(args, *a, **kw):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(wr.subprocess, "check_call", boom)
    monkeypatch.setattr(wr, "INSTALL_IF_MISSING", ())
    monkeypatch.setattr(wr, "ALWAYS_UPGRADE", ("no-such-package-xyz",))
    with pytest.raises(ImportError):
        wr._ensure_deps()


def test_version_is_logged(capsys, pip_calls, importable_lists):
    """The missing diagnostic that made this bug expensive to find."""
    wr._ensure_deps()
    err = capsys.readouterr().err
    assert "json" in err, f"the upgraded package's version must be logged, got {err!r}"
