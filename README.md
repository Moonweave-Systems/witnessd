# witnessd

`witnessd` is the executing half of Moonweave. It runs local lanes, records what
happened, signs the evidence, and leaves bytes that Depone can re-derive
offline. The runner installs witnessd; the auditor can install only Depone and
check the emitted run directory.

## 10-minute quickstart

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

For the same path as CI:

```bash
WITNESSD_DEPONE_ROOT=../depone scripts/quickstart_check.sh
```

Expected output:

```text
quickstart_check: pass
```

## What The Commands Do

`witnessd init` creates:

- `.witnessd/config.json`
- `.witnessd/provision.json`
- `.witnessd/keys/`

The provision record pins the local Depone checkout by git commit and records
the witnessd commit. Setup may use network only when explicitly allowed by the
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

## Session Skill

This repo ships two in-session guidance files:

- `SKILL.md` for Claude Code style skill installation
- `AGENTS.md` for Codex sessions

Both instruct the session agent to design lanes, run witnessd, then report the
Depone verdict from `team-ledger-verdict.json`. A session transcript or lane
self-report is not a verdict.

## Manual Team Runs

The lower-level team command remains available for explicit lanes:

```bash
python3 -m witnessd team run \
  --repo . \
  --out /tmp/witnessd-team \
  --lane alpha:pkg/alpha.txt \
  --lane beta:pkg/beta.txt
python3 -m witnessd verify /tmp/witnessd-team --home .witnessd
```

Use `--merge-group` when overlapping lane regions are intentionally reconciled
by a merge lane.

## Auditor Path

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

## Honest Limits

- W18 is single-host execution. Distributed lanes are out of scope.
- The default quickstart uses shell lanes for quota-free validation.
- Live paid agent lanes are W19 and require an operator checkpoint.
- Keyless transparency anchoring is W20; W18 uses operator-key DSSE bundles.
- A2 isolation still depends on host uid setup where that path is used.
- Clean-machine macOS validation and release publication are operator actions.

## Development Checks

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

Design source of truth:

- `SPEC3.md`
- `docs/plans/GOALMODE.md`
- `docs/plans/2026-07-04-w18-distribution-dx.md`
