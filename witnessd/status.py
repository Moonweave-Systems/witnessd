"""User-facing status render — enum-gated, no success theater (§7.6).

All user-facing status output goes through render_status(), and the output
domain is a fixed finite set. witnessd never emits a standalone success
string ("VERIFIED"/"DONE"/"COMPLETE"); success is only ever a Depone verdict
passed through verbatim.
"""

# Native witnessd states plus Depone verdicts passed through verbatim.
STATUS_DOMAIN: frozenset[str] = frozenset(
    {
        "evidence-pending",
        "emit-refused",
        "blocked",
        "refuted",
        "A0",
        "A1",
        "A2",
    }
)


def render_status(pending: int, verdict: str | None) -> str:
    if verdict is not None:
        if verdict not in STATUS_DOMAIN:
            raise ValueError(f"verdict not in status domain: {verdict!r}")
        return verdict
    return "evidence-pending"
