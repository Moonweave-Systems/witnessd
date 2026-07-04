# W17 — Journaled replay-resume

Problem (from SPEC3 §3, plus measured current state)

SPEC3 W17 requires a crash-safe `witnessd team resume <run-dir>` that never trusts a journal or ledger completion claim by itself. Surviving lane bytes may be skipped only when re-derived as passing evidence; absent, partial, or tampered lanes are re-executed into a fresh attempt while older bytes remain preserved. Current W15 code already has `resume_audit` classification and lane-exec control files in `witnessd/fanin.py`; it does not perform replay. Depone main already contains the additive `resume_receipt` validator and Team Ledger optional field.

Contract delta

No new Depone schema is introduced in witnessd Phase 2. Witnessd consumes the existing `depone-team-resume-receipt` contract:
- `boundary.trusts_journal_completion=false`
- `boundary.skip_requires_rederivation=true`
- `boundary.append_only_attempt_history=true`
- decisions are `skipped_as_proven` or `re_executed`
- attempts are positive, contiguous, preserved, and newest attempt matches the decision attempt
- `skipped_as_proven` is valid only when Depone re-derives the referenced lane as `pass`; otherwise Depone rejects with `ERR_TEAM_RESUME_RECEIPT_SKIP_NOT_REDERIVED`

Design

`team resume` loads the existing `.lane-exec` control specs from the interrupted run directory and reuses W15 `resume_audit` as the first survival classifier. For each apparently complete lane, witnessd writes a temporary one-lane Team Ledger and invokes `sys.executable -m depone team-ledger` against the surviving evidence directory. Only a Depone `pass` decision becomes `skipped_as_proven`.

All non-proven lanes are re-executed through the existing W15 lane supervisor/nursery into `attempts/attempt-N/<lane_id>`, where `N` is the next attempt number. The original lane evidence stays in place; witnessd does not overwrite partial or tampered bytes. The final resumed Team Ledger lives at the original run root and links `team-resume-receipt.json`.

Failure semantics are fail-closed: unreadable controls, missing results, malformed results, Depone CLI failure, or non-pass re-derivation all become re-execution targets, not skips. Replay-resume does not implement W18 deployment, distributed execution, or W20 keyless anchoring.

Tasks

- RED: add W17 tests for missing lane result, tampered completed lane, and `team resume` CLI.
- GREEN: add optional `resume_receipt` to witnessd Team Ledger builder.
- GREEN: implement `resume_team` with re-derivation-only skip and append-only attempt directories.
- GREEN: add `witnessd team resume <run-dir>` CLI.
- GREEN: add `fixtures/w17/` and `scripts/revalidate_w17.py`.

Negative fixtures

`fixtures/w17/negative/forged-skipped-team-ledger.json` claims `skipped_as_proven` for a tampered lane whose `evidence-next-verdict.json` is blocked. `scripts/revalidate_w17.py` requires Depone to reject it with `ERR_TEAM_RESUME_RECEIPT_SKIP_NOT_REDERIVED`.

Acceptance bar

- `python3 -m unittest discover -s tests` in Depone.
- `PYTHONPATH=../depone python3 -m unittest discover -s tests` in witnessd.
- `PYTHONPATH=../depone python3 -m witnessd self-test --all`.
- `for s in scripts/revalidate_*.py; do PYTHONPATH=../depone python3 "$s" || exit 1; done`.
- `git diff --check`.
- Export-root re-derivation from `git archive HEAD`.
- No Depone changes in Phase 2; `ingest_signed_evidence_bundle` diff remains zero.
- No `production_gate`, archive, or operator review edits.

Out of scope

W18 deployment/DX, W19 paid live parallel run, W20 keyless anchor/OIDC, distributed replay, and any Depone contract change.

## Outcome — 2026-07-04

Landed in witnessd only:
- `witnessd team resume <run-dir>` performs re-derivation-only skips and re-executes unproven lanes into `attempts/attempt-N`.
- Team Ledger emission links `resume_receipt` additively.
- W17 tests cover missing lane result, tampered completed lane, malformed lane control fail-closed behavior, repeated resume over a successful newer attempt, and CLI.
- W17 fixture proves a tampered lane is re-executed and a clean lane is skipped only after re-derivation.
- Negative fixture proves forged `skipped_as_proven` is rejected by Depone.
- Post-review hardening prevents malformed controls from disappearing from the final resumed ledger and re-derives the newest preserved attempt before deciding whether another rerun is required.

Verification captured before commit:
- `PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest tests.test_w17_journaled_resume tests.test_runtime_depone_decoupling` PASS.
- `PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_w17.py` PASS.
- `PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest discover -s tests` in witnessd PASS, 320 tests.
- `python3 -m unittest discover -s tests` in Depone PASS, 356 tests.
- `PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m witnessd self-test --all` PASS, 24/24.
- `for s in scripts/revalidate_*.py; do PYTHONPATH=/home/ubuntu/moonweave/depone python3 "$s" || exit 1; done` PASS.
- `git diff --check` PASS.
