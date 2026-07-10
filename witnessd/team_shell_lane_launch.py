"""Stdlib-only shell lane command adapter with argv allowlist receipts."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

from witnessd.canonical import canonical_hash

TEAM_SHELL_LANE_LAUNCH_KIND = "depone-team-shell-lane-launch"
TEAM_SHELL_LANE_LAUNCH_SCHEMA_VERSION = "0.1"
TEAM_SHELL_LANE_LAUNCH_DEPRECATION = {
    "status": "deprecated",
    "migration_target": "witnessd",
    "reason": "lane command execution belongs to the witnessd runtime boundary",
}
AGENT_OPERATING_CONTRACT_ID = "depone-agent-operating-contract.v0.1"
AGENT_OPERATING_CONTRACT_KIND = "depone-agent-operating-contract"
AGENT_OPERATING_CONTRACT_SCHEMA_VERSION = "0.1"
AGENT_OPERATING_CONTRACT_PATH = Path("packaging/depone-agent-operating-contract.json")
DWM_ROLES_PATH = Path("packaging/dwm-roles.json")
V22_WORKER_ROLE_ID = "worker"
V22_REQUIRED_ROLE_FIELDS = frozenset(
    {
        "id",
        "purpose",
        "allowed_tools",
        "output_schema",
        "evidence_obligations",
        "trust_boundary",
    }
)
ROLE_REGISTRY_PATH = DWM_ROLES_PATH
DEFAULT_AGENT_ROLE_ID = V22_WORKER_ROLE_ID
PROHIBITED_EXECUTABLES = frozenset({"codex", "claude", "claude-code", "opencode"})
SHELL_INTERPRETERS = frozenset(
    {"bash", "sh", "dash", "zsh", "ksh", "csh", "tcsh", "fish"}
)
# Split each argv token into candidate words so prohibited executables cannot hide
# inside interpreter (`bash -c "codex ..."`) or wrapper (`env codex`) payloads.
_ARGV_WORD_SPLIT = re.compile(r"""[\s;&|()<>{}\[\]'"=,]+""")


def _argv_words(argv: list[str]) -> Iterator[str]:
    for token in argv:
        for word in _ARGV_WORD_SPLIT.split(token):
            if word:
                yield word


def _scan_argv_for_prohibited_agent(argv: list[str]) -> str | None:
    for word in _argv_words(argv):
        name = Path(word).name.lower()
        if name in PROHIBITED_EXECUTABLES:
            return name
    return None


class TeamShellLaneLaunchError(Exception):
    """Structured shell lane launch failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def run_shell_lane_command(
    *,
    allowlist: dict[str, object],
    command_id: str,
    cwd: Path,
    transcript_path: Path,
    timeout_seconds: int = 120,
    agent_role_id: str = DEFAULT_AGENT_ROLE_ID,
    agent_contract_path: Path | None = None,
    role_registry_path: Path | None = None,
) -> dict[str, object]:
    """Run one allowlisted argv command and return a hash-bound receipt."""

    argv = _resolve_allowlisted_argv(allowlist, command_id)
    agent_contract = _resolve_agent_contract(
        agent_role_id=agent_role_id,
        contract_path=agent_contract_path or AGENT_OPERATING_CONTRACT_PATH,
        role_registry_path=role_registry_path or ROLE_REGISTRY_PATH,
    )
    _validate_timeout(timeout_seconds)
    resolved_cwd = _resolve_cwd(cwd)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    completed = subprocess.run(
        argv,
        cwd=resolved_cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
    )
    stdout = completed.stdout
    stderr = completed.stderr
    transcript = {
        "command_id": command_id,
        "cwd": resolved_cwd.as_posix(),
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout_text": stdout.decode("utf-8", errors="replace"),
        "stderr_text": stderr.decode("utf-8", errors="replace"),
    }
    transcript_path.write_text(
        json.dumps(transcript, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "kind": TEAM_SHELL_LANE_LAUNCH_KIND,
        "schema_version": TEAM_SHELL_LANE_LAUNCH_SCHEMA_VERSION,
        "decision": "pass" if completed.returncode == 0 else "failed",
        "command_id": command_id,
        "cwd": resolved_cwd.as_posix(),
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout_sha256": _sha256_bytes(stdout),
        "stderr_sha256": _sha256_bytes(stderr),
        "transcript_path": transcript_path.as_posix(),
        "transcript_sha256": _sha256_bytes(transcript_path.read_bytes()),
        "allowlist_sha256": _canonical_hash(allowlist),
        "agent_contract_hash": agent_contract["agent_contract_hash"],
        "agent_contract": agent_contract,
        "deprecation": dict(TEAM_SHELL_LANE_LAUNCH_DEPRECATION),
        "boundary": {
            "uses_shell": Path(argv[0]).name.lower() in SHELL_INTERPRETERS,
            "uses_argv_allowlist": True,
            "executes_commands": True,
            # False is an argv-scan fact: prohibited agents are blocked before execution.
            "launches_agents": False,
            "calls_live_models": False,
            "raises_assurance": False,
            "allows_arbitrary_shell_string": False,
        },
    }


def _repo_root() -> Path:
    source_root = Path(__file__).resolve().parents[1]
    candidates = [
        Path.cwd(),
        source_root,
        source_root.parent / "depone",
    ]
    for candidate in candidates:
        if (candidate / AGENT_OPERATING_CONTRACT_PATH).is_file() and (
            candidate / DWM_ROLES_PATH
        ).is_file():
            return candidate
    return source_root


def _read_json_object(path: Path, *, code: str, label: str) -> dict[str, object]:
    resolved = path if path.is_absolute() else _repo_root() / path
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TeamShellLaneLaunchError(code, str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise TeamShellLaneLaunchError(code, str(exc)) from exc
    if not isinstance(value, dict):
        raise TeamShellLaneLaunchError(code, f"{label} must be a JSON object")
    return value


def _require_string(value: object, *, code: str, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise TeamShellLaneLaunchError(code, message)
    return value


def _resolve_agent_contract(
    *,
    agent_role_id: str,
    contract_path: Path,
    role_registry_path: Path,
) -> dict[str, object]:
    role_id = _require_string(
        agent_role_id,
        code="ERR_TEAM_SHELL_LANE_AGENT_ROLE_INVALID",
        message="agent_role_id must be a non-empty V22 role id",
    )
    contract = _read_json_object(
        contract_path,
        code="ERR_TEAM_SHELL_LANE_AGENT_CONTRACT_INVALID",
        label="agent operating contract",
    )
    registry = _read_json_object(
        role_registry_path,
        code="ERR_TEAM_SHELL_LANE_ROLE_REGISTRY_INVALID",
        label="V22 role registry",
    )
    try:
        return _build_agent_contract_facts(contract, registry, role_id)
    except ValueError as exc:
        code = "ERR_TEAM_SHELL_LANE_AGENT_CONTRACT_INVALID"
        if "ERR_AGENT_CONTRACT_V22_ROLE_ID_" in str(exc):
            code = "ERR_TEAM_SHELL_LANE_AGENT_ROLE_INVALID"
        raise TeamShellLaneLaunchError(code, str(exc)) from exc


def _build_agent_contract_facts(
    contract: dict[str, object],
    role_registry: dict[str, object],
    role_id: str,
) -> dict[str, object]:
    errors = [
        *_validate_agent_operating_contract(contract, role_registry),
        *_validate_v22_role_id(role_registry, role_id),
    ]
    if errors:
        codes = ", ".join(error["code"] for error in errors)
        raise ValueError(f"agent contract facts invalid: {codes}")
    registry = contract["role_registry"]
    if not isinstance(registry, dict):
        raise ValueError("agent contract facts invalid: role_registry")
    binding = contract.get("v22_role_binding")
    if not isinstance(binding, dict) or binding.get("required_role_id") != role_id:
        raise ValueError(
            "agent contract facts invalid: ERR_AGENT_CONTRACT_V22_ROLE_ID_MISMATCH"
        )
    return {
        "agent_contract_id": contract["agent_contract_id"],
        "agent_contract_hash": contract["agent_contract_hash"],
        "role_id": role_id,
        "role_registry_path": registry["path"],
        "role_registry_sha256": registry["sha256"],
    }


def _validate_agent_operating_contract(
    contract: dict[str, object],
    role_registry: dict[str, object],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(contract, dict):
        return [_error("ERR_AGENT_CONTRACT_INVALID", "contract must be an object")]
    if not isinstance(role_registry, dict):
        return [
            _error(
                "ERR_AGENT_CONTRACT_ROLE_REGISTRY_INVALID",
                "role registry must be an object",
            )
        ]
    if contract.get("kind") != AGENT_OPERATING_CONTRACT_KIND:
        errors.append(
            _error(
                "ERR_AGENT_CONTRACT_KIND_INVALID",
                f"kind must be {AGENT_OPERATING_CONTRACT_KIND}",
            )
        )
    if contract.get("schema_version") != AGENT_OPERATING_CONTRACT_SCHEMA_VERSION:
        errors.append(
            _error(
                "ERR_AGENT_CONTRACT_SCHEMA_VERSION_INVALID",
                f"schema_version must be {AGENT_OPERATING_CONTRACT_SCHEMA_VERSION}",
            )
        )
    if contract.get("contract_id") != AGENT_OPERATING_CONTRACT_ID:
        errors.append(
            _error(
                "ERR_AGENT_CONTRACT_ID_INVALID",
                f"contract_id must be {AGENT_OPERATING_CONTRACT_ID}",
            )
        )
    if contract.get("agent_contract_id") != AGENT_OPERATING_CONTRACT_ID:
        errors.append(
            _error(
                "ERR_AGENT_CONTRACT_AGENT_ID_INVALID",
                f"agent_contract_id must be {AGENT_OPERATING_CONTRACT_ID}",
            )
        )
    boundary = contract.get("boundary")
    if not isinstance(boundary, dict):
        errors.append(
            _error("ERR_AGENT_CONTRACT_BOUNDARY_INVALID", "boundary must be an object")
        )
    else:
        for key in (
            "executes_commands",
            "launches_agents",
            "calls_live_models",
            "raises_assurance",
        ):
            if boundary.get(key) is not False:
                errors.append(
                    _error(
                        "ERR_AGENT_CONTRACT_BOUNDARY_INVALID",
                        f"boundary.{key} must be false",
                    )
                )
    expected = _contract_payload_hash(contract)
    if contract.get("agent_contract_hash") != expected:
        errors.append(
            _error(
                "ERR_AGENT_CONTRACT_HASH_MISMATCH",
                "agent_contract_hash must match the canonical contract payload",
            )
        )
    registry = contract.get("role_registry")
    if not isinstance(registry, dict):
        errors.append(
            _error(
                "ERR_AGENT_CONTRACT_ROLE_REGISTRY_INVALID",
                "role_registry must be an object",
            )
        )
    else:
        if registry.get("path") != DWM_ROLES_PATH.as_posix():
            errors.append(
                _error(
                    "ERR_AGENT_CONTRACT_ROLE_REGISTRY_PATH_INVALID",
                    f"role_registry.path must be {DWM_ROLES_PATH.as_posix()}",
                )
            )
        if registry.get("sha256") != canonical_hash(role_registry):
            errors.append(
                _error(
                    "ERR_AGENT_CONTRACT_ROLE_REGISTRY_HASH_MISMATCH",
                    "role_registry.sha256 must bind packaging/dwm-roles.json",
                )
            )
    binding = contract.get("v22_role_binding")
    if not isinstance(binding, dict):
        errors.append(
            _error(
                "ERR_AGENT_CONTRACT_V22_BINDING_INVALID",
                "v22_role_binding must be an object",
            )
        )
    else:
        if binding.get("source_path") != DWM_ROLES_PATH.as_posix():
            errors.append(
                _error(
                    "ERR_AGENT_CONTRACT_V22_BINDING_PATH_INVALID",
                    f"v22_role_binding.source_path must be {DWM_ROLES_PATH.as_posix()}",
                )
            )
        if binding.get("required_role_id") != V22_WORKER_ROLE_ID:
            errors.append(
                _error(
                    "ERR_AGENT_CONTRACT_V22_ROLE_ID_MISMATCH",
                    f"required_role_id must be {V22_WORKER_ROLE_ID}",
                )
            )
        required_fields = binding.get("required_fields")
        if required_fields != sorted(V22_REQUIRED_ROLE_FIELDS):
            errors.append(
                _error(
                    "ERR_AGENT_CONTRACT_V22_REQUIRED_FIELDS_INVALID",
                    "required_fields must match the V22 worker role contract",
                )
            )
    return errors


def _validate_v22_role_id(
    role_registry: dict[str, object],
    role_id: str,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(role_id, str) or not role_id.strip():
        return [
            _error(
                "ERR_AGENT_CONTRACT_V22_ROLE_ID_REQUIRED",
                "role_id must be a non-empty string",
            )
        ]
    roles = role_registry.get("roles") if isinstance(role_registry, dict) else None
    if not isinstance(roles, list):
        return [
            _error(
                "ERR_AGENT_CONTRACT_V22_ROLES_INVALID",
                "role registry roles must be a list",
            )
        ]
    role_ids = [role.get("id") for role in roles if isinstance(role, dict)]
    if role_id not in role_ids:
        errors.append(
            _error(
                "ERR_AGENT_CONTRACT_V22_ROLE_ID_UNKNOWN",
                f"role_id must exist in {DWM_ROLES_PATH.as_posix()}",
            )
        )
    if len(role_ids) != len(set(role_ids)):
        errors.append(
            _error(
                "ERR_AGENT_CONTRACT_V22_ROLE_ID_DUPLICATE",
                f"role ids in {DWM_ROLES_PATH.as_posix()} must be unique",
            )
        )
    return errors


def _contract_payload_hash(contract: dict[str, object]) -> str:
    payload = dict(contract)
    payload.pop("agent_contract_hash", None)
    return canonical_hash(payload)


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def load_allowlist(path: Path) -> dict[str, object]:
    """Load a JSON allowlist object from disk."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_ALLOWLIST_READ_FAILED",
            str(exc),
        ) from exc
    except json.JSONDecodeError as exc:
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_ALLOWLIST_JSON_INVALID",
            str(exc),
        ) from exc
    if not isinstance(value, dict):
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_ALLOWLIST_INVALID",
            "allowlist must be a JSON object",
        )
    return value


def write_receipt(path: Path, receipt: dict[str, object]) -> None:
    """Write a shell lane launch receipt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _resolve_allowlisted_argv(
    allowlist: dict[str, object], command_id: str
) -> list[str]:
    if not command_id.strip():
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_COMMAND_ID_REQUIRED",
            "command_id must be a non-empty string",
        )
    commands = allowlist.get("commands")
    if not isinstance(commands, list):
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_ALLOWLIST_COMMANDS_INVALID",
            "allowlist.commands must be a list",
        )
    selected: dict[str, object] | None = None
    for command in commands:
        if not isinstance(command, dict):
            raise TeamShellLaneLaunchError(
                "ERR_TEAM_SHELL_LANE_ALLOWLIST_COMMAND_INVALID",
                "allowlist command entries must be objects",
            )
        if command.get("id") == command_id:
            selected = command
            break
    if selected is None:
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_COMMAND_NOT_ALLOWED",
            "command_id is not present in allowlist",
        )
    argv = selected.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(part, str) and part for part in argv)
    ):
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_ARGV_INVALID",
            "allowlisted argv must be a non-empty list of non-empty strings",
        )
    normalized = list(argv)
    blocked = _scan_argv_for_prohibited_agent(normalized)
    if blocked is not None:
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_AGENT_EXECUTABLE_BLOCKED",
            f"prohibited agent executable '{blocked}' is not permitted anywhere in shell lane argv "
            "(including interpreter -c and wrapper trampolines)",
        )
    return normalized


def _resolve_cwd(cwd: Path) -> Path:
    try:
        resolved = cwd.resolve(strict=True)
    except OSError as exc:
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_CWD_INVALID", str(exc)
        ) from exc
    if not resolved.is_dir():
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_CWD_INVALID",
            "cwd must be an existing directory",
        )
    return resolved


def _validate_timeout(timeout_seconds: int) -> None:
    if timeout_seconds < 1 or timeout_seconds > 3600:
        raise TeamShellLaneLaunchError(
            "ERR_TEAM_SHELL_LANE_TIMEOUT_INVALID",
            "timeout_seconds must be between 1 and 3600",
        )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(payload)


def _self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        allowlist = {
            "commands": [
                {
                    "id": "hello",
                    "argv": [sys.executable, "-c", "print('hello shell lane')"],
                }
            ]
        }
        receipt = run_shell_lane_command(
            allowlist=allowlist,
            command_id="hello",
            cwd=root,
            transcript_path=root / "transcript.json",
            timeout_seconds=30,
        )
        assert receipt["decision"] == "pass"
        assert receipt["exit_code"] == 0
        assert isinstance(receipt["agent_contract_hash"], str)
        assert receipt["agent_contract"]["role_id"] == DEFAULT_AGENT_ROLE_ID
        assert receipt["boundary"]["uses_shell"] is False
        assert receipt["boundary"]["allows_arbitrary_shell_string"] is False
        assert receipt["boundary"]["raises_assurance"] is False
        assert Path(str(receipt["transcript_path"])).exists()
