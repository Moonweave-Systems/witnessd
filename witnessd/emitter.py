"""Evidence Emitter — the sole writer to the run source-of-truth (E6).

Every artifact witnessd emits for a lane (capture-manifest, observer-capture,
runner-receipt, signed evidence bundle, evidence-contract companions, the signed
trusted-observer-provenance record, and the run transcript) is written through
one path: `_emit_artifact`, which appends a hash-chained runlog event
(EventLog, the append-only SoT) for the exact bytes it just wrote. Worker and
observer code have no other route to the evidence dir, so run-state is always a
projection of the signed event stream, never a side-written file.

The trusted-observer public key is the trust root and is kept OUT of the
evidence dir (the runner-reachable surface); the emitter fails closed if asked
to root trust inside it, and exports the out-of-band location via
`DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE` exactly as Depone reads it.

Runtime is stdlib-only; the provenance record is produced by witnessd's local
emit-side copy of Depone's provenance contract.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from witnessd.canonical import canonical_hash
from witnessd.capture import build_capture_manifest
from witnessd.eventlog import EventLog
from witnessd.observer import build_observer_capture
from witnessd.privacy import (
    CAPTURE_PROFILE_REDACTED,
    REDACTION_MANIFEST_ARTIFACT_NAME,
    REDACTION_MANIFEST_SUBJECT_NAME,
)
from witnessd.provenance import build_signed_trusted_observer_provenance
from witnessd.runintent import (
    RUN_INTENT_ARTIFACT_NAME,
    RUN_INTENT_SUBJECT_NAME,
    build_role_capability_intent,
    build_run_intent,
    git_baseline,
    write_signed_run_intent,
)
from witnessd.signing import DEFAULT_OPERATOR_KEY_ID, derive_public_key_id
from witnessd.substrate import build_bundle, build_evidence_contract, build_otel_spans

TRUSTED_PUBLIC_KEY_ENV = "DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE"
RUNLOG_NAME = "runlog.jsonl"


class EmitterError(Exception):
    pass


def _is_inside_or_equal(path: str, root: str) -> bool:
    norm_path = os.path.normcase(os.path.realpath(path))
    norm_root = os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([norm_path, norm_root]) == norm_root
    except ValueError:
        return False


def _lane_exit_code(lane_result: dict[str, Any]) -> int:
    for receipt in lane_result.get("command_receipts", []):
        code = receipt.get("exit_code")
        if isinstance(code, int) and code != 0:
            return code
    return 0


def _transcript(lane_result: dict[str, Any]) -> str:
    lines: list[str] = []
    for receipt in lane_result.get("command_receipts", []):
        command = receipt.get("command", [])
        lines.append(f"$ {' '.join(str(part) for part in command)}")
        lines.append(f"exit={receipt.get('exit_code')}")
        stdout = receipt.get("stdout", "")
        stderr = receipt.get("stderr", "")
        if stdout:
            lines.append(stdout.rstrip("\n"))
        if stderr:
            lines.append(stderr.rstrip("\n"))
    return "\n".join(lines) + "\n"


def _portable_path(path: str, root: str) -> str:
    relative = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    return relative.replace(os.sep, "/")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_lane_evidence(
    lane_result: dict[str, Any],
    evidence_dir: str,
    private_key_path: str,
    *,
    fixture: dict[str, Any],
    allowed_touched_files: list[str],
    public_key_path: str,
    task_id: str = "witnessd-lane",
    invocation: list[str] | None = None,
    runner_sandbox: str = "",
    prev_capture_hash: str | None = None,
    isolation: dict[str, Any] | None = None,
    runner_kind: str | None = None,
    key_id: str = DEFAULT_OPERATOR_KEY_ID,
    started_at: str | None = None,
    ended_at: str | None = None,
    diff_patch: str = "",
    evidence_mode: str = "contemporaneous",
    epoch_seconds: int = 300,
    monotonic_counter: int = 1,
    parent_attestation_id: str | None = None,
    run_intent_path: str | None = None,
    run_intent: dict[str, Any] | None = None,
    capture_profile: str = "full",
    redaction_manifest: dict[str, Any] | None = None,
    provider_artifacts: dict[str, str] | None = None,
    write_scope: list[str] | None = None,
    role_id: str | None = None,
    role_capability: str | None = None,
) -> dict[str, Any]:
    """Assemble and emit a lane's full evidence set through the runlog SoT.

    Returns the built artifacts plus the ordered runlog events. Fails closed —
    writing nothing — if `public_key_path` (the trust root) resolves inside
    `evidence_dir`.
    """
    if _is_inside_or_equal(public_key_path, evidence_dir):
        raise EmitterError("ERR_TRUST_ROOT_NOT_SEPARATED")
    if key_id == DEFAULT_OPERATOR_KEY_ID:
        key_id = derive_public_key_id(public_key_path)

    os.makedirs(evidence_dir, exist_ok=True)
    os.environ[TRUSTED_PUBLIC_KEY_ENV] = os.path.abspath(public_key_path)
    if run_intent_path is None:
        intent = run_intent or build_run_intent(
            run_id=task_id,
            baseline=git_baseline(runner_sandbox) if runner_sandbox else {},
            allowed_paths=allowed_touched_files,
            approval_policy="unknown",
            sandbox_mode="unknown",
            provider=runner_kind or "manual",
            instruction_hashes={},
            budgets={},
            capture_profile=capture_profile,
            role_capability=(
                build_role_capability_intent(
                    role_id=role_id or task_id,
                    capability=role_capability or "execute",
                    declared_write_scope=list(write_scope),
                )
                if write_scope is not None
                else None
            ),
        )
        run_intent_path = os.path.join(evidence_dir, RUN_INTENT_ARTIFACT_NAME)
        write_signed_run_intent(
            run_intent_path, intent, private_key_path, key_id=key_id
        )

    source_fixture_hash = canonical_hash(fixture)
    observer_capture = build_observer_capture(
        command_receipts=lane_result["command_receipts"],
        touched_files=lane_result["touched_files"],
        allowed_touched_files=allowed_touched_files,
        test_output=lane_result["test_output"],
        source_fixture_hash=source_fixture_hash,
    )
    manifest = build_capture_manifest(
        fixture,
        observer_capture=observer_capture,
        allowed_touched_files=allowed_touched_files,
        prev_capture_hash=prev_capture_hash,
        isolation=isolation,
        evidence_mode=evidence_mode,
        epoch_seconds=epoch_seconds,
        monotonic_counter=monotonic_counter,
        parent_attestation_id=parent_attestation_id,
    )

    exit_code = _lane_exit_code(lane_result)
    receipts = lane_result.get("command_receipts", [])
    if invocation is None:
        invocation = list(receipts[0]["command"]) if receipts else ["sh", "-c", "true"]
    transcript_path = os.path.join(evidence_dir, "verify.log")
    receipt = None  # built after transcript path is known

    log = EventLog(os.path.join(evidence_dir, RUNLOG_NAME))
    events: list[dict[str, Any]] = []

    events.append(
        log.append(
            {
                "kind": "witnessd-runlog-event",
                "event": "lane-observed",
                "task_id": task_id,
                "source_fixture_hash": source_fixture_hash,
                "assurance": manifest["assurance"],
                "evidence_mode": manifest["evidence_mode"],
                "epoch_seconds": manifest["epoch_seconds"],
                "monotonic_counter": manifest["monotonic_counter"],
            }
        )
    )

    def _emit_artifact_bytes(name: str, data: bytes) -> str:
        path = os.path.join(evidence_dir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        events.append(
            log.append(
                {
                    "kind": "witnessd-runlog-event",
                    "event": "emit-artifact",
                    "artifact": name,
                    "path": os.path.abspath(path),
                    "content_sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        )
        return path

    def _emit_artifact(name: str, content: str) -> str:
        return _emit_artifact_bytes(name, content.encode("utf-8"))

    def _record_existing_artifact(name: str, path: str) -> str:
        with open(path, "rb") as handle:
            data = handle.read()
        events.append(
            log.append(
                {
                    "kind": "witnessd-runlog-event",
                    "event": "emit-artifact",
                    "artifact": name,
                    "path": os.path.abspath(path),
                    "content_sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        )
        return path

    recorded_run_intent_path = _record_existing_artifact(
        RUN_INTENT_ARTIFACT_NAME,
        run_intent_path,
    )
    _emit_artifact("verify.log", _transcript(lane_result))

    from witnessd.receipt import build_runner_receipt

    receipt = build_runner_receipt(
        task_id=task_id,
        worktree=runner_sandbox,
        invocation=invocation,
        transcript_path=_portable_path(transcript_path, os.path.dirname(evidence_dir)),
        exit_code=exit_code,
        touched_files=lane_result["touched_files"],
        started_at=started_at or _now_iso(),
        ended_at=ended_at or _now_iso(),
        runner_kind=runner_kind or "manual",
    )

    manifest_path = _emit_artifact("capture-manifest.json", json.dumps(manifest))
    _emit_artifact("observer-capture.json", json.dumps(manifest["observer_capture"]))
    _emit_artifact("runner-receipt.json", json.dumps(receipt))
    redaction_manifest_path = None
    if capture_profile == CAPTURE_PROFILE_REDACTED and redaction_manifest is not None:
        redaction_manifest_path = _emit_artifact(
            REDACTION_MANIFEST_ARTIFACT_NAME,
            json.dumps(redaction_manifest),
        )

    artifacts = {
        "capture-manifest": manifest_path,
        "observer-capture": os.path.join(evidence_dir, "observer-capture.json"),
        "runner-receipt": os.path.join(evidence_dir, "runner-receipt.json"),
        RUN_INTENT_SUBJECT_NAME: recorded_run_intent_path,
    }
    if redaction_manifest_path is not None:
        artifacts[REDACTION_MANIFEST_SUBJECT_NAME] = redaction_manifest_path
    if provider_artifacts:
        for subject_name, source_path in sorted(provider_artifacts.items()):
            source = os.path.abspath(source_path)
            artifact_name = (
                "review-receipt.json"
                if subject_name == "review-receipt"
                else "model-declaration.json"
                if subject_name == "model-declaration"
                else "write-scope-declaration.json"
                if subject_name == "write-scope-declaration"
                else "tool-declaration.json"
                if subject_name == "tool-declaration"
                else f"{subject_name}.jsonl"
            )
            with open(source, "rb") as handle:
                artifacts[subject_name] = _emit_artifact_bytes(
                    artifact_name, handle.read()
                )
    otel_spans = None
    if runner_kind is not None:
        otel_spans = build_otel_spans(manifest, runner_receipt=receipt)
    bundle = build_bundle(
        manifest,
        artifacts,
        private_key_path,
        public_key_path,
        key_id=key_id,
        otel_spans=otel_spans,
    )
    _emit_artifact("bundle.json", json.dumps(bundle))

    contract_files = build_evidence_contract(
        allowed_touched_files=allowed_touched_files,
        touched_files=lane_result["touched_files"],
        exit_code=exit_code,
        diff_patch=diff_patch,
        write_scope=write_scope,
    )
    for name, content in contract_files.items():
        _emit_artifact(name, content)

    provenance = build_signed_trusted_observer_provenance(
        manifest,
        evidence_path=_portable_path(manifest_path, evidence_dir),
        private_key_path=private_key_path,
        key_id=key_id,
    )
    _emit_artifact("provenance.json", json.dumps(provenance))

    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "receipt": receipt,
        "bundle": bundle,
        "provenance": provenance,
        "artifacts": artifacts,
        "runlog": events,
        "public_key_path": os.path.abspath(public_key_path),
        "assurance": manifest["assurance"],
    }


def emit_supervised_lane(
    lane_result: dict[str, Any],
    evidence_dir: str,
    private_key_path: str,
    *,
    fixture: dict[str, Any],
    allowed_touched_files: list[str],
    public_key_path: str,
    observer_dir: str,
    runner_uid: int | None,
    task_id: str = "witnessd-supervised-lane",
    invocation: list[str] | None = None,
    runner_sandbox: str = "",
    prev_capture_hash: str | None = None,
    runner_kind: str | None = None,
    key_id: str = DEFAULT_OPERATOR_KEY_ID,
    started_at: str | None = None,
    ended_at: str | None = None,
    diff_patch: str = "",
    evidence_mode: str = "contemporaneous",
    epoch_seconds: int = 300,
    monotonic_counter: int = 1,
    parent_attestation_id: str | None = None,
    isolation_model: str | None = None,
    observer_launched: bool = False,
    run_intent_path: str | None = None,
    run_intent: dict[str, Any] | None = None,
    capture_profile: str = "full",
    redaction_manifest: dict[str, Any] | None = None,
    provider_artifacts: dict[str, str] | None = None,
    write_scope: list[str] | None = None,
    role_id: str | None = None,
    role_capability: str | None = None,
) -> dict[str, Any]:
    """Emit supervised-lane evidence with per-spawn isolation facts.

    witnessd probes facts and passes them into the existing W1 evidence path
    when they establish A2; otherwise the manifest remains A1.
    """
    from witnessd.isolation import (
        ISOLATION_MODEL,
        probe_lane_isolation,
        verify_isolation_boundary,
    )

    facts = probe_lane_isolation(
        observer_dir=observer_dir,
        runner_uid=runner_uid,
        model=isolation_model or ISOLATION_MODEL,
        observer_launched=observer_launched,
    )
    isolation = (
        facts if verify_isolation_boundary(facts).get("boundary") is True else None
    )
    return emit_lane_evidence(
        lane_result,
        evidence_dir,
        private_key_path,
        fixture=fixture,
        allowed_touched_files=allowed_touched_files,
        public_key_path=public_key_path,
        task_id=task_id,
        invocation=invocation,
        runner_sandbox=runner_sandbox,
        prev_capture_hash=prev_capture_hash,
        isolation=isolation,
        runner_kind=runner_kind,
        key_id=key_id,
        started_at=started_at,
        ended_at=ended_at,
        diff_patch=diff_patch,
        evidence_mode=evidence_mode,
        epoch_seconds=epoch_seconds,
        monotonic_counter=monotonic_counter,
        parent_attestation_id=parent_attestation_id,
        run_intent_path=run_intent_path,
        run_intent=run_intent,
        capture_profile=capture_profile,
        redaction_manifest=redaction_manifest,
        provider_artifacts=provider_artifacts,
        write_scope=write_scope,
        role_id=role_id,
        role_capability=role_capability,
    )


def _self_test() -> None:
    import shutil
    import tempfile

    if shutil.which("openssl") is None:
        print("witnessd emitter --self-test: pass (openssl unavailable)")
        return

    from witnessd.adapters.shell import run_shell_lane
    from witnessd.provenance import PROVENANCE_KIND
    from witnessd.signing import gen_operator_keypair

    with tempfile.TemporaryDirectory() as tmp:
        sandbox = os.path.join(tmp, "sandbox")
        evidence_dir = os.path.join(tmp, "evidence")
        keydir = os.path.join(tmp, "keys")
        os.makedirs(sandbox)
        os.makedirs(keydir)
        priv, pub = gen_operator_keypair(keydir)
        lane = run_shell_lane(sandbox=sandbox, commands=[["sh", "-c", "true"]])
        result = emit_lane_evidence(
            lane,
            evidence_dir,
            priv,
            fixture={"kind": "witnessd-self-test-fixture"},
            allowed_touched_files=[],
            public_key_path=pub,
            runner_sandbox=sandbox,
        )
        if result["provenance"].get("kind") != PROVENANCE_KIND:
            raise AssertionError("trusted provenance kind mismatch")
        if result["provenance"].get("manifest_hash") != canonical_hash(
            result["manifest"]
        ):
            raise AssertionError("trusted provenance manifest_hash mismatch")
    print("witnessd emitter --self-test: pass")


if __name__ == "__main__":
    _self_test()
