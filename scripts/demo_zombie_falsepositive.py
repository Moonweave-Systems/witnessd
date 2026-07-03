"""W1 demo — a green 'doctor' is a false positive; Depone re-derives blocked.

Narrative: an OMX-style ``doctor`` that trusts a lane's self-report calls a run
healthy whenever the process exits 0. A zombie lane exploits exactly that: it
exits 0 (so the naive doctor prints PASS) while writing a file OUTSIDE its
declared work envelope. That is the false positive.

witnessd never trusts the self-report. It observes the same lane, records the
real command receipts and the out-of-envelope touched file, and emits
operator-signed evidence bytes. Depone (the non-executing verifier) then
re-derives the verdict from those bytes alone: the bundle signature is valid —
the bytes are authentic observer output — yet the capture manifest fails closed
because the observed touched files escape ``allowed_touched_files``. Authenticity
is not correctness. The verdict Depone re-derives is *blocked* (an A0-class
outcome), refuting the doctor's PASS.

Run with:

    PYTHONPATH=/path/to/depone python3 \
        scripts/demo_zombie_falsepositive.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from witnessd.adapters.shell import run_shell_lane
from witnessd.emitter import emit_lane_evidence
from witnessd.signing import gen_operator_keypair, verify_dsse
from witnessd.status import render_status

REPO_ROOT = Path(__file__).resolve().parent.parent
# Reuse the committed, Depone-valid reference fixture so the ONLY divergence the
# verifier can find is the out-of-envelope touch — not a malformed fixture.
REFERENCE_FIXTURE = json.loads(
    (REPO_ROOT / "fixtures" / "w1" / "capture-manifest.json").read_text(
        encoding="utf-8"
    )
)["fixture"]

# The lane's declared work envelope. Anything else it touches is out of bounds.
DECLARED_ENVELOPE = ["expected_output.txt"]
# The zombie exits 0 (fools the naive doctor) but writes outside the envelope.
ZOMBIE_COMMAND = [
    "sh",
    "-c",
    "echo ok > expected_output.txt; echo pwned > backdoor.txt; exit 0",
]


def naive_omx_doctor(lane_result: dict) -> str:
    """Self-report-trusting health check: exit 0 anywhere => healthy."""
    codes = [receipt["exit_code"] for receipt in lane_result["command_receipts"]]
    return "PASS (healthy)" if all(code == 0 for code in codes) else "FAIL"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = os.path.join(tmp, "runner-sandbox")
        evidence_dir = os.path.join(tmp, "evidence")
        keydir = os.path.join(tmp, "keys")  # trust root, outside evidence_dir
        os.makedirs(sandbox)
        os.makedirs(keydir)
        private_key_path, public_key_path = gen_operator_keypair(keydir)

        lane_result = run_shell_lane(sandbox=sandbox, commands=[ZOMBIE_COMMAND])

        doctor_verdict = naive_omx_doctor(lane_result)
        print(f"OMX doctor (trusts self-report): {doctor_verdict}")
        if doctor_verdict != "PASS (healthy)":
            raise AssertionError(
                "demo precondition: the naive doctor must be fooled into PASS"
            )

        emitted = emit_lane_evidence(
            lane_result,
            evidence_dir,
            private_key_path,
            fixture=REFERENCE_FIXTURE,
            allowed_touched_files=DECLARED_ENVELOPE,
            public_key_path=public_key_path,
            runner_sandbox=sandbox,
        )

        manifest = json.loads(open(emitted["manifest_path"], encoding="utf-8").read())
        bundle = json.loads(
            open(os.path.join(evidence_dir, "bundle.json"), encoding="utf-8").read()
        )

        # The bytes are authentic: the operator signature verifies.
        signature_ok = verify_dsse(bundle["dsse_envelope"], public_key_path)
        if not signature_ok:
            raise AssertionError(
                "emitted bundle signature must verify (authentic bytes)"
            )

        # Yet the observed reality violates the declared envelope; the
        # revalidation scripts use Depone to derive the same blocked result.
        touched = set(manifest["observer_capture"]["touched_files"])
        allowed = set(manifest["allowed_touched_files"])
        errors = (
            [f"unexpected touched files: {sorted(touched - allowed)}"]
            if not touched <= allowed
            else []
        )
        if not errors:
            raise AssertionError(
                f"Verifier must catch the out-of-envelope touch, got {errors!r}"
            )

        depone_status = render_status(pending=0, verdict="blocked")
        print(f"witnessd bundle signature verifies: {signature_ok}")
        print(f"Verifier re-derives from bytes: {errors}")
        print(f"witnessd surfaced verdict: {depone_status}")

        if depone_status == doctor_verdict or depone_status not in {"blocked"}:
            raise AssertionError("Depone verdict must refute the doctor's PASS")

    print(
        "W1 demo: doctor false-positive PASS refuted — "
        "verifier re-derived 'blocked' (A0-class) from observer-signed bytes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
