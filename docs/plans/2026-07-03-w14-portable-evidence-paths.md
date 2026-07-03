# W14 — Portable evidence paths (fix CI-red w10 live fixture)

## Problem (root-caused 2026-07-03)

GitHub CI has been **red since `e2075fe0` (2026-07-02 "w10: harden live evidence
boundaries")** through the v2.0.0 release and the external-team-pilot gate. It
went unnoticed because local runs pass: the failing fixtures exist on disk
locally but were never committed / are path-bound to this machine.

Two independent causes:

1. **Missing public-key fixtures (fixed separately).** `.gitignore` had `keys/`,
   which silently dropped `fixtures/w10/keys/operator.pub` and
   `fixtures/v2-demo/keys/operator.pub`. Fixed by narrowing the ignore to
   `**/keys/*` + `!**/keys/*.pub` and committing both public keys. This greens
   `test_revalidate_v2_demo`.

2. **Absolute paths baked into signed w10 evidence (this plan).** The w10 live
   fixture embeds this machine's absolute paths inside signed/hashed evidence:
   - `runner-receipt.json` invocation binds
     `--output-last-message /home/ubuntu/moonweave/witnessd/fixtures/w10/adapter-transcript.txt`;
     `revalidate_w10._assert_auxiliary_command_and_transcript` compares it to
     `str(FIX / "adapter-transcript.txt")` (the *live checkout* absolute path).
   - `provenance.json` records `evidence_path =
     /home/ubuntu/.../fixtures/w10/evidence/capture-manifest.json`; Depone's
     `validate_trusted_observer_provenance` skips any record whose
     `evidence_path` != the passed (live-checkout) path, yielding "trusted
     observer provenance missing".

   Both compare a **frozen absolute path** against the **current checkout's
   absolute path**, so they only pass at `/home/ubuntu/moonweave/witnessd` and
   fail in CI (`/home/runner/...`) or any export. The paths are inside signed
   DSSE + the runlog SHA-256 chain, so they cannot be edited in place. The w10
   signing **private key is lost** (only `operator.pub` remains), so the
   existing evidence cannot be re-signed — and re-signing old evidence is
   forbidden by the key-rotation runbook anyway.

This is a real product defect: evidence that only re-derives at its birth
absolute path contradicts the thesis "Depone re-derives from persisted bytes."

## Fix (the durable direction)

1. **Emitter: emit repo-relative paths for in-fixture references.** In the
   evidence path (`witnessd/provenance.py`, `witnessd/adapter_run.py`,
   `witnessd/observer.py`, `witnessd/emitter.py` as applicable), write
   references that point *into the evidence set* (adapter transcript path,
   provenance `evidence_path`) as **repo-root-relative** strings, not absolute.
   Genuine runtime paths that are not compared against the checkout (worktree /
   cwd temp dirs like `/tmp/witnessd-w10-live-sandbox-*`) stay as captured —
   they are internally consistent (`command_log["cwd"] == receipt["worktree"]`)
   and never compared to the fixture location.
   - Depone needs no contract change: `validate_trusted_observer_provenance`
     does an exact string match and is agnostic to absolute vs relative. The
     verifier must pass the same relative `evidence_path`.

2. **`revalidate_w10`: compare repo-relative paths.** Pass repo-relative
   `evidence_path`; compare the transcript binding against the repo-relative
   fixture path. Assert the fixture re-derives from an arbitrary checkout root
   (add a test that runs revalidate from a `git archive` export in `/tmp`).

3. **Regenerate the w10 live fixture (operator, one paid codex run).** With the
   fixed emitter and a **fresh** operator keypair (private key kept off-repo,
   `operator.pub` committed), run one real codex live capture for the w10 lane,
   re-sign, and commit the portable fixture. Do not re-sign the old evidence.

## Acceptance

- `git archive HEAD | tar -x -C /tmp/...` then
  `PYTHONPATH=<depone> python3 -m unittest discover -s tests` is **green from
  the export root** (proves portability, i.e. no birth-path dependence).
- GitHub CI green on the resulting SHA.
- No private key committed; `**/keys/*` + `!**/keys/*.pub` keeps it that way.

## Non-goals

- Re-signing old w10 evidence (runbook forbids; key lost anyway).
- Weakening `revalidate_w10` checks or the Depone provenance contract.
- Touching the external-team-pilot gate (already open, evidence is portable —
  canary pubkey is tracked and its checks are path-agnostic).
