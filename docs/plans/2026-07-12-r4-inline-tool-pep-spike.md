# R4 inline tool-call PEP spike

Date: 2026-07-12

Status: feasibility spike, docs-only. No witnessd production code, Depone
contract code, `lock.py`, or `run_team` changes are part of this slice.

## Goal

R4 raises the role-capability trust root for tools from "the runtime configured
what was visible" to "the runtime made a fail-closed decision before each tool
call, emitted a sealed decision receipt, and Depone re-derived that decision
from signed bytes." This is stricter than S3. S3 limits what adapters see;
R4 targets the dangerous moment: the actual tool call.

The executor/verifier split remains non-negotiable:

- witnessd executes the adapter, owns the policy-enforcement point (PEP), and
  emits signed evidence.
- Depone remains non-executing. It re-derives the allow/deny verdict from the
  signed receipt bytes, declared policy bytes, and linked run evidence.
- witnessd advisory summaries are not trusted as verdict input unless Depone's
  contract has first defined the byte schema and derivation rule.

## Current baseline

The role-capability plan already distinguishes enforcement from verification.
For tools, S3 is mostly config-gate enforcement:

- codex: witnessd writes only granted `mcp_servers` into isolated `CODEX_HOME`.
- claude: witnessd passes `--mcp-config`, `--strict-mcp-config`, and
  `--allowedTools`.
- tool usage is only verified when the adapter emits usable tool-call events.
  Codex was explicitly labeled enforced-only in S3 because `exec --json` did
  not expose enough effective settings/usage evidence in that slice.

R4 is not a replacement for S1-S5. It is a later trust-root upgrade above them.
The rolepack remains the grant source; the new question is where an adapter lets
witnessd intercept a concrete `tool_name + arguments` request before execution.

## Live probe environment

Repository: `/home/ubuntu/moonweave/witnessd`

Branch base: `main` at `26f0beb9e4714e908a1f123002b2fb7441efad87`

Installed CLI versions:

```text
codex-cli 0.144.1
Claude Code 2.1.207
agy 1.1.1
gemini 0.35.3
```

All probe scripts were scratch files under `/tmp/r4-pep-*` and are not intended
to be committed. The scripts used only Python stdlib JSON-RPC over stdio.

## Adapter probe results

### Codex

Mechanism tested: witnessd-controlled `CODEX_HOME/config.toml` registering one
stdio MCP server. The server exposes `allowed_echo` and `forbidden_echo`, logs
`tools/list`, logs every `tools/call`, and returns an allow result or a deny
JSON-RPC error before executing any downstream action.

Config shape used:

```toml
[mcp_servers.r4_pep]
command = "/tmp/r4-pep-codex.mhvBbD/mcp_proxy.py"
env = { PEP_LOG = "/tmp/r4-pep-codex.mhvBbD/codex-mcp.log" }
```

Discovery command:

```bash
CODEX_HOME=/tmp/r4-pep-codex.mhvBbD/codex-home \
HOME=/tmp/r4-pep-codex.mhvBbD/home \
codex mcp list
```

Observed result: Codex listed `r4_pep` as an enabled stdio MCP server.

Safe non-interactive invocation:

```bash
CODEX_HOME=/tmp/r4-pep-codex.mhvBbD/codex-home \
HOME=/tmp/r4-pep-codex.mhvBbD/home \
PYTHONNOUSERSITE=1 \
codex --sandbox read-only --ask-for-approval never \
  exec --json --skip-git-repo-check --cd /tmp \
  "Call the MCP tool mcp__r4_pep__allowed_echo with text 'codex-allowed'."
```

Observed result:

- The MCP server received `initialize`, `notifications/initialized`, and
  `tools/list`.
- Codex emitted an `mcp_tool_call` event for `r4_pep/allowed_echo`.
- The MCP server did **not** receive `tools/call`.
- Codex completed the item with error `user cancelled MCP tool call`.
- `--ask-for-approval on-request` produced the same result in headless mode.

Interpretation: Codex 0.144.1 has a real pre-call approval gate for MCP calls.
Under the current witnessd-safe non-interactive style, that gate blocks before
the witnessd MCP proxy can make its own allow/deny decision. This proves the
dangerous call does not silently bypass approval, but it does not yet provide a
witnessd PEP receipt.

Bypass invocation:

```bash
CODEX_HOME=/tmp/r4-pep-codex.mhvBbD/codex-home \
HOME=/tmp/r4-pep-codex.mhvBbD/home \
PYTHONNOUSERSITE=1 \
codex --dangerously-bypass-approvals-and-sandbox \
  exec --json --skip-git-repo-check --cd /tmp \
  "Call the MCP tool mcp__r4_pep__allowed_echo with text 'codex-bypass'."
```

Observed allow result:

- The MCP server received `tools/call` for `allowed_echo`.
- The server logged a PEP decision: `decision=allow`.
- Codex `exec --json` emitted `mcp_tool_call` with `status=completed` and
  result text `ALLOWED_ECHO:codex-bypass`.

Bypass deny invocation:

```bash
CODEX_HOME=/tmp/r4-pep-codex.mhvBbD/codex-home \
HOME=/tmp/r4-pep-codex.mhvBbD/home \
PYTHONNOUSERSITE=1 \
codex --dangerously-bypass-approvals-and-sandbox \
  exec --json --skip-git-repo-check --cd /tmp \
  "Call the MCP tool mcp__r4_pep__forbidden_echo with text 'codex-deny'."
```

Observed deny result:

- The MCP server received `tools/call` for `forbidden_echo`.
- The server logged a PEP decision: `decision=deny`.
- The server returned JSON-RPC error `-32001: R4_PEP_DENY: tool not granted`.
- Codex `exec --json` emitted `mcp_tool_call` with `status=failed` and the same
  denial message.

Codex classification:

`candidate, but blocked by headless approval semantics for safe witnessd use`.
The MCP proxy can be a true pre-call PEP when Codex is allowed to call MCP
tools, and Codex emits useful `mcp_tool_call` events. However, the only mode
observed to reach the proxy was `--dangerously-bypass-approvals-and-sandbox`,
which is not acceptable as the default witnessd execution posture because it
turns off Codex sandboxing and approval protections. A production Codex slice
must first find a non-dangerous way to pre-authorize the witnessd PEP proxy
while preserving the sandbox.

### Claude

Mechanism intended: `--mcp-config` plus `--strict-mcp-config` exposes a scratch
MCP server; `--settings` injects a `PreToolUse` hook with matcher
`mcp__r4_pep__.*`; `--include-hook-events` records hook lifecycle in
`stream-json`. The hook logs the full hook stdin and exits 2 for
`mcp__r4_pep__forbidden_echo`.

Probe command shape:

```bash
PEP_HOOK_LOG=/tmp/r4-pep-claude.Aja12K/claude-hook.log \
PYTHONNOUSERSITE=1 \
claude -p "Call mcp__r4_pep__allowed_echo with text 'claude-allowed'." \
  --settings /tmp/r4-pep-claude.Aja12K/settings.json \
  --mcp-config /tmp/r4-pep-claude.Aja12K/mcp-config.json \
  --strict-mcp-config \
  --allowedTools mcp__r4_pep__allowed_echo,mcp__r4_pep__forbidden_echo \
  --permission-mode bypassPermissions \
  --output-format stream-json \
  --verbose \
  --include-hook-events
```

Observed result:

- Claude loaded the scratch MCP server and reported tools
  `mcp__r4_pep__allowed_echo` and `mcp__r4_pep__forbidden_echo` in its
  stream-json `init` event.
- Claude loaded hook infrastructure and emitted hook lifecycle events.
- The run failed before model/tool execution with authentication error:
  `401 Invalid authentication credentials`.
- A plain `claude -p "Reply OK only."` in the same checkout failed with
  `OAuth session expired and could not be refreshed`.
- `claude auth status` reported `loggedIn: false`, `authMethod: none`.

Interpretation: this environment cannot complete a paid Claude live tool-call
probe without refreshing auth. The local CLI surface does show the right
primitives in headless mode: strict MCP config, allowed tools, hook lifecycle
events, and loaded MCP tools. It does **not** prove the R4 acceptance criterion
because the model never attempted a tool call, so no `PreToolUse` allow/deny
decision was observed for MCP.

Claude classification:

`promising, unverified in this spike due expired auth`. Prior local settings
show PreToolUse can block tools, but R4 should not count that as acceptance for
MCP PEP. The next slice must rerun the exact allowed and denied MCP probes after
auth renewal and require: hook log exists, hook fires before MCP `tools/call`,
deny prevents the MCP server from receiving the forbidden `tools/call`, and
stream-json records the denial.

### Agy

Probe command:

```bash
agy --help
```

Observed result: help exposes print mode, sandbox mode, permissions skip, agent
and model selection. It did not expose MCP registration, policy files, or
pre-tool hooks in the top-level help.

Agy classification:

`no R4 PEP surface found in shallow probe`. Agy remains review-only in the
current role-capability model, so this is not a first-wave target.

### Gemini

Probe commands:

```bash
gemini --help
gemini hooks --help
gemini mcp --help
gemini mcp list
```

Observed result:

- Gemini 0.35.3 exposes `--allowed-mcp-server-names`, deprecated
  `--allowed-tools`, policy/admin-policy files, `gemini mcp`, and
  `gemini hooks`.
- `gemini hooks --help` only listed `hooks migrate` in this installation; no
  direct PreToolUse-equivalent command surface was visible from help.
- `gemini mcp list` could not authenticate because this client is no longer
  supported for the configured individual tier and reported migration to
  Antigravity.

Gemini classification:

`policy/MCP surface exists, live adapter not usable in this environment`.
Do not claim verified R4 support until a supported auth path and a concrete
pre-call hook/policy denial are observed.

## Recommended architecture

### PEP placement

For adapters that can route tool calls through witnessd:

1. witnessd exposes exactly one adapter-visible MCP server, the witnessd PEP
   proxy.
2. The proxy lists only tools derived from the rolepack grant and adapter
   capabilities, or lists a stable superset while denying per call. Listing only
   granted tools reduces prompt confusion; denying per call is still required
   for fail-closed defense against drift or forged names.
3. On `tools/call`, the proxy canonicalizes the requested server/tool name and
   arguments, evaluates the role grant, emits a decision receipt, and only then
   forwards allowed calls to an upstream MCP server or local tool implementation.
4. Denied calls return a structured tool error and do not reach the upstream
   tool.

For Claude, if the auth-refreshed probe succeeds, PreToolUse can be a cleaner
first PEP because it is explicitly before tool execution and works for both
built-in and MCP tool names. For Codex, the MCP proxy is the right technical
shape, but production feasibility depends on solving headless approval without
using the dangerous bypass flag.

### Decision receipt

The implementation wave should treat this as Depone-first once it changes
verdicts. A candidate signed artifact shape:

```json
{
  "kind": "moonweave-tool-call-decision-receipt",
  "schema_version": "1.0",
  "run_id": "...",
  "lane_id": "...",
  "role_id": "runner",
  "capability": "execute",
  "adapter": "claude",
  "sequence": 7,
  "tool_namespace": "mcp",
  "server_id": "filesystem",
  "tool_name": "read_file",
  "canonical_request_sha256": "...",
  "decision": "deny",
  "reason_code": "ERR_ROLE_CAPABILITY_TOOL_NOT_GRANTED",
  "policy_ref": {
    "rolepack_name": "developer",
    "rolepack_sha256": "...",
    "grant_sha256": "..."
  },
  "observed_at_unix_ms": 1783823290971,
  "previous_decision_sha256": "..."
}
```

Depone should own the final schema and version. witnessd should not invent a
verdict-changing contract first.

### Depone re-derivation

Depone can re-derive the verdict without executing tools:

1. Load the signed run intent / rolepack binding and the signed decision
   receipt.
2. Canonicalize the receipt request hash and policy reference.
3. Check that `role_id`, `capability`, `adapter`, `server_id`, and `tool_name`
   are granted by the declared role capability.
4. Check that `decision` matches the policy result.
5. Fail the verdict on missing receipt for an observed tool call, deny decision
   followed by an observed successful tool result, allow decision outside grant,
   receipt hash mismatch, sequence gap, or invalid signature.

This proves consistency of sealed pre-call decisions with sealed policy bytes.
It still does not prove physical ground truth outside the adapter/PEP boundary.
That caveat matches the R3 wording: Moonweave proves tamper-evident consistency
of signed observations and declarations, not omniscient host-state truth.

### Policy language

Use a simple stdlib-only allowlist for the first implementation wave:

```json
{
  "tools": {
    "mcp": ["filesystem"],
    "allow": ["mcp__filesystem__read_file"]
  }
}
```

Exact string matching should be the default. Globs should only be added where
there is already a repo pattern and a clear canonicalization rule. Cedar, OPA,
or Rego are not first-wave runtime choices because witnessd's runtime contract
is Python stdlib plus OpenSSL CLI. A richer policy engine can be revisited as a
separate dependency and verification decision, not smuggled into R4.

## Fake-masking risks

R4 must never accept fake-only tests. The failure modes are exactly the ones the
live Codex probe exposed:

- The adapter may list an MCP server but never send `tools/call`.
- The adapter may emit a tool-call-looking event while an internal approval gate
  cancels before the witnessd PEP sees the request.
- A proxy may log startup and `tools/list`, which proves only discovery, not
  enforcement.
- A deny receipt is meaningful only if the forbidden upstream tool did not run.
- For adapters with stream events, the event must be reconciled against the PEP
  receipt; event text alone is not the verdict source.

Every implementation slice needs a live smoke gated by explicit env vars and a
real binary. The acceptance phrase is: "the real adapter attempted a disallowed
tool call, witnessd denied it before upstream execution, and the signed receipt
plus adapter events make that denial re-derivable."

## Slice plan

### R4-S0: this spike

Output: feasibility report and slice plan only.

Enforce: none in production.

Verify: live probes classify adapter surfaces and identify blockers.

Acceptance: docs-only PR; no production code or contract changes.

### R4-S1: Claude PreToolUse completion probe

Target: Claude only, after auth renewal.

Enforce: scratch PreToolUse hook denies `mcp__r4_pep__forbidden_echo` before MCP
server `tools/call`.

Verify: real `claude -p --output-format stream-json --include-hook-events`
shows loaded MCP tools; hook log records allow and deny; forbidden MCP server
log has no `tools/call`; allowed call reaches MCP server.

Difficulty: medium. The CLI surface is present, but this spike could not finish
the paid turn due expired auth.

Live acceptance:

```text
allowed: hook decision=allow, MCP tools/call reached, stream-json tool result
denied: hook decision=deny, MCP tools/call not reached, stream-json denial
```

### R4-S2: witnessd-local Claude PEP advisory

Target: Claude adapter only, still witnessd-local advisory.

Enforce: generate a per-run PreToolUse hook and strict MCP config from the
rolepack tool grant. Deny-by-default. Do not change Depone verdict yet.

Verify: emit advisory `tool-call-decision` artifacts with
`can_change_evidence_verdict:false`; include adapter event correlation where
available.

Difficulty: medium-high because hook trust, settings sources, and auth behavior
must be deterministic in non-interactive runs.

Live acceptance: env-gated real Claude smoke for allow and deny. Fake binaries
may test serialization only.

### R4-S3: Depone contract for tool-call decision receipts

Target: one adapter dimension, preferably Claude if S1/S2 pass.

Enforce: none in Depone. Depone is non-executing.

Verify: Depone defines the receipt schema, contract version bump, fixtures, and
verdict axis. Violations fail on receipt/policy mismatch, missing receipt for
observed call, deny followed by success, or invalid signature.

Difficulty: high. This is the trust-root promotion and must follow the
Depone-first protocol.

Live acceptance: Depone fixtures fail/pass deterministically without launching
any adapter.

### R4-S4: witnessd emits Depone contract receipts

Target: wire the merged Depone receipt contract into witnessd Claude PEP.

Enforce: same PreToolUse PEP as S2.

Verify: witnessd evidence bundle includes signed receipt bytes; Depone
re-derives pass/fail from those bytes.

Difficulty: high because witnessd must preserve stdlib/OpenSSL-only runtime and
avoid claiming assurance from advisory summaries.

Live acceptance: witnessd real Claude run with allowed call passes Depone;
forbidden call is denied before execution and Depone verdict fails or records
the deny according to the contract's expected semantics.

### R4-S5: Codex MCP proxy path

Target: Codex only after a non-dangerous headless MCP authorization path is
found.

Enforce: witnessd registers only the PEP MCP proxy in isolated `CODEX_HOME`; the
proxy evaluates each `tools/call` and optionally forwards to upstream MCP.

Verify: Codex `exec --json` `mcp_tool_call` events correlate with signed proxy
decision receipts.

Difficulty: very high. The live probe proved the proxy works only with
`--dangerously-bypass-approvals-and-sandbox`; that is not acceptable for normal
witnessd.

Live acceptance: with sandbox preserved, real Codex reaches proxy `tools/call`,
allowed call succeeds, forbidden call is denied, and the upstream forbidden tool
does not run.

### R4-S6: Gemini / agy revisit

Target: lower-priority review adapters.

Enforce: Gemini policy/hooks if a supported auth path and pre-call hook are
confirmed; agy only if future CLI versions expose a hook/MCP surface.

Verify: same receipt pattern as above once a concrete pre-call mechanism exists.

Difficulty: unknown.

Live acceptance: adapter-specific; no fake-only acceptance.

## Open blockers

- Claude auth is expired in this environment. The exact PreToolUse MCP
  allow/deny acceptance test still needs to run after `claude auth login` or
  another approved auth source is restored.
- Codex safe headless MCP execution needs a non-dangerous way to authorize the
  witnessd PEP proxy. The current safe modes stop before proxy `tools/call`.
- Codex proxying arbitrary upstream MCP servers is more than config filtering:
  witnessd would need to multiplex upstream stdio/http MCP servers or constrain
  R4-S5 to a small built-in tool set first.
- Depone contract design must happen before any verdict-affecting witnessd
  receipt is emitted.
- Tool receipts improve the trust root at the adapter boundary. They still do
  not prove host-wide ground truth outside the observed/controlled boundary.

## Recommendation

Do not start with Codex despite the successful proxy-deny proof. Its only
successful proxy execution path in this spike disabled sandboxing and approvals.

Start R4 implementation with a Claude completion probe after auth renewal. If
PreToolUse blocks MCP calls before `tools/call`, Claude becomes the first
production candidate because the hook is explicitly pre-call and can cover both
built-in and MCP tools. Then promote the receipt schema Depone-first. Keep Codex
as a separate high-risk slice focused on safe headless MCP authorization.
