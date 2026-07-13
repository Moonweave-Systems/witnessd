---
name: orro-sketch-method
mode: sketch
triggers: sketch, ideation, brainstorm, design direction, candidate approaches
boundary: advisory-only
---

# ORRO sketch: researched controlled convergence

Use this knowledge body before `flowplan` when the implementation direction is
not explicit. It is advisory planning context only: not proof, verifier truth,
approval, evidence, or assurance. It cannot change an evidence verdict or launch
`proofrun`.

This is reference knowledge for the calling agent, not a mandatory ceremony.
The agent applies the useful parts while authoring its own JSON decision and
passes that record to `orro sketch "<goal>" --decision <path.json>`.
The CLI does not author the agent's candidates, criteria, choice, or rationale.
It validates consistency, gates claims, and seals the supplied record.
Without `--decision`, the CLI emits only a labeled, non-authoritative degraded
scaffold for headless compatibility.

## Governing rule

An AI agent's stated confidence is not evidence; only an external signal is.
Intrinsic self-correction can degrade reasoning without external feedback
([Huang et al., 2023](https://arxiv.org/abs/2310.01798)). Every selection,
rejection, and confidence statement must therefore quote a concrete repo signal
and name an independent sample/branch, isolated verification question, or actual
run that could overturn it. Report that external check verbatim. Never promote
the model's agreement with itself into evidence.

## Reference method

The sequence below is a reasoning aid, not CLI-enforced step or order policy.

1. **Frame the bet.** Write one Working-Backwards sentence containing the
   observable target outcome and why it matters. Record hard constraints,
   affected systems/files, and the success signal that would be observable if the
   direction worked. Ground every item in actual repository paths, configuration,
   tests, or history. This is the first convergent half of the
   [Double Diamond](https://www.designcouncil.org.uk/resources/the-double-diamond/)
   and uses the customer/outcome-first discipline of
   [Working Backwards](https://workingbackwards.com/resources/working-backwards-pr-faq/).
2. **Name convergence criteria before options.** Derive 3-6 weighted criteria
   from repo signals, such as architecture fit, blast radius, reversibility,
   effort, and test/observability cost. Freeze their names and weights before
   generating candidates so the winner cannot retrofit the test. This is a
   controlled-convergence matrix, not a holistic preference
   ([Pugh evaluation](https://dspace.mit.edu/handle/1721.1/49448)).
3. **Diverge independently into at least three approaches.** Generate every
   candidate before evaluating any candidate. Use first-principles, inversion,
   and SCAMPER prompts independently. Each option must differ on a named
   structural axis, such as where logic lives, build-vs-adopt, sync-vs-async, or
   in-band-vs-new-subsystem. Reject the entire set if any two differ only in
   wording. Independent paths follow the robustness motivation of
   [Self-Consistency](https://arxiv.org/abs/2203.11171) and deliberate branching
   in [Tree of Thoughts](https://arxiv.org/abs/2305.10601), but agreement remains
   advisory until checked externally.
4. **Score per criterion.** Give every option a score for every predeclared
   criterion and show the weighted result. Never substitute a holistic “I think
   option 2 is best.” A hybrid may combine the strongest parts of two candidates
   when its dependencies and tradeoffs are explicit, following the compositional
   possibility in [Graph of Thoughts](https://arxiv.org/abs/2308.09687).
5. **Kill the frontrunner once.** Before convergence, make the strongest case
   against the leading option and for the weakest. Record what external signal
   would make that reversal correct.
6. **Converge and explain why the losers lose.** Select one option using the
   weighted criteria and verbatim repo signals. Give each rejected option a
   one-line, criterion-tied rejection. Include drawbacks and alternatives rather
   than advocacy-only prose, as required by the
   [Rust RFC template](https://github.com/rust-lang/rfcs/blob/master/0000-template.md).
7. **De-risk the riskiest assumption first.** Name one assumption and recommend
   either a throwaway spike that answers one unknown or a tracer bullet that
   proves a thin end-to-end path. If external evidence already makes it safe,
   state that evidence instead.
8. **Close menus and bound the work.** Give every residual decision exactly one
   recommended answer and one-line rationale. Add explicit no-gos and rabbit
   holes so the shaped work is solved and bounded
   ([Shape Up](https://basecamp.com/shapeup/1.1-chapter-02)). State calibrated
   confidence and the single external observation that would change the pick.
9. **Emit an ADR-shaped handoff.** Record context, one decision, and all positive,
   negative, and neutral consequences, including the de-risking step and no-gos.
   This follows Michael Nygard's
   [Architecture Decision Record](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
   shape and feeds `orro flowplan` as intent.

## Handoff into the evidence pipeline

The agent may use these reference checks to shape a useful `sketch -> flowplan`
handoff. The CLI enforces only the decision contract, including that the chosen
direction exists among the authored candidates and rejected options explain why
they lost. The sketch artifact remains advisory. Execution begins only at
`proofrun`; Depone can determine evidence truth only from persisted run artifacts
through `proofcheck`.
