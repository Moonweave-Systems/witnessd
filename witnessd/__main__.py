"""witnessd CLI — `run` / `status` / `self-test`.

`run` wires the lane pipeline: observer/runner separation check (Task 4) →
shell adapter (Task 5) → observer_capture (Task 6) → Evidence Emitter (Task 11).
Separation is enforced fail-closed BEFORE any capture happens, so a --out/--log
inside the runner sandbox aborts with ERR_OBSERVER_NOT_SEPARATED and writes
nothing.

`status` renders only through the enum-gated render_status (Task 3): witnessd
never self-declares success; a lane is "evidence-pending" until a separate
Depone verification returns a verdict.

`self-test --all` runs each module's `_self_test` and reports `N/N passed`.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import shutil
import shlex
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from witnessd.cli._output import (
    _depone_subprocess_env,
    _emit_orro_error,
    _hash_file,
    _json_or_text,
    _read_runlog,
    _run_depone_json,
    _structured_error,
    _write_json_file,
)

from witnessd.observer import ObserverSeparationError, assert_separated
from witnessd.status import render_status


def _cli_handler(module: str, name: str):
    def _invoke(args: argparse.Namespace) -> int:
        import importlib

        return getattr(importlib.import_module(f"witnessd.cli.{module}"), name)(args)

    return _invoke


DEFAULT_TEAM_PLAN_RUN_LANE_TIMEOUT_SECONDS = 900

PROOFCHECK_WORKFLOW_ARTIFACTS = (
    "repo-profile.json",
    "context-pack.json",
    "skillpack-lock.json",
    "verification-recipe.json",
    "verification-receipt.json",
    "pr-handoff.json",
)


def _count_pending(evidence_dir: str) -> int:
    if not os.path.isdir(evidence_dir):
        return 0
    count = 0
    for root, _dirs, files in os.walk(evidence_dir):
        count += sum(1 for name in files if name == "capture-manifest.json")
    return count












def _cmd_plan(args: argparse.Namespace) -> int:
    from witnessd.adapter_run import LaneBlocked, run_adapter_lane
    from witnessd.adapters.codex import CodexAdapterError
    from witnessd.orro_workflow import (
        OrroWorkflowError,
        compile_role_lane_plan,
        compile_workflow_plan,
        summarize_executable_lanes,
        write_role_lane_plan,
    )
    from witnessd.planner import (
        PlannerError,
        parse_draft_packets,
        plan_heuristic,
        seal_plan,
    )

    draft_events: list[dict] = []
    packets: list[dict] | None = None
    root = os.path.abspath(args.root)
    workflow_plan = None
    role_lane_plan_ref: dict[str, object] | None = None

    if getattr(args, "profile", None):
        try:
            workflow_plan = compile_workflow_plan(
                goal=args.goal,
                profile=args.profile,
                lane_intent=getattr(args, "lane_intent", None),
            )
        except OrroWorkflowError as exc:
            _emit_orro_error(
                args,
                code=exc.code,
                message="unknown ORRO workflow profile",
            )
            return 2

    if getattr(args, "check", None) and not getattr(args, "role_lanes_out", None):
        _emit_orro_error(
            args,
            code="ERR_ORRO_VERIFICATION_CHECK_UNSUPPORTED",
            message=(
                "--check requires --role-lanes-out with --profile verification-only"
            ),
        )
        return 2

    if args.draft_adapter:
        draft_root = f"{root.rstrip(os.sep)}-witnessd-plan-draft"
        draft_out = args.draft_out or os.path.join(draft_root, "evidence")
        try:
            result = run_adapter_lane(
                root=root,
                sandbox=root,
                adapter=args.draft_adapter,
                task_id="w11-plan-draft",
                prompt=_draft_prompt(args.goal),
                arm="direct",
                tier=args.tier,
                is_supported=lambda _model: True,
                budget={
                    "max_tokens": args.max_tokens,
                    "max_usd": args.max_usd,
                    "max_depth": args.max_depth,
                },
                predicted_tokens=args.predicted_tokens,
                predicted_usd=args.predicted_usd,
                codex_binary=args.codex_binary,
                claude_binary=args.claude_binary,
                agy_binary=args.agy_binary,
                gemini_binary=args.gemini_binary,
                opencode_binary=args.opencode_binary,
                evidence_dir=draft_out,
                state_root=draft_root,
                allowed_touched_files=["witnessd-plan-draft.txt"],
            )
            transcript = (
                Path(result["evidence_dir"]).resolve(strict=False).parent
                / "adapter-transcript.txt"
            )
            packets = parse_draft_packets(transcript.read_text(encoding="utf-8"))
            draft_events.append(
                {
                    "adapter": args.draft_adapter,
                    "status": "adopted",
                    "evidence_dir": result["evidence_dir"],
                }
            )
        except (LaneBlocked, PlannerError, OSError, CodexAdapterError) as exc:
            reason = str(exc).split(":", 1)[0]
            draft_events.append(
                {
                    "adapter": args.draft_adapter,
                    "status": "fallback",
                    "reason": reason,
                }
            )

    if packets is None:
        packets = plan_heuristic(args.goal, seed=args.seed, root=root)

    sealed = seal_plan(packets, goal=args.goal)
    payload: dict[str, object] = {"sealed_plan": sealed, "draft_events": draft_events}
    if workflow_plan is not None:
        payload["workflow_plan"] = workflow_plan
    if getattr(args, "role_lanes_out", None):
        if workflow_plan is None:
            workflow_plan = compile_workflow_plan(
                goal=args.goal,
                profile="code-change",
                lane_intent=getattr(args, "lane_intent", None),
            )
            payload["workflow_plan"] = workflow_plan
        rolepack: dict[str, object] | None = None
        try:
            from witnessd.model_policy import DEFAULT_MODEL_POLICY
            from witnessd.role_capability import (
                RolepackError,
                load_rolepack_file,
                resolve_rolepack,
            )

            selected_rolepack_inputs = [
                value
                for value in (
                    args.rolepack,
                    args.rolepack_file,
                    getattr(args, "team", None),
                )
                if value
            ]
            if len(selected_rolepack_inputs) > 1:
                raise RolepackError(
                    "ERR_ORRO_ROLEPACK_CONFLICT",
                    "--rolepack, --rolepack-file, and --team are mutually exclusive",
                )
            rolepack = (
                load_rolepack_file(args.team or args.rolepack_file)
                if args.team or args.rolepack_file
                else resolve_rolepack(args.rolepack)
            )

            role_lane_plan = compile_role_lane_plan(
                workflow_plan=workflow_plan,
                lane_adapter=args.lane_adapter,
                tier=args.role_lane_tier,
                policy=DEFAULT_MODEL_POLICY if args.model_policy == "default" else None,
                rolepack=rolepack,
                check_commands=getattr(args, "check", None),
            )
            role_lane_plan_ref = write_role_lane_plan(
                Path(args.role_lanes_out).resolve(strict=False),
                role_lane_plan,
            )
        except RolepackError as exc:
            _emit_orro_error(args, code=exc.code, message=exc.message)
            return 1
        except OrroWorkflowError as exc:
            details = _flowplan_role_lane_error_details(
                args,
                code=exc.code,
                message=str(exc),
                rolepack=rolepack,
            )
            _emit_orro_error(
                args,
                code=exc.code,
                message=str(exc),
                **(details or {}),
            )
            return 1
        payload["role_lane_plan"] = role_lane_plan_ref
        payload.update(summarize_executable_lanes(role_lane_plan["lanes"]))
    if getattr(args, "out", None):
        out_path = Path(args.out).resolve(strict=False)
        try:
            out_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            _emit_orro_error(
                args,
                code="ERR_ORRO_WORKFLOW_PLAN_WRITE_FAILED",
                message=str(exc),
            )
            return 1
    print(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _draft_prompt(goal: str) -> str:
    return (
        "Return only JSON with a packets array of witnessd LanePacket objects for "
        f"this goal: {goal}"
    )




def _derive_runlog_liveness(path: str) -> dict[str, str]:
    from witnessd.liveness import derive_liveness

    records = _read_runlog(path)
    return derive_liveness(records, now_monotonic=time.monotonic())








def _flowplan_role_lane_error_details(
    args: argparse.Namespace,
    *,
    code: str,
    message: str,
    rolepack: dict[str, object] | None,
) -> dict[str, str] | None:
    if code == "ERR_ORRO_ROLE_LANE_WRITE_SCOPE_REQUIRED":
        flowplan_command = (
            "python3 -m orro flowplan "
            f"{shlex.quote(str(args.goal))} --root {shlex.quote(str(args.root))} "
            f"--profile {shlex.quote(str(args.profile or 'code-change'))} "
            f"--role-lanes-out {shlex.quote(str(args.role_lanes_out))} "
            "--rolepack-file rolepack.json --model-policy default"
        )
        return {
            "reason": (
                "code-change proofrun lanes need a concrete write_scope from the rolepack"
            ),
            "required_input_or_grant": (
                "a rolepack granting the role's write_scope"
            ),
            "next_command": (
                "python3 -m orro team init --template developer "
                "--write-scope '<glob>' --out rolepack.json && "
                f"{flowplan_command}"
            ),
        }
    if code != "ERR_ROLE_CAPABILITY_ADAPTER_NOT_GRANTED":
        return None

    grants = rolepack.get("grants") if isinstance(rolepack, dict) else None
    grant_rows = [grant for grant in grants if isinstance(grant, dict)] if isinstance(grants, list) else []
    role_ids = sorted(
        str(grant["role_id"])
        for grant in grant_rows
        if isinstance(grant.get("role_id"), str)
    )
    role_id = next(
        (candidate for candidate in role_ids if f"role_id={candidate!r}" in message),
        role_ids[0] if len(role_ids) == 1 else "<role>",
    )
    matching_grant = next(
        (grant for grant in grant_rows if grant.get("role_id") == role_id),
        None,
    )
    adapters = (
        matching_grant.get("adapters")
        if isinstance(matching_grant, dict)
        else None
    )
    granted_adapters = sorted(
        str(adapter) for adapter in adapters if isinstance(adapter, str)
    ) if isinstance(adapters, list) else []
    resolved_adapter = next(
        (
            adapter
            for adapter in ("shell", "codex", "claude", "agy", "gemini", "opencode")
            if f"adapter {adapter!r}" in message
        ),
        str(args.lane_adapter),
    )
    reason = (
        f"resolved adapter {resolved_adapter!r} is not granted for role_id={role_id!r}; "
        f"the rolepack has granted adapters {granted_adapters!r} for that role and grants "
        f"role_ids {role_ids!r}"
    )
    if resolved_adapter == "shell" and any(
        adapter != "shell" for adapter in granted_adapters
    ):
        reason += (
            "; pass --model-policy default (routes to the granted adapter) or ensure "
            "the rolepack grants the adapter you intend"
        )
    rolepack_arg = (
        f"--rolepack-file {shlex.quote(str(args.rolepack_file))}"
        if args.rolepack_file
        else (
            f"--team {shlex.quote(str(args.team))}"
            if getattr(args, "team", None)
            else f"--rolepack {shlex.quote(str(args.rolepack))}"
        )
    )
    next_command = (
        "python3 -m orro flowplan "
        f"{shlex.quote(str(args.goal))} --root {shlex.quote(str(args.root))} "
        f"--profile {shlex.quote(str(args.profile or 'code-change'))} "
        f"--role-lanes-out {shlex.quote(str(args.role_lanes_out))} "
        f"{rolepack_arg} --model-policy default; or scaffold a matching rolepack: "
        "python3 -m orro team init --role <role> --write-scope '<glob>' "
        "--out rolepack.json"
    )
    return {
        "reason": reason,
        "required_input_or_grant": (
            f"a rolepack granting adapter {resolved_adapter!r} for role_id={role_id!r}; "
            f"granted adapters: {granted_adapters!r}; role_ids: {role_ids!r}"
        ),
        "next_command": next_command,
    }




def _emit_orro_engine_lock_check_error(
    args: argparse.Namespace, *, code: str, message: str
) -> None:
    payload = {
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
    }
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
    print(json.dumps(result, sort_keys=True))
    return 0 if code == 0 and result["decision"] == "pass" else 1


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
        error_payload = {
            "error": {
                "code": error_code,
                "message": (
                    "advisory provenance re-derivation must pass before handoff"
                ),
            },
            "advisory_provenance": advisory_provenance,
        }
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
        try:
            env = _depone_subprocess_env(home)
        except Exception as exc:  # noqa: BLE001 - readiness check reports pin failure
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
        print(json.dumps(check_payload, sort_keys=True))
        if check_payload["locked"]:
            return 0
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





























































































def _cmd_self_test(args: argparse.Namespace) -> int:
    from witnessd import (
        budget,
        emitter,
        fanin,
        faultkit,
        installer,
        isolation,
        killswitch,
        learning,
        lock,
        liveness,
        pause,
        pilot,
        preflight,
        router,
        scheduler,
        session,
        signing,
        state,
        substrate,
        supervisor,
        team_ledger,
        worktree,
    )
    from witnessd.adapters import base as adapter_base
    from witnessd.adapters import codex as codex_adapter

    checks = [
        ("signing", signing._self_test),
        ("substrate", substrate._self_test),
        ("emitter", emitter._self_test),
        ("liveness", liveness._self_test),
        ("supervisor", supervisor._self_test),
        ("scheduler", scheduler._self_test),
        ("session", session._self_test),
        ("isolation", isolation._self_test),
        ("pause", pause._self_test),
        ("killswitch", killswitch._self_test),
        ("pilot", pilot._self_test),
        ("learning", learning._self_test),
        ("installer", installer._self_test),
        ("faultkit", faultkit._self_test),
        ("lock", lock._self_test),
        ("worktree", worktree._self_test),
        ("team_ledger", team_ledger._self_test),
        ("fanin", fanin._self_test),
        ("adapter_base", adapter_base._self_test),
        ("codex_adapter", codex_adapter._self_test),
        ("preflight", preflight._self_test),
        ("router", router._self_test),
        ("budget", budget._self_test),
        ("state", state._self_test),
    ]
    report_pass_names = {
        "adapter_base",
        "codex_adapter",
        "preflight",
        "router",
        "budget",
        "state",
        "pause",
        "killswitch",
        "learning",
        "installer",
    }
    passed = 0
    for name, check in checks:
        try:
            check()
            if name in report_pass_names:
                print(f"witnessd {name} --self-test: pass")
            passed += 1
        except Exception as exc:  # noqa: BLE001 — report which self-test failed
            print(f"witnessd {name} --self-test: FAIL ({exc})", file=sys.stderr)
    total = len(checks)
    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="witnessd")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="initialize witnessd config and pinned Depone")
    init.add_argument("--home", default=None)
    init.add_argument("--repo", default=".")
    init.add_argument("--depone-root", default=None)
    init.add_argument("--depone-repository", default=None)
    init.add_argument("--depone-ref", default=None)
    init.add_argument("--team", default=None)
    init.add_argument(
        "--allow-network",
        action="store_true",
        help="allow setup-time network provisioning when no local Depone root is supplied",
    )
    init.set_defaults(func=_cli_handler("bootstrap", "_cmd_init"))

    orro_setup = sub.add_parser(
        "orro-setup",
        help=argparse.SUPPRESS,
    )
    orro_setup.add_argument("--home", default=".witnessd")
    orro_setup.add_argument("--depone-root", default=None)
    orro_setup.add_argument("--depone-repository", default=None)
    orro_setup.add_argument("--depone-ref", default=None)
    orro_setup.add_argument("--json", action="store_true")
    orro_setup.add_argument(
        "--yes",
        action="store_true",
        help="acknowledge setup-time provisioning without prompting",
    )
    orro_setup.set_defaults(func=_cli_handler("bootstrap", "_cmd_orro_setup"))

    scout = sub.add_parser("scout", help="run read-only ORRO repo scout")
    scout.add_argument("goal")
    scout.add_argument("--repo", default=".")
    scout.add_argument("--home", default=None)
    scout.add_argument("--out-dir", default=None)
    scout.set_defaults(func=_cli_handler("bootstrap", "_cmd_scout"))

    run = sub.add_parser("run", help="observe a lane and emit signed evidence")
    _add_run_args(run)
    run.set_defaults(func=_cli_handler("run", "_cmd_run"))

    proofrun = sub.add_parser(
        "proofrun",
        help="ORRO evidence-backed execution alias; emits evidence without final trust",
        description=(
            "ORRO evidence-backed execution alias. A direct shell invocation "
            "(`--adapter shell -- <command>`) is capture-only and is not "
            "proofcheckable by itself. For a proofcheckable packet, run `orro "
            "scout` first and continue through `flowplan -> proofrun -> "
            "proofcheck` in the same run directory, or use `orro team go`."
        ),
    )
    _add_run_args(proofrun)
    proofrun.add_argument(
        "--allow-reference-adapter",
        action="store_true",
        help=(
            "allow deterministic W18 fallback or role-lane shell reference/placeholder "
            "proofrun lanes for intentional script/test use; marked as not real AI work"
        ),
    )
    proofrun.set_defaults(func=_cli_handler("run", "_cmd_run"))

    a2 = sub.add_parser(
        "a2-observer-run",
        help=(
            "run one observer-launched shell lane; A2 independence additionally "
            "requires an external operator key and real observer/runner separation"
        ),
    )
    a2.add_argument("--runner-sandbox", required=True)
    a2.add_argument("--out", required=True, help="observer evidence directory")
    a2.add_argument("--observer-dir", required=True)
    a2.add_argument("--keys-dir", default=None)
    a2.add_argument("--runner-user", default="ubuntu")
    a2.add_argument("--runner-uid", type=int, default=None)
    a2.add_argument("--task-id", default="w12-real-a2")
    a2.add_argument("--test-command", default=None)
    a2.add_argument("--allow", action="append", default=[])
    a2.add_argument("command", nargs=argparse.REMAINDER)
    a2.set_defaults(func=_cli_handler("team_ops", "_cmd_a2_observer_run"))

    plan = sub.add_parser(
        "plan",
        help="compatibility name for flowplan; emits a sealed plan without execution",
    )
    _add_plan_args(plan)
    plan.set_defaults(func=_cmd_plan)

    flowplan = sub.add_parser(
        "flowplan",
        help="ORRO plan-only workflow design; emits a sealed plan without execution",
    )
    _add_flowplan_args(flowplan)
    flowplan.set_defaults(func=_cmd_plan)

    status = sub.add_parser("status", help="render evidence-pending status")
    status.add_argument("--evidence-dir", default=".")
    status.add_argument("--runlog", default=None)
    status.set_defaults(func=_cli_handler("runtime_ops", "_cmd_status"))

    verify = sub.add_parser("verify", help="verify a run directory or runlog integrity")
    verify.add_argument("run_dir", nargs="?")
    verify.add_argument("--home", default=None)
    verify.add_argument("--runlog", default=None)
    verify.set_defaults(func=_cli_handler("runtime_ops", "_cmd_verify"))

    proofcheck = sub.add_parser(
        "proofcheck",
        help="ORRO offline proof verification wrapper delegated to Depone",
    )
    proofcheck.add_argument("evidence_dir", nargs="?")
    proofcheck.add_argument("--evidence-dir", dest="evidence_dir_option", default=None)
    proofcheck.add_argument("--home", default=None)
    proofcheck.add_argument("--out", default=None)
    proofcheck.add_argument("--json", action="store_true")
    proofcheck.set_defaults(func=_cmd_proofcheck)

    advisory_provenance_check = sub.add_parser(
        "advisory-provenance-check",
        help="offline Depone v110 check for sealed advisory provenance only",
    )
    advisory_provenance_check.add_argument("evidence_dir")
    advisory_provenance_check.add_argument("--home", required=True)
    advisory_provenance_check.add_argument("--json", action="store_true")
    advisory_provenance_check.set_defaults(func=_cmd_advisory_provenance_check)

    handoff = sub.add_parser(
        "handoff",
        help="package ORRO evidence hashes and verifier decision references",
    )
    handoff.add_argument("evidence_dir", nargs="?")
    handoff.add_argument("--evidence-dir", dest="evidence_dir_option", default=None)
    handoff.add_argument("--home", default=None)
    handoff.add_argument("--out", default=None)
    handoff.add_argument("--json", action="store_true")
    handoff.set_defaults(func=_cmd_handoff)

    route = sub.add_parser("route", help="dry-run W4 model routing")
    route.add_argument("--root", default=".")
    route.add_argument("--runlog", default=None)
    route.add_argument("--task-id", default="witnessd-route")
    route.add_argument(
        "--tier", required=True, choices=["quick", "agentic", "frontier"]
    )
    route.add_argument("--unsupported-model", action="append", default=[])
    route.set_defaults(func=_cli_handler("bootstrap", "_cmd_route"))

    doctor = sub.add_parser("doctor", help="report runlog-derived lane health")
    doctor.add_argument("--runlog", default=None)
    doctor.add_argument("--root", default=".")
    doctor.add_argument("--external-worktree", action="append", default=[])
    doctor.set_defaults(func=_cli_handler("runtime_ops", "_cmd_doctor"))

    orro_doctor = sub.add_parser("orro-doctor", help=argparse.SUPPRESS)
    orro_doctor.add_argument("--home", default=None)
    orro_doctor.add_argument(
        "--adapter",
        action="append",
        default=None,
        choices=["codex", "claude", "agy", "gemini", "opencode"],
    )
    orro_doctor.add_argument("--json", action="store_true")
    orro_doctor.add_argument("--engine-lock", default=None)
    orro_doctor.set_defaults(func=_cmd_orro_doctor)

    engine_lock = sub.add_parser(
        "engine-lock",
        help="write/check ORRO distribution metadata for pinned engine commits",
    )
    engine_lock.add_argument("--home", default=None)
    engine_lock.add_argument("--out", default=None)
    engine_lock.add_argument("--check", default=None)
    engine_lock.add_argument("--json", action="store_true")
    engine_lock.set_defaults(func=_cmd_orro_engine_lock)

    orro_next = sub.add_parser("orro-next", help=argparse.SUPPRESS)
    orro_next.add_argument("run_dir", nargs="?")
    orro_next.add_argument("--home", default=None)
    orro_next.add_argument("--out", default=None)
    orro_next.add_argument("--json", action="store_true")
    orro_next.set_defaults(func=_cli_handler("advisory", "_cmd_orro_next"))

    orro_advise = sub.add_parser("orro-advise", help=argparse.SUPPRESS)
    orro_advise.add_argument("goal", nargs="?")
    orro_advise.add_argument("--repo", default=".")
    orro_advise.add_argument("--home", default=None)
    orro_advise.add_argument("--out", default=None)
    orro_advise.add_argument("--json", action="store_true")
    orro_advise.set_defaults(func=_cli_handler("advisory", "_cmd_orro_advise"))

    orro_sketch = sub.add_parser(
        "orro-sketch",
        help=argparse.SUPPRESS,
        description=(
            "Validate and seal an agent-authored advisory sketch decision. Without "
            "--decision, emits a degraded heuristic scaffold. Not proof or assurance."
        ),
    )
    orro_sketch.add_argument("goal", nargs="?")
    orro_sketch.add_argument("--repo", default=".")
    orro_sketch.add_argument("--home", default=None)
    orro_sketch.add_argument("--decision", default=None)
    orro_sketch.add_argument("--out", default=None)
    orro_sketch.add_argument("--json", action="store_true")
    orro_sketch.set_defaults(func=_cli_handler("advisory", "_cmd_orro_sketch"))

    orro_trace = sub.add_parser(
        "orro-trace",
        help=argparse.SUPPRESS,
        description=(
            "Validate, gate, and seal an agent-authored root cause decision. Without "
            "--decision, emits a degraded heuristic scaffold. Not proof or assurance."
        ),
    )
    orro_trace.add_argument("goal", nargs="?")
    orro_trace.add_argument("--repo", default=".")
    orro_trace.add_argument("--home", default=None)
    orro_trace.add_argument("--decision", default=None)
    orro_trace.add_argument("--out", default=None)
    orro_trace.add_argument("--json", action="store_true")
    orro_trace.set_defaults(func=_cli_handler("advisory", "_cmd_orro_trace"))

    orro_report = sub.add_parser("orro-report", help=argparse.SUPPRESS)
    orro_report.add_argument("run_dir", nargs="?")
    orro_report.add_argument("--home", default=None)
    orro_report.add_argument("--out", default=None)
    orro_report.add_argument("--workstyle-decision", default=None)
    orro_report.add_argument("--json", action="store_true")
    orro_report.set_defaults(func=_cli_handler("advisory", "_cmd_orro_report"))

    orro_review = sub.add_parser(
        "orro-review",
        help=argparse.SUPPRESS,
        description=(
            "Run review-only role lanes through advisory read-only adapters. "
            "Not proofrun, not proofcheck, and not assurance."
        ),
    )
    orro_review.add_argument("--repo", required=True)
    orro_review.add_argument("--home", default=None)
    orro_review.add_argument("--run-dir", default=None)
    orro_review.add_argument("--role-lane-plan", required=True)
    orro_review.add_argument("--claude-binary", default="claude")
    orro_review.add_argument("--agy-binary", default="agy")
    orro_review.add_argument("--gemini-binary", default="gemini")
    orro_review.add_argument("--timeout-seconds", type=int, default=120)
    orro_review.add_argument("--json", action="store_true")
    orro_review.set_defaults(func=_cli_handler("advisory", "_cmd_orro_review"))

    orro_auto = sub.add_parser("orro-auto", help=argparse.SUPPRESS)
    orro_auto.add_argument("run_dir", nargs="?")
    orro_auto.add_argument("--dry-run", action="store_true")
    orro_auto.add_argument("--once", action="store_true")
    orro_auto.add_argument("--until-complete", action="store_true")
    orro_auto.add_argument("--max-steps", type=int, default=None)
    orro_auto.add_argument("--home", default=None)
    orro_auto.add_argument("--out", default=None)
    orro_auto.add_argument("--json", action="store_true")
    orro_auto.set_defaults(func=_cli_handler("advisory", "_cmd_orro_auto"))

    orro_flow = sub.add_parser(
        "orro-flow",
        help=argparse.SUPPRESS,
        description=(
            "Guided init -> scout -> flowplan -> proofrun -> proofcheck orchestration. "
            "Existing gates remain authoritative and are surfaced as blockers."
        ),
    )
    orro_flow.add_argument("goal")
    orro_flow.add_argument("--write-scope", action="append", default=[])
    orro_flow.add_argument(
        "--adapter",
        default=None,
        choices=["shell", "codex", "claude", "agy", "gemini", "opencode"],
    )
    orro_flow.add_argument("--home", default=None)
    orro_flow.add_argument("--runner-sandbox", default=None)
    orro_flow.add_argument("--rolepack-file", default=None)
    orro_flow.add_argument(
        "--role-lane-tier",
        default="quick",
        choices=["quick", "agentic", "frontier"],
    )
    orro_flow.add_argument("--run-dir", default=None)
    orro_flow.add_argument("--allow-reference-adapter", action="store_true")
    orro_flow.add_argument("--json", action="store_true")
    orro_flow.add_argument("--verification-only", action="store_true")
    orro_flow.set_defaults(func=_cli_handler("flow", "_cmd_orro_flow"))

    isolation = sub.add_parser("isolation", help="isolation contract checks")
    isolation.add_argument("--self-test", action="store_true")
    isolation.set_defaults(func=_cli_handler("runtime_ops", "_cmd_isolation"))

    faultkit = sub.add_parser("faultkit", help="deterministic fault injection")
    faultkit_sub = faultkit.add_subparsers(dest="fault", required=True)
    zombie = faultkit_sub.add_parser("zombie-hang")
    zombie.add_argument("--runlog", required=True)
    zombie.set_defaults(func=_cli_handler("runtime_ops", "_cmd_faultkit"))
    crash = faultkit_sub.add_parser("crash-mid-toolcall")
    crash.add_argument("--runlog-before", required=True)
    crash.add_argument("--runlog-after", required=True)
    crash.add_argument("--session", required=True)
    crash.set_defaults(func=_cli_handler("runtime_ops", "_cmd_faultkit"))
    pause_race = faultkit_sub.add_parser("pause-race")
    pause_race.add_argument("--runlog", required=True)
    pause_race.add_argument("--run-id", default="faultkit-pause-run")
    pause_race.set_defaults(func=_cli_handler("runtime_ops", "_cmd_faultkit"))
    budget = faultkit_sub.add_parser("budget-blowout")
    budget.add_argument("--root", required=True)
    budget.add_argument("--runner-sandbox", required=True)
    budget.add_argument("--codex-binary", default="codex")
    budget.add_argument("--task-id", default="budget-blowout")
    budget.add_argument("--prompt", default="trigger budget blowout")
    budget.add_argument("--max-tokens", type=int, default=1)
    budget.add_argument("--max-usd", type=float, default=10**9)
    budget.add_argument("--max-depth", type=int, default=3)
    budget.set_defaults(func=_cli_handler("runtime_ops", "_cmd_faultkit"))

    team = sub.add_parser("team", help="run a local team fan-in")
    team_sub = team.add_subparsers(dest="team_cmd", required=True)
    team_init = team_sub.add_parser(
        "init",
        help="scaffold .orro/team.json readiness configuration",
        description=(
            "Scaffold an ORRO rolepack. This writes readiness configuration only; "
            "it does not execute, verify evidence, or raise assurance."
        ),
    )
    team_init.add_argument("--out", default=".orro/team.json")
    from witnessd.orro_team_surface import valid_team_templates

    team_templates = valid_team_templates()
    team_init.add_argument(
        "--template",
        default="developer",
        help=f"team rolepack template (valid: {', '.join(team_templates)})",
    )
    team_init.add_argument("--role", action="append", default=[])
    team_init.add_argument("--write-scope", action="append", default=[])
    team_init.add_argument("--tool-mcp", action="append", default=[])
    team_init.add_argument("--tool-allow", action="append", default=[])
    team_init.add_argument("--interactive", action="store_true")
    team_init.add_argument("--yes", action="store_true")
    team_init.add_argument("--json", action="store_true")
    team_init.set_defaults(func=_cli_handler("team_ops", "_cmd_team_init"))

    team_go = team_sub.add_parser(
        "go",
        help="run flowplan, proofrun, proofcheck, and report for a team rolepack",
    )
    team_go.add_argument("goal")
    team_go.add_argument("--task", default=None)
    team_go.add_argument("--repo", required=True)
    team_go.add_argument("--home", default=None)
    team_go.add_argument("--team", default=None)
    team_go.add_argument("--run-dir", default=None)
    team_go.add_argument(
        "--profile",
        choices=[
            "code-change",
            "docs-change",
            "review-only",
            "verification-only",
            "release-readiness",
        ],
        default=None,
    )
    team_go.add_argument(
        "--role-lane-tier",
        default="quick",
        choices=["quick", "agentic", "frontier"],
    )
    team_go.add_argument("--max-parallel", type=int, default=1)
    team_go.add_argument("--fail-fast", action="store_true")
    team_go.add_argument(
        "--allow-reference-adapter",
        action="store_true",
        help="allow shell reference/script proofrun lanes; flagged as not real AI work",
    )
    team_go.add_argument("--codex-binary", default="codex")
    team_go.add_argument("--claude-binary", default="claude")
    team_go.add_argument("--agy-binary", default="agy")
    team_go.add_argument("--gemini-binary", default="gemini")
    team_go.add_argument("--opencode-binary", default="opencode")
    team_go.add_argument("--json", action="store_true")
    team_go.set_defaults(func=_cli_handler("team_go", "_cmd_team_go"))

    team_run = team_sub.add_parser("run", help="emit team fan-in evidence")
    team_run.add_argument("--repo", required=True)
    team_run.add_argument("--out", required=True)
    team_run.add_argument("--keys-dir", default=None)
    team_run.add_argument("--state-root", default=None)
    team_run.add_argument("--codex-auth-source", default=None)
    team_run.add_argument("--max-parallel", type=int, default=None)
    team_run.add_argument("--fail-fast", action="store_true")
    team_run.add_argument("--lane-prompt-file", action="append", default=[])
    team_run.add_argument(
        "--merge-group",
        action="append",
        default=[],
        help="merge_lane:source_a,source_b:file[,file...] for explicit overlapped-source merge evidence",
    )
    team_run.add_argument(
        "--lane",
        action="append",
        required=True,
        help="lane_id:file[,file...] or lane_id:adapter=codex:tier=quick:region=file[,file...]:prompt=...",
    )
    team_run.set_defaults(func=_cli_handler("team_ops", "_cmd_team_run"))

    team_plan_run = team_sub.add_parser(
        "plan-run", help="plan a goal and run the resulting team lanes"
    )
    team_plan_run.add_argument("goal")
    team_plan_run.add_argument("--repo", required=True)
    team_plan_run.add_argument("--out", required=True)
    team_plan_run.add_argument("--keys-dir", default=None)
    team_plan_run.add_argument("--seed", default="w11")
    team_plan_run.add_argument(
        "--draft-adapter",
        choices=["heuristic", "codex", "claude", "opencode"],
        default="heuristic",
    )
    team_plan_run.add_argument(
        "--lane-adapter",
        choices=["shell", "codex", "claude", "agy", "gemini", "opencode"],
        default="shell",
    )
    team_plan_run.add_argument("--tier", default="agentic")
    team_plan_run.add_argument("--max-tokens", type=int, default=10**9)
    team_plan_run.add_argument("--max-usd", type=float, default=10**9)
    team_plan_run.add_argument("--max-depth", type=int, default=3)
    team_plan_run.add_argument(
        "--lane-timeout",
        type=int,
        default=DEFAULT_TEAM_PLAN_RUN_LANE_TIMEOUT_SECONDS,
        help="whole-lane adapter timeout in seconds (default: 900)",
    )
    team_plan_run.add_argument("--state-root", default=None)
    team_plan_run.add_argument("--codex-auth-source", default="~/.codex/auth.json")
    team_plan_run.add_argument("--codex-binary", default="codex")
    team_plan_run.add_argument("--claude-binary", default="claude")
    team_plan_run.add_argument("--agy-binary", default="agy")
    team_plan_run.add_argument("--gemini-binary", default="gemini")
    team_plan_run.add_argument("--opencode-binary", default="opencode")
    team_plan_run.add_argument("--max-parallel", type=int, default=None)
    team_plan_run.add_argument("--fail-fast", action="store_true")
    team_plan_run.set_defaults(func=_cli_handler("team_ops", "_cmd_team_plan_run"))

    team_ledger = sub.add_parser(
        "team-ledger", help="show team-ledger status pending Depone verification"
    )
    team_ledger.add_argument("--ledger", required=True)
    team_ledger.add_argument("--json", action="store_true")
    team_ledger.set_defaults(func=_cli_handler("team_ops", "_cmd_team_ledger"))

    team_resume_audit = team_sub.add_parser(
        "resume-audit", help="audit surviving team lane bytes without replay"
    )
    team_resume_audit.add_argument("--out", required=True)
    team_resume_audit.add_argument("--run-id", default="w15-resume-audit")
    team_resume_audit.add_argument("--json", action="store_true")
    team_resume_audit.set_defaults(
        func=_cli_handler("team_ops", "_cmd_team_resume_audit")
    )

    team_resume = team_sub.add_parser("resume", help="resume an interrupted team run")
    team_resume.add_argument("run_dir")
    team_resume.add_argument("--run-id", default="w3-team")
    team_resume.add_argument("--max-parallel", type=int, default=None)
    team_resume.add_argument("--fail-fast", action="store_true")
    team_resume.add_argument("--json", action="store_true")
    team_resume.set_defaults(func=_cli_handler("team_ops", "_cmd_team_resume"))

    team_kill = team_sub.add_parser("kill", help="kill all live team lanes")
    team_kill.add_argument("--runlog", default=None)
    team_kill.add_argument("--state-root", default=None)
    team_kill.add_argument("--run-id", default="team-kill")
    team_kill.add_argument("--all", action="store_true", default=True)
    team_kill.set_defaults(func=_cli_handler("team_ops", "_cmd_team_kill"))

    lane_exec = sub.add_parser("lane-exec", help=argparse.SUPPRESS)
    lane_exec.add_argument("--spec-json", required=True)
    lane_exec.add_argument("--result-json", required=True)
    lane_exec.set_defaults(func=_cli_handler("team_ops", "_cmd_lane_exec"))

    pause = sub.add_parser("pause", help="append a user pause event")
    pause.add_argument("run_id")
    pause.add_argument("--runlog", required=True)
    pause.set_defaults(func=_cli_handler("runtime_ops", "_cmd_pause"))

    resume = sub.add_parser("resume", help="append an explicit user resume event")
    resume.add_argument("run_id")
    resume.add_argument("--runlog", required=True)
    resume.add_argument("--confirm", action="store_true")
    resume.set_defaults(func=_cli_handler("runtime_ops", "_cmd_resume_pause"))

    kill = sub.add_parser("kill", help="kill all supervised children")
    kill.add_argument("--all", action="store_true")
    kill.add_argument("--runlog", required=True)
    kill.add_argument("--run-id", default="witnessd-kill")
    kill.set_defaults(func=_cli_handler("runtime_ops", "_cmd_kill"))

    learn = sub.add_parser("learn", help="learning delta commands")
    learn_sub = learn.add_subparsers(dest="learn_cmd", required=True)
    promote = learn_sub.add_parser("promote")
    promote.add_argument("--delta", required=True)
    promote.add_argument("--capture", action="append", required=True)
    promote.add_argument("--approval-log", action="append", default=[])
    promote.add_argument("--runlog", required=True)
    promote.add_argument("--run-id", default="witnessd-learning")
    promote.add_argument("--private-key", required=True)
    promote.add_argument("--public-key", required=True)
    promote.add_argument("--evidence-dir", required=True)
    promote.add_argument("--bundle-out", default=None)
    promote.set_defaults(func=_cli_handler("runtime_ops", "_cmd_learn"))

    for name, help_text in (
        ("install", "atomically install witnessd payload"),
        ("upgrade", "atomically upgrade witnessd payload"),
    ):
        install = sub.add_parser(name, help=help_text)
        install.add_argument("--payload", required=True)
        install.add_argument("--dest", required=True)
        install.add_argument("--config", required=True)
        install.add_argument("--shim-dir", required=True)
        install.add_argument("--version", required=True)
        install.add_argument("--root", default=".")
        install.add_argument("--runlog", default=None)
        install.set_defaults(func=_cli_handler("runtime_ops", "_cmd_install"))

    self_test = sub.add_parser("self-test", help="run module self-tests")
    self_test.add_argument("--all", action="store_true")
    self_test.set_defaults(func=_cmd_self_test)

    pilot = sub.add_parser("pilot", help="external-team pilot tooling")
    pilot_sub = pilot.add_subparsers(dest="pilot_cmd", required=True)

    pilot_init = pilot_sub.add_parser("init", help="create a pilot deployment record")
    pilot_init.add_argument("--operator", required=True)
    pilot_init.add_argument("--team-scope", required=True)
    pilot_init.add_argument("--out", required=True)
    pilot_init.add_argument("--deployed-runtime", action="store_true")
    pilot_init.add_argument("--not-dogfood", action="store_true")
    pilot_init.add_argument("--not-ci", action="store_true")
    pilot_init.add_argument(
        "--deployment-root",
        default=None,
        help="repo path of the deployed runtime whose git SHA is recorded (default: this tree)",
    )
    pilot_init.set_defaults(func=_cli_handler("pilot", "_cmd_pilot_init"))

    pilot_close = pilot_sub.add_parser("close", help="close a pilot deployment record")
    pilot_close.add_argument("--record", required=True)
    pilot_close.set_defaults(func=_cli_handler("pilot", "_cmd_pilot_close"))

    pilot_rotation = pilot_sub.add_parser(
        "rotation-record", help="create an operator key rotation record"
    )
    pilot_rotation.add_argument("--archive", required=True)
    pilot_rotation.add_argument("--out", required=True)
    pilot_rotation.add_argument("--retired-key-id", default="witnessd-operator")
    pilot_rotation.set_defaults(func=_cli_handler("pilot", "_cmd_pilot_rotation_record"))

    pilot_canary = pilot_sub.add_parser(
        "canary", help="emit a signed operator key-rotation canary bundle"
    )
    pilot_canary.add_argument("--keys-dir", required=True)
    pilot_canary.add_argument("--out", required=True)
    pilot_canary.set_defaults(func=_cli_handler("pilot", "_cmd_pilot_canary"))

    pilot_archive = pilot_sub.add_parser(
        "archive-evidence", help="record pilot evidence paths and hashes"
    )
    pilot_archive.add_argument("--archive", required=True)
    pilot_archive.add_argument("--out", default=None)
    pilot_archive.add_argument("--artifact", action="append", required=True)
    pilot_archive.set_defaults(func=_cli_handler("pilot", "_cmd_pilot_archive_evidence"))

    return parser


def _add_plan_args(plan: argparse.ArgumentParser) -> None:
    plan.add_argument("goal")
    plan.add_argument("--root", default=".")
    plan.add_argument("--seed", default="w11")
    plan.add_argument(
        "--draft-adapter", choices=["codex", "claude", "agy", "gemini", "opencode"]
    )
    plan.add_argument("--draft-out", default=None)
    plan.add_argument(
        "--tier", default="agentic", choices=["quick", "agentic", "frontier"]
    )
    plan.add_argument("--codex-binary", default="codex")
    plan.add_argument("--claude-binary", default="claude")
    plan.add_argument("--agy-binary", default="agy")
    plan.add_argument("--gemini-binary", default="gemini")
    plan.add_argument("--opencode-binary", default="opencode")
    plan.add_argument("--max-tokens", type=int, default=10**9)
    plan.add_argument("--max-usd", type=float, default=10**9)
    plan.add_argument("--max-depth", type=int, default=3)
    plan.add_argument("--predicted-tokens", type=int, default=0)
    plan.add_argument("--predicted-usd", type=float, default=0.0)


def _add_run_args(run: argparse.ArgumentParser) -> None:
    run.add_argument("--goal", default=None, help=argparse.SUPPRESS)
    run.add_argument("--task", default=None, help=argparse.SUPPRESS)
    run.add_argument("--repo", default=None)
    run.add_argument("--home", default=None)
    run.add_argument("--run-dir", default=None)
    run.add_argument("--workflow-plan", default=None)
    run.add_argument("--role-lane-plan", default=None)
    run.add_argument("--json", action="store_true")
    run.add_argument("--max-parallel", type=int, default=None)
    run.add_argument("--fail-fast", action="store_true")
    run.add_argument(
        "--adapter",
        default="shell",
        choices=["shell", "codex", "claude", "agy", "gemini", "opencode"],
    )
    run.add_argument("--root", default=".")
    run.add_argument("--runner-sandbox", default=None)
    run.add_argument(
        "--out", default=None, help="observer output path (outside sandbox)"
    )
    run.add_argument("--log", default=None, help="observer log path (outside sandbox)")
    run.add_argument("--keys-dir", default=None)
    run.add_argument("--task-id", default="witnessd-lane")
    run.add_argument("--arm", default="direct", choices=["direct", "governed"])
    run.add_argument(
        "--tier", default="agentic", choices=["quick", "agentic", "frontier"]
    )
    run.add_argument("--codex-binary", default="codex")
    run.add_argument("--claude-binary", default="claude")
    run.add_argument("--agy-binary", default="agy")
    run.add_argument("--gemini-binary", default="gemini")
    run.add_argument("--opencode-binary", default="opencode")
    run.add_argument("--max-tokens", type=int, default=10**9)
    run.add_argument("--max-usd", type=float, default=10**9)
    run.add_argument("--max-depth", type=int, default=3)
    run.add_argument("--predicted-tokens", type=int, default=0)
    run.add_argument("--predicted-usd", type=float, default=0.0)
    run.add_argument(
        "--allow", action="append", default=[], help="allowed touched file"
    )
    run.add_argument(
        "--capture-profile",
        choices=["full", "redacted"],
        default="redacted",
    )
    run.add_argument("command", nargs=argparse.REMAINDER)


def _add_flowplan_args(flowplan: argparse.ArgumentParser) -> None:
    flowplan.add_argument("goal")
    flowplan.add_argument("--root", default=".")
    flowplan.add_argument("--seed", default="w11")
    flowplan.add_argument("--profile", default=None)
    flowplan.add_argument(
        "--lane-intent",
        choices=["implementation", "verification-only"],
        default=None,
    )
    flowplan.add_argument(
        "--check",
        action="append",
        default=None,
        help="declared verification check command for verification-only role "
        "lanes (repeatable; requires --role-lanes-out)",
    )
    flowplan.add_argument("--out", default=None)
    flowplan.add_argument("--role-lanes-out", default=None)
    flowplan.add_argument(
        "--lane-adapter",
        default="shell",
        choices=["shell", "codex", "claude", "agy", "gemini", "opencode"],
    )
    flowplan.add_argument(
        "--role-lane-tier",
        default="quick",
        choices=["quick", "agentic", "frontier"],
        help="tier stamped on each compiled role lane (also the model-policy lookup key)",
    )
    flowplan.add_argument(
        "--model-policy",
        default="off",
        choices=["off", "default"],
        help="off (default) keeps --lane-adapter uniform across lanes; "
        "default resolves each lane's (role, tier) to a policy adapter/model",
    )
    flowplan.add_argument(
        "--rolepack",
        default=None,
        help="named rolepack to apply when compiling --role-lanes-out",
    )
    flowplan.add_argument(
        "--rolepack-file",
        default=None,
        help="JSON rolepack file to apply when compiling --role-lanes-out",
    )
    flowplan.add_argument(
        "--team",
        default=None,
        help="onboarding rolepack JSON file (usually .orro/team.json)",
    )
    flowplan.add_argument("--json", action="store_true")
    flowplan.set_defaults(
        draft_adapter=None,
        draft_out=None,
        tier="agentic",
        codex_binary="codex",
        claude_binary="claude",
        agy_binary="agy",
        gemini_binary="gemini",
        opencode_binary="opencode",
        max_tokens=10**9,
        max_usd=10**9,
        max_depth=3,
        predicted_tokens=0,
        predicted_usd=0.0,
    )


def main(argv: list[str] | None = None) -> int:
    argv = _normalize_run_goal_argv(
        _normalize_orro_argv(
            _normalize_superflow_argv(list(sys.argv[1:] if argv is None else argv))
        )
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    # argparse.REMAINDER keeps a leading "--"; drop it so command is the argv.
    if getattr(args, "command", None) and args.command[0] == "--":
        args.command = args.command[1:]
    return args.func(args)


def _normalize_run_goal_argv(argv: list[str]) -> list[str]:
    if (
        len(argv) < 2
        or argv[0] not in {"run", "proofrun"}
        or "--" in argv
        or "--goal" in argv
    ):
        return argv
    first = argv[1]
    if first.startswith("-"):
        return argv
    return [argv[0], "--goal", first, *argv[2:]]


def _normalize_superflow_argv(argv: list[str]) -> list[str]:
    if not argv or argv[0] != "superflow":
        return argv
    if len(argv) >= 2 and argv[1] == "scout":
        return ["scout", *argv[2:]]
    return argv


ORRO_COMMAND_MAP: dict[str, str] = {
    "setup": "orro-setup",
    "init": "init",
    "scout": "scout",
    "flowplan": "flowplan",
    "proofrun": "proofrun",
    "proofcheck": "proofcheck",
    "advisory-provenance-check": "advisory-provenance-check",
    "handoff": "handoff",
    "doctor": "orro-doctor",
    "engine-lock": "engine-lock",
    "lock": "engine-lock",
    "next": "orro-next",
    "advise": "orro-advise",
    "sketch": "orro-sketch",
    "trace": "orro-trace",
    "report": "orro-report",
    "review": "orro-review",
    "auto": "orro-auto",
    "flow": "orro-flow",
    "team": "team",
}
ORRO_COMMANDS: frozenset[str] = frozenset(ORRO_COMMAND_MAP)


def _normalize_orro_argv(argv: list[str]) -> list[str]:
    if not argv or argv[0] != "orro":
        return argv
    if len(argv) >= 2 and argv[1] in ORRO_COMMAND_MAP:
        return [ORRO_COMMAND_MAP[argv[1]], *argv[2:]]
    return argv


if __name__ == "__main__":
    sys.exit(main())
