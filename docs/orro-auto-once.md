# ORRO Auto Once v0

`orro auto --once` is the limited one-step execution mode after `orro next` and
`orro auto --dry-run`.

It is not the workstyle router. Use `orro advise` before planning/execution to
decide whether proofrun, review-only, verification-only, or human review is the
right path.

```bash
orro auto --once .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m orro auto --once .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m witnessd orro auto --once .witnessd/runs/<run-dir> --home .witnessd --json
```

It re-checks continuation state before acting and executes at most one safe next
step:

- `needs-proofcheck`: run proofcheck once and write
  `proofcheck-verdict.json`.
- `ready-for-handoff`: run handoff once and write `orro-handoff.json`.
- `complete`: no-op successfully.

It does not launch proofrun, launch workers, call live models, call MCP, call
live APIs, execute recipes, repair artifacts, retry failed lanes, resume lanes,
approve merge, or raise assurance. It may mutate only the explicit proofcheck or
handoff output file caused by the allowed single step, plus an explicit `--out`
receipt path when requested.

When `--once` runs proofcheck, verification is delegated to Depone. ORRO does
not verify evidence itself.

The `orro-auto-receipt` is orchestration metadata. It records the before
decision, executed phase, command, exit code, after decision, and files written.
It is not proof of task success, not verifier truth, not merge approval, and not
assurance.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

Broader autonomous `orro auto` and `orro ultra` remain future work.

The next bounded post-run mode is:

```bash
orro auto --until-complete .witnessd/runs/<run-dir> --home .witnessd --max-steps 2 --json
```

It loops over the same allowed post-run steps, proofcheck and handoff, with a
required v0 `--max-steps` bound of 1 or 2. It never launches proofrun or
workers. Its `orro-auto-session` is orchestration metadata, not proof.
