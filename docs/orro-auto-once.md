# ORRO Auto Once v0

`orro auto --once` is the limited one-step execution mode after `orro next` and
`orro auto --dry-run`.

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

Multi-step autonomous `orro auto` remains future work.
