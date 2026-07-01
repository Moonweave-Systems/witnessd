"""Generate the committed W1 evidence fixtures under ``fixtures/w1/``.

A1 is produced natively by witnessd's Evidence Emitter (the sole SoT writer):
observer/runner separation holds, a real shell lane runs, and the emitter emits
the capture-manifest, observer-capture, runner-receipt, signed evidence bundle,
and evidence-contract companions. The trusted-observer-provenance is re-bound to
a stable, repo-relative evidence path so a checkout can re-derive it.

A2 is a DEMONSTRATION only: this host runs the lane in-process as the observer
uid, so there is no uid-isolated runner to observe. The A2 manifest records real
isolation facts probed via Depone's ``probe_isolation_facts`` against a
mode-0700 observer dir with a distinct runner uid, which the isolation gate
accepts as a boundary — it demonstrates the A2 path without claiming a real
isolated run. ``scripts/revalidate_w1.py`` treats the strict A2 assert as
uid-host-conditional.

Only public material is committed; the operator private key never leaves the
throwaway temp dir. Run with:

    PYTHONPATH=/home/ubuntu/depone-assurance-repair uv run python3 scripts/gen_w1_fixtures.py
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from depone.agent_fabric.isolation import probe_isolation_facts
from depone.agent_fabric.observer_provenance import (
    build_signed_trusted_observer_provenance,
)
from depone.agent_fabric.reference_adapter import build_reference_adapter_fixture

from witnessd.adapters.shell import run_shell_lane
from witnessd.canonical import canonical_hash
from witnessd.capture import build_capture_manifest
from witnessd.emitter import emit_lane_evidence
from witnessd.observer import build_observer_capture
from witnessd.signing import gen_operator_keypair

REPO_ROOT = Path(__file__).resolve().parent.parent
FIX = REPO_ROOT / "fixtures" / "w1"
PROVENANCE_EVIDENCE_PATH = "fixtures/w1/capture-manifest.json"
A2_DEMONSTRATION_RUNNER_UID = 65534  # real distinct non-root uid (nobody)

A1_ARTIFACTS = (
    "capture-manifest.json",
    "observer-capture.json",
    "runner-receipt.json",
    "bundle.json",
    "evidence-contract.json",
    "git-diff-name-only.txt",
    "git-diff.patch",
    "exit-code.txt",
)


def _invocation(profile: str) -> dict:
    return {
        "packet_version": "1.0",
        "target_harness": "shell",
        "profile": profile,
        "role": "runner",
        "toolbelt": {
            "allowed_tools": ["cat", "python3"],
            "allowed_mcp": [],
            "forbidden_tools": ["write"],
            "context_policy": "local-code-only",
            "output_schema": "runner-result-v1",
            "evidence_obligations": ["command_receipt"],
        },
        "instructions": "Run checks and report outputs.",
        "evidence_obligations": ["command_receipt"],
        "context_policy": "local-code-only",
    }


def _write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


def _chain_manifest(profile: str, prev: str | None) -> dict:
    observer_capture = build_observer_capture(
        command_receipts=[{"command": ["sh", "-c", "true"], "exit_code": 0}],
        touched_files=["depone/example.py"],
        allowed_touched_files=["depone/example.py"],
        test_output={"status": "passed", "summary": "1 passed"},
    )
    return build_capture_manifest(
        build_reference_adapter_fixture(_invocation(profile)),
        observer_capture=observer_capture,
        allowed_touched_files=["depone/example.py"],
        prev_capture_hash=prev,
    )


def _a2_demonstration_manifest(work: Path) -> dict:
    observer_dir = work / "observer-a2"
    observer_dir.mkdir()
    os.chmod(observer_dir, stat.S_IRWXU)  # 0700: not writable by a different uid
    facts = probe_isolation_facts(observer_dir, runner_uid=A2_DEMONSTRATION_RUNNER_UID)
    observer_capture = build_observer_capture(
        command_receipts=[{"command": ["sh", "-c", "true"], "exit_code": 0}],
        touched_files=["depone/example.py"],
        allowed_touched_files=["depone/example.py"],
        test_output={"status": "passed", "summary": "1 passed"},
    )
    manifest = build_capture_manifest(
        build_reference_adapter_fixture(_invocation("w1-a2-demonstration")),
        observer_capture=observer_capture,
        allowed_touched_files=["depone/example.py"],
        isolation=facts,
    )
    if manifest["assurance"] != "A2-isolated-observed":
        raise SystemExit(f"A2 demonstration did not reach A2: {manifest['assurance']}")
    return manifest


def main() -> None:
    FIX.mkdir(parents=True, exist_ok=True)
    (FIX / "chain").mkdir(exist_ok=True)
    (FIX / "keys").mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sandbox = work / "sandbox"
        evidence_dir = work / "evidence"
        keydir = work / "keys"  # OUT of evidence dir
        sandbox.mkdir()
        keydir.mkdir()
        private_key_path, public_key_path = gen_operator_keypair(str(keydir))

        lane = run_shell_lane(
            sandbox=str(sandbox),
            commands=[["sh", "-c", "echo hi > f.txt"]],
            test_command=["sh", "-c", "true"],
        )
        result = emit_lane_evidence(
            lane,
            str(evidence_dir),
            private_key_path,
            fixture=build_reference_adapter_fixture(_invocation("w1-a1")),
            allowed_touched_files=["f.txt"],
            public_key_path=public_key_path,
            task_id="w1-a1",
            invocation=["sh", "-c", "echo hi > f.txt"],
            runner_sandbox=str(sandbox),
        )

        for name in A1_ARTIFACTS:
            shutil.copyfile(evidence_dir / name, FIX / name)

        # Re-bind provenance to a stable repo-relative evidence path so a fresh
        # checkout re-derives it (the emitter binds an absolute temp path).
        provenance = build_signed_trusted_observer_provenance(
            result["manifest"],
            evidence_path=PROVENANCE_EVIDENCE_PATH,
            private_key_path=private_key_path,
            key_id="witnessd-operator",
        )
        _write_json(FIX / "provenance.json", provenance)

        shutil.copyfile(public_key_path, FIX / "keys" / "operator.pub")

        m1 = _chain_manifest("w1-chain-001", prev=None)
        m2 = _chain_manifest("w1-chain-002", prev=canonical_hash(m1))
        _write_json(FIX / "chain" / "capture-manifest-001.json", m1)
        _write_json(FIX / "chain" / "capture-manifest-002.json", m2)

        _write_json(FIX / "capture-manifest-a2.json", _a2_demonstration_manifest(work))

    (FIX / "A2-DEMONSTRATION.md").write_text(
        "# A2 fixture: demonstration only\n\n"
        "`capture-manifest-a2.json` is a demonstration of the A2 isolation gate, "
        "not a real isolated run. This host has no uid isolation for the witnessd "
        "runtime (the shell adapter runs in-process as the observer uid), so there "
        "is no uid-separated runner to observe. The manifest records real isolation "
        "facts probed via Depone's `probe_isolation_facts` (mode-0700 observer dir, "
        f"distinct runner uid {A2_DEMONSTRATION_RUNNER_UID}); Depone validates it as "
        "A2. `scripts/revalidate_w1.py` treats the strict A2 assert as "
        'uid-host-conditional and only enforces `assurance == "A2-isolated-observed"` '
        "on a host that produced a genuinely uid-isolated run.\n",
        encoding="utf-8",
    )
    print(f"W1 fixtures written to {FIX}")


if __name__ == "__main__":
    main()
