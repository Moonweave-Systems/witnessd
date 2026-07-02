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
- **Status:** W1-W5 complete on local mainline work: evidence substrate,
  supervised liveness/durable sessions, team fan-in, adapter routing/cost
  controls, and autonomy safety. Depone re-derives each wave from committed
  fixtures via `scripts/revalidate_w1.py` through `scripts/revalidate_w5.py`.
- **Deps:** Python stdlib + `openssl` CLI. No third-party runtime packages.
- **Contract:** witnessd emits evidence per Depone's schemas; see [`CLAUDE.md`](CLAUDE.md).

Co-developed with Depone in the `moonweave/` workspace; run `make dogfood` /
`make test` there for the cross-repo conformance loop.
