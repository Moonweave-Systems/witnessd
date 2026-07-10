import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import depone
from depone.agent_fabric.codex_local_capability import (
    build_codex_local_capability as depone_codex_capability,
)
from depone.agent_fabric.evidence_substrate import build_otel_genai_spans
from depone.agent_fabric.isolation import (
    UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
    probe_isolation_facts,
    verify_isolation_boundary,
)
from depone.agent_fabric.observer_provenance import (
    build_signed_trusted_observer_provenance as depone_provenance,
)
from depone.agent_fabric.paired_run import VALID_RUNNERS as DEPONE_VALID_RUNNERS
from depone.agent_fabric.reference_adapter import (
    build_reference_adapter_fixture as depone_reference_fixture,
)

from witnessd.adapters.base import VALID_RUNNERS
from witnessd.codex_capability import build_codex_local_capability
from witnessd.fixture import build_reference_adapter_fixture
from witnessd.isolation import (
    probe_lane_isolation,
    verify_isolation_boundary as witnessd_verify_isolation_boundary,
)
from witnessd.provenance import build_signed_trusted_observer_provenance
from witnessd.signing import gen_operator_keypair
from witnessd.substrate import build_otel_spans

DEPONE_ROOT = Path(
    os.environ.get("WITNESSD_DEPONE_ROOT", Path(depone.__file__).resolve().parents[1])
)


@contextmanager
def _cwd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class TestDeponeReplicaConformance(unittest.TestCase):
    def test_valid_runners_match_depone_contract(self):
        self.assertEqual(VALID_RUNNERS, DEPONE_VALID_RUNNERS)

    def test_reference_adapter_fixture_matches_depone(self):
        invocation = {
            "packet_version": "1.0",
            "target_harness": "codex",
            "profile": "w4-adapter-run",
            "role": "runner",
        }
        self.assertEqual(
            build_reference_adapter_fixture(invocation),
            depone_reference_fixture(invocation),
        )

    def test_isolation_decisions_match_depone(self):
        cases = [
            {
                "runner_uid": 1001,
                "observer_uid": 1002,
                "observer_dir_writable_by_runner": False,
            },
            {
                "runner_uid": 1001,
                "observer_uid": 1001,
                "observer_dir_writable_by_runner": False,
            },
            {
                "model": UID_OBSERVER_LAUNCHED_ISOLATION_MODEL,
                "runner_uid": 1001,
                "observer_uid": 1002,
                "observer_dir_writable_by_runner": False,
                "observer_launched": True,
            },
            {"runner_uid": 1001},
            {"model": "unknown"},
        ]

        for facts in cases:
            self.assertEqual(
                witnessd_verify_isolation_boundary(facts),
                verify_isolation_boundary(facts),
            )

    def test_isolation_probe_matches_depone(self):
        with tempfile.TemporaryDirectory() as tmp:
            witnessd_facts = probe_lane_isolation(observer_dir=tmp, runner_uid=999999)
            depone_facts = probe_isolation_facts(Path(tmp), runner_uid=999999)
            expected_mode = f"{stat.S_IMODE(os.stat(tmp).st_mode):04o}"

        shared = {
            "runner_uid",
            "observer_uid",
            "observer_dir_writable_by_runner",
        }
        self.assertEqual(
            {key: witnessd_facts[key] for key in shared},
            {key: depone_facts[key] for key in shared},
        )
        self.assertEqual(witnessd_facts["observer_dir_mode"], expected_mode)

    def test_otel_spans_match_depone(self):
        manifest = {
            "assurance": "A1-local-observed",
            "decision": "observed-local-capture",
            "observer_capture": {
                "command_receipts": [
                    {
                        "command": ["sh", "-c", "true"],
                        "exit_code": 0,
                        "status": "passed",
                    }
                ]
            },
        }
        receipt = {"runner_kind": "codex-cli", "arm": "direct", "task_id": "t"}

        self.assertEqual(
            build_otel_spans(manifest, runner_receipt=receipt),
            build_otel_genai_spans(manifest, runner_receipt=receipt),
        )

    @unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
    def test_signed_provenance_matches_depone(self):
        manifest = {
            "kind": "agent-fabric-capture-manifest",
            "observer_capture_hash": "abc",
        }
        with tempfile.TemporaryDirectory() as tmp:
            private_key, _public_key = gen_operator_keypair(tmp)
            self.assertEqual(
                build_signed_trusted_observer_provenance(
                    manifest,
                    evidence_path="/tmp/capture-manifest.json",
                    private_key_path=private_key,
                    key_id="k",
                ),
                depone_provenance(
                    manifest,
                    evidence_path="/tmp/capture-manifest.json",
                    private_key_path=private_key,
                    key_id="k",
                ),
            )

    def test_codex_capability_matches_depone_for_missing_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "AGENTS.md").write_text("# test\n", encoding="utf-8")
            kwargs = {
                "repo": repo,
                "codex_binary": "definitely-missing-codex",
                "instruction_files": [Path("AGENTS.md")],
            }

            with _cwd(DEPONE_ROOT):
                depone_receipt = depone_codex_capability(**kwargs)

            self.assertEqual(build_codex_local_capability(**kwargs), depone_receipt)

    def test_codex_capability_blocks_when_git_head_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

            receipt = build_codex_local_capability(
                repo=repo,
                codex_binary="definitely-missing-codex",
            )

            self.assertIn("git HEAD unknown", receipt["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
