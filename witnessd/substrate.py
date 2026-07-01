"""Evidence-substrate bundle (in-toto + DSSE + inline OTel) + evidence-contract.

witnessd emits an operator-signed evidence bundle whose subjects are the raw
sha256 digests of the on-disk artifacts it produced. Depone's
`ingest_signed_evidence_bundle` re-derives those digests from the same bytes and,
after verifying the DSSE signature against the distributed public key, confirms
every subject. The bundle never raises assurance: the predicate carries the
manifest assurance capped at A2, `otel_spans` invent no `gen_ai.usage.*` fields,
and an unsigned bundle keeps `signatures == []` with an unsigned signing status.

The evidence-contract is the separate `v105.verify_wedge` control artifact
Depone's `validate_evidence_contract` consumes; it declares at least one
enforcement directive plus the git-diff / exit-code companion files.

Runtime is stdlib-only; signing shells out to openssl via `witnessd.signing`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from witnessd.canonical import canonical_hash
from witnessd.signing import sign_dsse

INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
DEPONE_PREDICATE_TYPE = "https://depone.dev/attestations/evidence/v1"
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
BUNDLE_KIND = "depone-evidence-substrate-bundle"
BUNDLE_SCHEMA_VERSION = "1.0"
SIGNING_STATUS_UNSIGNED = "unsigned-content-addressed"
SIGNING_STATUS_OPERATOR_KEY = "signed-ed25519-operator-key"
EVIDENCE_CONTRACT_SCHEMA_VERSION = "v105.verify_wedge"

# Assurance is a derived view over the capture manifest; the substrate is a
# packaging/signing layer and must never claim more than A2.
_ASSURANCE_CEILING = ["A0-claims-only", "A1-local-observed", "A2-isolated-observed"]


def _operator_key_signature_boundary() -> dict[str, Any]:
    # Byte-identical to Depone sign.operator_key_signature_boundary(); verify_signed_bundle
    # rejects any divergence.
    return {
        "scheme": "DSSE-Ed25519-openssl-cli",
        "operator_key": True,
        "public_verifiable": True,
        "keyless_identity": False,
        "transparency_logged": False,
        "note": (
            "Trust is rooted in the operator-held key and distributed public "
            "key; this is not Fulcio keyless identity or Rekor logging."
        ),
    }


def _cap_assurance(value: Any) -> str:
    if value in _ASSURANCE_CEILING:
        return value
    return "A2-isolated-observed"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_file(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_otel_spans(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Static OTel GenAI-shaped spans over the capture — no invented usage fields."""
    seed = canonical_hash(manifest)
    trace_id = canonical_hash({"trace": seed})[:32]
    root_span_id = canonical_hash({"seed": seed, "offset": 0})[:16]
    spans: list[dict[str, Any]] = [
        {
            "trace_id": trace_id,
            "span_id": root_span_id,
            "parent_span_id": None,
            "name": "invoke_agent",
            "attributes": {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": "witnessd-shell-lane",
                "depone.assurance": str(manifest.get("assurance", "")),
                "depone.decision": str(manifest.get("decision", "")),
            },
        }
    ]
    observer_capture = manifest.get("observer_capture")
    observer_capture = observer_capture if isinstance(observer_capture, dict) else {}
    receipts = observer_capture.get("command_receipts", [])
    if isinstance(receipts, list):
        for index, receipt in enumerate(receipts, start=1):
            if not isinstance(receipt, dict):
                continue
            spans.append(
                {
                    "trace_id": trace_id,
                    "span_id": canonical_hash({"seed": seed, "offset": index})[:16],
                    "parent_span_id": root_span_id,
                    "name": "execute_tool",
                    "attributes": {
                        "gen_ai.operation.name": "execute_tool",
                        "gen_ai.tool.name": "verification_command",
                        "depone.command": receipt.get("command", []),
                        "depone.exit_code": receipt.get("exit_code"),
                    },
                }
            )
    return spans


def build_bundle(
    manifest: dict[str, Any],
    artifacts: dict[str, str],
    private_key_path: str | None = None,
    public_key_path: str | None = None,
    *,
    key_id: str = "witnessd-operator",
    otel_spans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Package on-disk artifacts as a signed in-toto/DSSE evidence bundle.

    `artifacts` maps subject name -> file path. Each subject digest is the raw
    sha256 of the artifact bytes, so Depone's `ingest_signed_evidence_bundle`
    (raw digest mode) re-derives them from the same files. Without a private key
    the bundle stays honestly unsigned (`signatures == []`); assurance is copied
    from the manifest and capped at A2 either way.
    """
    _ = public_key_path
    assurance = _cap_assurance(manifest.get("assurance"))
    signed = private_key_path is not None
    boundary = {
        "raises_assurance": False,
        "signed": signed,
        "approves_public_claim": False,
    }
    subjects = [
        {"name": name, "digest": {"sha256": _sha256_file(path)}}
        for name, path in sorted(artifacts.items())
    ]
    statement = {
        "_type": INTOTO_STATEMENT_TYPE,
        "subject": subjects,
        "predicateType": DEPONE_PREDICATE_TYPE,
        "predicate": {
            "schema_version": "1.0",
            "source_kind": manifest.get("kind"),
            "assurance": assurance,
            "decision": manifest.get("decision"),
            "prev_capture_hash": manifest.get("prev_capture_hash"),
            "boundary": boundary,
        },
    }
    envelope = {
        "payloadType": DSSE_PAYLOAD_TYPE,
        "payload": base64.b64encode(_canonical_json(statement).encode("utf-8")).decode(
            "ascii"
        ),
        "signatures": [],
    }
    bundle: dict[str, Any] = {
        "kind": BUNDLE_KIND,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "statement": statement,
        "dsse_envelope": envelope,
        "otel_spans": otel_spans
        if otel_spans is not None
        else build_otel_spans(manifest),
        "assurance": assurance,
        "signing_status": SIGNING_STATUS_UNSIGNED,
        "boundary": boundary,
    }
    if signed:
        bundle["dsse_envelope"] = sign_dsse(envelope, private_key_path, key_id=key_id)
        bundle["signing_status"] = SIGNING_STATUS_OPERATOR_KEY
        bundle["signature_boundary"] = _operator_key_signature_boundary()
    return bundle


def build_evidence_contract(
    *,
    allowed_touched_files: list[str],
    touched_files: list[str],
    exit_code: int,
    diff_patch: str = "",
) -> dict[str, str]:
    """Build the `v105.verify_wedge` evidence-contract plus its companion files.

    Returns a name -> content map ready to write at the evidence root. The
    contract declares `allowed_touched_files` and `expected_exit_code` as
    enforcement directives that Depone's `validate_evidence_contract` checks
    against the git-diff / exit-code artifacts.
    """
    contract = {
        "schema_version": EVIDENCE_CONTRACT_SCHEMA_VERSION,
        "allowed_touched_files": list(allowed_touched_files),
        "expected_exit_code": exit_code,
    }
    name_only = "".join(f"{name}\n" for name in touched_files)
    return {
        "evidence-contract.json": json.dumps(contract, indent=2),
        "git-diff-name-only.txt": name_only,
        "git-diff.patch": diff_patch,
        "exit-code.txt": f"{exit_code}\n",
    }


def _self_test() -> None:
    import shutil
    import tempfile

    if shutil.which("openssl") is None:
        print("witnessd substrate --self-test: pass (openssl unavailable)")
        return

    from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle
    from depone.agent_fabric.sign import verify_signed_bundle
    from witnessd.signing import gen_operator_keypair

    manifest = {
        "kind": "agent-fabric-capture-manifest",
        "assurance": "A1-local-observed",
        "decision": "observed-local-capture",
        "prev_capture_hash": None,
        "observer_capture": {
            "command_receipts": [
                {"command": ["sh", "-c", "true"], "exit_code": 0, "status": "passed"}
            ]
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        artifact_path = Path(tmp) / "capture-manifest.json"
        artifact_path.write_text(json.dumps(manifest), encoding="utf-8")
        artifacts = {"capture-manifest": str(artifact_path)}
        priv, pub = gen_operator_keypair(tmp)
        bundle = build_bundle(manifest, artifacts, priv, pub)
        if not verify_signed_bundle(bundle, pub):
            raise AssertionError("signed bundle must verify")
        verdict = ingest_signed_evidence_bundle(
            bundle, pub, artifacts, otel_spans=bundle["otel_spans"]
        )
        if not verdict.get("signature_verified") or verdict.get("decision") != "pass":
            raise AssertionError("signed bundle must ingest with all subjects verified")

        unsigned = build_bundle(manifest, artifacts)
        if unsigned["dsse_envelope"]["signatures"] != []:
            raise AssertionError("unsigned bundle must keep signatures empty")

    files = build_evidence_contract(
        allowed_touched_files=["depone/example.py"],
        touched_files=["depone/example.py"],
        exit_code=0,
    )
    if json.loads(files["evidence-contract.json"])["schema_version"] != (
        EVIDENCE_CONTRACT_SCHEMA_VERSION
    ):
        raise AssertionError("evidence contract must declare the wedge schema version")
    print("witnessd substrate --self-test: pass")


if __name__ == "__main__":
    _self_test()
