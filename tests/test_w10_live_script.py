import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


def _fake_codex(directory: str) -> str:
    path = Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "cat > wordscore/core.py <<'PY'\n"
        "\"\"\"Word scoring helpers.\"\"\"\n"
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "\n"
        "def score_phrase(text: str) -> dict[str, int]:\n"
        "    counts: dict[str, int] = {}\n"
        "    for word in text.lower().split():\n"
        "        counts[word] = counts.get(word, 0) + 1\n"
        "    return counts\n"
        "PY\n"
        "python3 -m unittest discover -s tests >/tmp/w10-fake-tests.log 2>&1\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        ": > \"$out\"\n"
        "echo 'implemented score_phrase' >> \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class TestW10LiveScript(unittest.TestCase):
    def test_prepare_sandbox_contains_real_failing_test(self):
        from scripts.run_w10_live import prepare_sandbox

        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp) / "sandbox"
            prepare_sandbox(sandbox)

            result = subprocess.run(
                ["python3", "-m", "unittest", "discover", "-s", "tests"],
                cwd=sandbox,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("NotImplementedError", result.stderr + result.stdout)

    def test_fake_codex_rehearsal_emits_evidence_and_no_private_key(self):
        from scripts.run_w10_live import main

        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as bindir,
            tempfile.TemporaryDirectory() as state,
        ):
            out = Path(tmp) / "w10"
            sandbox = Path(tmp) / "sandbox"
            exit_code = main(
                [
                    "--adapter",
                    "codex",
                    "--out",
                    str(out),
                    "--sandbox",
                    str(sandbox),
                    "--state-root",
                    state,
                    "--codex-binary",
                    _fake_codex(bindir),
                    "--max-tokens",
                    "1000",
                    "--max-usd",
                    "0.01",
                    "--max-depth",
                    "1",
                    "--force",
                ]
            )

            self.assertEqual(exit_code, 0)
            evidence = out / "evidence"
            self.assertTrue((evidence / "capture-manifest.json").exists())
            self.assertTrue((out / "PROMPT.md").exists())
            self.assertTrue((out / "keys" / "operator.pub").exists())
            receipt = json.loads((evidence / "runner-receipt.json").read_text())
            self.assertEqual(receipt["runner_kind"], "codex-cli")
            self.assertEqual(receipt["exit_code"], 0)
            patch = (evidence / "git-diff.patch").read_text(encoding="utf-8")
            self.assertIn("+    counts: dict[str, int] = {}", patch)
            self.assertNotIn("__pycache__", patch)

            fixture_bytes = b"".join(
                path.read_bytes() for path in out.rglob("*") if path.is_file()
            )
            self.assertNotIn(b"PRIVATE KEY", fixture_bytes)

    def test_rejects_fixture_output_inside_state_root(self):
        from scripts.run_w10_live import main

        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as bindir,
        ):
            root = Path(tmp)
            state = root / "state"
            out = state / "fixture"
            sandbox = root / "sandbox"
            exit_code = main(
                [
                    "--adapter",
                    "codex",
                    "--out",
                    str(out),
                    "--sandbox",
                    str(sandbox),
                    "--state-root",
                    str(state),
                    "--codex-binary",
                    _fake_codex(bindir),
                    "--max-tokens",
                    "1000",
                    "--max-usd",
                    "0.01",
                    "--max-depth",
                    "1",
                ]
            )

            self.assertEqual(exit_code, 2)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
