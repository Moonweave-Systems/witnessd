# witnessd

> An executing team/agent runtime whose every action leaves **observer-signed,
> independently verifiable evidence** — *done is signed bytes, not a self-reported
> string.*

`witnessd` spawns and supervises agent/team workers (retry, worktree, durable
sessions) and emits evidence bundles. A separate, **non-executing** verifier —
[Depone](https://github.com/Moonweave-Systems/Depone) — re-derives A0/A1/A2
assurance from those bytes, offline, with no ability to raise the grade. Because
every action is falsifiable after the fact, witnessd can be **more** aggressive
about autonomy than tools that trust their own "VERIFIED" tags.

- **Design (SoT):** [`SPEC.md`](SPEC.md). Implementation plans: [`docs/plans/`](docs/plans/).
- **Status:** W1 complete (event log · observer separation · capture-manifest ·
  Ed25519 DSSE · runner-receipt · evidence-substrate). Depone re-derives A1/A2
  from W1's committed fixtures. W2–W5 planned.
- **Deps:** Python stdlib + `openssl` CLI. No third-party runtime packages.
- **Contract:** witnessd emits evidence per Depone's schemas; see [`CLAUDE.md`](CLAUDE.md).

Co-developed with Depone in the `moonweave/` workspace; run `make dogfood` /
`make test` there for the cross-repo conformance loop.
