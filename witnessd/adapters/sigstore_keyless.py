"""Opt-in adapter for Sigstore keyless DSSE attestations.

This module locates the external ``sigstore`` console script only to resolve its
Sigstore-capable Python interpreter. The library import remains isolated in a
standalone subprocess helper. All errors are returned as structured fail-closed
results; the adapter never manufactures a Sigstore bundle or keyless boundary.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

MINIMUM_SIGSTORE_VERSION = (4, 0, 0)
DEFAULT_PREDICATE_TYPE = "https://moonweave.dev/witnessd/keyless-evidence-anchor/v1"
PUBLIC_LOG_WARNING = (
    "WARNING: KEYLESS SIGNING PERMANENTLY PUBLISHES YOUR IDENTITY AND THE "
    "EVIDENCE HASH TO THE PUBLIC REKOR TRANSPARENCY LOG; IT CANNOT BE DELETED."
)


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "keyless_identity": False,
        "transparency_logged": False,
        "error": {"code": code, "message": message},
    }


def _resolve_sigstore(binary: str) -> str | None:
    if os.path.sep in binary or (
        os.path.altsep is not None and os.path.altsep in binary
    ):
        path = Path(binary)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(binary)


def _parse_version(output: str) -> tuple[int, int, int] | None:
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", output)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _read_sigstore_interpreter(console_script: str) -> str | None:
    try:
        with Path(console_script).open("rb") as script:
            first_line = script.readline(4096)
    except OSError:
        return None
    if not first_line.startswith(b"#!"):
        return None
    try:
        interpreter = first_line[2:].decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not interpreter or any(character.isspace() for character in interpreter):
        return None
    return interpreter


def _env_enabled(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def attest_keyless(
    evidence_file: str | os.PathLike[str],
    *,
    sigstore_binary: str = "sigstore",
    identity_token: str | None = None,
    oauth_force_oob: bool = False,
    staging: bool = False,
    environ: Mapping[str, str] | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Attest ``evidence_file`` and return a parsed Sigstore v0.3 bundle.

    Interactive OIDC is allowed only on a terminal. Non-interactive callers
    must pass an identity token explicitly or through
    ``SIGSTORE_IDENTITY_TOKEN``. Ambient providers are always disabled so cron
    and CI cannot silently acquire a different identity.
    """

    print(PUBLIC_LOG_WARNING, file=sys.stderr, flush=True)
    environment = os.environ if environ is None else environ
    resolved = _resolve_sigstore(sigstore_binary)
    if resolved is None:
        return _error(
            "ERR_WITNESSD_SIGSTORE_UNAVAILABLE",
            f"sigstore executable not found: {sigstore_binary}",
        )

    token = identity_token or environment.get("SIGSTORE_IDENTITY_TOKEN") or None
    interactive = bool(sys.stdin.isatty())
    if token is None and not interactive:
        return _error(
            "ERR_WITNESSD_KEYLESS_NONINTERACTIVE",
            "keyless signing requires an interactive terminal or identity token",
        )

    evidence = Path(evidence_file)
    if not evidence.is_file():
        return _error(
            "ERR_WITNESSD_KEYLESS_EVIDENCE_MISSING",
            f"evidence file not found: {evidence}",
        )

    interpreter = _read_sigstore_interpreter(resolved)
    if interpreter is None:
        return _error(
            "ERR_WITNESSD_SIGSTORE_VERSION_CHECK_FAILED",
            "sigstore version check failed",
        )

    try:
        version = subprocess.run(
            [
                interpreter,
                "-c",
                "import sigstore,sys;print(sigstore.__version__)",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return _error(
            "ERR_WITNESSD_SIGSTORE_VERSION_CHECK_FAILED",
            "sigstore version check failed",
        )
    parsed_version = _parse_version(f"{version.stdout}\n{version.stderr}")
    if version.returncode != 0 or parsed_version is None:
        return _error(
            "ERR_WITNESSD_SIGSTORE_VERSION_CHECK_FAILED",
            "sigstore version could not be determined",
        )
    if parsed_version < MINIMUM_SIGSTORE_VERSION:
        return _error(
            "ERR_WITNESSD_SIGSTORE_VERSION_UNSUPPORTED",
            "sigstore 4.0.0 or newer is required",
        )

    use_staging = staging or _env_enabled(environment.get("SIGSTORE_STAGING"))
    with tempfile.TemporaryDirectory(prefix="witnessd-keyless-") as tmp:
        bundle_path = Path(tmp) / "sigstore-bundle.json"
        helper_path = Path(__file__).with_name("_keyless_sign_helper.py")
        command = [
            interpreter,
            str(helper_path),
            "--evidence",
            str(evidence),
            "--bundle",
            str(bundle_path),
            "--oidc-disable-ambient-providers",
            *(["--staging"] if use_staging else []),
            *(["--identity-token-stdin"] if token is not None else []),
            *(["--oauth-force-oob"] if oauth_force_oob else []),
        ]
        run_options: dict[str, Any] = {
            "capture_output": token is not None,
            "text": True,
            "check": False,
            "timeout": timeout_seconds,
        }
        if token is not None:
            run_options["input"] = json.dumps({"identity_token": token})
        try:
            completed = subprocess.run(command, **run_options)
        except subprocess.TimeoutExpired:
            return _error(
                "ERR_WITNESSD_KEYLESS_ATTEST_TIMEOUT",
                "sigstore keyless helper timed out",
            )
        except OSError:
            return _error(
                "ERR_WITNESSD_KEYLESS_ATTEST_FAILED",
                "sigstore keyless helper could not be started",
            )
        if completed.returncode != 0:
            return _error(
                "ERR_WITNESSD_KEYLESS_ATTEST_FAILED",
                "sigstore keyless helper failed; no keyless bundle was emitted",
            )
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _error(
                "ERR_WITNESSD_KEYLESS_BUNDLE_INVALID",
                "sigstore did not emit a valid JSON bundle",
            )
    if not isinstance(bundle, dict) or not {
        "mediaType",
        "verificationMaterial",
        "dsseEnvelope",
    }.issubset(bundle):
        return _error(
            "ERR_WITNESSD_KEYLESS_BUNDLE_INVALID",
            "sigstore bundle is missing required v0.3 fields",
        )
    return bundle
