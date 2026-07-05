# witnessd - Agent Context

`witnessd` is the executing runtime engine in the ORRO pair. It spawns workers,
owns durable sessions, creates worktrees, retries, supervises teams, records
verification and MCP/tool receipts, and emits observer-signed evidence. Depone is
the non-executing verifier that re-derives the verdict from those bytes.

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

Moonweave is the publisher/account namespace. ORRO is the product/tool name.
`Superflow` is historical/compatibility naming and should not be used for new
public docs.

## Source of truth

[`SPEC3.md`](SPEC3.md) is the only top-level witnessd product/runtime authority.
`SPEC.md`, `SPEC2.md`, `docs/plans/*`, `docs/conformance/*`, README, `SKILL.md`,
`AGENTS.md`, fixture notes, and release notes are derived, wave-specific, or
historical. If they conflict with `SPEC3.md`, `SPEC3.md` wins.

For the Depone verifier contract itself, Depone's `docs/spec.md` is the
authority. See [`docs/README.md`](docs/README.md) for the witnessd documentation
map and legacy policy.

## Public names

| Public surface | Purpose |
| --- | --- |
| ORRO | Observed Run & Review Orchestrator; flagship product/tool |
| ORRO Flow | scout -> flowplan -> proofrun -> proofcheck -> handoff |
| `orro` | flagship goal -> scout -> plan -> run -> evidence -> verifier summary -> handoff |
| `orro init` | setup readiness/provision metadata; not proof or assurance |
| `orro advise` | non-executing workstyle router for the smallest safe workflow |
| `orro scout` | read-only repo profile, context pack, and discovery notes |
| `flowplan` | plan-only workflow design and rolepack/workflow compiler surface |
| `proofrun` | precise evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro next` | non-executing continuation gate over persisted run artifacts |
| `orro report` | human-facing summary of observed artifacts and next safe action |
| `orro auto --dry-run` | non-executing automation planner; recommendation context only |
| `orro auto --once` | one-step proofcheck/handoff executor; orchestration metadata only |
| `orro auto --until-complete` | bounded post-run proofcheck/handoff loop; orchestration metadata only |
| `orro skillpack` | knowledge-as-code and progressive-disclosure support |
| `orro doctor` | engine/verifier/adapter/key/MCP/policy readiness check |
| `orro auto` | future broader resume/continuation loop behind evidence gates |
| `orro ultra` | future high-autonomy profile with stricter gates |

`witnessd` is the engine name, not the main session skill name.

## Entrypoint and repository boundary

`python3 -m orro ...` is a thin product-name entrypoint hosted in this witnessd
repo. It delegates to the existing `witnessd orro ...` surfaces and does not add
execution or verifier logic. It is not a standalone ORRO repo and not a third
engine.
`python3 -m orro --help` is ORRO-facing and lists only public ORRO commands, not
witnessd engine-internal commands.

`python3 -m orro init --home .witnessd --depone-root ../Depone` is the public
setup path. It delegates to existing witnessd initialization/provisioning and
creates readiness metadata such as `.witnessd/provision.json`. It does not run
ORRO Flow work, verify evidence, approve merge, or raise assurance. Use a local
`--depone-root` for development and tests.

`python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json`
writes distribution metadata for the pinned witnessd and Depone commits.
`python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json`
checks the current local environment for drift against that metadata. A matching
lock is readiness alignment only. A mismatch is readiness-blocked, not
verifier-refuted. The engine lock is not proof, does not verify evidence, does
not approve merge, and does not raise assurance.
`orro doctor` checks readiness, not evidence truth.

`python3 -m orro advise "<goal>" --repo <repo> --home .witnessd --json` is the
developer-judgment/workstyle layer. It recommends the smallest safe workflow and
returns an `orro-workstyle-decision`. It is non-executing advice only: not
proof, verifier truth, approval, or assurance. It helps non-developers avoid
wasteful or risky AI workflows but does not replace proofrun, proofcheck,
handoff, or human review for risky changes.

`python3 -m orro flowplan "<goal>" --root <repo> --profile code-change` compiles
a deterministic `orro-workflow-plan` intent artifact. Supported profiles are
`code-change`, `review-only`, `verification-only`, `docs-change`, and
`release-readiness`. Workflow plans are not evidence. Roles do not create
assurance by existing. `proofrun` is the first execution phase, `proofcheck` is
the verifier phase, `handoff` is review packaging only, and broader autonomous
`orro auto` and `orro ultra` remain future work.

`python3 -m orro proofrun "<goal>" --repo <repo> --home .witnessd --workflow-plan workflow-plan.json`
first applies a phase gate: the plan must allow `proofrun` through a witnessd
engine call that executes and does not verify. If allowed, proofrun records
`workflow-plan.json`, `workflow-plan-binding.json`, and
`workflow-role-dispatch.json` as intended workflow context in the run directory.
These artifacts are not proof that execution followed the plan, not approval,
and not assurance. Depone proofcheck still decides what evidence supports.
`review-only` does not authorize proofrun, and formal `orro handoff` still
requires a passing bound proofcheck verdict.

`python3 -m orro flowplan "<goal>" --root <repo> --profile code-change --role-lanes-out role-lane-plan.json`
writes executable role-lane intent. `python3 -m orro proofrun "<goal>" --repo
<repo> --home .witnessd --workflow-plan workflow-plan.json --role-lane-plan
role-lane-plan.json` validates the role-lane plan against the workflow hash and
executes allowed lanes through existing witnessd team machinery. Role-lane plans
are not proof, approval, or assurance. `review-only`, `verification-only`, and
default `release-readiness` role-lane plans cannot launch proofrun.

`python3 -m orro next <run-dir> --home .witnessd --json` reads persisted run
artifacts and recommends the next safe action. It does not run proofcheck,
launch workers, retry lanes, repair evidence, write handoff, verify evidence,
approve merge, or raise assurance. `needs-proofcheck` means run proofcheck next;
`ready-for-handoff` means a passing bound proofcheck verdict exists; `complete`
means handoff exists after proofcheck pass. Role status is observed context, not
proof.

`python3 -m orro report <run-dir> --home .witnessd --json` is the human-facing
compression layer over observed artifacts. It reports state, next safe action,
proofcheck/handoff status, reviewer focus, and do-not-trust boundaries. It does
not execute, verify evidence, approve merge, raise assurance, replace
proofcheck, or replace human review.

`python3 -m orro auto --dry-run <run-dir> --home .witnessd --json` consumes
the continuation decision and emits an `orro-auto-plan` with the exact command
it would run next. It does not execute that command, call Depone, launch
workers, write proofcheck verdicts, write handoff packages, mutate worktrees,
verify evidence, approve merge, or raise assurance. The auto-plan is
recommendation context only, not proof. Broader autonomous `orro auto` remains
future work.

`python3 -m orro auto --once <run-dir> --home .witnessd --json` re-checks
continuation state and executes at most one allowed step. In v0 that means
proofcheck, handoff, or complete no-op only. It never launches proofrun or
workers, calls live models or MCP, repairs artifacts, retries or resumes lanes,
approves merge, or raises assurance. The auto receipt is orchestration metadata,
not proof or verifier truth.

`python3 -m orro auto --until-complete <run-dir> --home .witnessd --max-steps 2 --json`
is bounded post-run automation over proofcheck and handoff only. It requires
`--max-steps`, re-checks continuation state before every step, and never
launches proofrun or workers. The auto session is orchestration metadata, not
proof, verifier truth, approval, or assurance.

A standalone ORRO repo remains deferred until packaging, marketplace manifests,
host-specific distribution, or version-lock distribution needs justify it.
Console-script packaging for a bare `orro` executable points at
`orro.__main__:main` and must remain an alias layer over the witnessd-hosted ORRO
surface.

## Runtime dependency rule

Runtime deps are Python **stdlib + the `openssl` CLI only**. Never add a
third-party runtime dependency to witnessd core.

Depone may be provisioned or pinned for verification, and tests may import Depone
validators. Shipped witnessd capture/runtime paths must not depend on importing
Depone as a Python package.

## Global workflow rule

ORRO is CLI-first but not IDE-hostile. IDEs are fast human steering surfaces; ORRO
owns the evidence-governed background path:

```text
scout -> flowplan -> proofrun -> proofcheck -> handoff
```

The handoff step is gated by an explicit `proofcheck-verdict.json` written by
`proofcheck --out`. `team-ledger-verdict.json` from proofrun is not enough by
itself. If the proofcheck verdict is missing, unreadable, malformed, or not
`decision: "pass"`, `handoff` / `orro handoff` must fail closed and must not
write `orro-handoff.json`.

Non-trivial runs should use progressive disclosure:

- build `repo-profile.json`,
- build `context-pack.json`,
- write `discovery-notes.md` after every two meaningful read/search actions,
- load skillpack/rule bodies only after frontmatter or path matching,
- create `verification-recipe.json` before implementation when checks exist,
- record `verification-receipt.json` and `mcp-tool-receipt-*.json` when those
  actions occur.

## The Depone contract

`witnessd` emits evidence that must satisfy Depone's contract, which is the source
of truth for capture-manifest / runner-receipt / isolation / DSSE / team-ledger /
verification-recipe / verification-receipt / skillpack-lock / MCP-tool-receipt
schemas and their error codes, plus:

```python
canonical_hash = sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8"))
```

Rules:

- Do not invent schema fields.
- Contract capability changes land in Depone first, then witnessd consumes them.
- Runtime receipt emission belongs in witnessd; receipt verification belongs in
  Depone.

## Testing / dogfood

From the Moonweave workspace:

```bash
cd depone
python3 -m unittest discover -s tests
cd ../witnessd
PYTHONPATH=../depone python3 -m unittest discover -s tests
PYTHONPATH=../depone python3 -m witnessd self-test --all
for script in scripts/revalidate_*.py; do
  PYTHONPATH=../depone python3 "$script"
done
scripts/quickstart_check.sh
```

## Invariants

- Pre-verification user status is `evidence-pending`.
- Worker output is not its own trust verdict; the observer and emitter create the
  evidence that Depone later re-derives.
- Skill text, MCP output, IDE/tmux views, and session transcripts are not
  verdicts by themselves.
- witnessd does not grant A1/A2 final trust by itself.
- Each wave's acceptance bar is a committed fixture plus a revalidator that
  Depone re-derives.
