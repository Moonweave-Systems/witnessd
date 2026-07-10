"""Direct coverage for witnessd's own shell lane command adapter.

This behavior used to be tested through Depone's `team_shell_lane_launch`
module. Depone's Phase 4 extraction (`Extract deprecated execution surfaces to
witnessd`) turned that module into a delegating shim with no logic of its own,
so the functional coverage moved here to test the canonical implementation
directly. See depone/docs/phase2-tcb-extraction.md.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from depone.agent_fabric.agent_operating_contract import build_agent_operating_contract

from witnessd.team_shell_lane_launch import (
    TEAM_SHELL_LANE_LAUNCH_KIND,
    TeamShellLaneLaunchError,
    _canonical_hash,
    run_shell_lane_command,
)


class TeamShellLaneLaunchTests(unittest.TestCase):
    def test_allowlisted_argv_command_writes_receipt_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            allowlist = {
                "commands": [
                    {
                        "id": "hello",
                        "argv": [sys.executable, "-c", "print('hello shell lane')"],
                    }
                ]
            }
            receipt = run_shell_lane_command(
                allowlist=allowlist,
                command_id="hello",
                cwd=root,
                transcript_path=root / "transcript.json",
                timeout_seconds=30,
            )

            self.assertEqual(receipt["kind"], TEAM_SHELL_LANE_LAUNCH_KIND)
            self.assertEqual(receipt["decision"], "pass")
            self.assertEqual(receipt["exit_code"], 0)
            self.assertEqual(receipt["argv"][1:], ["-c", "print('hello shell lane')"])
            self.assertIn("stdout_sha256", receipt)
            self.assertIn("stderr_sha256", receipt)
            self.assertEqual(receipt["allowlist_sha256"], _canonical_hash(allowlist))
            self.assertIsInstance(receipt["agent_contract_hash"], str)
            self.assertEqual(
                receipt["agent_contract_hash"],
                receipt["agent_contract"]["agent_contract_hash"],
            )
            self.assertEqual(receipt["agent_contract"]["role_id"], "worker")
            self.assertEqual(
                receipt["agent_contract"]["role_registry_path"],
                "packaging/dwm-roles.json",
            )
            self.assertTrue(Path(str(receipt["transcript_path"])).exists())
            self.assertFalse(receipt["boundary"]["uses_shell"])
            self.assertTrue(receipt["boundary"]["uses_argv_allowlist"])
            self.assertFalse(receipt["boundary"]["allows_arbitrary_shell_string"])
            self.assertFalse(receipt["boundary"]["raises_assurance"])
            transcript = json.loads(
                Path(str(receipt["transcript_path"])).read_text(encoding="utf-8")
            )
            self.assertEqual(transcript["stdout_text"], "hello shell lane\n")
            self.assertEqual(
                receipt["stdout_sha256"],
                hashlib.sha256(transcript["stdout_text"].encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                receipt["stderr_sha256"],
                hashlib.sha256(transcript["stderr_text"].encode("utf-8")).hexdigest(),
            )

    def test_agent_contract_hash_binds_common_contract_and_v22_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            role = {
                "id": "worker",
                "purpose": "test worker",
                "allowed_tools": ["read"],
                "output_schema": "worker-result-v1",
                "evidence_obligations": ["files", "commands", "tests"],
                "trust_boundary": "untrusted until reviewed",
            }
            registry = {"roles": [role]}
            contract = build_agent_operating_contract(registry)
            contract_path = root / "contract.json"
            registry_path = root / "roles.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            registry_path.write_text(json.dumps(registry), encoding="utf-8")

            receipt = run_shell_lane_command(
                allowlist={
                    "commands": [
                        {
                            "id": "hello",
                            "argv": [sys.executable, "-c", "print('contract hash')"],
                        }
                    ]
                },
                command_id="hello",
                cwd=root,
                transcript_path=root / "transcript.json",
                timeout_seconds=30,
                agent_role_id="worker",
                agent_contract_path=contract_path,
                role_registry_path=registry_path,
            )

            self.assertEqual(
                receipt["agent_contract_hash"], contract["agent_contract_hash"]
            )
            self.assertEqual(
                receipt["agent_contract"]["agent_contract_hash"],
                contract["agent_contract_hash"],
            )
            self.assertEqual(
                receipt["agent_contract"]["role_registry_sha256"],
                contract["role_registry"]["sha256"],
            )
            self.assertEqual(receipt["agent_contract"]["role_id"], "worker")

    def test_invalid_agent_contract_is_blocked_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "contract.json"
            registry_path = root / "roles.json"
            contract_path.write_text(
                json.dumps({"contract_id": "missing identity"}), encoding="utf-8"
            )
            registry_path.write_text(
                json.dumps(
                    {
                        "roles": [
                            {
                                "id": "worker",
                                "purpose": "test worker",
                                "allowed_tools": ["read"],
                                "output_schema": "worker-result-v1",
                                "evidence_obligations": ["files", "commands", "tests"],
                                "trust_boundary": "untrusted until reviewed",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(TeamShellLaneLaunchError) as raised:
                run_shell_lane_command(
                    allowlist={
                        "commands": [
                            {"id": "hello", "argv": [sys.executable, "--version"]}
                        ]
                    },
                    command_id="hello",
                    cwd=root,
                    transcript_path=root / "transcript.json",
                    agent_role_id="worker",
                    agent_contract_path=contract_path,
                    role_registry_path=registry_path,
                )

            self.assertEqual(
                raised.exception.code, "ERR_TEAM_SHELL_LANE_AGENT_CONTRACT_INVALID"
            )
            self.assertFalse((root / "transcript.json").exists())

    def test_unknown_agent_role_is_blocked_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "contract.json"
            registry_path = root / "roles.json"
            worker_role = {
                "id": "worker",
                "purpose": "test worker",
                "allowed_tools": ["read"],
                "output_schema": "worker-result-v1",
                "evidence_obligations": ["files", "commands", "tests"],
                "trust_boundary": "untrusted until reviewed",
            }
            registry_path.write_text(
                json.dumps({"roles": [worker_role]}),
                encoding="utf-8",
            )
            contract_path.write_text(
                json.dumps(build_agent_operating_contract({"roles": [worker_role]})),
                encoding="utf-8",
            )

            with self.assertRaises(TeamShellLaneLaunchError) as raised:
                run_shell_lane_command(
                    allowlist={
                        "commands": [
                            {"id": "hello", "argv": [sys.executable, "--version"]}
                        ]
                    },
                    command_id="hello",
                    cwd=root,
                    transcript_path=root / "transcript.json",
                    agent_role_id="operator",
                    agent_contract_path=contract_path,
                    role_registry_path=registry_path,
                )

            self.assertEqual(
                raised.exception.code, "ERR_TEAM_SHELL_LANE_AGENT_ROLE_INVALID"
            )
            self.assertFalse((root / "transcript.json").exists())

    def test_unknown_command_id_is_blocked_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TeamShellLaneLaunchError) as raised:
                run_shell_lane_command(
                    allowlist={
                        "commands": [
                            {"id": "known", "argv": [sys.executable, "--version"]}
                        ]
                    },
                    command_id="missing",
                    cwd=Path(tmp),
                    transcript_path=Path(tmp) / "transcript.json",
                )

            self.assertEqual(
                raised.exception.code, "ERR_TEAM_SHELL_LANE_COMMAND_NOT_ALLOWED"
            )
            self.assertFalse((Path(tmp) / "transcript.json").exists())

    def test_agent_executables_are_blocked_even_when_allowlisted(self) -> None:
        for executable in ("codex", "claude", "claude-code", "opencode"):
            with self.subTest(executable=executable):
                with tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaises(TeamShellLaneLaunchError) as raised:
                        run_shell_lane_command(
                            allowlist={
                                "commands": [
                                    {
                                        "id": "agent",
                                        "argv": [executable, "--version"],
                                    }
                                ]
                            },
                            command_id="agent",
                            cwd=Path(tmp),
                            transcript_path=Path(tmp) / "transcript.json",
                        )

                    self.assertEqual(
                        raised.exception.code,
                        "ERR_TEAM_SHELL_LANE_AGENT_EXECUTABLE_BLOCKED",
                    )
                    self.assertFalse((Path(tmp) / "transcript.json").exists())

    def test_interpreter_and_wrapper_trampolines_cannot_smuggle_agents(self) -> None:
        bypass_argvs = [
            ["bash", "-c", "codex --version"],
            ["sh", "-c", "codex exec do-work"],
            [sys.executable, "-c", "import os; os.system('codex')"],
            ["env", "codex", "--version"],
            ["npx", "codex", "--version"],
        ]
        for argv in bypass_argvs:
            with self.subTest(argv=argv):
                with tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaises(TeamShellLaneLaunchError) as raised:
                        run_shell_lane_command(
                            allowlist={"commands": [{"id": "bypass", "argv": argv}]},
                            command_id="bypass",
                            cwd=Path(tmp),
                            transcript_path=Path(tmp) / "transcript.json",
                        )

                    self.assertEqual(
                        raised.exception.code,
                        "ERR_TEAM_SHELL_LANE_AGENT_EXECUTABLE_BLOCKED",
                    )
                    self.assertFalse((Path(tmp) / "transcript.json").exists())

    def test_receipt_binds_uses_shell_to_actual_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = run_shell_lane_command(
                allowlist={
                    "commands": [{"id": "sh", "argv": ["bash", "-c", "printf hi"]}]
                },
                command_id="sh",
                cwd=root,
                transcript_path=root / "transcript.json",
                timeout_seconds=30,
            )

            self.assertEqual(receipt["decision"], "pass")
            self.assertTrue(receipt["boundary"]["uses_shell"])
            self.assertFalse(receipt["boundary"]["launches_agents"])
            self.assertEqual(receipt["deprecation"]["migration_target"], "witnessd")


if __name__ == "__main__":
    unittest.main()
