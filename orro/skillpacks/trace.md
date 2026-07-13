---
name: orro-trace-method
mode: trace
triggers: trace, debug, bug, failure, symptom, root cause, regression
boundary: advisory-only
---

# ORRO trace: researched scientific debugging

Use this knowledge body before a fix `flowplan` when behavior is failing or
unexpected. Trace is read-only advisory context: not proof, verifier truth,
approval, evidence, or assurance. It consumes a symptom-bound receipt from a
prior actual run and performs read-only probes over the recorded output, but it
must not execute repository code, edit the inspected repository, launch workers
or `proofrun`, or change an evidence verdict.

## Governing rule

An AI agent's stated confidence is not evidence; only an external signal is.
Without external feedback, intrinsic self-correction does not reliably improve
and can degrade reasoning ([Huang et al., 2023](https://arxiv.org/abs/2310.01798)).
For trace, execution is the oracle. Consume a prior-run receipt that records the
command, exit status, and output verbatim; trace itself does not execute that
command. An isolated verification answer must be produced without re-reading the
hypothesis it checks. Narration, consistency, and model confidence never confirm
a cause.

## Ordered phases

0. **Frame and check the plug.** State expected versus actual behavior, when it
   was first observed, and whether it worked before. Capture environment,
   version, branch, and effective configuration. Rule out stale builds, wrong
   branch, configuration/environment drift, and flaky infrastructure before
   expensive investigation. This applies Agans' “check the plug” and audit-trail
   rules ([Debugging Rules](https://www.debuggingrules.com/Debugging_CH2.PDF)).
1. **Reproduce: hard gate.** Establish a deterministic runnable reproduction or
   pin the failure to a concrete trace/log. Record an observed red before any
   hypothesis or stated root cause. If neither exists, stop with `cannot
   localize; need X`; do not guess. This is scientific debugging's
   observation/reproduction foundation
   ([Zeller, Why Programs Fail](https://queue.acm.org/detail.cfm?id=1217270)) and
   matches reproduce-first agent practice in
   [SWE-agent](https://github.com/swe-agent/swe-agent).
2. **Minimize and localize by a named technique.** Delta-minimize inputs or
   changed configuration
   ([Delta Debugging](https://www.cs.columbia.edu/~junfeng/09fa-e6998/papers/delta-debug.pdf));
   use [git bisect](https://git-scm.com/docs/git-bisect) for regression-in-time;
   or assert invariants and binary-search state to its first divergence for
   impossible data. Cite actual implicated repo paths and line numbers before
   hypothesizing. Structure-aware localization is the relevant lesson from
   [AutoCodeRover](https://arxiv.org/abs/2404.05427) and the localize-before-repair
   separation in [Agentless](https://arxiv.org/abs/2407.01489).
3. **Create at least two competing, falsifiable mechanisms.** Each hypothesis
   records a distinct mechanism, a prediction that must hold, a read-only
   discriminating probe whose outcomes differ across rivals, and prior
   confidence. Conjunctive causes such as `H1 AND H3` are allowed, but must have a
   joint prediction. This strengthens the reflect/distill/instrument/confirm
   pattern in [Roo Debug](https://roocodeinc.github.io/Roo-Code/basic-usage/using-modes/)
   by requiring independent mechanisms and discriminating outcomes.
4. **Confirm by falsification.** Run the discriminating probes; reject predictions
   that fail. Generate verification questions, answer them independently, and
   reconcile afterward as in
   [Chain-of-Verification](https://arxiv.org/abs/2309.11495). Interleave reasoning
   with external observations as in [ReAct](https://arxiv.org/abs/2210.03629).
   Confirm a cause only when its prediction holds, it fully explains the symptom,
   and at least one rival was actively ruled out. “Consistent with” is not
   confirmation.
5. **Go to actionable depth.** Ask “why does that occur?” until the systemic cause
   is found. Use a 5-Whys chain for a single path
   ([Five whys](https://en.wikipedia.org/wiki/Five_whys)) and branch into a fault
   tree for multiple contributors
   ([NASA Fault Tree Handbook](https://s3vi.ndc.nasa.gov/ssri-kb/static/resources/Fault%20Tree%20Handbook_NASA.pdf)).
   Stop when the next why leaves the code/config boundary or changes nothing
   actionable, and record that stop reason.
6. **Emit an evidence-typed verdict and handoff.** Confidence is a taxonomy, not
   prose: `confirmed` means reproduced plus a discriminating test isolates the
   cause plus an external receipt reporting the discriminating intervention and
   red-to-green check verbatim; `suspected` means
   observations fit but no complete discriminating/red-to-green check ran;
   `speculative` means reasoning only and nothing executed. Never assert “the root
   cause is X” without the tier and backing artifact. Emit symptom, minimized
   reproduction, logbook, ranked hypotheses, refuted losers, root cause or honest
   unconfirmed state, and a recommended fix scope containing cause site, blast
   radius, invariant, and regression test. The regression must verify removal of
   the cause, consistent with Agans' “if you didn't fix it, it ain't fixed.” The
   fix is not implemented by trace.

When a probe falsifies a hypothesis, add a one-line reflection tied to that
external result so the investigation does not loop back without new evidence.
This uses the feedback-memory idea from
[Reflexion](https://arxiv.org/abs/2303.11366), while retaining execution as the
oracle. [Self-Debugging](https://arxiv.org/abs/2304.05128) motivates explanation
and execution feedback, but does not override the no-external-oracle warning.

## Gate into the evidence pipeline

No hypothesis is emitted before observed red, and no fix flowplan is ready while
the cause is unconfirmed. A confirmed cause may shape a later fix `flowplan` whose
execution starts at `proofrun`. Trace remains advisory and read-only; its record
does not become Depone-re-derivable evidence and never replaces `proofcheck`.
