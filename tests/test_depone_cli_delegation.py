"""End-to-end coverage for Depone's deprecated CLI shims delegating to witnessd.

`depone codex-local-capability`, `depone team-shell-lane-launch`, and `depone
team-worktree-prep` are compatibility surfaces that Depone kept after its Phase
4 extraction (`Extract deprecated execution surfaces to witnessd`); they raise
`ERR_DEPONE_EXECUTION_SURFACE_MOVED_TO_WITNESSD` when witnessd is unavailable,
so Depone's own standalone test suite cannot exercise the delegated behavior.
witnessd is the one repo where both sides are guaranteed present, so the
end-to-end CLI path is tested here instead. See
depone/docs/phase2-tcb-extraction.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DeponeCliDelegationTests(unittest.TestCase):
    def test_codex_local_capability_self_test(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "depone", "codex-local-capability", "--self-test"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("codex-local-capability --self-test: pass", completed.stdout)

    def test_codex_local_capability_writes_pass_receipt_for_fake_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "tester"], cwd=root, check=True
            )
            (root / "AGENTS.md").write_text("# contract\n", encoding="utf-8")
            fake_codex = root / "codex"
            fake_codex.write_text(
                "#!/bin/sh\nprintf 'codex 0.cli\\n'\n", encoding="utf-8"
            )
            fake_codex.chmod(0o755)
            subprocess.run(["git", "add", "AGENTS.md", "codex"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
            out = base / "capability.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "depone",
                    "codex-local-capability",
                    "--repo",
                    str(root),
                    "--codex-binary",
                    str(fake_codex),
                    "--instruction-file",
                    "AGENTS.md",
                    "--out",
                    str(out),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            receipt = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "pass")
        self.assertEqual(receipt["decision"], "pass")
        self.assertEqual(receipt["adapter"]["version"], "codex 0.cli")

    def test_team_shell_lane_launch_self_test(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "depone", "team-shell-lane-launch", "--self-test"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("team-shell-lane-launch --self-test: pass", completed.stdout)

    def test_team_shell_lane_launch_runs_allowlisted_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            allowlist = root / "allowlist.json"
            receipt_path = root / "receipt.json"
            transcript_path = root / "transcript.json"
            allowlist.write_text(
                json.dumps(
                    {
                        "commands": [
                            {
                                "id": "fixture-echo",
                                "argv": [sys.executable, "-c", "print('fixture ok')"],
                            }
                        ]
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "depone",
                    "team-shell-lane-launch",
                    "--allowlist",
                    str(allowlist),
                    "--command-id",
                    "fixture-echo",
                    "--cwd",
                    str(root),
                    "--out",
                    str(receipt_path),
                    "--transcript",
                    str(transcript_path),
                    "--agent-role-id",
                    "worker",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            stdout = json.loads(completed.stdout)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(stdout["decision"], "pass")
            self.assertEqual(receipt["boundary"]["uses_shell"], False)

    def test_team_worktree_prep_self_test(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "depone", "team-worktree-prep", "--self-test"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("team-worktree-prep --self-test: pass", completed.stdout)


if __name__ == "__main__":
    unittest.main()
