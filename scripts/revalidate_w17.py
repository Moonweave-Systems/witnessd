#!/usr/bin/env python3
"""Re-derive the W17 journaled resume fixture from committed bytes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle  # noqa: E402
from depone.agent_fabric.sign import verify_signed_bundle  # noqa: E402
from depone.agent_fabric.team_ledger import build_team_ledger_verdict  # noqa: E402
from witnessd.runlog import verify_runlog  # noqa: E402


FIXTURE_DIR = ROOT / "fixtures" / "w17"
PUBLIC_KEY = FIXTURE_DIR / "keys" / "operator.pub"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_positive_resume() -> dict[str, Any]:
    ledger = _load_json(FIXTURE_DIR / "team-ledger.json")
    verdict = build_team_ledger_verdict(ledger, base_dir=FIXTURE_DIR)
    _require(verdict["decision"] == "pass", f"W17 resumed ledger must pass: {verdict}")
    _require(ledger["resume_receipt"] == "team-resume-receipt.json", "resume receipt link drifted")

    lanes = {lane["lane_id"]: lane for lane in ledger["lanes"]}
    _require(lanes["lane-a"]["evidence_dir"] == "attempts/attempt-2/lane-a", "tampered lane must rerun into attempt-2")
    _require(lanes["lane-b"]["evidence_dir"] == "lane-b", "untampered lane must be skipped from surviving evidence")
    _require((FIXTURE_DIR / "lane-a" / "evidence-next-verdict.json").is_file(), "attempt-1 lane-a evidence missing")
    _require((FIXTURE_DIR / "attempts" / "attempt-2" / "lane-a").is_dir(), "attempt-2 lane-a evidence missing")

    receipt = _load_json(FIXTURE_DIR / ledger["resume_receipt"])
    decisions = {decision["lane_id"]: decision for decision in receipt["decisions"]}
    _require(decisions["lane-a"]["decision"] == "re_executed", "tampered lane must be re_executed")
    _require(decisions["lane-a"]["attempt"] == 2, "reexecuted lane must use attempt 2")
    _require(
        [item["attempt"] for item in decisions["lane-a"]["attempts"]] == [1, 2],
        "attempt history must be contiguous",
    )
    _require(decisions["lane-b"]["decision"] == "skipped_as_proven", "clean lane must skip as proven")
    _require(receipt["boundary"]["trusts_journal_completion"] is False, "resume must not trust journal completion")
    _require(receipt["boundary"]["skip_requires_rederivation"] is True, "resume skip must require rederivation")
    _require(receipt["boundary"]["append_only_attempt_history"] is True, "resume attempts must be append-only")

    bundle = _load_json(FIXTURE_DIR / "team-resume-receipt-bundle.json")
    _require(verify_signed_bundle(bundle, str(PUBLIC_KEY)) is True, "resume receipt signature failed")
    ingest = ingest_signed_evidence_bundle(
        bundle,
        str(PUBLIC_KEY),
        {"team-resume-receipt": str(FIXTURE_DIR / ledger["resume_receipt"])},
        otel_spans=bundle.get("otel_spans"),
    )
    _require(ingest["decision"] == "pass", f"resume bundle ingest failed: {ingest}")
    return ledger


def _assert_negative() -> None:
    forged = _load_json(FIXTURE_DIR / "negative" / "forged-skipped-team-ledger.json")
    verdict = build_team_ledger_verdict(forged, base_dir=FIXTURE_DIR)
    codes = {error["code"] for error in verdict["errors"]}
    _require(verdict["decision"] == "blocked", f"forged skipped ledger must block: {verdict}")
    _require(
        "ERR_TEAM_RESUME_RECEIPT_SKIP_NOT_REDERIVED" in codes,
        f"forged skipped receipt must fail rederivation, got {codes}",
    )


def _assert_runlog() -> None:
    _require(verify_runlog(_jsonl(FIXTURE_DIR / "runlog.jsonl"))["ok"], "W17 runlog broken")


def _assert_quota_free() -> None:
    forbidden = {b"auth.json", b"PRIVATE KEY", b"codex exec", b"claude", b"opencode"}
    for path in FIXTURE_DIR.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        for token in forbidden:
            _require(token not in data, f"quota/secret marker {token!r} in {path}")


def main() -> int:
    _assert_positive_resume()
    _assert_runlog()
    _assert_negative()
    _assert_quota_free()
    print("w17 resume: tampered lane re_executed into attempts/attempt-2")
    print("w17 resume: clean lane skipped_as_proven after rederivation")
    print("w17 negative: forged skipped_as_proven rejected")
    print("revalidate_w17: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
