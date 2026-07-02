# witnessd

> **Done is signed bytes, not a self-reported string.**

`witnessd` is the executing half of Moonweave's evidence loop: it runs lanes and
teams, supervises retries/worktrees/sessions, and emits observer-signed evidence
bundles. [Depone](https://github.com/Moonweave-Systems/Depone) is the separate,
non-executing verifier: it reads those bytes offline and re-derives the assurance
verdict. The verifier can refuse or cap a claim; it cannot silently upgrade it.

## Why

Agent runtimes often collapse completion into self-reporting: a transcript says
"done", a task row is marked complete, or an orchestrator prints a success tag.
`witnessd` treats those strings as untrusted. The durable artifact is the signed
capture: command receipts, observer output, runner receipt, runlog, and bundle
hashes. The design source of truth is [`SPEC.md`](SPEC.md), especially the
status discipline that keeps runtime output at `evidence-pending` until Depone
re-derives a verdict.

## Two products, one evidence contract

```text
witnessd (runtime / arbiter)        Depone (verifier / non-executing)
----------------------------       ---------------------------------
spawn lane or team worker      ->   read committed evidence bytes
observe command + files        ->   recompute canonical hashes
emit signed bundle + runlog    ->   verify signature and schemas
print evidence-pending         ->   derive A0 / A1 / A2 / blocked / refuted
```

The repos are developed side by side under `moonweave/`, but they are not a
monorepo. The only coupling is the evidence contract described in
[`/home/ubuntu/moonweave/CLAUDE.md`](../CLAUDE.md): canonical JSON hashing,
capture-manifest, runner-receipt, isolation, DSSE, and team-ledger schemas come
from Depone. `witnessd` does not invent schema fields.

## What is implemented

- **W1 evidence substrate** — shell lane capture, signed bundle, and Depone
  revalidation fixtures: [`fixtures/w1/`](fixtures/w1/),
  [`scripts/revalidate_w1.py`](scripts/revalidate_w1.py).
- **W2 supervised liveness** — durable runlog and liveness projection:
  [`witnessd/liveness.py`](witnessd/liveness.py),
  [`scripts/revalidate_w2.py`](scripts/revalidate_w2.py).
- **W3 team fan-in** — team ledger and overlap/conflict evidence:
  [`witnessd/team_ledger.py`](witnessd/team_ledger.py),
  [`scripts/revalidate_w3.py`](scripts/revalidate_w3.py).
- **W4 adapters/routing/cost** — shell/Codex/Claude/OpenCode adapter receipts,
  routing, and budget controls: [`witnessd/adapters/`](witnessd/adapters/),
  [`scripts/revalidate_w4.py`](scripts/revalidate_w4.py).
- **W5 autonomy safety** — pause/resume/kill/learning gates backed by runlog
  evidence: [`witnessd/pause.py`](witnessd/pause.py),
  [`witnessd/killswitch.py`](witnessd/killswitch.py),
  [`scripts/revalidate_w5.py`](scripts/revalidate_w5.py).
- **W7 team adapter wiring** — team worker execution through adapter receipts:
  [`tests/test_team_adapter_wiring.py`](tests/test_team_adapter_wiring.py),
  [`scripts/revalidate_w7.py`](scripts/revalidate_w7.py).
- **W8 OVERT alignment** — schema/documentation alignment for OVERT 1.1 AAL-3
  Agentic scope: [`docs/conformance/OVERT.md`](docs/conformance/OVERT.md),
  [`scripts/revalidate_w8.py`](scripts/revalidate_w8.py).

## Reproduce the local evidence loop

From this repo, with Depone checked out next to it:

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest discover -s tests
for s in scripts/revalidate_*.py; do
  PYTHONPATH=/home/ubuntu/moonweave/depone python3 "$s"
done
python3 -m witnessd self-test --all
```

A minimal shell lane emits evidence and prints only `evidence-pending`:

```bash
cd /home/ubuntu/moonweave/witnessd
tmp="$(mktemp -d)"
mkdir -p "$tmp/sandbox" "$tmp/evidence"
python3 -m witnessd run \
  --adapter shell \
  --runner-sandbox "$tmp/sandbox" \
  --out "$tmp/evidence/capture-manifest.json" \
  --log "$tmp/evidence/runlog.jsonl" \
  --allow out.txt \
  -- sh -c 'echo hi > out.txt'
PYTHONPATH=/home/ubuntu/moonweave/depone python3 scripts/revalidate_w1.py
```

The negative fixture path is deliberately falsifiable: tampered W1 bytes and W4
route/budget examples are rejected by Depone-backed revalidators in
[`scripts/`](scripts/), not by trusting a runtime success string.

## OVERT profile

[`docs/conformance/OVERT.md`](docs/conformance/OVERT.md) records schema-level
OVERT 1.1 alignment at **AAL-3 Agentic** scope. This is not a certification. The
current profile excludes independent IAP notary, transparency-log inclusion, and
OVERT `DELAYED_NOTARY` (`0x01`).

## Honest limits

- **Assurance ceiling is A2.** `witnessd` emits bytes; Depone re-derives at most
  A2 in the current contract.
- **A2 in this repo is demonstration-only unless captured on a uid-isolated
  host.** See [`fixtures/w1/A2-DEMONSTRATION.md`](fixtures/w1/A2-DEMONSTRATION.md).
- **Temporality is self-declared / self-attested in v1.0.** `evidence_mode` distinguishes
  `contemporaneous` from `post_hoc`, but without a live independent notary,
  co-epoch anchor, or transparency timestamp, the bytes alone do not prove that
  distinction. W8 preserves this as an honesty fixture rather than a detector.
- **Keyless signing is not shipped.** The W6 keyless/production gate remains a
  separate blocked track; this release uses operator key material and `openssl`.
- **Runtime dependencies stay small.** `witnessd` runtime code is Python stdlib +
  `openssl` CLI. Depone is a dev/test verifier dependency, not a runtime import.

## CI and release validation

The GitHub Actions workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
runs unit tests on Python 3.10 and 3.12, fixture revalidators, a Depone-free
runtime decoupling guard, and a no-overclaim grep gate. If Depone is private,
configure a `DEPONE_TOKEN` repository secret as documented in the workflow.

Before tagging a release locally:

```bash
PYTHONPATH=/home/ubuntu/moonweave/depone python3 -m unittest discover -s tests
for s in scripts/revalidate_*.py; do PYTHONPATH=/home/ubuntu/moonweave/depone python3 "$s"; done
python3 -m witnessd self-test --all
```

Suggested annotated tag message for `v1.0.0`:

```text
witnessd v1.0.0

W1-W8 summary: signed evidence substrate; supervised liveness; team fan-in;
adapter routing/cost controls; autonomy pause/kill/learning safety; team adapter
wiring; OVERT 1.1 AAL-3 Agentic schema alignment.

Conformance: Depone re-derives committed evidence bytes; witnessd remains the
executing emitter and keeps runtime status at evidence-pending until verifier
results exist.

Known limits: A2 fixture is demo/host-conditional; no independent notary or
transparency log; keyless production gate remains blocked/out of scope.
```

Do not push tags or branches without explicit operator approval.
