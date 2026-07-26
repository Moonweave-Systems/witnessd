---
name: orro-inspect
description: Look up ORRO run status, repository tidy inventory, readiness, or workstyle advice without running repository work. Published by Moonweave.
witnessd_version: {witnessd_version}
witnessd_generated: true
---

# orro-inspect - read-only ORRO lookup

Use this skill when an operator wants to look something up: run status, tidy
inventory, doctor readiness, or advise routing. These commands
are observation and planning surfaces; they do not launch repository work or
change the repository.

## Public modes

| Mode | Meaning |
| --- | --- |
| `orro status` | roadmap status, or a run-scoped report with `<run-dir>` or `--latest` |
| `orro tidy` | dry-run worktree inventory |
| `orro doctor` | engine, verifier, adapter, key, MCP, and policy readiness check |
| `orro advise` | non-executing workstyle router for the smallest safe workflow |

## Read-only material

`orro status <run-dir>` renders the existing human-facing report for one run;
`--latest` selects the newest run. Plain status remains the roadmap view and
accepts a run directory for the run-scoped view.

`orro doctor` checks readiness, not evidence truth.

`python3 -m orro advise "<goal>" --repo <repo> --home .witnessd --json` is the
developer-judgment/workstyle layer. It returns an `orro-workstyle-decision` with
the recommended task class, profile, path, skip list, gates, and reasons. It is
non-executing advice only and is not proof, verifier truth, approval, or
assurance. It helps non-developers avoid wasteful or risky AI workflows, but it
does not replace proofrun, proofcheck, handoff, or human review for risky
changes.

`python3 -m orro advise "<goal>" --repo <repo> --home .witnessd --json` routes
workstyle advice and automatically emits the existing advisory artifact for
new-work goals or symptom-shaped goals. Use `--mode route|sketch|trace` to
select the route explicitly. These advisory paths remain non-executing and
preserve their existing artifact schemas.

For roadmap status, tidy retention, task workspaces, and bounded continuation,
load the relevant progressive-disclosure reference. These references do not
create proof or a new assurance source.

## Lookup procedure

1. Choose the lookup mode that matches the question.
2. Use JSON output when the result will be handed to another tool.
3. Report the observed status, paths, blockers, and next safe action exactly as
   returned. A readiness or advisory result is not evidence truth.

The execution skill documents repository work, evidence production, review,
handoff, shipping, and the deterministic demo. This skill does not invoke those
surfaces.

## Boundaries

- Do not launch adapter lanes or workers.
- Do not apply tidy changes or otherwise mutate a repository.
- Do not treat skill text, terminal state, or advisory output as proof.
