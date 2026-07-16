import io
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from witnessd.__main__ import main

_HAS_OPENSSL = shutil.which("openssl") is not None


def _init_repo(path: str) -> str:
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    with open(os.path.join(path, "seed.txt"), "w", encoding="utf-8") as handle:
        handle.write("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class TestRunSeparation(unittest.TestCase):
    @unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
    def test_run_outside_sandbox_emits_evidence(self):
        with tempfile.TemporaryDirectory() as base:
            sandbox = os.path.join(base, "sandbox")
            out_dir = os.path.join(base, "evidence")
            os.makedirs(sandbox)
            os.makedirs(out_dir)
            code = main(
                [
                    "run",
                    "--adapter",
                    "shell",
                    "--runner-sandbox",
                    sandbox,
                    "--out",
                    os.path.join(out_dir, "capture.json"),
                    "--log",
                    os.path.join(out_dir, "verify.log"),
                    "--",
                    "sh",
                    "-c",
                    "echo hi",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(
                os.path.exists(os.path.join(out_dir, "capture-manifest.json"))
            )
            self.assertTrue(os.path.exists(os.path.join(out_dir, "bundle.json")))

    def test_run_inside_sandbox_refused_no_output(self):
        with tempfile.TemporaryDirectory() as base:
            sandbox = os.path.join(base, "sandbox")
            os.makedirs(sandbox)
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(
                    [
                        "run",
                        "--adapter",
                        "shell",
                        "--runner-sandbox",
                        sandbox,
                        "--out",
                        os.path.join(sandbox, "capture.json"),
                        "--log",
                        os.path.join(sandbox, "verify.log"),
                        "--",
                        "sh",
                        "-c",
                        "echo hi",
                    ]
                )
            self.assertNotEqual(code, 0)
            self.assertIn("ERR_OBSERVER_NOT_SEPARATED", err.getvalue())
            self.assertFalse(
                os.path.exists(os.path.join(sandbox, "capture-manifest.json"))
            )
            self.assertEqual(os.listdir(sandbox), [])

    @unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
    def test_proofrun_shell_persists_requested_observer_and_log(self):
        with tempfile.TemporaryDirectory() as base:
            sandbox = os.path.join(base, "sandbox")
            evidence = os.path.join(base, "evidence")
            keys = os.path.join(base, "keys")
            observer_out = os.path.join(evidence, "observer.json")
            proofrun_log = os.path.join(evidence, "proofrun.log")
            os.makedirs(sandbox)
            os.makedirs(evidence)

            code = main(
                [
                    "orro",
                    "proofrun",
                    "--adapter",
                    "shell",
                    "--runner-sandbox",
                    sandbox,
                    "--out",
                    observer_out,
                    "--log",
                    proofrun_log,
                    "--keys-dir",
                    keys,
                    "--task-id",
                    "issue-44-success",
                    "--capture-profile",
                    "full",
                    "--",
                    "printf ok",
                ]
            )

            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(observer_out))
            self.assertTrue(os.path.isfile(proofrun_log))
            with open(observer_out, encoding="utf-8") as handle:
                observer = json.load(handle)
            command_receipt = observer["command_receipts"][0]
            self.assertEqual(command_receipt["command"], ["sh", "-c", "printf ok"])
            self.assertEqual(command_receipt["exit_code"], 0)
            self.assertEqual(command_receipt["stdout"], "ok")
            with open(proofrun_log, encoding="utf-8") as handle:
                transcript = handle.read()
            self.assertIn("$ sh -c printf ok", transcript)
            self.assertIn("exit=0", transcript)
            with open(
                os.path.join(evidence, "runner-receipt.json"), encoding="utf-8"
            ) as handle:
                receipt = json.load(handle)
            self.assertEqual(receipt["invocation"], ["sh", "-c", "printf ok"])
            self.assertEqual(receipt["exit_code"], 0)
            self.assertEqual(receipt["transcript_path"], "evidence/proofrun.log")
            with open(
                os.path.join(evidence, "run-intent.json"), encoding="utf-8"
            ) as handle:
                intent = json.load(handle)["intent"]
            self.assertEqual(intent["capture_profile"], "full")
            self.assertFalse(
                os.path.exists(os.path.join(evidence, "redaction-manifest.json"))
            )

    @unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
    def test_proofrun_shell_redacts_persisted_path_but_uses_real_git_baseline(self):
        with tempfile.TemporaryDirectory() as base:
            sandbox = os.path.join(base, "real-sandbox")
            evidence = os.path.join(base, "redacted-evidence")
            keys = os.path.join(base, "keys")
            git_head = _init_repo(sandbox)

            code = main(
                [
                    "orro",
                    "proofrun",
                    "--adapter",
                    "shell",
                    "--runner-sandbox",
                    sandbox,
                    "--out",
                    os.path.join(evidence, "observer.json"),
                    "--log",
                    os.path.join(evidence, "proofrun.log"),
                    "--keys-dir",
                    keys,
                    "--task-id",
                    "orro-48-redacted",
                    "--",
                    "git status --short",
                ]
            )

            self.assertEqual(code, 0)
            with open(
                os.path.join(evidence, "run-intent.json"), encoding="utf-8"
            ) as handle:
                intent = json.load(handle)["intent"]
            self.assertEqual(intent["baseline"]["git_head"], git_head)
            self.assertEqual(intent["baseline"]["git_head_status"], "known")
            self.assertEqual(intent["capture_profile"], "redacted")
            with open(
                os.path.join(evidence, "runner-receipt.json"), encoding="utf-8"
            ) as handle:
                receipt = json.load(handle)
            self.assertNotEqual(receipt["worktree"], sandbox)
            self.assertIn("path:", receipt["worktree"])
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in sorted(pathlib.Path(evidence).rglob("*"))
                if path.is_file()
            )
            self.assertNotIn(sandbox, persisted)

    @unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
    def test_proofrun_shell_full_profile_keeps_real_path_and_git_baseline(self):
        with tempfile.TemporaryDirectory() as base:
            sandbox = os.path.join(base, "real-sandbox")
            evidence = os.path.join(base, "full-evidence")
            git_head = _init_repo(sandbox)

            code = main(
                [
                    "orro",
                    "proofrun",
                    "--adapter",
                    "shell",
                    "--runner-sandbox",
                    sandbox,
                    "--out",
                    os.path.join(evidence, "observer.json"),
                    "--log",
                    os.path.join(evidence, "proofrun.log"),
                    "--keys-dir",
                    os.path.join(base, "keys"),
                    "--task-id",
                    "orro-48-full",
                    "--capture-profile",
                    "full",
                    "--",
                    "git status --short",
                ]
            )

            self.assertEqual(code, 0)
            with open(
                os.path.join(evidence, "run-intent.json"), encoding="utf-8"
            ) as handle:
                intent = json.load(handle)["intent"]
            self.assertEqual(intent["baseline"]["git_head"], git_head)
            with open(
                os.path.join(evidence, "runner-receipt.json"), encoding="utf-8"
            ) as handle:
                receipt = json.load(handle)
            self.assertEqual(receipt["worktree"], sandbox)

    def test_proofrun_shell_missing_runtime_sandbox_returns_stable_error(self):
        with tempfile.TemporaryDirectory() as base:
            missing = os.path.join(base, "missing-sandbox")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "--adapter",
                        "shell",
                        "--runner-sandbox",
                        missing,
                        "--out",
                        os.path.join(base, "evidence", "observer.json"),
                        "--log",
                        os.path.join(base, "evidence", "proofrun.log"),
                        "--",
                        "printf ok",
                    ]
                )

            self.assertEqual(code, 2)
            self.assertEqual(stderr.getvalue(), "ERR_RUNTIME_SANDBOX_UNAVAILABLE\n")
            self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())

    @unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
    def test_proofrun_shell_fails_closed_when_observer_sink_is_not_writable(self):
        with tempfile.TemporaryDirectory() as base:
            sandbox = os.path.join(base, "sandbox")
            evidence = os.path.join(base, "evidence")
            keys = os.path.join(base, "keys")
            observer_out = os.path.join(evidence, "observer.json")
            proofrun_log = os.path.join(evidence, "proofrun.log")
            os.makedirs(sandbox)
            os.makedirs(observer_out)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "--adapter",
                        "shell",
                        "--runner-sandbox",
                        sandbox,
                        "--out",
                        observer_out,
                        "--log",
                        proofrun_log,
                        "--keys-dir",
                        keys,
                        "--",
                        "printf ok",
                    ]
                )

            self.assertNotEqual(code, 0)
            self.assertNotIn("evidence-pending", stdout.getvalue())
            self.assertIn("ERR_OBSERVER_PERSIST_FAILED", stderr.getvalue())

    @unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
    def test_proofrun_shell_fails_closed_when_command_cannot_run(self):
        with tempfile.TemporaryDirectory() as base:
            sandbox = os.path.join(base, "sandbox")
            evidence = os.path.join(base, "evidence")
            keys = os.path.join(base, "keys")
            observer_out = os.path.join(evidence, "observer.json")
            proofrun_log = os.path.join(evidence, "proofrun.log")
            os.makedirs(sandbox)
            os.makedirs(evidence)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "orro",
                        "proofrun",
                        "--adapter",
                        "shell",
                        "--runner-sandbox",
                        sandbox,
                        "--out",
                        observer_out,
                        "--log",
                        proofrun_log,
                        "--keys-dir",
                        keys,
                        "--",
                        "command-that-does-not-exist-issue-44",
                    ]
                )

            self.assertNotEqual(code, 0)
            self.assertNotIn("evidence-pending", stdout.getvalue())
            self.assertIn("ERR_VERIFICATION_COMMAND_FAILED", stderr.getvalue())


class TestStatus(unittest.TestCase):
    def test_status_evidence_pending_only(self):
        with tempfile.TemporaryDirectory() as base:
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["status", "--evidence-dir", base])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("evidence-pending", text)
            self.assertNotIn("VERIFIED", text)
            self.assertNotIn("COMPLETE", text)
            self.assertNotIn("DONE", text)


class TestSelfTest(unittest.TestCase):
    def test_self_test_all_exit_zero(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["self-test", "--all"])
        self.assertEqual(code, 0)
        self.assertIn("passed", out.getvalue())


class TestRunDeponeValid(unittest.TestCase):
    """`witnessd run` must emit a capture-manifest Depone accepts and re-derives
    to A1 — not merely a well-shaped file. Guards gap#1 (placeholder fixture)."""

    @unittest.skipUnless(_HAS_OPENSSL, "openssl required to sign emitted evidence")
    def test_run_emits_depone_valid_a1_manifest(self):
        from depone.agent_fabric.capture_bridge import validate_capture_manifest

        with tempfile.TemporaryDirectory() as base:
            sandbox = os.path.join(base, "sandbox")
            out_dir = os.path.join(base, "evidence")
            os.makedirs(sandbox)
            os.makedirs(out_dir)
            code = main(
                [
                    "run",
                    "--adapter",
                    "shell",
                    "--runner-sandbox",
                    sandbox,
                    "--out",
                    os.path.join(out_dir, "observer-capture.json"),
                    "--log",
                    os.path.join(out_dir, "verify.log"),
                    "--task-id",
                    "cli-demo",
                    "--allow",
                    "out.txt",
                    "--",
                    "sh",
                    "-c",
                    "echo hi > out.txt",
                ]
            )
            self.assertEqual(code, 0)
            with open(os.path.join(out_dir, "capture-manifest.json")) as handle:
                manifest = json.load(handle)
            errors = validate_capture_manifest(manifest)
            self.assertEqual(errors, [], f"CLI manifest must be Depone-valid: {errors}")
            self.assertEqual(manifest["assurance"], "A1-local-observed")


if __name__ == "__main__":
    unittest.main()
