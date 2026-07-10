"""Direct coverage for witnessd's own Codex local capability builder.

This behavior used to be tested through Depone's `codex_local_capability`
module. Depone's Phase 4 extraction (`Extract deprecated execution surfaces to
witnessd`) turned that module into a delegating shim with no logic of its own,
so the functional coverage moved here to test the canonical implementation
directly. See depone/docs/phase2-tcb-extraction.md.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from witnessd import codex_capability as capability
from witnessd.codex_capability import (
    ALLOWED_APPROVAL_POLICIES,
    CODEX_LOCAL_CAPABILITY_KIND,
    build_codex_local_capability,
    validate_codex_local_capability,
)


def _seed_repo(root: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "tester"], cwd=root, check=True)
    fake_codex = root / "codex"
    fake_codex.write_text("#!/bin/sh\nprintf 'codex 0.test\\n'\n", encoding="utf-8")
    fake_codex.chmod(0o755)
    subprocess.run(["git", "add", "codex"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
    return fake_codex


class CodexLocalCapabilityTests(unittest.TestCase):
    def test_missing_codex_binary_blocks_without_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            with patch("shutil.which", return_value=None):
                receipt = build_codex_local_capability(
                    repo=root,
                    codex_binary="codex",
                    sandbox_mode="workspace-write",
                    approval_policy="on-request",
                    instruction_files=[],
                )

        self.assertEqual(receipt["kind"], CODEX_LOCAL_CAPABILITY_KIND)
        self.assertEqual(receipt["decision"], "blocked")
        self.assertIn("codex binary not found", receipt["blocked_reasons"])
        self.assertEqual(
            receipt["readiness"]["version_probe"],
            {
                "executed": False,
                "argv": ["codex", "--version"],
                "exit_code": None,
                "timed_out": False,
                "stdout_present": False,
                "stderr_present": False,
                "sanitized_version_text": None,
                "unexpected_output": False,
                "error": "binary_not_found",
            },
        )
        self.assertFalse(receipt["boundary"]["launches_live_model"])
        self.assertFalse(receipt["boundary"]["executes_coding_task"])
        self.assertFalse(receipt["boundary"]["raises_assurance"])
        self.assertEqual(validate_codex_local_capability(receipt), [])

    def test_pass_receipt_records_version_repo_and_instruction_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_codex = _seed_repo(root)
            (root / "AGENTS.md").write_text("# contract\n", encoding="utf-8")
            subprocess.run(["git", "add", "AGENTS.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "add agents"], cwd=root, check=True)

            with patch("shutil.which", return_value=fake_codex.as_posix()):
                receipt = build_codex_local_capability(
                    repo=root,
                    codex_binary="codex",
                    sandbox_mode="workspace-write",
                    approval_policy="on-request",
                    instruction_files=[Path("AGENTS.md")],
                )

        self.assertEqual(receipt["decision"], "pass")
        self.assertEqual(receipt["adapter"]["version"], "codex 0.test")
        self.assertEqual(receipt["repo"]["dirty"], False)
        self.assertEqual(receipt["instruction_files"][0]["present"], True)
        self.assertEqual(validate_codex_local_capability(receipt), [])

    def test_version_probe_nonzero_exit_blocks_with_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "tester"], cwd=root, check=True
            )
            fake_codex = root / "codex"
            fake_codex.write_text(
                "#!/bin/sh\nprintf 'nope\\n' >&2\nexit 7\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            subprocess.run(["git", "add", "codex"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)

            with patch("shutil.which", return_value=fake_codex.as_posix()):
                receipt = build_codex_local_capability(repo=root)

        probe = receipt["readiness"]["version_probe"]
        self.assertEqual(receipt["decision"], "blocked")
        self.assertIn("codex version probe failed", receipt["blocked_reasons"])
        self.assertEqual(probe["exit_code"], 7)
        self.assertTrue(probe["stderr_present"])
        self.assertIsNone(probe["sanitized_version_text"])
        self.assertEqual(validate_codex_local_capability(receipt), [])

    def test_version_probe_timeout_blocks_without_raw_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "tester"], cwd=root, check=True
            )
            fake_codex = root / "codex"
            fake_codex.write_text("#!/bin/sh\nsleep 2\n", encoding="utf-8")
            fake_codex.chmod(0o755)
            subprocess.run(["git", "add", "codex"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)

            with patch("shutil.which", return_value=fake_codex.as_posix()):
                receipt = build_codex_local_capability(
                    repo=root,
                    version_timeout_seconds=0.01,
                )

        probe = receipt["readiness"]["version_probe"]
        self.assertEqual(receipt["decision"], "blocked")
        self.assertIn("codex version probe timed out", receipt["blocked_reasons"])
        self.assertTrue(probe["timed_out"])
        self.assertIsNone(probe["exit_code"])
        self.assertIsNone(probe["sanitized_version_text"])
        self.assertEqual(validate_codex_local_capability(receipt), [])

    def test_version_probe_unexpected_output_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "tester"], cwd=root, check=True
            )
            fake_codex = root / "codex"
            fake_codex.write_text(
                "#!/bin/sh\nprintf 'unexpected-marker\\nextra\\n'\n", encoding="utf-8"
            )
            fake_codex.chmod(0o755)
            subprocess.run(["git", "add", "codex"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)

            with patch("shutil.which", return_value=fake_codex.as_posix()):
                receipt = build_codex_local_capability(repo=root)

        probe = receipt["readiness"]["version_probe"]
        self.assertEqual(receipt["decision"], "blocked")
        self.assertIn(
            "codex version probe returned unexpected output", receipt["blocked_reasons"]
        )
        self.assertTrue(probe["unexpected_output"])
        self.assertIsNone(probe["sanitized_version_text"])
        self.assertNotIn("unexpected-marker", json.dumps(receipt))
        self.assertEqual(validate_codex_local_capability(receipt), [])

    def test_instruction_file_outside_repo_boundary_blocks_without_hashing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_codex = _seed_repo(root)

            with patch("shutil.which", return_value=fake_codex.as_posix()):
                receipt = build_codex_local_capability(
                    repo=root,
                    instruction_files=[Path("../outside.md")],
                )

        self.assertEqual(receipt["decision"], "blocked")
        self.assertIn(
            "instruction file path outside repo boundary", receipt["blocked_reasons"]
        )
        self.assertEqual(receipt["instruction_files"][0]["sha256"], None)
        self.assertEqual(
            receipt["instruction_files"][0]["blocked_reason"],
            "instruction file must be repo-relative",
        )
        self.assertEqual(validate_codex_local_capability(receipt), [])

    def test_dirty_repo_blocks_even_when_codex_binary_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "tester"], cwd=root, check=True
            )
            (root / "tracked.txt").write_text("before\n", encoding="utf-8")
            fake_codex = root / "codex"
            fake_codex.write_text(
                "#!/bin/sh\nprintf 'codex 0.test\\n'\n", encoding="utf-8"
            )
            fake_codex.chmod(0o755)
            subprocess.run(["git", "add", "tracked.txt", "codex"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
            (root / "tracked.txt").write_text("after\n", encoding="utf-8")

            with patch("shutil.which", return_value=fake_codex.as_posix()):
                receipt = build_codex_local_capability(repo=root)

        self.assertEqual(receipt["decision"], "blocked")
        self.assertIn("repo working tree is dirty", receipt["blocked_reasons"])
        self.assertEqual(validate_codex_local_capability(receipt), [])

    def test_invalid_receipt_validation_reports_hash_mismatch(self) -> None:
        receipt = {
            "kind": CODEX_LOCAL_CAPABILITY_KIND,
            "schema_version": "0.1",
            "decision": "pass",
            "blocked_reasons": [],
            "readiness": {
                "version_probe": {
                    "executed": True,
                    "argv": ["codex", "--version"],
                    "exit_code": 0,
                    "timed_out": False,
                    "stdout_present": True,
                    "stderr_present": False,
                    "sanitized_version_text": "codex 0.test",
                    "unexpected_output": False,
                    "error": None,
                },
            },
            "boundary": {
                "launches_live_model": False,
                "executes_coding_task": False,
                "captures_capability_only": True,
                "raises_assurance": False,
            },
            "agent_contract_hash": "wrong",
            "agent_contract": {"agent_contract_hash": "right"},
        }

        self.assertIn(
            "agent_contract_hash mismatch", validate_codex_local_capability(receipt)
        )

    def test_invalid_receipt_validation_requires_readiness(self) -> None:
        receipt = {
            "kind": CODEX_LOCAL_CAPABILITY_KIND,
            "schema_version": "0.1",
            "decision": "pass",
            "blocked_reasons": [],
            "boundary": {
                "launches_live_model": False,
                "executes_coding_task": False,
                "captures_capability_only": True,
                "raises_assurance": False,
            },
            "agent_contract_hash": "same",
            "agent_contract": {"agent_contract_hash": "same"},
        }

        self.assertIn(
            "readiness must be an object", validate_codex_local_capability(receipt)
        )

    def test_invalid_receipt_validation_reports_malformed_version_probe(self) -> None:
        receipt = {
            "kind": CODEX_LOCAL_CAPABILITY_KIND,
            "schema_version": "0.1",
            "decision": "pass",
            "blocked_reasons": [],
            "readiness": {
                "version_probe": {
                    "executed": "yes",
                    "argv": ["/home/user/.codex/bin/codex", "--version"],
                    "exit_code": "0",
                    "timed_out": "no",
                    "stdout_present": "yes",
                    "stderr_present": "no",
                    "sanitized_version_text": "unexpected-marker\ncodex 0.test",
                    "unexpected_output": "no",
                },
            },
            "boundary": {
                "launches_live_model": False,
                "executes_coding_task": False,
                "captures_capability_only": True,
                "raises_assurance": False,
            },
            "agent_contract_hash": "same",
            "agent_contract": {"agent_contract_hash": "same"},
        }

        errors = validate_codex_local_capability(receipt)

        self.assertIn("readiness.version_probe.executed must be boolean", errors)
        self.assertIn(
            "readiness.version_probe.argv must be sanitized codex --version", errors
        )
        self.assertIn("readiness.version_probe.exit_code must be int or null", errors)
        self.assertIn(
            "readiness.version_probe.sanitized_version_text is invalid", errors
        )

    def test_qa03_instruction_symlink_escape_blocks_without_hashing_target(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as repo_dir,
            tempfile.TemporaryDirectory() as outside_dir,
        ):
            repo = Path(repo_dir)
            fake_codex = _seed_repo(repo)
            outside = Path(outside_dir) / "outside.md"
            outside.write_text("outside secret instructions", encoding="utf-8")
            (repo / "AGENTS.md").symlink_to(outside)

            with patch("shutil.which", return_value=fake_codex.as_posix()):
                receipt = build_codex_local_capability(
                    repo=repo,
                    instruction_files=[Path("AGENTS.md")],
                )

        self.assertEqual(receipt["decision"], "blocked")
        blocked_reasons = cast(list[str], receipt["blocked_reasons"])
        self.assertIn(
            "instruction file path outside repo boundary",
            blocked_reasons,
        )
        instruction_files = cast(list[dict[str, object]], receipt["instruction_files"])
        instruction = instruction_files[0]
        self.assertFalse(instruction["present"])
        self.assertIsNone(instruction["sha256"])
        self.assertIn("blocked_reason", instruction)
        self.assertEqual(validate_codex_local_capability(receipt), [])

    def test_qa04_git_probe_unknown_never_reports_clean(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo = Path(repo_dir)

            def fake_run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
                if argv[-2:] == ["rev-parse", "--show-toplevel"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=str(repo) + "\n",
                        stderr="",
                    )
                return SimpleNamespace(returncode=128, stdout="", stderr="fatal")

            with patch.object(capability.subprocess, "run", side_effect=fake_run):
                facts = capability._git_facts(repo)

        self.assertTrue(facts["is_git_worktree"])
        self.assertNotEqual(facts.get("dirty"), False)
        self.assertIn("error", facts)

    def test_git_probe_timeout_never_reports_clean(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo = Path(repo_dir)

            with patch.object(
                capability.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["git", "status"], 10),
            ):
                facts = capability._git_facts(repo)

        self.assertFalse(facts["is_git_worktree"])
        self.assertNotEqual(facts.get("dirty"), False)
        probe_errors = cast(list[str], facts["probe_errors"])
        self.assertIn("git status unknown", probe_errors)

    def test_qa06_current_codex_approval_policy_untrusted_is_supported(self) -> None:
        self.assertIn("untrusted", ALLOWED_APPROVAL_POLICIES)


if __name__ == "__main__":
    unittest.main()
