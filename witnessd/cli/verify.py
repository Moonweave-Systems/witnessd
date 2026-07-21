from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from witnessd.cli._output import (
    _depone_subprocess_env,
    _emit_orro_error as _base_emit_orro_error,
    _hash_file,
    _run_depone_json,
    _with_structured_error,
)


PROOFCHECK_WORKFLOW_ARTIFACTS = (
    "repo-profile.json",
    "context-pack.json",
    "skillpack-lock.json",
    "verification-recipe.json",
    "verification-receipt.json",
    "pr-handoff.json",
)


VERIFY_REMEDIATION = {
    "proofcheck": (
        "proofcheck needs valid persisted evidence and verifier readiness",
        "an existing proofrun evidence directory and a pinned Depone provision",
        "python3 -m orro proofcheck <run-dir> --home .witnessd --out <run-dir>/proofcheck-verdict.json --json",
    ),
    "handoff": (
        "handoff is allowed only after a passing proofcheck verdict is bound to the current evidence",
        "a run directory with a passing bound proofcheck-verdict.json",
        "python3 -m orro handoff <run-dir> --home .witnessd --out <run-dir>/orro-handoff.json --json",
    ),
    "orro-doctor": (
        "one or more ORRO readiness prerequisites are blocked",
        "a provisioned witnessd home and the required local adapters",
        "python3 -m orro doctor --home .witnessd --json",
    ),
    "engine-lock": (
        "engine-lock needs a valid pinned engine provision and readable lock metadata",
        "a provisioned witnessd home and, for checks, an existing engine-lock file",
        "python3 -m orro engine-lock --home .witnessd --check .witnessd/orro-engine-lock.json --json",
    ),
    "advisory-provenance-check": (
        "advisory provenance can be re-derived only from sealed artifacts with verifier readiness",
        "a sealed advisory artifact directory and a provisioned witnessd home",
        "python3 -m orro advisory-provenance-check <artifact-dir> --home .witnessd --json",
    ),
}


def _verify_remediation(args: argparse.Namespace) -> tuple[str, str, str]:
    return VERIFY_REMEDIATION.get(
        str(getattr(args, "cmd", "")),
        (
            "the verification command is blocked by missing or invalid input",
            "valid command input and verifier readiness",
            "python3 -m orro --help",
        ),
    )


def _emit_orro_error(
    args: argparse.Namespace, *, code: str, message: str
) -> None:
    reason, required_input_or_grant, next_command = _verify_remediation(args)
    _base_emit_orro_error(
        args,
        code=code,
        message=message,
        reason=reason,
        required_input_or_grant=required_input_or_grant,
        next_command=next_command,
    )


def _with_verify_error(
    args: argparse.Namespace,
    payload: dict[str, object],
    *,
    default_code: str,
    default_message: str,
) -> dict[str, object]:
    reason, required_input_or_grant, next_command = _verify_remediation(args)
    return _with_structured_error(
        payload,
        default_code=default_code,
        default_message=default_message,
        reason=reason,
        required_input_or_grant=required_input_or_grant,
        next_command=next_command,
    )


def _emit_orro_engine_lock_check_error(
    args: argparse.Namespace, *, code: str, message: str
) -> None:
    payload = _with_verify_error(args, {
        "command": "orro engine-lock check",
        "locked": False,
        "mismatches": [],
        "boundary": {
            "approves_merge": False,
            "raises_assurance": False,
            "executes_commands": False,
            "verifies_evidence": False,
        },
        "error": {"code": code, "message": message},
    }, default_code=code, default_message=message)
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
        return
    print(code, file=sys.stderr)


def _collect_orro_artifact_hashes(
    evidence_dir: Path, *, out_path: Path | None = None
) -> list[dict[str, str]]:
    generated_names = {
        "orro-handoff.json",
        "proofcheck-verdict.json",
        "team-ledger-verdict.json",
    }
    artifact_hashes = []
    for path in sorted(p for p in evidence_dir.rglob("*") if p.is_file()):
        if path.name in generated_names or (out_path is not None and path == out_path):
            continue
        artifact_hashes.append(
            {
                "path": str(path.relative_to(evidence_dir)),
                "sha256": _hash_file(path),
            }
        )
    return artifact_hashes


def _proofcheck_binding(
    evidence_dir: Path, *, out_path: Path | None = None
) -> dict[str, object]:
    return {
        "kind": "orro-proofcheck-binding",
        "schema_version": "1.0",
        "evidence_dir": str(evidence_dir),
        "artifact_hashes": _collect_orro_artifact_hashes(
            evidence_dir, out_path=out_path
        ),
    }


def _advisory_provenance_home(evidence_dir: Path, *, home: Path | None) -> Path | None:
    if home is not None:
        return home.resolve(strict=False)
    if evidence_dir.parent.name == "runs":
        return evidence_dir.parent.parent.resolve(strict=False)
    return None


def _advisory_provenance_blocked(code: str, message: str) -> dict[str, object]:
    from witnessd.advisory_provenance import ADVISORY_PROVENANCE_SCHEMA_VERSION

    return {
        "kind": "orro-advisory-provenance-verdict",
        "schema_version": ADVISORY_PROVENANCE_SCHEMA_VERSION,
        "decision": "BLOCKED",
        "error_codes": [code],
        "errors": [{"code": code, "message": message, "evidence_path": ""}],
        "boundary": {
            "advisory_provenance_only": True,
            "asserts_correctness": False,
            "raises_assurance": False,
            "verifies_execution_evidence": False,
            "can_change_evidence_verdict": False,
            "executes_proofrun": False,
        },
    }


def _run_advisory_provenance_verify(
    evidence_dir: Path, *, home: Path | None
) -> tuple[int, dict[str, object]]:
    if home is None:
        return 2, _advisory_provenance_blocked(
            "ERR_ADVISORY_PROVENANCE_CHECK_HOME_REQUIRED",
            "--home is required when the evidence directory is not under <home>/runs",
        )
    try:
        env = _depone_subprocess_env(home)
    except Exception as exc:  # noqa: BLE001 - readiness is a blocked verdict
        return 2, _advisory_provenance_blocked(
            str(exc), "Depone verifier readiness is blocked"
        )

    from witnessd.trust_anchor import resolve_trust_anchor

    trust_anchor = resolve_trust_anchor(home=home)
    if not trust_anchor.public_key_path.is_file():
        return 2, _advisory_provenance_blocked(
            "ERR_ADVISORY_PROVENANCE_PUBLIC_KEY_MISSING",
            "trusted public key is required outside the advisory artifact directory",
        )
    env["DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE"] = str(trust_anchor.public_key_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "witnessd.advisory_provenance_verify",
            str(evidence_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return 2, _advisory_provenance_blocked(
            "ERR_ADVISORY_PROVENANCE_CHECK_FAILED",
            completed.stderr.strip()
            or completed.stdout.strip()
            or "Depone advisory validator produced no output",
        )
    if not isinstance(payload, dict):
        return 2, _advisory_provenance_blocked(
            "ERR_ADVISORY_PROVENANCE_CHECK_FAILED",
            "Depone advisory validator output must be a JSON object",
        )
    payload["trust_anchor"] = trust_anchor.trust_anchor
    payload["independent_trust_anchor"] = trust_anchor.independent
    return completed.returncode, payload


def _optional_advisory_provenance_verify(
    evidence_dir: Path, *, home: Path | None
) -> tuple[int, dict[str, object]] | None:
    if not (evidence_dir / "advisory-provenance-bundle.json").is_file():
        return None
    return _run_advisory_provenance_verify(
        evidence_dir,
        home=_advisory_provenance_home(evidence_dir, home=home),
    )


def _cmd_proofcheck(args: argparse.Namespace) -> int:
    from witnessd.orro_workflow import (
        role_lane_plan_binding_ref,
        workflow_plan_binding_ref,
        workflow_role_dispatch_ref,
    )

    evidence_arg = args.evidence_dir_option or args.evidence_dir
    if not evidence_arg:
        _emit_orro_error(
            args,
            code="ERR_ORRO_PROOFCHECK_INPUT_REQUIRED",
            message="evidence directory is required",
        )
        return 2
    evidence_dir = Path(evidence_arg).resolve(strict=False)
    from witnessd.cli.team_go import _load_json_if_exists

    reference_warning = _load_json_if_exists(
        evidence_dir / "moonweave-reference-adapter-warning.json"
    )
    home = Path(args.home).resolve(strict=False) if args.home else None
    try:
        env = _depone_subprocess_env(home)
    except Exception as exc:  # noqa: BLE001 - surface pin/readiness failure as usage
        _emit_orro_error(
            args,
            code=str(exc),
            message="Depone verifier readiness is blocked",
        )
        return 2

    from witnessd.trust_anchor import resolve_trust_anchor

    default_public_key = (
        home / "keys" / "operator-ed25519.pub.pem"
        if home is not None
        else Path(f"{evidence_dir}-keys") / "operator-ed25519.pub.pem"
    )
    trust_anchor = resolve_trust_anchor(
        home=home,
        runtime_public_key=default_public_key,
    )
    env["DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE"] = str(trust_anchor.public_key_path)

    out_path = Path(args.out).resolve(strict=False) if args.out else None
    command = ["proofcheck", "--evidence-dir", str(evidence_dir)]
    if out_path is not None:
        command.extend(["--out", str(out_path)])
    code, payload = _run_depone_json(command, env=env)
    command_policy: dict[str, object] | None = None
    command_policy_source: dict[str, object] | None = None
    try:
        command_policy, command_policy_source = _derive_command_lane_policy(
            evidence_dir=evidence_dir,
            env=env,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        code = 1
        payload = {
            **payload,
            "decision": "blocked",
            "error": {
                "code": "ERR_ORRO_POLICY_CONFORMANCE_DERIVATION_FAILED",
                "message": str(exc),
            },
        }
    if command_policy is not None:
        payload["policy_conformance"] = command_policy
        if command_policy_source is not None:
            payload["policy_conformance_source"] = command_policy_source
        if command_policy.get("overall") != "pass":
            code = 1
            payload["decision"] = "fail"
            policy_errors = [
                {
                    "code": axis.get("error_code")
                    or "ERR_ORRO_POLICY_CONFORMANCE_FAILED",
                    "message": (
                        f"{axis.get('axis', 'policy')} policy conformance failed"
                    ),
                    **(
                        {"path": axis["evidence_path"]}
                        if axis.get("evidence_path")
                        else {}
                    ),
                }
                for axis in command_policy.get("axes", [])
                if isinstance(axis, dict) and axis.get("status") == "fail"
            ]
            existing_errors = payload.get("errors")
            base_errors = existing_errors if isinstance(existing_errors, list) else []
            payload["errors"] = [*base_errors, *policy_errors]
            payload["error_count"] = len(payload["errors"])
        if out_path is not None and out_path.is_file():
            verdict_payload = json.loads(out_path.read_text(encoding="utf-8"))
            if not isinstance(verdict_payload, dict):
                raise ValueError("proofcheck verdict must be a JSON object")
            verdict_payload["decision"] = payload.get("decision", "blocked")
            verdict_payload["policy_conformance"] = command_policy
            verdict_payload["policy_conformance_source"] = command_policy_source
            if payload.get("errors"):
                verdict_payload["errors"] = payload["errors"]
            out_path.write_text(
                json.dumps(verdict_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    advisory_result = _optional_advisory_provenance_verify(evidence_dir, home=home)
    advisory_provenance = advisory_result[1] if advisory_result is not None else None
    binding: dict[str, object] | None = None
    binding_error: str | None = None
    if code == 0 and payload.get("decision") == "pass":
        binding = _proofcheck_binding(evidence_dir, out_path=out_path)
    workflow_plan_ref = workflow_plan_binding_ref(evidence_dir)
    role_lane_plan_ref = role_lane_plan_binding_ref(evidence_dir)
    workflow_role_dispatch = workflow_role_dispatch_ref(evidence_dir)
    if out_path is not None and (
        out_path.is_file() or (code == 0 and payload.get("decision") == "pass")
    ):
        try:
            verdict_payload = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            verdict_payload = None
            binding_error = str(exc)
        if isinstance(verdict_payload, dict):
            verdict_payload["trust_anchor"] = trust_anchor.trust_anchor
            verdict_payload["independent_trust_anchor"] = trust_anchor.independent
            if advisory_provenance is not None:
                verdict_payload["advisory_provenance"] = advisory_provenance
            if code == 0 and payload.get("decision") == "pass":
                verdict_payload["orro_binding"] = binding
                if workflow_plan_ref is not None:
                    verdict_payload["workflow_plan"] = workflow_plan_ref
                if role_lane_plan_ref is not None:
                    verdict_payload["role_lane_plan"] = role_lane_plan_ref
                if workflow_role_dispatch is not None:
                    verdict_payload["workflow_role_dispatch"] = workflow_role_dispatch
            try:
                out_path.write_text(
                    json.dumps(verdict_payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                binding_error = str(exc)
            else:
                binding_error = None
        elif binding_error is None:
            binding_error = "proofcheck-verdict.json must be a JSON object"
    if binding_error is not None:
        payload = {
            "decision": "blocked",
            "verifier_command": payload.get("verifier_command", "proofcheck"),
            "error": {
                "code": "ERR_ORRO_PROOFCHECK_VERDICT_BINDING_FAILED",
                "message": binding_error,
            },
        }
        code = 1
    result = {
        "command": "proofcheck",
        "verifier_command": payload.get("verifier_command", "proofcheck"),
        "decision": payload.get("decision", "blocked"),
        "trust_anchor": trust_anchor.trust_anchor,
        "independent_trust_anchor": trust_anchor.independent,
        "evidence_dir": str(evidence_dir),
        **(
            {"orro_binding": binding}
            if binding is not None and binding_error is None
            else {}
        ),
        **(
            {"workflow_plan": workflow_plan_ref}
            if workflow_plan_ref is not None
            else {}
        ),
        **(
            {"role_lane_plan": role_lane_plan_ref}
            if role_lane_plan_ref is not None
            else {}
        ),
        **(
            {"workflow_role_dispatch": workflow_role_dispatch}
            if workflow_role_dispatch is not None
            else {}
        ),
        **(
            {"advisory_provenance": advisory_provenance}
            if advisory_provenance is not None
            else {}
        ),
        "error_count": payload.get("error_count", 1 if payload.get("error") else 0),
        **(
            {"policy_conformance": command_policy}
            if command_policy is not None
            else {}
        ),
        **(
            {"policy_conformance_source": command_policy_source}
            if command_policy_source is not None
            else {}
        ),
        **({"out": payload["out"]} if payload.get("out") else {}),
        **({"errors": payload["errors"]} if payload.get("errors") else {}),
        **({"error": payload["error"]} if payload.get("error") else {}),
    }
    workflow_contract = _proofcheck_workflow_contract(payload)
    if workflow_contract is not None:
        result["workflow_contract"] = workflow_contract
        result["message"] = (
            "proofcheck blocked: this direct shell run is capture-only and is not "
            "proofcheckable by itself; missing workflow artifacts: "
            f"{', '.join(workflow_contract['missing_workflow_artifacts'])}. "
            "Run `orro scout` first, then the `flowplan -> proofrun -> proofcheck` "
            "workflow in the same run directory, or use `orro team go`."
        )
    if reference_warning is not None:
        from witnessd.cli.run import _reference_adapter_markers, _stamp_reference_adapter_artifact

        result.update(_reference_adapter_markers(reference_warning))
        if out_path is not None and out_path.is_file():
            _stamp_reference_adapter_artifact(out_path, reference_warning)
    if code != 0 or result["decision"] != "pass":
        result = _with_verify_error(
            args,
            result,
            default_code="ERR_ORRO_PROOFCHECK_BLOCKED",
            default_message="proofcheck did not produce a passing verdict",
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if code == 0 and result["decision"] == "pass" else 1


def _derive_command_lane_policy(
    *,
    evidence_dir: Path,
    env: dict[str, str],
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    role_lane_path = evidence_dir / "role-lane-plan.json"
    if not role_lane_path.is_file():
        return None, None
    role_lane_plan = json.loads(role_lane_path.read_text(encoding="utf-8"))
    if not isinstance(role_lane_plan, dict):
        raise ValueError("role-lane-plan.json must contain a JSON object")
    lanes = role_lane_plan.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError("role-lane-plan.json lanes must be a list")
    command_lanes = [
        lane
        for lane in lanes
        if isinstance(lane, dict)
        and lane.get("adapter") == "shell"
        and isinstance(lane.get("commands"), list)
        and lane.get("commands")
    ]
    if not command_lanes:
        return None, None
    if len(command_lanes) != 1:
        raise ValueError("declared command policy verification requires exactly one lane")

    lane = command_lanes[0]
    lane_id = str(lane.get("lane_id", ""))
    if not lane_id:
        raise ValueError("declared command lane_id is missing")
    lane_evidence = evidence_dir / lane_id
    if not lane_evidence.is_dir():
        raise ValueError(f"declared command lane evidence is missing: {lane_id}")

    verifier_dir = evidence_dir / "depone-policy-verification"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    plan_path = verifier_dir / "plan.json"
    report_path = verifier_dir / f"{lane_id}.json"
    required_axes = role_lane_plan.get("required_role_capability_axes")
    if required_axes != ["write_scope"]:
        raise ValueError("declared command lane must require the write_scope axis")
    plan = {
        "schema_version": "0.5",
        "plan_id": f"orro-command-policy-{lane_id}",
        "phases": [{"id": "proofrun"}],
        "required_role_capability_axes": list(required_axes),
    }
    plan_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "depone",
            "verify",
            str(plan_path),
            "--evidence",
            str(lane_evidence),
            "--out",
            str(report_path),
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if not report_path.is_file():
        raise ValueError(
            "Depone policy verification did not write a report: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Depone policy verification report must be a JSON object")
    policy = report.get("policy_conformance")
    if not isinstance(policy, dict) or policy.get("overall") not in {
        "pass",
        "fail",
        "inconclusive",
    }:
        raise ValueError("Depone policy verification report lacks policy_conformance")
    return dict(policy), {
        "verifier": "Depone",
        "report": str(report_path),
        "lane_id": lane_id,
    }


def _proofcheck_workflow_contract(
    payload: dict[str, object],
) -> dict[str, object] | None:
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return None
    missing: list[str] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        message = error.get("message")
        if not isinstance(message, str):
            continue
        for artifact in PROOFCHECK_WORKFLOW_ARTIFACTS:
            if (
                message == f"required artifact is missing: {artifact}"
                and artifact not in missing
            ):
                missing.append(artifact)
    workflow_packet_missing = [
        artifact for artifact in missing if artifact != "verification-receipt.json"
    ]
    if not workflow_packet_missing:
        return None
    return {
        "capture_only": True,
        "proofcheckable_by_itself": False,
        "missing_workflow_artifacts": missing,
        "next_step": (
            "Run `orro scout` first, then `flowplan -> proofrun -> proofcheck` "
            "in the same run directory, or use `orro team go`."
        ),
    }


def _cmd_advisory_provenance_check(args: argparse.Namespace) -> int:
    evidence_dir = Path(args.evidence_dir).resolve(strict=False)
    home = Path(args.home).resolve(strict=False) if args.home else None
    if home is None:
        _emit_orro_error(
            args,
            code="ERR_ADVISORY_PROVENANCE_CHECK_HOME_REQUIRED",
            message="--home is required to locate the pinned Depone verifier and operator key",
        )
        return 2
    code, payload = _run_advisory_provenance_verify(evidence_dir, home=home)
    if code != 0:
        payload = _with_verify_error(
            args,
            payload,
            default_code="ERR_ADVISORY_PROVENANCE_CHECK_BLOCKED",
            default_message="advisory provenance re-derivation did not pass",
        )
    print(json.dumps(payload, sort_keys=True))
    return code




def _cmd_handoff(args: argparse.Namespace) -> int:
    from witnessd.orro_workflow import (
        role_lane_plan_binding_ref,
        workflow_plan_binding_ref,
        workflow_role_dispatch_ref,
    )

    evidence_arg = args.evidence_dir_option or args.evidence_dir
    if not evidence_arg:
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_INPUT_REQUIRED",
            message="evidence directory is required",
        )
        return 2
    evidence_dir = Path(evidence_arg).resolve(strict=False)
    if not evidence_dir.is_dir():
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_EVIDENCE_DIR_MISSING",
            message=f"evidence directory is missing: {evidence_dir}",
        )
        return 2

    proofcheck_path = evidence_dir / "proofcheck-verdict.json"
    if not proofcheck_path.is_file():
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_REQUIRED",
            message="proofcheck-verdict.json is required before handoff",
        )
        return 1
    try:
        proofcheck_payload = json.loads(proofcheck_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_LOAD_FAILED",
            message=f"failed to read proofcheck-verdict.json: {exc}",
        )
        return 1
    if not isinstance(proofcheck_payload, dict):
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_LOAD_FAILED",
            message="proofcheck-verdict.json must be a JSON object",
        )
        return 1
    if proofcheck_payload.get("decision") != "pass":
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_NOT_PASS",
            message="proofcheck-verdict.json decision must be pass",
        )
        return 1
    home = Path(args.home).resolve(strict=False) if args.home else None
    advisory_result = _optional_advisory_provenance_verify(evidence_dir, home=home)
    advisory_provenance = advisory_result[1] if advisory_result is not None else None
    if (
        advisory_provenance is not None
        and advisory_provenance.get("decision") != "PASS"
    ):
        decision = advisory_provenance.get("decision")
        error_code = (
            "ERR_ORRO_HANDOFF_ADVISORY_PROVENANCE_REFUTED"
            if decision == "REFUTE"
            else "ERR_ORRO_HANDOFF_ADVISORY_PROVENANCE_BLOCKED"
        )
        error_payload = _with_verify_error(args, {
            "error": {
                "code": error_code,
                "message": (
                    "advisory provenance re-derivation must pass before handoff"
                ),
            },
            "advisory_provenance": advisory_provenance,
        }, default_code=error_code, default_message="advisory provenance re-derivation must pass before handoff")
        if args.json:
            print(json.dumps(error_payload, sort_keys=True))
        else:
            print(error_code, file=sys.stderr)
        return 1
    out_path = Path(args.out).resolve(strict=False) if args.out else None
    expected_binding = _proofcheck_binding(evidence_dir, out_path=out_path)
    proofcheck_binding = proofcheck_payload.get("orro_binding")
    if not isinstance(proofcheck_binding, dict):
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_UNBOUND",
            message="proofcheck-verdict.json must include an ORRO proofcheck binding",
        )
        return 1
    if proofcheck_binding != expected_binding:
        _emit_orro_error(
            args,
            code="ERR_ORRO_HANDOFF_PROOFCHECK_BINDING_MISMATCH",
            message="proofcheck-verdict.json does not match this evidence directory",
        )
        return 1

    artifact_hashes = _collect_orro_artifact_hashes(evidence_dir, out_path=out_path)
    workflow_plan_ref = workflow_plan_binding_ref(evidence_dir)
    role_lane_plan_ref = role_lane_plan_binding_ref(evidence_dir)
    workflow_role_dispatch = workflow_role_dispatch_ref(evidence_dir)
    decision_refs = []
    for name in ("proofcheck-verdict.json", "team-ledger-verdict.json"):
        path = evidence_dir / name
        if not path.is_file():
            continue
        ref = {"path": name, "sha256": _hash_file(path)}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if isinstance(payload.get("decision"), str):
            ref["decision"] = payload["decision"]
        decision_refs.append(ref)
    if advisory_provenance is not None:
        decision_refs.append(
            {
                "path": "advisory-provenance-bundle.json",
                "sha256": _hash_file(evidence_dir / "advisory-provenance-bundle.json"),
                "track": "advisory-provenance",
                "decision": advisory_provenance["decision"],
                "error_codes": advisory_provenance.get("error_codes", []),
            }
        )

    payload = {
        "kind": "orro-handoff",
        "schema_version": "1.0",
        "evidence_dir": str(evidence_dir),
        "artifact_hashes": artifact_hashes,
        "decision_refs": decision_refs,
        **(
            {"advisory_provenance": advisory_provenance}
            if advisory_provenance is not None
            else {}
        ),
        **(
            {"workflow_plan": workflow_plan_ref}
            if workflow_plan_ref is not None
            else {}
        ),
        **(
            {"role_lane_plan": role_lane_plan_ref}
            if role_lane_plan_ref is not None
            else {}
        ),
        **(
            {"workflow_role_dispatch": workflow_role_dispatch}
            if workflow_role_dispatch is not None
            else {}
        ),
        "boundary": {
            "approves_merge": False,
            "raises_assurance": False,
        },
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, sort_keys=True))
    return 0

def _cmd_orro_doctor(args: argparse.Namespace) -> int:
    from witnessd.preflight import probe_adapter_capability

    checks = []
    checks.append({"name": "witnessd_import", "status": "pass"})
    home = Path(args.home).resolve(strict=False) if args.home else None
    env = os.environ.copy()
    if home is not None:
        from witnessd.distribution import classify_depone_pin_state

        depone_pin_state = classify_depone_pin_state(home)
        if depone_pin_state["state"] == "stale-upgrade":
            depone_root = str(depone_pin_state["depone_root"])
            remediation_command = (
                "python3 -m orro init "
                f"--home {shlex.quote(str(home))} "
                f"--repo {shlex.quote(str(Path.cwd().resolve(strict=False)))} "
                f"--depone-root {shlex.quote(depone_root)}"
            )
            checks.append(
                {
                    "name": "depone_pin",
                    "status": "blocked",
                    "code": str(depone_pin_state["code"]),
                    "reason": "stale-upgrade-provision",
                    "path": str(home / "provision.json"),
                    "recorded_commit": depone_pin_state["recorded_commit"],
                    "current_commit": depone_pin_state["current_commit"],
                    "expected_commit": depone_pin_state["expected_commit"],
                    "detail": (
                        "Stale upgrade provision: the Depone checkout is at the "
                        "current expected pin, but provision.json records an older "
                        "valid commit. Run the explicit init remediation to refresh "
                        "provisioning metadata."
                    ),
                    "remediation": {
                        "command": remediation_command,
                        "effect": (
                            "refreshes provision.json to the current pinned Depone; "
                            "preserves existing run artifacts"
                        ),
                        "requires_explicit_user_action": True,
                    },
                }
            )
            if not args.json:
                print(
                    "depone_pin: blocked (stale-upgrade-provision); "
                    f"remediation: {remediation_command}",
                    file=sys.stderr,
                )
        elif depone_pin_state["state"] != "ok":
            check = {
                "name": "depone_pin",
                "status": "blocked",
                "code": str(depone_pin_state["code"]),
                "reason": (
                    "depone-pin-mismatch"
                    if depone_pin_state["state"] == "mismatch"
                    else "depone-pin-missing"
                ),
                "path": str(home / "provision.json"),
            }
            for field in (
                "depone_root",
                "recorded_commit",
                "current_commit",
                "expected_commit",
            ):
                if field in depone_pin_state:
                    check[field] = depone_pin_state[field]
            checks.append(check)
        else:
            try:
                env = _depone_subprocess_env(home)
            except Exception as exc:  # noqa: BLE001 - readiness check reports pin race
                checks.append(
                    {
                        "name": "depone_pin",
                        "status": "blocked",
                        "code": str(exc),
                        "path": str(home / "provision.json"),
                    }
                )
            else:
                checks.append(
                    {
                        "name": "depone_pin",
                        "status": "pass",
                        "path": str(home / "provision.json"),
                    }
                )
    engine_lock_path: Path | None = None
    if args.engine_lock:
        engine_lock_path = Path(args.engine_lock).resolve(strict=False)
    elif home is not None:
        default_engine_lock_path = home / "orro-engine-lock.json"
        if default_engine_lock_path.is_file():
            engine_lock_path = default_engine_lock_path
    if engine_lock_path is not None:
        if home is None:
            checks.append(
                {
                    "name": "engine_lock",
                    "status": "blocked",
                    "locked": False,
                    "code": "ERR_ORRO_ENGINE_LOCK_HOME_REQUIRED",
                }
            )
        else:
            from witnessd.distribution import ProvisionError, check_orro_engine_lock

            try:
                engine_lock = check_orro_engine_lock(
                    home=home,
                    witnessd_root=Path(__file__).resolve().parents[1],
                    lock_path=engine_lock_path,
                )
            except ProvisionError as exc:
                checks.append(
                    {
                        "name": "engine_lock",
                        "status": "blocked",
                        "locked": False,
                        "code": exc.code,
                    }
                )
            else:
                checks.append(
                    {
                        "name": "engine_lock",
                        "status": "pass" if engine_lock["locked"] else "blocked",
                        "locked": engine_lock["locked"],
                        "code": engine_lock.get("error", {}).get("code"),
                        "mismatches": engine_lock["mismatches"],
                    }
                )
    completed = subprocess.run(
        [sys.executable, "-m", "depone", "doctor", "--self-test"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    checks.append(
        {
            "name": "depone_doctor",
            "status": "pass" if completed.returncode == 0 else "blocked",
            "detail": completed.stdout.strip() or completed.stderr.strip(),
        }
    )

    for adapter in args.adapter or ["codex", "claude", "agy", "gemini", "opencode"]:
        receipt = probe_adapter_capability(adapter, repo=os.getcwd())
        checks.append(
            {
                "name": f"adapter:{adapter}",
                "status": "pass" if receipt["decision"] == "pass" else "missing",
                "receipt": receipt,
            }
        )
    decision = (
        "blocked" if any(check["status"] == "blocked" for check in checks) else "pass"
    )
    payload = {
        "command": "orro doctor",
        "decision": decision,
        "checks": checks,
        "boundary": {
            "verifier_refuted": False,
            "executes_recipes": False,
            "raises_assurance": False,
        },
    }
    if decision != "pass":
        payload = _with_verify_error(
            args,
            payload,
            default_code="ERR_ORRO_DOCTOR_READINESS_BLOCKED",
            default_message="ORRO readiness checks are blocked",
        )
    print(json.dumps(payload, sort_keys=True))
    return 0 if decision == "pass" else 1


def _cmd_orro_engine_lock(args: argparse.Namespace) -> int:
    if not args.home:
        if args.check:
            _emit_orro_engine_lock_check_error(
                args,
                code="ERR_ORRO_ENGINE_LOCK_HOME_REQUIRED",
                message="--home is required to check the pinned Depone provision",
            )
        else:
            _emit_orro_error(
                args,
                code="ERR_ORRO_ENGINE_LOCK_HOME_REQUIRED",
                message="--home is required to read the pinned Depone provision",
            )
        return 2
    from witnessd.distribution import (
        ERR_ORRO_ENGINE_LOCK_MISMATCH,
        ProvisionError,
        build_orro_engine_lock,
        check_orro_engine_lock,
    )

    if args.check:
        try:
            check_payload = check_orro_engine_lock(
                home=Path(args.home).resolve(strict=False),
                witnessd_root=Path(__file__).resolve().parents[1],
                lock_path=Path(args.check).resolve(strict=False),
            )
        except ProvisionError as exc:
            _emit_orro_engine_lock_check_error(
                args,
                code=exc.code,
                message="ORRO engine lock cannot be checked against the current provision",
            )
            return 2
        if check_payload["locked"]:
            print(json.dumps(check_payload, sort_keys=True))
            return 0
        check_payload = _with_verify_error(
            args,
            check_payload,
            default_code="ERR_ORRO_ENGINE_LOCK_CHECK_BLOCKED",
            default_message="ORRO engine lock check did not match the current provision",
        )
        print(json.dumps(check_payload, sort_keys=True))
        if check_payload.get("error", {}).get("code") == ERR_ORRO_ENGINE_LOCK_MISMATCH:
            return 1
        return 2
    try:
        payload = build_orro_engine_lock(
            home=Path(args.home).resolve(strict=False),
            witnessd_root=Path(__file__).resolve().parents[1],
        )
    except ProvisionError as exc:
        _emit_orro_error(
            args,
            code=exc.code,
            message="ORRO engine lock cannot be produced from the current provision",
        )
        return 2
    if args.out:
        out_path = Path(args.out).resolve(strict=False)
        try:
            out_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            _emit_orro_error(
                args,
                code="ERR_ORRO_ENGINE_LOCK_WRITE_FAILED",
                message=str(exc),
            )
            return 1
    print(json.dumps(payload, sort_keys=True))
    return 0
