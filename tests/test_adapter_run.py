import json
import os
import base64
import hashlib
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from depone.agent_fabric.paired_run import validate_runner_receipt
from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle
from depone.verify.adapters.base import EvidenceContext, EvidenceFile
from depone.verify.evidence_contract import validate_evidence_contract

from witnessd.adapter_run import LaneBlocked, run_adapter_lane
from witnessd.emitter import emit_lane_evidence
from witnessd.observer import ObserverSeparationError
from witnessd.runintent import RUN_INTENT_PAYLOAD_TYPE, build_run_intent
from witnessd.signing import verify_dsse


BOUND_OBSERVATION_SCHEMA_VERSION = "v109.role_capability_write_scope"
PLANTED_OPENAI_KEY = "sk-" + "a" * 32
PLANTED_GITHUB_PAT = "ghp_" + "b" * 36
PLANTED_PEM_KEY = (
    "-----BEGIN PRIVATE KEY-----\n"
    "planted-private-key-material\n"
    "-----END PRIVATE KEY-----"
)
PLANTED_SECRET_OUTPUT = " ".join(
    [PLANTED_OPENAI_KEY, PLANTED_GITHUB_PAT, PLANTED_PEM_KEY]
)


def _evidence_context_from_dir(
    root: pathlib.Path,
    *,
    trusted_observer_public_key_file: str | None = None,
) -> EvidenceContext:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        content = path.read_text(encoding="utf-8")
        files.append(
            EvidenceFile(
                path=rel,
                content=content,
                sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        )
    trusted_observer_public_key_file = (
        trusted_observer_public_key_file
        or os.environ.get("DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE")
    )
    raw = (
        {"trusted_observer_public_key_file": trusted_observer_public_key_file}
        if trusted_observer_public_key_file is not None
        else {}
    )
    return EvidenceContext(run_id=None, files=files, raw=raw)


def _fake_codex(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'codex-cli 0.0.0\'; exit 0; fi\n'
        "while [ $# -gt 0 ]; do shift; done\n"
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T1"}\'\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"message","text":"done"}}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_codex_with_secrets(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    started = json.dumps({"type": "thread.started", "thread_id": "T1"})
    completed = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "message", "text": PLANTED_SECRET_OUTPUT},
        }
    )
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'codex-cli 0.0.0\'; exit 0; fi\n'
        "while [ $# -gt 0 ]; do shift; done\n"
        "cat >/dev/null\n"
        f"printf '%s\\n' '{started}'\n"
        f"printf '%s\\n' '{completed}'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_codex_writes_env_and_code(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'codex-cli 0.0.0\'; exit 0; fi\n'
        "printf '%s\\n' \"$CODEX_HOME\" > codex-home.txt\n"
        "mkdir -p pkg\n"
        "cat > pkg/agent.py <<'PY'\n"
        "def generated():\n"
        "    return 'agent generated code'\n"
        "PY\n"
        "while [ $# -gt 0 ]; do shift; done\n"
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T1"}\'\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"command_execution","command":"write code"}}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_codex_stages_tracked_change(directory: str) -> str:
    path = pathlib.Path(directory) / "codex"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo \'codex-cli 0.0.0\'; exit 0; fi\n'
        "printf 'updated\\n' > tracked.txt\n"
        "git add tracked.txt\n"
        "while [ $# -gt 0 ]; do shift; done\n"
        "cat >/dev/null\n"
        'printf \'%s\\n\' \'{"type":"thread.started","thread_id":"T1"}\'\n'
        'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"command_execution","command":"update tracked"}}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_claude(directory: str) -> str:
    path = pathlib.Path(directory) / "claude"
    path.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' \'{"type":"session.started","session_id":"S1"}\'\n'
        'printf \'%s\\n\' \'{"type":"assistant.message","message_id":"M1","text":"done"}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_claude_invokes_builtin_hook(directory: str) -> str:
    path = pathlib.Path(directory) / "claude"
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json\n"
        "import shlex\n"
        "import subprocess\n"
        "import sys\n"
        "settings_path = sys.argv[sys.argv.index('--settings') + 1]\n"
        "settings = json.load(open(settings_path, encoding='utf-8'))\n"
        "command = settings['hooks']['PreToolUse'][0]['hooks'][0]['command']\n"
        "completed = subprocess.run(shlex.split(command), input=json.dumps({'tool_name': 'Edit'}), text=True, capture_output=True)\n"
        "if completed.returncode != 0 or completed.stdout:\n"
        "    raise SystemExit(17)\n"
        "print(json.dumps({'type': 'session.started', 'session_id': 'S1'}))\n"
        "print(json.dumps({'type': 'assistant.message', 'message_id': 'M1', 'text': 'done'}))\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_gemini(directory: str) -> str:
    path = pathlib.Path(directory) / "gemini"
    path.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' \'{"type":"message","content":"review start"}\'\n'
        'printf \'%s\\n\' \'{"type":"result","text":"[{\\"severity\\":\\"low\\",\\"file\\":\\"seed.txt\\",\\"line\\":1,\\"summary\\":\\"review note\\"}]"}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _fake_agy(directory: str, *, stale_context: bool = False) -> str:
    path = pathlib.Path(directory) / "agy"
    observed_root = "'/tmp/stale-agy-project'" if stale_context else "os.getcwd()"
    observed_head = (
        "'0' * 40"
        if stale_context
        else (
            "subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()"
        )
    )
    path.write_text(
        "#!/usr/bin/python3\n"
        "import json\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "if sys.stdout.isatty():\n"
        f"    observed_root = {observed_root}\n"
        f"    observed_head = {observed_head}\n"
        "    print('WITNESSD_AGY_CONTEXT ' + json.dumps({'repo_root': observed_root, 'git_head': observed_head}, sort_keys=True))\n"
        "    print('Review findings:')\n"
        "    print('low seed.txt:1 review note')\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _init_repo(path: str) -> None:
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    pathlib.Path(path, "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)


@unittest.skipIf(shutil.which("openssl") is None, "openssl unavailable")
class TestAdapterRun(unittest.TestCase):
    def test_happy_path_emits_valid_receipt(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex(bindir),
                allowed_touched_files=["noop.txt"],
            )

            self.assertEqual(validate_runner_receipt(out["runner_receipt"]), [])
            self.assertEqual(out["runner_receipt"]["runner_kind"], "codex-cli")
            self.assertEqual(out["status_axis"]["assurance"], "evidence-pending")
            run_intent_path = pathlib.Path(out["evidence_dir"], "run-intent.json")
            self.assertTrue(run_intent_path.exists())
            run_intent_artifact = json.loads(
                run_intent_path.read_text(encoding="utf-8")
            )
            self.assertEqual(run_intent_artifact["schema_version"], "1.0")
            envelope = run_intent_artifact["dsse_envelope"]
            self.assertEqual(envelope["payloadType"], RUN_INTENT_PAYLOAD_TYPE)
            self.assertTrue(verify_dsse(envelope, out["public_key_path"]))
            intent = json.loads(base64.b64decode(envelope["payload"]).decode("utf-8"))
            self.assertEqual(intent["schema_version"], "1.0")
            self.assertNotIn("role_capability", intent)
            self.assertEqual(intent["run_id"], "t")
            self.assertEqual(intent["capture_profile"], "redacted")
            self.assertNotIn("noop.txt", json.dumps(intent["allowed_paths"]))
            self.assertEqual(intent["provider"]["name"], "codex")
            self.assertTrue(
                pathlib.Path(out["evidence_dir"], "redaction-manifest.json").exists()
            )
            evidence_contract = json.loads(
                pathlib.Path(out["evidence_dir"], "evidence-contract.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(evidence_contract["schema_version"], "v105.verify_wedge")
            self.assertNotIn("role_capability_write_scope", evidence_contract)
            subject_names = [
                item["name"] for item in out["bundle"]["statement"]["subject"]
            ]
            self.assertIn("run-intent", subject_names)

    def test_high_confidence_secrets_are_scrubbed_and_rederived_in_both_profiles(self):
        for capture_profile in ("full", "redacted"):
            with (
                self.subTest(capture_profile=capture_profile),
                tempfile.TemporaryDirectory() as root,
                tempfile.TemporaryDirectory() as bindir,
            ):
                sandbox = os.path.join(root, "repo")
                evidence_dir = pathlib.Path(root, "evidence")
                _init_repo(sandbox)

                out = run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id=f"secret-{capture_profile}",
                    prompt="emit planted fake credentials",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    codex_binary=_fake_codex_with_secrets(bindir),
                    evidence_dir=str(evidence_dir),
                    allowed_touched_files=["noop.txt"],
                    capture_profile=capture_profile,
                )

                persisted = b"\n".join(
                    path.read_bytes()
                    for path in sorted(evidence_dir.rglob("*"))
                    if path.is_file()
                )
                for raw_secret in (
                    PLANTED_OPENAI_KEY,
                    PLANTED_GITHUB_PAT,
                    PLANTED_PEM_KEY,
                ):
                    self.assertNotIn(raw_secret.encode("utf-8"), persisted)
                for rule in ("openai_key", "github_pat_classic", "pem_private_key"):
                    self.assertIn(f"[REDACTED:{rule}:".encode("utf-8"), persisted)

                manifest = json.loads(
                    (evidence_dir / "redaction-manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                secret_scrub = manifest["secret_scrub"]
                self.assertEqual(
                    {item["rule"] for item in secret_scrub["rules_matched"]},
                    {"openai_key", "github_pat_classic", "pem_private_key"},
                )
                self.assertEqual(
                    secret_scrub["boundary"],
                    {
                        "best_effort": True,
                        "guarantees_completeness": False,
                        "note": "high-confidence secret patterns only; not a guarantee that all secrets are removed",
                    },
                )

                verdict = ingest_signed_evidence_bundle(
                    out["bundle"],
                    out["public_key_path"],
                    {
                        "capture-manifest": str(evidence_dir / "capture-manifest.json"),
                        "observer-capture": str(evidence_dir / "observer-capture.json"),
                        "runner-receipt": str(evidence_dir / "runner-receipt.json"),
                        "run-intent": str(evidence_dir / "run-intent.json"),
                        "redaction-manifest": str(
                            evidence_dir / "redaction-manifest.json"
                        ),
                        "events.raw": str(evidence_dir / "events.raw.jsonl"),
                        "events.normalized": str(
                            evidence_dir / "events.normalized.jsonl"
                        ),
                    },
                    otel_spans=out["bundle"]["otel_spans"],
                )
                self.assertEqual(verdict["decision"], "pass")

    def test_full_profile_without_secret_matches_preserves_provider_bytes(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = pathlib.Path(root, "evidence")
            _init_repo(sandbox)
            fake_codex = _fake_codex(bindir)

            run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="ordinary-full",
                prompt="ordinary output",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=fake_codex,
                evidence_dir=str(evidence_dir),
                allowed_touched_files=["noop.txt"],
                capture_profile="full",
            )

            expected = (
                b'{"type":"thread.started","thread_id":"T1"}\n'
                b'{"type":"item.completed","item":{"type":"message","text":"done"}}\n'
            )
            self.assertEqual((evidence_dir / "events.raw.jsonl").read_bytes(), expected)
            self.assertFalse((evidence_dir / "redaction-manifest.json").exists())

    def test_redacted_capture_profile_emits_manifest_subject_and_verifies(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            _init_repo(sandbox)
            secret_prompt = "read /home/operator/private-notes and write code"

            with patch(
                "witnessd.adapter_run.emit_lane_evidence",
                wraps=emit_lane_evidence,
            ) as emit:
                out = run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t-redacted",
                    prompt=secret_prompt,
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    codex_binary=_fake_codex_writes_env_and_code(bindir),
                    evidence_dir=evidence_dir,
                    allowed_touched_files=["codex-home.txt", "pkg/agent.py"],
                    capture_profile="redacted",
                )

            self.assertEqual(emit.call_args.kwargs["runtime_sandbox"], sandbox)
            self.assertNotEqual(out["runner_receipt"]["worktree"], sandbox)
            self.assertIn("path:", out["runner_receipt"]["worktree"])
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in sorted(pathlib.Path(evidence_dir).rglob("*"))
                if path.is_file()
            )
            self.assertNotIn(sandbox, persisted)

            run_intent_artifact = json.loads(
                pathlib.Path(evidence_dir, "run-intent.json").read_text(
                    encoding="utf-8"
                )
            )
            intent = json.loads(
                base64.b64decode(
                    run_intent_artifact["dsse_envelope"]["payload"]
                ).decode("utf-8")
            )
            self.assertEqual(intent["capture_profile"], "redacted")
            self.assertEqual(intent["baseline"]["git_head_status"], "known")
            self.assertNotIn("pkg/agent.py", json.dumps(intent))
            self.assertNotIn(secret_prompt, json.dumps(intent))

            redaction_manifest = json.loads(
                pathlib.Path(evidence_dir, "redaction-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(redaction_manifest["capture_profile"], "redacted")
            self.assertEqual(
                redaction_manifest["prompt_sha256"],
                hashlib.sha256(secret_prompt.encode("utf-8")).hexdigest(),
            )
            self.assertIn(
                "redaction-manifest",
                [item["name"] for item in out["bundle"]["statement"]["subject"]],
            )
            self.assertNotIn(
                "pkg/agent.py",
                pathlib.Path(evidence_dir, "capture-manifest.json").read_text(
                    encoding="utf-8"
                ),
            )

            artifact_paths = {
                "capture-manifest": str(
                    pathlib.Path(evidence_dir, "capture-manifest.json")
                ),
                "observer-capture": str(
                    pathlib.Path(evidence_dir, "observer-capture.json")
                ),
                "runner-receipt": str(
                    pathlib.Path(evidence_dir, "runner-receipt.json")
                ),
                "run-intent": str(pathlib.Path(evidence_dir, "run-intent.json")),
                "redaction-manifest": str(
                    pathlib.Path(evidence_dir, "redaction-manifest.json")
                ),
                "events.raw": str(pathlib.Path(evidence_dir, "events.raw.jsonl")),
                "events.normalized": str(
                    pathlib.Path(evidence_dir, "events.normalized.jsonl")
                ),
            }
            verdict = ingest_signed_evidence_bundle(
                out["bundle"],
                out["public_key_path"],
                artifact_paths,
                otel_spans=out["bundle"]["otel_spans"],
            )
            self.assertEqual(verdict["decision"], "pass")

    def test_redacted_write_scope_is_rederived_by_depone_without_raw_paths(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "redacted-write-scope-evidence")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t-redacted-write-scope",
                prompt="write pkg/agent.py",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex_writes_env_and_code(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["codex-home.txt", "pkg/agent.py"],
                write_scope=["pkg/**", "codex-home.txt"],
                role_id="runner",
                role_capability="execute",
                capture_profile="redacted",
            )

            evidence_root = pathlib.Path(evidence_dir)
            self.assertEqual(
                validate_evidence_contract(_evidence_context_from_dir(evidence_root)),
                [],
            )

            run_intent_artifact = json.loads(
                (evidence_root / "run-intent.json").read_text(encoding="utf-8")
            )
            run_intent = json.loads(
                base64.b64decode(
                    run_intent_artifact["dsse_envelope"]["payload"]
                ).decode("utf-8")
            )
            declared_scope = run_intent["role_capability"]["declared_write_scope"]
            self.assertNotIn("pkg/agent.py", json.dumps(declared_scope))
            self.assertNotIn("pkg/**", json.dumps(declared_scope))

            declaration = json.loads(
                (evidence_root / "write-scope-declaration.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(declaration["declared_write_scope"], declared_scope)
            self.assertEqual(declaration["verification_status"], "verified")
            self.assertEqual(declaration["conformance"], "pass")
            self.assertNotIn("pkg/agent.py", json.dumps(declaration))
            self.assertNotIn("pkg/**", json.dumps(declaration))

            verdict = ingest_signed_evidence_bundle(
                out["bundle"],
                out["public_key_path"],
                {
                    "capture-manifest": str(evidence_root / "capture-manifest.json"),
                    "observer-capture": str(evidence_root / "observer-capture.json"),
                    "runner-receipt": str(evidence_root / "runner-receipt.json"),
                    "run-intent": str(evidence_root / "run-intent.json"),
                    "events.raw": str(evidence_root / "events.raw.jsonl"),
                    "events.normalized": str(evidence_root / "events.normalized.jsonl"),
                    "redaction-manifest": str(
                        evidence_root / "redaction-manifest.json"
                    ),
                    "write-scope-declaration": str(
                        evidence_root / "write-scope-declaration.json"
                    ),
                    "git-diff-name-only.txt": str(
                        evidence_root / "git-diff-name-only.txt"
                    ),
                },
                otel_spans=out["bundle"]["otel_spans"],
            )
            self.assertEqual(verdict["decision"], "pass")

    def test_same_run_intent_codex_and_claude_emit_same_contract_shape(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as codex_bindir,
            tempfile.TemporaryDirectory() as claude_bindir,
        ):
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)
            intent = build_run_intent(
                run_id="provider-neutral-run",
                baseline={"git_head": "test-head", "git_status_state": "known"},
                allowed_paths=["noop.txt"],
                approval_policy="on-request",
                sandbox_mode="workspace-write",
                provider="provider-neutral",
                instruction_hashes={
                    "prompt_sha256": hashlib.sha256(b"do X").hexdigest()
                },
                budgets={"max_tokens": 1000, "max_usd": 1.0, "max_depth": 1},
                capture_profile="full",
            )

            outputs = {}
            for adapter, binary_arg, fake_binary in (
                ("codex", "codex_binary", _fake_codex(codex_bindir)),
                ("claude", "claude_binary", _fake_claude(claude_bindir)),
            ):
                outputs[adapter] = run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter=adapter,
                    task_id=f"{adapter}-lane",
                    prompt="do X",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    evidence_dir=os.path.join(root, f"{adapter}-evidence"),
                    allowed_touched_files=["noop.txt"],
                    run_intent=intent,
                    capture_profile="full",
                    **{binary_arg: fake_binary},
                )

            subject_names = {
                adapter: [
                    item["name"]
                    for item in output["bundle"]["statement"]["predicate"][
                        "artifact_index"
                    ]
                ]
                for adapter, output in outputs.items()
            }
            self.assertEqual(subject_names["codex"], subject_names["claude"])
            self.assertIn("events.raw", subject_names["codex"])
            self.assertIn("events.normalized", subject_names["codex"])

            schema_keys = {
                adapter: set(output["normalized_events"][0])
                for adapter, output in outputs.items()
            }
            self.assertEqual(schema_keys["codex"], schema_keys["claude"])
            self.assertEqual(
                {
                    event["schema"]
                    for output in outputs.values()
                    for event in output["normalized_events"]
                },
                {"moonweave.agent-event/v1"},
            )

            for adapter, output in outputs.items():
                evidence_dir = pathlib.Path(output["evidence_dir"])
                verdict = ingest_signed_evidence_bundle(
                    output["bundle"],
                    output["public_key_path"],
                    {
                        "capture-manifest": str(evidence_dir / "capture-manifest.json"),
                        "observer-capture": str(evidence_dir / "observer-capture.json"),
                        "runner-receipt": str(evidence_dir / "runner-receipt.json"),
                        "run-intent": str(evidence_dir / "run-intent.json"),
                        "events.raw": str(evidence_dir / "events.raw.jsonl"),
                        "events.normalized": str(
                            evidence_dir / "events.normalized.jsonl"
                        ),
                    },
                    otel_spans=output["bundle"]["otel_spans"],
                )
                self.assertEqual(verdict["decision"], "pass", adapter)

    def test_gemini_review_receipt_is_signed_bundle_subject(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "gemini-evidence")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="gemini",
                task_id="gemini-review",
                prompt="review only",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                evidence_dir=evidence_dir,
                gemini_binary=_fake_gemini(bindir),
                capture_profile="full",
            )

            subject_names = [
                item["name"]
                for item in out["bundle"]["statement"]["predicate"]["artifact_index"]
            ]
            self.assertIn("review-receipt", subject_names)
            self.assertIn("events.raw", subject_names)
            self.assertIn("events.normalized", subject_names)

            evidence_root = pathlib.Path(evidence_dir)
            verdict = ingest_signed_evidence_bundle(
                out["bundle"],
                out["public_key_path"],
                {
                    "capture-manifest": str(evidence_root / "capture-manifest.json"),
                    "observer-capture": str(evidence_root / "observer-capture.json"),
                    "runner-receipt": str(evidence_root / "runner-receipt.json"),
                    "run-intent": str(evidence_root / "run-intent.json"),
                    "events.raw": str(evidence_root / "events.raw.jsonl"),
                    "events.normalized": str(evidence_root / "events.normalized.jsonl"),
                    "review-receipt": str(evidence_root / "review-receipt.json"),
                },
                otel_spans=out["bundle"]["otel_spans"],
            )
            self.assertEqual(verdict["decision"], "pass")

    def test_agy_review_receipt_is_signed_bundle_subject(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "agy-evidence")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="agy",
                task_id="agy-review",
                prompt="review only",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                evidence_dir=evidence_dir,
                agy_binary=_fake_agy(bindir),
                capture_profile="full",
            )

            subject_names = [
                item["name"]
                for item in out["bundle"]["statement"]["predicate"]["artifact_index"]
            ]
            self.assertIn("review-receipt", subject_names)
            self.assertIn("events.raw", subject_names)
            self.assertIn("events.normalized", subject_names)

            evidence_root = pathlib.Path(evidence_dir)
            verdict = ingest_signed_evidence_bundle(
                out["bundle"],
                out["public_key_path"],
                {
                    "capture-manifest": str(evidence_root / "capture-manifest.json"),
                    "observer-capture": str(evidence_root / "observer-capture.json"),
                    "runner-receipt": str(evidence_root / "runner-receipt.json"),
                    "run-intent": str(evidence_root / "run-intent.json"),
                    "events.raw": str(evidence_root / "events.raw.jsonl"),
                    "events.normalized": str(evidence_root / "events.normalized.jsonl"),
                    "review-receipt": str(evidence_root / "review-receipt.json"),
                },
                otel_spans=out["bundle"]["otel_spans"],
            )
            self.assertEqual(verdict["decision"], "pass")

    def test_invalid_agy_context_is_not_emitted_as_review_or_event_evidence(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "agy-invalid-evidence")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="agy",
                task_id="agy-stale-review",
                prompt="review only",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                evidence_dir=evidence_dir,
                agy_binary=_fake_agy(bindir, stale_context=True),
            )

            subject_names = {
                item["name"]
                for item in out["bundle"]["statement"]["predicate"]["artifact_index"]
            }
            self.assertNotIn("review-receipt", subject_names)
            self.assertNotIn("events.raw", subject_names)
            self.assertNotIn("events.normalized", subject_names)
            self.assertEqual(out["runner_receipt"]["exit_code"], 126)
            self.assertNotIn("review note", json.dumps(out))
            self.assertNotIn(
                "review note",
                pathlib.Path(evidence_dir, "verify.log").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                pathlib.Path(root, "adapter-transcript.txt").read_bytes(), b""
            )
            self.assertNotIn(
                "review note",
                pathlib.Path(root, "adapter-command.json").read_text(encoding="utf-8"),
            )

    def test_explicit_model_wires_through_to_codex_invocation_and_declaration(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "model-evidence")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t-model",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["noop.txt"],
                model="gpt-5.5",
                capture_profile="full",
            )

            self.assertIn("-m", out["runner_receipt"]["invocation"])
            self.assertEqual(
                out["runner_receipt"]["invocation"][
                    out["runner_receipt"]["invocation"].index("-m") + 1
                ],
                "gpt-5.5",
            )
            subject_names = [
                item["name"]
                for item in out["bundle"]["statement"]["predicate"]["artifact_index"]
            ]
            self.assertIn("model-declaration", subject_names)

            declaration = json.loads(
                (pathlib.Path(evidence_dir) / "model-declaration.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(declaration["requested_model"], "gpt-5.5")
            self.assertEqual(declaration["verification_status"], "verified")
            self.assertFalse(declaration["can_change_evidence_verdict"])

            evidence_root = pathlib.Path(evidence_dir)
            verdict = ingest_signed_evidence_bundle(
                out["bundle"],
                out["public_key_path"],
                {
                    "capture-manifest": str(evidence_root / "capture-manifest.json"),
                    "observer-capture": str(evidence_root / "observer-capture.json"),
                    "runner-receipt": str(evidence_root / "runner-receipt.json"),
                    "run-intent": str(evidence_root / "run-intent.json"),
                    "events.raw": str(evidence_root / "events.raw.jsonl"),
                    "events.normalized": str(evidence_root / "events.normalized.jsonl"),
                    "model-declaration": str(evidence_root / "model-declaration.json"),
                },
                otel_spans=out["bundle"]["otel_spans"],
            )
            self.assertEqual(verdict["decision"], "pass")

    def test_requested_unverified_model_emits_degraded_event(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "agy-model-evidence")
            _init_repo(sandbox)

            run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="agy",
                task_id="t-agy-model",
                prompt="review only",
                arm="direct",
                tier="quick",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                agy_binary=_fake_agy(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=[],
                model="gemini-3.5-flash",
            )

            declaration = json.loads(
                (pathlib.Path(evidence_dir) / "model-declaration.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(declaration["verification_status"], "requested-unverified")
            runlog_path = pathlib.Path(root, ".witnessd", "runlog.jsonl")
            events = [
                json.loads(line)
                for line in runlog_path.read_text(encoding="utf-8").splitlines()
            ]
            degraded_events = [
                event
                for event in events
                if event.get("event") == "model_route_degraded"
            ]
            self.assertEqual(len(degraded_events), 1)
            self.assertTrue(degraded_events[0]["payload"]["degraded"])
            self.assertEqual(
                degraded_events[0]["payload"]["verification_status"],
                "requested-unverified",
            )

    def test_write_scope_declaration_is_signed_bundle_subject(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "write-scope-evidence")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t-write-scope",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex_writes_env_and_code(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["codex-home.txt", "pkg/agent.py"],
                write_scope=["pkg/**", "codex-home.txt"],
                role_id="runner",
                role_capability="execute",
                capture_profile="full",
            )

            subject_names = [
                item["name"]
                for item in out["bundle"]["statement"]["predicate"]["artifact_index"]
            ]
            self.assertIn("write-scope-declaration", subject_names)
            self.assertIn("git-diff-name-only.txt", subject_names)

            declaration = json.loads(
                (pathlib.Path(evidence_dir) / "write-scope-declaration.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(declaration["kind"], "moonweave-write-scope-declaration")
            self.assertFalse(declaration["can_change_evidence_verdict"])
            self.assertEqual(declaration["role_id"], "runner")
            self.assertEqual(declaration["capability"], "execute")
            self.assertEqual(
                declaration["declared_write_scope"], ["pkg/**", "codex-home.txt"]
            )
            self.assertEqual(
                declaration["allowed_touched_files"], ["codex-home.txt", "pkg/agent.py"]
            )
            self.assertEqual(
                declaration["touched_files"], ["codex-home.txt", "pkg/agent.py"]
            )
            self.assertEqual(declaration["verification_status"], "verified")
            self.assertEqual(declaration["conformance"], "pass")

            evidence_root = pathlib.Path(evidence_dir)
            run_intent_artifact = json.loads(
                (evidence_root / "run-intent.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_intent_artifact["schema_version"], "1.1")
            run_intent = json.loads(
                base64.b64decode(
                    run_intent_artifact["dsse_envelope"]["payload"]
                ).decode("utf-8")
            )
            self.assertEqual(run_intent["schema_version"], "1.1")
            self.assertEqual(
                run_intent["role_capability"],
                {
                    "schema_version": "1.0",
                    "role_id": "runner",
                    "capability": "execute",
                    "declared_write_scope": ["pkg/**", "codex-home.txt"],
                },
            )
            evidence_contract = json.loads(
                (evidence_root / "evidence-contract.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                evidence_contract["schema_version"],
                BOUND_OBSERVATION_SCHEMA_VERSION,
            )
            self.assertEqual(
                evidence_contract["role_capability_write_scope"],
                {
                    "run_intent_path": "run-intent.json",
                    "bundle_path": "bundle.json",
                },
            )
            self.assertEqual(
                validate_evidence_contract(_evidence_context_from_dir(evidence_root)),
                [],
            )
            verdict = ingest_signed_evidence_bundle(
                out["bundle"],
                out["public_key_path"],
                {
                    "capture-manifest": str(evidence_root / "capture-manifest.json"),
                    "observer-capture": str(evidence_root / "observer-capture.json"),
                    "runner-receipt": str(evidence_root / "runner-receipt.json"),
                    "run-intent": str(evidence_root / "run-intent.json"),
                    "events.raw": str(evidence_root / "events.raw.jsonl"),
                    "events.normalized": str(evidence_root / "events.normalized.jsonl"),
                    "write-scope-declaration": str(
                        evidence_root / "write-scope-declaration.json"
                    ),
                    "git-diff-name-only.txt": str(
                        evidence_root / "git-diff-name-only.txt"
                    ),
                },
                otel_spans=out["bundle"]["otel_spans"],
            )
            self.assertEqual(verdict["decision"], "pass")

    def test_write_scope_git_diff_observation_is_bound_for_depone(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "write-scope-bound-observation")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t-write-scope-bound-observation",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex_writes_env_and_code(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["codex-home.txt", "pkg/agent.py"],
                capture_profile="full",
                write_scope=["pkg/**", "codex-home.txt"],
                role_id="runner",
                role_capability="execute",
            )

            evidence_root = pathlib.Path(evidence_dir)
            contract = json.loads(
                (evidence_root / "evidence-contract.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                contract["schema_version"], BOUND_OBSERVATION_SCHEMA_VERSION
            )
            observation_path = evidence_root / "git-diff-name-only.txt"
            subjects = {
                item["name"]: item["digest"]["sha256"]
                for item in out["bundle"]["statement"]["subject"]
            }
            self.assertEqual(
                subjects["git-diff-name-only.txt"],
                hashlib.sha256(observation_path.read_bytes()).hexdigest(),
            )
            errors = validate_evidence_contract(
                _evidence_context_from_dir(
                    evidence_root,
                    trusted_observer_public_key_file=out["public_key_path"],
                )
            )

            self.assertEqual(errors, [])

    def test_write_scope_git_diff_observation_tamper_refutes_in_depone(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "write-scope-tampered-observation")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t-write-scope-tampered-observation",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex_writes_env_and_code(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["codex-home.txt", "pkg/agent.py"],
                write_scope=["pkg/**", "codex-home.txt"],
                role_id="runner",
                role_capability="execute",
            )

            evidence_root = pathlib.Path(evidence_dir)
            observation_path = evidence_root / "git-diff-name-only.txt"
            observation_path.write_text(
                observation_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            errors = validate_evidence_contract(
                _evidence_context_from_dir(
                    evidence_root,
                    trusted_observer_public_key_file=out["public_key_path"],
                )
            )

            self.assertEqual(
                [error.code for error in errors],
                ["ERR_ROLE_CAPABILITY_OBSERVATION_DIGEST_MISMATCH"],
            )

    def test_write_scope_blocks_allowed_touched_files_outside_scope(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)

            with self.assertRaises(LaneBlocked) as ctx:
                run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t-write-scope-blocked",
                    prompt="do X",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    codex_binary=_fake_codex_writes_env_and_code(bindir),
                    allowed_touched_files=["pkg/agent.py"],
                    write_scope=["docs/**"],
                    role_id="runner",
                    role_capability="execute",
                )

            self.assertEqual(
                ctx.exception.reason, "ERR_ROLE_CAPABILITY_WRITE_SCOPE_VIOLATION"
            )

    def test_tool_declaration_is_signed_bundle_subject(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "tool-evidence")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t-tools",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["noop.txt"],
                capture_profile="full",
                tools={"mcp": ["filesystem"], "allow": ["read_file"]},
                role_id="runner",
                role_capability="execute",
            )

            subject_names = [
                item["name"]
                for item in out["bundle"]["statement"]["predicate"]["artifact_index"]
            ]
            self.assertIn("tool-declaration", subject_names)

            declaration = json.loads(
                (pathlib.Path(evidence_dir) / "tool-declaration.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(declaration["kind"], "moonweave-tool-declaration")
            self.assertFalse(declaration["can_change_evidence_verdict"])
            self.assertEqual(declaration["role_id"], "runner")
            self.assertEqual(declaration["capability"], "execute")
            self.assertEqual(declaration["adapter"], "codex")
            self.assertEqual(
                declaration["declared_tools"],
                {"mcp": ["filesystem"], "allow": ["read_file"]},
            )
            self.assertEqual(declaration["enforcement_status"], "enforced")
            self.assertEqual(declaration["usage_verification_status"], "enforced-only")

    def test_claude_tool_decision_artifacts_are_signed_contract_subjects(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
            tempfile.TemporaryDirectory() as config_dir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "tool-evidence")
            _init_repo(sandbox)
            source_config = pathlib.Path(config_dir) / "source-mcp.json"
            source_config.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "neutral_probe": {
                                "command": "/bin/echo",
                                "args": ["neutral"],
                            },
                        }
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            old_config = os.environ.get("WITNESSD_CLAUDE_MCP_CONFIG")
            os.environ["WITNESSD_CLAUDE_MCP_CONFIG"] = str(source_config)
            try:
                out = run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="claude",
                    task_id="t-claude-tools",
                    prompt="do X",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    claude_binary=_fake_claude_invokes_builtin_hook(bindir),
                    evidence_dir=evidence_dir,
                    allowed_touched_files=["noop.txt"],
                    write_scope=["noop.txt"],
                    tools={
                        "mcp": ["neutral_probe"],
                        "allow": ["mcp__neutral_probe__allowed_echo"],
                    },
                    role_id="runner",
                    role_capability="execute",
                )
            finally:
                if old_config is None:
                    os.environ.pop("WITNESSD_CLAUDE_MCP_CONFIG", None)
                else:
                    os.environ["WITNESSD_CLAUDE_MCP_CONFIG"] = old_config

            subject_names = [
                item["name"]
                for item in out["bundle"]["statement"]["predicate"]["artifact_index"]
            ]
            self.assertIn("tool-call-decision-advisory", subject_names)
            self.assertIn("tool-call-decision-receipts", subject_names)

            advisory = json.loads(
                (
                    pathlib.Path(evidence_dir) / "tool-call-decision-advisory.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(advisory["kind"], "moonweave-tool-call-decision-advisory")
            self.assertFalse(advisory["can_change_evidence_verdict"])
            self.assertEqual(advisory["adapter"], "claude")
            self.assertEqual(advisory["policy"]["mcp"], ["neutral_probe"])

            receipts = json.loads(
                (
                    pathlib.Path(evidence_dir) / "tool-call-decision-receipts.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(receipts["kind"], "moonweave-tool-call-decision-receipts")
            self.assertEqual(receipts["adapter"], "claude")
            self.assertEqual(receipts["decisions"], [])
            self.assertEqual(
                [
                    (
                        item["canonical_tool_name"],
                        item["decision"],
                        item["reason_code"],
                    )
                    for item in receipts["all_tool_decisions"]
                ],
                [
                    (
                        "Edit",
                        "allow",
                        "ROLE_CAPABILITY_BUILTIN_TOOL_GRANTED",
                    )
                ],
            )
            self.assertEqual(receipts["observed_mcp_tool_calls"], [])

            run_intent_artifact = json.loads(
                (pathlib.Path(evidence_dir) / "run-intent.json").read_text(
                    encoding="utf-8"
                )
            )
            declared_tools = run_intent_artifact["intent"]["role_capability"][
                "declared_tools"
            ]
            self.assertEqual(
                declared_tools,
                {
                    "mcp": ["neutral_probe"],
                    "allow": ["mcp__neutral_probe__allowed_echo"],
                },
            )

            evidence_contract = json.loads(
                (pathlib.Path(evidence_dir) / "evidence-contract.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                evidence_contract["schema_version"],
                "v109.role_capability_write_scope",
            )
            self.assertEqual(
                evidence_contract["role_capability_tool_calls"][
                    "decision_receipts_path"
                ],
                "tool-call-decision-receipts.json",
            )
            self.assertEqual(
                evidence_contract["role_capability_write_scope"]["bundle_path"],
                "bundle.json",
            )
            self.assertEqual(
                validate_evidence_contract(
                    _evidence_context_from_dir(pathlib.Path(evidence_dir))
                ),
                [],
            )

    def test_no_model_requested_emits_no_model_declaration_artifact(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "no-model-evidence")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t-no-model",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["noop.txt"],
            )

            self.assertNotIn("-m", out["runner_receipt"]["invocation"])
            subject_names = [
                item["name"]
                for item in out["bundle"]["statement"]["predicate"]["artifact_index"]
            ]
            self.assertNotIn("model-declaration", subject_names)
            self.assertFalse(
                (pathlib.Path(evidence_dir) / "model-declaration.json").exists()
            )

    def test_codex_uses_isolated_state_namespace(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)
            outside_codex_home = os.path.join(root, "operator-codex-home")
            os.makedirs(outside_codex_home)

            old_codex_home = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = outside_codex_home
            try:
                run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t",
                    prompt="do X",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    codex_binary=_fake_codex_writes_env_and_code(bindir),
                    allowed_touched_files=["codex-home.txt", "pkg/agent.py"],
                )
            finally:
                if old_codex_home is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old_codex_home

            used_home = (
                pathlib.Path(sandbox, "codex-home.txt")
                .read_text(encoding="utf-8")
                .strip()
            )
            self.assertTrue(
                os.path.realpath(used_home).startswith(
                    os.path.realpath(os.path.join(root, ".witnessd"))
                )
            )
            self.assertNotEqual(used_home, outside_codex_home)

    def test_state_dir_inside_sandbox_rejected_failclosed(self):
        # Live-bug regression: a caller passing `root` equal to `sandbox`
        # with no explicit `state_root` used to let .witnessd/codex-home
        # land inside the observed sandbox -- a real codex run then wrote
        # its own cache/plugin/config files there, and those showed up as
        # ~350 polluting entries in touched_files alongside the agent's
        # actual edit. Must fail closed before anything runs.
        with (
            tempfile.TemporaryDirectory() as sandbox,
            tempfile.TemporaryDirectory() as bindir,
        ):
            _init_repo(sandbox)
            with self.assertRaises(ObserverSeparationError):
                run_adapter_lane(
                    root=sandbox,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t",
                    prompt="do X",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    codex_binary=_fake_codex(bindir),
                    allowed_touched_files=["noop.txt"],
                )

    def test_adapter_evidence_includes_generated_diff_patch(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            _init_repo(sandbox)

            run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex_writes_env_and_code(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["codex-home.txt", "pkg/agent.py"],
                capture_profile="full",
            )

            patch = pathlib.Path(evidence_dir, "git-diff.patch").read_text(
                encoding="utf-8"
            )
            self.assertIn("+def generated():", patch)
            self.assertIn("pkg/agent.py", patch)

    def test_codex_transcript_binding_is_relative_to_evidence_parent(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            _init_repo(sandbox)

            out = run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["noop.txt"],
                capture_profile="full",
            )

            receipt = out["runner_receipt"]
            self.assertIn("--json", receipt["invocation"])
            self.assertNotIn("--output-last-message", receipt["invocation"])
            self.assertEqual(receipt["transcript_path"], "evidence/verify.log")
            command_log = json.loads(
                pathlib.Path(root, "adapter-command.json").read_text(encoding="utf-8")
            )
            self.assertEqual(command_log["command"], receipt["invocation"])
            self.assertTrue(pathlib.Path(root, "adapter-transcript.txt").exists())
            self.assertIn(
                '"thread.started"',
                pathlib.Path(root, "adapter-transcript.txt").read_text(
                    encoding="utf-8"
                ),
            )

    def test_missing_allowlist_is_not_filled_from_observed_touches(self):
        with tempfile.TemporaryDirectory() as root:
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            _init_repo(sandbox)

            with (
                patch("witnessd.adapter_run.probe_adapter_capability"),
                patch(
                    "witnessd.adapter_run._run_adapter",
                    return_value=SimpleNamespace(
                        command_receipts=[
                            {
                                "command": ["fake-adapter"],
                                "exit_code": 0,
                                "stdout": "",
                                "stderr": "",
                            }
                        ],
                        touched_files=["touched.txt"],
                        test_output={"status": "not-run"},
                        invocation=["fake-adapter"],
                        runner_kind="fake-adapter",
                    ),
                ),
            ):
                run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t",
                    prompt="do X",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    evidence_dir=evidence_dir,
                )

            manifest = json.loads(
                pathlib.Path(evidence_dir, "capture-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["allowed_touched_files"], [])
            self.assertIn(
                "touched.txt",
                manifest["observer_capture"]["touched_files"],
            )

    def test_adapter_evidence_includes_staged_tracked_diff_patch(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            evidence_dir = os.path.join(root, "evidence")
            subprocess.run(["git", "init", "-q", sandbox], check=True)
            pathlib.Path(sandbox, "tracked.txt").write_text(
                "original\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=sandbox,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "test"], cwd=sandbox, check=True
            )
            subprocess.run(["git", "add", "tracked.txt"], cwd=sandbox, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=sandbox, check=True)

            run_adapter_lane(
                root=root,
                sandbox=sandbox,
                adapter="codex",
                task_id="t",
                prompt="do X",
                arm="direct",
                tier="agentic",
                is_supported=lambda _model: True,
                budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                codex_binary=_fake_codex_stages_tracked_change(bindir),
                evidence_dir=evidence_dir,
                allowed_touched_files=["tracked.txt"],
                capture_profile="full",
            )

            patch = pathlib.Path(evidence_dir, "git-diff.patch").read_text(
                encoding="utf-8"
            )
            self.assertIn("diff --git a/tracked.txt b/tracked.txt", patch)
            self.assertIn("+updated", patch)

    def test_route_exhausted_ends_blocked_not_silent(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)

            with self.assertRaises(LaneBlocked) as cm:
                run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t",
                    prompt="x",
                    arm="direct",
                    tier="quick",
                    is_supported=lambda _model: False,
                    budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                    codex_binary=_fake_codex(bindir),
                )

            self.assertEqual(cm.exception.reason, "route_blocked")
            runlog_path = os.path.join(root, ".witnessd", "runlog.jsonl")
            with open(runlog_path, encoding="utf-8") as handle:
                events = [json.loads(line) for line in handle]
            self.assertIn("model_not_supported", [event["event"] for event in events])
            self.assertIn("route_blocked", [event["event"] for event in events])
            self.assertNotIn("VERIFIED", json.dumps(events))

    def test_budget_blowout_hard_stops(self):
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as bindir,
        ):
            sandbox = os.path.join(root, "repo")
            _init_repo(sandbox)

            with self.assertRaises(LaneBlocked) as cm:
                run_adapter_lane(
                    root=root,
                    sandbox=sandbox,
                    adapter="codex",
                    task_id="t",
                    prompt="x",
                    arm="direct",
                    tier="agentic",
                    is_supported=lambda _model: True,
                    budget={"max_tokens": 1, "max_usd": 1.0, "max_depth": 3},
                    predicted_tokens=10**6,
                    codex_binary=_fake_codex(bindir),
                )

            self.assertEqual(cm.exception.reason, "budget_exceeded")


if __name__ == "__main__":
    unittest.main()
