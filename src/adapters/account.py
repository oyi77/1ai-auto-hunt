"""Omni Account Onboarding adapter — stdio (CLI subprocess).

Wraps omni_onboarding.py for email-driven account creation and OTP extraction.
Used by: factory (email verification during account creation).

Interface: argparse CLI with subcommands (register, batch, list-services, monitor).
Transport: subprocess.run() — fastest for local CLI tools.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

from src.core.logger import get_logger

log = get_logger(__name__)

# Path to omni_onboarding.py — adjust if relocated
ONBOARDING_SCRIPT = "/home/openclaw/projects/omni-account-onboarding/omni_onboarding.py"


class AccountOnboardingClient:
    """Client for omni-account-onboarding via stdio subprocess."""

    def __init__(self, script_path: str = ONBOARDING_SCRIPT) -> None:
        self._script = script_path

    def _run(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", self._script, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    # ── Email Generation ───────────────────────────────────────────────
    def generate_email(self, domain: str = "berkahkarya.org") -> str:
        """Generate a unique email address for account creation.

        Uses catch-all routing on the Stalwart mail server.
        """
        result = self._run("generate-email", "--domain", domain)
        if result.returncode != 0:
            raise RuntimeError(f"generate-email failed: {result.stderr}")
        return result.stdout.strip()

    # ── Account Registration ───────────────────────────────────────────
    def register(self, service: str, target: str, timeout: int = 120) -> dict[str, Any]:
        """Register an account on a target service.

        Args:
            service: service name (github, gitlab, twitter, openrouter, etc.)
            target: target username
            timeout: max seconds to wait for verification email

        Returns: dict with registration result (email, otp, credentials)
        """
        result = self._run(
            "register",
            "--service", service,
            "--target", target,
            "--timeout", str(timeout),
            timeout=timeout + 10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"register failed: {result.stderr}")
        # Parse output — omni_onboarding prints structured output
        return {"raw": result.stdout, "service": service, "target": target}

    def batch_register(
        self,
        service: str,
        users: list[str],
        timeout: int = 120,
    ) -> list[dict[str, Any]]:
        """Register multiple accounts in batch.

        Args:
            service: service name
            users: list of target usernames
            timeout: per-user timeout
        """
        result = self._run(
            "batch",
            "--service", service,
            "--users", ",".join(users),
            "--timeout", str(timeout),
            timeout=(timeout + 10) * len(users),
        )
        if result.returncode != 0:
            raise RuntimeError(f"batch register failed: {result.stderr}")
        return [{"raw": result.stdout, "service": service, "users": users}]

    # ── Inbox Monitoring ───────────────────────────────────────────────
    def monitor(
        self,
        target: str,
        pattern: str = "verify|otp",
        timeout: int = 120,
    ) -> dict[str, Any]:
        """Monitor inbox for verification emails matching pattern.

        Args:
            target: email prefix to monitor
            pattern: regex pattern for subject matching
            timeout: max seconds to wait
        """
        result = self._run(
            "monitor",
            "--target", target,
            "--pattern", pattern,
            "--timeout", str(timeout),
            timeout=timeout + 10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"monitor failed: {result.stderr}")
        return {"raw": result.stdout, "target": target}

    # ── Service Registry ───────────────────────────────────────────────
    def list_services(self) -> list[str]:
        """List supported services for registration."""
        result = self._run("list-services")
        if result.returncode != 0:
            raise RuntimeError(f"list-services failed: {result.stderr}")
        return [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]

    # ── Testing ────────────────────────────────────────────────────────
    def test_jmap(self, host: str = "127.0.0.1", port: int = 18080) -> dict:
        """Test JMAP connection to Stalwart mail server."""
        result = self._run(
            "test-jmap",
            "--host", host,
            "--jmap-port", str(port),
            "--use-http",
            timeout=30,
        )
        return {"success": result.returncode == 0, "output": result.stdout, "error": result.stderr}
