from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from witnessd.cli.verify import _derive_command_lane_health


class CommandLaneHealthDerivationTest(unittest.TestCase):
    def test_derives_v111_health_conformance_from_recorded_gate_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp)
            health_dir = evidence_dir / "health"
            health_dir.mkdir()
            gates = [
                {
                    "gate": "format",
                    "tool": "black",
                    "enforcement": "block",
                    "expected_exit_code": 0,
                    "exit_code_path": "health/00-format.exit",
                    "log_path": "health/00-format.log",
                },
                {
                    "gate": "complexity",
                    "tool": "ruff-c901",
                    "enforcement": "advisory",
                    "expected_exit_code": 0,
                    "exit_code_path": "health/01-complexity.exit",
                    "log_path": "health/01-complexity.log",
                },
            ]
            (health_dir / "gates.json").write_text(
                json.dumps({"gates": gates}), encoding="utf-8"
            )
            (health_dir / "00-format.exit").write_text("0\n", encoding="utf-8")
            (health_dir / "00-format.log").write_text("clean\n", encoding="utf-8")
            (health_dir / "01-complexity.exit").write_text("1\n", encoding="utf-8")
            (health_dir / "01-complexity.log").write_text("C901\n", encoding="utf-8")

            health, source = _derive_command_lane_health(
                evidence_dir=evidence_dir,
                env=dict(os.environ),
            )

            self.assertEqual(health["overall"], "fail")
            self.assertEqual(health["axes"][0]["status"], "pass")
            self.assertEqual(health["axes"][1]["status"], "fail")
            self.assertIs(health["axes"][1]["blocks_handoff"], False)
            contract = json.loads(
                (
                    evidence_dir
                    / "depone-health-verification"
                    / "evidence-contract.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                contract,
                {"schema_version": "v111.code_health", "code_health": {"gates": gates}},
            )
            self.assertEqual(source["verifier"], "Depone")


if __name__ == "__main__":
    unittest.main()
