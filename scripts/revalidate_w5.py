#!/usr/bin/env python3
"""Re-derive W5 autonomy-safety fixtures from committed bytes."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import (
    ingest_signed_evidence_bundle,
    verify_capture_chain,
)
from depone.agent_fabric.sign import verify_signed_bundle

from witnessd.canonical import canonical_hash
from witnessd.installer import (
    ERR_WITNESSD_CONFIG_UNREADABLE,
    InstallerError,
    atomic_install,
    list_orphan_shims,
)
from witnessd.learning import (
    ERR_LEARNING_DELTA_UNAPPROVED,
    ERR_LEARNING_PROVENANCE_MISSING,
    validate_learning_delta_provenance,
)
from witnessd.liveness import derive_liveness
from witnessd.pause import PAUSE_EVENT, derive_pause_state
from witnessd.runlog import verify_runlog

FIX = ROOT / "fixtures" / "w5"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check_pause_override() -> list[dict]:
    records = _load_jsonl(FIX / "pause-override.jsonl")
    _assert(verify_runlog(records)["ok"] is True, "pause runlog chain must verify")
    _assert(derive_pause_state(records) is True, "pause fixture must derive paused")
    pause_index = next(i for i, record in enumerate(records) if record.get("event") == PAUSE_EVENT)
    side_effects = {"spawn", "dispatch", "edit", "commit"}
    after = records[pause_index + 1 :]
    _assert(
        not any(record.get("event") in side_effects for record in after),
        "pause override fixture has side-effect after user_pause",
    )
    return records


def _check_kill_all() -> None:
    records = _load_jsonl(FIX / "kill-all.jsonl")
    _assert(verify_runlog(records)["ok"] is True, "kill runlog chain must verify")
    state = derive_liveness(records, now_monotonic=10**12)
    _assert(bool(state), "kill fixture must contain at least one lane")
    _assert(all(value == "dead" for value in state.values()), "all lanes must derive dead")
    _assert(any(record.get("event") == "kill" for record in records), "kill event missing")


def _check_learning(records: list[dict]) -> None:
    capture = _load(FIX / "capture-for-learning.json")
    delta = _load(FIX / "learning-delta.json")
    bundle = _load(FIX / "learning-delta-bundle.json")
    pub = str(FIX / "keys" / "operator.pub")
    approvals = [record for record in records if record.get("event") == "learning_approval"]

    _assert(validate_capture_manifest(capture) == [], "learning capture must pass W1 validator")
    _assert(
        verify_capture_chain([capture])["decision"] == "pass",
        "learning capture must be valid chain genesis",
    )
    _assert(
        validate_learning_delta_provenance(
            delta,
            committed_captures=[capture],
            approval_events=approvals,
        )
        == [],
        "learning delta provenance must re-derive",
    )
    _assert(
        delta["provenance"]["capture_hash"] == canonical_hash(capture),
        "learning delta capture hash mismatch",
    )
    _assert(verify_signed_bundle(bundle, pub) is True, "learning bundle signature must verify")
    verdict = ingest_signed_evidence_bundle(
        bundle,
        pub,
        {"learning-delta": str(FIX / "learning-delta.json")},
        otel_spans=bundle.get("otel_spans"),
    )
    _assert(verdict.get("signature_verified") is True, "learning bundle signature not verified")
    _assert(verdict.get("decision") == "pass", f"learning bundle ingest failed: {verdict}")


def _check_negative_learning() -> None:
    capture = _load(FIX / "capture-for-learning.json")
    no_prov = _load(FIX / "negative" / "learning-delta-no-provenance.json")
    unapproved = _load(FIX / "negative" / "learning-delta-unapproved.json")
    _assert(
        ERR_LEARNING_PROVENANCE_MISSING
        in validate_learning_delta_provenance(
            no_prov, committed_captures=[capture], approval_events=[]
        ),
        "missing provenance fixture must be blocked",
    )
    _assert(
        ERR_LEARNING_DELTA_UNAPPROVED
        in validate_learning_delta_provenance(
            unapproved, committed_captures=[capture], approval_events=[]
        ),
        "unapproved delta fixture must be blocked",
    )


def _check_installer_negative() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dest = tmp_path / "dest"
        shim = tmp_path / "bin"
        dest.mkdir()
        shim.mkdir()
        existing = dest / "v1.txt"
        existing.write_text("ORIGINAL", encoding="utf-8")
        payload = tmp_path / "payload.txt"
        payload.write_text("NEW", encoding="utf-8")
        bad_config = tmp_path / "config.bin"
        shutil.copyfile(FIX / "negative" / "installer-unreadable-config" / "config.bin", bad_config)
        try:
            atomic_install(
                payload_path=str(payload),
                dest_dir=str(dest),
                config_path=str(bad_config),
                shim_dir=str(shim),
                version="v2",
            )
        except InstallerError as exc:
            _assert(
                exc.code == ERR_WITNESSD_CONFIG_UNREADABLE,
                "unreadable config must fail with ERR_WITNESSD_CONFIG_UNREADABLE",
            )
        else:
            raise AssertionError("unreadable config unexpectedly installed")
        _assert(existing.read_text(encoding="utf-8") == "ORIGINAL", "installer overwrote existing file")
        _assert(os.listdir(shim) == [], "installer created shim after unreadable config")
        _assert(list_orphan_shims(str(shim), str(dest)) == [], "installer left orphan shim")


def main() -> int:
    records = _check_pause_override()
    _check_kill_all()
    _check_learning(records)
    _check_negative_learning()
    _check_installer_negative()
    print("W5 revalidate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
