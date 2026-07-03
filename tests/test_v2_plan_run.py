import io
import json
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.__main__ import main


def _fake_codex(directory: Path) -> str:
    path = directory / "codex"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex-cli 0.0.0'; exit 0; fi\n"
        "out=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--output-last-message\" ]; then out=\"$2\"; fi\n"
        "  shift\n"
        "done\n"
        "mkdir -p v2_demo\n"
        "cat > v2_demo/live_agent_result.py <<'PY'\n"
        "\"\"\"Fake live-agent output for the v2 plan-run rehearsal.\"\"\"\n"
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "\n"
        "def v2_agent_marker() -> str:\n"
        "    return \"fake-codex-plan-run\"\n"
        "PY\n"
        "printf '%s\\n' 'implemented v2_agent_marker' > \"$out\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "w@x.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "witnessd-v2"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestV2PlanRun(unittest.TestCase):
    def test_fake_codex_plan_run_reaches_evidence_pending_with_isolated_state(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            base = Path(root)
            repo = base / "repo"
            out_dir = base / "v2-demo"
            state_root = base / "w4-state-root"
            _init_repo(repo)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "team",
                        "plan-run",
                        "create v2 demo marker code",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                        "--draft-adapter",
                        "heuristic",
                        "--lane-adapter",
                        "codex",
                        "--state-root",
                        str(state_root),
                        "--codex-auth-source",
                        "",
                        "--codex-binary",
                        _fake_codex(Path(bindir)),
                        "--max-tokens",
                        "1000",
                        "--max-usd",
                        "0.01",
                        "--max-depth",
                        "1",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("evidence-pending", stdout.getvalue())
            self.assertTrue((state_root / ".witnessd" / "codex-home").is_dir())
            self.assertFalse((repo / ".witnessd").exists())

            sealed = json.loads((out_dir / "sealed-plan.json").read_text())
            packet = sealed["packets"][0]
            self.assertEqual(packet["adapter"], "codex")
            self.assertEqual(
                packet["budget"],
                {"max_tokens": 1000, "max_usd": 0.01, "max_depth": 1},
            )

            ledger = json.loads((out_dir / "team-ledger.json").read_text())
            lane = ledger["lanes"][0]
            self.assertEqual(lane["runner_adapter_kind"], "codex")
            self.assertEqual(lane["verification_state"], "pass")
            self.assertEqual(lane["touched_files"], ["v2_demo/live_agent_result.py"])

            lane_dir = out_dir / lane["evidence_dir"]
            receipt = json.loads((lane_dir / "runner-receipt.json").read_text())
            self.assertEqual(receipt["runner_kind"], "codex-cli")
            self.assertEqual(receipt["exit_code"], 0)
            patch = (lane_dir / "git-diff.patch").read_text(encoding="utf-8")
            self.assertIn("+def v2_agent_marker() -> str:", patch)

    def test_plan_run_seeds_codex_auth_only_into_isolated_state(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            base = Path(root)
            repo = base / "repo"
            out_dir = base / "v2-demo"
            state_root = base / "w4-state-root"
            auth_source = base / "auth.json"
            auth_source.write_text('{"session":"subscription"}\n', encoding="utf-8")
            _init_repo(repo)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "team",
                        "plan-run",
                        "create v2 demo marker code",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                        "--draft-adapter",
                        "heuristic",
                        "--lane-adapter",
                        "codex",
                        "--state-root",
                        str(state_root),
                        "--codex-auth-source",
                        str(auth_source),
                        "--codex-binary",
                        _fake_codex(Path(bindir)),
                        "--max-tokens",
                        "1000",
                        "--max-depth",
                        "1",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("evidence-pending", stdout.getvalue())
            isolated_auth = state_root / ".witnessd" / "codex-home" / "auth.json"
            self.assertEqual(isolated_auth.read_text(encoding="utf-8"), auth_source.read_text(encoding="utf-8"))
            self.assertEqual(oct(isolated_auth.stat().st_mode & 0o777), "0o600")
            fixture_bytes = b"".join(
                path.read_bytes() for path in out_dir.rglob("*") if path.is_file()
            )
            self.assertNotIn(b"subscription", fixture_bytes)

    def test_plan_run_default_codex_state_root_stays_outside_output(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            base = Path(root)
            repo = base / "repo"
            out_dir = base / "v2-demo"
            state_root = Path(str(out_dir).rstrip("/") + "-w4-state-root")
            auth_source = base / "auth.json"
            auth_source.write_text('{"session":"subscription"}\n', encoding="utf-8")
            _init_repo(repo)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "team",
                        "plan-run",
                        "create v2 demo marker code",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                        "--draft-adapter",
                        "heuristic",
                        "--lane-adapter",
                        "codex",
                        "--codex-auth-source",
                        str(auth_source),
                        "--codex-binary",
                        _fake_codex(Path(bindir)),
                        "--max-tokens",
                        "1000",
                        "--max-depth",
                        "1",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("evidence-pending", stdout.getvalue())
            self.assertEqual(
                (state_root / ".witnessd" / "codex-home" / "auth.json").read_text(
                    encoding="utf-8"
                ),
                auth_source.read_text(encoding="utf-8"),
            )
            self.assertFalse((out_dir / "w4-state-root").exists())
            fixture_bytes = b"".join(
                path.read_bytes() for path in out_dir.rglob("*") if path.is_file()
            )
            self.assertNotIn(b"subscription", fixture_bytes)

    def test_plan_run_rejects_codex_state_root_inside_output(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bindir:
            base = Path(root)
            repo = base / "repo"
            out_dir = base / "v2-demo"
            state_root = out_dir / "w4-state-root"
            auth_source = base / "auth.json"
            auth_source.write_text('{"session":"subscription"}\n', encoding="utf-8")
            _init_repo(repo)

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(
                    [
                        "team",
                        "plan-run",
                        "create v2 demo marker code",
                        "--repo",
                        str(repo),
                        "--out",
                        str(out_dir),
                        "--draft-adapter",
                        "heuristic",
                        "--lane-adapter",
                        "codex",
                        "--state-root",
                        str(state_root),
                        "--codex-auth-source",
                        str(auth_source),
                        "--codex-binary",
                        _fake_codex(Path(bindir)),
                    ]
                )

            self.assertEqual(code, 2)
            self.assertIn("ERR_PLAN_RUN_STATE_ROOT_INSIDE_OUTPUT", stderr.getvalue())
            self.assertFalse((state_root / ".witnessd" / "codex-home" / "auth.json").exists())


if __name__ == "__main__":
    unittest.main()
