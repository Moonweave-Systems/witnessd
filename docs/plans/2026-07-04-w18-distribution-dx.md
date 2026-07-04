# W18 — Distribution & DX

## Problem

SPEC3 says W18 makes the tool installable and usable by a runner who installs
only `witnessd`, while an auditor installs only Depone. The current state is
close in pieces but still hand-wired:

- SPEC3 requires `witnessd init` to create config, a keys directory, and a
  pinned Depone provision, recording both repo hashes (`SPEC3.md:195`).
- SPEC3 requires `witnessd run "<goal>" --repo <path>` to cover plan, parallel
  lanes, evidence, and verdict, and `witnessd verify <run-dir>` to invoke local
  Depone re-derivation (`SPEC3.md:199`).
- SPEC3 makes in-session skill packaging a primary runner UX: `SKILL.md` plus
  `AGENTS.md` must drive witnessd from inside an agent session and report only
  a Depone-re-derived verdict (`SPEC3.md:204`).
- GOALMODE requires the wave plan first, a full regression floor, no hardcoded
  machine paths, `sys.executable` in subprocesses, and no `codex`/`uv`
  assumption in CI (`docs/plans/GOALMODE.md:30`, `docs/plans/GOALMODE.md:59`).
- The CLI has the raw pieces: legacy single-lane `run`, `team run`,
  `team plan-run`, `team-ledger`, and W17 `team resume`
  (`witnessd/__main__.py:1113`, `witnessd/__main__.py:1234`,
  `witnessd/__main__.py:1259`, `witnessd/__main__.py:1290`,
  `witnessd/__main__.py:1303`).
- Existing CI runs unit tests, all revalidators, runtime decoupling, and
  no-overclaim checks, but has no quickstart job (`.github/workflows/ci.yml:8`).
- The README still leads with architecture/history and manual sibling Depone
  wiring rather than a 10-minute install/run/verify path (`README.md:118`).

## Contract Delta

No Depone contract delta in W18. Witnessd consumes the existing Team Ledger,
schedule, merge, and resume receipt contracts. Depone remains the non-executing
offline verifier.

Witnessd-local evidence additions are metadata only:

- init/provision metadata records witnessd commit, Depone commit/tag/source, the
  config path, keys path, and whether setup used network.
- verify checks that the provision metadata still matches the local pinned
  Depone checkout/path before invoking Depone.
- quickstart fixtures are witnessd-local W18 fixtures; Depone still validates
  the resulting team ledger bytes through its existing `team-ledger` command.

## Design

### `witnessd init`

`witnessd init` creates a user-selected home (default `.witnessd` under the
current repo or `$WITNESSD_HOME` when present), writes `config.json`, creates
`keys/` with mode `0600`, and records `provision.json`.

The Depone provision strategy is local-first:

1. If `--depone-root` or `WITNESSD_DEPONE_ROOT` is supplied, record that
   checkout as the pinned verifier.
2. Otherwise, setup may clone/install the pinned Depone source into an isolated
   directory under witnessd home. That network use is setup-only and is recorded
   in `provision.json`.
3. Runtime and verify paths do not clone, pip install, fetch, or otherwise use
   network. If the pinned verifier is missing or hash-mismatched, they fail
   closed with an explicit error.

### Ergonomic `run`

The top-level `witnessd run "<goal>" --repo <path>` becomes the runner path. It
uses the existing W11 heuristic planner and W15/W16 fan-in:

- default adapter is `shell` for quota-free CI and quickstart;
- default output is a timestamped run directory under `.witnessd/runs/`;
- advanced flags preserve today's explicit `--runner-sandbox`, `--out`, `--log`,
  adapter binaries, budget knobs, and team options;
- it writes sealed plan, dispatch log, team lane evidence, team ledger, and a
  Depone verdict JSON by invoking the pinned verifier offline.

Legacy single-lane behavior remains available when explicit legacy lane flags
(`--runner-sandbox` plus command) are present.

### `witnessd verify <run-dir>`

`verify` becomes a run-directory command. It finds `team-ledger.json`, validates
the init provision pin, invokes Depone with `sys.executable -m depone
team-ledger`, writes `team-ledger-verdict.json`, and prints the verdict
decision. Old runlog verification remains available as an advanced override via
`--runlog`.

### Quickstart and CI

`scripts/quickstart_check.sh` creates a temporary repo, runs:

1. `python3 -m witnessd init --home <tmp-home> --depone-root <depone>`;
2. `python3 -m witnessd run "<goal>" --repo <repo> --home <tmp-home>`;
3. `python3 -m witnessd verify <run-dir> --home <tmp-home>`.

It uses shell lanes, plain `python3`, and no `codex` or `uv`. CI adds a
quickstart job that clones Depone and runs the script with `WITNESSD_DEPONE_ROOT`.

### Skill and Session Guidance

`SKILL.md` and `AGENTS.md` instruct in-session agents to design explicit lanes
or use Depone design output when available, run witnessd, then report only the
Depone verdict and paths. They must preserve the evidence-pending invariant:
no standalone completion claim without a verdict file whose decision was
re-derived by Depone.

### Operator Artifacts

Prepare docs for D4 reverse-conformance PAT setup and a CI workflow change that
references a secret name only. Actual PAT creation/registration remains an
operator checkpoint. Prepare release notes draft from v2.1.0 forward; release
publication remains operator-only.

## Tasks

1. RED: add tests for `init` provision metadata, keys mode, commit hash capture,
   and forged pin rejection.
   GREEN: implement `witnessd.distribution` helpers and `witnessd init`.
2. RED: add CLI tests for `run "<goal>" --repo <path>` and `verify <run-dir>`
   using shell lanes and local Depone.
   GREEN: wire ergonomic run/verify to existing planner/fan-in/Depone paths,
   keeping legacy flags working.
3. RED: add quickstart script test or smoke invocation that fails before the
   script exists.
   GREEN: add `scripts/quickstart_check.sh` and CI job.
4. RED: add no-overclaim text checks for `SKILL.md` and `AGENTS.md`.
   GREEN: add session skill/guidance files.
5. RED: add docs presence checks for release notes and PAT setup.
   GREEN: rewrite README, add release notes draft, add PAT/reverse-conformance
   operator doc and workflow reference.
6. Run focused tests after each slice, then the GOALMODE floor.

## Negative Fixtures

- Forged provision pin: alter recorded Depone hash and prove `witnessd verify`
  rejects before invoking a verdict as trusted.
- Skill overclaim: a static fixture/check rejects standalone `DONE`,
  `VERIFIED`, or equivalent completion text not tied to a Depone verdict.
- Network leak: runtime/verify paths are covered by tests that monkeypatch the
  setup network/provision functions and prove no setup action is called.

## Acceptance Bar

Runnable local evidence before W18 is complete:

```bash
cd ../depone
python3 -m unittest discover -s tests
cd ../witnessd
PYTHONPATH=../depone python3 -m unittest discover -s tests
PYTHONPATH=../depone python3 -m witnessd self-test --all
for s in scripts/revalidate_*.py; do PYTHONPATH=../depone python3 "$s" || exit 1; done
scripts/quickstart_check.sh
git diff --check
tmp=$(mktemp -d); git archive HEAD | tar -x -C "$tmp"
cd "$tmp" && PYTHONPATH="$(cd ../depone && pwd)" python3 scripts/revalidate_key_rotation.py
```

Also confirm:

- `production_gate.status` stays open and archive/operator review files are not
  edited.
- `depone/agent_fabric/evidence_substrate.py::ingest_signed_evidence_bundle`
  has diff 0.
- `witnessd run` and `witnessd verify` work without `codex`, `uv`, or network.
- The clean-machine macOS/new-environment quickstart remains an operator
  checkpoint, reported but not claimed.

## Out of Scope

- W17.5 workflow-plan contract and Depone DWM compile execution bridge.
- W19 paid live Codex lanes.
- W20 keyless/sigstore/transparency anchoring.
- W21 declarative policy layer.
- W22 repo publication and actual GitHub release publication.
- Any Depone schema extension or execution behavior.

## Outcome

W18 implemented the runner DX path in witnessd and limited Depone changes to
reverse-conformance CI authentication docs/workflow wiring. `witnessd init`
now records a pinned Depone provision from an explicit checkout, environment
checkout, sibling checkout, or setup-only `--allow-network` clone into
`<home>/depone-pinned`. `witnessd run "<goal>" --repo <path>` creates a
quota-free shell team run and immediately invokes the pinned Depone verifier;
`witnessd verify <run-dir>` re-derives the verdict offline from the run ledger.

Validation evidence collected before the W18 checkpoint:

- Depone full suite: `python3 -m unittest discover -s tests` -> 356 tests OK.
- witnessd full suite with CI-style Depone env:
  `PYTHONPATH=/home/ubuntu/moonweave/depone WITNESSD_DEPONE_ROOT=/home/ubuntu/moonweave/depone python3 -m unittest discover -s tests`
  -> 337 tests OK.
- witnessd self-test: `PYTHONPATH=../depone python3 -m witnessd self-test --all`
  -> 24/24 passed.
- All `scripts/revalidate_*.py` -> PASS through W17 plus key rotation and
  v2 demo.
- W18 quickstart: `WITNESSD_DEPONE_ROOT=../depone scripts/quickstart_check.sh`
  -> `quickstart_check: pass`.
- Clean setup clone probe from a temp witnessd checkout with no sibling Depone
  -> `clean_setup_clone: pass`.
- `git diff --check` clean in both repos; Depone
  `ingest_signed_evidence_bundle` diff 0; witnessd gate/archive/operator-review
  diff 0; export-root `revalidate_key_rotation.py` PASS.

Remaining operator-only checkpoints are PAT creation/registration for Depone
reverse-conformance, clean-machine macOS/new-environment quickstart, push/CI,
and GitHub release publication.
