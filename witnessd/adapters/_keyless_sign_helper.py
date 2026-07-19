"""Standalone Sigstore-library helper for keyless evidence anchoring.

This module is executed only as a subprocess by ``sigstore_keyless.py``.  It is
deliberately not imported by witnessd runtime modules so the core package keeps
Sigstore as an optional external tool dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn

PREDICATE_TYPE = "https://moonweave.dev/witnessd/keyless-evidence-anchor/v1"
PREDICATE = {
    "kind": "witnessd-keyless-emission",
    "schema_version": "1.0",
    "raises_assurance": False,
}
_REQUIRED_BUNDLE_FIELDS = {"mediaType", "verificationMaterial", "dsseEnvelope"}
_MAX_STDIN_BYTES = 1024 * 1024


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--staging", action="store_true")
    parser.add_argument("--identity-token-stdin", action="store_true")
    parser.add_argument("--oauth-force-oob", action="store_true")
    parser.add_argument("--oidc-disable-ambient-providers", action="store_true")
    return parser


def _identity_token_from_stdin() -> str:
    raw = sys.stdin.read(_MAX_STDIN_BYTES + 1)
    if len(raw) > _MAX_STDIN_BYTES:
        raise ValueError("identity-token request exceeds size limit")
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError("identity-token request must be a JSON object")
    token = request.get("identity_token")
    if not isinstance(token, str) or not token:
        raise ValueError("identity-token request is missing identity_token")
    return token


def _write_bundle_atomically(path: Path, bundle_json: str) -> None:
    parsed = json.loads(bundle_json)
    if not isinstance(parsed, dict) or not _REQUIRED_BUNDLE_FIELDS.issubset(parsed):
        raise ValueError("Sigstore bundle is missing required v0.3 fields")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(bundle_json)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _sign(args: argparse.Namespace) -> None:
    from sigstore.dsse import DigestSet, StatementBuilder, Subject
    from sigstore.models import ClientTrustConfig
    from sigstore.oidc import IdentityToken, Issuer
    from sigstore.sign import SigningContext

    if not args.oidc_disable_ambient_providers:
        raise ValueError("ambient OIDC providers must remain disabled")

    evidence = args.evidence.read_bytes()
    digest = hashlib.sha256(evidence).hexdigest()
    subject = Subject(
        name=args.evidence.name,
        digest=DigestSet(root={"sha256": digest}),
    )
    statement = (
        StatementBuilder(subjects=[subject])
        .predicate_type(PREDICATE_TYPE)
        .predicate(PREDICATE)
        .build()
    )

    trust_config = (
        ClientTrustConfig.staging() if args.staging else ClientTrustConfig.production()
    )
    # Pin Rekor v1 (dsse 0.0.1 + signed entry timestamp) so Depone can re-derive
    # the anchor offline. Rekor v2 (hashedrekord + RFC3161 timestamp + witnessed
    # checkpoints) verification is a tracked follow-up; production public-good
    # Rekor still defaults to v1, and this makes staging match.
    trust_config.force_tlog_version = 1
    signing_context = SigningContext.from_trust_config(trust_config)
    if args.identity_token_stdin:
        token = IdentityToken(_identity_token_from_stdin())
    else:
        issuer = Issuer(trust_config.signing_config.get_oidc_url())
        token = issuer.identity_token(force_oob=args.oauth_force_oob)

    with signing_context.signer(token) as signer:
        bundle = signer.sign_dsse(statement)
    _write_bundle_atomically(args.bundle, bundle.to_json())


def _structured_error(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "ERR_KEYLESS_HELPER_FAILED",
            "message": "keyless signing helper failed",
            "detail": type(exc).__name__,
        },
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        _sign(args)
    except Exception as exc:
        print(json.dumps(_structured_error(exc), sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
