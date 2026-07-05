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
python3 -m orro scout "inspect repo" --repo .
python3 -m orro flowplan "goal" --root .
python3 -m orro proofrun "goal" --repo . --home .witnessd
python3 -m orro proofcheck .witnessd/runs/<run-dir> \
  --home .witnessd \
  --out .witnessd/runs/<run-dir>/proofcheck-verdict.json
python3 -m orro handoff .witnessd/runs/<run-dir> \
  --out .witnessd/runs/<run-dir>/orro-handoff.json
python3 -m orro doctor --json
python3 -m orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json
```

The module entrypoint delegates to the same wrapper surface as
`python3 -m witnessd orro ...`. It is not a standalone ORRO repo and not a third
engine. It must not replace or break existing `witnessd` commands.

`python3 -m orro --help` is product-facing and lists only public ORRO commands:
`scout`, `flowplan`, `proofrun`, `proofcheck`, `handoff`, `doctor`, and
`engine-lock`. It must not promote witnessd engine-internal commands.

A future console script named `orro` may point at the same wrapper surface, but
it is deferred because this checkout has no packaging metadata for installed
entrypoints. It must remain an alias layer and must be covered by an install
smoke test when packaging exists.

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
- `witnessd orro scout`
- `witnessd orro flowplan`
- `witnessd orro proofrun`
- `witnessd orro proofcheck`
- `witnessd orro handoff`
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
python3 -m witnessd orro engine-lock --home .witnessd --out .witnessd/orro-engine-lock.json
```

`engine-lock` validates the Depone pin recorded in `.witnessd/provision.json` and
reads the local witnessd git commit. If the home is missing or the pin cannot be
validated, it fails closed. It does not fetch network, update Depone, execute
workers, verify evidence, approve merge, or raise assurance. `orro lock` is a
compatibility alias for the same command; `engine-lock` is the public name.

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
