---
name: orro-trace-method
mode: trace
triggers: trace, debug, bug, failure, symptom, root cause, regression
boundary: advisory-only
---

# ORRO trace method

Use this knowledge body before a fix `flowplan` when behavior is failing or
unexpected. It is planning context only: not proof, verifier truth, approval, or
assurance, and it cannot change an evidence verdict.

## Investigation sequence

The phases are ordered gates, not headings that may be filled retroactively:

1. **Observe** — record the exact symptom, error output, boundary where it is
   visible, relevant recent changes, and the difference between expected and
   actual behavior.
2. **Reproduce and localize** — obtain the smallest repeatable reproduction,
   then trace the incorrect value or transition backward across component
   boundaries. If reproduction is not available, record that gap and continue
   gathering facts; do not guess a fix.
3. **Hypothesize** — rank distinct causal explanations. Each hypothesis needs
   supporting facts, disconfirming facts, and one minimal confirmation test that
   changes a single variable.
4. **Confirm root cause** — name the source only after a hypothesis survives its
   test and explains the observed symptom. Otherwise keep the root cause
   explicitly unconfirmed.

## Gate into the evidence pipeline

No fix scope is authorized while root cause is unconfirmed. After confirmation,
shape a fix `flowplan` around the confirmed source, preserve the reproduction as
a failing regression test, and use `proofrun` for execution. The trace artifact
itself never becomes execution evidence and never replaces `proofcheck`.
