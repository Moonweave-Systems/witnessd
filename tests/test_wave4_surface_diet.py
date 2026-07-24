from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main


def _run(*argv: str) -> tuple[int, dict, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(["orro", *argv, "--json"])
    return code, json.loads(stdout.getvalue()), stderr.getvalue()


class Wave4AdviseSurfaceTests(unittest.TestCase):
    def test_advise_auto_routes_bug_goal_to_trace_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, payload, stderr = _run(
                "advise", "fix crash when X", "--repo", tmp
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["kind"], "orro-trace")
        self.assertEqual(stderr, "")

    def test_advise_auto_routes_new_work_goal_to_sketch_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, payload, stderr = _run(
                "advise", "add feature Y", "--repo", tmp
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["kind"], "orro-sketch")
        self.assertEqual(stderr, "")

    def test_advise_mode_overrides_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace_code, trace_payload, trace_stderr = _run(
                "advise", "add feature Y", "--mode", "trace", "--repo", tmp
            )
            sketch_code, sketch_payload, sketch_stderr = _run(
                "advise", "fix crash when X", "--mode", "sketch", "--repo", tmp
            )

        self.assertEqual(trace_code, 0)
        self.assertEqual(trace_payload["kind"], "orro-trace")
        self.assertEqual(trace_stderr, "")
        self.assertEqual(sketch_code, 0)
        self.assertEqual(sketch_payload["kind"], "orro-sketch")
        self.assertEqual(sketch_stderr, "")


class Wave4AutoSurfaceTests(unittest.TestCase):
    def test_auto_dry_run_exposes_next_payload_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "missing-run"
            auto_code, auto_payload, auto_stderr = _run(
                "auto", "--dry-run", str(run_dir)
            )

        self.assertEqual(auto_code, 2)
        self.assertEqual(auto_payload["kind"], "orro-auto-plan")
        self.assertIn("observed_artifacts", auto_payload)
        self.assertIn("next_allowed", auto_payload)
        self.assertEqual(auto_stderr, "")


class Wave4StatusSurfaceTests(unittest.TestCase):
    def test_status_run_scope_and_latest_render_report_view(self) -> None:
        report_payload = {
            "kind": "orro-report",
            "summary": {"state": "needs-proofcheck", "recommended_next_action": "proofcheck"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            run_dir = home / "runs" / "run-one"
            run_dir.mkdir(parents=True)
            with patch("witnessd.cli.status.build_report", return_value=(0, report_payload)) as build_report:
                status_code, status_payload, status_stderr = _run(
                    "status", str(run_dir), "--home", str(home)
                )
                latest_code, latest_payload, latest_stderr = _run(
                    "status", "--latest", "--home", str(home)
                )

        self.assertEqual(status_code, 0)
        self.assertEqual(status_payload, report_payload)
        self.assertEqual(latest_code, 0)
        self.assertEqual(latest_payload, report_payload)
        self.assertEqual(status_stderr, "")
        self.assertEqual(latest_stderr, "")
        self.assertEqual(build_report.call_count, 2)


if __name__ == "__main__":
    unittest.main()
