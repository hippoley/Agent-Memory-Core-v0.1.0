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
import json
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env.local"
PROFILES = {
    "lite": ("0", "0"),
    "standard": ("1", "1"),
    "full": ("1", "1"),
    "extended": ("1", "1"),
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

def compose_args(profile: str, identity: bool = False) -> list[str]:
    args = ["docker", "compose", "--env-file", str(ENV),
            "-f", "compose.yaml", "-f", "compose.app.yaml"]
    if profile in ("full", "extended"):
        args.extend(["-f", "compose.worker.yaml"])
    if profile == "extended":
        args.extend(["-f", "compose.temporal.yaml"])
    if identity:
        args.extend(["-f", "compose.keycloak.yaml", "-f", "compose.identity.yaml"])
    return args

def call(args: list[str], *, check: bool = True) -> int:
    return subprocess.run(args, cwd=ROOT, check=check).returncode

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["up", "down", "status"])
    p.add_argument("--profile", choices=sorted(PROFILES), default=None)
    p.add_argument("--identity", action="store_true", help="Add the local Keycloak OIDC and identity control-plane stack")
    args = p.parse_args(argv)

    settings = parse_env(ENV)
    old_profile = settings.get("MEMORY_LOCAL_PROFILE", "lite")
    profile = args.profile or old_profile
    identity = args.identity or settings.get("MEMORY_LOCAL_IDENTITY", "0") == "1"
    if args.command == "up":
        current_key = settings.get("MEMORY_API_KEY", "")
        if len(current_key) < 32 or current_key.startswith(("REPLACE", "CHANGE")):
            settings["MEMORY_API_KEY"] = secrets.token_urlsafe(48)
        settings["MEMORY_TENANT_ID"] = settings.get("MEMORY_TENANT_ID", "demo-home")
        settings["MEMORY_EXTRACTOR_PROVIDER"] = settings.get("MEMORY_EXTRACTOR_PROVIDER", "rule")
        settings["MEMORY_ENABLE_VECTOR"], settings["MEMORY_ENABLE_AGE"] = PROFILES[profile]
        settings["MEMORY_LOCAL_PROFILE"] = profile
        settings["MEMORY_LOCAL_IDENTITY"] = "1" if identity else "0"
        # Ensure full-profile worker is removed when switching away, without
        # deleting DB/objects volumes.
        if old_profile == "extended" and profile != "extended":
            # Stop the optional local Temporal server and worker before
            # removing the overlay; persistent dev data is not deleted.
            call(compose_args("extended") + [
                "stop", "memory-temporal-worker", "memory-temporal"])
            call(compose_args("extended") + [
                "rm", "-f", "memory-temporal-worker", "memory-temporal"])
        if old_profile in ("full", "extended") and profile not in ("full", "extended"):
            call(compose_args(old_profile) + ["stop", "memory-worker"])
            call(compose_args(old_profile) + ["rm", "-f", "memory-worker"])
        save_env(settings)
        call(compose_args(profile, identity) + ["up", "-d", "--build", "--wait", "--remove-orphans"])
        print("\nLocal services ready (loopback only):")
        print("  Console:     http://127.0.0.1:8765/dashboard/")
        print("  Swagger:     http://127.0.0.1:8765/docs/swagger")
        print("  API manual:  http://127.0.0.1:8765/docs")
        print("  Key:         .env.local → MEMORY_API_KEY (keep private)")
        print(f"  Profile:     {profile} (not a benchmark qualification)")
        if identity:
            print("  Keycloak:    http://127.0.0.1:8081 (realm agent-memory)")
            print("  Login:       http://127.0.0.1:8765/v1/auth/oidc/login")
            print("  Admin:       admin / local-keycloak-admin-change-me (local only)")
        if profile == "extended":
            print("  Temporal UI: http://127.0.0.1:8233 (development only)")
            print("  Temporal:    localhost:7233 (verification worker starts separately)")
        return 0
    if not ENV.exists():
        p.error(".env.local missing; run 'up' first")
    if args.command == "down":
        return call(compose_args(profile, identity) + ["down"])

    # A running container is not the same as a usable product. Report Compose
    # state, unauthenticated API liveness, and authenticated PostgreSQL readiness.
    compose_status = call(compose_args(profile, identity) + ["ps"], check=False)
    base_url = os.getenv("MEMORY_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    healthy = True

    try:
        with urllib.request.urlopen(base_url + "/v1/health", timeout=4) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health.get("status") != "alive":
            raise RuntimeError(f"unexpected liveness response: {health!r}")
        print(f"API liveness:  OK ({base_url}/v1/health)")
    except Exception as exc:
        healthy = False
        print(f"API liveness:  FAILED ({exc})", file=sys.stderr)

    api_key = settings.get("MEMORY_API_KEY", "")
    if not api_key:
        healthy = False
        print("PostgreSQL:    UNKNOWN (.env.local has no MEMORY_API_KEY)", file=sys.stderr)
    else:
        request = urllib.request.Request(
            base_url + "/v1/ready",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=6) as response:
                readiness = json.loads(response.read().decode("utf-8"))
            if readiness.get("status") != "ready" or readiness.get("checks", {}).get("postgresql") != "ready":
                raise RuntimeError(f"unexpected readiness response: {readiness!r}")
            print("PostgreSQL:    OK (authenticated SELECT 1)")
        except Exception as exc:
            healthy = False
            print(f"PostgreSQL:    FAILED ({exc})", file=sys.stderr)

    if compose_status != 0:
        healthy = False
    if healthy:
        print("PRODUCT STATUS: READY")
        print("Console:       http://127.0.0.1:8765/dashboard/")
        print("Observatory:   http://127.0.0.1:8765/dashboard/observatory.html")
        print("Swagger:       http://127.0.0.1:8765/docs/swagger")
        return 0
    print("PRODUCT STATUS: NOT READY — inspect Docker Compose output and service logs.", file=sys.stderr)
    return 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Local Docker command failed with exit status {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
