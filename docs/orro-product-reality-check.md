# ORRO Product Reality Check

Use this local dogfood checklist after ORRO productization waves. It measures
whether ORRO is making the workflow clearer and safer, not merely adding more
artifacts or automation.

The scenario manifest lives at:

```text
docs/orro-reality-check/manifest.json
```

The no-network checker is:

```bash
python scripts/check_orro_product_reality.py
```

It validates local scenarios for `trivial-doc-fix`, `docs-change`,
`code-change`, `review-only`, `verification-only`, `release-readiness`,
`risky-change`, `scout-only-blocked`, and `stale-verdict-blocked`.

## Product Questions

Step reduction:
How many commands did ORRO recommend compared with the manual workflow?

Waste avoidance:
Did `orro advise` skip unnecessary proofrun, role-lane, team, model, or tool
work?

Reviewer burden:
Did `orro report` identify the verdict, next action, and human review focus?

Gate integrity:
Did ORRO block handoff without a passing bound `proofcheck-verdict.json`?

Artifact fatigue:
Did `orro report` compress artifacts into a clear state?

Non-developer clarity:
Could a non-developer identify the next safe action from advice or report?

Stop behavior:
Did ORRO stop when evidence was missing instead of guessing?

`orro report` is the main artifact for this check. It should show whether ORRO
reduced user steps, made the next action clear, lowered reviewer burden, avoided
unnecessary work, explained blocked states, and reduced artifact fatigue without
turning the report itself into proof or assurance.

Runtime hardening should be visible in this check: malformed, stale, copied, or
unbound artifacts should produce blocked explanations instead of optimistic
status. A blocked report is a product success when continuing would overclaim.

The reality check is documentation guidance only. It is not proof, verifier
truth, merge approval, assurance, telemetry, or a benchmark claim. It does not
phone home, collect user data, call live models, call MCP, or change proofcheck
semantics.

Depone verifies; witnessd executes; ORRO exposes the workflow.
