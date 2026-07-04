import io
import inspect
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import witnessd.__main__ as witnessd_cli
import witnessd.distribution as distribution
from witnessd.__main__ import main
from witnessd.distribution import ERR_WITNESSD_DEPONE_PIN_MISMATCH


def _seed_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "w18@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "w18"], cwd=repo, check=True)
    (repo / "README.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)


class W18DxCliTests(unittest.TestCase):
    def _run_ergonomic_goal(self, root: Path) -> tuple[Path, Path]:
        witnessd_root = Path(__file__).resolve().parents[1]
        depone_root = witnessd_root.parent / "depone"
        repo = root / "repo"
        home = root / "home"
        repo.mkdir()
        _seed_repo(repo)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(["init", "--home", str(home), "--depone-root", str(depone_root)]),
                0,
            )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "run",
                    "write two independent files",
                    "--repo",
                    str(repo),
                    "--home",
                    str(home),
                ]
            )
        self.assertEqual(code, 0, stderr.getvalue())
        return home, Path(json.loads(stdout.getvalue())["run_dir"])

    def test_run_goal_repo_syntax_after_goal_emits_parallel_team_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, run_dir = self._run_ergonomic_goal(root)
            payload = json.loads((run_dir / "team-ledger-verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(payload["lane_count"], 2)
            self.assertTrue((run_dir / "sealed-plan.json").is_file())
            self.assertTrue((run_dir / "team-ledger.json").is_file())
            self.assertTrue((run_dir / "team-ledger-verdict.json").is_file())

    def test_verify_run_dir_rederives_with_pinned_depone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir = self._run_ergonomic_goal(Path(tmp))
            (run_dir / "team-ledger-verdict.json").unlink()
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["verify", str(run_dir), "--home", str(home)])

            self.assertEqual(code, 0, stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["decision"], "pass")
            self.assertEqual(payload["team_ledger"], str(run_dir / "team-ledger.json"))
            self.assertTrue((run_dir / "team-ledger-verdict.json").is_file())

    def test_verify_run_dir_rejects_forged_depone_pin_before_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, run_dir = self._run_ergonomic_goal(Path(tmp))
            (run_dir / "team-ledger-verdict.json").unlink()
            provision_path = home / "provision.json"
            provision = json.loads(provision_path.read_text(encoding="utf-8"))
            provision["depone"]["commit"] = "0" * 40
            provision_path.write_text(
                json.dumps(provision, sort_keys=True), encoding="utf-8"
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["verify", str(run_dir), "--home", str(home)])

            self.assertEqual(code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn(ERR_WITNESSD_DEPONE_PIN_MISMATCH, stderr.getvalue())
            self.assertFalse((run_dir / "team-ledger-verdict.json").exists())

    def test_runtime_and_verify_paths_do_not_contain_network_provision_actions(self) -> None:
        runtime_sources = "\n".join(
            inspect.getsource(obj)
            for obj in (
                witnessd_cli._cmd_run_goal,
                witnessd_cli._cmd_verify,
                distribution.run_depone_team_ledger,
            )
        )
        forbidden_tokens = (
            "git clone",
            "git fetch",
            "pip install",
            "curl ",
            "http://",
            "https://",
            "allow_network",
            "init_witnessd_home",
        )
        for token in forbidden_tokens:
            self.assertNotIn(token, runtime_sources)

    def test_quickstart_script_and_ci_use_plain_python(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts" / "quickstart_check.sh"
        workflow = root / ".github" / "workflows" / "ci.yml"

        script_text = script.read_text(encoding="utf-8")
        workflow_text = workflow.read_text(encoding="utf-8")

        self.assertIn("python3", script_text)
        self.assertNotIn(" uv ", f" {script_text} ")
        self.assertNotIn("codex", script_text)
        self.assertIn("quickstart_check.sh", workflow_text)

    def test_session_guidance_requires_depone_verdict_without_success_tokens(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for rel in ("SKILL.md", "AGENTS.md"):
            text = (root / rel).read_text(encoding="utf-8")
            lowered = text.lower()
            self.assertIn("depone", lowered)
            self.assertIn("team-ledger-verdict.json", text)
            self.assertIn("decision", lowered)
            self.assertNotRegex(text, r"\b(DONE|VERIFIED|COMPLETE)\b")

    def test_readme_leads_with_quickstart_and_honest_limits(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        first_headings = [
            line.strip()
            for line in readme.splitlines()
            if line.startswith("## ")
        ][:3]
        self.assertIn("## 10-minute quickstart", first_headings[:1])
        self.assertIn("witnessd init", readme)
        self.assertIn("witnessd run", readme)
        self.assertIn("witnessd verify", readme)
        self.assertIn("honest limits", readme.lower())

    def test_release_and_operator_docs_cover_w18_checkpoints(self) -> None:
        root = Path(__file__).resolve().parents[1]
        release = (root / "docs" / "releases" / "v2.3.0-draft.md").read_text(
            encoding="utf-8"
        )
        operator = (
            root / "docs" / "ops" / "w18-operator-checkpoints.md"
        ).read_text(encoding="utf-8")

        self.assertIn("v2.3.0", release)
        self.assertIn("W18", release)
        self.assertIn("WITNESSD_REVERSE_CONFORMANCE_PAT", operator)
        self.assertIn("Contents: Read-only", operator)
        self.assertIn("Clean-machine quickstart", operator)


if __name__ == "__main__":
    unittest.main()
