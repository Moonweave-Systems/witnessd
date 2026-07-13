from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


DEPONE_ROOT = Path(
    os.environ.get(
        "WITNESSD_DEPONE_ROOT", Path(__file__).resolve().parents[2] / "depone"
    )
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


def _write_trace_receipt(repo: Path, symptom: str) -> str:
    command = ["python3", "-m", "unittest", "tests.test_widget"]
    output = f"{symptom}\nAssertionError: 7 != 9"
    receipt_text = (
        json.dumps(
            {
                "kind": "orro-trace-reproduction",
                "symptom": symptom,
                "command": command,
                "exit_code": 1,
                "stdout": "",
                "stderr": output,
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
        + "\n"
    )
    (repo / "orro-trace-reproduction.json").write_text(
        receipt_text,
        encoding="utf-8",
    )
    return hashlib.sha256(receipt_text.encode("utf-8")).hexdigest()


def _sketch_decision(*, chosen_direction: str = "validate-agent-record") -> dict:
    return {
        "frame": "Preserve agent reasoning and seal only validated claims.",
        "criteria": ["honest provenance", "thin harness"],
        "candidates": [
            {
                "axis": "validate-agent-record",
                "summary": "Validate and seal the calling agent's authored decision.",
                "benefits": ["preserves real reasoning"],
                "risks": ["requires a decision file"],
                "tradeoff": "More explicit input for honest provenance.",
            },
            {
                "axis": "keep-template-authoring",
                "summary": "Keep deterministic template authoring.",
                "benefits": ["works without input"],
                "risks": ["fabricates reasoning provenance"],
                "tradeoff": "Convenience at the cost of honesty.",
            },
        ],
        "chosen": {
            "direction": chosen_direction,
            "reason": "The seal must cover reasoning the agent actually authored.",
            "confidence": "high",
            "what_would_change_it": "A verifier contract that cannot seal authored JSON.",
        },
        "rejected": [
            {
                "option": "keep-template-authoring",
                "why_lost": "It replaces and misattributes the agent's reasoning.",
            }
        ],
        "no_gos": ["claim that sealing establishes correctness"],
        "rabbit_holes": ["enforce a mandatory ideation ceremony"],
    }


def _trace_decision(receipt_sha256: str, *, tier: str) -> dict:
    hypotheses = [
        {
            "mechanism": "The total path returns the stale value seven.",
            "prediction": "The observed output contains the seven-versus-nine mismatch.",
            "discriminating_probe": "AssertionError: 7 != 9",
            "confidence": "moderate",
        }
    ]
    decision = {
        "check_the_plug": {"repo_exists": True},
        "reproduction": {
            "path": "orro-trace-reproduction.json",
            "sha256": receipt_sha256,
        },
        "localization": {"suspect_region_cited": ["widget.py:2"]},
        "hypotheses": hypotheses,
        "confirmation": {},
        "root_cause": {
            "tier": tier,
            "hypothesis_index": 0,
            "summary": hypotheses[0]["mechanism"],
            "finding": hypotheses[0]["mechanism"],
        },
        "fix_scope": {"cause_site": "widget.py:2"},
    }
    if tier == "confirmed":
        decision["hypotheses"].append(
            {
                "mechanism": "The runtime imports a different widget module.",
                "prediction": "The module path differs from the repository path.",
                "discriminating_probe": "module path mismatch",
                "confidence": "low",
            }
        )
        decision["confirmation"] = {"rival_hypotheses_ruled_out": [1]}
    return decision


def _run_advisory(
    mode: str,
    goal: str,
    *,
    repo: Path,
    home: Path,
    out_dir: Path,
    decision: dict | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    decision_path = out_dir.parent / f"{mode}-agent-decision.json"
    if decision is not None:
        decision_path.write_text(
            json.dumps(decision, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    argv = [
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
    if decision is not None:
        argv.extend(["--decision", str(decision_path)])
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
    except SystemExit as exc:
        code = int(exc.code)
    if code != 0:
        raise AssertionError(stdout.getvalue() or stderr.getvalue())
    return json.loads(stdout.getvalue())


def _run_advisory_result(
    mode: str,
    goal: str,
    *,
    repo: Path,
    home: Path,
    out_dir: Path,
    decision: dict,
) -> tuple[int, dict]:
    decision_path = out_dir.parent / f"{mode}-agent-decision.json"
    decision_path.write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "orro",
                    mode,
                    goal,
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                    "--decision",
                    str(decision_path),
                    "--out",
                    str(out_dir / f"orro-{mode}.json"),
                    "--json",
                ]
            )
    except SystemExit as exc:
        code = int(exc.code)
    output = stdout.getvalue()
    return code, json.loads(output) if output else {"stderr": stderr.getvalue()}


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

    def test_agent_authored_sketch_is_validated_and_sealed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            home = root / ".witnessd"
            out_dir = root / "sketch-artifacts"

            decision = _run_advisory(
                "sketch",
                "seal the agent-authored direction",
                repo=repo,
                home=home,
                out_dir=out_dir,
                decision=_sketch_decision(),
            )

            self.assertTrue(decision["agent_authored"])
            self.assertEqual(decision["authored_by"], "agent")
            self.assertEqual(
                decision["chosen"]["reason"],
                "The seal must cover reasoning the agent actually authored.",
            )
            self.assertNotIn("generated_independently", json.dumps(decision))
            self.assertNotIn("critique_deferred", json.dumps(decision))
            self.assertEqual(
                _validate(out_dir, home / "keys" / "operator-ed25519.pub.pem"),
                [],
            )

    def test_agent_authored_sketch_refuses_choice_outside_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            home = root / ".witnessd"
            out_dir = root / "sketch-artifacts"

            code, result = _run_advisory_result(
                "sketch",
                "refuse an invented direction",
                repo=repo,
                home=home,
                out_dir=out_dir,
                decision=_sketch_decision(chosen_direction="invented-direction"),
            )

            self.assertEqual(code, 1)
            self.assertEqual(
                result["error"]["code"],
                "ERR_ORRO_SKETCH_CHOSEN_NOT_IN_CANDIDATES",
            )
            self.assertFalse((out_dir / "orro-sketch.json").exists())
            self.assertFalse((out_dir / "advisory-provenance-bundle.json").exists())

    def test_agent_authored_sketch_preserves_non_first_candidate_choice(self) -> None:
        # Regression: sealing must not rebind chosen.direction to candidates[0].
        # An agent that picks the second candidate must have that choice sealed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            home = root / ".witnessd"
            out_dir = root / "sketch-artifacts"

            decision = _run_advisory(
                "sketch",
                "preserve the agent's second-candidate choice",
                repo=repo,
                home=home,
                out_dir=out_dir,
                decision=_sketch_decision(chosen_direction="keep-template-authoring"),
            )

            self.assertEqual(decision["chosen"]["direction"], "keep-template-authoring")
            sealed = json.loads(
                (out_dir / "orro-sketch.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sealed["chosen"]["direction"], "keep-template-authoring")
            self.assertEqual(
                _validate(out_dir, home / "keys" / "operator-ed25519.pub.pem"),
                [],
            )

    def test_trace_confirmed_emission_seals_receipt_and_preserves_execution_verdict(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "widget.py").write_text(
                "def total() -> int:\n    return 7\n", encoding="utf-8"
            )
            symptom = "widget total was 7 expected 9"
            receipt_sha256 = _write_trace_receipt(repo, symptom)
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
                decision=_trace_decision(receipt_sha256, tier="confirmed"),
            )

            receipt_path = out_dir / "orro-trace-reproduction.json"
            self.assertTrue(receipt_path.is_file(), "trace receipt was not emitted")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(decision["root_cause"]["tier"], "confirmed")
            self.assertEqual(
                decision["reproduction"]["receipt_sha256"], canonical_hash(receipt)
            )
            self._assert_boundary(decision)
            self.assertEqual(verdict_path.read_bytes(), verdict_before)
            self.assertEqual(
                _validate(out_dir, home / "keys" / "operator-ed25519.pub.pem"),
                [],
            )

    def test_agent_authored_suspected_trace_accepts_one_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            symptom = "widget total was 7 expected 9"
            receipt_sha256 = _write_trace_receipt(repo, symptom)
            home = root / ".witnessd"
            out_dir = root / "trace-artifacts"

            decision = _run_advisory(
                "trace",
                symptom,
                repo=repo,
                home=home,
                out_dir=out_dir,
                decision=_trace_decision(receipt_sha256, tier="suspected"),
            )

            self.assertTrue(decision["agent_authored"])
            self.assertEqual(decision["root_cause"]["tier"], "suspected")
            self.assertEqual(len(decision["hypotheses"]), 1)
            self.assertEqual(
                _validate(out_dir, home / "keys" / "operator-ed25519.pub.pem"),
                [],
            )

    def test_agent_authored_confirmed_trace_without_backing_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            symptom = "widget total was 7 expected 9"
            home = root / ".witnessd"
            out_dir = root / "trace-artifacts"

            code, result = _run_advisory_result(
                "trace",
                symptom,
                repo=repo,
                home=home,
                out_dir=out_dir,
                decision=_trace_decision("0" * 64, tier="confirmed"),
            )

            self.assertEqual(code, 1)
            self.assertEqual(
                result["error"]["code"],
                "ERR_ORRO_TRACE_CONFIRMED_UNBACKED",
            )
            self.assertFalse((out_dir / "orro-trace.json").exists())
            self.assertFalse((out_dir / "advisory-provenance-bundle.json").exists())

    def test_bare_goal_fallback_is_explicitly_degraded(self) -> None:
        for mode in ("sketch", "trace"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "repo"
                repo.mkdir()
                decision = _run_advisory(
                    mode,
                    "headless fallback record",
                    repo=repo,
                    home=root / ".witnessd",
                    out_dir=root / f"{mode}-artifacts",
                )

                self.assertFalse(decision["agent_authored"])
                self.assertTrue(decision["degraded"])
                self.assertNotIn("generated_independently", json.dumps(decision))
                self.assertNotIn("critique_deferred", json.dumps(decision))

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

            self.assertIn(
                "ERR_ADVISORY_SKETCH_TAMPER", [error.code for error in errors]
            )

    def test_confirmed_trace_without_sealed_receipt_refutes_as_unbacked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "widget.py").write_text(
                "def total() -> int:\n    return 7\n", encoding="utf-8"
            )
            symptom = "widget total was 7 expected 9"
            receipt_sha256 = _write_trace_receipt(repo, symptom)
            home = root / ".witnessd"
            out_dir = root / "trace-artifacts"
            _run_advisory(
                "trace",
                symptom,
                repo=repo,
                home=home,
                out_dir=out_dir,
                decision=_trace_decision(receipt_sha256, tier="confirmed"),
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
