"""One-command LOCAL development stack: database + migrated API + Swagger.

Does not publish the server to the Internet or provision a production identity.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env.local"
PROFILES = {
    "lite": ("0", "0"),
    "standard": ("1", "1"),
    "full": ("1", "1"),
}

def parse_env(path: Path) -> dict[str, str]:
    result = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                result[key] = value
    return result

def save_env(settings: dict[str, str]) -> None:
    body = "# Generated locally by scripts/launch_local.py; NEVER COMMIT.\n"
    body += "".join(f"{k}={v}\n" for k, v in sorted(settings.items()))
    ENV.write_text(body, encoding="utf-8")
    if os.name != "nt":
        ENV.chmod(0o600)

def compose_args(profile: str) -> list[str]:
    args = ["docker", "compose", "--env-file", str(ENV),
            "-f", "compose.yaml", "-f", "compose.app.yaml"]
    if profile == "full":
        args.extend(["-f", "compose.worker.yaml"])
    return args

def call(args: list[str], *, check: bool = True) -> int:
    return subprocess.run(args, cwd=ROOT, check=check).returncode

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["up", "down", "status"])
    p.add_argument("--profile", choices=sorted(PROFILES), default=None)
    args = p.parse_args(argv)

    settings = parse_env(ENV)
    old_profile = settings.get("MEMORY_LOCAL_PROFILE", "lite")
    profile = args.profile or old_profile
    if args.command == "up":
        current_key = settings.get("MEMORY_API_KEY", "")
        if len(current_key) < 32 or current_key.startswith(("REPLACE", "CHANGE")):
            settings["MEMORY_API_KEY"] = secrets.token_urlsafe(48)
        settings["MEMORY_TENANT_ID"] = settings.get("MEMORY_TENANT_ID", "demo-home")
        settings["MEMORY_EXTRACTOR_PROVIDER"] = settings.get("MEMORY_EXTRACTOR_PROVIDER", "rule")
        settings["MEMORY_ENABLE_VECTOR"], settings["MEMORY_ENABLE_AGE"] = PROFILES[profile]
        settings["MEMORY_LOCAL_PROFILE"] = profile
        # Ensure full-profile worker is removed when switching away, without
        # deleting DB/objects volumes.
        if old_profile == "full" and profile != "full":
            call(compose_args("full") + ["stop", "memory-worker"])
            call(compose_args("full") + ["rm", "-f", "memory-worker"])
        save_env(settings)
        call(compose_args(profile) + ["up", "-d", "--build", "--wait", "--remove-orphans"])
        print("\nLocal services ready (loopback only):")
        print("  Console:     http://127.0.0.1:8765/dashboard/")
        print("  Swagger:     http://127.0.0.1:8765/docs/swagger")
        print("  API manual:  http://127.0.0.1:8765/docs")
        print("  Key:         .env.local → MEMORY_API_KEY (keep private)")
        print(f"  Profile:     {profile} (not a benchmark qualification)")
        return 0
    if not ENV.exists():
        p.error(".env.local missing; run 'up' first")
    call(compose_args(profile) + (["ps"] if args.command == "status" else ["down"]))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Local Docker command failed with exit status {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
