# witnessd

`witnessd` is the executing runtime engine for **ORRO** (Observed Run & Review
Orchestrator), published under the Moonweave account. It runs local lanes,
records what happened, signs the evidence, and leaves bytes that Depone can
re-derive offline.

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

`Superflow` was the earlier product-surface name. New public docs should use
ORRO. Existing `superflow` commands, fixture paths, or artifact kinds may remain
as compatibility aliases during migration.

## 10-minute quickstart

```bash
cd witnessd
python3 -m witnessd init --home .witnessd --depone-root ../depone
python3 -m witnessd scout "map the repo before planning" --repo . --home .witnessd
python3 -m witnessd run "write two independent files" --repo . --home .witnessd
python3 -m witnessd verify .witnessd/runs/<run-dir> --home .witnessd
```

The `run` command prints JSON. Use its `run_dir` field for the verify step.
For the public ORRO handoff path, run `proofcheck` with an explicit
`proofcheck-verdict.json` output before packaging the handoff:

```bash
python3 -m witnessd proofcheck .witnessd/runs/<run-dir> \
  --home .witnessd \
  --out .witnessd/runs/<run-dir>/proofcheck-verdict.json
python3 -m witnessd orro handoff .witnessd/runs/<run-dir> \
  --out .witnessd/runs/<run-dir>/orro-handoff.json
```

`team-ledger-verdict.json` emitted during a proofrun is not enough by itself for
handoff. `handoff` / `orro handoff` fails closed unless
`proofcheck-verdict.json` exists, is readable JSON, and has `decision: "pass"`.

## Honest limits

witnessd may emit self-declared runtime facts and `DELAYED_NOTARY` style
post-hoc records, but those records do not upgrade trust. A2 requires a
dedicated observer uid, a separate runner, and observer-owned evidence paths that
are not writable by the runner. Depone decides what the persisted bytes support.

## Source of truth

[`SPEC3.md`](SPEC3.md) is the only top-level witnessd product/runtime authority.
`SPEC.md`, `SPEC2.md`, `docs/plans/*`, `docs/conformance/*`, README, `SKILL.md`,
`AGENTS.md`, fixture notes, and release notes are derived, wave-specific, or
historical. If they conflict with `SPEC3.md`, `SPEC3.md` wins.

For the Depone verifier contract itself, Depone's `docs/spec.md` is the
authority. For the repo documentation map, see [`docs/README.md`](docs/README.md).

## User-facing names

| Public surface | Purpose |
| --- | --- |
| ORRO | flagship product/tool: evidence-governed agent workflow orchestrator |
| ORRO Flow | `scout -> flowplan -> proofrun -> proofcheck -> handoff` |
| `orro` | flagship goal -> scout -> plan -> run -> evidence -> verifier summary -> handoff |
| `orro scout` | read-only repo exploration, repo profile, context pack, and discovery notes |
| `flowplan` | plan-only workflow design |
| `proofrun` | precise evidence-backed execution alias |
| `proofcheck` | offline evidence verification alias |
| `orro handoff` | maintainer review package bound to an explicit passing `proofcheck-verdict.json` |
| `orro skillpack` | knowledge-as-code and progressive-disclosure support |
| `orro doctor` | engine, verifier, adapter, key, MCP, and policy readiness check |
| `orro auto` | later resume/continuation loop behind evidence gates |
| `orro ultra` | future high-autonomy profile with stricter gates |

`witnessd` is the engine name, not the main session skill name. `Moonweave` is the
publisher/account namespace, not the tool name.

## Repository strategy

Development currently stays in two engine repositories:

```text
Depone   = verifier engine and evidence contract
witnessd = execution engine, evidence emitter, and near-term ORRO surface
```

The user-facing install should still be one thing: ORRO. Do not ask normal users
to install separate Depone and witnessd skills. In the near term, this repo may
ship the thin `orro` command/skill because ORRO starts execution and witnessd owns
execution. Depone remains a pinned verifier dependency.

Create a separate `ORRO` repository only when distribution needs justify it:
marketplace manifests, host-specific plugin packaging, version locking, examples,
product docs, and end-to-end integration tests. That future repo is a wrapper and
distribution repo, not a third engine; it must not duplicate witnessd runtime
logic or Depone verifier logic.

## Operating model

ORRO is an evidence-backed agent-team operating surface. The normal loop is:

```text
scout -> flowplan -> proofrun -> proofcheck -> handoff
```

The scout step uses progressive disclosure instead of loading a whole repository
into one model context. It produces:

- `repo-profile.json`
- `context-pack.json`
- `discovery-notes.md`
- optional `skillpack-lock.json`

Runnable lanes may include:

- `verification-recipe.json` for intended checks,
- `verification-receipt.json` for actual command execution,
- `mcp-tool-receipt-*.json` for declared external tool bridge calls,
- `pr-handoff.json` for maintainer review.

Scout does not write `verification-receipt.json`; it has not run the recipe.
Depone proofcheck treats a scout-only artifact directory as planning evidence,
not proof of execution.

Depone decides what these bytes support. Skill text, MCP output, IDE terminals,
tmux panes, and session transcripts are not verdicts by themselves.
The handoff step packages reviewed evidence only after an explicit passing
`proofcheck-verdict.json`; it does not verify evidence, approve merge, or raise
assurance.

## Setup details

Prerequisites:

- Python 3.10 or newer
- `git`
- `openssl`
- a local Depone checkout or provisioned Depone pin

From a checkout with Depone next to witnessd:

```bash
cd witnessd
python3 -m witnessd init --home .witnessd --depone-root ../depone
python3 -m witnessd run "write two independent files" --repo . --home .witnessd
python3 -m witnessd verify .witnessd/runs/<run-dir> --home .witnessd
```

The `run` command prints JSON. Use its `run_dir` field for the verify step:

```bash
run_json="$(python3 -m witnessd run "write two independent files" --repo . --home .witnessd)"
run_dir="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["run_dir"])' "$run_json")"
python3 -m witnessd verify "$run_dir" --home .witnessd
```

On a runner machine without a local Depone checkout, setup can provision the
pinned verifier into `.witnessd/depone-pinned` and record that setup-time network
use:

```bash
python3 -m witnessd init --home .witnessd --allow-network
```

For the same path as CI:

```bash
WITNESSD_DEPONE_ROOT=../depone scripts/quickstart_check.sh
```

Expected output:

```text
quickstart_check: pass
```

## What the commands do

`witnessd init` creates:

- `.witnessd/config.json`
- `.witnessd/provision.json`
- `.witnessd/keys/`

The provision record pins the local Depone checkout by git commit and records the
witnessd commit. Setup may use network only when explicitly allowed by the
operator. Runtime and verify commands do not fetch or install.

`witnessd run "<goal>" --repo <path>` uses the W18 quota-free shell path by
default. It creates a run directory containing:

- `sealed-plan.json`
- `dispatch-log.jsonl`
- lane evidence directories
- `team-schedule-receipt.json`
- `team-ledger.json`
- `team-ledger-verdict.json`

`witnessd verify <run-dir>` validates the pinned Depone record, invokes Depone
through `python3 -m depone team-ledger`, and rewrites
`team-ledger-verdict.json` from the run bytes.

`witnessd proofcheck <run-dir> --out <run-dir>/proofcheck-verdict.json`
delegates to Depone's proofcheck path and writes the public ORRO verdict artifact
required by `handoff` / `orro handoff`. A missing, malformed, unreadable, or
non-pass `proofcheck-verdict.json` blocks handoff and does not write
`orro-handoff.json`.

## Session skill

This repo ships two in-session guidance files:

- `SKILL.md` for host skill installation
- `AGENTS.md` for Codex sessions

Both instruct the session agent to scout when useful, design lanes, run witnessd,
then report the Depone verdict. `team-ledger-verdict.json` records the proofrun
team-ledger check; `proofcheck-verdict.json` is the explicit public verdict
artifact required before handoff. A session transcript or lane self-report is not
a verdict, and a self-declared success claim remains evidence-pending until
Depone re-derives the run bytes.

## Auditor path

An auditor does not need witnessd to execute anything. Given a run directory and
Depone:

```bash
python3 -m depone team-ledger \
  --ledger <run-dir>/team-ledger.json \
  --base-dir <run-dir> \
  --out <run-dir>/team-ledger-verdict.json \
  --json
```

Depone verifies from bytes. It does not run lanes.

## Development checks

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
