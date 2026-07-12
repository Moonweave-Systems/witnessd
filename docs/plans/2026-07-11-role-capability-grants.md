# Role-Capability Grants — plan

Status: plan/roadmap, 2026-07-11. Derived from and subordinate to
[`SPEC3.md`](../../SPEC3.md); if this file conflicts with SPEC3, SPEC3 wins.
Depone's verifier contract (`Depone/docs/spec.md`) stays authoritative for any
evidence re-derivation this plan promotes into the contract.

One-line decision: **a role declares a deny-by-default capability grant
(adapters, model, tools/MCP, write-scope, execute-vs-review); witnessd enforces
it at two moments — a pre-execution config gate and a post-execution evidence
re-derivation — and every dimension is labelled `enforced` vs `verified` so the
evidence never over-claims.** Teams are named bundles (rolepacks) of these
grants. ORRO stays a single front door; rolepacks are how domains (developer,
later designer) are exposed, not new skills.

---

## 1. Why (global grounding)

The 2026 industry is converging on a model this plan mirrors and then sharpens:

- Agents are becoming **actors/workers managed like human staff**; work splits
  into **specialised role teams** (researcher/writer/reviewer — CrewAI's crew
  model).
- **Per-role tool RBAC** is the enterprise standard: deny-by-default tool/MCP
  allowlists per agent/role, enforced at a gateway before invocation
  ("MCP RBAC").
- The reference stack is three layers: **identity → authorization (tool/data
  entitlements) → a runtime control plane that enforces policy and captures
  evidence.** witnessd is that third layer.
- **Verifiable provenance** (audit trails, verifiable credentials, continuous
  verification) is a rising need with a regulatory tailwind (EU AI Act
  enforcement 2026-08, SOC2/GDPR scrutiny of agent access).

**Our wedge over the field:** most players *log* mutable runtime claims.
witnessd emits sealed evidence bytes and Depone independently re-derives whether
those sealed bytes are internally consistent with the contract. For write scope,
that means checking sealed declarations against sealed observed touched files,
not proving ground-truth filesystem history. This is stronger than a mutable
SIEM-style log, but it must not be sold as omniscient proof of every file access.
This plan is the substrate that turns human-team RBAC into an evidenced
contract.

## 2. What already exists (do not rebuild)

- `witnessd/model_policy.py` — `(role_id, tier) -> (adapter, model)` policy,
  `resolve_policy_route`, first-candidate-only, no silent fallback.
- `witnessd/orro_workflow.py::compile_role_lane_plan(policy=, tier=)` — compiles
  roles into lanes carrying `(adapter, model)`; unmapped `(role, tier)` fails
  closed (`ERR_ORRO_ROLE_LANE_POLICY_UNRESOLVED`).
- Capability fail-closed (first slice, already shipped): execution lanes are
  restricted to `EXECUTION_LANE_ADAPTERS = ROLE_LANE_ADAPTERS - {agy, gemini}`;
  review lanes to `{agy, gemini}` (`_validate_role_lane`).
- `witnessd/model_declaration.py` — advisory artifact
  (`can_change_evidence_verdict: false`) recording per-dimension verification
  status: codex model = `verified` (fail-loud), agy model =
  `requested-unverified` (silent fallback, never claimed verified).
- Write-scope enforcement primitive: `allowed_touched_files` is threaded through
  `capture.py`/`adapter_run.py`/`emitter.py`; a lane that touches outside its
  declared set already fails closed.
- Adapter state isolation: `witnessd/state.py` gives each lane an isolated
  `CODEX_HOME` (and equivalent), so witnessd already **controls the adapter's
  config surface** — the hook the tool/MCP allowlist enforcement hangs on.
- `orro review` (`witnessd/orro_review.py`) — read-only advisory reviewer-lane
  execution via `run_agy_review_lane`, non-assurance, no proofrun/run_team/lock.

## 3. Core concept — `RoleCapabilityGrant` and two enforcement moments

Each role declares a deny-by-default grant:

```jsonc
RoleCapabilityGrant = {
  "role_id": "runner",
  "capability": "execute",                 // "execute" | "review"
  "adapters": ["codex", "claude"],         // allowed adapters (deny-by-default)
  "model": "gpt-5.5",                      // optional explicit model; absent = policy/tier path
  "tools": {                               // deny-by-default tool/MCP allowlist
    "mcp": ["filesystem"],
    "allow": ["fs.read", "fs.write", "git"]
  },
  "write_scope": ["src/**", "tests/**"]    // globs the role may modify (touched ⊆ this)
}
```

Enforcement happens twice:

- **① Pre-execution config gate (enforced).** witnessd wires the adapter using
  only the grant: adapter ∈ `adapters`; an explicit `model` overrides the
  tier/policy model path for that role, otherwise the existing model policy
  resolves the model; the isolated `CODEX_HOME`/adapter config exposes only
  `tools.mcp`; the sandbox / `allowed_touched_files` is set from `write_scope`.
  Anything the grant does not allow is not offered to the worker.
  Deny-by-default.
- **② Post-execution evidence re-derivation (verified).** From the emitted
  bytes: `touched_files ⊆ write_scope`, `tools_used ⊆ tools.allow`,
  `model = declared`. A violation fails closed. This starts as a witnessd-local
  advisory record and is promoted into Depone's verdict contract per dimension
  (Slice 5). That promotion is the moat: Depone re-derives conformance from the
  sealed evidence bytes rather than trusting witnessd's summary. It is still a
  sealed-observation consistency check, not a claim that witnessd observed
  writes outside its capture boundary, write-then-delete behavior, symlink
  escape, or every possible host side effect.

**Honesty rule (inherited from `model_declaration.py`).** Each dimension is
labelled independently as `enforced` (the gate restricted it) and/or `verified`
(the evidence re-derives it). They are not the same. Example: a codex tool
allowlist is **enforced** by writing only allowed `mcp_servers` into the
isolated `CODEX_HOME`, but codex emits no `effective.settings`, so unless tool
usage is observable in `exec --json` events the *usage* is
`requested-unverified`, exactly like agy's model. Never report `verified` for a
dimension whose evidence a real CLI does not actually expose. Live-verify per
adapter which dimensions are `verified` vs `enforced-only`.

## 4. Placement, artifacts, and Depone sequencing

- New module `witnessd/role_capability.py`: `RoleCapabilityGrant` schema,
  validators, and the default **developer rolepack**.
- Grants attach to roles in `_role`/`_profile_spec` and surface inline on
  role-lane-plan lanes (the same pattern used for `model` in PR #50). Advisory,
  non-assurance, **Depone contract untouched** at first.
- witnessd runtime stays **stdlib + `openssl` CLI only**.
- Any promotion of a conformance check into the verdict (Slice 5) is a **Depone
  contract capability change → lands in Depone first** (its own PR, contract
  version bump, conformance fixtures), then witnessd consumes it. Never make
  witnessd depend on an unmerged Depone change.

## 5. Invariants preserved

- Roles/lanes/grants **must not claim assurance**
  (`raises_assurance: false`, `can_change_evidence_verdict: false`).
- **review-only does not authorize proofrun**; reviewer grants are
  `capability: "review"` and run only through the advisory `orro review` path.
- Depone stays non-executing; execution and receipt emission stay in witnessd;
  receipt verification stays in Depone.
- No silent degradation: an unresolvable/over-broad grant fails closed and is
  logged, never quietly narrowed to a default.

## 6. Slices (priority order)

Each code slice is RED-first, clean-env green (fresh Depone clone +
`PYTHONNOUSERSITE=1` + python3-shim bypass), and — for any dimension touching a
real CLI — **live-smoked against the real adapter** (fakes mask broken wiring;
double-gate `WITNESSD_LIVE_*_SMOKE=1`). Separate branch/PR each; no direct main.

### S0 — this document
Freeze the grant schema, the two enforcement moments, the per-dimension
`enforced`/`verified` honesty rule, and the Depone sequencing. Delegation spec
for S1–S5.

### S1 — per-role adapter + capability grant (foundation)  ·  risk: low
Introduce `role_capability.py` (`RoleCapabilityGrant`, validators, default
developer rolepack). Lift the global `EXECUTION_LANE_ADAPTERS` constraint into a
per-role `adapters` grant; `compile_role_lane_plan` must resolve the policy
adapter **within** the role's grant and fail closed
(`ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED`) if the policy picks an adapter the
role does not grant. Grant surfaced inline on the lane (advisory).
- Enforced: witnessd already chooses the adapter — now bounded by the grant.
- Verified: grant recorded in role-lane-plan; unit-tested (pure).
- Acceptance: policy that resolves outside a role's `adapters` fails closed;
  `policy=None` path unchanged (backward compatible).

### S2 — write-scope grant, evidenced  ·  risk: medium
Promote lane `region` into a per-role `write_scope` grant; drive
`allowed_touched_files` from it. witnessd already fails closed when touched
files escape the set — make that relationship explicit in the grant and the
evidence.
- Enforced: existing `allowed_touched_files` fail-closed.
- Verified: witnessd-local advisory record `touched_files ⊆ write_scope`.
  (Depone re-derivation deferred to S5.)
- Acceptance: a lane touching outside `write_scope` fails closed with the
  grant-scoped error; the advisory record states scope conformance.
- Current compile-time caveat: `flowplan` creates a placeholder lane `region`
  such as `orro/<lane-id>.txt` before the real task diff exists. A rolepack
  `write_scope` used at flowplan time must include that planned region root
  (for example `orro/**` for code-change lanes), or compilation fails closed.
  Narrow task scopes such as `src/**` are runtime write bounds only after the
  planner can provide matching lane regions.

### S3 — tool/MCP allowlist grant (headline "역할별 도구")  ·  risk: high
Deny-by-default tool grant. Enforce by writing only `tools.mcp` into the
isolated `CODEX_HOME` (and the claude equivalent) so the worker is never offered
a non-granted MCP server. Record observed tool calls where the CLI exposes them.
- Enforced: config gate (isolated adapter config exposes only granted MCP).
- Verified: per adapter — `verified` if tool-call/usage events are observable,
  else `requested-unverified` (codex has no `effective.settings`; confirm live
  what `exec --json` actually emits). Emit a `tool-declaration` advisory record
  mirroring `model-declaration`.
- Acceptance (live): a real adapter with a restricted grant cannot invoke a
  non-granted MCP tool; the advisory record honestly labels enforced vs
  verified. Fakes are insufficient — live-smoke each supported adapter.

### S4 — rolepack = team bundle  ·  risk: medium
A rolepack is a named bundle: `{name, profile, roles: {role_id: grant}}`. Ship
the **developer** rolepack composing S1–S3 grants. `orro` selects a rolepack
(flag/arg) that feeds `compile_role_lane_plan`. This is the extension point where
a future **designer** rolepack plugs in (see §7).
- Acceptance: `orro flowplan --rolepack developer` produces a role-lane-plan
  whose lanes carry each role's granted adapter/model/tools/scope; unknown
  rolepack fails closed.

### S5 — Depone contract promotion (deferred)  ·  risk: contract
Promote grant conformance (`touched ⊆ write_scope`, `tools_used ⊆ allow`,
`model = declared`) from witnessd-local advisory into Depone's verdict as a
re-derived axis. **Depone-first**, per dimension, with contract version bump and
conformance fixtures. The first strong candidate is write scope because Depone
can compare the signed declared scope with the sealed capture-manifest touched
files. The correct claim is sealed self-report consistency re-derived by an
independent verifier, not ground-truth filesystem surveillance.

## 7. Non-code teams (designer, etc.) — the deferred fork

Decision (2026-07-11): **execution stays code-centric** (git-diff / touched-files
evidence is where the proof is strongest). Non-code roles (designer, research)
are exposed **advisory / review-only first** — they plug in as S4 rolepacks whose
roles are `capability: "review"` and run through `orro review`, producing
non-assurance advisory receipts. Generalising the evidence substrate to non-code
artifacts (images, design files → a new capture/sign/re-derive model, Depone
contract extension) is a **separate, larger track** and is intentionally out of
scope here; it is opened only if and when a concrete non-code execution need
justifies leaving the code-evidence moat.

## 8. Open questions

- Which tool-usage signals do codex / claude actually emit in structured output
  (decides `verified` vs `enforced-only` for S3 per adapter)? Resolve by live
  probe before S3 implementation.
- Grant precedence when a rolepack grant and an explicit `--model` / adapter
  flag disagree — proposed: explicit flag must still fall within the grant or
  fail closed (never widen a grant from the CLI).
- Whether `write_scope` globs need read-scope as a separate dimension (S2 covers
  write; read-scope may be a later dimension if a role must be blocked from
  reading paths, not just writing them).

## 9. Future direction (post-S5) — team identity without rolepack field sprawl

Decision (2026-07-12): rolepacks stay focused on **capability grants**:
`adapters`, optional `model`, `write_scope`, and `tools`. Those are the
authorization fields Moonweave owns because there is no stable external standard
for this exact grant contract.

Knowledge and rules should not become invented rolepack JSON fields. The
ecosystem is converging on existing instruction files and agent descriptors
(`AGENTS.md`, skillpacks, rule files, subagent frontmatter). witnessd already has
a signed run-intent seam for `instruction_hashes`; that is the right binding
point. A future team can bind "this run was governed by these instruction bytes"
by hashing the relevant instruction/skillpack/rule files into the signed intent,
instead of putting `skillpack_ref`, `rules`, or `domain_knowledge_ref` inside the
rolepack schema.

Flow also stays outside the rolepack. It remains the ORRO workflow profile or
compiled workflow plan (`code-change`, `review-only`, and so on). The rolepack
answers "what is this role allowed to use or write?", not "which workflow did
the operator choose?"

This is still **composition, not greenfield**: rolepack grants bind execution
capability; `instruction_hashes` binds the exact knowledge/rule bytes; workflow
profiles bind the flow. Each dimension can then be promoted only when the
evidence contract can re-derive it honestly.

**Current gap.** The `instruction_hashes` seam exists, but it is not yet a free
team-identity proof:

- population is not consistent across all paths (some emitter paths still write
  an empty dict),
- Depone does not yet re-derive instruction-hash conformance,
- deciding which files count as the governing instruction set is a separate
  policy problem.

So the direction is to use `instruction_hashes` for knowledge/rules binding, but
the work requires a later wave: consistent population first, then optional
Depone promotion if the contract needs to make it verdict-affecting.

**Extension mechanism — explicit, not silent.** S1 rejects unknown grant fields
and S4 rejects unknown rolepack fields on purpose (silent-ignore prevention).
Future capability fields arrive only with a `schema_version` bump and an
extended validator — never by lax acceptance. Knowledge/rules/flow should use
their own existing seams instead of expanding rolepack by default.
