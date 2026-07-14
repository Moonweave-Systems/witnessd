from __future__ import annotations

import json
import pathlib
import stat
import tempfile
import unittest

from witnessd.adapters.base import AdapterResult
from witnessd.adapters.claude import (
    CLAUDE_CRITIC_BUILTIN_TOOLS,
    run_claude_critic_lane,
)


def _fake_claude_critic(
    directory: pathlib.Path,
    *,
    observed_model: str | None,
    reject_model: bool = False,
    bypass_hook_mutation: bool = False,
) -> str:
    path = directory / "claude"
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json\n"
        "import pathlib\n"
        "import shlex\n"
        "import subprocess\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "settings_path = pathlib.Path(args[args.index('--settings') + 1])\n"
        "settings = json.loads(settings_path.read_text(encoding='utf-8'))\n"
        "command = settings['hooks']['PreToolUse'][0]['hooks'][0]['command']\n"
        "for tool_name in ['Read', 'Edit', 'Write', 'NotebookEdit', 'Bash', 'WebSearch', 'WebFetch', 'mcp__filesystem__write_file']:\n"
        "    completed = subprocess.run(\n"
        "        shlex.split(command),\n"
        "        input=json.dumps({'tool_name': tool_name}),\n"
        "        text=True,\n"
        "        capture_output=True,\n"
        "        check=False,\n"
        "    )\n"
        "    if tool_name == 'Edit' and not completed.stdout.strip():\n"
        "        pathlib.Path('EDITED.md').write_text('edit escaped hook\\n', encoding='utf-8')\n"
        + (
            "pathlib.Path('MUTATED.md').write_text('hook bypass\\n', encoding='utf-8')\n"
            if bypass_hook_mutation
            else ""
        )
        + (
            "print(json.dumps({'type': 'assistant', 'error': 'model_not_found', 'result': 'requested model unavailable'}))\n"
            if reject_model
            else (
                "print(json.dumps({'type': 'system', 'subtype': 'init', 'model': "
                + repr(observed_model)
                + "}))\n"
                "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'critic complete'}))\n"
            )
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class ClaudeCriticTests(unittest.TestCase):
    def _run(
        self,
        *,
        observed_model: str | None,
        reject_model: bool = False,
        bypass_hook_mutation: bool = False,
    ) -> tuple[pathlib.Path, AdapterResult]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = pathlib.Path(temporary.name)
        repo = root / "repo"
        evidence = root / "evidence"
        bindir = root / "bin"
        repo.mkdir()
        evidence.mkdir()
        bindir.mkdir()
        (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
        result = run_claude_critic_lane(
            sandbox=str(repo),
            prompt="Review only; do not edit files.",
            claude_binary=_fake_claude_critic(
                bindir,
                observed_model=observed_model,
                reject_model=reject_model,
                bypass_hook_mutation=bypass_hook_mutation,
            ),
            transcript_path=str(evidence / "events.raw.jsonl"),
            review_receipt_path=str(evidence / "review-receipt.json"),
            log_path=str(evidence / "command-log.json"),
            model="claude-sonnet-4-5",
            role_id="critic",
            lane_id="critic-1",
        )
        return repo, result

    def test_critic_pep_denies_edits_and_write_capable_tools(self) -> None:
        self.assertEqual(CLAUDE_CRITIC_BUILTIN_TOOLS, ("Read", "Glob", "Grep"))
        repo, result = self._run(observed_model="claude-sonnet-4-5")

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.touched_files, [])
        self.assertFalse((repo / "EDITED.md").exists())
        receipt = json.loads(
            pathlib.Path(result.review_receipt_path).read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["decision"], "pass")
        self.assertFalse(receipt["raises_assurance"])
        self.assertFalse(receipt["verifies_evidence"])
        self.assertFalse(receipt["can_change_evidence_verdict"])
        decisions = {
            item["canonical_tool_name"]: item
            for item in receipt["tool_decisions"]
        }
        self.assertEqual(decisions["Read"]["decision"], "allow")
        for tool_name in (
            "Edit",
            "Write",
            "NotebookEdit",
            "Bash",
            "WebSearch",
            "WebFetch",
            "mcp__filesystem__write_file",
        ):
            self.assertEqual(decisions[tool_name]["decision"], "deny")
        self.assertEqual(
            decisions["Edit"]["reason_code"],
            "ERR_CLAUDE_CRITIC_WRITE_TOOL_DENIED",
        )
        self.assertEqual(
            decisions["mcp__filesystem__write_file"]["reason_code"],
            "ERR_ROLE_CAPABILITY_TOOL_NOT_GRANTED",
        )

    def test_critic_mutation_is_a_hard_failure(self) -> None:
        _repo, result = self._run(
            observed_model="claude-sonnet-4-5",
            bypass_hook_mutation=True,
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(result.test_output["status"], "failed")
        self.assertIn("MUTATED.md", result.touched_files)
        receipt = json.loads(
            pathlib.Path(result.review_receipt_path).read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["decision"], "blocked")
        self.assertEqual(receipt["failure"]["kind"], "mutation-violation")
        self.assertIn("MUTATED.md", receipt["failure"]["touched_files"])

    def test_critic_model_mismatch_writes_failure_receipt(self) -> None:
        _repo, result = self._run(observed_model="claude-haiku-4-5")

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(
            result.model_declaration["verification_status"], "rejected"
        )
        receipt = json.loads(
            pathlib.Path(result.review_receipt_path).read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["decision"], "blocked")
        self.assertEqual(receipt["failure"]["kind"], "model_rejected")
        self.assertEqual(
            receipt["failure"]["error_code"], "ERR_WITNESSD_MODEL_REJECTED"
        )

    def test_critic_unavailable_model_writes_failure_receipt(self) -> None:
        _repo, result = self._run(observed_model=None, reject_model=True)

        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(
            result.model_declaration["verification_status"], "rejected"
        )
        receipt = json.loads(
            pathlib.Path(result.review_receipt_path).read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["decision"], "blocked")
        self.assertEqual(receipt["failure"]["kind"], "model_rejected")
        self.assertIn("unavailable", receipt["failure"]["detail"])


if __name__ == "__main__":
    unittest.main()
