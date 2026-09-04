"""Tests for scripts/mtplx-qos, the external policy controller companion
tool (docs/resource-governor/, brief section 15). Deliberately outside the
mtplx package (mechanism-vs-policy split), so this loads it by file path
rather than importing it as a normal module — it has no .py extension and
isn't installed anywhere.
"""

from __future__ import annotations

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "mtplx-qos"


def _load_module():
    loader = SourceFileLoader("mtplx_qos_under_test", str(_SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture()
def qos():
    return _load_module()


def test_script_exists_and_is_executable():
    assert _SCRIPT_PATH.exists()
    import os

    assert os.access(_SCRIPT_PATH, os.X_OK)


def test_known_profiles_match_resource_governor_builtins(qos):
    from mtplx.resource_governor import BUILTIN_PROFILES

    assert set(qos.KNOWN_PROFILES) == set(BUILTIN_PROFILES)


# ---- decision logic (no network, no real subprocess calls) --------------


def test_decide_auto_profile_interactive_when_a_trigger_fires(qos):
    qos.INTERACTIVE_TRIGGER_DETECTORS = {"fake-game": lambda: True}
    profile, reason = qos.decide_auto_profile()
    assert profile == "interactive"
    assert "fake-game" in reason


def test_decide_auto_profile_balanced_when_no_trigger_fires(qos):
    qos.INTERACTIVE_TRIGGER_DETECTORS = {"fake-game": lambda: False}
    profile, reason = qos.decide_auto_profile()
    assert profile == "balanced"


def test_decide_auto_profile_survives_a_crashing_detector(qos, capsys):
    def _boom():
        raise RuntimeError("detector exploded")

    qos.INTERACTIVE_TRIGGER_DETECTORS = {"broken": _boom, "fake-game": lambda: True}
    profile, reason = qos.decide_auto_profile()
    # A crashing detector must not take down auto mode — later detectors
    # still run, and the crash is reported, not swallowed silently.
    assert profile == "interactive"
    assert "broken detector failed" in capsys.readouterr().err


def test_decide_auto_profile_checks_detectors_in_order(qos):
    calls: list[str] = []

    def _make(name, result):
        def _detector():
            calls.append(name)
            return result

        return _detector

    qos.INTERACTIVE_TRIGGER_DETECTORS = {
        "first": _make("first", False),
        "second": _make("second", True),
        "third": _make("third", True),
    }
    profile, reason = qos.decide_auto_profile()
    assert profile == "interactive"
    assert "second" in reason
    # "third" should not have been consulted once "second" already matched.
    assert calls == ["first", "second"]


def test_process_matches_returns_false_for_a_nonexistent_process(qos):
    assert qos._process_matches("definitely-not-a-real-process-xyz123") is False


def test_process_matches_survives_a_missing_pgrep_binary(qos, monkeypatch):
    def _raise(*_args, **_kwargs):
        raise FileNotFoundError("pgrep not found")

    monkeypatch.setattr(qos.subprocess, "run", _raise)
    assert qos._process_matches("anything") is False


# ---- CLI parsing (argparse structure only, no execution) -----------------


def test_parser_accepts_every_known_profile_as_a_subcommand(qos):
    parser = qos._build_parser()
    for profile in qos.KNOWN_PROFILES:
        args = parser.parse_args([profile])
        assert args.command == profile


def test_parser_accepts_status(qos):
    parser = qos._build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"


def test_parser_accepts_auto_with_watch_and_dry_run(qos):
    parser = qos._build_parser()
    args = parser.parse_args(["auto", "--watch", "5", "--dry-run"])
    assert args.command == "auto"
    assert args.watch == 5.0
    assert args.dry_run is True


def test_parser_rejects_unknown_command(qos):
    parser = qos._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["not-a-real-command"])


def test_url_defaults_and_env_override(qos, monkeypatch):
    monkeypatch.delenv("MTPLX_QOS_URL", raising=False)
    parser = qos._build_parser()
    args = parser.parse_args(["status"])
    assert args.url == "http://127.0.0.1:8000"


# ---- HTTP client (mocked transport, no real network) ----------------------


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_get_status_calls_the_admin_endpoint(qos, monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _FakeResponse({"profile": "max"})

    monkeypatch.setattr(qos.urllib.request, "urlopen", _fake_urlopen)
    result = qos.get_status("http://example.test:8000")
    assert captured["url"] == "http://example.test:8000/admin/resource-governor"
    assert captured["method"] == "GET"
    assert result == {"profile": "max"}


def test_set_profile_posts_the_profile_body(qos, monkeypatch):
    captured = {}

    def _fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"profile": "interactive"})

    monkeypatch.setattr(qos.urllib.request, "urlopen", _fake_urlopen)
    result = qos.set_profile("interactive", "http://example.test:8000")
    assert captured["url"] == "http://example.test:8000/admin/resource-governor/profile"
    assert captured["method"] == "POST"
    assert captured["body"] == {"profile": "interactive"}
    assert result == {"profile": "interactive"}


def test_api_key_env_var_is_sent_as_both_headers(qos, monkeypatch):
    monkeypatch.setenv("MTPLX_QOS_API_KEY", "secret-key")
    captured = {}

    def _fake_urlopen(req, timeout=5):
        captured["x_api_key"] = req.get_header("X-api-key")
        captured["authorization"] = req.get_header("Authorization")
        return _FakeResponse({})

    monkeypatch.setattr(qos.urllib.request, "urlopen", _fake_urlopen)
    qos.get_status("http://example.test:8000")
    assert captured["x_api_key"] == "secret-key"
    assert captured["authorization"] == "Bearer secret-key"


def test_no_api_key_sends_no_auth_headers(qos, monkeypatch):
    monkeypatch.delenv("MTPLX_QOS_API_KEY", raising=False)
    monkeypatch.delenv("MTPLX_API_KEY", raising=False)
    captured = {}

    def _fake_urlopen(req, timeout=5):
        captured["x_api_key"] = req.get_header("X-api-key")
        return _FakeResponse({})

    monkeypatch.setattr(qos.urllib.request, "urlopen", _fake_urlopen)
    qos.get_status("http://example.test:8000")
    assert captured["x_api_key"] is None


def test_http_error_exits_cleanly_with_detail(qos, monkeypatch):
    import urllib.error
    import io

    def _fake_urlopen(req, timeout=5):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error": "bad profile"}')
        )

    monkeypatch.setattr(qos.urllib.request, "urlopen", _fake_urlopen)
    with pytest.raises(SystemExit, match="bad profile"):
        qos.get_status("http://example.test:8000")


def test_connection_error_exits_with_a_helpful_hint(qos, monkeypatch):
    import urllib.error

    def _fake_urlopen(req, timeout=5):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(qos.urllib.request, "urlopen", _fake_urlopen)
    with pytest.raises(SystemExit, match="mtplx serve"):
        qos.get_status("http://example.test:8000")
