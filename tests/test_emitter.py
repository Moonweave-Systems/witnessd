import hashlib
import json
import os
import shutil
import tempfile
import unittest

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle
from depone.agent_fabric.observer_provenance import (
    validate_trusted_observer_provenance,
)
from depone.agent_fabric.reference_adapter import build_reference_adapter_fixture

from witnessd.adapters.shell import run_shell_lane
from witnessd.emitter import EmitterError, emit_lane_evidence

ARTIFACT_NAMES = {
    "capture-manifest.json",
    "observer-capture.json",
    "runner-receipt.json",
    "run-intent.json",
    "bundle.json",
    "evidence-contract.json",
    "observed-touched-files.txt",
    "git-diff.patch",
    "exit-code.txt",
    "provenance.json",
    "verify.log",
}


def _fixture() -> dict:
    invocation = {
        "packet_version": "1.0",
        "target_harness": "shell",
        "profile": "w1-task11",
        "role": "runner",
        "toolbelt": {
            "allowed_tools": ["cat", "python3"],
            "allowed_mcp": [],
            "forbidden_tools": ["write"],
            "context_policy": "local-code-only",
            "output_schema": "runner-result-v1",
            "evidence_obligations": ["command_receipt"],
        },
        "instructions": "Run checks and report outputs.",
        "evidence_obligations": ["command_receipt"],
        "context_policy": "local-code-only",
    }
    return build_reference_adapter_fixture(invocation)


def _sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestEmitter(unittest.TestCase):
    def _emit(self, tmp: str):
        from witnessd.signing import gen_operator_keypair

        sandbox = os.path.join(tmp, "sandbox")
        evidence_dir = os.path.join(tmp, "evidence")
        keydir = os.path.join(tmp, "keys")  # OUT of evidence_dir
        os.makedirs(sandbox)
        os.makedirs(keydir)
        priv, pub = gen_operator_keypair(keydir)

        lane = run_shell_lane(
            sandbox=sandbox,
            commands=[["sh", "-c", "echo hi > f.txt"]],
            test_command=["sh", "-c", "true"],
        )
        result = emit_lane_evidence(
            lane,
            evidence_dir,
            priv,
            fixture=_fixture(),
            allowed_touched_files=["f.txt"],
            public_key_path=pub,
            task_id="w1-task11",
            invocation=["sh", "-c", "echo hi > f.txt"],
            runner_sandbox=sandbox,
        )
        return evidence_dir, pub, result

    def test_full_artifact_set_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir, _pub, _result = self._emit(tmp)
            on_disk = set(os.listdir(evidence_dir))
            self.assertTrue(ARTIFACT_NAMES.issubset(on_disk))

    def test_trusted_observer_provenance_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            _evidence_dir, pub, result = self._emit(tmp)
            manifest = result["manifest"]
            errors = validate_trusted_observer_provenance(
                manifest,
                evidence_path=result["provenance"]["evidence_path"],
                provenance=[result["provenance"]],
                public_key_path=pub,
            )
            self.assertEqual(errors, [])

    def test_trusted_observer_provenance_binds_relative_manifest_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            _evidence_dir, pub, result = self._emit(tmp)
            manifest = result["manifest"]
            provenance = result["provenance"]

            self.assertEqual(provenance["evidence_path"], "capture-manifest.json")
            errors = validate_trusted_observer_provenance(
                manifest,
                evidence_path="capture-manifest.json",
                provenance=[provenance],
                public_key_path=pub,
            )
            self.assertEqual(errors, [])

    def test_manifest_is_a1_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            _evidence_dir, _pub, result = self._emit(tmp)
            manifest = result["manifest"]
            self.assertEqual(validate_capture_manifest(manifest), [])
            self.assertEqual(manifest["assurance"], "A1-local-observed")

    def test_signed_bundle_ingests(self):
        with tempfile.TemporaryDirectory() as tmp:
            _evidence_dir, pub, result = self._emit(tmp)
            bundle = result["bundle"]
            verdict = ingest_signed_evidence_bundle(
                bundle, pub, result["artifacts"], otel_spans=bundle["otel_spans"]
            )
            self.assertTrue(verdict["signature_verified"])
            self.assertEqual(verdict["decision"], "pass")

    def test_every_artifact_routed_through_eventlog(self):
        # No file may reach the evidence dir except through the EventLog SoT:
        # every emitted artifact must have a runlog event whose content hash
        # matches the on-disk bytes, and every event must map to a real file.
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir, _pub, result = self._emit(tmp)
            events = result["runlog"]
            artifact_events = [e for e in events if e.get("event") == "emit-artifact"]
            self.assertTrue(artifact_events)

            covered = {}
            for event in artifact_events:
                name = event["artifact"]
                path = os.path.join(evidence_dir, name)
                self.assertTrue(os.path.exists(path))
                self.assertEqual(event["content_sha256"], _sha256_file(path))
                covered[name] = event

            # Every non-log, non-key file present on disk was emitter-written.
            on_disk = {
                name for name in os.listdir(evidence_dir) if name != "runlog.jsonl"
            }
            self.assertEqual(on_disk, set(covered))

    def test_runlog_hash_chained_from_genesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            _evidence_dir, _pub, result = self._emit(tmp)
            events = result["runlog"]
            self.assertIsNone(events[0]["prev_event_hash"])
            for prev, cur in zip(events, events[1:]):
                self.assertEqual(cur["prev_event_hash"], prev["event_hash"])

    def test_runlog_persisted_and_covers_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir, _pub, result = self._emit(tmp)
            runlog_path = os.path.join(evidence_dir, "runlog.jsonl")
            self.assertTrue(os.path.exists(runlog_path))
            with open(runlog_path, encoding="utf-8") as handle:
                lines = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(len(lines), len(result["runlog"]))

    def test_public_key_inside_evidence_dir_refused(self):
        # Fail closed: the trust root must live out-of-band, never inside the
        # runner-reachable evidence dir. No artifacts on refusal.
        with tempfile.TemporaryDirectory() as tmp:
            from witnessd.signing import gen_operator_keypair

            sandbox = os.path.join(tmp, "sandbox")
            evidence_dir = os.path.join(tmp, "evidence")
            os.makedirs(sandbox)
            os.makedirs(evidence_dir)
            inside_keys = os.path.join(evidence_dir, "keys")
            os.makedirs(inside_keys)
            priv, pub = gen_operator_keypair(inside_keys)
            lane = run_shell_lane(sandbox=sandbox, commands=[["sh", "-c", "true"]])
            with self.assertRaises(EmitterError):
                emit_lane_evidence(
                    lane,
                    evidence_dir,
                    priv,
                    fixture=_fixture(),
                    allowed_touched_files=[],
                    public_key_path=pub,
                    runner_sandbox=sandbox,
                )
            self.assertFalse(os.path.exists(os.path.join(evidence_dir, "bundle.json")))

    def test_missing_runtime_sandbox_raises_stable_emitter_error(self):
        from witnessd.signing import gen_operator_keypair

        with tempfile.TemporaryDirectory() as tmp:
            sandbox = os.path.join(tmp, "sandbox")
            evidence_dir = os.path.join(tmp, "evidence")
            keydir = os.path.join(tmp, "keys")
            os.makedirs(sandbox)
            os.makedirs(keydir)
            priv, pub = gen_operator_keypair(keydir)
            lane = run_shell_lane(
                sandbox=sandbox,
                commands=[["sh", "-c", "true"]],
            )

            with self.assertRaisesRegex(
                EmitterError, "^ERR_RUNTIME_SANDBOX_UNAVAILABLE$"
            ):
                emit_lane_evidence(
                    lane,
                    evidence_dir,
                    priv,
                    fixture=_fixture(),
                    allowed_touched_files=[],
                    public_key_path=pub,
                    runner_sandbox="path:redacted-sandbox",
                    runtime_sandbox=os.path.join(tmp, "missing-sandbox"),
                )

            self.assertFalse(os.path.exists(evidence_dir))


if __name__ == "__main__":
    unittest.main()
