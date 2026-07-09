# ORRO Productization Roadmap

## Recommendation

Keep the near-term ORRO wrapper inside witnessd. Do not create a standalone
`Moonweave-Systems/ORRO` repository yet.

The current product surface is still a thin workflow wrapper around the
executing runtime and the pinned verifier:

```text
orro scout -> orro flowplan -> orro proofrun -> orro proofcheck -> orro handoff
```

witnessd already owns execution, provisioning, run directories, and local Depone
pinning. A standalone repo would be premature until the work is mostly
distribution packaging rather than runtime UX. Creating it now would add a third
place for agents to put logic, which is the risk this wave is avoiding.

## Current Phase

- Depone and witnessd remain separate engine repositories.
- Depone is the non-executing verifier and proofcheck contract source of truth.
- witnessd is the executing runtime and evidence emitter.
- ORRO is the user-facing workflow surface hosted near witnessd.
- Depone is consumed as a pinned verifier dependency through witnessd init and
  proofcheck paths.

The current entrypoint strategy is:

```bash
python3 -m witnessd orro scout --repo .
python3 -m witnessd orro flowplan "goal" --root .
python3 -m witnessd orro proofrun "goal" --repo . --home .witnessd
python3 -m witnessd orro proofcheck .witnessd/runs/<run-dir> --home .witnessd
python3 -m witnessd orro handoff --proofcheck-verdict .witnessd/runs/<run-dir>/team-ledger-verdict.json
```

A future console script named `orro` may point at this same wrapper surface, but
it must remain an alias layer. It must not replace or break existing `witnessd`
commands.

## Engine Boundary Contract

Required invariant:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

### Depone Verifier API Surface

Allowed ORRO calls into Depone are verifier calls over persisted bytes:

- `python3 -m depone proofcheck --evidence-dir <dir> --json`
- `python3 -m depone team-ledger --ledger <path> --base-dir <dir> --out <path> --json`
- Depone library validators in tests and conformance checks
- Published schema, error-code, and canonical hash contracts

Depone must not spawn workers, mutate user worktrees, fetch runtime assets, or
raise assurance from ORRO narration.

### witnessd Runtime API Surface

Allowed ORRO calls into witnessd are runtime and wrapper calls:

- `witnessd init`
- `witnessd orro scout`
- `witnessd orro flowplan`
- `witnessd orro proofrun`
- `witnessd orro proofcheck`
- `witnessd orro handoff`
- existing lower-level `witnessd run`, `witnessd verify`, and `witnessd team *`
  commands used as implementation surfaces

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
    "ref": "main",
    "commit": "<40-hex-commit>"
  },
  "depone": {
    "repository": "Moonweave-Systems/Depone",
    "ref": "main",
    "commit": "<40-hex-commit>"
  }
}
```

The lock is product distribution metadata. It is not a schema extension, trust
verdict, or replacement for Depone's persisted evidence verification.

## E2E Smoke Contract

The ORRO wrapper must eventually test the full public flow:

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
- handoff requires an explicit passing `proofcheck-verdict.json`;
- handoff does not approve merge or raise assurance.

## Standalone ORRO Repo Trigger

Create `Moonweave-Systems/ORRO` only when at least one of these is true:

- marketplace/plugin manifests need a repo that is not an engine repo;
- packaging needs a product-level release artifact that locks both engine SHAs;
- examples and e2e integration tests become larger than witnessd's runtime docs;
- multiple host integrations need one product README and version lock source;
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
