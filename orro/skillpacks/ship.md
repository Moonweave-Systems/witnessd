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

After a successful push, ship also posts one `ORRO guardrail receipt` commit
status when `gh` can reach a GitHub remote. Creating check runs requires a
GitHub App installation, so ORRO publishes this receipt pointer as a commit
status, which works with the operator's own credentials. The status is
orchestration metadata rather than proof; the full claim remains in the PR
body. When `gh`, GitHub remote parsing, or permission is unavailable, ship
skips it honestly and records the reason.
