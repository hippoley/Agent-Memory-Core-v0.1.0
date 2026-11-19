"""Start a hands-on local product demo and open both browser interfaces.

This is a convenience wrapper around the supported local launcher and the
public SDK smoke journey. It does not expose the API to the network.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8765"
PAGES = (
    ("Memory Console", BASE_URL + "/dashboard/"),
    ("Memory Observatory", BASE_URL + "/dashboard/observatory.html"),
    ("API Manual", BASE_URL + "/docs"),
)


def launch_command(profile: str, identity: bool) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "launch_local.py"),
        "up",
        "--profile",
        profile,
    ]
    if identity:
        command.append("--identity")
    return command


def smoke_command() -> list[str]:
    return [sys.executable, str(ROOT / "scripts" / "smoke_local.py")]


def seed_command() -> list[str]:
    return [sys.executable, str(ROOT / "scripts" / "seed_demo.py"), "--seed"]


def local_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["MEMORY_BASE"] = BASE_URL
    env["MEMORY_BASE_URL"] = BASE_URL
    env_file = ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key in {"MEMORY_API_KEY", "MEMORY_TENANT_ID"}:
                    env[key] = value
    return env

def require_sdk() -> None:
    try:
        import agent_memory_core.sdk  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "The local Python SDK is not installed in this environment. "
            "From the repository root run:\n"
            "  python -m pip install -e '.[api,postgres]'\n"
            "Then rerun: python scripts/demo_local.py"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start Agent Memory Core locally, verify a real SDK lifecycle, and open the UI."
    )
    parser.add_argument(
        "--profile",
        choices=("lite", "standard", "full", "extended"),
        default="lite",
        help="Local Compose profile (default: lite).",
    )
    parser.add_argument(
        "--identity",
        action="store_true",
        help="Also start the local Keycloak identity stack.",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Only start and open the UI; do not create the SDK smoke records.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Print the browser URLs without opening tabs.",
    )
    args = parser.parse_args(argv)

    if not args.skip_smoke:
        require_sdk()

    subprocess.run(launch_command(args.profile, args.identity), cwd=ROOT, check=True)

    if args.skip_smoke:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "launch_local.py"), "status"],
            cwd=ROOT,
            check=True,
        )
    else:
        child_env = local_environment()
        subprocess.run(smoke_command(), cwd=ROOT, env=child_env, check=True)
        subprocess.run(seed_command(), cwd=ROOT, env=child_env, check=True)

    print("\nAGENT_MEMORY_CORE_LOCAL_DEMO_READY")
    print("The API is bound to loopback; it is not published to the Internet.")
    print("A real SDK smoke journey and namespaced demo memories are ready for inspection.")
    print("The credential remains in .env.local; do not paste it into chat or commit it.")
    print("\nOpen these local interfaces:")
    for label, url in PAGES:
        print(f"  {label}: {url}")
    print("  API key: .env.local → MEMORY_API_KEY")
    print("  Stop:    python scripts/launch_local.py down")

    if not args.no_open:
        for _, url in PAGES:
            try:
                webbrowser.open_new_tab(url)
            except Exception as exc:
                print(f"Could not open a browser tab for {url}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(
            f"LOCAL_DEMO_FAILED: command exited with status {exc.returncode}. "
            "Inspect Docker Compose logs before retrying.",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode)
