---
name: orro-ship
mode: ship
triggers: ship, push, pull request, PR
boundary: evidence-gated-push-and-human-merge
---

# ORRO ship

Use `orro ship <run-dir> --home <home>` only after the run contains a passing,
bound `proofcheck-verdict.json` and a matching `orro-handoff.json`. It refuses
dirty worktrees, the configured default branch, missing remotes, and all
force-pushes. It may push the current branch and ask `gh` to open a PR.

Shipping writes `ship-receipt.json` as orchestration metadata, not proof. It
never commits, merges, approves a merge, or raises assurance. Merge approval
stays human forever.
