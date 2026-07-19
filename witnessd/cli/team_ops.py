from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from witnessd.cli._output import _emit_orro_error
from witnessd.cli.team_go import (
    _apply_lane_prompt_files,
    _fill_interactive_team_init_args,
)
from witnessd.status import render_status
from witnessd.trust_anchor import TrustAnchor


def _cmd_team_run(args: argparse.Namespace) -> int:
    from witnessd.fanin import run_team
    from witnessd.signing import gen_operator_keypair

    out_dir_path = Path(args.out).resolve()
    out_dir = str(out_dir_path)
    lane_specs = [_parse_team_lane(text) for text in args.lane]
    try:
        merge_groups = [_parse_team_merge_group(text) for text in args.merge_group]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    from witnessd.cli.team_go import _apply_lane_prompt_files

    try:
        _apply_lane_prompt_files(lane_specs, args.lane_prompt_file)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    state_root = _team_run_state_root(args, out_dir_path)
    if state_root is not None and _paths_overlap(Path(state_root), out_dir_path):
        _emit_orro_error(
            args,
            code="ERR_TEAM_RUN_STATE_ROOT_INSIDE_OUTPUT",
            message="team runner state cannot overlap observer output",
            reason="runner state inside --out would break observer/runner separation",
            required_input_or_grant="--state-root <dir> outside --out",
            next_command=(
                "python3 -m witnessd team run "
                f"--repo {shlex.quote(str(args.repo))} "
                f"--out {shlex.quote(str(args.out))} "
                "--state-root <separate-dir> --lane <lane-spec>"
            ),
        )
        return 2
    codex_specs = [spec for spec in lane_specs if spec.get("adapter") == "codex"]
    if (
        len(codex_specs) > 1
        and state_root is None
        and not _codex_specs_are_isolated(codex_specs)
    ):
        _emit_orro_error(
            args,
            code="ERR_TEAM_RUN_MULTI_CODEX_UNISOLATED",
            message="multiple Codex lanes require isolated state roots",
            reason="the requested Codex lanes would otherwise share mutable runner state",
            required_input_or_grant=(
                "--state-root <dir> or a distinct state_root in every Codex lane"
            ),
            next_command=(
                "python3 -m witnessd team run "
                f"--repo {shlex.quote(str(args.repo))} "
                f"--out {shlex.quote(str(args.out))} "
                "--state-root <isolated-state-dir> --lane <lane-spec>"
            ),
        )
        return 2
    if state_root is not None and codex_specs:
        if len(codex_specs) > 1:
            for spec in codex_specs:
                lane_state_root = _team_run_lane_state_root(
                    Path(state_root), str(spec["lane_id"])
                )
                spec["state_root"] = str(lane_state_root)
                _seed_codex_auth(lane_state_root, args.codex_auth_source)
        else:
            _seed_codex_auth(Path(state_root), args.codex_auth_source)
    elif len(codex_specs) > 1:
        for spec in codex_specs:
            _seed_codex_auth(Path(str(spec["state_root"])), args.codex_auth_source)

    keys_dir = os.path.abspath(args.keys_dir or (out_dir.rstrip(os.sep) + "-keys"))
    os.makedirs(keys_dir, exist_ok=True)
    private_key_path, public_key_path = gen_operator_keypair(keys_dir)
    result = run_team(
        lane_specs,
        repo_root=args.repo,
        out_dir=out_dir,
        private_key_path=private_key_path,
        public_key_path=public_key_path,
        state_root=state_root,
        max_parallel=args.max_parallel,
        fail_fast=args.fail_fast,
        merge_groups=merge_groups,
    )
    pending = len(result["ledger"]["lanes"])
    print(
        f"{pending} team lane(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    print(f"team_ledger: {os.path.join(out_dir, 'team-ledger.json')}")
    return 0


def _cmd_team_init(args: argparse.Namespace) -> int:
    from witnessd.orro_team_surface import (
        OrroTeamSurfaceError,
        build_rolepack_scaffold,
        valid_team_templates,
        write_rolepack_scaffold,
    )

    try:
        if args.interactive:
            if not sys.stdin.isatty():
                print("ERR_ORRO_TEAM_INIT_INTERACTIVE_REQUIRES_TTY", file=sys.stderr)
                return 2
            from witnessd.cli.team_go import _fill_interactive_team_init_args

            _fill_interactive_team_init_args(args)
        rolepack = build_rolepack_scaffold(
            template=args.template,
            roles=args.role,
            write_scope=args.write_scope if args.write_scope else None,
            tool_mcp=args.tool_mcp,
            tool_allow=args.tool_allow,
        )
        result = write_rolepack_scaffold(
            Path(args.out).resolve(strict=False),
            rolepack,
            yes=args.yes,
        )
    except OrroTeamSurfaceError as exc:
        if exc.code == "ERR_ORRO_TEAM_TEMPLATE_UNKNOWN":
            valid_templates = list(valid_team_templates())
            required = "--template " + "|".join(valid_templates)
            _emit_orro_error(
                args,
                code=exc.code,
                message=exc.message,
                reason="the requested team template is not registered",
                required_input_or_grant=required,
                next_command=(
                    "python3 -m orro team init "
                    f"--template {shlex.quote(valid_templates[0])} --yes"
                ),
                extra={"valid_templates": valid_templates},
            )
        else:
            print(exc.code, file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"ERR_ORRO_TEAM_INIT_INVALID: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def _team_run_state_root(args: argparse.Namespace, out_dir: Path) -> str | None:
    if args.state_root is not None:
        return str(Path(args.state_root).resolve(strict=False))
    if args.codex_auth_source:
        return str(
            Path(str(out_dir).rstrip(os.sep) + "-w4-state-root").resolve(strict=False)
        )
    return None


def _team_run_lane_state_root(state_root: Path, lane_id: str) -> Path:
    slug = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-" for char in lane_id
    ).strip("-._")
    digest = hashlib.sha256(lane_id.encode("utf-8")).hexdigest()[:16]
    return state_root / f"{slug or 'lane'}-{digest}"


def _codex_specs_are_isolated(codex_specs: list[dict]) -> bool:
    roots: list[Path] = []
    for spec in codex_specs:
        state_root = spec.get("state_root")
        if not state_root:
            return False
        roots.append(Path(str(state_root)).resolve(strict=False))
    if len({str(root) for root in roots}) != len(roots):
        return False
    for left_index, left in enumerate(roots):
        for right in roots[left_index + 1 :]:
            if _paths_overlap(left, right):
                return False
    return True


def _parse_team_merge_group(text: str) -> dict:
    lane_id, sep, rest = text.partition(":")
    if sep != ":" or not lane_id.strip():
        raise ValueError("ERR_TEAM_MERGE_GROUP_FORMAT")
    sources_text, sep, files_text = rest.partition(":")
    if sep != ":":
        raise ValueError("ERR_TEAM_MERGE_GROUP_FORMAT")
    sources = [part.strip() for part in sources_text.split(",") if part.strip()]
    files = [part.strip() for part in files_text.split(",") if part.strip()]
    if len(sources) < 2 or not files:
        raise ValueError("ERR_TEAM_MERGE_GROUP_FORMAT")
    return {"lane_id": lane_id.strip(), "sources": sources, "files": files}


def _cmd_team_plan_run(args: argparse.Namespace) -> int:
    from witnessd.eventlog import EventLog
    from witnessd.fanin import run_team
    from witnessd.planner import dispatch, plan_heuristic, seal_plan
    from witnessd.signing import gen_operator_keypair

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.draft_adapter != "heuristic":
        _emit_orro_error(
            args,
            code="ERR_PLAN_RUN_DRAFT_ADAPTER_UNSUPPORTED",
            message="team plan-run supports only the deterministic heuristic drafter",
            reason="the selected draft adapter is not implemented for this command",
            required_input_or_grant="--draft-adapter heuristic",
            next_command=(
                "python3 -m witnessd team plan-run "
                f"{shlex.quote(str(args.goal))} "
                f"--repo {shlex.quote(str(args.repo))} "
                f"--out {shlex.quote(str(args.out))} --draft-adapter heuristic"
            ),
        )
        return 2
    if args.lane_timeout < 1 or args.lane_timeout > 3600:
        _emit_orro_error(
            args,
            code="ERR_PLAN_RUN_LANE_TIMEOUT_INVALID",
            message="team plan-run lane timeout is outside the supported range",
            reason="lane timeouts must be between 1 and 3600 seconds",
            required_input_or_grant="--lane-timeout <1..3600>",
            next_command=(
                "python3 -m witnessd team plan-run "
                f"{shlex.quote(str(args.goal))} "
                f"--repo {shlex.quote(str(args.repo))} "
                f"--out {shlex.quote(str(args.out))} --lane-timeout 900"
            ),
        )
        return 2

    state_root = _team_plan_state_root(args, out_dir)
    if state_root is not None and _paths_overlap(Path(state_root), out_dir):
        _emit_orro_error(
            args,
            code="ERR_PLAN_RUN_STATE_ROOT_INSIDE_OUTPUT",
            message="planned team runner state cannot overlap observer output",
            reason="runner state inside --out would break observer/runner separation",
            required_input_or_grant="--state-root <dir> outside --out",
            next_command=(
                "python3 -m witnessd team plan-run "
                f"{shlex.quote(str(args.goal))} "
                f"--repo {shlex.quote(str(args.repo))} "
                f"--out {shlex.quote(str(args.out))} "
                "--state-root <separate-dir>"
            ),
        )
        return 2

    budget = {
        "max_tokens": args.max_tokens,
        "max_usd": args.max_usd,
        "max_depth": args.max_depth,
    }
    sealed = seal_plan(
        plan_heuristic(
            args.goal,
            seed=args.seed,
            root=args.repo,
            adapter=args.lane_adapter,
            budget=budget,
            tier=args.tier,
        ),
        goal=args.goal,
    )
    sealed_path = out_dir / "sealed-plan.json"
    sealed_path.write_text(
        json.dumps(sealed, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    dispatch_log = EventLog(str(out_dir / "dispatch-log.jsonl"))
    for event in dispatch(sealed):
        dispatch_log.append(event)

    keys_dir = Path(args.keys_dir or (str(out_dir).rstrip(os.sep) + "-keys")).resolve()
    keys_dir.mkdir(parents=True, exist_ok=True)
    private_key_path, public_key_path = gen_operator_keypair(str(keys_dir))
    lane_specs = [
        _lane_packet_to_run_team_spec(packet, args) for packet in sealed["packets"]
    ]
    if args.lane_adapter == "codex" and state_root is not None:
        _seed_codex_auth(Path(state_root), args.codex_auth_source)
    result = run_team(
        lane_specs,
        repo_root=args.repo,
        out_dir=str(out_dir),
        private_key_path=private_key_path,
        public_key_path=public_key_path,
        leader_objective=args.goal,
        stop_rule="evidence-pending",
        state_root=state_root,
        max_parallel=args.max_parallel,
        fail_fast=args.fail_fast,
    )
    pending = len(result["ledger"]["lanes"])
    print(
        f"{pending} planned team lane(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    print(f"sealed_plan: {sealed_path}")
    print(f"dispatch_log: {out_dir / 'dispatch-log.jsonl'}")
    print(f"team_ledger: {out_dir / 'team-ledger.json'}")
    return 0


def _team_plan_state_root(args: argparse.Namespace, out_dir: Path) -> str | None:
    if args.state_root is not None:
        return str(Path(args.state_root).resolve(strict=False))
    if args.lane_adapter == "shell":
        return None
    return str(
        Path(str(out_dir).rstrip(os.sep) + "-w4-state-root").resolve(strict=False)
    )


def _is_inside_or_equal(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_inside_or_equal(left, right) or _is_inside_or_equal(right, left)


def _seed_codex_auth(state_root: Path, source: str | None) -> None:
    if not source:
        return
    source_path = Path(source).expanduser().resolve(strict=False)
    if not source_path.exists():
        raise RuntimeError(f"codex auth source does not exist: {source_path}")
    target = state_root.resolve(strict=False) / ".witnessd" / "codex-home" / "auth.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target)
    target.chmod(0o600)


def _cmd_a2_observer_run(args: argparse.Namespace) -> int:
    if not args.command:
        print("ERR_NO_COMMAND", file=sys.stderr)
        return 2

    from witnessd.a2 import run_observer_launched_shell_lane, uid_for_user
    from witnessd.signing import gen_operator_keypair

    evidence_dir = os.path.abspath(args.out)
    observer_dir = os.path.abspath(args.observer_dir)
    keys_dir = os.path.abspath(args.keys_dir or (evidence_dir.rstrip(os.sep) + "-keys"))
    os.makedirs(keys_dir, exist_ok=True)
    keypair_preexisted = all(
        os.path.isfile(os.path.join(keys_dir, name))
        for name in ("operator-ed25519.pem", "operator-ed25519.pub.pem")
    )
    private_key_path, public_key_path = gen_operator_keypair(keys_dir)
    from witnessd.trust_anchor import resolve_trust_anchor

    trust_anchor = resolve_trust_anchor(
        runtime_public_key=Path(public_key_path),
        runtime_generated=not keypair_preexisted,
    )
    runner_uid = args.runner_uid
    if runner_uid is None:
        runner_uid = uid_for_user(args.runner_user)

    result = run_observer_launched_shell_lane(
        sandbox=os.path.abspath(args.runner_sandbox),
        commands=[list(args.command)],
        evidence_dir=evidence_dir,
        private_key_path=private_key_path,
        public_key_path=public_key_path,
        observer_dir=observer_dir,
        runner_user=args.runner_user,
        runner_uid=runner_uid,
        allowed_touched_files=list(args.allow or []),
        task_id=args.task_id,
        test_command=["sh", "-c", args.test_command] if args.test_command else None,
    )

    pending = 1
    print(
        f"{pending} capture(s) pending Depone verification "
        f"({render_status(pending=pending, verdict=None)})"
    )
    print(f"evidence_dir: {evidence_dir}")
    _print_trust_anchor_summary(trust_anchor, candidate_assurance=result["assurance"])
    return 0


def _print_trust_anchor_summary(
    trust_anchor: TrustAnchor, *, candidate_assurance: str
) -> None:
    print(f"trust_anchor: {trust_anchor.trust_anchor}")
    print(f"operator public key: {trust_anchor.public_key_path}")
    if trust_anchor.independent:
        print(f"assurance (candidate, unverified): {candidate_assurance}")
        print(f"independent trust anchor: {trust_anchor.trust_anchor}")
    else:
        print("assurance claim: unavailable without an external operator key")
        print("independent trust anchor: false")


def _lane_packet_to_run_team_spec(
    packet: dict, args: argparse.Namespace | None = None
) -> dict:
    if packet["adapter"] == "shell":
        return {
            "lane_id": packet["lane_id"],
            "region": list(packet["region"]),
            "commands": [
                _default_team_lane_command(packet["lane_id"], packet["region"])
            ],
        }
    spec = {
        "lane_id": packet["lane_id"],
        "adapter": packet["adapter"],
        "tier": packet["tier"],
        "region": list(packet["region"]),
        "prompt": packet["prompt"],
        "budget": dict(packet["budget"]),
    }
    if args is not None:
        spec.update(
            {
                "codex_binary": args.codex_binary,
                "claude_binary": args.claude_binary,
                "opencode_binary": args.opencode_binary,
            }
        )
        if hasattr(args, "lane_timeout"):
            spec["timeout_seconds"] = args.lane_timeout
    return spec


def _cmd_team_ledger(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    pending = (
        len(ledger.get("lanes", [])) if isinstance(ledger.get("lanes"), list) else 0
    )
    status = render_status(pending=pending, verdict=None)
    result = {
        "decision": status,
        "pending": pending,
        "ledger": str(ledger_path),
        "message": f"{pending} team lane(s) pending Depone verification",
    }
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['message']} ({status})")
    return 0


def _cmd_lane_exec(args: argparse.Namespace) -> int:
    from witnessd.fanin import run_lane_exec_from_spec

    return run_lane_exec_from_spec(args.spec_json, args.result_json)


def _cmd_team_resume_audit(args: argparse.Namespace) -> int:
    from witnessd.fanin import resume_audit

    audit = resume_audit(args.out, run_id=args.run_id)
    if args.json:
        print(json.dumps(audit, sort_keys=True))
    else:
        print(
            f"team_resume_audit: {Path(args.out).resolve(strict=False) / 'team-resume-audit.json'}"
        )
    return 0


def _cmd_team_resume(args: argparse.Namespace) -> int:
    from witnessd.fanin import resume_team

    try:
        result = resume_team(
            args.run_dir,
            run_id=args.run_id,
            max_parallel=args.max_parallel,
            fail_fast=args.fail_fast,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {"ledger": str(result["base_dir"] / "team-ledger.json")}, sort_keys=True
            )
        )
    else:
        print(f"team_resume: {result['base_dir'] / 'team-ledger.json'}")
    return 0


def _cmd_team_kill(args: argparse.Namespace) -> int:
    runlog = args.runlog
    if runlog is None and args.state_root is not None:
        state_root = Path(args.state_root).resolve(strict=False)
        manifest_path = state_root / "team-run.json"
        if not manifest_path.is_file():
            print("ERR_TEAM_KILL_STATE_MANIFEST_MISSING", file=sys.stderr)
            return 2
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("kind") != "witnessd-team-run-state":
            print("ERR_TEAM_KILL_STATE_MANIFEST_INVALID", file=sys.stderr)
            return 2
        manifest_runlog = manifest.get("runlog")
        if not isinstance(manifest_runlog, str) or not manifest_runlog:
            print("ERR_TEAM_KILL_STATE_MANIFEST_INVALID", file=sys.stderr)
            return 2
        runlog = manifest_runlog
    if runlog is None:
        print("ERR_TEAM_KILL_RUNLOG_REQUIRED", file=sys.stderr)
        return 2
    args.runlog = runlog
    from witnessd.cli.runtime_ops import _cmd_kill
    return _cmd_kill(args)


def _parse_team_lane(text: str) -> dict:
    lane_id, sep, body = text.partition(":")
    lane_id = lane_id.strip()
    if not lane_id or sep != ":":
        raise ValueError("ERR_TEAM_LANE_FORMAT")

    parts = body.split(":")
    keyed = any(part.startswith("adapter=") for part in parts)
    if not keyed:
        region = [item.strip() for item in body.split(",") if item.strip()]
        return {
            "lane_id": lane_id,
            "region": region,
            "commands": [_default_team_lane_command(lane_id, region)],
        }

    fields: dict[str, str] = {}
    for part in parts:
        key, field_sep, value = part.partition("=")
        if field_sep != "=" or not key.strip():
            raise ValueError("ERR_TEAM_LANE_FORMAT")
        fields[key.strip()] = value.strip()

    from witnessd.adapters.base import RUNNER_KIND_BY_ADAPTER

    adapter = fields.get("adapter", "")
    valid_adapters = set(RUNNER_KIND_BY_ADAPTER) | {"shell"}
    if adapter not in valid_adapters:
        raise ValueError("ERR_TEAM_LANE_ADAPTER")
    prompt = fields.get("prompt", "")
    if not prompt:
        raise ValueError("ERR_TEAM_LANE_PROMPT")

    region = [
        item.strip() for item in fields.get("region", "").split(",") if item.strip()
    ]
    parsed = {
        "lane_id": lane_id,
        "adapter": adapter,
        "tier": fields.get("tier", "agentic"),
        "region": region,
        "allowed_touched_files": list(region),
        "prompt": prompt,
    }
    if "model" in fields:
        parsed["model"] = fields["model"]
    return parsed


def _default_team_lane_command(lane_id: str, region: list[str]) -> list[str]:
    statements: list[str] = []
    for path in region:
        parent = os.path.dirname(path)
        if parent:
            statements.append(f"mkdir -p {shlex.quote(parent)}")
        statements.append(
            f"printf '%s\\n' {shlex.quote(lane_id)} > {shlex.quote(path)}"
        )
    return ["sh", "-c", " && ".join(statements) if statements else "true"]
