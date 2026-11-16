"""The AGE/Temporal profile is opt-in and does not alter Lite/Standard/Full."""
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("memory_launcher",ROOT/"scripts/launch_local.py")
launcher=importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_extended_enables_graph_and_temporal_only_when_selected():
    assert launcher.PROFILES["extended"]==("1","1")
    for profile in ("lite","standard","full"):
        args=launcher.compose_args(profile)
        assert "compose.temporal.yaml" not in args
    assert "compose.temporal.yaml" in launcher.compose_args("extended")
    assert "compose.worker.yaml" in launcher.compose_args("extended")


def test_temporal_overlay_is_development_only_and_bound_to_localhost():
    config=(ROOT/"compose.temporal.yaml").read_text(encoding="utf-8")
    assert "temporalio/temporal:1.10.1" in config
    assert '"127.0.0.1:7233:7233"' in config
    assert '"127.0.0.1:8233:8233"' in config
    assert "agent_memory_core.temporal_worker" in config
    assert "memory-temporal:7233" in config
    assert "memory-db:" in config
    assert "service_healthy" in config
    assert "MEMORY_DSN:" in config
    assert "memory_temporal_dev_data:" in config


def test_temporal_worker_installs_optional_adapter_not_app_http_runtime():
    build=(ROOT/"docker/Dockerfile.temporal").read_text(encoding="utf-8")
    assert ".[postgres,temporal]" in build
    assert "memory_core.temporal_worker" in build
    assert "USER memory" in build
