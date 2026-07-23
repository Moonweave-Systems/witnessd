from __future__ import annotations

import argparse
import json
from pathlib import Path

from witnessd.cli._output import _emit_orro_error
from witnessd.orro_roadmap import OrroRoadmapError
from witnessd.orro_task import OrroTaskError, begin_task


TASK_BOUNDARY_HELP = (
    "Task worktrees, branches, and commits are workspace state, not proof; "
    "task begin output is setup metadata only; merge approval and execution stay "
    "human; panes/agent/session state are never sealed into evidence."
)


def _cmd_orro_task(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve(strict=False)
    try:
        payload = begin_task(repo=repo, item_id=args.item_id, base=args.base, no_open=args.no_open)
    except (OrroRoadmapError, OrroTaskError) as exc:
        _emit_orro_error(args, code=exc.code, message=str(exc))
        return 2
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"ORRO task begin: {payload['item_id']} ({payload['state']})")
        print(f"  worktree: {payload['worktree']}")
        print(f"  branch: {payload['branch']}")
        print(f"  base commit: {payload['base_commit']}")
        print(f"  descriptor: {payload['descriptor']}")
        if payload.get("message"):
            print(f"  {payload['message']}")
        if "open_hook_exit_code" in payload:
            print(f"  open hook exit code: {payload['open_hook_exit_code']}")
        print(f"  Boundary: {payload['boundary']}")
    return int(payload.get("open_hook_exit_code", 0))
