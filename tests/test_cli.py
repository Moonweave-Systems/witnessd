import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from witnessd.__main__ import main

_HAS_OPENSSL = shutil.which("openssl") is not None


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
