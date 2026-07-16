# Security Policy

`witnessd` is the local execution runtime. It spawns workers, records what
happened, and emits operator-key-signed evidence. The default single-machine
flow generates that key on the runtime and is therefore self-signed, not an
independent observer anchor. It does not decide trust.

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

## Supported Versions

witnessd is pre-1.0 software (semver `0.x`/`v2.x` pre-release conventions).
There is no LTS track yet; only the latest published release is supported.

| Version | Supported |
| --- | --- |
| v2.2.0 (latest) | Yes |
| < v2.2.0 | No |

Security fixes land on `main` and ship in the next tagged release. If you are
running an older tag, upgrade before reporting — we will ask you to reproduce
against latest first.

## Reporting a Vulnerability

Use GitHub's private vulnerability reporting on this repository:
**Security -> Report a vulnerability**
(`https://github.com/Moonweave-Systems/witnessd/security/advisories/new`).

Do not open a public issue or pull request for a sensitive report — that
discloses the problem before a fix exists. If private vulnerability reporting
is not enabled when you look, maintainers should enable it under
**Settings -> Security -> Private vulnerability reporting**; in the meantime,
contact a maintainer directly and reference this policy.

## Response Expectations

This is a small, pre-1.0 project. There is no formal SLA. As a working target:

- best-effort acknowledgement within a few business days;
- a rough severity read and next step once a maintainer has looked at it;
- no guaranteed fix timeline — complex evidence-integrity issues may take
  longer than a quick doc or CLI bug.

If you have heard nothing after a week, it is fine to follow up on the same
advisory thread.

## Coordinated Disclosure

Please give us a reasonable embargo to investigate and ship a fix or
mitigation before any public write-up, and confirm the disclosure date with a
maintainer rather than assuming a fixed default window. We will credit
reporters in the advisory and release notes unless you ask us not to.

## Severity Guidance

Treated as high severity:

- evidence-integrity or signature-forgery — bytes that let a run claim
  properties it did not have, or that let evidence be altered undetected;
- observer/runner separation bypass — anything that lets the runner write or
  influence evidence paths that must be observer-owned for A2;
- secret leakage into evidence — tokens, keys, or credentials captured into
  emitted evidence, logs, or artifacts;
- sandbox escape or path traversal in a runner sandbox;
- kill-switch or pause bypass — a run that continues after a kill/pause
  signal, or budget/lifecycle controls that can be silently defeated.

Lower severity: documentation errors, non-security bugs, CLI ergonomics,
flaky tests. File those as normal issues, not security reports.

## Secret & Evidence Handling

Do not paste secrets, tokens, API keys, private URLs, cookies, or signing
material into a report, issue, PR, or advisory thread. Prefer evidence paths
and redacted excerpts over raw command output or raw evidence files.
Secret-looking material in a report is not proof of a vulnerability by
itself — describe the mechanism, and attach only what is needed to reproduce
it with sensitive values removed or replaced.

The default `redacted` capture profile replaces known local values such as the
run prompt, selected paths, worktree, and `CODEX_HOME`. In every capture
profile, including explicit `full`, witnessd also best-effort-scrubs a small set
of high-confidence secret patterns from captured output before the evidence is
hashed and signed. A `redaction-manifest.json` records matched rules and states
the boundary when a scrub occurs.

This pattern scrub is best-effort and high-confidence-patterns-only. It is not
a guarantee that all secrets are removed. Operators must still avoid placing
secrets where a lane, command, model, tool, or adapter can print them. The
explicit `full` profile preserves local paths and prompts; it does not disable
the always-on high-confidence secret-pattern scrub.

## witnessd Boundary

witnessd executes lanes and emits operator-key-signed evidence. It does not
itself grant final trust. The default runtime-generated key is labeled
`trust_anchor: "self-signed"`; it must not support an independence or A1/A2
claim. `trust_anchor: "operator-provided"` requires an external public key
selected through `DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE` and real
observer/runner separation must still hold for observer-signed/A2 language.

- witnessd does not verify evidence; Depone re-derives verdicts offline from
  the persisted bytes.
- witnessd does not approve merges.
- witnessd does not raise assurance on its own say-so. A1/A2 (`A1-local-observed`,
  `A2-isolated-observed`) require the actual observer/runner separation and
  evidence-path ownership to hold — witnessd cannot self-declare into them.
- The observer and emitter produce the bytes; Depone decides what those bytes
  support. Self-declared runtime facts and post-hoc (`DELAYED_NOTARY`-style)
  records do not upgrade trust by themselves.

Known, documented ceiling (not a bug to report): witnessd + Depone currently
align to OVERT 1.1 at **AAL-3** (operator-controlled, automated monitoring).
**AAL-4** — a transparency log and an independent, operator-decoupled notary —
is roadmap, not implemented. The reserved Fulcio/Rekor keyless profile fails
closed with `ERR_WITNESSD_KEYLESS_LIVE_UNIMPLEMENTED`; it is not a live trust
anchor yet. Reports that these are
"missing" restate a known limit rather than disclose a new one, unless you
have found a way the current AAL-3 claim itself is false.

## Out of Scope

- Treating a session transcript, model confidence, or an unbound artifact
  (evidence not tied to the current run/engine-lock) as proof — that is a
  design boundary, not a bug. See [`docs/conformance/OVERT.md`](docs/conformance/OVERT.md).
- Bugs in third-party CLI adapters themselves (e.g. the `codex` or `claude`
  CLIs) rather than in how witnessd invokes, sandboxes, or records them.
- The documented AAL-4 gap (transparency log, independent IAP notary) and
  other roadmap items already called out in `docs/conformance/OVERT.md` and
  release notes — these are tracked, not hidden.
- Reports about `superflow`-named compatibility aliases behaving like legacy
  aliases; that is intentional migration behavior, not a vulnerability.
