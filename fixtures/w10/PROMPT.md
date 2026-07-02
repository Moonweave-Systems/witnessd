# W10 Live-Agent Prompt Provenance

- generated_at: 2026-07-02T14:36:00Z
- adapter: codex
- runner_kind: codex-cli
- sandbox: /tmp/witnessd-w10-live-sandbox-20260702T1429Z
- isolated_state_root: /tmp/witnessd-w10-live-state-20260702T1429Z
- evidence_dir: /home/ubuntu/moonweave/witnessd/fixtures/w10/evidence
- public_key: /home/ubuntu/moonweave/witnessd/fixtures/w10/keys/operator.pub
- budget_flags: --max-tokens 12000 --max-usd 0.25 --max-depth 1
- predicted_budget: --predicted-tokens 3000 --predicted-usd 0.05
- initial_test_exit_code: 1
- post_run_tests_exit_code: 0

## Prompt

```text
You are in a temporary git repository.

Task: implement score_phrase in wordscore/core.py so the existing failing tests pass.

Requirements:
- Only edit wordscore/core.py.
- score_phrase(text: str) must lowercase whitespace-separated words and return a dict of word counts.
- Do not modify tests.
- Run: python3 -m unittest discover -s tests
- Do not add dependencies.
- Do not print the words VERIFIED or DONE as a verdict.
```
