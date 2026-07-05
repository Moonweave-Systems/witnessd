# ORRO Productization Roadmap

## Recommendation

Do not create `Moonweave-Systems/ORRO` yet. Keep the near-term ORRO wrapper in
witnessd.

witnessd already hosts the thin user-facing ORRO surface:

```text
scout -> flowplan -> proofrun -> proofcheck -> handoff
```

That surface calls existing engine paths. It does not justify a third repository
until the remaining work is mostly packaging and distribution rather than
runtime UX.

The invariant remains:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

## Current Phase

- Depone and witnessd remain separate engine repositories.
- Depone is the non-executing verifier and proofcheck contract source of truth.
- witnessd is the executing runtime, evidence emitter, and near-term ORRO
  wrapper host.
- Depone is consumed as a pinned verifier dependency.
- ORRO handoff requires an explicit passing `proofcheck-verdict.json` produced
  for the current evidence snapshot.

The current entrypoint strategy is `python3 -m orro`, hosted inside witnessd:

```bash
python3 -m orro init --home .witnessd --depone-root ../Depone
python3 -m orro doctor --home .witnessd --json
python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json
python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json
python3 -m orro advise "goal" --repo . --home .witnessd --json
python3 -m orro scout "inspect repo" --repo .
python3 -m orro flowplan "goal" --root .
python3 -m orro flowplan "goal" --root . --profile code-change --out workflow-plan.json
python3 -m orro proofrun "goal" --repo . --home .witnessd --workflow-plan workflow-plan.json
python3 -m orro proofcheck .witnessd/runs/<run-dir> \
  --home .witnessd \
  --out .witnessd/runs/<run-dir>/proofcheck-verdict.json
python3 -m orro next .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m orro auto --dry-run .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m orro auto --once .witnessd/runs/<run-dir> --home .witnessd --json
python3 -m orro auto --until-complete .witnessd/runs/<run-dir> --home .witnessd --max-steps 2 --json
python3 -m orro handoff .witnessd/runs/<run-dir> \
  --out .witnessd/runs/<run-dir>/orro-handoff.json
```

The module entrypoint delegates to the same wrapper surface as
`python3 -m witnessd orro ...`. It is not a standalone ORRO repo and not a third
engine. It must not replace or break existing `witnessd` commands.

`orro init` delegates to existing witnessd initialization/provisioning and
creates readiness metadata such as `.witnessd/provision.json`. It does not run
ORRO Flow work, verify evidence, approve merge, or raise assurance. Use a local
`--depone-root` for development and tests. If no local Depone root is supplied,
preserve the existing witnessd initialization behavior.

`python3 -m orro --help` is product-facing and lists only public ORRO commands:
`init`, `scout`, `flowplan`, `proofrun`, `proofcheck`, `handoff`, `next`,
`auto`, `doctor`, and `engine-lock`. It must not promote witnessd engine-internal
commands.

The console script named `orro` points at the same wrapper surface through
`orro.__main__:main`. It must remain an alias layer and is covered by an install
smoke test. Marketplace manifests and a standalone ORRO repository remain
deferred.

The current workflow compiler is `orro flowplan --profile <profile>`. It emits a
deterministic `orro-workflow-plan` intent artifact for `code-change`,
`review-only`, `verification-only`, `docs-change`, and `release-readiness`.
Workflow plans map goals to roles, phases, engine calls, gates, and forbidden
assurance sources. They are not evidence. Roles do not create assurance by
existing. `proofrun` is the first execution phase, `proofcheck` is the verifier
phase, `handoff` is review packaging only, and broader autonomous `orro auto`
and `orro ultra` remain future work.

`proofrun --workflow-plan <path>` gates execution against workflow-plan intent
before any run directory is created. The plan must allow `proofrun` through a
witnessd engine call that executes and does not verify. If allowed, proofrun
records `workflow-plan.json`, `workflow-plan-binding.json`, and
`workflow-role-dispatch.json`. The binding lets proofcheck and handoff preserve
the intended workflow hash for review, and role dispatch maps roles to actual or
pending engine phases. These artifacts are not proof, evidence verification,
approval, or assurance. A `review-only` profile remains review intent and does
not authorize proofrun; actual `orro handoff` still requires a passing
proofcheck verdict bound to the current evidence snapshot.

`flowplan --role-lanes-out <path>` now bridges rolepacks to witnessd team lane
intent by writing `orro-role-lane-plan`. `proofrun --role-lane-plan <path>`
validates the role-lane plan against the workflow-plan hash and executes allowed
lanes through existing witnessd team machinery. This closes the bridge from
rolepack intent to team lanes without creating ORRO as a third engine. The
role-lane plan and role dispatch remain review context, not proof or assurance.

`orro next <run-dir> --home <home> --json` is the pre-auto continuation gate. It
reads run-directory artifacts and reports whether the safest next action is
proofcheck, handoff, complete, blocked, evidence-pending, or invalid-run-dir. It
does not execute, run proofcheck, call live models, repair evidence, retry
lanes, approve merge, verify evidence, or raise assurance. Role status is
derived only from observed artifacts. Future `orro auto` must consume this kind
of decision before attempting any continuation.

`orro auto --dry-run <run-dir> --home <home> --json` is the first auto surface.
It consumes `orro next` state and emits an `orro-auto-plan` with the exact next
command it would run. It does not execute that command, call Depone, run
proofcheck, write handoff, launch workers, mutate worktrees, approve merge,
verify evidence, or raise assurance. The auto-plan is recommendation context,
not proof. Broader autonomous `orro auto` remains deferred behind the dry-run,
once, and until-complete contracts.

`orro auto --once <run-dir> --home <home> --json` is the first limited execution
surface. It re-checks continuation state and executes at most one allowed step:
proofcheck, handoff, or complete no-op. It never launches proofrun or workers,
calls live models or MCP, repairs artifacts, retries or resumes lanes, approves
merge, or raises assurance. The auto receipt is orchestration metadata, not
proof or verifier truth.

`orro auto --until-complete <run-dir> --home <home> --max-steps 2 --json` is the
bounded post-run loop. It may run proofcheck and handoff, re-checking
continuation state before every step, but never launches proofrun or workers.
The auto session is orchestration metadata, not proof or verifier truth. Broader
autonomous `orro auto` remains deferred.

`orro advise "<goal>" --repo <repo> --home <home> --json` is the workstyle
router and developer-judgment layer. It recommends the smallest safe workflow
before planning or execution, classifies goals deterministically, and explains
which actions to skip. Its `orro-workstyle-decision` is non-executing advice,
not proof, verifier truth, approval, or assurance. Future LLM-based routing must
remain policy-gated and advisory unless a separate execution gate authorizes
action.

## Engine Boundary Contract

### Depone Verifier API Surface

Allowed ORRO calls into Depone are verifier calls over persisted bytes:

- `python3 -m depone proofcheck --evidence-dir <dir> --json`
- `python3 -m depone team-ledger --ledger <path> --base-dir <dir> --out <path> --json`
- Depone library validators in tests and conformance checks
- published schema, error-code, and canonical hash contracts

Depone must not spawn workers, mutate user worktrees, fetch runtime assets, or
raise assurance from ORRO narration.

### witnessd Runtime API Surface

Allowed ORRO calls into witnessd are runtime and wrapper calls:

- `witnessd init`
- `witnessd orro init`
- `witnessd orro scout`
- `witnessd orro flowplan`
- `witnessd orro proofrun`
- `witnessd orro proofcheck`
- `witnessd orro handoff`
- `witnessd orro next`
- `witnessd orro auto --dry-run`
- `witnessd orro auto --once`
- `witnessd orro auto --until-complete`
- existing lower-level `witnessd run`, `witnessd verify`, `witnessd proofcheck`,
  and `witnessd team *` commands used as implementation surfaces

witnessd must not duplicate Depone proofcheck logic or claim final trust from
its own transcript.

### ORRO Wrapper Responsibilities

ORRO owns product workflow shape, user-facing terminology, examples, install
guidance, version locks, and e2e smoke tests. It translates user intent into the
engine calls above and reports only what the engines emitted or verified.

### Forbidden Dependencies

- Depone must not depend on witnessd runtime code.
- witnessd runtime code must not require Depone as an import-time runtime
  dependency; verifier interaction goes through the pinned verifier path.
- ORRO must not contain verifier implementation or runtime execution logic.
- No repo may introduce a third engine for proofrun or proofcheck.

### Allowed Call Graph

```text
user
  -> ORRO wrapper
      -> witnessd runtime commands
          -> evidence bytes
      -> Depone verifier commands
          -> verdict bytes
      -> ORRO handoff summary
```

Forbidden call graph:

```text
Depone -> witnessd runtime
witnessd -> duplicated Depone validators
ORRO -> new execution engine
ORRO -> new verifier engine
```

## Version Lock Format

A standalone wrapper or packaged ORRO release must lock both engines explicitly:

```json
{
  "kind": "orro-engine-lock",
  "schema_version": "1.0",
  "witnessd": {
    "repository": "Moonweave-Systems/witnessd",
    "commit": "<40-hex-commit>",
    "ref_name": "<optional provenance label>"
  },
  "depone": {
    "repository": "Moonweave-Systems/Depone",
    "commit": "<40-hex-commit>",
    "ref_name": "<optional provenance label>"
  },
  "boundary": {
    "approves_merge": false,
    "raises_assurance": false,
    "executes_commands": false,
    "verifies_evidence": false
  }
}
```

The lock is product distribution metadata. It is not a schema extension, trust
verdict, or replacement for Depone's persisted evidence verification. The
`commit` fields are authoritative. `ref_name`, when present, is descriptive
provenance only and must not be used as a moving target.

The current v0 command is:

```bash
python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json
python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json
python3 -m witnessd orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json
python3 -m witnessd orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json
```

`engine-lock --out` validates the Depone pin recorded in
`.witnessd/provision.json` and reads the local witnessd git commit before writing
metadata. `engine-lock --check` reads an existing lock and compares it with the
current local witnessd commit, validated Depone pin, repositories, schema, and
boundary flags. If the home is missing, the pin cannot be validated, the lock is
missing or malformed, or the fields mismatch, it fails closed. A matching lock is
distribution/readiness alignment only. A mismatch is readiness-blocked, not
verifier-refuted. It does not fetch network, update Depone, execute workers,
verify evidence, approve merge, or raise assurance. `orro lock` is a
compatibility alias for the same command; `engine-lock` is the public name.

`orro doctor` checks readiness, not evidence truth. It may report setup or
engine-lock drift as readiness-blocked; that is not Depone verifier refutation.

## E2E Smoke Contract

The ORRO wrapper must test the full public flow:

- `orro scout`
- `orro flowplan`
- `orro proofrun`
- `orro proofcheck`
- `orro handoff`

Required assertions:

- scout-only directory does not pass proofcheck;
- flowplan does not run workers;
- proofrun emits evidence but does not claim final trust;
- proofcheck delegates to Depone;
- handoff requires an explicit passing `proofcheck-verdict.json` with an ORRO
  binding for the current evidence snapshot;
- handoff does not approve merge or raise assurance.

## Standalone ORRO Repo Trigger

Create `Moonweave-Systems/ORRO` only when at least one of these is true:

- marketplace/plugin manifests need a repo that is not an engine repo;
- packaging needs a product-level release artifact that locks both engine SHAs
  by commit;
- host install orchestration needs to install both engines from pinned release
  artifacts rather than a witnessd checkout;
- marketplace or host-specific plugin manifests need a product repo that is not
  an engine repo;
- the wrapper needs CI that installs both engines from pinned releases without
  importing either as implementation code.

Allowed standalone contents:

- product README;
- architecture docs;
- wrapper CLI plan;
- examples;
- version lock files;
- end-to-end integration test plan;
- packaging or marketplace manifest drafts.

Forbidden standalone contents:

- Depone verifier implementation;
- witnessd runtime implementation;
- duplicate proofcheck logic;
- duplicate proofrun/run logic;
- a new execution engine;
- a new verifier engine.

## Future Standalone Skeleton

If the trigger is met, create only this skeleton first:

```text
ORRO/
  README.md
  docs/
    architecture.md
    version-lock.md
    e2e-smoke-contract.md
  examples/
    full-flow.md
  packaging/
    marketplace-manifest.draft.json
```

The first PR should contain docs and manifest drafts only. Engine code stays in
Depone and witnessd.
