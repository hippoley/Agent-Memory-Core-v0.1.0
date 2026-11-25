"""Backend selection contract: HIP is not equivalent to generic CUDA."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from contextlib import nullcontext
import pytest


def load_server(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=nullcontext))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoModelForCausalLM=object, AutoTokenizer=object))
    path = Path(__file__).resolve().parents[1] / "scripts/eval/serve_local_llm.py"
    spec = importlib.util.spec_from_file_location("amc_backend_select_test", path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    return server


def fake_torch(*, hip=None, available=False):
    return SimpleNamespace(
        version=SimpleNamespace(hip=hip),
        cuda=SimpleNamespace(is_available=lambda: available,
                             get_device_name=lambda _: "AMD Radeon 8060S" if hip else "NVIDIA GPU"))


def test_amd_hip_is_recognized(monkeypatch):
    server = load_server(monkeypatch)
    selected = server.select_execution_backend(
        fake_torch(hip="7.0", available=True), require_rocm=True)
    assert selected["execution_backend"] == "rocm"
    assert selected["device_map"] == "cuda"
    assert "8060S" in selected["device_name"]


def test_cuda_without_hip_must_not_pass_rocm_gate(monkeypatch):
    server = load_server(monkeypatch)
    with pytest.raises(RuntimeError, match="ROCm required"):
        server.select_execution_backend(fake_torch(available=True), require_rocm=True)


def test_hip_installed_without_gpu_must_not_pass(monkeypatch):
    server = load_server(monkeypatch)
    with pytest.raises(RuntimeError, match="ROCm required"):
        server.select_execution_backend(fake_torch(hip="7.0"), require_rocm=True)


def test_cpu_mode_must_not_pass_rocm_gate(monkeypatch):
    server = load_server(monkeypatch)
    with pytest.raises(RuntimeError, match="ROCm required"):
        server.select_execution_backend(fake_torch(hip="7.0", available=True),
                                        requested="cpu", require_rocm=True)


def test_cpu_fallback_is_explicit_in_health_metadata(monkeypatch):
    server = load_server(monkeypatch)
    chosen = server.select_execution_backend(fake_torch())
    assert chosen["execution_backend"] == "cpu"
    assert chosen["device_map"] == "cpu"


def test_explicit_cuda_without_gpu_is_an_error(monkeypatch):
    server = load_server(monkeypatch)
    with pytest.raises(RuntimeError, match="explicitly requested"):
        server.select_execution_backend(fake_torch(), requested="cuda")
