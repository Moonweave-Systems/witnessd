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

Runtime is stdlib-only; the provenance record is produced by Depone's own
`build_signed_trusted_observer_provenance` (signing shells out to openssl), so
witnessd emits the artifact rather than reconstructing the binding by hand.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from depone.agent_fabric.observer_provenance import (
    build_signed_trusted_observer_provenance,
)

from witnessd.canonical import canonical_hash
from witnessd.capture import build_capture_manifest
from witnessd.eventlog import EventLog
from witnessd.observer import build_observer_capture
from witnessd.signing import DEFAULT_OPERATOR_KEY_ID
from witnessd.substrate import build_bundle, build_evidence_contract

TRUSTED_PUBLIC_KEY_ENV = "DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE"
RUNLOG_NAME = "runlog.jsonl"


class EmitterError(Exception):
    pass


def _is_inside_or_equal(path: str, root: str) -> bool:
    norm_path = os.path.normcase(os.path.abspath(path))
    norm_root = os.path.normcase(os.path.abspath(root))
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
) -> dict[str, Any]:
    """Assemble and emit a lane's full evidence set through the runlog SoT.

    Returns the built artifacts plus the ordered runlog events. Fails closed —
    writing nothing — if `public_key_path` (the trust root) resolves inside
    `evidence_dir`.
    """
    if _is_inside_or_equal(public_key_path, evidence_dir):
        raise EmitterError("ERR_TRUST_ROOT_NOT_SEPARATED")

    os.makedirs(evidence_dir, exist_ok=True)
    os.environ[TRUSTED_PUBLIC_KEY_ENV] = os.path.abspath(public_key_path)

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
            }
        )
    )

    def _emit_artifact(name: str, content: str) -> str:
        path = os.path.join(evidence_dir, name)
        data = content.encode("utf-8")
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

    _emit_artifact("verify.log", _transcript(lane_result))

    from witnessd.receipt import build_runner_receipt

    receipt = build_runner_receipt(
        task_id=task_id,
        worktree=runner_sandbox,
        invocation=invocation,
        transcript_path=os.path.abspath(transcript_path),
        exit_code=exit_code,
        touched_files=lane_result["touched_files"],
        started_at=started_at or _now_iso(),
        ended_at=ended_at or _now_iso(),
        runner_kind=runner_kind or "manual",
    )

    manifest_path = _emit_artifact("capture-manifest.json", json.dumps(manifest))
    _emit_artifact("observer-capture.json", json.dumps(manifest["observer_capture"]))
    _emit_artifact("runner-receipt.json", json.dumps(receipt))

    artifacts = {
        "capture-manifest": manifest_path,
        "observer-capture": os.path.join(evidence_dir, "observer-capture.json"),
        "runner-receipt": os.path.join(evidence_dir, "runner-receipt.json"),
    }
    otel_spans = None
    if runner_kind is not None:
        from depone.agent_fabric.evidence_substrate import build_otel_genai_spans

        otel_spans = build_otel_genai_spans(manifest, runner_receipt=receipt)
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
    )
    for name, content in contract_files.items():
        _emit_artifact(name, content)

    provenance = build_signed_trusted_observer_provenance(
        manifest,
        evidence_path=manifest_path,
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
) -> dict[str, Any]:
    """Emit supervised-lane evidence with per-spawn isolation facts.

    Depone owns boundary verification. witnessd only probes facts and passes
    them into the existing W1 evidence path when they establish A2; otherwise
    the manifest remains A1.
    """
    from depone.agent_fabric.isolation import verify_isolation_boundary

    from witnessd.isolation import probe_lane_isolation

    facts = probe_lane_isolation(observer_dir=observer_dir, runner_uid=runner_uid)
    isolation = facts if verify_isolation_boundary(facts).get("boundary") is True else None
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
    )


def _self_test() -> None:
    import shutil
    import tempfile

    if shutil.which("openssl") is None:
        print("witnessd emitter --self-test: pass (openssl unavailable)")
        return

    from depone.agent_fabric.observer_provenance import (
        validate_trusted_observer_provenance,
    )

    from witnessd.adapters.shell import run_shell_lane
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
        errors = validate_trusted_observer_provenance(
            result["manifest"],
            evidence_path=result["manifest_path"],
            provenance=[result["provenance"]],
            public_key_path=pub,
        )
        if errors:
            raise AssertionError(f"trusted provenance must validate: {errors}")
    print("witnessd emitter --self-test: pass")


if __name__ == "__main__":
    _self_test()
