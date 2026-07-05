from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "orro@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "ORRO"], cwd=repo, check=True)
    (repo / "README.md").write_text("# ORRO fixture\n", encoding="utf-8")
    (repo / "SKILL.md").write_text("---\nname: orro-fixture\n---\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


def _depone_root() -> Path:
    env_root = os.environ.get("WITNESSD_DEPONE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[1].parent / "depone"


class OrroPublicFlowTests(unittest.TestCase):
    def _module_run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        depone_root = str(_depone_root())
        current_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            depone_root if not current_pythonpath else f"{depone_root}{os.pathsep}{current_pythonpath}"
        )
        return subprocess.run(
            [sys.executable, "-m", "witnessd", *args],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _init_home(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        home = root / "home"
        repo.mkdir()
        _seed_repo(repo)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(["init", "--home", str(home), "--depone-root", str(_depone_root())]),
                0,
            )
        return repo, home

    def _proofrun(self, root: Path, *, orro_alias: bool = False) -> tuple[Path, Path, dict]:
        repo, home = self._init_home(root)
        stdout = io.StringIO()
        stderr = io.StringIO()
        command = ["orro", "proofrun"] if orro_alias else ["proofrun"]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([*command, "write two proof files", "--repo", str(repo), "--home", str(home)])
        self.assertEqual(code, 0, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        return home, Path(payload["run_dir"]), payload

    def test_proofrun_alias_reuses_run_surface_without_final_trust_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, payload = self._proofrun(Path(tmp))

            self.assertEqual(payload["decision"], "pass")
            self.assertTrue((run_dir / "team-ledger.json").is_file())
            self.assertTrue((run_dir / "team-ledger-verdict.json").is_file())
            self.assertNotIn("final_trust", payload)
            self.assertNotIn("raises_assurance", payload)

    def test_orro_proofrun_normalizes_to_proofrun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, payload = self._proofrun(Path(tmp), orro_alias=True)

            self.assertEqual(payload["decision"], "pass")
            self.assertTrue((run_dir / "team-ledger.json").is_file())

    def test_proofcheck_delegates_team_ledger_run_dir_to_depone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))
            out = run_dir / "proofcheck-verdict.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["proofcheck", str(run_dir), "--home", str(home), "--out", str(out)])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["command"], "proofcheck")
            self.assertEqual(payload["verifier_command"], "team-ledger")
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(payload["out"], str(out))
            self.assertEqual(payload["orro_binding"]["kind"], "orro-proofcheck-binding")
            self.assertTrue(out.is_file())

    def test_proofcheck_without_out_does_not_write_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))
            verdict = run_dir / "proofcheck-verdict.json"
            if verdict.exists():
                verdict.unlink()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["proofcheck", str(run_dir), "--home", str(home)])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "pass")
            self.assertNotIn("out", payload)
            self.assertFalse(verdict.exists())

    def test_proofcheck_out_fails_closed_when_depone_writes_no_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            out = evidence_dir / "proofcheck-verdict.json"

            with patch(
                "witnessd.__main__._run_depone_json",
                return_value=(
                    0,
                    {
                        "decision": "pass",
                        "verifier_command": "proofcheck",
                        "out": str(out),
                    },
                ),
            ):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = main(["proofcheck", str(evidence_dir), "--out", str(out)])

            self.assertEqual(code, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(
                payload["error"]["code"],
                "ERR_ORRO_PROOFCHECK_VERDICT_BINDING_FAILED",
            )
            self.assertNotIn("orro_binding", payload)

    def test_proofcheck_out_fails_closed_when_depone_writes_bad_verdict(self) -> None:
        for contents in ("{not json\n", "[]\n"):
            with self.subTest(contents=contents):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    evidence_dir = root / "evidence"
                    evidence_dir.mkdir()
                    out = evidence_dir / "proofcheck-verdict.json"

                    def fake_depone(_command: list[str], *, env: dict[str, str]) -> tuple[int, dict]:
                        out.write_text(contents, encoding="utf-8")
                        return (
                            0,
                            {
                                "decision": "pass",
                                "verifier_command": "proofcheck",
                                "out": str(out),
                            },
                        )

                    with patch("witnessd.__main__._run_depone_json", side_effect=fake_depone):
                        stdout = io.StringIO()
                        with redirect_stdout(stdout):
                            code = main(["proofcheck", str(evidence_dir), "--out", str(out)])

                    self.assertEqual(code, 1)
                    payload = json.loads(stdout.getvalue())
                    self.assertEqual(payload["decision"], "blocked")
                    self.assertEqual(
                        payload["error"]["code"],
                        "ERR_ORRO_PROOFCHECK_VERDICT_BINDING_FAILED",
                    )
                    self.assertNotIn("orro_binding", payload)

    def test_orro_proofcheck_blocks_scout_only_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _home = self._init_home(root)
            scout_stdout = io.StringIO()
            with redirect_stdout(scout_stdout):
                self.assertEqual(main(["orro", "scout", "inspect", "--repo", str(repo)]), 0)
            scout_dir = Path(json.loads(scout_stdout.getvalue())["run_dir"])

            proofcheck_stdout = io.StringIO()
            with redirect_stdout(proofcheck_stdout):
                code = main(["orro", "proofcheck", str(scout_dir)])

            self.assertEqual(code, 1)
            payload = json.loads(proofcheck_stdout.getvalue())
            self.assertEqual(payload["decision"], "blocked")
            self.assertEqual(payload["verifier_command"], "proofcheck")

    def test_orro_handoff_hashes_evidence_without_approval_or_assurance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["proofcheck", str(run_dir), "--home", str(home), "--out", str(run_dir / "proofcheck-verdict.json")]),
                    0,
                )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "handoff", str(run_dir), "--out", str(run_dir / "orro-handoff.json")])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "orro-handoff")
            self.assertFalse(payload["boundary"]["approves_merge"])
            self.assertFalse(payload["boundary"]["raises_assurance"])
            proofcheck_payload = json.loads(
                (run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                proofcheck_payload["orro_binding"]["artifact_hashes"],
                payload["artifact_hashes"],
            )
            hashed_paths = {item["path"] for item in payload["artifact_hashes"]}
            self.assertIn("team-ledger.json", hashed_paths)
            self.assertNotIn("proofcheck-verdict.json", hashed_paths)
            self.assertNotIn("team-ledger-verdict.json", hashed_paths)
            self.assertNotIn("orro-handoff.json", hashed_paths)
            self.assertTrue((run_dir / "orro-handoff.json").is_file())

            rerun_stdout = io.StringIO()
            with redirect_stdout(rerun_stdout):
                self.assertEqual(
                    main(
                        [
                            "orro",
                            "handoff",
                            str(run_dir),
                            "--out",
                            str(run_dir / "orro-handoff.json"),
                        ]
                    ),
                    0,
                )
            self.assertEqual(
                json.loads(rerun_stdout.getvalue())["artifact_hashes"],
                payload["artifact_hashes"],
            )

    def test_orro_handoff_requires_explicit_passing_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, _payload = self._proofrun(Path(tmp))
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_REQUIRED",
            )

    def test_handoff_rejects_malformed_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, _payload = self._proofrun(Path(tmp))
            (run_dir / "proofcheck-verdict.json").write_text("{not json\n", encoding="utf-8")
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_LOAD_FAILED",
            )

    def test_handoff_rejects_non_object_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, _payload = self._proofrun(Path(tmp))
            (run_dir / "proofcheck-verdict.json").write_text("[]\n", encoding="utf-8")
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_LOAD_FAILED",
            )

    def test_orro_handoff_rejects_non_passing_proofcheck_verdict(self) -> None:
        for decision in ("blocked", "refuted"):
            with self.subTest(decision=decision):
                with tempfile.TemporaryDirectory() as tmp:
                    _home, run_dir, _payload = self._proofrun(Path(tmp))
                    (run_dir / "proofcheck-verdict.json").write_text(
                        json.dumps({"decision": decision}),
                        encoding="utf-8",
                    )
                    out = run_dir / "orro-handoff.json"

                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        code = main(["orro", "handoff", str(run_dir), "--out", str(out), "--json"])

                    self.assertEqual(code, 1)
                    self.assertFalse(out.exists())
                    self.assertEqual(
                        json.loads(stdout.getvalue())["error"]["code"],
                        "ERR_ORRO_HANDOFF_PROOFCHECK_NOT_PASS",
                    )

    def test_handoff_rejects_unbound_passing_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _home, run_dir, _payload = self._proofrun(Path(tmp))
            (run_dir / "proofcheck-verdict.json").write_text(
                json.dumps({"decision": "pass"}),
                encoding="utf-8",
            )
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_UNBOUND",
            )

    def test_handoff_rejects_stale_passing_proofcheck_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_root = root / "first"
            second_root = root / "second"
            first_root.mkdir()
            second_root.mkdir()
            home, first_run_dir, _payload = self._proofrun(first_root)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "proofcheck",
                            str(first_run_dir),
                            "--home",
                            str(home),
                            "--out",
                            str(first_run_dir / "proofcheck-verdict.json"),
                        ]
                    ),
                    0,
                )
            _home, second_run_dir, _payload = self._proofrun(second_root)
            (second_run_dir / "proofcheck-verdict.json").write_text(
                (first_run_dir / "proofcheck-verdict.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            out = second_run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "handoff", str(second_run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_HANDOFF_PROOFCHECK_BINDING_MISMATCH",
            )

    def test_handoff_ignores_non_object_optional_decision_ref_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir, _payload = self._proofrun(Path(tmp))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "proofcheck",
                            str(run_dir),
                            "--home",
                            str(home),
                            "--out",
                            str(run_dir / "proofcheck-verdict.json"),
                        ]
                    ),
                    0,
                )
            (run_dir / "team-ledger-verdict.json").write_text("[]\n", encoding="utf-8")
            out = run_dir / "orro-handoff.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["handoff", str(run_dir), "--out", str(out), "--json"])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            team_ref = next(
                ref
                for ref in payload["decision_refs"]
                if ref["path"] == "team-ledger-verdict.json"
            )
            self.assertNotIn("decision", team_ref)
            self.assertTrue(out.is_file())

    def test_public_orro_json_errors_are_json(self) -> None:
        proofcheck_stdout = io.StringIO()
        with redirect_stdout(proofcheck_stdout):
            proofcheck_code = main(["proofcheck", "--json"])

        self.assertEqual(proofcheck_code, 2)
        self.assertEqual(
            json.loads(proofcheck_stdout.getvalue())["error"]["code"],
            "ERR_ORRO_PROOFCHECK_INPUT_REQUIRED",
        )

        handoff_stdout = io.StringIO()
        with redirect_stdout(handoff_stdout):
            handoff_code = main(["handoff", "--json"])

        self.assertEqual(handoff_code, 2)
        self.assertEqual(
            json.loads(handoff_stdout.getvalue())["error"]["code"],
            "ERR_ORRO_HANDOFF_INPUT_REQUIRED",
        )

    def test_proofcheck_json_pin_failure_is_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            (evidence_dir / "team-ledger.json").write_text("{}", encoding="utf-8")
            bad_home = root / "uninitialized-home"
            bad_home.mkdir()

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "proofcheck",
                        str(evidence_dir),
                        "--home",
                        str(bad_home),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_WITNESSD_DEPONE_PIN_MISSING",
            )

    def test_orro_doctor_reports_readiness_not_verifier_refutation(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["orro", "doctor", "--adapter", "codex"])

        self.assertIn(code, {0, 1})
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["command"], "orro doctor")
        self.assertFalse(payload["boundary"]["verifier_refuted"])
        self.assertFalse(payload["boundary"]["raises_assurance"])

    def test_orro_doctor_blocks_invalid_depone_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            (home / "provision.json").write_text(
                json.dumps(
                    {
                        "kind": "witnessd-depone-provision",
                        "schema_version": "0.1",
                        "depone": {
                            "root": str(Path(tmp) / "missing-depone"),
                            "commit": "0" * 40,
                        },
                    }
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["orro", "doctor", "--home", str(home), "--json"])

            self.assertEqual(code, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "blocked")
            checks = {check["name"]: check for check in payload["checks"]}
            self.assertEqual(checks["depone_pin"]["status"], "blocked")
            self.assertEqual(
                checks["depone_pin"]["code"],
                "ERR_WITNESSD_DEPONE_ROOT_INVALID",
            )
            self.assertFalse(payload["boundary"]["verifier_refuted"])

    def test_full_orro_flow_module_surface_reaches_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = self._init_home(root)

            scout = self._module_run(
                ["orro", "scout", "inspect repo", "--repo", str(repo), "--home", str(home)]
            )
            self.assertEqual(scout.returncode, 0, scout.stderr)
            scout_payload = json.loads(scout.stdout)
            self.assertEqual(scout_payload["decision"], "scouted")

            flowplan = self._module_run(
                ["orro", "flowplan", "plan proof run", "--root", str(repo)]
            )
            self.assertEqual(flowplan.returncode, 0, flowplan.stderr)
            flowplan_payload = json.loads(flowplan.stdout)
            self.assertEqual(flowplan_payload["sealed_plan"]["goal"], "plan proof run")
            self.assertNotIn("team_ledger", flowplan_payload)

            proofrun = self._module_run(
                [
                    "orro",
                    "proofrun",
                    "write proof files",
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                ]
            )
            self.assertEqual(proofrun.returncode, 0, proofrun.stderr)
            proofrun_payload = json.loads(proofrun.stdout)
            run_dir = Path(proofrun_payload["run_dir"])
            self.assertTrue((run_dir / "team-ledger.json").is_file())

            proofcheck = self._module_run(
                [
                    "orro",
                    "proofcheck",
                    str(run_dir),
                    "--home",
                    str(home),
                    "--out",
                    str(run_dir / "proofcheck-verdict.json"),
                ]
            )
            self.assertEqual(proofcheck.returncode, 0, proofcheck.stderr)
            proofcheck_payload = json.loads(proofcheck.stdout)
            self.assertEqual(proofcheck_payload["decision"], "pass")
            self.assertEqual(proofcheck_payload["verifier_command"], "team-ledger")
            self.assertEqual(proofcheck_payload["out"], str(run_dir / "proofcheck-verdict.json"))

            handoff = self._module_run(
                [
                    "orro",
                    "handoff",
                    str(run_dir),
                    "--out",
                    str(run_dir / "orro-handoff.json"),
                ]
            )
            self.assertEqual(handoff.returncode, 0, handoff.stderr)
            handoff_payload = json.loads(handoff.stdout)
            self.assertEqual(handoff_payload["kind"], "orro-handoff")
            self.assertFalse(handoff_payload["boundary"]["approves_merge"])
            self.assertFalse(handoff_payload["boundary"]["raises_assurance"])


if __name__ == "__main__":
    unittest.main()
