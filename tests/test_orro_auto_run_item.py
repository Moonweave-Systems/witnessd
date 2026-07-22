from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from orro.__main__ import main as orro_main
from witnessd.orro_auto import build_auto_session, run_item_session


def _roadmap_item() -> dict[str, object]:
    return {
        "id": "item",
        "title": "Item",
        "steps": [
            {"id": "first", "profile": "verification-only", "checks": ["true"]},
            {"id": "second", "profile": "verification-only", "checks": ["true"]},
        ],
    }


def _status(step_states: list[str]) -> dict[str, object]:
    steps = [
        {"id": step_id, "state": state}
        for step_id, state in zip(("first", "second"), step_states)
    ]
    next_step = next((step for step in steps if step["state"] != "done (verified)"), None)
    return {
        "items": [{
            "id": "item",
            "title": "Item",
            "steps": steps,
            "status": "done (verified)" if next_step is None else "in-progress",
            "next_step": next_step,
        }],
    }


class OrroAutoRunItemTests(unittest.TestCase):
    def test_run_item_requires_repo_max_steps_and_excludes_other_modes(self) -> None:
        cases = [
            (["orro", "auto", "--run-item", "item", "--repo", "/repo", "--json"], "ERR_ORRO_AUTO_MAX_STEPS_REQUIRED"),
            (["orro", "auto", "--run-item", "item", "--max-steps", "1", "--json"], "ERR_ORRO_AUTO_REPO_REQUIRED"),
            (["orro", "auto", "--run-item", "item", "--repo", "/repo", "--max-steps", "1", "--dry-run", "--json"], "ERR_ORRO_AUTO_MODE_CONFLICT"),
            (["orro", "auto", "--run-item", "item", "--repo", "/repo", "--max-steps", "1", "/run", "--json"], "ERR_ORRO_AUTO_RUN_DIR_CONFLICT"),
        ]
        for argv, expected_code in cases:
            argv = argv[1:]
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = orro_main(argv)
            self.assertEqual(code, 2, argv)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], expected_code)

    def test_run_item_parser_and_help_expose_explicit_bounded_mode(self) -> None:
        from witnessd.__main__ import _build_parser

        parsed = _build_parser().parse_args(
            ["orro-auto", "--run-item", "item", "--repo", "/repo", "--max-steps", "2"]
        )
        self.assertEqual(parsed.run_item, "item")
        self.assertEqual(parsed.repo, "/repo")
        help_text = _build_parser()._subparsers._group_actions[0].choices["orro-auto"].format_help()
        self.assertIn("executes the next declared step's recommended command", help_text)
        self.assertIn("stops at the first non-pass", help_text)

    def test_run_item_executes_recommended_commands_and_stops_after_verified_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = root / "repo", root / "home"
            raw_item = _roadmap_item()
            statuses = iter([_status(["not-started", "not-started"]), _status(["done (verified)", "not-started"]), _status(["done (verified)", "done (verified)"])])
            completed = subprocess.CompletedProcess([], 0, stdout=json.dumps({"run_dir": str(home / "companion-run")}), stderr="")
            with patch("witnessd.orro_auto.read_roadmap", return_value={"items": [raw_item]}), patch("witnessd.orro_auto.build_status", side_effect=lambda **_: next(statuses)), patch("witnessd.orro_auto.subprocess.run", return_value=completed) as run:
                code, payload = run_item_session(repo=repo, home=home, item_id="item", max_steps=3)

            self.assertEqual(code, 0)
            self.assertEqual(payload["mode"], "run-item")
            self.assertTrue(payload["complete"])
            self.assertEqual(len(run.call_args_list), 2)
            self.assertTrue(all("--json" in call.args[0] for call in run.call_args_list))
            self.assertEqual([step["step_id"] for step in payload["steps"]], ["first", "second"])
            self.assertTrue(payload["boundary"]["executes_proofrun"])
            self.assertTrue(payload["boundary"]["launches_workers"])

    def test_run_item_stops_on_first_non_verified_step_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_item = _roadmap_item()
            statuses = iter([_status(["not-started", "not-started"]), _status(["in-progress", "not-started"])])
            completed = subprocess.CompletedProcess([], 1, stdout="", stderr="check failed")
            with patch("witnessd.orro_auto.read_roadmap", return_value={"items": [raw_item]}), patch("witnessd.orro_auto.build_status", side_effect=lambda **_: next(statuses)), patch("witnessd.orro_auto.subprocess.run", return_value=completed) as run:
                code, payload = run_item_session(repo=root / "repo", home=root / "home", item_id="item", max_steps=3)

            self.assertNotEqual(code, 0)
            self.assertEqual(len(run.call_args_list), 1)
            self.assertEqual(payload["steps"][0]["resulting_state"], "in-progress")

    def test_run_item_manual_command_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_item = {"id": "item", "title": "Item", "steps": [{"id": "review", "profile": "review-only"}]}
            with patch("witnessd.orro_auto.read_roadmap", return_value={"items": [raw_item]}), patch("witnessd.orro_auto.build_status", return_value={"items": [{"id": "item", "steps": [{"id": "review", "state": "not-started"}], "status": "not-started"}]}), patch("witnessd.orro_auto.subprocess.run") as run:
                code, payload = run_item_session(repo=root / "repo", home=root / "home", item_id="item", max_steps=1)

            self.assertNotEqual(code, 0)
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_AUTO_STEP_NOT_EXECUTABLE")
            run.assert_not_called()

    def test_run_item_enforces_max_steps_and_existing_session_receipt_stays_non_executing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_item = _roadmap_item()
            statuses = iter([_status(["not-started", "not-started"]), _status(["done (verified)", "not-started"])])
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with patch("witnessd.orro_auto.read_roadmap", return_value={"items": [raw_item]}), patch("witnessd.orro_auto.build_status", side_effect=lambda **_: next(statuses)), patch("witnessd.orro_auto.subprocess.run", return_value=completed):
                code, payload = run_item_session(repo=root / "repo", home=root / "home", item_id="item", max_steps=1)

            self.assertNotEqual(code, 0)
            self.assertFalse(payload["complete"])
            self.assertEqual(payload["error"]["code"], "ERR_ORRO_AUTO_MAX_STEPS_REACHED")
            legacy = build_auto_session(root / "run", max_steps=1, steps=[], decision_initial="complete", decision_final="complete", complete=True, blocked=False)
            self.assertFalse(legacy["boundary"]["executes_proofrun"])
            self.assertFalse(legacy["boundary"]["launches_workers"])


if __name__ == "__main__":
    unittest.main()
