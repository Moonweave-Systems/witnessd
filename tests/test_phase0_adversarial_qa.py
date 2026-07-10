from __future__ import annotations

import json
import os
import stat
import time
import tempfile
import unittest
from pathlib import Path

from witnessd.adapters.codex import CodexAdapterError, run_codex_lane
from witnessd.adapters.shell import _diff_touched, _snapshot
from witnessd.canonical import canonical_hash
from witnessd.eventlog import EventLog
from witnessd.runlog import verify_runlog
from witnessd.signing import gen_operator_keypair


def _fake_codex(directory: str) -> str:
    path = Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "saw_json=0\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--json\" ]; then saw_json=1; shift; continue; fi\n"
        "  shift\n"
        "done\n"
        "cat >/dev/null\n"
        "rm -f delete-me.txt\n"
        "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"T1\"}'\n"
        "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"command_execution\",\"command\":\"rm delete-me.txt\"}}'\n"
        "if [ \"$saw_json\" -ne 1 ]; then exit 9; fi\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class Phase0AdversarialQaTests(unittest.TestCase):
    def test_qa01_shell_deleted_file_detection(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            victim = Path(sandbox) / "victim.txt"
            victim.write_text("secret", encoding="utf-8")
            before = _snapshot(sandbox)
            victim.unlink()
            after = _snapshot(sandbox)

        self.assertIn("victim.txt", _diff_touched(before, after))

    def test_qa02_shell_same_size_same_mtime_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            target = Path(sandbox) / "same.txt"
            target.write_bytes(b"AAAA")
            stat_result = target.stat()
            before = _snapshot(sandbox)
            target.write_bytes(b"BBBB")
            os.utime(
                target,
                ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns),
            )
            after = _snapshot(sandbox)

        self.assertIn("same.txt", _diff_touched(before, after))

    def test_qa05_operator_keypair_refuses_accidental_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as key_dir:
            private_path, public_path = gen_operator_keypair(key_dir)
            public_key = Path(public_path).read_bytes()

            second_private_path, second_public_path = gen_operator_keypair(key_dir)

            self.assertEqual(private_path, str(Path(key_dir) / "operator-ed25519.pem"))
            self.assertEqual(second_private_path, private_path)
            self.assertEqual(second_public_path, public_path)
            self.assertEqual(public_key, Path(public_path).read_bytes())

    def test_qa07_eventlog_rejects_append_after_chain_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            runlog = Path(sandbox) / "runlog.jsonl"
            log = EventLog(str(runlog))
            log.append({"kind": "witnessd-runlog-event", "event": "original"})
            record = json.loads(runlog.read_text(encoding="utf-8").strip())
            record["event"] = "tampered"
            runlog.write_text(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(Exception):
                log.append({"kind": "witnessd-runlog-event", "event": "after-tamper"})

            records = [
                json.loads(line)
                for line in runlog.read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual(len(records), 1)
            self.assertNotEqual(
                records[0]["event_hash"],
                canonical_hash(
                    {
                        key: value
                        for key, value in records[0].items()
                        if key != "event_hash"
                    }
                ),
            )

    def test_qa08_eventlog_scaling_warning_is_visible(self) -> None:
        bench = []
        for event_count in (8, 16, 32):
            with tempfile.TemporaryDirectory() as sandbox:
                path = Path(sandbox) / "runlog.jsonl"
                log = EventLog(str(path))
                started = time.perf_counter()
                for index in range(event_count):
                    log.append(
                        {
                            "kind": "witnessd-runlog-event",
                            "event": "qa08-scaling-probe",
                            "index": index,
                        }
                    )
                elapsed = time.perf_counter() - started
                records = EventLog(str(path)).read()
                checkpoint_exists = os.path.exists(EventLog(str(path))._checkpoint_path())
            self.assertEqual(len(records), event_count)
            self.assertEqual(verify_runlog(records), {"ok": True, "broken_at": None})
            bench.append(
                {
                    "events": event_count,
                    "seconds": round(elapsed, 6),
                    "us_per_append": round(elapsed / event_count * 1_000_000, 2),
                    "checkpoint": checkpoint_exists,
                }
            )

        result = {
            "case": "QA-08 EventLog append scaling",
            "expected_secure_behavior": "near O(1) append or bounded checkpoint verification",
            "observed": {
                "bench": bench,
                "checkpoint": all(row["checkpoint"] for row in bench),
            },
            "finding": "PASS" if all(row["checkpoint"] for row in bench) else "WARN",
        }
        self.assertEqual(result["finding"], "PASS")
        self.assertEqual([row["events"] for row in bench], [8, 16, 32])

    def test_phase0_codex_write_requires_predeclared_allowed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            with self.assertRaises(CodexAdapterError) as error:
                run_codex_lane(
                    sandbox=sandbox,
                    prompt="delete a file",
                    codex_binary=_fake_codex(bindir),
                    transcript_path=str(Path(sandbox) / "events.raw.jsonl"),
                    sandbox_mode="workspace-write",
                )

        self.assertEqual(error.exception.code, "ERR_CODEX_ALLOWED_PATHS_REQUIRED")

    def test_phase0_codex_json_capture_preserves_raw_events(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            repo = Path(sandbox)
            (repo / "delete-me.txt").write_text("x", encoding="utf-8")
            raw_events = repo / "events.raw.jsonl"
            command_log = repo / "adapter-command.json"

            result = run_codex_lane(
                sandbox=sandbox,
                prompt="delete delete-me.txt",
                codex_binary=_fake_codex(bindir),
                transcript_path=str(raw_events),
                log_path=str(command_log),
                sandbox_mode="workspace-write",
                allowed_touched_files=["delete-me.txt"],
            )

            self.assertIn("--json", result.invocation)
            self.assertNotIn("--output-last-message", result.invocation)
            self.assertEqual(result.exit_code, 0)
            self.assertIn('"thread.started"', raw_events.read_text(encoding="utf-8"))
            receipt = result.command_receipts[0]
            self.assertIn('"item.completed"', receipt["stdout"])

    def test_qa09_codex_structured_capture_full_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox, tempfile.TemporaryDirectory() as bindir:
            repo = Path(sandbox)
            (repo / "delete-me.txt").write_text("x", encoding="utf-8")
            raw_events = repo / "events.raw.jsonl"

            result = run_codex_lane(
                sandbox=sandbox,
                prompt="delete delete-me.txt",
                codex_binary=_fake_codex(bindir),
                transcript_path=str(raw_events),
                sandbox_mode="workspace-write",
                allowed_touched_files=["delete-me.txt"],
            )

            self.assertTrue(
                hasattr(result, "normalized_events"),
                "Phase 1 C2 owns normalized AgentEventEnvelope capture.",
            )
            self.assertTrue(result.normalized_events)
            self.assertEqual(
                {event["schema"] for event in result.normalized_events},
                {"moonweave.agent-event/v1"},
            )
            self.assertEqual(
                [event["event_type"] for event in result.normalized_events],
                ["thread.started", "command.completed"],
            )
            self.assertTrue(
                all(event["raw_event_sha256"] for event in result.normalized_events)
            )
            self.assertIn("delete-me.txt", result.touched_files)
            normalized_path = raw_events.with_name("events.normalized.jsonl")
            self.assertTrue(normalized_path.exists())
            self.assertEqual(
                len(normalized_path.read_text(encoding="utf-8").splitlines()),
                len(result.normalized_events),
            )


if __name__ == "__main__":
    unittest.main()
