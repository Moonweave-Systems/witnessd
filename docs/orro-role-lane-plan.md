# ORRO Role-Lane Plan v0

`orro-role-lane-plan` is the bridge from ORRO rolepack intent to witnessd team
lanes. It is executable intent, not proof.

Typical path:

```bash
orro flowplan "fix parser bug" \
  --root . \
  --profile code-change \
  --out .witnessd/workflow-plan.json \
  --role-lanes-out .witnessd/role-lane-plan.json

orro proofrun "fix parser bug" \
  --repo . \
  --home .witnessd \
  --workflow-plan .witnessd/workflow-plan.json \
  --role-lane-plan .witnessd/role-lane-plan.json
```

Contract:

- `workflow_plan_hash` binds the role-lane plan to one workflow plan.
- `execution_allowed` must be true before proofrun may run lanes.
- `lanes` map executable ORRO roles to witnessd team lanes.
- default lanes use deterministic `shell`; live model adapters are not used by
  default.
- proofrun executes through existing witnessd team execution, observer, fan-in,
  and ledger machinery.
- proofcheck remains delegated to Depone.

Boundary:

```text
Depone verifies; witnessd executes; ORRO exposes the workflow.
```

Role-lane plans, workflow plans, role dispatch, review prose, and role names are
not evidence, not proof, not approval, and not assurance. Actual proof begins
only when proofrun emits evidence. Depone proofcheck decides what persisted
evidence supports. Handoff includes plan and role context only for review and
still requires a passing bound `proofcheck-verdict.json`.

Safety profiles:

- `code-change`: execution allowed.
- `docs-change`: execution allowed.
- `review-only`: execution not allowed.
- `verification-only`: execution not allowed.
- `release-readiness`: execution not allowed by default.

Full `orro auto` remains future work.
