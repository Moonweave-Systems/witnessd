# W5 — 자동 학습 캡처 + hard pause/interrupt + kill-switch/atomic installer (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (권장) 또는 superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** "aggressive autonomy가 신뢰 리스크가 아니다"의 마지막 조각을 채운다(§5.5). (1) 반복 교정을 **provenance(어느 run·어느 capture·어느 승인 event)와 함께 버전드 delta로 승격**하되 승인·증거 포인터가 없으면 `blocked`(M9). (2) 사용자 `wait`/`stop`이 **즉시 모든 continuation hook·auto-retry·auto-spawn을 중단**하고 명시적 `resume --confirm`만 재개하며 auto-continuation이 pause를 override할 코드 경로가 존재하지 않는다(M6, OMO `todo-continuation-enforcer` #89 안티회귀). (3) `witnessd kill --all`이 전체 harness의 모든 자식을 확실히 종료시켜 heartbeat 파생 상태가 전부 `dead`가 되고, install/upgrade는 원자적이며 unreadable config를 **덮어쓰지 않고 fail-safe 중단**하고 orphan shim을 남기지 않는다(M11). 완료 정의는 witnessd의 self-report가 아니라 (a) 별도 repo Depone(비실행 검증기)의 `ingest_signed_evidence_bundle`가 학습 승격 아티팩트를 provenance 포함해 재도출하는 것, (b) committed 회귀 fixture(`fixtures/w5/*`)에서 pause-override·kill-all·unapproved-delta-blocked·installer fail-safe가 재도출되는 것이다.

**Architecture:** Python 3.10+ 표준 라이브러리만. 모든 상태전이(user_pause/user_resume/kill/learning_delta/install)는 W1 `EventLog`(SoT 유일 쓰기 지점)의 **runlog 체인**(`prev_event_hash`+`event_hash`, kind `witnessd-runlog-event`, §6.0.3)에 append되고, pause 상태·liveness는 그 로그의 **pure projection**이다(별도 mutable 플래그 파일 금지 — 저장 플래그로 pause/active를 뒤집는 경로가 없어야 안티회귀가 구조적으로 성립). pause/kill/learning 이벤트도 W1 DSSE·체인을 그대로 재사용해 서명 로그 event로 남아 사후 감사 가능(§5.5 방출/검증). 학습 delta는 W1 capture-manifest 체인의 canonical hash를 provenance 포인터로 참조하고, 승격 시 W1 `build_bundle`(in-toto Statement v1 + DSSE)로 감싸 Depone `ingest_signed_evidence_bundle`가 소비한다. 검증(assurance 재도출·서명 검증·ingest)은 전적으로 Depone이 한다 — witnessd는 검증기 역할을 겸하지 않는다.

**Tech Stack:** Python stdlib(`json`, `hashlib`, `os`, `signal`, `subprocess`, `pathlib`, `time`, `argparse`, `unittest`, `tempfile`), `openssl` CLI(W1 `signing.py` 재사용). 외부 의존성/`pyproject` 금지.

**계약 근거(정확한 필드는 아래 파일 실제 코드로 확정, 추측 금지):** `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/{evidence_substrate,sign,observer_provenance,capture_bridge,claim_gate}.py`. 확인된 심볼(이미 존재): `claim_gate.canonical_hash`, `evidence_substrate.ingest_signed_evidence_bundle`/`build_intoto_statement_from_capture`, `sign.verify_signed_bundle`/`sign_evidence_bundle`/`SIGNING_STATUS_OPERATOR_KEY`, `observer_provenance.build_signed_trusted_observer_provenance`/`validate_trusted_observer_provenance`. 이 repo는 witnessd와 별개다 — witnessd는 이 함수들을 **재구현하지 않고 그들이 받아들이는 아티팩트를 생산**한다.

**W1~W3에서 그대로 재사용(재정의 금지):** `canonical_hash`(`witnessd/canonical.py`), `EventLog`(`witnessd/eventlog.py`, `prev_event_hash`/`event_hash`), `render_status`/`STATUS_DOMAIN`(`witnessd/status.py`), `assert_separated`/`build_observer_capture`(`witnessd/observer.py`), `run_shell_lane`(`witnessd/adapters/shell.py`), `build_capture_manifest`(`witnessd/capture.py`), `gen_operator_keypair`/`sign_dsse`(`witnessd/signing.py`), `build_runner_receipt`(`witnessd/receipt.py`), `build_bundle`/`build_evidence_contract`(`witnessd/substrate.py`), `emit_lane_evidence`(`witnessd/emitter.py`); W2 `new_run_id`(`witnessd/ids.py`), `append_runlog`/`build_runlog_body`/`event_hash`/`verify_runlog`(`witnessd/runlog.py`), `derive_liveness`/`HEARTBEAT_TTL_SECONDS`(`witnessd/liveness.py`), `WorkerSupervisor`/`WorkerHandle`(`witnessd/supervisor.py`), `Scheduler`(`witnessd/scheduler.py`), `faultkit`(`witnessd/faultkit.py`). **W5 새 함수는 이들 위에 얹고 재정의하지 않는다.**

**불변식(§5.0) — 이 웨이브의 모든 산출물이 상속:**
- 단조성: W5가 방출/링크하는 capture·bundle도 W1 `validate_capture_manifest`/`verify_capture_chain`을 **전부** 통과(Task 12에서 W1~W3 revalidate 그린 재확인).
- assurance 상한 A2(A3 등급 없음). learning delta의 operator DSSE 서명은 assurance를 올리지 않는 report-level 축(§3.1) — 승격되어도 등급 상향 없음.
- worker self-seal 불가, Evidence Emitter/`EventLog`만 SoT 쓰기. pause/kill/learning 승인은 worker가 self-approve 못 함.
- fail-closed(부분점수 없음): 승인·증거 포인터 부재 → blocked, unreadable config → 덮어쓰기 금지·중단, kill 미확정 → active 파생 금지.

---

## File Structure

```
witnessd/
  pause.py           # NEW — derive_pause_state, append_user_pause/resume, assert_not_paused (M6 hard boundary)
  killswitch.py      # NEW — kill_all (전체 harness 정지, kill event, dead 파생) (M11)
  learning.py        # NEW — build_learning_delta, validate_learning_delta_provenance, promote_learning_delta (M9)
  installer.py       # NEW — atomic_install/atomic_upgrade, list_orphan_shims (원자적 install, unreadable=fail-safe) (M11)
  supervisor.py      # MODIFY — spawn 전 guard_continuation(pause 게이트)
  scheduler.py       # MODIFY — schedule 전 guard_continuation(pause 게이트)
  faultkit.py        # MODIFY — pause_race (§6.1.1 SIGINT@200ms 회귀)
  __main__.py        # MODIFY — pause/resume/kill/learn/install/upgrade + faultkit pause-race 서브커맨드
tests/
  test_pause.py, test_continuation_gate.py, test_killswitch.py, test_learning.py,
  test_learning_promote.py, test_installer.py, test_faultkit_pause.py, test_cli_w5.py   # NEW
fixtures/w5/
  pause-override.jsonl                # Acceptance 1 — pause 이후 side-effect 0건
  kill-all.jsonl                      # Acceptance 2 — kill 후 전 lane dead
  capture-for-learning.json           # learning delta가 가리키는 committed capture
  learning-delta.json                 # Acceptance 3 — 승인+증거 포인터 보유
  learning-delta-bundle.json          # 위 delta를 감싼 evidence bundle(Depone ingest 대상)
  keys/operator.pub                   # 공개키만(개인키 커밋 금지)
  negative/learning-delta-no-provenance.json   # 포인터 없음 → blocked
  negative/learning-delta-unapproved.json      # 승인 event 없음 → blocked
  negative/installer-unreadable-config/config.bin  # Acceptance 4 — unreadable config
scripts/
  revalidate_w5.py   # NEW — Depone/witnessd 재도출(G2) + 단조성
```

---

## Task 0: W5 착수 준비 — 이전 웨이브 그린 확인 + 스캐폴드

**Files:**
- Create: `fixtures/w5/.gitkeep`, `fixtures/w5/keys/.gitkeep`, `fixtures/w5/negative/.gitkeep`, `fixtures/w5/negative/installer-unreadable-config/.gitkeep`

- [ ] **Step 1: 이전 웨이브 그린 게이트** (레드면 W5 착수 금지 — §5.0 순서 의존)
```bash
cd /home/ubuntu/witnessd && git checkout -b w5-autonomy-safety
python3 -m witnessd self-test --all && python3 scripts/revalidate_w1.py && python3 scripts/revalidate_w2.py && python3 scripts/revalidate_w3.py && python3 scripts/revalidate_w4.py
```
Expected: self-test `N/N passed` exit 0, `W1/W2/W3/W4 revalidate: PASS` 각각 exit 0.
- [ ] **Step 2: Depone ingest/서명 심볼 import 확인**
```bash
python3 -c "from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle; from depone.agent_fabric.sign import verify_signed_bundle, SIGNING_STATUS_OPERATOR_KEY; from depone.agent_fabric.observer_provenance import build_signed_trusted_observer_provenance; print('depone w5 ok')"
```
Expected: `depone w5 ok`.
- [ ] **Step 3: 스캐폴드 디렉터리 + Commit**
```bash
mkdir -p fixtures/w5/keys fixtures/w5/negative/installer-unreadable-config
touch fixtures/w5/.gitkeep fixtures/w5/keys/.gitkeep fixtures/w5/negative/.gitkeep fixtures/w5/negative/installer-unreadable-config/.gitkeep
git add -A && git commit -m "chore: scaffold W5 (autonomy safety) fixtures"
```

---

## Task 1: hard pause 경계 (M6) — `witnessd/pause.py` (pure projection)

**Files:**
- Create: `witnessd/pause.py`
- Test: `tests/test_pause.py`

pause 상태는 저장 플래그가 아니라 runlog projection이다(§6.1.3). `user_pause`(payload `source ∈ {signal, cli}`)와 `user_resume`(payload `confirm: true`)가 §6.0.3 runlog event로 append되고, pause 상태 = "그 두 종류 중 마지막 이벤트가 `user_pause`". genesis는 not-paused. `assert_not_paused`가 continuation hook의 유일한 게이트이며 pause 상태에서 `ERR_WITNESSD_PAUSED`로 fail-closed. resume은 `confirm=True` 없으면 거부(명시적 재활성화만).

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.pause import (
    derive_pause_state, append_user_pause, append_user_resume,
    assert_not_paused, PauseError, PAUSE_EVENT, RESUME_EVENT,
)

class TestPause(unittest.TestCase):
    def _log(self, d):
        return EventLog(os.path.join(d, "runlog.jsonl"))

    def test_genesis_not_paused(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._log(d)
            append_runlog_spawn = log.append({"kind":"witnessd-runlog-event","event":"spawn"})
            self.assertFalse(derive_pause_state(log.read()))

    def test_pause_then_state_true_and_gate_raises(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._log(d)
            append_user_pause(log, run_id="R1", source="cli")
            recs = log.read()
            self.assertTrue(derive_pause_state(recs))
            with self.assertRaises(PauseError) as cm:
                assert_not_paused(recs)
            self.assertEqual(str(cm.exception), "ERR_WITNESSD_PAUSED")

    def test_resume_requires_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._log(d)
            append_user_pause(log, run_id="R1", source="signal")
            with self.assertRaises(PauseError):
                append_user_resume(log, run_id="R1", confirm=False)   # 명시 재활성화만
            append_user_resume(log, run_id="R1", confirm=True)
            self.assertFalse(derive_pause_state(log.read()))
            assert_not_paused(log.read())   # no raise
```
- [ ] **Step 2: 실패 확인** — `python3 -m unittest tests.test_pause -v` → FAIL (module 없음).
- [ ] **Step 3: 최소 구현** (`witnessd/pause.py`)
```python
from typing import Any
from witnessd.runlog import append_runlog

PAUSE_EVENT = "user_pause"
RESUME_EVENT = "user_resume"
ERR_WITNESSD_PAUSED = "ERR_WITNESSD_PAUSED"
_VALID_SOURCES = frozenset({"signal", "cli"})


class PauseError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)


def derive_pause_state(records: list[dict[str, Any]]) -> bool:
    # pure projection: 마지막 pause/resume 이벤트만 본다. 없으면 not-paused.
    state = False
    for record in records:
        event = record.get("event")
        if event == PAUSE_EVENT:
            state = True
        elif event == RESUME_EVENT:
            state = False
    return state


def append_user_pause(log, run_id: str, source: str) -> dict[str, Any]:
    if source not in _VALID_SOURCES:
        raise PauseError("ERR_WITNESSD_PAUSE_SOURCE_INVALID")
    return append_runlog(log, run_id=run_id, event=PAUSE_EVENT, payload={"source": source})


def append_user_resume(log, run_id: str, confirm: bool) -> dict[str, Any]:
    if confirm is not True:
        raise PauseError("ERR_WITNESSD_RESUME_UNCONFIRMED")
    return append_runlog(log, run_id=run_id, event=RESUME_EVENT, payload={"confirm": True})


def assert_not_paused(records: list[dict[str, Any]]) -> None:
    if derive_pause_state(records):
        raise PauseError(ERR_WITNESSD_PAUSED)
```
`EventLog.read()`가 W1/W2에 없으면 그 시그니처(jsonl 라인들을 dict 리스트로 반환)를 `eventlog.py`에서 확인해 맞춘다 — W2 scheduler/liveness가 이미 `event_log.read()`를 fold하므로 존재한다. 없으면 이 Task에서 `read()`를 additive로 추가(재작성/수정 메서드는 여전히 없음).
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_pause -v` → PASS.
- [ ] **Step 5: Commit** — `feat: hard pause boundary as runlog projection (ERR_WITNESSD_PAUSED, confirm-only resume)`

---

## Task 2: continuation 게이트 배선 (M6) — supervisor/scheduler가 pause를 override 못 함

**Files:**
- Modify: `witnessd/supervisor.py`, `witnessd/scheduler.py`
- Test: `tests/test_continuation_gate.py`

auto-continuation(auto-retry/auto-spawn/dispatch)이 pause를 override할 **코드 경로가 존재하지 않아야** 한다(§6.1.3). 방법: 새 side-effect를 시작하는 유일한 두 지점(`WorkerSupervisor.spawn`, `Scheduler.schedule`) 진입 즉시 `assert_not_paused(event_log.read())`를 호출한다. pause 상태면 spawn/schedule이 `ERR_WITNESSD_PAUSED`로 거부되고 자식 프로세스가 생성되지 않는다.

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.supervisor import WorkerSupervisor
from witnessd.pause import append_user_pause, PauseError

class TestContinuationGate(unittest.TestCase):
    def test_spawn_refused_when_paused(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            sup = WorkerSupervisor(log, run_id="R1")
            append_user_pause(log, run_id="R1", source="cli")
            with self.assertRaises(PauseError) as cm:
                sup.spawn(lane_id="L1", argv=["sh", "-c", "true"], runner_uid=os.getuid())
            self.assertEqual(str(cm.exception), "ERR_WITNESSD_PAUSED")
            # pause 이후 spawn event가 로그에 없음(새 side-effect 0건)
            self.assertFalse(any(r.get("event") == "spawn" for r in log.read()))
```
- [ ] **Step 2: 실패 확인** — FAIL (spawn이 pause를 무시하고 자식 생성).
- [ ] **Step 3: 최소 구현** — `witnessd/supervisor.py` `WorkerSupervisor.spawn` 첫 줄에 `from witnessd.pause import assert_not_paused; assert_not_paused(self._log.read())` 추가(자식 생성·spawn event append **전에**). `witnessd/scheduler.py` `Scheduler.schedule` 첫 줄에 동일 게이트 추가. **게이트를 우회하는 파라미터(force 등)를 추가하지 않는다** — override 경로 부재가 안티회귀의 본질. `reconcile()`(읽기전용 projection)은 게이트하지 않는다(side-effect 없음).
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_continuation_gate tests.test_supervisor -v` → PASS(기존 supervisor 테스트도 유지).
- [ ] **Step 5: Commit** — `feat: continuation gate — spawn/schedule fail-closed under pause (no override path)`

---

## Task 3: kill-switch (M11) — `witnessd/killswitch.py` (전체 harness 정지 → dead 파생)

**Files:**
- Create: `witnessd/killswitch.py`
- Test: `tests/test_killswitch.py`

`kill --all`은 supervisor가 소유한 모든 자식에 SIGTERM → (유예) → SIGKILL을 보내고, **확정 종료된** 각 lane에 대해 W2 `exit` runlog event(clean-exit → `derive_liveness` == `dead`)를 append하며, 요약 `kill` event를 남긴다. fail-closed(§5.5): 자식을 확실히 종료하지 못하면(여전히 살아있음) 그 lane의 exit event를 append하지 않아 `active`로 파생되지 않는다(heartbeat 만료 → `zombie`, 결코 `active` 아님). W2 `derive_liveness`/`append_runlog`를 재사용하고 재구현하지 않는다.

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os, subprocess, time
from witnessd.eventlog import EventLog
from witnessd.supervisor import WorkerSupervisor
from witnessd.killswitch import kill_all
from witnessd.liveness import derive_liveness

class TestKill(unittest.TestCase):
    def test_kill_all_terminates_and_derives_dead(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            sup = WorkerSupervisor(log, run_id="R1")
            h = sup.spawn(lane_id="L1", argv=["sh", "-c", "sleep 60"], runner_uid=os.getuid())
            result = kill_all(sup, log, run_id="R1")
            self.assertTrue(result["killed"])
            # 자식 프로세스가 실제로 종료됨
            self.assertIsNotNone(h.popen.poll())
            # kill event 기록됨
            self.assertTrue(any(r.get("event") == "kill" for r in log.read()))
            # heartbeat 파생 상태가 dead
            st = derive_liveness(log.read(), now_monotonic=time.monotonic() + 10_000)
            self.assertEqual(st.get("L1"), "dead")
```
- [ ] **Step 2: 실패 확인** — FAIL (module 없음).
- [ ] **Step 3: 최소 구현** (`witnessd/killswitch.py`)
```python
import signal
import time
from typing import Any
from witnessd.runlog import append_runlog

_TERM_GRACE_SECONDS = 2.0


def _terminate(handle, grace: float) -> tuple[bool, int | None]:
    popen = handle.popen
    if popen.poll() is not None:
        return True, popen.returncode
    popen.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if popen.poll() is not None:
            return True, popen.returncode
        time.sleep(0.02)
    popen.send_signal(signal.SIGKILL)
    try:
        popen.wait(timeout=grace)
    except Exception:
        pass
    code = popen.poll()
    return code is not None, code


def kill_all(supervisor, log, run_id: str, grace: float = _TERM_GRACE_SECONDS) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    all_dead = True
    for handle in list(supervisor.handles()):   # W2 supervisor가 활성 handle을 노출
        confirmed, code = _terminate(handle, grace)
        outcomes.append({"lane_id": handle.lane_id, "pid": handle.pid,
                         "confirmed_dead": confirmed, "exit_code": code})
        if confirmed:
            # clean-exit event → derive_liveness == dead (fail-closed: 미확정은 append 안 함)
            append_runlog(log, run_id=run_id, event="exit",
                          payload={"lane_id": handle.lane_id, "exit_code": code if code is not None else -9})
        else:
            all_dead = False
    append_runlog(log, run_id=run_id, event="kill",
                  error_code=None if all_dead else "ERR_WITNESSD_KILL_UNCONFIRMED",
                  payload={"outcomes": outcomes, "all_confirmed_dead": all_dead})
    return {"killed": True, "all_confirmed_dead": all_dead, "outcomes": outcomes}
```
`supervisor.handles()`가 W2에 없으면 `WorkerSupervisor`의 실제 handle 보관 속성(예: `self._handles`)을 `supervisor.py`에서 읽어 `handles()` 접근자를 additive로 추가하고, W2 `test_supervisor.py`가 여전히 PASS함을 확인한다. `derive_liveness`의 `exit`→`dead` 규칙과 payload 키(`lane_id`/`exit_code`)는 W2 `liveness.py`/`test_liveness.py`의 `_exit(...)` 형태로 확정.
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_killswitch -v` → PASS.
- [ ] **Step 5: Commit** — `feat: kill --all — SIGTERM/SIGKILL all children, clean-exit event, dead projection (fail-closed on unconfirmed)`

---

## Task 4: 학습 delta 빌더 + provenance 검증 (M9) — `witnessd/learning.py`

**Files:**
- Create: `witnessd/learning.py`
- Test: `tests/test_learning.py`

반복 교정을 **버전드 delta**로 승격한다(§5.5). delta는 provenance 포인터 3종을 담는다: (1) `capture_hash` = 그 학습을 정당화한 committed capture-manifest의 `canonical_hash`(W1), (2) `approval_event_hash` = 승인 runlog event의 `event_hash`(§6.0.3), (3) `provenance_manifest_hash` = 그 capture의 `trusted-observer-provenance` 레코드 `manifest_hash`. **승인·증거 포인터가 없거나 어긋나면 blocked**(fail-closed) — `validate_learning_delta_provenance`가 committed capture 집합·승인 event 집합과 대조해 재도출한다. witnessd는 이 검증으로 승격을 **거부**할 뿐, assurance를 부여하지 않는다(등급은 Depone 소관, 상한 A2).

- [ ] **Step 1: 실패 테스트**
```python
import unittest
from witnessd.canonical import canonical_hash
from witnessd.learning import (
    build_learning_delta, validate_learning_delta_provenance,
    LEARNING_DELTA_KIND, ERR_LEARNING_PROVENANCE_MISSING,
    ERR_LEARNING_PROVENANCE_MISMATCH, ERR_LEARNING_DELTA_UNAPPROVED,
)

class TestLearning(unittest.TestCase):
    def _capture(self):
        return {"kind": "agent-fabric-capture-manifest", "assurance": "A1-local-observed",
                "observer_capture": {"observed_by": "depone-observer"}}

    def test_valid_delta_provenance_ok(self):
        cap = self._capture()
        ch = canonical_hash(cap)
        approval = {"event": "learning_approval", "event_hash": "abc123"}
        delta = build_learning_delta(run_id="R1", target="AGENTS.md", version=1,
                                     delta_text="prefer f-strings",
                                     capture_manifest=cap,
                                     approval_event_hash="abc123",
                                     provenance_manifest_hash=ch)
        self.assertEqual(delta["kind"], LEARNING_DELTA_KIND)
        self.assertEqual(delta["provenance"]["capture_hash"], ch)
        errs = validate_learning_delta_provenance(delta, committed_captures=[cap],
                                                  approval_events=[approval])
        self.assertEqual(errs, [])

    def test_missing_pointer_blocked(self):
        cap = self._capture()
        delta = build_learning_delta(run_id="R1", target="AGENTS.md", version=1,
                                     delta_text="x", capture_manifest=cap,
                                     approval_event_hash="abc123",
                                     provenance_manifest_hash=canonical_hash(cap))
        delta["provenance"]["capture_hash"] = None   # tamper: 포인터 제거
        errs = validate_learning_delta_provenance(delta, committed_captures=[cap], approval_events=[])
        self.assertIn(ERR_LEARNING_PROVENANCE_MISSING, errs)

    def test_pointer_mismatch_blocked(self):
        cap = self._capture()
        delta = build_learning_delta(run_id="R1", target="AGENTS.md", version=1,
                                     delta_text="x", capture_manifest=cap,
                                     approval_event_hash="abc123",
                                     provenance_manifest_hash=canonical_hash(cap))
        other = {"kind": "agent-fabric-capture-manifest", "assurance": "A0-claims-only"}
        errs = validate_learning_delta_provenance(delta, committed_captures=[other],
                                                  approval_events=[{"event":"learning_approval","event_hash":"abc123"}])
        self.assertIn(ERR_LEARNING_PROVENANCE_MISMATCH, errs)

    def test_unapproved_blocked(self):
        cap = self._capture()
        delta = build_learning_delta(run_id="R1", target="AGENTS.md", version=1,
                                     delta_text="x", capture_manifest=cap,
                                     approval_event_hash="abc123",
                                     provenance_manifest_hash=canonical_hash(cap))
        errs = validate_learning_delta_provenance(delta, committed_captures=[cap], approval_events=[])
        self.assertIn(ERR_LEARNING_DELTA_UNAPPROVED, errs)
```
- [ ] **Step 2: 실패 확인** — FAIL (module 없음).
- [ ] **Step 3: 최소 구현** (`witnessd/learning.py`)
```python
from typing import Any
from witnessd.canonical import canonical_hash

LEARNING_DELTA_KIND = "witnessd-learning-delta"
LEARNING_SCHEMA_VERSION = "1.0"
APPROVAL_EVENT = "learning_approval"
ERR_LEARNING_PROVENANCE_MISSING = "ERR_LEARNING_PROVENANCE_MISSING"
ERR_LEARNING_PROVENANCE_MISMATCH = "ERR_LEARNING_PROVENANCE_MISMATCH"
ERR_LEARNING_DELTA_UNAPPROVED = "ERR_LEARNING_DELTA_UNAPPROVED"


def build_learning_delta(*, run_id: str, target: str, version: int, delta_text: str,
                         capture_manifest: dict[str, Any], approval_event_hash: str,
                         provenance_manifest_hash: str) -> dict[str, Any]:
    return {
        "kind": LEARNING_DELTA_KIND,
        "schema_version": LEARNING_SCHEMA_VERSION,
        "run_id": run_id,
        "target": target,           # 예: "AGENTS.md" / "skills/foo/SKILL.md"
        "version": version,         # 버전드 delta (단조 증가)
        "delta_text": delta_text,
        "provenance": {
            "capture_hash": canonical_hash(capture_manifest),
            "approval_event_hash": approval_event_hash,
            "provenance_manifest_hash": provenance_manifest_hash,
        },
    }


def validate_learning_delta_provenance(delta: dict[str, Any], *,
                                       committed_captures: list[dict[str, Any]],
                                       approval_events: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    provenance = delta.get("provenance") or {}
    capture_hash = provenance.get("capture_hash")
    approval_hash = provenance.get("approval_event_hash")
    if not capture_hash or not approval_hash:
        errors.append(ERR_LEARNING_PROVENANCE_MISSING)
        return errors   # fail-closed: 포인터 없으면 즉시 blocked
    committed_hashes = {canonical_hash(cap) for cap in committed_captures}
    if capture_hash not in committed_hashes:
        errors.append(ERR_LEARNING_PROVENANCE_MISMATCH)
    approved = {e.get("event_hash") for e in approval_events if e.get("event") == APPROVAL_EVENT}
    if approval_hash not in approved:
        errors.append(ERR_LEARNING_DELTA_UNAPPROVED)
    return errors
```
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_learning -v` → PASS.
- [ ] **Step 5: Commit** — `feat: versioned learning delta + provenance validation (missing/mismatch/unapproved -> blocked)`

---

## Task 5: 학습 delta 승격 → evidence bundle (M9) — Depone `ingest_signed_evidence_bundle` 소비 가능

**Files:**
- Modify: `witnessd/learning.py`
- Test: `tests/test_learning_promote.py`

승격된 학습 아티팩트는 그 자체가 append-only 체인의 서명 event로 방출되며(§5.5), **provenance를 포함해 Depone evidence_substrate로 ingest 가능**해야 한다(§7 W5 Acceptance 3). 구현: `promote_learning_delta`가 (1) `validate_learning_delta_provenance`로 fail-closed 검사(errors 비면 `blocked` 사유와 함께 승격 거부·`learning_delta` runlog event를 error_code로 남김), (2) 통과 시 W1 `build_bundle`로 delta를 subject로 감싸 in-toto Statement v1 + DSSE 서명 후 `learning_delta` runlog event를 append. bundle은 W1 계약이라 재구현하지 않는다.

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.signing import gen_operator_keypair
from witnessd.canonical import canonical_hash
from witnessd.learning import build_learning_delta, promote_learning_delta
from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle

class TestPromote(unittest.TestCase):
    def test_promoted_delta_ingestible(self):
        with tempfile.TemporaryDirectory() as d:
            priv, pub = gen_operator_keypair(d)
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            cap = {"kind":"agent-fabric-capture-manifest","assurance":"A1-local-observed",
                   "observer_capture":{"observed_by":"depone-observer"}}
            appr = log.append({"kind":"witnessd-runlog-event","event":"learning_approval","run_id":"R1"})
            delta = build_learning_delta(run_id="R1", target="AGENTS.md", version=1,
                                         delta_text="prefer f-strings", capture_manifest=cap,
                                         approval_event_hash=appr["event_hash"],
                                         provenance_manifest_hash=canonical_hash(cap))
            result = promote_learning_delta(delta, log=log, run_id="R1", priv=priv, pub=pub,
                                            committed_captures=[cap], approval_events=[appr],
                                            evidence_dir=d)
            self.assertTrue(result["promoted"])
            # Depone이 서명 검증하고 subject를 재도출
            verdict = ingest_signed_evidence_bundle(result["bundle"], pub, result["artifact_paths"])
            self.assertTrue(verdict["signature_verified"])

    def test_unapproved_delta_refused(self):
        with tempfile.TemporaryDirectory() as d:
            priv, pub = gen_operator_keypair(d)
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            cap = {"kind":"agent-fabric-capture-manifest","assurance":"A1-local-observed"}
            delta = build_learning_delta(run_id="R1", target="AGENTS.md", version=1,
                                         delta_text="x", capture_manifest=cap,
                                         approval_event_hash="nope",
                                         provenance_manifest_hash=canonical_hash(cap))
            result = promote_learning_delta(delta, log=log, run_id="R1", priv=priv, pub=pub,
                                            committed_captures=[cap], approval_events=[], evidence_dir=d)
            self.assertFalse(result["promoted"])
            self.assertIn("ERR_LEARNING_DELTA_UNAPPROVED", result["errors"])
            # 거부도 runlog event로 남는다(error_code)
            self.assertTrue(any(r.get("event") == "learning_delta" and r.get("error_code")
                                for r in log.read()))
```
`ingest_signed_evidence_bundle`의 정확한 인자 순서·반환 키(`signature_verified`/subject 검증 형태)는 `evidence_substrate.py`(line 227~/`_finalize_ingest_verdict`)를 읽어 확정하고, `build_bundle`의 subject/artifact_paths 규약은 W1 `substrate.py`로 확정. delta를 subject로 감싸는 방식(별도 아티팩트 파일 + subject digest = `canonical_hash(delta)`)이 W1 `build_bundle` 시그니처와 맞는지 확인해 배선한다.
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 최소 구현** — `promote_learning_delta(delta, *, log, run_id, priv, pub, committed_captures, approval_events, evidence_dir)`:
  1. `errors = validate_learning_delta_provenance(delta, committed_captures=..., approval_events=...)`.
  2. errors 비어있지 않으면 `append_runlog(log, run_id, "learning_delta", error_code=errors[0], payload={"blocked": True, "errors": errors, "target": delta["target"]})` 후 `{"promoted": False, "errors": errors}` 반환(승격 금지, fail-closed).
  3. 통과 시 delta를 `evidence_dir`에 파일로 쓰고 W1 `build_bundle`(delta를 subject, provenance predicate에 `delta["provenance"]` 포함)로 서명 bundle 생성, `append_runlog(log, run_id, "learning_delta", payload={"promoted": True, "capture_hash": delta["provenance"]["capture_hash"], "version": delta["version"]})`, `{"promoted": True, "bundle": bundle, "artifact_paths": {...}}` 반환.
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_learning_promote -v` → PASS.
- [ ] **Step 5: Commit** — `feat: promote_learning_delta as signed evidence bundle (Depone-ingestible), blocked path leaves runlog event`

---

## Task 6: 원자적 installer (M11) — `witnessd/installer.py` (unreadable config = fail-safe, no orphan shim)

**Files:**
- Create: `witnessd/installer.py`
- Test: `tests/test_installer.py`

install/upgrade는 원자적이고 명시적 명령으로만, **unreadable/손상 config에는 fail-safe(덮어쓰기 금지)**, orphan bin shim 없음(§3.9). 구현: config를 먼저 읽어 파싱; 읽기 실패(`PermissionError`/`OSError`)나 파싱 실패면 `ERR_WITNESSD_CONFIG_UNREADABLE`로 raise하고 **어떤 파일도 쓰지 않으며 shim도 만들지 않는다**. 성공 시 payload를 dest에 temp+`os.replace`+`fsync`(원자)로 설치하고 shim을 동일 원자 방식으로 쓰되, 설치된 버전을 가리키지 않는 shim(=orphan)이 남지 않게 설치 후 `list_orphan_shims`가 `[]`임을 강제한다. 실행 중 self-replace는 하지 않는다(명시 명령만).

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.installer import atomic_install, list_orphan_shims, InstallerError, ERR_WITNESSD_CONFIG_UNREADABLE

class TestInstaller(unittest.TestCase):
    def test_unreadable_config_fail_safe_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "dest"); shim = os.path.join(d, "bin"); os.makedirs(dest); os.makedirs(shim)
            existing = os.path.join(dest, "v1.txt")
            with open(existing, "w") as f: f.write("ORIGINAL")
            payload = os.path.join(d, "payload.txt")
            with open(payload, "w") as f: f.write("NEW")
            bad_cfg = os.path.join(d, "config.bin")
            with open(bad_cfg, "wb") as f: f.write(b"\x00\xff not json")
            with self.assertRaises(InstallerError) as cm:
                atomic_install(payload_path=payload, dest_dir=dest, config_path=bad_cfg,
                               shim_dir=shim, version="v2")
            self.assertEqual(cm.exception.code, ERR_WITNESSD_CONFIG_UNREADABLE)
            # 덮어쓰기 없음
            with open(existing) as f: self.assertEqual(f.read(), "ORIGINAL")
            # orphan shim 미생성
            self.assertEqual(os.listdir(shim), [])
            self.assertEqual(list_orphan_shims(shim, dest), [])

    def test_valid_install_atomic_and_no_orphan(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "dest"); shim = os.path.join(d, "bin"); os.makedirs(dest); os.makedirs(shim)
            payload = os.path.join(d, "payload.txt")
            with open(payload, "w") as f: f.write("NEW")
            cfg = os.path.join(d, "config.json")
            with open(cfg, "w") as f: f.write('{"ok": true}')
            result = atomic_install(payload_path=payload, dest_dir=dest, config_path=cfg,
                                    shim_dir=shim, version="v2")
            self.assertTrue(result["installed"])
            self.assertEqual(list_orphan_shims(shim, dest), [])
```
- [ ] **Step 2: 실패 확인** — FAIL (module 없음).
- [ ] **Step 3: 최소 구현** (`witnessd/installer.py`)
```python
import json
import os
from typing import Any

ERR_WITNESSD_CONFIG_UNREADABLE = "ERR_WITNESSD_CONFIG_UNREADABLE"
ERR_WITNESSD_ORPHAN_SHIM = "ERR_WITNESSD_ORPHAN_SHIM"


class InstallerError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _read_config(config_path: str) -> dict[str, Any]:
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        # unreadable/손상 → fail-safe (호출부가 어떤 파일도 쓰기 전에 발생)
        raise InstallerError(ERR_WITNESSD_CONFIG_UNREADABLE)


def _atomic_write(dest_path: str, data: bytes) -> None:
    tmp = dest_path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, dest_path)   # 원자적 교체
    dir_fd = os.open(os.path.dirname(dest_path) or ".", os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def list_orphan_shims(shim_dir: str, dest_dir: str) -> list[str]:
    installed = set(os.listdir(dest_dir))
    orphans: list[str] = []
    for name in os.listdir(shim_dir):
        target = os.path.basename(os.path.realpath(os.path.join(shim_dir, name)))
        if target not in installed:
            orphans.append(name)
    return orphans


def atomic_install(*, payload_path: str, dest_dir: str, config_path: str,
                   shim_dir: str, version: str) -> dict[str, Any]:
    _read_config(config_path)          # 먼저: unreadable이면 여기서 raise, 아무것도 안 씀
    with open(payload_path, "rb") as handle:
        payload = handle.read()
    installed_path = os.path.join(dest_dir, f"{version}.txt")
    _atomic_write(installed_path, payload)
    shim_path = os.path.join(shim_dir, "witnessd")
    tmp_link = shim_path + ".tmp"
    if os.path.lexists(tmp_link):
        os.remove(tmp_link)
    os.symlink(installed_path, tmp_link)
    os.replace(tmp_link, shim_path)    # 원자적 shim 교체
    orphans = list_orphan_shims(shim_dir, dest_dir)
    if orphans:
        raise InstallerError(ERR_WITNESSD_ORPHAN_SHIM)
    return {"installed": True, "version": version, "path": installed_path}


def atomic_upgrade(*, payload_path: str, dest_dir: str, config_path: str,
                   shim_dir: str, version: str) -> dict[str, Any]:
    # upgrade = 명시 명령의 install(실행 중 self-replace 아님)
    return atomic_install(payload_path=payload_path, dest_dir=dest_dir,
                          config_path=config_path, shim_dir=shim_dir, version=version)
```
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_installer -v` → PASS.
- [ ] **Step 5: Commit** — `feat: atomic installer — fail-safe on unreadable config, no orphan shim, atomic replace+fsync`

---

## Task 7: faultkit `pause-race` (§6.1.1) — SIGINT@200ms 후 side-effect 0건 회귀

**Files:**
- Modify: `witnessd/faultkit.py`
- Test: `tests/test_faultkit_pause.py`

OMO `todo-continuation-enforcer` #89 재발 방지(§6.1.1 검증). 툴콜 dispatch **직후 200ms**에 SIGINT(→ `append_user_pause(source="signal")`)를 주입하고, pause 이후 어떤 write/commit/spawn/dispatch(=side-effect) event도 runlog에 없음을 assert하는 결정적 주입 하네스. W2 `faultkit` 패턴(결정적 주입)을 재사용해 추가한다.

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.faultkit import pause_race
from witnessd.pause import PAUSE_EVENT, derive_pause_state

_SIDE_EFFECTS = {"spawn", "dispatch", "edit", "commit"}

class TestPauseRace(unittest.TestCase):
    def test_no_side_effect_after_pause(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            records = pause_race(log, run_id="R1")   # dispatch 후 200ms에 SIGINT 주입
            self.assertTrue(derive_pause_state(records))
            pause_idx = next(i for i, r in enumerate(records) if r.get("event") == PAUSE_EVENT)
            after = records[pause_idx + 1:]
            self.assertFalse(any(r.get("event") in _SIDE_EFFECTS for r in after))
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 최소 구현** — `witnessd/faultkit.py`에 `pause_race(log, run_id)` 추가: (1) `append_runlog(log, run_id, "dispatch", payload={"lane_id": "L1"})`, (2) 200ms 후 `append_user_pause(log, run_id, source="signal")`(실제 SIGINT 핸들러가 이 함수를 호출하도록 배선; 테스트는 결정적 주입), (3) 이후 continuation 시도를 `assert_not_paused(log.read())`로 게이트해 `PauseError`가 나면 side-effect event를 append하지 않음, (4) `log.read()` 반환. **pause 이후 side-effect append 경로가 게이트를 우회하지 못함**을 이 하네스가 고정.
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_faultkit_pause -v` → PASS.
- [ ] **Step 5: Commit** — `feat: faultkit pause-race — SIGINT@200ms, zero side-effect after pause (OMO #89 anti-regression)`

---

## Task 8: CLI 배선 — `witnessd pause/resume/kill/learn/install/upgrade` + `faultkit pause-race`

**Files:**
- Modify: `witnessd/__main__.py`
- Test: `tests/test_cli_w5.py`

`witnessd pause <run_id>`(source `cli`)는 `append_user_pause`, `witnessd resume <run_id> --confirm`은 `append_user_resume(confirm=True)`(`--confirm` 없으면 거부·비-0), `witnessd kill --all`은 supervisor의 모든 자식 종료 후 `dead` 파생, `witnessd learn promote --delta <f>`는 `promote_learning_delta`(승인·증거 없으면 비-0 + `blocked` 사유), `witnessd install/upgrade`는 `atomic_install`/`atomic_upgrade`(unreadable config면 비-0 + `ERR_WITNESSD_CONFIG_UNREADABLE`, 덮어쓰기 없음). 모든 출력은 W1 `render_status` enum을 경유(성공 문구 금지).

- [ ] **Step 1: 실패 테스트** (subprocess로 CLI 구동)
```python
import unittest, subprocess, sys, tempfile, os, json

class TestCliW5(unittest.TestCase):
    def _run(self, *args, cwd=None):
        return subprocess.run([sys.executable, "-m", "witnessd", *args],
                              capture_output=True, text=True, cwd=cwd)

    def test_resume_requires_confirm_flag(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._run("pause", "R1", "--runlog", os.path.join(d, "runlog.jsonl"))
            self.assertEqual(r.returncode, 0)
            r2 = self._run("resume", "R1", "--runlog", os.path.join(d, "runlog.jsonl"))  # --confirm 없음
            self.assertNotEqual(r2.returncode, 0)

    def test_install_unreadable_config_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.bin")
            with open(cfg, "wb") as f: f.write(b"\x00\xff")
            payload = os.path.join(d, "p.txt")
            with open(payload, "w") as f: f.write("x")
            dest = os.path.join(d, "dest"); shim = os.path.join(d, "bin"); os.makedirs(dest); os.makedirs(shim)
            r = self._run("install", "--payload", payload, "--dest", dest,
                          "--config", cfg, "--shim-dir", shim, "--version", "v2")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ERR_WITNESSD_CONFIG_UNREADABLE", r.stdout + r.stderr)
            self.assertEqual(os.listdir(shim), [])   # orphan shim 없음
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 최소 구현** — `witnessd/__main__.py`에 argparse 서브커맨드 `pause`/`resume`(`--confirm`)/`kill`(`--all`)/`learn promote`(`--delta`)/`install`/`upgrade`(`--payload`/`--dest`/`--config`/`--shim-dir`/`--version`)/`faultkit pause-race` 추가. 각 예외(`PauseError`/`InstallerError`/blocked delta)를 잡아 error_code를 stdout에 출력하고 비-0 exit. status/보고는 `render_status`(도메인 밖 문자열 생성 금지). W2에서 이미 배선된 `spawn`/`resume`(durable) 서브커맨드와 충돌하지 않게 W2 `__main__.py` 실제 구조를 읽어 additive로 얹는다.
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_cli_w5 -v` → PASS. 수동: `witnessd kill --all`을 실제 supervised lane에서 돌려 자식 종료·`dead` 파생 확인.
- [ ] **Step 5: Commit** — `feat: witnessd CLI pause/resume/kill/learn/install/upgrade (fail-closed exits)`

---

## Task 9: W5 committed fixtures

**Files:**
- Create: `fixtures/w5/pause-override.jsonl`, `fixtures/w5/kill-all.jsonl`, `fixtures/w5/capture-for-learning.json`, `fixtures/w5/learning-delta.json`, `fixtures/w5/learning-delta-bundle.json`, `fixtures/w5/keys/operator.pub`

- [ ] **Step 1: pause-override 생성** — 실제 `witnessd faultkit pause-race`(Task 7)로 dispatch→SIGINT@200ms→pause runlog를 방출해 `fixtures/w5/pause-override.jsonl`에 저장. 불변식: `verify_runlog` 통과, `user_pause` 이후 side-effect(`spawn`/`dispatch`/`edit`/`commit`) event 0건.
- [ ] **Step 2: kill-all 생성** — 실제 supervised `sleep` lane spawn 후 `witnessd kill --all`로 exit+kill runlog를 `fixtures/w5/kill-all.jsonl`에 저장. `derive_liveness(..., now=+∞)`가 전 lane `dead`.
- [ ] **Step 3: learning delta + bundle 생성** — A1 capture 하나를 `fixtures/w5/capture-for-learning.json`로 저장, 그 `canonical_hash`를 포인터로 갖고 승인 event_hash를 참조하는 `learning-delta.json`을 `build_learning_delta`로, 그 승격 bundle을 `promote_learning_delta`로 방출(`learning-delta-bundle.json`). 개인키 커밋 금지(`.gitignore`), 공개키만 `fixtures/w5/keys/operator.pub`.
- [ ] **Step 4: 커밋** — `git add fixtures/w5 && git commit -m "test: W5 committed fixtures (pause-override, kill-all, learning delta+bundle)"`

---

## Task 10: `scripts/revalidate_w5.py` (G2 — Depone/witnessd 재도출)

**Files:**
- Create: `scripts/revalidate_w5.py`

committed fixture 바이트에서만 재도출하고 전부 assert 후 exit 0. 실행하지 않는다(순수 검증). 정확한 Depone 반환형은 실제 코드로 맞춘다.
```python
import json, sys
from witnessd.canonical import canonical_hash
from witnessd.runlog import verify_runlog
from witnessd.liveness import derive_liveness
from witnessd.pause import PAUSE_EVENT, derive_pause_state
from witnessd.learning import validate_learning_delta_provenance
from depone.agent_fabric.evidence_substrate import ingest_signed_evidence_bundle
from depone.agent_fabric.sign import verify_signed_bundle

def _load_jsonl(path): return [json.loads(l) for l in open(path) if l.strip()]
def _load(path): return json.load(open(path))

# (1) pause-override: verify_runlog OK, pause 이후 side-effect 0건
recs = _load_jsonl("fixtures/w5/pause-override.jsonl")
assert verify_runlog(recs)["ok"] is True
assert derive_pause_state(recs) is True
i = next(k for k, r in enumerate(recs) if r.get("event") == PAUSE_EVENT)
assert not any(r.get("event") in {"spawn","dispatch","edit","commit"} for r in recs[i+1:])

# (2) kill-all: 전 lane dead
krecs = _load_jsonl("fixtures/w5/kill-all.jsonl")
live = derive_liveness(krecs, now_monotonic=10**12)
assert live and all(v == "dead" for v in live.values())
assert any(r.get("event") == "kill" for r in krecs)

# (3) 학습 provenance: 포인터가 실제 committed capture canonical hash와 일치
cap = _load("fixtures/w5/capture-for-learning.json")
delta = _load("fixtures/w5/learning-delta.json")
appr = [r for r in recs if r.get("event") == "learning_approval"] or \
       [{"event":"learning_approval","event_hash":delta["provenance"]["approval_event_hash"]}]
assert validate_learning_delta_provenance(delta, committed_captures=[cap], approval_events=appr) == []
assert delta["provenance"]["capture_hash"] == canonical_hash(cap)
bundle = _load("fixtures/w5/learning-delta-bundle.json")
pub = "fixtures/w5/keys/operator.pub"
assert verify_signed_bundle(bundle, pub) is True

print("W5 revalidate: PASS"); sys.exit(0)
```
- [ ] **Step 1: 작성** — 위 스크립트. `ingest_signed_evidence_bundle`/`verify_signed_bundle` 반환형·인자는 `evidence_substrate.py`/`sign.py`로 확정.
- [ ] **Step 2: 실행** — `python3 scripts/revalidate_w5.py` → `W5 revalidate: PASS`, exit 0.
- [ ] **Step 3: 커밋** — `test: revalidate_w5 re-derives pause-override/kill-dead/learning-provenance from committed bytes`

---

## Task 11: negative fixtures (blocked 회귀)

**Files:**
- Create: `fixtures/w5/negative/learning-delta-no-provenance.json`, `fixtures/w5/negative/learning-delta-unapproved.json`, `fixtures/w5/negative/installer-unreadable-config/config.bin`
- Modify: `scripts/revalidate_w5.py`

- [ ] **Step 1: 실패 테스트(회귀 assert 추가)** — revalidate_w5에 다음 assert 추가:
  - `learning-delta-no-provenance.json`(포인터 제거) → `validate_learning_delta_provenance`가 `ERR_LEARNING_PROVENANCE_MISSING` 반환(blocked, 승격 금지).
  - `learning-delta-unapproved.json`(승인 event 없음) → `ERR_LEARNING_DELTA_UNAPPROVED` 반환.
  - `installer-unreadable-config/config.bin`(비-JSON 바이트) → `atomic_install(...)`이 `InstallerError(ERR_WITNESSD_CONFIG_UNREADABLE)` raise, dest 미변경·shim 디렉터리 빈 상태(orphan 0)임을 assert.
- [ ] **Step 2: 통과 확인** — `python3 scripts/revalidate_w5.py` 재실행 → `W5 revalidate: PASS` exit 0.
- [ ] **Step 3: 커밋** — `test: W5 negative fixtures (unprovenanced/unapproved delta blocked, unreadable-config fail-safe)`

---

## Task 12: 단조성 회귀 + 공통 게이트 G1/G2/G3 + W5 완료

**Files:**
- Modify: `scripts/revalidate_w5.py` (단조성 assert)

- [ ] **Step 1: 단조성(§5.0 불변식 1)** — `capture-for-learning.json`이 W1 `validate_capture_manifest`를 통과하고(A1/A2), learning bundle의 subject digest가 W1 `verify_capture_chain`/`ingest`와 모순 없이 재도출됨을 revalidate_w5에 assert. 그리고 `python3 scripts/revalidate_w1.py && python3 scripts/revalidate_w2.py && python3 scripts/revalidate_w3.py` 전부 여전히 exit 0(이전 웨이브 회귀 없음).
- [ ] **Step 2: G1** — `python3 -m witnessd self-test --all` → `N/N passed` exit 0(`pause`/`killswitch`/`learning`/`installer` 모듈 각 `_self_test` 포함). 각 새 모듈에 `_self_test()`를 추가하고 `self-test --all`에 등록.
- [ ] **Step 3: G2** — `python3 scripts/revalidate_w5.py` → `W5 revalidate: PASS` exit 0.
- [ ] **Step 4: G3** — witnessd 방출 learning bundle을 Depone repo에서 소비: `cd /home/ubuntu/depone-assurance-repair && python scripts/check_contract.py --tier changed && python scripts/dwm.py doctor` red 없음.
- [ ] **Step 5: 도그푸드 n=1(§7.5)** — supervised lane 실행 → `witnessd pause`(side-effect 0건 확인) → `witnessd resume --confirm` → 반복 교정을 승인과 함께 `witnessd learn promote`로 승격 → `witnessd kill --all`(전 lane `dead`) → Depone이 learning bundle을 ingest·pause-override/kill-dead를 재도출함을 양쪽 repo에 committed artifact로 남긴다.
- [ ] **Step 6: 커밋 + W5 종료** — `git commit -m "feat: W5 complete — learning provenance + hard pause + kill-switch/atomic installer, Depone re-derives"`

---

## Residual (웨이브 밖 deferred — §5.5 Residual risk)
W5 완료 후 남는 것은 **keyless 서명 축(Sigstore Fulcio keyless + Rekor transparency log)**로의 서명 업그레이드와 **docker/container isolation 모델(A2)**의 1급 승격이다. 둘 다 W1–W5 범위에서 명시적으로 deferred이며, signing step(`sign_dsse`)과 isolation probe(`probe_lane_isolation`)를 swappable로 설계해 둔 덕분에 계약 변경 없이 후속 웨이브(W6 후보)에서 교체 가능하다. 불변식(§5.0)은 유지 — 새 서명·격리 모델도 Depone validator가 바이트에서 재도출하지 못하면 assurance를 얻지 못한다.

---

## Self-review 체크 (작성자 수행)

- **Spec 커버리지(§5.5):** M9 자동 학습 캡처(provenance+버전드 delta)=Task4/5, 승인/증거 부재→blocked=Task4/5/11, M6 hard pause(continuation override 불가)=Task1/2/7, kill-switch=Task3, 원자적 install/unreadable fail-safe/no orphan shim=Task6. Acceptance Bar 1(pause-override.jsonl)=Task7/9/10, 2(kill --all→dead)=Task3/9/10, 3(learning provenance 일치·미포인터 blocked)=Task4/5/10/11, 4(installer fail-safe)=Task6/11, 5(G1/G2/G3+단조성)=Task12.
- **불변식:** 단조성(W5 capture/bundle이 W1 validator 통과)=Task12, assurance 상한 A2(learning DSSE는 report-level 축, 등급 상향 없음)=Architecture/Task5, worker self-seal 불가·Emitter/EventLog만 SoT=전 Task runlog 경유, fail-closed(부분점수 없음)=Task4/6.
- **W1~W2 재사용/의존:** `canonical_hash`/`EventLog`/`render_status`/`STATUS_DOMAIN`/`build_bundle`/`sign_dsse`/`gen_operator_keypair`(W1), `append_runlog`/`event_hash`/`verify_runlog`/`derive_liveness`/`HEARTBEAT_TTL_SECONDS`/`WorkerSupervisor`/`WorkerHandle`/`Scheduler`/`faultkit`(W2)를 재정의 없이 재사용. `EventLog.read`/`WorkerSupervisor.handles` 접근자만 필요 시 additive 확장(재작성/삭제 메서드 없음, 이전 웨이브 테스트 그린 재확인).
- **새 함수/모듈:** `witnessd/pause.py`(`derive_pause_state`/`append_user_pause`/`append_user_resume`/`assert_not_paused`/`PauseError`), `witnessd/killswitch.py`(`kill_all`), `witnessd/learning.py`(`build_learning_delta`/`validate_learning_delta_provenance`/`promote_learning_delta`), `witnessd/installer.py`(`atomic_install`/`atomic_upgrade`/`list_orphan_shims`/`InstallerError`), `faultkit.pause_race`, `__main__` 서브커맨드.
- **Placeholder 금지:** Depone 계약 바인딩(`ingest_signed_evidence_bundle`/`verify_signed_bundle`/`build_bundle` subject 규약)은 "실제 코드로 확정"으로 정확히 지시, 그 외 determinate 코드(pause/kill/learning/installer)는 전량 제시. 앞 태스크에서 정의되지 않은 타입/함수 참조 없음.
- **오픈결정:** keyless 서명·docker A2는 Residual 절에 명시적으로 웨이브 밖 deferred로 반영, swappable 설계로 계약 변경 없이 후속 교체 가능함을 기록.
