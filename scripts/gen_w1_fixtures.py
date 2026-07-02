"""Generate the committed W1 evidence fixtures under ``fixtures/w1/``.

A1 is produced natively by witnessd's Evidence Emitter (the sole SoT writer):
observer/runner separation holds, a real shell lane runs, and the emitter emits
the capture-manifest, observer-capture, runner-receipt, signed evidence bundle,
and evidence-contract companions. The trusted-observer-provenance is re-bound to
a stable, repo-relative evidence path so a checkout can re-derive it.

A2 is sourced from the W12 real observer-launched uid fixture. Regenerating W1
now requires ``fixtures/w12/capture-manifest.json`` to exist and to prove the
dedicated-observer-uid boundary through the same strict W12 assertions; the old
demonstration marker must not be recreated.

Only public material is committed; the operator private key never leaves the
throwaway temp dir. Run with:

    PYTHONPATH=/home/ubuntu/depone-assurance-repair uv run python3 scripts/gen_w1_fixtures.py
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from witnessd.adapters.shell import run_shell_lane
from witnessd.canonical import canonical_hash
from witnessd.capture import build_capture_manifest
from witnessd.emitter import emit_lane_evidence
from witnessd.fixture import build_reference_adapter_fixture
from witnessd.isolation import (
    UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
    verify_isolation_boundary,
)
from witnessd.observer import build_observer_capture
from witnessd.provenance import build_signed_trusted_observer_provenance
from witnessd.signing import gen_operator_keypair
from scripts.revalidate_w12 import (
    assert_runner_writable_observer_dir_blocks,
    assert_strict_real_a2,
)

FIX = REPO_ROOT / "fixtures" / "w1"
W12_REAL_A2 = REPO_ROOT / "fixtures" / "w12" / "capture-manifest.json"
PROVENANCE_EVIDENCE_PATH = "fixtures/w1/capture-manifest.json"

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


def _a2_real_manifest() -> dict:
    if not W12_REAL_A2.exists():
        raise SystemExit(f"W12 real A2 fixture missing: {W12_REAL_A2}")
    manifest = json.loads(W12_REAL_A2.read_text(encoding="utf-8"))
    assert_strict_real_a2(manifest)
    assert_runner_writable_observer_dir_blocks(manifest)
    if manifest.get("assurance") != "A2-isolated-observed":
        raise SystemExit(f"W12 fixture is not A2: {manifest.get('assurance')!r}")
    verified = verify_isolation_boundary(manifest.get("isolation"))
    if verified.get("boundary") is not True:
        raise SystemExit(f"W12 fixture does not prove A2 isolation: {verified!r}")
    if verified.get("model") != UID_OBSERVER_LAUNCHED_ISOLATION_MODEL:
        raise SystemExit(f"W12 fixture uses unexpected isolation model: {verified!r}")
    if verified.get("observer_launched") is not True:
        raise SystemExit(f"W12 fixture is not observer-launched: {verified!r}")
    return manifest


def _write_negative_fixtures() -> None:
    """Derive tamper fixtures from the just-written valid A1 artifacts.

    Each is a single targeted forgery that Depone must detect: a manifest with a
    stale ``observer_capture_hash``, a stale ``observer_capture.source_fixture_hash``,
    an out-of-envelope touched file, and a bundle whose top-level assurance is
    inflated to a forged ``A3`` the signature does not cover.
    """

    neg = FIX / "negative"
    neg.mkdir(exist_ok=True)
    manifest = json.loads((FIX / "capture-manifest.json").read_text(encoding="utf-8"))

    hash_mismatch = copy.deepcopy(manifest)
    good_hash = hash_mismatch["observer_capture_hash"]
    hash_mismatch["observer_capture_hash"] = (
        "0" if good_hash[0] != "0" else "1"
    ) + good_hash[1:]
    _write_json(neg / "observer_capture_hash_mismatch.json", hash_mismatch)

    stale_source = copy.deepcopy(manifest)
    stale_source["observer_capture"]["source_fixture_hash"] = "0" * 64
    stale_source["observer_capture_hash"] = canonical_hash(
        stale_source["observer_capture"]
    )
    _write_json(neg / "stale_source_fixture_hash.json", stale_source)

    unexpected_touched = copy.deepcopy(manifest)
    unexpected_touched["observer_capture"]["touched_files"] = ["f.txt", "secret.py"]
    unexpected_touched["observer_capture_hash"] = canonical_hash(
        unexpected_touched["observer_capture"]
    )
    _write_json(neg / "unexpected_touched_files.json", unexpected_touched)

    bundle = json.loads((FIX / "bundle.json").read_text(encoding="utf-8"))
    forged = copy.deepcopy(bundle)
    forged["assurance"] = "A3-fabricated-observed"
    forged["statement"]["predicate"]["assurance"] = "A3-fabricated-observed"
    _write_json(neg / "forged_a3.json", forged)


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

        _a2_real_manifest()
        shutil.copyfile(W12_REAL_A2, FIX / "capture-manifest-a2.json")

    marker = FIX / "A2-DEMONSTRATION.md"
    if marker.exists():
        marker.unlink()
    _write_negative_fixtures()
    print(f"W1 fixtures written to {FIX}")


if __name__ == "__main__":
    main()
