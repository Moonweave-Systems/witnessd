from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

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

    def test_sketch_and_trace_are_deprecated_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sketch_code, sketch_payload, sketch_stderr = _run(
                "sketch", "add feature Y", "--repo", tmp
            )
            trace_code, trace_payload, trace_stderr = _run(
                "trace", "fix crash when X", "--repo", tmp
            )

        self.assertEqual(sketch_code, 0)
        self.assertEqual(sketch_payload["kind"], "orro-sketch")
        self.assertEqual(
            sketch_stderr,
            "deprecated: use orro advise --mode sketch (this alias will be removed in a future release)\n",
        )
        self.assertEqual(trace_code, 0)
        self.assertEqual(trace_payload["kind"], "orro-trace")
        self.assertEqual(
            trace_stderr,
            "deprecated: use orro advise --mode trace (this alias will be removed in a future release)\n",
        )


if __name__ == "__main__":
    unittest.main()
