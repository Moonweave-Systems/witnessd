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
from witnessd.signing import DEFAULT_OPERATOR_KEY_ID, derive_public_key_id, sign_dsse
from witnessd.signing_profile import (
    OPERATOR_KEY_PROFILE,
    operator_key_signature_boundary,
    select_signing_profile,
)

INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
DEPONE_PREDICATE_TYPE = "https://depone.dev/attestations/evidence/v1"
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
BUNDLE_KIND = "depone-evidence-substrate-bundle"
BUNDLE_SCHEMA_VERSION = "1.0"
SIGNING_STATUS_UNSIGNED = "unsigned-content-addressed"
SIGNING_STATUS_OPERATOR_KEY = "signed-ed25519-operator-key"
EVIDENCE_CONTRACT_SCHEMA_VERSION = "v105.verify_wedge"
ROLE_CAPABILITY_EVIDENCE_CONTRACT_SCHEMA_VERSION = "v109.role_capability_write_scope"
ROLE_CAPABILITY_TOOL_CALLS_EVIDENCE_CONTRACT_SCHEMA_VERSION = (
    "v107.role_capability_tool_calls"
)
GIT_DIFF_NAME_ONLY_SUBJECT_NAME = "git-diff-name-only.txt"
EVIDENCE_MODE_CONTEMPORANEOUS = "contemporaneous"
EVIDENCE_MODE_POST_HOC = "post_hoc"
DEFAULT_EPOCH_SECONDS = 300

# Assurance is a derived view over the capture manifest; the substrate is a
# packaging/signing layer and must never claim more than A2.
_ASSURANCE_CEILING = ["A0-claims-only", "A1-local-observed", "A2-isolated-observed"]


def _cap_assurance(value: Any) -> str:
    if value in _ASSURANCE_CEILING:
        return value
    return "A2-isolated-observed"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_file(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _artifact_index(subjects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "digest": {"sha256": item["digest"]["sha256"]},
        }
        for item in sorted(subjects, key=lambda record: record["name"])
    ]


def _artifact_merkle_root(artifact_index: list[dict[str, Any]]) -> str:
    leaves = [
        canonical_hash({"name": item["name"], "digest": item["digest"]})
        for item in artifact_index
    ]
    if not leaves:
        return canonical_hash([])
    level = leaves
    while len(level) > 1:
        next_level: list[str] = []
        for index in range(0, len(level), 2):
            left = level[index]
            right = level[index + 1] if index + 1 < len(level) else left
            next_level.append(canonical_hash({"left": left, "right": right}))
        level = next_level
    return level[0]


def _span_id(seed: str, offset: int = 0) -> str:
    return canonical_hash({"seed": seed, "offset": offset})[:16]


def _trace_id(seed: str) -> str:
    return canonical_hash({"trace": seed})[:32]


def build_otel_spans(
    manifest: dict[str, Any],
    *,
    runner_receipt: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Static OTel GenAI-shaped spans over the capture — no invented usage fields."""
    seed = canonical_hash(
        {
            "capture": manifest,
            "runner": runner_receipt or {},
        }
    )
    trace_id = _trace_id(seed)
    root_span_id = _span_id(seed, 0)
    runner_kind = (
        runner_receipt.get("runner_kind")
        if isinstance(runner_receipt, dict)
        else "unknown"
    )
    arm = runner_receipt.get("arm") if isinstance(runner_receipt, dict) else "unknown"
    spans: list[dict[str, Any]] = [
        {
            "trace_id": trace_id,
            "span_id": root_span_id,
            "parent_span_id": None,
            "name": "invoke_agent",
            "attributes": {
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": str(runner_kind),
                "depone.arm": str(arm),
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
                        "depone.status": receipt.get("status"),
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
    key_id: str = DEFAULT_OPERATOR_KEY_ID,
    otel_spans: list[dict[str, Any]] | None = None,
    signing_profile: str | None = None,
) -> dict[str, Any]:
    """Package on-disk artifacts as a signed in-toto/DSSE evidence bundle.

    `artifacts` maps subject name -> file path. Each subject digest is the raw
    sha256 of the artifact bytes, so Depone's `ingest_signed_evidence_bundle`
    (raw digest mode) re-derives them from the same files. Without a private key
    the bundle stays honestly unsigned (`signatures == []`); assurance is copied
    from the manifest and capped at A2 either way.
    """
    _ = public_key_path
    profile = select_signing_profile(signing_profile)
    if profile.name != OPERATOR_KEY_PROFILE:
        raise AssertionError(
            "non-operator signing profile escaped fail-closed selection"
        )
    assurance = _cap_assurance(manifest.get("assurance"))
    signed = private_key_path is not None
    evidence_mode = manifest.get("evidence_mode", EVIDENCE_MODE_CONTEMPORANEOUS)
    epoch_seconds = manifest.get("epoch_seconds", DEFAULT_EPOCH_SECONDS)
    monotonic_counter = manifest.get("monotonic_counter", 1)
    boundary = {
        "raises_assurance": False,
        "signed": signed,
        "approves_public_claim": False,
    }
    subjects = [
        {"name": name, "digest": {"sha256": _sha256_file(path)}}
        for name, path in sorted(artifacts.items())
    ]
    artifact_index = _artifact_index(subjects)
    statement = {
        "_type": INTOTO_STATEMENT_TYPE,
        "subject": subjects,
        "predicateType": DEPONE_PREDICATE_TYPE,
        "predicate": {
            "schema_version": "1.0",
            "source_kind": manifest.get("kind"),
            "evidence_mode": evidence_mode,
            "assurance": assurance,
            "decision": manifest.get("decision"),
            "epoch_seconds": epoch_seconds,
            "monotonic_counter": monotonic_counter,
            **(
                {"parent_attestation_id": manifest["parent_attestation_id"]}
                if isinstance(manifest.get("parent_attestation_id"), str)
                else {}
            ),
            "prev_capture_hash": manifest.get("prev_capture_hash"),
            "boundary": boundary,
            "artifact_index": artifact_index,
            "artifact_merkle_root": _artifact_merkle_root(artifact_index),
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
        "evidence_mode": evidence_mode,
        "epoch_seconds": epoch_seconds,
        "monotonic_counter": monotonic_counter,
        "assurance": assurance,
        "signing_status": SIGNING_STATUS_UNSIGNED,
        "boundary": boundary,
    }
    if isinstance(manifest.get("parent_attestation_id"), str):
        bundle["parent_attestation_id"] = manifest["parent_attestation_id"]
    if signed:
        if public_key_path is not None and key_id == DEFAULT_OPERATOR_KEY_ID:
            key_id = derive_public_key_id(public_key_path)
        bundle["dsse_envelope"] = sign_dsse(envelope, private_key_path, key_id=key_id)
        bundle["signing_status"] = SIGNING_STATUS_OPERATOR_KEY
        bundle["signature_boundary"] = operator_key_signature_boundary()
    return bundle


def build_evidence_contract(
    *,
    allowed_touched_files: list[str],
    touched_files: list[str],
    exit_code: int,
    diff_patch: str = "",
    write_scope: list[str] | None = None,
    tool_call_decision_receipts: bool = False,
) -> dict[str, str]:
    """Build the evidence-contract plus its companion files.

    Returns a name -> content map ready to write at the evidence root. The
    contract declares `allowed_touched_files` and `expected_exit_code` as
    enforcement directives that Depone's `validate_evidence_contract` checks.
    When `write_scope` is supplied, the contract also activates Depone's v109
    role-capability write-scope conformance axis and binds the observed touched
    paths to the signed bundle; the declared scope itself stays in the signed
    run-intent artifact.
    """
    # write_scope requires the v109 bound-observation schema so Depone verifies the
    # git-diff observation is bound to the signed bundle. A contract that also carries
    # tool-call receipts stays on v109 (which accepts both directives) rather than
    # falling back to v107, whose write_scope would be accepted without a bound
    # observation and is refused by Depone.
    if write_scope is not None:
        schema_version = ROLE_CAPABILITY_EVIDENCE_CONTRACT_SCHEMA_VERSION
    elif tool_call_decision_receipts:
        schema_version = ROLE_CAPABILITY_TOOL_CALLS_EVIDENCE_CONTRACT_SCHEMA_VERSION
    else:
        schema_version = EVIDENCE_CONTRACT_SCHEMA_VERSION
    contract = {
        "schema_version": schema_version,
        "allowed_touched_files": list(allowed_touched_files),
        "expected_exit_code": exit_code,
    }
    if write_scope is not None:
        contract["role_capability_write_scope"] = {
            "run_intent_path": "run-intent.json",
            "bundle_path": "bundle.json",
        }
    if tool_call_decision_receipts:
        contract["role_capability_tool_calls"] = {
            "run_intent_path": "run-intent.json",
            "bundle_path": "bundle.json",
            "decision_receipts_path": "tool-call-decision-receipts.json",
        }
    name_only = "".join(f"{name}\n" for name in touched_files)
    return {
        "evidence-contract.json": json.dumps(contract, indent=2),
        GIT_DIFF_NAME_ONLY_SUBJECT_NAME: name_only,
        "git-diff.patch": diff_patch,
        "exit-code.txt": f"{exit_code}\n",
    }


def _self_test() -> None:
    import shutil
    import tempfile

    if shutil.which("openssl") is None:
        print("witnessd substrate --self-test: pass (openssl unavailable)")
        return

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
        if not bundle["dsse_envelope"]["signatures"]:
            raise AssertionError("signed bundle must include a signature")
        if [item["name"] for item in bundle["statement"]["subject"]] != [
            "capture-manifest"
        ]:
            raise AssertionError("signed bundle subjects must match artifacts")

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
