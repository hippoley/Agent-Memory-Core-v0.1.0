"""Unit checks for the local demo command composition."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "demo_local", ROOT / "scripts" / "demo_local.py"
)
assert SPEC and SPEC.loader
demo_local = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo_local)


def test_lite_demo_launches_supported_local_profile():
    command = demo_local.launch_command("lite", False)
    assert command == [
        sys.executable,
        str(ROOT / "scripts" / "launch_local.py"),
        "up",
        "--profile",
        "lite",
    ]


def test_identity_flag_is_explicit_and_opt_in():
    command = demo_local.launch_command("extended", True)
    assert command[-1] == "--identity"
    assert command[command.index("--profile") + 1] == "extended"


def test_smoke_uses_the_public_sdk_entrypoint():
    assert demo_local.smoke_command() == [
        sys.executable,
        str(ROOT / "scripts" / "smoke_local.py"),
    ]


def test_browser_urls_are_loopback_only():
    assert all(url.startswith("http://127.0.0.1:") for _, url in demo_local.PAGES)


def test_demo_seeds_namespaced_live_records():
    assert demo_local.seed_command() == [
        sys.executable,
        str(ROOT / "scripts" / "seed_demo.py"),
        "--seed",
    ]


def test_local_environment_pins_requests_to_loopback():
    env = demo_local.local_environment()
    assert env["MEMORY_BASE"] == "http://127.0.0.1:8765"
    assert env["MEMORY_BASE_URL"] == "http://127.0.0.1:8765"


def test_partial_seed_failure_never_prints_demo_ready(monkeypatch, capsys):
    import subprocess
    import pytest

    monkeypatch.setattr(demo_local, "require_sdk", lambda: None)
    seen = []
    def fake_run(command, **kwargs):
        seen.append(command)
        if command == demo_local.seed_command():
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(demo_local.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        demo_local.main(["--no-open"])
    assert demo_local.seed_command() in seen
    assert "AGENT_MEMORY_CORE_LOCAL_DEMO_READY" not in capsys.readouterr().out
