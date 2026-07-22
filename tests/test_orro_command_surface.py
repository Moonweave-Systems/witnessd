import contextlib
import io
import unittest

from orro.__main__ import ORRO_HELP, main as orro_main
from witnessd.__main__ import ORRO_COMMAND_MAP, _build_parser, _normalize_orro_argv


class OrroCommandSurfaceTests(unittest.TestCase):
    def test_run_and_proofrun_expose_honest_keyless_opt_in(self) -> None:
        parser = _build_parser()
        commands = parser._subparsers._group_actions[0].choices
        for command in ("run", "proofrun"):
            help_text = commands[command].format_help()
            self.assertIn("--keyless", help_text)
            self.assertIn("--signing-profile", help_text)
            self.assertIn("public Rekor", help_text)
            self.assertIn("fails closed", help_text)

    def test_unknown_command_names_token_and_valid_commands(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = orro_main(["workflow", "--help"])

        self.assertEqual(result, 2)
        message = stderr.getvalue()
        self.assertIn("orro: unknown command 'workflow'", message)
        self.assertIn("flowplan", message)
        self.assertNotIn("invalid choice: 'orro'", message)

    def test_unknown_command_suggests_close_match(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = orro_main(["flowpln"])

        self.assertEqual(result, 2)
        self.assertIn("orro: unknown command 'flowpln'", stderr.getvalue())
        self.assertIn("did you mean 'flowplan'?", stderr.getvalue())

    def test_recognized_commands_keep_existing_normalization(self) -> None:
        expected = {
            "setup": "orro-setup",
            "init": "init",
            "scout": "scout",
            "flow": "orro-flow",
            "flowplan": "flowplan",
            "proofrun": "proofrun",
            "proofcheck": "proofcheck",
            "advisory-provenance-check": "advisory-provenance-check",
            "handoff": "handoff",
            "doctor": "orro-doctor",
            "engine-lock": "engine-lock",
            "lock": "engine-lock",
            "next": "orro-next",
            "advise": "orro-advise",
            "sketch": "orro-sketch",
            "trace": "orro-trace",
            "report": "orro-report",
            "review": "orro-review",
            "check": "orro-check",
            "demo": "orro-demo",
            "auto": "orro-auto",
            "team": "team",
        }

        self.assertEqual(ORRO_COMMAND_MAP, expected)
        for public_command, witnessd_command in expected.items():
            self.assertEqual(
                _normalize_orro_argv(["orro", public_command]),
                [witnessd_command],
            )

    def test_repo_root_alias_spellings_reach_existing_handler_attributes(self) -> None:
        parser = _build_parser()

        flowplan_repo = parser.parse_args(
            ["flowplan", "inspect aliases", "--repo", "/tmp/repo-alias"]
        )
        flowplan_root = parser.parse_args(
            ["flowplan", "inspect aliases", "--root", "/tmp/repo-alias"]
        )
        scout_root = parser.parse_args(
            ["scout", "inspect aliases", "--root", "/tmp/root-alias"]
        )
        scout_repo = parser.parse_args(
            ["scout", "inspect aliases", "--repo", "/tmp/root-alias"]
        )

        self.assertEqual(flowplan_repo.root, flowplan_root.root)
        self.assertIs(flowplan_repo.func, flowplan_root.func)
        self.assertEqual(scout_root.repo, scout_repo.repo)
        self.assertIs(scout_root.func, scout_repo.func)

    def test_flowplan_exposes_bounded_write_scope_help(self) -> None:
        parser = _build_parser()
        commands = parser._subparsers._group_actions[0].choices
        flowplan = parser.parse_args(
            ["flowplan", "goal", "--write-scope", "src/**", "--write-scope", "docs/**"]
        )

        self.assertEqual(flowplan.write_scope, ["src/**", "docs/**"])
        expected_help = (
            "--write-scope '<glob>' (repeatable): bounded write scope for a "
            "code-change plan; generates the role capability directly instead of "
            "requiring a prebuilt rolepack. Never inferred or defaulted."
        )
        flowplan_help = commands["flowplan"].format_help()
        self.assertIn("--write-scope '<glob>'", flowplan_help)
        self.assertIn("(repeatable): bounded write", flowplan_help)
        self.assertIn("code-change plan; generates the role", flowplan_help)
        self.assertIn("requiring a prebuilt", flowplan_help)
        self.assertIn("rolepack. Never inferred or defaulted.", flowplan_help)
        self.assertIn(expected_help, ORRO_HELP)

    def test_flowplan_exposes_declared_shell_command_help(self) -> None:
        parser = _build_parser()
        commands = parser._subparsers._group_actions[0].choices
        flowplan = parser.parse_args(
            [
                "flowplan",
                "goal",
                "--command",
                "touch src/a.txt",
                "--command",
                "touch src/b.txt",
            ]
        )

        self.assertEqual(
            flowplan.command,
            ["touch src/a.txt", "touch src/b.txt"],
        )
        expected_help = (
            "--command '<shell>' (repeatable, --lane-adapter shell only): declared "
            "deterministic commands the runner executes; touched files are checked "
            "against --write-scope. Not for AI adapters."
        )
        flowplan_help = commands["flowplan"].format_help()
        self.assertIn("--command '<shell>'", flowplan_help)
        self.assertIn("declared deterministic commands", flowplan_help)
        self.assertIn("executes; touched files are checked", flowplan_help)
        self.assertIn("against --write-", flowplan_help)
        self.assertIn("Not for AI adapters.", flowplan_help)
        self.assertIn(expected_help, ORRO_HELP)

    def test_orro_demo_is_public_and_exposes_violation_mode(self) -> None:
        parser = _build_parser()
        commands = parser._subparsers._group_actions[0].choices

        demo = parser.parse_args(["orro-demo", "--violate"])

        self.assertTrue(demo.violate)
        self.assertIn("--violate", commands["orro-demo"].format_help())
        self.assertIn("demo", ORRO_HELP)

    def test_orro_check_exposes_health_flags_and_repeatable_fix_scope(self) -> None:
        parser = _build_parser()
        commands = parser._subparsers._group_actions[0].choices
        parsed = parser.parse_args(
            [
                "orro-check",
                "--health",
                "--fix",
                "--write-scope",
                "src/**",
                "--write-scope",
                "tests/**",
                "--health-plan",
            ]
        )

        self.assertTrue(parsed.health)
        self.assertTrue(parsed.fix)
        self.assertEqual(parsed.write_scope, ["src/**", "tests/**"])
        self.assertTrue(parsed.health_plan)
        help_text = commands["orro-check"].format_help()
        self.assertIn("already-adopted deterministic gates", help_text)
        self.assertIn("requires explicit --write-scope", help_text)
        self.assertIn("never inferred", help_text)
        self.assertIn("as JSON without", help_text)
        self.assertIn("running phases", help_text)

    def test_doctor_help_distinguishes_runlog_health_from_orro_readiness(self) -> None:
        self.assertIn("runlog health", _build_parser().format_help())
        self.assertIn("not runlog health", ORRO_HELP)

    def test_role_lane_tier_defaults_to_adapter_aware_auto_on_every_surface(
        self,
    ) -> None:
        parser = _build_parser()
        flowplan = parser.parse_args(["flowplan", "goal"])
        guided_flow = parser.parse_args(["orro-flow", "goal"])
        team_go = parser.parse_args(["team", "go", "goal", "--repo", "."])

        self.assertEqual(flowplan.role_lane_tier, "auto")
        self.assertEqual(guided_flow.role_lane_tier, "auto")
        self.assertEqual(team_go.role_lane_tier, "auto")

        expected_help = (
            "auto (default): shell lanes run at quick/120s, AI-adapter lanes at "
            "agentic/1800s; override with quick|agentic|frontier"
        )
        commands = parser._subparsers._group_actions[0].choices
        team_commands = commands["team"]._subparsers._group_actions[0].choices
        for help_text in (
            commands["flowplan"].format_help(),
            commands["orro-flow"].format_help(),
            team_commands["go"].format_help(),
        ):
            self.assertIn("auto (default): shell lanes run at quick/120s", help_text)
            self.assertIn("adapter lanes at agentic/1800s; override with", help_text)
            self.assertIn("quick|agentic|frontier", help_text)
        self.assertIn(expected_help, ORRO_HELP)

    def test_runner_sandbox_help_distinguishes_directory_from_codex_mode(self) -> None:
        parser = _build_parser()
        commands = parser._subparsers._group_actions[0].choices
        expected = (
            "filesystem DIR where the runner executes; NOT a Codex sandbox mode "
            "(read-only/workspace-write) and NOT the observer run/out directory"
        )

        for help_text in (
            commands["a2-observer-run"].format_help(),
            commands["orro-flow"].format_help(),
            commands["faultkit"]
            ._subparsers._group_actions[0]
            .choices["budget-blowout"]
            .format_help(),
            commands["run"].format_help(),
            commands["proofrun"].format_help(),
        ):
            self.assertIn("--runner-sandbox DIR", help_text)
            self.assertIn("filesystem DIR where the runner executes", help_text)
            self.assertIn("NOT a Codex", help_text)
            self.assertIn("sandbox mode (read-only/workspace-write)", help_text)
            self.assertIn("observer run/out directory", help_text)
        self.assertIn(expected, ORRO_HELP)


if __name__ == "__main__":
    unittest.main()
