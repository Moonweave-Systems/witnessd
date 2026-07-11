"""Model-declaration advisory artifact (explicit lane model routing).

Records whether a lane's explicitly requested model was actually accepted by
the real CLI, when the CLI's own signal makes that determinable. This is
deliberately NOT part of Depone's evidence contract -- run-intent.json and
runner-receipt.json schemas are untouched (per moonweave/CLAUDE.md: contract
capability changes land in Depone first). Like agy/gemini's
review-receipt.json, this is a witnessd-local advisory record: it cannot
change the evidence verdict, and it only ever exists alongside a raw
invocation (-m/--model argv) that already carries the requested model name
as ordinary evidence bytes -- this artifact adds nothing but the
verification-status judgment on top of that.

Only emitted when a lane explicitly requests a model. Tier-only routing
(no explicit model) emits no artifact at all -- there is no request to make
a verification claim about.
"""

from __future__ import annotations

from typing import Any

MODEL_DECLARATION_KIND = "moonweave-model-declaration"
MODEL_DECLARATION_SCHEMA_VERSION = "1.0"

# codex: the CLI itself fails the turn loud (turn.failed with a model-specific
# message) on an invalid model -- accepting a turn without that signal is as
# close to "verified" as this evidence layer can honestly claim.
VERIFICATION_VERIFIED = "verified"
# claude/codex: the CLI's own signal reported the requested model was rejected.
VERIFICATION_REJECTED = "rejected"
# agy: --model has no rejection signal at all (silent fallback observed live),
# so a request can never be marked verified -- only that it was asked for.
VERIFICATION_REQUESTED_UNVERIFIED = "requested-unverified"


def build_model_declaration(
    *,
    adapter: str,
    requested_model: str,
    verification_status: str,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": MODEL_DECLARATION_KIND,
        "schema_version": MODEL_DECLARATION_SCHEMA_VERSION,
        "can_change_evidence_verdict": False,
        "adapter": adapter,
        "requested_model": requested_model,
        "verification_status": verification_status,
        "detail": detail,
    }
