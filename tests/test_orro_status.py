from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from witnessd.__main__ import main
from witnessd.cli._output import _hash_file
from witnessd.cli.status import build_status, render_status_text
from witnessd.orro_roadmap import seal_roadmap_binding, write_roadmap


def _roadmap() -> dict[str, object]:
    return {
        "kind": "orro-roadmap",
        "schema_version": "0.1",
        "items": [
            {"id": "verified-item", "title": "Verified"},
            {"id": "active-item", "title": "Active"},
            {"id": "claimed-item", "title": "Claimed", "status": "done"},
            {"id": "future-item", "title": "Future"},
        ],
    }


def _steps_roadmap() -> dict[str, object]:
    return {
        "kind": "orro-roadmap",
        "schema_version": "0.1",
        "items": [{
            "id": "feature",
            "title": "Feature",
            "steps": [
                {"id": "implement", "profile": "code-change", "write_scope": ["src/**"], "adapter": "codex"},
                {"id": "verify", "profile": "verification-only", "checks": ["true"]},
            ],
        }],
    }


def _write_companion_verdict(
    run_dir: Path, *, decision: str, manifest_decision: str | None = None, tamper: bool = False
) -> Path:
    verdict_path = run_dir / "proofcheck-verdict.json"
    verdict_path.write_text(
        json.dumps({"kind": "orro-proofcheck-verdict", "decision": decision})
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "kind": "orro-companion-manifest",
        "verdict_ref": {
            "path": str(verdict_path),
            "sha256": _hash_file(verdict_path),
            "decision": decision if manifest_decision is None else manifest_decision,
        },
    }
    (run_dir / "companion-manifest.json").write_text(
        json.dumps(manifest) + "\n", encoding="utf-8"
    )
    if tamper:
        verdict_path.write_text(
            json.dumps({"kind": "orro-proofcheck-verdict", "decision": "tampered"})
            + "\n",
            encoding="utf-8",
        )
    return verdict_path


class OrroStatusTests(unittest.TestCase):
    def test_resolve_home_prefers_explicit_then_environment_then_repo(self) -> None:
        from witnessd.cli.status import resolve_home

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            explicit = root / "explicit-home"
            env_home = root / "env-home"
            with patch.dict(os.environ, {"WITNESSD_HOME": str(env_home)}, clear=False):
                self.assertEqual(resolve_home(str(explicit), repo), explicit.resolve())
                self.assertEqual(resolve_home(None, repo), env_home.resolve())
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(resolve_home(None, repo), (repo / ".witnessd").resolve())
    def test_steps_derive_progress_and_next_command_from_verified_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = root / "repo", root / "home"
            write_roadmap(repo, _steps_roadmap())
            runs = home / "runs"
            runs.mkdir(parents=True)
            implement_run = runs / "run-implement-pending"
            implement_run.mkdir()
            seal_roadmap_binding(repo=repo, run_dir=implement_run, item_id="feature", step_id="implement")
            with patch("witnessd.cli.status.decide_next", return_value=(0, {"decision": "evidence-pending"})):
                initial = build_status(repo=repo, home=home)
            item = initial["items"][0]
            self.assertEqual(item["status"], "in-progress (0/2 steps)")
            self.assertEqual(item["steps"][0]["state"], "in-progress")
            self.assertIn("orro flow \"Feature: implement\"", item["steps"][0]["suggested_next_command"])
            self.assertIn("--roadmap-item feature --roadmap-step implement", item["steps"][0]["suggested_next_command"])

            verify_run = runs / "run-verify"
            verify_run.mkdir()
            seal_roadmap_binding(repo=repo, run_dir=verify_run, item_id="feature", step_id="verify")
            def partial_decide(run_dir: Path, *, home: Path) -> tuple[int, dict[str, object]]:
                return (0, {"decision": "complete" if run_dir == verify_run else "evidence-pending"})

            with patch("witnessd.cli.status.decide_next", side_effect=partial_decide):
                partial = build_status(repo=repo, home=home)
            item = partial["items"][0]
            self.assertEqual(item["status"], "in-progress (1/2 steps)")
            self.assertEqual(item["steps"][1]["state"], "done (verified)")
            self.assertIn("--roadmap-step implement", item["next_step"]["suggested_next_command"])

            implement_run = runs / "run-implement"
            implement_run.mkdir()
            seal_roadmap_binding(repo=repo, run_dir=implement_run, item_id="feature", step_id="implement")
            with patch("witnessd.cli.status.decide_next", return_value=(0, {"decision": "complete"})):
                complete = build_status(repo=repo, home=home)
            self.assertEqual(complete["items"][0]["status"], "done (verified)")
            self.assertIsNone(complete["items"][0]["next_step"])

    def test_steps_manual_recommendation_does_not_guess_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, home = root / "repo", root / "home"
            write_roadmap(repo, {
                "kind": "orro-roadmap", "schema_version": "0.1", "items": [{
                    "id": "review", "title": "Review", "steps": [
                        {"id": "inspect", "profile": "review-only"},
                    ],
                }],
            })
            with patch("witnessd.cli.status.decide_next", return_value=(0, {"decision": "evidence-pending"})):
                item = build_status(repo=repo, home=home)["items"][0]
            self.assertIn("construct the command manually (profile: review-only)", item["next_step"]["suggested_next_command"])
    def test_status_uses_honest_vocabulary_and_decide_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            runs = home / "runs"
            runs.mkdir(parents=True)
            write_roadmap(repo, _roadmap())

            verified = runs / "run-verified"
            active_old = runs / "run-active-old"
            active_new = runs / "run-active-new"
            unbound_old = runs / "run-unbound-old"
            unbound_new = runs / "run-unbound-new"
            malformed_binding = runs / "run-malformed-binding"
            for run_dir in (
                verified,
                active_old,
                active_new,
                unbound_old,
                unbound_new,
                malformed_binding,
            ):
                run_dir.mkdir()
            seal_roadmap_binding(repo=repo, run_dir=verified, item_id="verified-item")
            seal_roadmap_binding(repo=repo, run_dir=active_old, item_id="active-item")
            seal_roadmap_binding(repo=repo, run_dir=active_new, item_id="active-item")
            (malformed_binding / "roadmap-binding.json").write_text("{", encoding="utf-8")
            now = 2_000_000_000
            os.utime(active_old, ns=(now, now))
            os.utime(active_new, ns=(now + 1, now + 1))
            os.utime(unbound_old, ns=(now + 2, now + 2))
            os.utime(unbound_new, ns=(now + 3, now + 3))
            os.utime(malformed_binding, ns=(now + 4, now + 4))

            worktree = verified / "worktrees" / "lane-one"
            worktree.mkdir(parents=True)
            (worktree / "bytes.txt").write_text("12345", encoding="utf-8")
            receipt_dir = verified / "lane-one"
            receipt_dir.mkdir()
            (receipt_dir / "worktree-lane-receipt.json").write_text(
                json.dumps({"worktree": str(worktree), "dirty": True}),
                encoding="utf-8",
            )

            decisions = {
                "run-verified": "complete",
                "run-active-old": "evidence-pending",
                "run-active-new": "needs-proofcheck",
                "run-unbound-old": "blocked",
                "run-unbound-new": "ready-for-handoff",
                "run-malformed-binding": "evidence-pending",
            }

            def fake_decide(run_dir: Path, *, home: Path) -> tuple[int, dict[str, object]]:
                self.assertEqual(home, root / "home")
                return 0, {"decision": decisions[run_dir.name]}

            with patch("witnessd.cli.status.decide_next", side_effect=fake_decide) as decide:
                payload = build_status(repo=repo, home=home)

            self.assertEqual(decide.call_count, 6)
            by_id = {item["id"]: item for item in payload["items"]}
            self.assertEqual(by_id["verified-item"]["status"], "done (verified)")
            self.assertEqual(
                by_id["verified-item"]["evidence_ref"],
                str(verified / "proofcheck-verdict.json"),
            )
            self.assertEqual(by_id["active-item"]["status"], "in-progress")
            self.assertEqual(by_id["active-item"]["run_state"], "needs-proofcheck")
            self.assertEqual(by_id["active-item"]["latest_run"], str(active_new))
            self.assertEqual(
                by_id["claimed-item"]["status"], "marked-done (unverified)"
            )
            self.assertEqual(by_id["future-item"]["status"], "not-started")
            self.assertEqual(
                [Path(item["run_dir"]).name for item in payload["off_plan"]],
                ["run-malformed-binding", "run-unbound-new", "run-unbound-old"],
            )
            self.assertEqual(payload["workspace"]["run_count"], 6)
            self.assertEqual(payload["workspace"]["worktree_count"], 1)
            self.assertEqual(payload["workspace"]["dirty_worktree_count"], 1)
            self.assertGreaterEqual(payload["workspace"]["worktree_bytes"], 5)

            text = render_status_text(payload)
            for phrase in (
                "verified-item: done (verified)",
                "active-item: in-progress",
                "claimed-item: marked-done (unverified)",
                "future-item: not-started",
                "Off-plan runs",
                "Workspace:",
                "not proof, not approval, not assurance",
                "operator claims",
            ):
                self.assertIn(phrase, text)

    def test_absent_ledger_still_reports_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "home" / "runs" / "run-one"
            run_dir.mkdir(parents=True)
            with patch(
                "witnessd.cli.status.decide_next",
                return_value=(1, {"decision": "blocked"}),
            ):
                payload = build_status(repo=root / "repo", home=root / "home")

            self.assertEqual(payload["items"], [])
            self.assertEqual(payload["off_plan"][0]["run_dir"], str(run_dir))

    def test_companion_run_is_included_in_item_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            companion = home / "companion-run"
            companion.mkdir(parents=True)
            write_roadmap(
                repo,
                {
                    "kind": "orro-roadmap",
                    "schema_version": "0.1",
                    "items": [{"id": "companion-item", "title": "Companion"}],
                },
            )
            seal_roadmap_binding(
                repo=repo, run_dir=companion, item_id="companion-item"
            )

            with patch(
                "witnessd.cli.status.decide_next",
                return_value=(0, {"decision": "needs-proofcheck"}),
            ):
                pending = build_status(repo=repo, home=home)

            pending_item = pending["items"][0]
            self.assertEqual(pending["workspace"]["run_count"], 1)
            self.assertEqual(pending_item["status"], "in-progress")
            self.assertEqual(pending_item["latest_run"], str(companion))

            with patch(
                "witnessd.cli.status.decide_next",
                return_value=(0, {"decision": "complete"}),
            ):
                verified = build_status(repo=repo, home=home)

            verified_item = verified["items"][0]
            self.assertEqual(verified_item["status"], "done (verified)")
            self.assertEqual(
                verified_item["evidence_ref"],
                str(companion / "proofcheck-verdict.json"),
            )

    def test_verified_companion_manifest_marks_bound_item_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            companion = home / "companion-run"
            companion.mkdir(parents=True)
            write_roadmap(
                repo,
                {
                    "kind": "orro-roadmap",
                    "schema_version": "0.1",
                    "items": [{"id": "companion-item", "title": "Companion"}],
                },
            )
            seal_roadmap_binding(repo=repo, run_dir=companion, item_id="companion-item")
            verdict_path = _write_companion_verdict(companion, decision="pass")

            with patch("witnessd.cli.status.decide_next") as decide:
                payload = build_status(repo=repo, home=home)

            decide.assert_not_called()
            item = payload["items"][0]
            self.assertEqual(item["status"], "done (verified)")
            self.assertEqual(item["run_state"], "companion-pass")
            self.assertEqual(item["evidence_ref"], str(verdict_path))

    def test_tampered_companion_verdict_is_unverified_and_not_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            companion = home / "companion-run"
            companion.mkdir(parents=True)
            write_roadmap(
                repo,
                {
                    "kind": "orro-roadmap",
                    "schema_version": "0.1",
                    "items": [{"id": "companion-item", "title": "Companion"}],
                },
            )
            seal_roadmap_binding(repo=repo, run_dir=companion, item_id="companion-item")
            _write_companion_verdict(companion, decision="pass", tamper=True)

            with patch("witnessd.cli.status.decide_next") as decide:
                payload = build_status(repo=repo, home=home)

            decide.assert_not_called()
            item = payload["items"][0]
            self.assertEqual(item["status"], "in-progress")
            self.assertEqual(item["run_state"], "companion-unverified")
            self.assertNotIn("evidence_ref", item)

    def test_companion_manifest_decision_cannot_override_hashed_verdict_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            companion = home / "companion-run"
            companion.mkdir(parents=True)
            write_roadmap(
                repo,
                {
                    "kind": "orro-roadmap",
                    "schema_version": "0.1",
                    "items": [{"id": "companion-item", "title": "Companion"}],
                },
            )
            seal_roadmap_binding(repo=repo, run_dir=companion, item_id="companion-item")
            _write_companion_verdict(
                companion, decision="fail", manifest_decision="pass"
            )

            with patch("witnessd.cli.status.decide_next") as decide:
                payload = build_status(repo=repo, home=home)

            decide.assert_not_called()
            item = payload["items"][0]
            self.assertEqual(item["status"], "in-progress")
            self.assertEqual(item["run_state"], "companion-blocked")
            self.assertNotIn("evidence_ref", item)

    def test_blocked_companion_manifest_stays_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            companion = home / "companion-run"
            companion.mkdir(parents=True)
            write_roadmap(
                repo,
                {
                    "kind": "orro-roadmap",
                    "schema_version": "0.1",
                    "items": [{"id": "companion-item", "title": "Companion"}],
                },
            )
            seal_roadmap_binding(repo=repo, run_dir=companion, item_id="companion-item")
            _write_companion_verdict(companion, decision="blocked")

            with patch("witnessd.cli.status.decide_next") as decide:
                payload = build_status(repo=repo, home=home)

            decide.assert_not_called()
            item = payload["items"][0]
            self.assertEqual(item["status"], "in-progress")
            self.assertEqual(item["run_state"], "companion-blocked")

    def test_malformed_ledger_is_structured_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roadmap = root / "repo" / ".orro" / "roadmap.json"
            roadmap.parent.mkdir(parents=True)
            roadmap.write_text("{}", encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(
                    [
                        "orro",
                        "status",
                        "--repo",
                        str(root / "repo"),
                        "--home",
                        str(root / "home"),
                        "--json",
                    ]
                )

            self.assertEqual(code, 2)
            self.assertEqual(
                json.loads(stdout.getvalue())["error"]["code"],
                "ERR_ORRO_ROADMAP_INVALID",
            )


if __name__ == "__main__":
    unittest.main()
