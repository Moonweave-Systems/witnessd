from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


DEPONE_ROOT = Path(
    os.environ.get("WITNESSD_DEPONE_ROOT", Path(__file__).resolve().parents[2] / "depone")
).resolve(strict=False)
_modules_before_depone_import = set(sys.modules)
_added_depone_path = str(DEPONE_ROOT) not in sys.path
if _added_depone_path:
    sys.path.insert(0, str(DEPONE_ROOT))

from depone.agent_fabric.claim_gate import canonical_hash  # noqa: E402
from depone.verify.adapters.base import EvidenceContext, EvidenceFile  # noqa: E402
from depone.verify.evidence_contract import validate_advisory_provenance  # noqa: E402
from witnessd.__main__ import main  # noqa: E402
from witnessd.distribution import InitConfig, init_witnessd_home  # noqa: E402

if _added_depone_path:
    sys.path.remove(str(DEPONE_ROOT))
for _module_name in set(sys.modules) - _modules_before_depone_import:
    if _module_name == "depone" or _module_name.startswith("depone."):
        del sys.modules[_module_name]


BOUNDARY_FLAGS = (
    "raises_assurance",
    "verifies_evidence",
    "can_change_evidence_verdict",
    "executes_proofrun",
)


def _write_trace_receipt(repo: Path, symptom: str) -> None:
    (repo / "orro-trace-reproduction.json").write_text(
        json.dumps(
            {
                "kind": "orro-trace-reproduction",
                "symptom": symptom,
                "command": ["python3", "-m", "unittest", "tests.test_widget"],
                "exit_code": 1,
                "stdout": "",
                "stderr": f"{symptom}\nAssertionError: 7 != 9",
                "minimized": True,
                "external_confirmation": {
                    "discriminating_probe_ran": True,
                    "ruled_out_rival": True,
                    "red_to_green_observed": True,
                    "reported_verbatim": "operator isolated the cause and reran: PASS",
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_advisory(
    mode: str,
    goal: str,
    *,
    repo: Path,
    home: Path,
    out_dir: Path,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(
            [
                "orro",
                mode,
                goal,
                "--repo",
                str(repo),
                "--home",
                str(home),
                "--out",
                str(out_dir / f"orro-{mode}.json"),
                "--json",
            ]
        )
    if code != 0:
        raise AssertionError(stdout.getvalue())
    return json.loads(stdout.getvalue())


def _validate(out_dir: Path, public_key: Path) -> list:
    contract_path = out_dir / "evidence-contract.json"
    if not contract_path.is_file():
        raise AssertionError("advisory provenance contract was not emitted")
    files = []
    for path in sorted(out_dir.iterdir()):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        files.append(
            EvidenceFile(
                path=path.name,
                content=content,
                sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    evidence = EvidenceContext(
        run_id="witnessd-advisory-test",
        files=files,
        raw={"trusted_observer_public_key_file": str(public_key)},
    )
    return validate_advisory_provenance(evidence, contract)


class OrroAdvisoryProvenanceTests(unittest.TestCase):
    def _assert_boundary(self, decision: dict) -> None:
        self.assertTrue(decision["boundary"]["advisory_only"])
        for flag in BOUNDARY_FLAGS:
            with self.subTest(flag=flag):
                self.assertFalse(decision["boundary"][flag])

    def test_sketch_emission_is_rederived_by_depone_v108(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            home = root / ".witnessd"
            out_dir = root / "sketch-artifacts"

            decision = _run_advisory(
                "sketch",
                "seal one bounded advisory direction",
                repo=repo,
                home=home,
                out_dir=out_dir,
            )

            self._assert_boundary(decision)
            self.assertEqual(
                _validate(out_dir, home / "keys" / "operator-ed25519.pub.pem"),
                [],
            )

    def test_trace_confirmed_emission_seals_receipt_and_preserves_execution_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "widget.py").write_text("def total() -> int:\n    return 7\n", encoding="utf-8")
            symptom = "widget total was 7 expected 9"
            _write_trace_receipt(repo, symptom)
            home = root / ".witnessd"
            out_dir = root / "trace-artifacts"
            out_dir.mkdir()
            verdict_path = out_dir / "team-ledger-verdict.json"
            verdict_path.write_text('{"decision":"pass"}\n', encoding="utf-8")
            verdict_before = verdict_path.read_bytes()

            decision = _run_advisory(
                "trace",
                symptom,
                repo=repo,
                home=home,
                out_dir=out_dir,
            )

            receipt_path = out_dir / "orro-trace-reproduction.json"
            self.assertTrue(receipt_path.is_file(), "trace receipt was not emitted")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(decision["root_cause"]["tier"], "confirmed")
            self.assertEqual(decision["reproduction"]["receipt_sha256"], canonical_hash(receipt))
            self._assert_boundary(decision)
            self.assertEqual(verdict_path.read_bytes(), verdict_before)
            self.assertEqual(
                _validate(out_dir, home / "keys" / "operator-ed25519.pub.pem"),
                [],
            )

    def test_tampered_sketch_refutes_with_tamper_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            home = root / ".witnessd"
            out_dir = root / "sketch-artifacts"
            _run_advisory(
                "sketch",
                "seal one bounded advisory direction",
                repo=repo,
                home=home,
                out_dir=out_dir,
            )
            decision_path = out_dir / "orro-sketch.json"
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            decision["decision_record"]["decision"] = "mutated after sealing"
            decision_path.write_text(
                json.dumps(decision, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            errors = _validate(out_dir, home / "keys" / "operator-ed25519.pub.pem")

            self.assertIn("ERR_ADVISORY_SKETCH_TAMPER", [error.code for error in errors])

    def test_confirmed_trace_without_sealed_receipt_refutes_as_unbacked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "widget.py").write_text("def total() -> int:\n    return 7\n", encoding="utf-8")
            symptom = "widget total was 7 expected 9"
            _write_trace_receipt(repo, symptom)
            home = root / ".witnessd"
            out_dir = root / "trace-artifacts"
            _run_advisory(
                "trace",
                symptom,
                repo=repo,
                home=home,
                out_dir=out_dir,
            )
            receipt_path = out_dir / "orro-trace-reproduction.json"
            self.assertTrue(receipt_path.is_file(), "trace receipt was not emitted")
            receipt_path.unlink()

            errors = _validate(out_dir, home / "keys" / "operator-ed25519.pub.pem")

            self.assertIn(
                "ERR_ADVISORY_TRACE_CONFIRMED_UNBACKED",
                [error.code for error in errors],
            )

    def test_offline_check_reports_provenance_pass_then_tamper_refute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            home = root / ".witnessd"
            init_witnessd_home(
                InitConfig(
                    home=home,
                    witnessd_root=Path(__file__).resolve().parents[1],
                    depone_root=DEPONE_ROOT,
                )
            )
            out_dir = root / "sketch-artifacts"
            _run_advisory(
                "sketch",
                "seal one bounded advisory direction",
                repo=repo,
                home=home,
                out_dir=out_dir,
            )

            def run_check() -> tuple[int, dict]:
                stdout = io.StringIO()
                try:
                    with redirect_stdout(stdout):
                        code = main(
                            [
                                "orro",
                                "advisory-provenance-check",
                                str(out_dir),
                                "--home",
                                str(home),
                                "--json",
                            ]
                        )
                except SystemExit as exc:
                    return int(exc.code), {}
                return code, json.loads(stdout.getvalue())

            pass_code, pass_payload = run_check()
            self.assertEqual(pass_code, 0)
            self.assertEqual(pass_payload["decision"], "PASS")
            self.assertFalse(pass_payload["boundary"]["raises_assurance"])
            self.assertFalse(pass_payload["boundary"]["asserts_correctness"])

            decision_path = out_dir / "orro-sketch.json"
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            decision["decision_record"]["decision"] = "tampered"
            decision_path.write_text(
                json.dumps(decision, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            refute_code, refute_payload = run_check()
            self.assertEqual(refute_code, 1)
            self.assertEqual(refute_payload["decision"], "REFUTE")
            self.assertIn("ERR_ADVISORY_SKETCH_TAMPER", refute_payload["error_codes"])


if __name__ == "__main__":
    unittest.main()
