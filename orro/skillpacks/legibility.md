---
name: orro-legibility
mode: legibility
triggers: status, tidy, task, roadmap, auto --run-item, continuation, workspace cleanup
boundary: observed-state-and-bounded-automation
---

# ORRO legibility

Use this reference when an operator needs a readable roadmap state, workspace
inventory, or bounded continuation action. These surfaces summarize persisted
artifacts and live Git state; they are not proof, approval, or assurance.

`orro status` derives roadmap item and step state from bound run artifacts and
shows evidence references only when the observed state supports them. `orro
tidy` is dry-run by default and inventories worktrees without deleting run
directories. `orro tidy --apply` removes only eligible clean worktrees; with
`--keep-checks N`, it additionally removes only the oldest `check-*` directories
that contain `companion-manifest.json`, never flow/team evidence. The default
does not remove check runs. Current item evidence is retained with the reason
`kept: item evidence`.

Roadmap item commands carry explicit item/step bindings; do not infer them.
`orro auto --run-item <id> --repo <repo> --home <home> --max-steps N` executes
only declared executable steps, stops at the first non-pass state, and records
the actual run directory. Its execution boundary is distinct from the
non-executing `auto --dry-run`, `auto --once`, and `auto --until-complete`
continuation modes.
