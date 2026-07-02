#!/usr/bin/env python3
"""Run the W10 live-agent lane and seal its evidence fixture."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from witnessd.adapter_run import LaneBlocked, run_adapter_lane
from witnessd.signing import gen_operator_keypair

DEFAULT_PROMPT = """You are in a temporary git repository.

Task: implement score_phrase in wordscore/core.py so the existing failing tests pass.

Requirements:
- Only edit wordscore/core.py.
- score_phrase(text: str) must lowercase whitespace-separated words and return a dict of word counts.
- Do not modify tests.
- Run: python3 -m unittest discover -s tests
- Do not add dependencies.
- Do not print the words VERIFIED or DONE as a verdict.
"""


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def _is_inside_or_equal(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_inside_or_equal(left, right) or _is_inside_or_equal(right, left)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def prepare_sandbox(sandbox: Path) -> None:
    sandbox.mkdir(parents=True, exist_ok=False)
    _write(sandbox / "wordscore" / "__init__.py", "")
    _write(
        sandbox / "wordscore" / "core.py",
        '"""Word scoring helpers."""\n\n'
        "from __future__ import annotations\n\n\n"
        "def score_phrase(text: str) -> dict[str, int]:\n"
        "    raise NotImplementedError('W10 live agent must implement this')\n",
    )
    _write(
        sandbox / "tests" / "test_core.py",
        "import unittest\n\n"
        "from wordscore.core import score_phrase\n\n\n"
        "class TestScorePhrase(unittest.TestCase):\n"
        "    def test_counts_repeated_words(self):\n"
        "        self.assertEqual(score_phrase('red blue red'), {'red': 2, 'blue': 1})\n\n"
        "    def test_lowercases_words(self):\n"
        "        self.assertEqual(score_phrase('RED blue'), {'red': 1, 'blue': 1})\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "witnessd-w10@example.invalid"],
        ["git", "config", "user.name", "witnessd W10"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "seed failing W10 sandbox"],
    ):
        completed = _run(command, cwd=sandbox)
        if completed.returncode != 0:
            raise RuntimeError(f"failed to seed sandbox with {' '.join(command)}: {completed.stderr}")


def _copy_codex_auth(state_root: Path, source: str | None) -> None:
    if not source:
        return
    source_path = Path(source).expanduser().resolve(strict=False)
    if not source_path.exists():
        raise RuntimeError(f"codex auth source does not exist: {source_path}")
    target = state_root / ".witnessd" / "codex-home" / "auth.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target)
    target.chmod(0o600)


def _run_sandbox_tests(sandbox: Path) -> subprocess.CompletedProcess[str]:
    return _run(["python3", "-m", "unittest", "discover", "-s", "tests"], cwd=sandbox)


def _remove_python_caches(root: Path) -> None:
    for path in root.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path)


def _write_prompt_note(
    *,
    out: Path,
    adapter: str,
    prompt: str,
    sandbox: Path,
    state_root: Path,
    evidence_dir: Path,
    public_key: Path,
    args: argparse.Namespace,
    initial_tests: subprocess.CompletedProcess[str],
    post_tests: subprocess.CompletedProcess[str] | None,
    predicted_tokens: int,
    predicted_usd: float,
) -> None:
    post_exit = "not-run" if post_tests is None else str(post_tests.returncode)
    note = f"""# W10 Live-Agent Prompt Provenance

- generated_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
- adapter: {adapter}
- runner_kind: {'codex-cli' if adapter == 'codex' else 'manual'}
- sandbox: {sandbox}
- isolated_state_root: {state_root}
- evidence_dir: {evidence_dir}
- public_key: {public_key}
- budget_flags: --max-tokens {args.max_tokens} --max-usd {args.max_usd} --max-depth {args.max_depth}
- predicted_budget: --predicted-tokens {predicted_tokens} --predicted-usd {predicted_usd}
- initial_test_exit_code: {initial_tests.returncode}
- post_run_tests_exit_code: {post_exit}

## Prompt

```text
{prompt.rstrip()}
```
"""
    _write(out / "PROMPT.md", note)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default="codex", choices=["codex", "claude", "opencode"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--sandbox", default=None)
    parser.add_argument("--state-root", default="/tmp/witnessd-w10-live-state")
    parser.add_argument("--prompt-file", default=None)
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--claude-binary", default="claude")
    parser.add_argument("--opencode-binary", default="opencode")
    parser.add_argument("--codex-auth-source", default=None)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--max-usd", type=float, default=0.25)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--predicted-tokens", type=int, default=3000)
    parser.add_argument("--predicted-usd", type=float, default=0.05)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    out = Path(args.out).resolve(strict=False)
    evidence_dir = out / "evidence"
    sandbox = Path(args.sandbox).resolve(strict=False) if args.sandbox else out.parent / "w10-sandbox"
    state_root = Path(args.state_root).resolve(strict=False)

    if _paths_overlap(state_root, out):
        print("ERR_W10_STATE_ROOT_INSIDE_FIXTURE", file=sys.stderr)
        return 2
    if out.exists() or sandbox.exists():
        if not args.force:
            print("ERR_W10_OUTPUT_EXISTS", file=sys.stderr)
            return 2
        if out.exists():
            shutil.rmtree(out)
        if sandbox.exists():
            shutil.rmtree(sandbox)

    out.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    prepare_sandbox(sandbox)
    initial_tests = _run_sandbox_tests(sandbox)
    if initial_tests.returncode == 0:
        print("ERR_W10_SANDBOX_TEST_NOT_FAILING", file=sys.stderr)
        return 2
    _remove_python_caches(sandbox)

    prompt = (
        Path(args.prompt_file).read_text(encoding="utf-8")
        if args.prompt_file
        else DEFAULT_PROMPT
    )
    _copy_codex_auth(state_root, args.codex_auth_source)
    predicted_tokens = min(args.predicted_tokens, args.max_tokens)
    predicted_usd = min(args.predicted_usd, args.max_usd)
    fixture_public_key = out / "keys" / "operator.pub"
    (state_root / "operator-keys").mkdir(parents=True, exist_ok=True)
    private_key, generated_public = gen_operator_keypair(str(state_root / "operator-keys"))
    fixture_public_key.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(generated_public, fixture_public_key)

    post_tests = None
    try:
        result = run_adapter_lane(
            root=str(state_root),
            sandbox=str(sandbox),
            adapter=args.adapter,
            task_id="w10-live-agent",
            prompt=prompt,
            arm="direct",
            tier="agentic",
            is_supported=lambda _model: True,
            budget={
                "max_tokens": args.max_tokens,
                "max_usd": args.max_usd,
                "max_depth": args.max_depth,
            },
            predicted_tokens=predicted_tokens,
            predicted_usd=predicted_usd,
            depth=1,
            codex_binary=args.codex_binary,
            claude_binary=args.claude_binary,
            opencode_binary=args.opencode_binary,
            timeout_seconds=args.timeout_seconds,
            evidence_dir=str(evidence_dir),
            private_key_path=private_key,
            public_key_path=str(fixture_public_key),
        )
    except LaneBlocked as exc:
        _write_prompt_note(
            out=out,
            adapter=args.adapter,
            prompt=prompt,
            sandbox=sandbox,
            state_root=state_root,
            evidence_dir=evidence_dir,
            public_key=fixture_public_key,
            args=args,
            initial_tests=initial_tests,
            post_tests=None,
            predicted_tokens=predicted_tokens,
            predicted_usd=predicted_usd,
        )
        print(exc.reason, file=sys.stderr)
        return 1

    post_tests = _run_sandbox_tests(sandbox)
    _write(out / "POST_RUN_TESTS.txt", (post_tests.stdout or "") + (post_tests.stderr or ""))
    _write_prompt_note(
        out=out,
        adapter=args.adapter,
        prompt=prompt,
        sandbox=sandbox,
        state_root=state_root,
        evidence_dir=evidence_dir,
        public_key=fixture_public_key,
        args=args,
        initial_tests=initial_tests,
        post_tests=post_tests,
        predicted_tokens=predicted_tokens,
        predicted_usd=predicted_usd,
    )

    print("1 adapter lane pending Depone verification (evidence-pending)")
    print(f"evidence_dir: {result['evidence_dir']}")
    print(f"runner_kind: {result['runner_receipt']['runner_kind']}")
    print(f"adapter_exit_code: {result['runner_receipt']['exit_code']}")
    print(f"post_run_tests_exit_code: {post_tests.returncode}")
    if result["runner_receipt"]["exit_code"] != 0 or post_tests.returncode != 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
