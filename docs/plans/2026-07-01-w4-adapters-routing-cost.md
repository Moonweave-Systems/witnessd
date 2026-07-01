# W4 — Codex/Claude/OpenCode 어댑터 + 모델 라우팅 solved abstraction + 비용 서킷브레이커 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (권장) 또는 `superpowers:executing-plans`. 각 Task는 bite-sized TDD 5스텝(실패 테스트 → 실패 확인 → 최소 구현 → 통과 확인 → commit)이며 Steps는 `- [ ]` 체크박스다.

**Goal:** 능력 breadth를 연다. 서로 다른 substrate 어댑터(Codex CLI=첫 어댑터, 이어 Claude Code/OpenCode)가 **동일한 W1 `build_runner_receipt` 스키마**를 방출해 Depone가 어댑터 무관하게 `validate_runner_receipt`로 검증하게 하고, 모델 라우팅을 버그 원천이 아닌 solved abstraction(M8: quick/agentic/frontier + `model_not_supported` 재시도 + per-task concurrency key + graceful degradation, **silent task death 금지**)으로, 비용을 하드 상한 서킷브레이커(M10: per-task 토큰·달러 예측 + 하드 상한 + delegation depth/spend 예산)로 만든다. 완료 정의는 witnessd의 self-report가 아니라 별도 Depone(비실행 검증기)이 witnessd가 방출한 바이트에서 `validate_runner_receipt == []`(Codex는 `runner_kind="codex-cli"`로 즉시)와 라우팅/비용 회귀를 재도출하는 것이다.

**Architecture:** Python 3.10+ 표준 라이브러리만. W4는 W1이 세운 Evidence Emitter + W3의 lane 오케스트레이션 **위에 얹기만** 한다 — 각 어댑터 lane은 여전히 W1 `emit_lane_evidence`로 observer-분리 capture-manifest + prev_capture 체인 + operator Ed25519 DSSE + runner-receipt를 방출하고(단조성), W4는 그 위에 (1) 어댑터 계층(Codex/Claude/OpenCode → 동일 runner-receipt), (2) 모델 라우터, (3) 비용 서킷브레이커, (4) OMX/LazyCodex 동시실행 대비 상태 격리(별도 상태 디렉터리 + lock)를 더한다. 라우팅/비용 실측은 W1 **runlog 체인**(`prev_event_hash`)에 event로 append하고, E7 evidence-substrate 번들의 인라인 OTel GenAI span에 라우팅 메타를 **정적 span**으로만 싣는다(usage 날조 금지). 검증은 전적으로 Depone이 한다 — witnessd는 Depone 검증 함수를 **재구현하지 않고**, 이들이 받아들이는 아티팩트를 생산한다.

**Tech Stack:** Python stdlib(`json`, `hashlib`, `subprocess`, `pathlib`, `os`, `fcntl`, `argparse`, `unittest`), `openssl` CLI(Ed25519, W1 `signing.py` 재사용), `git` CLI(W3 worktree 재사용), Codex/Claude/OpenCode CLI(어댑터가 subprocess로 호출; 미설치 시 preflight가 fail-closed). 외부 의존성/`pyproject` 금지.

**계약 근거 (정확한 필드는 아래 파일을 읽어 확정 — 추측 금지):**
- `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/paired_run.py` — `build_runner_receipt`(`*, runner_kind, arm, task_id, worktree, invocation, transcript_path, exit_code, touched_files, started_at, ended_at, human_intervened=False`), `validate_runner_receipt`, 상수 `RUNNER_RECEIPT_KIND="agent-fabric-runner-receipt"`/`RUNNER_RECEIPT_VERSION="1.0"`, enum `VALID_RUNNERS=frozenset({"codex-cli","manual"})`/`VALID_ARMS=frozenset({"direct","governed"})`, 그리고 **Codex invocation 형태의 정본** `run_codex_exec`(라인 226–300: `codex --sandbox <mode> exec --skip-git-repo-check --cd <repo> --output-last-message <transcript> -`). witnessd는 이 함수를 **import하지 않고**, 이 invocation 형태를 자기 어댑터로 재현한다.
- `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/codex_local_capability.py` — `build_codex_local_capability`/`validate_codex_local_capability`, 상수 `CODEX_LOCAL_CAPABILITY_KIND="depone-codex-local-capability"`/`SCHEMA_VERSION="0.1"`, `ALLOWED_SANDBOX_MODES={"read-only","workspace-write"}`, `ALLOWED_APPROVAL_POLICIES={"on-request","on-failure","never"}`, `boundary.{launches_live_model,executes_coding_task,raises_assurance}==False`/`captures_capability_only==True` (비-launching preflight의 정본).
- `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/evidence_substrate.py` — `build_otel_genai_spans(capture_manifest, *, runner_receipt=None)`(정적 GenAI span, usage 발명 금지)와 `validate_external_otel_spans`(라우팅 메타 span이 만족해야 할 shape). `gen_ai.operation.name`은 문자열 필수.

**W1/W3에서 그대로 재사용(재정의 금지):** `canonical_hash`(`witnessd/canonical.py`), `EventLog`(`witnessd/eventlog.py`, `prev_event_hash`), `render_status`/`STATUS_DOMAIN`(`witnessd/status.py`), `assert_separated`/`build_observer_capture`(`witnessd/observer.py`), `run_shell_lane`(`witnessd/adapters/shell.py`), `build_capture_manifest`(`witnessd/capture.py`), `gen_operator_keypair`/`sign_dsse`(`witnessd/signing.py`), **`build_runner_receipt`(`witnessd/receipt.py`, `runner_kind` 파라미터 그대로 — W1이 `"manual"`로 호출한 그 함수를 W4는 `"codex-cli"`로도 호출)**, `build_bundle`/`build_evidence_contract`(`witnessd/substrate.py`), `emit_lane_evidence`(`witnessd/emitter.py`), 그리고 W3 `create_lane_worktree`/`build_worktree_lane_receipt`(`witnessd/worktree.py`)·`run_team`(`witnessd/fanin.py`). W4 새 함수는 이들 **위에** 얹는다.

**불변식(§5.0, 협상 불가):**
- **단조성**: W4 각 어댑터 capture가 W1 `validate_capture_manifest`+`verify_capture_chain` 및 W3 team-ledger를 여전히 통과, W2 A2 격리 유지.
- **assurance 상한 A2**(A3 없음) · worker self-seal 불가 · **Evidence Emitter만 SoT 쓰기**(W4 신규 라우팅/비용 event도 `EventLog`/emitter 경유) · fail-closed(부분점수 없음).
- **runner_kind 계약(cross-repo, fail-closed)**: Depone `VALID_RUNNERS=={"codex-cli","manual"}`. Codex 어댑터=`"codex-cli"`(즉시 검증). **Claude Code/OpenCode 어댑터는 `VALID_RUNNERS` 확장 전까지 `"manual"`로 방출**하며, A-등급 승격을 위한 enum 확장은 **Depone repo의 별도 contract PR로 게이트**된다 — witnessd는 임의 `runner_kind`를 위조해 검증을 통과시킬 수 없다(미지 `runner_kind` → receipt invalid). 이 확장 PR은 W4 범위 밖이다.

---

## File Structure (W4에서 신규/수정)

```
witnessd/
  adapters/
    base.py          # NEW — AdapterResult 정규화 계약 + RUNNER_KIND_BY_ADAPTER 매핑 + assert_runner_kind_valid (fail-closed)
    codex.py         # NEW — run_codex_lane (codex CLI exec, invocation=run_codex_exec 형태, runner_kind="codex-cli")
    claude.py        # NEW — run_claude_lane (claude CLI/Task, runner_kind="manual")
    opencode.py      # NEW — run_opencode_lane (opencode CLI, runner_kind="manual")
  preflight.py       # NEW — probe_adapter_capability (codex_local_capability shape, 비-launching, ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE)
  router.py          # NEW — route_model (M8: quick/agentic/frontier + model_not_supported 재시도 + concurrency key + graceful degradation, RouteExhausted→blocked)
  budget.py          # NEW — CostBreaker (M10: predict/charge/hard cap + delegation depth/spend 예산, ERR_WITNESSD_BUDGET_EXCEEDED)
  state.py           # NEW — StateNamespace(별도 state dir + flock) + detect_state_contention (witnessd doctor, ERR_WITNESSD_STATE_CONTENTION)
  adapter_run.py     # NEW — run_adapter_lane: preflight→state 격리→router→budget→adapter→W1 emit (단조성)
  __main__.py        # MODIFY — `witnessd run --adapter codex|claude|opencode`, `witnessd route`, `witnessd doctor`, `witnessd faultkit budget-blowout`
tests/
  test_adapter_base.py, test_codex_adapter.py, test_claude_opencode_adapter.py,
  test_preflight.py, test_router.py, test_budget.py, test_state_isolation.py,
  test_adapter_run.py, test_cli_w4.py            # NEW
fixtures/w4/
  runner-receipt-codex.json          # Codex 어댑터 실 lane, runner_kind="codex-cli", validate_runner_receipt==[]
  runner-receipt-claude-manual.json  # Claude 어댑터, runner_kind="manual"
  runner-receipt-opencode-manual.json
  route-degrade.jsonl                # model_not_supported 주입 → 재시도→degradation→blocked 명시 종료(silent stop 없음)
  budget-blowout.jsonl               # per-task 상한 초과 → 하드 중단 + budget_exceeded event + spawn 0
  state-isolation/                   # mock OMX/LazyCodex store + witnessd 네임스페이스(자기 것만 씀 증명)
  bundle-codex.json                  # in-toto+DSSE+정적 OTel(라우팅 메타, usage 미날조)
  keys/operator.pub                  # 공개키만(개인키 커밋 금지)
scripts/
  revalidate_w4.py                   # NEW — Depone validator로 재도출(G2)
```

---

## Task 0: W4 스캐폴드 + Depone 어댑터/capability validator import 확인

**Files:**
- Create: `fixtures/w4/.gitkeep`
- Verify: W1/W3 모듈 import 가능(회귀 없음)

- [ ] **Step 1: 디렉터리 + import 스모크**
```bash
cd /home/ubuntu/witnessd
mkdir -p fixtures/w4/state-isolation
touch fixtures/w4/.gitkeep
python3 -c "from witnessd.receipt import build_runner_receipt; from witnessd.emitter import emit_lane_evidence; from witnessd.eventlog import EventLog; print('w1/w3 ok')"
python3 -c "from depone.agent_fabric.paired_run import build_runner_receipt, validate_runner_receipt, VALID_RUNNERS, VALID_ARMS; from depone.agent_fabric.codex_local_capability import build_codex_local_capability, validate_codex_local_capability; from depone.agent_fabric.evidence_substrate import build_otel_genai_spans, validate_external_otel_spans; print('depone w4 ok')"
# §5.0.5 이전-웨이브 그린 게이트(W4는 W1/W2/W3 그린 상태에서만 착수 — 레드면 착수 금지)
python3 scripts/revalidate_w1.py && python3 scripts/revalidate_w2.py && python3 scripts/revalidate_w3.py
```
Expected: `w1/w3 ok` / `depone w4 ok`, 그리고 `W1/W2/W3 revalidate: PASS` 각각 exit 0.
- [ ] **Step 2: enum 스냅샷 확인(계약 그라운딩)** — `python3 -c "from depone.agent_fabric.paired_run import VALID_RUNNERS; assert VALID_RUNNERS=={'codex-cli','manual'}, VALID_RUNNERS; print('VALID_RUNNERS locked')"`. (달라졌으면 `paired_run.py`를 읽어 Task 1 매핑을 갱신.)
- [ ] **Step 3: Commit** — `git add -A && git commit -m "chore: scaffold witnessd W4 adapters/routing/cost"`

---

## Task 1: 어댑터 정규화 계약 (`adapters/base.py`) — 동일 runner-receipt로 수렴하는 접점

**Files:**
- Create: `witnessd/adapters/base.py`
- Test: `tests/test_adapter_base.py`

모든 어댑터는 실행 결과를 **하나의 정규화 dict `AdapterResult`** 로 반환해, W1 `build_observer_capture`(command_receipts/touched_files/test_output)와 W1 `build_runner_receipt`(invocation/exit_code/transcript_path/runner_kind)가 어댑터 무관하게 소비하게 한다. `runner_kind`는 어댑터→enum 매핑으로만 정해지며 임의값 금지(fail-closed).

- [ ] **Step 1: 실패 테스트**
```python
import unittest
from witnessd.adapters.base import (
    RUNNER_KIND_BY_ADAPTER, assert_runner_kind_valid, RunnerKindError, AdapterResult,
)
from depone.agent_fabric.paired_run import VALID_RUNNERS

class TestAdapterBase(unittest.TestCase):
    def test_codex_maps_to_codex_cli(self):
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["codex"], "codex-cli")
    def test_claude_opencode_manual_until_extension(self):
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["claude"], "manual")
        self.assertEqual(RUNNER_KIND_BY_ADAPTER["opencode"], "manual")
    def test_all_mapped_kinds_in_depone_valid_runners(self):
        self.assertTrue(set(RUNNER_KIND_BY_ADAPTER.values()) <= VALID_RUNNERS)
    def test_unknown_kind_rejected_failclosed(self):
        with self.assertRaises(RunnerKindError):
            assert_runner_kind_valid("claude-code")   # not in VALID_RUNNERS
    def test_result_requires_nonempty_invocation(self):
        with self.assertRaises(ValueError):
            AdapterResult(adapter="codex", runner_kind="codex-cli", invocation=[],
                          exit_code=0, transcript_path="t", command_receipts=[],
                          touched_files=[], test_output={"status": "passed"})
```
- [ ] **Step 2: 실패 확인** — `python3 -m unittest tests.test_adapter_base -v` → FAIL(module 없음).
- [ ] **Step 3: 구현** — `RUNNER_KIND_BY_ADAPTER = {"codex": "codex-cli", "claude": "manual", "opencode": "manual"}`. `assert_runner_kind_valid(runner_kind: str) -> None`은 Depone `VALID_RUNNERS`를 import해 미포함이면 `RunnerKindError("runner_kind must be one of {sorted(VALID_RUNNERS)}")`(위조 차단). `AdapterResult`는 `dataclass`(frozen): 필드 `adapter: str`, `runner_kind: str`, `invocation: list[str]`, `exit_code: int`, `transcript_path: str`, `command_receipts: list[dict]`, `touched_files: list[str]`, `test_output: dict`. `__post_init__`에서 `invocation` 비어있으면 `ValueError`, `assert_runner_kind_valid(runner_kind)` 호출, `runner_kind == RUNNER_KIND_BY_ADAPTER[adapter]`(불일치 시 `RunnerKindError`). `to_runner_receipt(*, arm, task_id, worktree, started_at, ended_at, human_intervened=False)`는 W1 `receipt.build_runner_receipt`를 그대로 호출(재구현 금지)해 receipt 반환. **`command_receipts[*]`와 `test_output.status` 키 구조는 W1 `observer.build_observer_capture`가 요구하는 것과 동일**(W1 `capture.py`/Depone `capture_bridge._check_observer_capture_shape` 준수).
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_adapter_base -v` → PASS.
- [ ] **Step 5: Commit** — `feat: adapter normalization contract (single runner-receipt convergence, fail-closed runner_kind)`

---

## Task 2: Codex 어댑터 (`adapters/codex.py`) — 첫 어댑터, runner_kind="codex-cli" 즉시 검증

**Files:**
- Create: `witnessd/adapters/codex.py`
- Test: `tests/test_codex_adapter.py`

Codex CLI를 non-interactive로 호출해 lane을 실행하고, invocation은 **Depone `run_codex_exec`(라인 246–257) 형태**를 재현한다: `codex --sandbox <mode> exec --skip-git-repo-check --cd <repo> --output-last-message <transcript> -`. 결과를 `AdapterResult(adapter="codex", runner_kind="codex-cli", ...)`로 반환. codex 미설치/미준비는 실행하지 않고 preflight(Task 3)가 fail-closed로 막으므로 여기서는 **codex 부재 시 `ERR_CODEX_UNAVAILABLE`** 로 raise(부분 산출 금지).

- [ ] **Step 1: 실패 테스트** — codex 바이너리 유무와 무관하게 회귀 가능하도록 `codex_binary=` 주입(가짜 스크립트) 지원.
```python
import unittest, tempfile, os, stat, pathlib
from witnessd.adapters.codex import run_codex_lane
from witnessd.receipt import build_runner_receipt
from depone.agent_fabric.paired_run import validate_runner_receipt

def _fake_codex(dir_):  # writes transcript path passed via --output-last-message, exit 0
    p = pathlib.Path(dir_) / "codex"
    p.write_text('#!/bin/sh\nout=""\nwhile [ $# -gt 0 ]; do [ "$1" = "--output-last-message" ] && out="$2"; shift; done\n: > "$out"; echo done >> "$out"; exit 0\n')
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)

class TestCodexAdapter(unittest.TestCase):
    def test_result_shape_and_receipt_valid(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as bindir, tempfile.TemporaryDirectory() as obs:
            res = run_codex_lane(sandbox=repo, prompt="do X",
                                 codex_binary=_fake_codex(bindir),
                                 transcript_path=os.path.join(obs, "transcript.txt"),
                                 log_path=os.path.join(obs, "codex.log"),
                                 sandbox_mode="workspace-write")
            self.assertEqual(res.runner_kind, "codex-cli")
            self.assertTrue(res.invocation and res.invocation[0].endswith("codex"))
            self.assertIn("exec", res.invocation)
            self.assertIsInstance(res.exit_code, int)
            r = res.to_runner_receipt(arm="direct", task_id="t1", worktree=repo,
                                      started_at="2026-07-01T00:00:00Z", ended_at="2026-07-01T00:00:01Z")
            self.assertEqual(validate_runner_receipt(r), [])
            self.assertEqual(r["runner_kind"], "codex-cli")
    def test_empty_prompt_rejected(self):
        with self.assertRaises(Exception):
            run_codex_lane(sandbox="/tmp", prompt="   ", codex_binary="/bin/true")
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `run_codex_lane(*, sandbox, prompt, codex_binary="codex", transcript_path, log_path, sandbox_mode="workspace-write", timeout_seconds=120) -> AdapterResult`: 빈 prompt → `ERR_CODEX_PROMPT_MISSING`; `shutil.which(codex_binary)`(절대경로면 그대로) None → `ERR_CODEX_UNAVAILABLE`. invocation = `[codex, "--sandbox", sandbox_mode, "exec", "--skip-git-repo-check", "--cd", sandbox, "--output-last-message", transcript_path, "-"]`(Depone `run_codex_exec` 정본과 1:1). `subprocess.run(cwd=sandbox, input=prompt, text=True, capture_output=True, timeout=...)`; `TimeoutExpired`면 exit_code=124. touched_files = 실행 전후 스냅샷 diff(W1 `run_shell_lane`의 diff 로직 재사용). `command_receipts=[{"command": invocation, "exit_code": exit_code}]`, `test_output`은 prompt 실행이 검증 커맨드가 아니므로 `{"status": "not-run"}`(별도 검증 lane은 W1 observer_capture 경로가 채움). AdapterResult 반환. **transcript_path/log_path는 observer-owned(runner sandbox 밖)여야 하며 그 강제는 W1 `assert_separated`가 오케스트레이터(Task 9)에서 수행** — 어댑터는 경로를 신뢰하지 않고 그대로 기록.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: Codex adapter (codex-cli invocation, runner_kind=codex-cli, receipt-valid)`

---

## Task 3: 어댑터 preflight (`preflight.py`) — 비-launching capability, fail-closed

**Files:**
- Create: `witnessd/preflight.py`
- Test: `tests/test_preflight.py`

어댑터 부재/미준비는 lane을 launch하지 않고 거부한다(§6.5.1 `ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE`). Codex는 Depone `build_codex_local_capability`가 정의한 **비-launching capability receipt**(decision `pass`/`blocked`, `boundary.launches_live_model==False`)를 사용해 준비도를 판정한다.

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, subprocess
from witnessd.preflight import probe_adapter_capability, PreflightError
from depone.agent_fabric.codex_local_capability import validate_codex_local_capability

class TestPreflight(unittest.TestCase):
    def test_codex_capability_receipt_valid_and_blocked_when_missing(self):
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-q", repo], check=True)
            cap = probe_adapter_capability("codex", repo=repo, codex_binary="definitely-not-a-real-binary")
            self.assertEqual(validate_codex_local_capability(cap), [])
            self.assertEqual(cap["decision"], "blocked")
            self.assertTrue(cap["blocked_reasons"])
            self.assertIs(cap["boundary"]["launches_live_model"], False)
    def test_require_ready_raises_when_blocked(self):
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-q", repo], check=True)
            with self.assertRaises(PreflightError):
                probe_adapter_capability("codex", repo=repo, codex_binary="definitely-not-a-real-binary", require_ready=True)
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `probe_adapter_capability(adapter: str, *, repo: str, codex_binary="codex", require_ready=False, **kw) -> dict`: `adapter=="codex"`면 Depone `build_codex_local_capability(repo=Path(repo), codex_binary=codex_binary, **kw)`를 **그대로 호출**(재구현 금지)해 capability receipt 반환. `claude`/`opencode`는 `shutil.which`로 바이너리 존재만 확인해 동형(`decision`/`blocked_reasons`/`boundary`) 최소 receipt 구성(정확한 required 키·중첩은 `codex_local_capability.build_codex_local_capability` 반환 dict를 읽어 boundary 4필드 구조를 맞춘다; capability 스키마 kind는 Codex 전용이므로 claude/opencode용은 witnessd-local kind `witnessd-adapter-capability`로 별도 표기하고 Depone 검증 대상이 아님을 주석). `require_ready=True`이고 `decision!="pass"`면 `PreflightError("ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE")`. **미지 runner_kind는 여기서 통과시키지 않는다** — capability는 어댑터명 화이트리스트로만.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: adapter preflight (non-launching capability, fail-closed on unavailable)`

---

## Task 4: Claude Code / OpenCode 어댑터 — runner_kind="manual" (VALID_RUNNERS 확장 전)

**Files:**
- Create: `witnessd/adapters/claude.py`, `witnessd/adapters/opencode.py`
- Test: `tests/test_claude_opencode_adapter.py`

두 어댑터는 각 CLI를 subprocess로 호출하고 `AdapterResult(runner_kind="manual")`로 반환한다. **`"manual"`은 Depone `VALID_RUNNERS`에 이미 존재**하므로 receipt는 즉시 valid — provenance만 `invocation`에 어느 CLI였는지 남긴다. A-등급(`claude-code`/`opencode` runner_kind) 승격은 Depone contract PR(W4 밖)이 게이트.

- [ ] **Step 1: 실패 테스트** — 가짜 CLI 스크립트 주입으로 회귀.
```python
import unittest, tempfile, pathlib, stat
from witnessd.adapters.claude import run_claude_lane
from witnessd.adapters.opencode import run_opencode_lane
from depone.agent_fabric.paired_run import validate_runner_receipt

def _fake_cli(dir_, name):
    p = pathlib.Path(dir_) / name
    p.write_text('#!/bin/sh\necho ran >&2\nexit 0\n'); p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)

class TestClaudeOpenCode(unittest.TestCase):
    def _check(self, res, cli_name):
        self.assertEqual(res.runner_kind, "manual")
        self.assertTrue(any(cli_name in tok for tok in res.invocation))
        r = res.to_runner_receipt(arm="direct", task_id="t", worktree="/tmp",
                                  started_at="2026-07-01T00:00:00Z", ended_at="2026-07-01T00:00:01Z")
        self.assertEqual(validate_runner_receipt(r), [])
    def test_claude(self):
        with tempfile.TemporaryDirectory() as s, tempfile.TemporaryDirectory() as b:
            self._check(run_claude_lane(sandbox=s, prompt="x", claude_binary=_fake_cli(b, "claude"),
                                        transcript_path=s + "/../t.txt".replace("..", b)), "claude")
    def test_opencode(self):
        with tempfile.TemporaryDirectory() as s, tempfile.TemporaryDirectory() as b:
            self._check(run_opencode_lane(sandbox=s, prompt="x", opencode_binary=_fake_cli(b, "opencode"),
                                          transcript_path=b + "/t.txt"), "opencode")
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `run_claude_lane(*, sandbox, prompt, claude_binary="claude", transcript_path, log_path=None, timeout_seconds=120) -> AdapterResult`: invocation은 해당 CLI의 non-interactive 실행 형태(예: `[claude, "-p", prompt]` — 실제 플래그는 설치된 CLI `--help`로 확인해 확정하고, 미설치면 `ERR_CLAUDE_UNAVAILABLE`), `runner_kind="manual"`, adapter="claude". `run_opencode_lane`도 동형(adapter="opencode", `ERR_OPENCODE_UNAVAILABLE`). Codex 어댑터와 동일한 touched-files diff + `command_receipts` + `test_output={"status":"not-run"}` 패턴. 두 파일은 Codex 어댑터의 공통 실행 헬퍼를 import해 재사용(3줄 이상 중복이면 `adapters/base.py`에 `_run_cli_lane` 하나로; 그 이하면 인라인).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: Claude Code + OpenCode adapters (runner_kind=manual until Depone VALID_RUNNERS extension)`

---

## Task 5: 모델 라우터 (`router.py`, M8) — solved abstraction, silent death 금지

**Files:**
- Create: `witnessd/router.py`
- Test: `tests/test_router.py`

quick/agentic/frontier tier 라우팅 + `model_not_supported` 시 다음 후보로 **재시도** + per-task **concurrency key** + 후보 소진 시 **graceful degradation** 계약. 라우팅 실패가 재시도·degradation을 소진하면 **silent stop이 아니라 명시적 blocked**로 lane을 종료하고 각 시도를 runlog event로 남긴다.

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.router import route_model, RouteExhaustedError, TIER_CANDIDATES

class TestRouter(unittest.TestCase):
    def test_returns_first_supported(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            dec = route_model(task_id="t", tier="agentic", log=log,
                              is_supported=lambda m: True)
            self.assertEqual(dec["model"], TIER_CANDIDATES["agentic"][0])
            self.assertEqual(dec["concurrency_key"], "t:agentic")
    def test_retry_on_model_not_supported_then_degrade(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            calls = []
            dec = route_model(task_id="t", tier="frontier", log=log,
                              is_supported=lambda m: calls.append(m) or (m == TIER_CANDIDATES["frontier"][-1]))
            self.assertEqual(dec["model"], TIER_CANDIDATES["frontier"][-1])
            self.assertTrue(dec["degraded"])   # fell below top candidate
            kinds = [e["event"] for e in _read(log)]
            self.assertIn("model_not_supported", kinds)   # each rejection recorded, not silent
    def test_exhausted_raises_blocked_not_silent(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            with self.assertRaises(RouteExhaustedError):
                route_model(task_id="t", tier="quick", log=log, is_supported=lambda m: False)
            self.assertIn("route_blocked", [e["event"] for e in _read(log)])
```
(`_read` = jsonl 로더 헬퍼는 테스트 상단에 정의.)
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `TIER_CANDIDATES = {"quick": [...], "agentic": [...], "frontier": [...]}`(각 tier의 우선순위 모델 리스트; 상위→하위 fallback). `route_model(*, task_id, tier, log, is_supported, concurrency_key=None) -> dict`: `tier` 미지 → `ValueError`. 후보를 순서대로 `is_supported(model)` 검사, 실패마다 `log.append({"kind":"witnessd-runlog-event","event":"model_not_supported","task_id":task_id,"tier":tier,"model":model})`(각 거부를 **명시 기록** — silent 금지; §6.0.3 정본 키 `event`). 첫 supported 반환 dict `{"model", "tier", "concurrency_key": concurrency_key or f"{task_id}:{tier}", "degraded": (선택된 후보가 0번이 아님), "attempts": [...]}`. 전부 실패면 `log.append({... "event":"route_blocked", "reason":"model_not_supported_exhausted"})` 후 `RouteExhaustedError("ERR_WITNESSD_ROUTE_EXHAUSTED")`. **usage/토큰 수치를 여기서 만들지 않는다**(라우팅은 메타데이터만). concurrency key는 오케스트레이터가 동일 key lane의 동시 실행을 직렬화하는 데 쓴다(실제 직렬화는 Task 9).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: model router M8 (tier routing + retry + graceful degrade + explicit blocked, no silent death)`

---

## Task 6: 비용 서킷브레이커 (`budget.py`, M10) — 하드 상한 + delegation depth/spend 예산

**Files:**
- Create: `witnessd/budget.py`
- Test: `tests/test_budget.py`

per-task 토큰·달러 **예측**을 예산에서 차감하고 **실측**을 같은 runlog에 기록(§6.5.5). per-task 하드 상한 초과 또는 delegation 트리의 depth/spend 예산 초과 → **즉시 하드 중단**(`ERR_WITNESSD_BUDGET_EXCEEDED`), `budget_exceeded{metric,limit,observed}` event, 하위 spawn 0. 자동 상향 경로 없음(재개는 명시적 `--budget` 상향).

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.budget import CostBreaker, BudgetExceededError

class TestBudget(unittest.TestCase):
    def _mk(self, d, **kw):
        return CostBreaker(log=EventLog(os.path.join(d, "runlog.jsonl")), **kw)
    def test_charge_records_measured_spend(self):
        with tempfile.TemporaryDirectory() as d:
            b = self._mk(d, max_tokens=1000, max_usd=1.0, max_depth=3)
            b.charge(task_id="t", tokens=100, usd=0.1)   # measured
            self.assertEqual(b.spent_tokens, 100)
    def test_predict_over_hard_cap_blocks_before_spawn(self):
        with tempfile.TemporaryDirectory() as d:
            b = self._mk(d, max_tokens=1000, max_usd=1.0, max_depth=3)
            with self.assertRaises(BudgetExceededError) as cm:
                b.check_can_spawn(task_id="t", predicted_tokens=2000, predicted_usd=0.1, depth=1)
            self.assertEqual(cm.exception.metric, "tokens")
    def test_depth_budget_rejects_deep_spawn(self):
        with tempfile.TemporaryDirectory() as d:
            b = self._mk(d, max_tokens=10**9, max_usd=10**9, max_depth=2)
            with self.assertRaises(BudgetExceededError) as cm:
                b.check_can_spawn(task_id="t", predicted_tokens=1, predicted_usd=0.0, depth=3)
            self.assertEqual(cm.exception.metric, "depth")
    def test_exceed_emits_budget_exceeded_event(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            b = CostBreaker(log=log, max_tokens=50, max_usd=1.0, max_depth=3)
            try:
                b.check_can_spawn(task_id="t", predicted_tokens=100, predicted_usd=0.0, depth=1)
            except BudgetExceededError:
                pass
            import json
            evs = [json.loads(l) for l in open(os.path.join(d, "runlog.jsonl"))]
            self.assertIn("budget_exceeded", [e["event"] for e in evs])
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `BudgetExceededError(Exception)`: `code="ERR_WITNESSD_BUDGET_EXCEEDED"`, `.metric`(`"tokens"|"usd"|"depth"`), `.limit`, `.observed`. `CostBreaker`: 생성자 `log, max_tokens, max_usd, max_depth`; 상태 `spent_tokens`/`spent_usd`(누적). `check_can_spawn(*, task_id, predicted_tokens, predicted_usd, depth)`: `depth > max_depth`, 또는 `spent_tokens+predicted_tokens > max_tokens`, 또는 `spent_usd+predicted_usd > max_usd`면 해당 metric으로 `log.append({"kind":"witnessd-runlog-event","event":"budget_exceeded","task_id":task_id,"metric":m,"limit":lim,"observed":obs})` 후 `BudgetExceededError`. `charge(*, task_id, tokens, usd)`: **실측만** 누적하고 `log.append({... "event":"spend_measured", "tokens":tokens, "usd":usd})`. 예측 없이 조용히 청구되지 않도록 charge는 항상 이벤트를 남긴다. **토큰 수를 발명하지 않는다** — `charge`는 어댑터가 실제 report한 값만 받는다(없으면 호출 안 함).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: cost circuit breaker M10 (per-task hard cap + delegation depth/spend budget, hard stop)`

---

## Task 7: 상태 격리 (`state.py`) — 별도 state dir + lock, OMX/LazyCodex 오염 차단

**Files:**
- Create: `witnessd/state.py`
- Test: `tests/test_state_isolation.py`

§8.2-4 오픈결정의 **기본값을 확정 채택**: witnessd는 자기 상태를 오직 `<root>/.witnessd/`(runlog·session·worktree 네임스페이스) + `fcntl.flock` 파일 lock에만 두고, Codex 어댑터 spawn 시 격리된 `CODEX_HOME`/전용 config env를 주입한다. 시작 시 `witnessd doctor`가 외부 도구(OMX/LazyCodex) 활성 store가 witnessd worktree/락과 겹치는지 검사해 겹치면 dispatch 거부(`ERR_WITNESSD_STATE_CONTENTION`, §6.4.3).

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os, json
from witnessd.state import StateNamespace, detect_state_contention, StateContentionError

class TestState(unittest.TestCase):
    def test_only_writes_own_namespace(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as omx:
            before = set(os.listdir(omx))
            with StateNamespace(root) as ns:
                self.assertTrue(ns.runlog_path.startswith(os.path.join(root, ".witnessd")))
                env = ns.codex_env(base_env={"HOME": omx})
                self.assertNotEqual(env["CODEX_HOME"], omx)  # isolated, not external store
                self.assertTrue(env["CODEX_HOME"].startswith(root))
            self.assertEqual(set(os.listdir(omx)), before)  # external store untouched
    def test_lock_is_exclusive(self):
        with tempfile.TemporaryDirectory() as root:
            with StateNamespace(root):
                with self.assertRaises(StateContentionError):
                    StateNamespace(root).__enter__()   # second holder blocked
    def test_doctor_detects_overlap(self):
        with tempfile.TemporaryDirectory() as root:
            wt = os.path.join(root, "wt")
            errs = detect_state_contention(witnessd_worktree=wt,
                                           external_active_worktrees=[wt])  # same path claimed
            self.assertIn("ERR_WITNESSD_STATE_CONTENTION", errs[0])
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `StateNamespace(root)`: context manager. `__enter__`가 `<root>/.witnessd/`를 mkdir, `<root>/.witnessd/lock`을 열어 `fcntl.flock(fd, LOCK_EX|LOCK_NB)` — 실패하면 `StateContentionError("ERR_WITNESSD_STATE_CONTENTION")`. 속성 `runlog_path=<root>/.witnessd/runlog.jsonl`, `session_dir`, `worktree_root`. `codex_env(base_env)`: `base_env` 복사 후 `CODEX_HOME=<root>/.witnessd/codex-home`(격리; 절대 외부 HOME 상속 안 함) 주입해 반환 — Codex 어댑터가 OMX/LazyCodex store와 물리 분리. `__exit__`는 flock 해제. `detect_state_contention(*, witnessd_worktree, external_active_worktrees) -> list[str]`: witnessd worktree가 외부 활성 worktree와 경로 겹치면(`os.path.commonpath`) `"ERR_WITNESSD_STATE_CONTENTION: <path>"` 리스트 반환(겹침 없으면 `[]`). **witnessd SoT는 hash-chained runlog 하나뿐**이므로 외부 mutable JSON은 witnessd 상태로 전파 경로가 없다(주석).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: state isolation (dedicated .witnessd namespace + flock, OMX/LazyCodex contention doctor)`

---

## Task 8: 라우팅 메타 → 정적 OTel span (usage 날조 금지) — substrate 소비 확인

**Files:**
- Create: `tests/test_routing_otel.py`
- (구현 없음 — W1 `build_bundle`가 Depone `build_otel_genai_spans`를 쓰도록 이미 배선됨을 **회귀로 고정**하고, 라우팅 메타가 정적 span shape을 만족함을 assert)

W4는 라우팅 정보를 evidence 번들에 **정적 span**으로만 싣고 토큰/usage를 발명하지 않는다. Depone `build_otel_genai_spans`는 이미 usage를 발명하지 않는 정본이므로, W4는 (a) 어댑터 runner-receipt의 `runner_kind`/`arm`이 span에 반영되고, (b) `validate_external_otel_spans`가 `[]`임을 회귀로 못 박는다.

- [ ] **Step 1: 실패 테스트**
```python
import unittest
from depone.agent_fabric.evidence_substrate import build_otel_genai_spans, validate_external_otel_spans

class TestRoutingOtel(unittest.TestCase):
    def test_static_spans_carry_runner_kind_no_usage_invented(self):
        manifest = {"assurance": "A1-local-observed", "decision": "A1",
                    "observer_capture": {"command_receipts": [{"command": ["sh","-c","true"], "exit_code": 0, "status": "passed"}]}}
        receipt = {"runner_kind": "codex-cli", "arm": "direct"}
        spans = build_otel_genai_spans(manifest, runner_receipt=receipt)
        self.assertEqual(validate_external_otel_spans(spans), [])
        root = spans[0]["attributes"]
        self.assertEqual(root["gen_ai.agent.name"], "codex-cli")
        # no fabricated usage/token counts anywhere
        for s in spans:
            self.assertNotIn("gen_ai.usage.input_tokens", s["attributes"])
            self.assertNotIn("gen_ai.usage.output_tokens", s["attributes"])
```
- [ ] **Step 2: 실패 확인 → 통과 확인** — `python3 -m unittest tests.test_routing_otel -v`. (구현 변경 없이 통과해야 정상; 실패하면 W1 `substrate.build_bundle`가 `build_otel_genai_spans`를 **정적으로만** 호출하는지, W4 어댑터가 usage 필드를 주입하지 않는지 점검. Depone `build_otel_genai_spans` 실제 반환 attribute 키로 assertion을 맞춘다.)
- [ ] **Step 3: 라우팅 메타 결선(선택 필드)** — 어댑터 lane emit 경로에서 route decision(`tier`/`model`/`degraded`)을 **runlog event**로만 남기고, 번들 span에는 Depone builder가 생성하는 정적 attribute 외에 usage를 추가하지 않음을 코드 리뷰로 확인(별도 구현 아님).
- [ ] **Step 4: Commit** — `test: routing metadata as static OTel spans (no usage fabrication regression)`

---

## Task 9: 어댑터 lane 오케스트레이터 (`adapter_run.py`) — preflight→격리→router→budget→adapter→W1 emit (단조성)

**Files:**
- Create: `witnessd/adapter_run.py`
- Test: `tests/test_adapter_run.py`

한 어댑터 lane의 전 경로를 배선한다: (1) `StateNamespace`로 격리 + lock, (2) `probe_adapter_capability(require_ready=True)`, (3) `route_model`(concurrency key로 동일 key lane 직렬화), (4) `CostBreaker.check_can_spawn`, (5) 어댑터 실행(Codex/Claude/OpenCode), (6) `assert_separated`(observer 경로가 sandbox 밖), (7) W1 `build_observer_capture`+`build_capture_manifest`+`build_runner_receipt`(어댑터 runner_kind)+`build_bundle`+`emit_lane_evidence`. 각 단계 event는 `EventLog` 경유(SoT 유일 쓰기). **route 소진 → blocked, budget 초과 → 하드 중단**을 여기서 명시 상태로 종료(silent stop 금지).

- [ ] **Step 1: 실패 테스트** — 가짜 codex + 낮은 예산/미지원 모델 주입으로 3경로(정상/route-degrade/budget-blowout) 회귀.
```python
import unittest, tempfile, os, json
from witnessd.adapter_run import run_adapter_lane, LaneBlocked
from depone.agent_fabric.paired_run import validate_runner_receipt

class TestAdapterRun(unittest.TestCase):
    def test_happy_path_emits_valid_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            out = run_adapter_lane(root=root, adapter="codex", task_id="t", prompt="do X",
                                   arm="direct", tier="agentic",
                                   is_supported=lambda m: True,
                                   budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                                   codex_binary=_fake_codex(root))   # helper as in Task 2
            self.assertEqual(validate_runner_receipt(out["runner_receipt"]), [])
            self.assertEqual(out["status_axis"]["assurance"], "evidence-pending")  # pre-Depone
    def test_route_exhausted_ends_blocked_not_silent(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(LaneBlocked) as cm:
                run_adapter_lane(root=root, adapter="codex", task_id="t", prompt="x",
                                 arm="direct", tier="quick", is_supported=lambda m: False,
                                 budget={"max_tokens": 10**9, "max_usd": 10**9, "max_depth": 3},
                                 codex_binary=_fake_codex(root))
            self.assertEqual(cm.exception.reason, "route_blocked")
            # runlog shows model_not_supported + route_blocked, never a success string
            evs = [json.loads(l) for l in open(os.path.join(root, ".witnessd", "runlog.jsonl"))]
            self.assertNotIn("VERIFIED", json.dumps(evs))
    def test_budget_blowout_hard_stops(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(LaneBlocked) as cm:
                run_adapter_lane(root=root, adapter="codex", task_id="t", prompt="x",
                                 arm="direct", tier="agentic", is_supported=lambda m: True,
                                 budget={"max_tokens": 1, "max_usd": 1.0, "max_depth": 3},
                                 predicted_tokens=10**6, codex_binary=_fake_codex(root))
            self.assertEqual(cm.exception.reason, "budget_exceeded")
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `run_adapter_lane(*, root, adapter, task_id, prompt, arm, tier, is_supported, budget, predicted_tokens=0, predicted_usd=0.0, depth=1, codex_binary="codex", ...) -> dict`: `StateNamespace(root)` 진입 → `EventLog(ns.runlog_path)`. preflight `require_ready=True`(실 CLI; 테스트는 가짜 binary라 pass); `route_model` `RouteExhaustedError`→`LaneBlocked(reason="route_blocked")`; `CostBreaker.check_can_spawn` `BudgetExceededError`→`LaneBlocked(reason="budget_exceeded")`. 어댑터 dispatch(`adapter`별 `run_*_lane`, Codex는 `ns.codex_env` 주입), transcript/log는 `ns` 아래 observer-owned 경로. `assert_separated(runner_sandbox=worktree, out_path=capture_out)`. 이후 W1 `emit_lane_evidence`(observer_capture=`build_observer_capture(command_receipts=adapter_result.command_receipts, touched_files=adapter_result.touched_files, allowed_touched_files=<ownership claim이 반환한 region>, test_output=adapter_result.test_output)` — W1 Task 6 정의(키워드 4개)에 맞춰 명시 언팩, `adapter_result` 통째 전달 금지; runner_receipt=`adapter_result.to_runner_receipt(...)`). 반환 dict `{"runner_receipt", "capture_manifest", "bundle_path", "status_axis": {"assurance": render_status(pending=1, verdict=None) 기반 'evidence-pending', "lifecycle": "active"}}`. `LaneBlocked`는 `render_status`가 성공 문자열을 내지 못하게 STATUS_DOMAIN 값(`blocked`/`evidence-pending`)만 파생. **단조성**: emit된 capture는 W1 validator 통과(별도 assert는 Task 12 revalidate).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: adapter lane orchestrator (preflight→isolate→route→budget→adapter→W1 emit, explicit blocked)`

---

## Task 10: CLI — `witnessd run --adapter` / `route` / `doctor` / `faultkit budget-blowout`

**Files:**
- Modify: `witnessd/__main__.py`
- Test: `tests/test_cli_w4.py`

- [ ] **Step 1: 실패 테스트** — `witnessd run --adapter codex --root <R> --task t --arm direct --tier agentic -- "do X"`가 evidence 방출(가짜 codex로); `witnessd doctor --root <R> --external-worktree <R>`가 겹침이면 `ERR_WITNESSD_STATE_CONTENTION` non-zero exit; `witnessd faultkit budget-blowout --root <R> --max-tokens 1`이 spawn 0 + `budget_exceeded` event + non-zero exit; 어떤 출력도 `VERIFIED`/`COMPLETE` 성공 문자열 없음(`render_status`/`STATUS_DOMAIN` 경유만).
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — argparse 서브커맨드 추가: `run --adapter {codex,claude,opencode}`은 Task 9 `run_adapter_lane` 배선; `route`는 Task 5 `route_model`을 dry-run(모델 후보/degrade 출력); `doctor`는 Task 7 `detect_state_contention`(+ preflight capability 요약) — 겹침이면 exit 3; `faultkit budget-blowout`은 낮은 상한으로 `run_adapter_lane`을 호출해 `LaneBlocked(reason="budget_exceeded")`를 재현하고 `paused` 서사 출력(spawn 0 assert). 모든 상태 출력은 W1 `render_status`. `self-test --all`에 W4 새 모듈 `_self_test()` 포함(Task 11).
- [ ] **Step 4: 통과 확인** — PASS. 수동: `python3 -m witnessd run --adapter codex ...`로 evidence_dir 파일 생성 확인.
- [ ] **Step 5: Commit** — `feat: witnessd CLI W4 (run --adapter / route / doctor / faultkit budget-blowout)`

---

## Task 11: 각 W4 모듈 `_self_test()` (G1 편입)

**Files:**
- Modify: `witnessd/adapters/base.py`, `witnessd/adapters/codex.py`, `witnessd/router.py`, `witnessd/budget.py`, `witnessd/state.py`, `witnessd/preflight.py`
- Test: `tests/test_selftest_w4.py`

- [ ] **Step 1: 실패 테스트** — `from witnessd.router import _self_test` 등 각 모듈 `_self_test()`가 예외 없이 실행되고, `witnessd self-test --all`이 W4 모듈을 포함해 `N/N passed` exit 0.
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — 각 모듈에 부수효과 없는 `_self_test()` 추가: base(runner_kind 매핑⊆VALID_RUNNERS + 미지 거부), router(retry→degrade→exhaust 3분기), budget(tokens/usd/depth 각 초과), state(flock 배타 + doctor 겹침), preflight(codex 미설치 blocked receipt valid), codex(가짜 binary invocation shape). `self-test --all` 러너에 등록.
- [ ] **Step 4: 통과 확인** — `python3 -m witnessd self-test --all` → `N/N passed` exit 0.
- [ ] **Step 5: Commit** — `test: W4 module self-tests wired into self-test --all`

---

## Task 12: W4 fixtures committed (codex receipt / manual receipts / route-degrade / budget-blowout / state-isolation)

**Files:**
- Create: `fixtures/w4/runner-receipt-codex.json`, `runner-receipt-claude-manual.json`, `runner-receipt-opencode-manual.json`, `route-degrade.jsonl`, `budget-blowout.jsonl`, `bundle-codex.json`, `state-isolation/` (mock OMX store + witnessd 네임스페이스 스냅샷), `keys/operator.pub`

- [ ] **Step 1: 생성 — Codex 실 lane(Acceptance 1)** — codex CLI 설치 호스트면 `witnessd run --adapter codex`로 실제 lane 실행해 `runner-receipt-codex.json`(+`bundle-codex.json`) committed. 미설치면 가짜 codex 대신 **manual arm으로라도 codex invocation shape을 남기되 receipt는 실측 exit_code**로 방출(위조 금지); 이 경우 fixture 헤더 주석에 "codex not installed on capture host, invocation recorded, exit_code measured"를 남긴다.
- [ ] **Step 2: Claude/OpenCode fixtures(Acceptance 2)** — 각 어댑터로 `runner_kind="manual"` receipt 방출, `validate_runner_receipt==[]`.
- [ ] **Step 3: route-degrade / budget-blowout(Acceptance 3/4)** — `witnessd faultkit`로 `model_not_supported` 주입 lane과 낮은 상한 lane을 실행해 `route-degrade.jsonl`(model_not_supported…→route_blocked, 성공 문자열 없음)·`budget-blowout.jsonl`(budget_exceeded + spawn 0) committed.
- [ ] **Step 4: state-isolation(Acceptance 5)** — mock OMX/LazyCodex store 디렉터리 + `witnessd doctor` 실행 후 witnessd가 자기 `.witnessd/` 네임스페이스만 썼고 mock store가 unchanged임을 담은 스냅샷 committed.
- [ ] **Step 5: 커밋** — private key 커밋 금지(`.gitignore`), 공개키만. `git add fixtures/w4 && git commit -m "test: W4 committed fixtures (codex receipt, manual receipts, route-degrade, budget-blowout, state-isolation)"`

---

## Task 13: `scripts/revalidate_w4.py` (G2 — Depone 재도출 + 단조성)

**Files:**
- Create: `scripts/revalidate_w4.py`

- [ ] **Step 1: 작성** — 설치된 Depone validator로 committed fixture 바이트에서 재도출, 전부 assert 후 exit 0:
```python
import json, sys
from depone.agent_fabric.paired_run import validate_runner_receipt, VALID_RUNNERS
from depone.agent_fabric.evidence_substrate import validate_external_otel_spans, ingest_signed_evidence_bundle
# from depone.agent_fabric.codex_local_capability import validate_codex_local_capability  # if capability fixture emitted
def _load(p): return json.load(open(p))
# Acceptance 1: codex receipt valid, runner_kind codex-cli in VALID_RUNNERS
rc = _load("fixtures/w4/runner-receipt-codex.json")
assert validate_runner_receipt(rc) == [], validate_runner_receipt(rc)
assert rc["runner_kind"] in VALID_RUNNERS
# Acceptance 2: manual receipts valid
for f in ("runner-receipt-claude-manual.json", "runner-receipt-opencode-manual.json"):
    r = _load(f"fixtures/w4/{f}"); assert validate_runner_receipt(r) == []; assert r["runner_kind"] == "manual"
# Acceptance 3: route-degrade ends blocked, no success string, model_not_supported recorded
evs = [json.loads(l) for l in open("fixtures/w4/route-degrade.jsonl")]
kinds = [e["event"] for e in evs]
assert "model_not_supported" in kinds and "route_blocked" in kinds
assert "VERIFIED" not in json.dumps(evs) and "COMPLETE" not in json.dumps(evs)
# Acceptance 4: budget-blowout hard stop, spawn 0
bevs = [json.loads(l) for l in open("fixtures/w4/budget-blowout.jsonl")]
assert "budget_exceeded" in [e["event"] for e in bevs]
assert "spawn" not in [e.get("event") for e in bevs]  # no spawn after breaker
# OTel: routing meta static, usage not fabricated
bundle = _load("fixtures/w4/bundle-codex.json")
spans = bundle.get("otel_spans") or bundle.get("predicate", {}).get("otel_spans")  # confirm exact path from W1 substrate.build_bundle
assert validate_external_otel_spans(spans) == []
# 단조성: bundle ingest still re-derives (W1 validator) — signature_verified True
# res = ingest_signed_evidence_bundle(bundle, pub, artifact_paths); assert res.signature_verified
print("W4 revalidate: PASS"); sys.exit(0)
```
정확한 함수 반환형·`otel_spans` 경로는 실제 Depone 코드(`evidence_substrate.py`)와 W1 `substrate.build_bundle`로 확정.
- [ ] **Step 2: 실행** — `python3 scripts/revalidate_w4.py` → `W4 revalidate: PASS`, exit 0.
- [ ] **Step 3: 커밋** — `test: revalidate_w4 re-derives adapter/route/cost verdicts from committed bytes via Depone`

---

## Task 14: negative/tamper 회귀 fixtures (위조 차단)

**Files:**
- Create: `fixtures/w4/negative/{forged_runner_kind,empty_invocation,source_hash_mismatch,fabricated_usage,budget_bypass}.json`
- Modify: `scripts/revalidate_w4.py`(각 변형이 검출됨을 assert)

- [ ] **Step 1: 실패 테스트(회귀)** — revalidate가 각 tamper 변형을 검출:
  - `forged_runner_kind`(`"claude-code"` 등 미지) → `validate_runner_receipt != []`(`runner_kind must be one of [...]`).
  - `empty_invocation`(`invocation: []`) → `validate_runner_receipt != []`.
  - `source_hash_mismatch`(`source_hashes.receipt` 변조) → witnessd 재계산 `canonical_hash(receipt-without-source_hashes)`와 불일치 검출(§4.6).
  - `fabricated_usage`(OTel span에 `gen_ai.usage.output_tokens` 주입) → 정책 위반으로 revalidate가 reject(정적 span에 usage 없음 assert).
  - `budget_bypass`(budget-blowout 로그에서 `budget_exceeded` 제거 후 spawn event 추가) → 예산 event 없이 spawn 존재를 revalidate가 blocked로 판정.
- [ ] **Step 2: 통과 확인** — `python3 scripts/revalidate_w4.py` 재실행 exit 0(모든 변형이 검출됨).
- [ ] **Step 3: 커밋** — `test: W4 negative/tamper regression (forged runner_kind, empty invocation, source-hash mismatch, fabricated usage, budget bypass)`

---

## Task 15: 공통 게이트 G1/G2/G3 통과 + 단조성 회귀 + W4 완료

- [ ] **Step 1: G1** — `python3 -m witnessd self-test --all` → `N/N passed` exit 0(W4 새 모듈 `adapters.base`/`adapters.codex`/`router`/`budget`/`state`/`preflight` `_self_test()` 포함).
- [ ] **Step 2: G2 + 단조성** — `python3 scripts/revalidate_w4.py` → `W4 revalidate: PASS` exit 0. **회귀 없음(단조성)**: `python3 scripts/revalidate_w1.py`, `python3 scripts/revalidate_w2.py`, `python3 scripts/revalidate_w3.py` 여전히 PASS — W4 어댑터 capture가 W1 `validate_capture_manifest`+`verify_capture_chain`, W2 A2 격리, W3 team-ledger를 그대로 통과.
- [ ] **Step 3: G3** — witnessd가 방출한 evidence를 Depone repo에서 소비: `cd /home/ubuntu/depone-assurance-repair && python scripts/check_contract.py --tier changed && python scripts/dwm.py doctor` red 없음. (Codex runner-receipt를 Depone `validate_runner_receipt`로 소비, 라우팅/비용 event는 witnessd-side runlog이므로 Depone 계약 변경 없음 — Claude/OpenCode의 A-등급 승격만 Depone `VALID_RUNNERS` 확장 contract PR로 별도 게이트.)
- [ ] **Step 4: W4 데모(서사)** — OMX/LazyCodex 동시실행을 mock한 상태에서 witnessd Codex 어댑터가 (a) 자기 네임스페이스만 쓰고, (b) `model_not_supported` 주입 시 blocked로 명시 종료, (c) 낮은 상한에서 하드 중단됨을 보여주는 짧은 스크립트 커밋(성공 문자열 없음, 전부 `evidence-pending`/`blocked`/`paused`).
- [ ] **Step 5: 커밋 + W4 종료** — `git commit -m "feat: W4 complete — multi-adapter runner-receipts, solved routing, cost circuit breaker, state isolation"`

---

## Self-review 체크 (작성자 수행)

- **Spec 커버리지(§5.4):** 어댑터 확장 Codex=Task2/Claude·OpenCode=Task4(모두 W1 `build_runner_receipt` 수렴=Task1), preflight §6.5.1=Task3, 모델 라우팅 M8(quick/agentic/frontier+model_not_supported 재시도+concurrency key+graceful degradation, silent death 금지)=Task5, 비용 서킷브레이커 M10(예측+하드 상한+depth/spend 예산)=Task6, 상태 격리(§8.2-4 기본값 확정, OMX/LazyCodex 오염 차단)=Task7, 라우팅 메타 정적 OTel(usage 날조 금지)=Task8, 오케스트레이터+단조성=Task9, CLI(run/route/doctor/faultkit)=Task10, self-test=Task11, Acceptance Bar 1~6=Task12/13/14/15. 모두 Task 존재.
- **runner_kind 계약(cross-repo):** Codex=`codex-cli`(즉시 검증), Claude/OpenCode=`manual`(VALID_RUNNERS 확장 전) — 확장은 Depone repo 별도 contract PR로 게이트하며 W4 범위 밖임을 header 불변식·Task1·Task15-G3에 명시. 미지 runner_kind 위조 차단=Task1 `assert_runner_kind_valid` + Task14 negative fixture.
- **Placeholder 없음:** 계약-바인딩 필드는 "Depone 파일에서 확정"으로 정확히 지시(`build_runner_receipt`/`validate_runner_receipt`/`VALID_RUNNERS`/`build_codex_local_capability`/`build_otel_genai_spans` 실제 시그니처는 본문에 나열), determinate 코드(router/budget/state 로직)는 전량 제시. "TBD"/"적절히 처리" 없음.
- **불변식:** 단조성(Task15-G2), assurance 상한 A2(라우팅/비용은 assurance 축 미상향 — evidence-pending/blocked/paused만), Evidence Emitter만 SoT 쓰기(모든 route/budget event가 `EventLog` 경유), fail-closed(route 소진→blocked, 예산 초과→하드 중단, 미지 runner_kind→invalid), usage 날조 금지(Task8/Task14).
- **재사용/의존:** W1 `canonical_hash`/`EventLog`/`render_status`/`assert_separated`/`build_observer_capture`/`build_runner_receipt`(runner_kind 파라미터)/`build_bundle`/`emit_lane_evidence` + W3 worktree/fanin 위에만 얹음(재정의 없음). 새 함수는 `run_codex_lane`/`run_claude_lane`/`run_opencode_lane`/`probe_adapter_capability`/`route_model`/`CostBreaker`/`StateNamespace`/`run_adapter_lane`.
- **오픈결정 반영:** §8.2-4(첫 어댑터 상태 격리 메커니즘) 기본값을 "별도 `.witnessd/` state 디렉터리 네임스페이스 + `fcntl.flock` + 격리 `CODEX_HOME`"으로 확정 채택(Task7). §8.2-1(계약 패키지 추출)/8.2-2(docker 1급)는 W4 범위 밖 — 후속 트리거 대기.
