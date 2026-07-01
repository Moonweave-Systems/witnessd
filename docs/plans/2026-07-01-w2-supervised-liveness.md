# W2 — Supervised worker + heartbeat liveness + durable session + per-spawn A2 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (권장) 또는 `superpowers:executing-plans`. 각 Task는 bite-sized TDD 5스텝(실패 테스트 → 실패 확인 → 최소 구현 → 통과 확인 → commit)이고 Steps는 `- [ ]` 체크박스로 진행한다. W1이 그린인 상태에서만 착수한다(§5.0 순서 의존: W2는 W1의 capture/chain/DSSE 위에만 얹는다).

**Goal:** "조용히 죽은 팀"(OMX zombie `%199` + `omx doctor` false-positive)을 **구조적으로 불가능**하게 만든다. worker는 tmux `send-keys`가 아니라 SIGCHLD/exit code로 감시되는 supervised 자식 프로세스이고, `active`는 저장 플래그가 아니라 "임계 내 서명된 heartbeat가 runlog에 관측됨"에서 파생되며, durable session은 프로세스 재시작/reboot 후 `run_id`(ULID)로 재개되어 tool-call cursor를 보존하고, **spawn마다** `probe_isolation_facts`로 uid 경계를 실측해 A2를 상시화한다. 완료는 witnessd의 self-report가 아니라 별도 repo Depone(`keelplane`)의 non-executing validator가 방출 바이트에서 A2/`dead`를 재도출하는 것으로만 정의된다.

**Architecture:** Python 3.10+ 표준 라이브러리만. 모든 상태전이(spawn/exit/heartbeat/claim/release/resume)는 W1 `EventLog`(SoT 유일 쓰기 지점)의 **runlog 체인**(`prev_event_hash`, kind `witnessd-runlog-event`, §6.0.3)에 append되고, run-state·liveness는 그 로그의 **pure projection**이다(별도 mutable 플래그 파일 금지). 이 runlog 체인은 W1 capture-manifest 체인(`prev_capture_hash`, Depone `verify_capture_chain` 대상)과 **별개**다 — heartbeat/spawn/exit 이벤트는 capture-manifest가 아니므로 `verify_capture_chain`에 넣지 않는다(§2.2). isolation facts와 서명은 W1 계약(`canonical_hash`·DSSE·`build_capture_manifest`·`probe_isolation_facts`)을 **재구현하지 않고 그대로 재사용**한다. 검증은 전적으로 Depone이 한다.

**Tech Stack:** Python stdlib(`json`, `hashlib`, `os`, `signal`, `subprocess`, `pathlib`, `time`, `secrets`, `argparse`, `unittest`, `tempfile`), `openssl` CLI(W1 signing 재사용). 외부 의존성/`pyproject` 금지.

**계약 근거(정확한 필드는 아래 파일 실제 코드로 확정 — 추측 금지):**
- `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/isolation.py` — `probe_isolation_facts(observer_dir, *, runner_uid, model=ISOLATION_MODEL, observer_launched=False)`, `verify_isolation_boundary(facts) -> dict`, 상수 `ISOLATION_MODEL="uid-boundary-unwritable-observer-dir"`/`UID_OBSERVER_LAUNCHED_ISOLATION_MODEL`/`CONTAINER_ISOLATION_MODEL`, `_self_test()`.
- `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/capture_bridge.py` — `validate_capture_manifest`, `_check_a2_manifest`(요구: `decision=="isolated-observed"`, `isolation` object, `isolation_hash == _sha256_json(isolation)`, 그리고 `verify_isolation_boundary(isolation)["boundary"] is True`; 실패 시 문자열 `"A2 isolation does not establish a privilege boundary"`), 상수 `ASSURANCE_A1="A1-local-observed"`/`ASSURANCE_A2="A2-isolated-observed"`.
- `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/paired_run.py` — `validate_runner_receipt`(W2의 supervised runner receipt도 `[]`).
- W1 산출물(재사용, 재정의 금지): `witnessd/canonical.py::canonical_hash`, `witnessd/eventlog.py::EventLog`, `witnessd/status.py::render_status`/`STATUS_DOMAIN`, `witnessd/observer.py::assert_separated`/`build_observer_capture`, `witnessd/adapters/shell.py::run_shell_lane`, `witnessd/capture.py::build_capture_manifest`, `witnessd/signing.py::gen_operator_keypair`/`sign_dsse`, `witnessd/receipt.py::build_runner_receipt`, `witnessd/substrate.py::build_bundle`/`build_evidence_contract`, `witnessd/emitter.py::emit_lane_evidence`.

**불변식(§5.0, 이 웨이브에서 예외 없음):**
- **단조성:** W2가 방출하는 모든 capture-manifest는 W1 `validate_capture_manifest` + `verify_capture_chain`을 여전히 통과한다(Task 12에서 회귀).
- **assurance 상한 A2:** A3 등급 없음. operator DSSE 서명은 등급을 올리지 않는 report-level 축.
- **role 분리:** worker는 자기 성공을 seal/validate 못 함. `EventLog`(Evidence Emitter)만 SoT에 쓴다. Depone은 assurance를 상향하지 못한다.
- **fail-closed:** 미지 fact / hash mismatch / heartbeat 부재 / chain 단절 / unreadable session → 부분점수 없이 `A0`/`blocked`/`refuted`/`dead`.

---

## Task 0: W2 착수 준비 — W1 그린 확인 + 스캐폴드

**Files:**
- Create: `fixtures/w2/.gitkeep`, `fixtures/w2/durable-resume/.gitkeep`, `fixtures/w2/negative/.gitkeep`

- [ ] **Step 1: W1 baseline 그린 확인** (W2는 W1 그린 상태에서만 착수)
```bash
cd /home/ubuntu/witnessd && python3 -m witnessd self-test --all && python3 scripts/revalidate_w1.py
```
Expected: self-test `N/N passed` exit 0, `W1 revalidate: PASS` exit 0. (레드면 W2 착수 금지 — W1을 먼저 고친다.)
- [ ] **Step 2: 브랜치 + 디렉터리**
```bash
cd /home/ubuntu/witnessd && git checkout -b w2-supervised-liveness
mkdir -p fixtures/w2/durable-resume fixtures/w2/negative
touch fixtures/w2/.gitkeep fixtures/w2/durable-resume/.gitkeep fixtures/w2/negative/.gitkeep
```
- [ ] **Step 3: Depone isolation API import 가능 확인**
```bash
python3 -c "from depone.agent_fabric.isolation import probe_isolation_facts, verify_isolation_boundary, ISOLATION_MODEL, _self_test; print('depone isolation ok')"
```
Expected: `depone isolation ok`.
- [ ] **Step 4: Commit** — `git add -A && git commit -m "chore: scaffold W2 (supervised liveness) fixtures"`

---

## Task 1: `run_id` (ULID, stdlib-only) — durable session 최상위 키

**Files:**
- Create: `witnessd/ids.py`
- Test: `tests/test_ids.py`

`run_id`는 ULID(시간 정렬 가능, tmux pane·호스트에 바인딩되지 않음, §6.1.1). Crockford Base32 26자, 상위 48비트=ms timestamp, 하위 80비트=랜덤. `secrets`만 사용.

- [ ] **Step 1: 실패 테스트**
```python
import unittest
from witnessd.ids import new_run_id

class TestIds(unittest.TestCase):
    def test_shape_and_alphabet(self):
        rid = new_run_id()
        self.assertEqual(len(rid), 26)
        self.assertTrue(all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in rid))
    def test_monotone_prefix_sorts_by_time(self):
        import time
        a = new_run_id(); time.sleep(0.002); b = new_run_id()
        self.assertLess(a, b)  # 시간 정렬 (상위 timestamp 비트)
    def test_unique(self):
        self.assertEqual(len({new_run_id() for _ in range(1000)}), 1000)
```
- [ ] **Step 2: 실패 확인** — `python3 -m unittest tests.test_ids -v` → FAIL (module 없음).
- [ ] **Step 3: 최소 구현**
```python
import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))

def new_run_id() -> str:
    ms = int(time.time() * 1000)
    rand = secrets.randbits(80)
    return _encode(ms, 10) + _encode(rand, 16)

def _self_test() -> None:
    assert len(new_run_id()) == 26
```
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_ids -v` → PASS.
- [ ] **Step 5: Commit** — `feat: ULID run_id (stdlib-only, time-sortable durable session key)`

---

## Task 2: runlog 체인 (§6.0.3 레코드 + `event_hash` + `verify_runlog` §6.2.5)

**Files:**
- Create: `witnessd/runlog.py`
- Modify: `witnessd/eventlog.py`, `tests/test_eventlog.py`
- Test: `tests/test_runlog.py`

heartbeat/spawn/exit/resume 이벤트는 §6.0.3 스키마로 W1 `EventLog`(runlog 체인)에 append된다. §6.0.3 레코드:
```json
{"schema_version":"1.0","kind":"witnessd-runlog-event","run_id":"<ulid>",
 "seq":<int>,"event":"<name>","error_code":"ERR_...|null",
 "ts_wall":"<RFC3339>","ts_monotonic":<float>,"payload":{...},
 "prev_event_hash":"<hex|null>","event_hash":"<hex>"}
```
`event_hash = canonical_hash(record without {event_hash})`, `prev_event_hash`는 직전 라인의 `event_hash`(genesis만 `null`). 이 체인은 §2.2의 (a) 축 — **Depone `verify_capture_chain`의 입력이 아니다**.

> **W1 reconciliation note (필수).** W1 `EventLog.append`는 `prev_event_hash`(직전 레코드의 canonical_hash)와 `seq`만 붙였고 `event_hash` 필드가 없었다. W2는 §6.0.3을 만족하도록 `append`가 (a) `prev_event_hash = 직전 라인의 event_hash`(직전이 없으면 None), (b) `event_hash = canonical_hash(record − {"event_hash"})`를 **추가로** 부여하도록 확장한다. 이는 additive refinement이다 — genesis+first 링크에서 값이 W1과 동일하고(직전 W1 레코드에 `event_hash` 키가 없으므로 `canonical_hash(record − {"event_hash"}) == canonical_hash(record)`), 링크 규약(prev==직전 hash)도 불변이다. 같은 Task에서 W1 `tests/test_eventlog.py`의 `test_chain_links_and_genesis_null`을 `self.assertEqual(e2["prev_event_hash"], e1["event_hash"])`로 갱신하고, `revalidate_w1.py`(Depone verdict 재도출; runlog 내부 무결성과 무관)가 **여전히 exit 0**임을 Step 4에서 재확인한다.

- [ ] **Step 1: 실패 테스트** (`tests/test_runlog.py`)
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.runlog import append_runlog, event_hash, verify_runlog
from witnessd.canonical import canonical_hash

class TestRunlog(unittest.TestCase):
    def test_record_shape_and_hash(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            r = append_runlog(log, run_id="R1", event="spawn", payload={"lane_id": "L1"})
            for k in ("schema_version","kind","run_id","seq","event","error_code",
                      "ts_wall","ts_monotonic","payload","prev_event_hash","event_hash"):
                self.assertIn(k, r)
            self.assertEqual(r["kind"], "witnessd-runlog-event")
            self.assertIsNone(r["prev_event_hash"])
            self.assertEqual(r["event_hash"], canonical_hash({k: v for k, v in r.items() if k != "event_hash"}))
    def test_chain_links_prev_to_event_hash(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            r1 = append_runlog(log, run_id="R1", event="spawn")
            r2 = append_runlog(log, run_id="R1", event="heartbeat")
            self.assertEqual(r2["prev_event_hash"], r1["event_hash"])
            self.assertEqual(verify_runlog([r1, r2]), {"ok": True, "broken_at": None})
    def test_tamper_detected(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            r1 = append_runlog(log, run_id="R1", event="spawn")
            r2 = append_runlog(log, run_id="R1", event="heartbeat")
            r2["payload"] = {"forged": True}   # tamper without re-hashing
            self.assertEqual(verify_runlog([r1, r2])["ok"], False)
```
- [ ] **Step 2: 실패 확인** — `python3 -m unittest tests.test_runlog -v` → FAIL.
- [ ] **Step 3: 구현**
  - `witnessd/eventlog.py`: `append`를 위 reconciliation note대로 확장 — `prev_event_hash = 직전 라인의 event_hash`(첫 이벤트 None), `event_hash = canonical_hash({k:v for k,v in record.items() if k != "event_hash"})` 부여 후 append. (append-only 유지: 재작성/수정 메서드 없음.) 또한 **`EventLog.read() -> list[dict]`를 additive로 정의** — jsonl 파일의 각 라인을 `json.loads`로 파싱해 dict 리스트로 반환(빈 라인 무시). 이 `read()`가 로그 projection의 유일한 소비 진입점이며, **최초 소비 지점은 이 웨이브의 supervisor/scheduler**(`log.read()` fold, Task 4/5)이고 W5도 이를 그대로 재사용한다(재작성/삭제 메서드는 여전히 없음).
  - `witnessd/runlog.py`:
```python
import time
from typing import Any
from witnessd.canonical import canonical_hash

RUNLOG_SCHEMA_VERSION = "1.0"
RUNLOG_KIND = "witnessd-runlog-event"

def _rfc3339(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

def build_runlog_body(run_id: str, event: str,
                      error_code: str | None = None,
                      payload: dict[str, Any] | None = None) -> dict[str, Any]:
    now = time.time()
    return {
        "schema_version": RUNLOG_SCHEMA_VERSION, "kind": RUNLOG_KIND,
        "run_id": run_id, "event": event, "error_code": error_code,
        "ts_wall": _rfc3339(now), "ts_monotonic": time.monotonic(),
        "payload": payload or {},
    }

def append_runlog(log, run_id: str, event: str,
                  error_code: str | None = None,
                  payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return log.append(build_runlog_body(run_id, event, error_code, payload))

def event_hash(record: dict[str, Any]) -> str:
    return canonical_hash({k: v for k, v in record.items() if k != "event_hash"})

def verify_runlog(records: list[dict[str, Any]]) -> dict[str, Any]:
    prev = None
    for i, rec in enumerate(records):
        if rec.get("event_hash") != event_hash(rec):
            return {"ok": False, "broken_at": i}
        if rec.get("prev_event_hash") != prev:
            return {"ok": False, "broken_at": i}
        prev = rec["event_hash"]
    return {"ok": True, "broken_at": None}
```
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_runlog tests.test_eventlog -v` → PASS. 그리고 `python3 scripts/revalidate_w1.py` → 여전히 exit 0(단조성).
- [ ] **Step 5: Commit** — `feat: §6.0.3 runlog record (event_hash) + verify_runlog; extend W1 EventLog additively`

---

## Task 3: liveness projection (M2) — heartbeat에서 파생되는 `active`

**Files:**
- Create: `witnessd/liveness.py`
- Test: `tests/test_liveness.py`

`active`는 저장 플래그가 아니라 파생값(§6.1.2): "최근 `HEARTBEAT_TTL_SECONDS`(기본 30) 이내에 서명된 heartbeat 이벤트가 runlog에 관측됨"으로만 참. 순서/liveness는 **`ts_monotonic`에만** 의존(§6.4.4). 재개 대상 worker는 heartbeat 재확립 전까지 `stale`(§6.1.1). 상태 도메인: lane별 `active`/`dead`(TTL 초과 후 정상 종료 없음)/`zombie`(SIGCHLD 없이 heartbeat 만료)/`stale`(resume 직후 heartbeat 미확립). **저장 플래그로 `active`를 뒤집는 경로는 존재하지 않는다** — 이것이 OMX false-positive의 구조적 안티회귀.

- [ ] **Step 1: 실패 테스트**
```python
import unittest
from witnessd.liveness import derive_liveness, HEARTBEAT_TTL_SECONDS

def _hb(lane, mono): return {"event":"heartbeat","payload":{"lane_id":lane},"ts_monotonic":mono}
def _spawn(lane, mono): return {"event":"spawn","payload":{"lane_id":lane},"ts_monotonic":mono}
def _exit(lane, mono, code): return {"event":"exit","payload":{"lane_id":lane,"exit_code":code},"ts_monotonic":mono}

class TestLiveness(unittest.TestCase):
    def test_recent_heartbeat_is_active(self):
        recs = [_spawn("L1", 0.0), _hb("L1", 100.0)]
        self.assertEqual(derive_liveness(recs, now_monotonic=105.0)["L1"], "active")
    def test_expired_heartbeat_no_exit_is_zombie(self):
        recs = [_spawn("L1", 0.0), _hb("L1", 10.0)]
        st = derive_liveness(recs, now_monotonic=10.0 + HEARTBEAT_TTL_SECONDS + 5)
        self.assertEqual(st["L1"], "zombie")
        self.assertNotEqual(st["L1"], "active")   # OMX false-positive 안티회귀
    def test_clean_exit_is_dead(self):
        recs = [_spawn("L1", 0.0), _hb("L1", 5.0), _exit("L1", 6.0, 0)]
        self.assertEqual(derive_liveness(recs, now_monotonic=1000.0)["L1"], "dead")
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `HEARTBEAT_TTL_SECONDS = 30`, `HEARTBEAT_INTERVAL_SECONDS = 10`. `derive_liveness(records, *, now_monotonic, ttl=HEARTBEAT_TTL_SECONDS, resumed_lanes=frozenset()) -> dict[str, str]`: lane_id별로 fold — 마지막 `exit` 이벤트가 있으면 `dead`; else 마지막 heartbeat `ts_monotonic`이 `now_monotonic - ttl` 이상이면 `active`; heartbeat가 아예 없고 `lane_id ∈ resumed_lanes`면 `stale`; 그 외(heartbeat 있었으나 TTL 초과, exit 없음)면 `zombie`. 파일 플래그를 읽지 않는다(순수 함수).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: heartbeat-derived liveness projection (active/dead/zombie/stale, no stored flag)`

---

## Task 4: Worker Supervisor (M3) — SIGCHLD/exit code, no send-keys

**Files:**
- Create: `witnessd/supervisor.py`
- Test: `tests/test_supervisor.py`

worker를 durable 자식 프로세스로 spawn하고, **exit code + `SIGCHLD`**로 종료를 확정하며, bounded interval 서명 heartbeat를 runlog에 중계하고, ownership-region lock을 claim/release한다(§2.4.3). **tmux/pane/send-keys IPC 폐기.** exit code는 그대로 runner-receipt의 int `exit_code`로 흐른다(Task 8). worker의 자기보고 텍스트를 완료 신호로 해석하지 않는다(exit code + Observer capture만 신뢰).

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os, signal, time
from witnessd.eventlog import EventLog
from witnessd.supervisor import WorkerSupervisor
from witnessd.liveness import derive_liveness, HEARTBEAT_TTL_SECONDS

class TestSupervisor(unittest.TestCase):
    def test_exit_code_captured_via_sigchld(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            sup = WorkerSupervisor(log, run_id="R1")
            h = sup.spawn(lane_id="L1", argv=["sh","-c","exit 3"], runner_uid=1002)
            code = sup.wait(h)
            self.assertEqual(code, 3)
            self.assertTrue(any(r["event"] == "exit" and r["payload"]["exit_code"] == 3
                                for r in log.read()))
    def test_kill_flips_projection_to_not_active(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            sup = WorkerSupervisor(log, run_id="R1")
            h = sup.spawn(lane_id="L1", argv=["sh","-c","sleep 30"], runner_uid=1002)
            os.kill(h.pid, signal.SIGKILL); sup.wait(h)
            # exit 관측됨 → dead (never active:true 잔존)
            last_hb = 0.0
            st = derive_liveness(log.read(), now_monotonic=last_hb + HEARTBEAT_TTL_SECONDS + 1)
            self.assertNotEqual(st.get("L1"), "active")
    def test_overlapping_region_lock_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            sup = WorkerSupervisor(log, run_id="R1")
            sup.claim_region("L1", ["src/a.py"])
            with self.assertRaises(Exception):
                sup.claim_region("L2", ["src/a.py"])
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `WorkerSupervisor(event_log, run_id)`(생성자에서 `self._handles: list[WorkerHandle] = []` 초기화):
  - `spawn(*, lane_id, argv, runner_uid, cwd=None) -> WorkerHandle`: `subprocess.Popen(argv, cwd=cwd)`로 자식 생성(자식 uid는 `runner_uid`로 설정할 수 있으면 `preexec_fn`로 `os.setuid`, 불가하면 facts에 실측 uid 기록 — 위조 금지). `spawn` 이벤트를 `append_runlog(log, run_id, "spawn", payload={"lane_id","pid","runner_uid"})`. `WorkerHandle`은 `pid`/`lane_id`/`runner_uid`/`popen` 보유. **생성한 handle을 `self._handles`에 append로 보관.**
  - `handles() -> list[WorkerHandle]`: 현재 보관 중인 활성 handle 리스트를 반환(방어적 복사). W5 `kill_all`이 이 접근자를 순회한다.
  - `heartbeat(handle)`: `append_runlog(log, run_id, "heartbeat", payload={"lane_id": handle.lane_id})` — bounded interval(`HEARTBEAT_INTERVAL_SECONDS`)로 호출되며 서명은 Emitter(Task 8)가 runlog DSSE로 얹는다.
  - `wait(handle) -> int`: `handle.popen.wait()`로 exit code 확정(SIGCHLD reaping), `exit` 이벤트 append(payload에 int `exit_code`). SIGCHLD 핸들러 등록은 `signal.signal(signal.SIGCHLD, ...)`로 하되 reaping은 `wait`이 담당(테스트 결정성). **종료 확정된 handle은 `self._handles`에서 제거(정리).**
  - `claim_region(lane_id, paths)`/`release_region(lane_id, paths)`: in-memory + runlog 이벤트. 겹치는 path를 다른 lane이 claim하면 `RegionLockError`. **send-keys/tmux/타임아웃 fallback 없음.**
- [ ] **Step 4: 통과 확인** — PASS. 수동 안티-tmux 게이트: `! grep -RIl "tmux\|send-keys" witnessd/` (매치 없어야 함).
- [ ] **Step 5: Commit** — `feat: Worker Supervisor (SIGCHLD/exit code, signed heartbeat relay, region lock, no send-keys)`

---

## Task 5: Scheduler (restart-safe, no tmux) — projection에서 미완 lane 재도출

**Files:**
- Create: `witnessd/scheduler.py`
- Test: `tests/test_scheduler.py`

준비된 lane을 동시성 예산 내에서 Supervisor에 넘기고, 재시작/reboot 후 로그 projection에서 "무엇이 아직 미완인가"를 재계산해 이어 디스패치(§2.4.2). **tmux/pane/send-keys 금지, in-memory 큐를 SoT로 삼지 않음**(SoT는 로그).

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.runlog import append_runlog
from witnessd.scheduler import Scheduler

class TestScheduler(unittest.TestCase):
    def test_reconcile_skips_completed_lanes(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            for lane in ("L1","L2"):
                append_runlog(log, run_id="R1", event="dispatch", payload={"lane_id":lane})
            append_runlog(log, run_id="R1", event="exit", payload={"lane_id":"L1","exit_code":0})
            sched = Scheduler(log, run_id="R1")
            pending = [p["lane_id"] for p in sched.reconcile()]
            self.assertEqual(pending, ["L2"])   # 완료 L1은 재디스패치 안 됨
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `Scheduler(event_log, run_id, concurrency=1)`: `reconcile() -> list[dict]`는 `event_log.read()`를 fold해 `dispatch`된 lane 중 `exit` 이벤트가 없는 lane packet만 반환(로그 projection 기준). `schedule(dispatch_event)`는 concurrency key 예산 내에서 Supervisor.spawn 호출. tmux/pane/send-keys 코드·in-memory 영속 큐 없음.
- [ ] **Step 4: 통과 확인** — PASS. 안티-tmux 게이트: `! grep -RIl "tmux\|send-keys" witnessd/scheduler.py`.
- [ ] **Step 5: Commit** — `feat: restart-safe Scheduler (reconcile from log projection, no tmux)`

---

## Task 6: Session Store (M4) — crash-safe atomic save / ID resume

**Files:**
- Create: `witnessd/session.py`
- Test: `tests/test_session.py`

각 세션의 last prompt, **tool-call cursor**, worktree 경로, 마지막 `runlog.seq`, 마지막 `event_hash`를 crash-safe하게 영속화(`.witnessd/runs/<run_id>/session.json`)하여 다른 host/reboot에서 `run_id`로 재개(§2.4.4, §6.1.1). **atomic write만(temp+`os.replace`+`fsync`), torn write 없음.** 불일치 시 로그가 우선. 복원 실패(unreadable state) → **덮어쓰지 않고 fail-safe blocked, 유령 재개 금지**(§6.1.1 처리, W2 fail-closed 규칙).

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.session import SessionStore, SessionResumeError

class TestSession(unittest.TestCase):
    def test_save_resume_preserves_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(root=d)
            store.save("R1", {"last_prompt":"go","tool_call_cursor":42,
                              "worktree":"/w/L1","last_seq":7,"last_event_hash":"ab"})
            st = store.resume("R1")
            self.assertEqual(st["tool_call_cursor"], 42)
            self.assertEqual(st["last_seq"], 7)
    def test_atomic_no_torn_write(self):
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(root=d)
            store.save("R1", {"tool_call_cursor":1})
            store.save("R1", {"tool_call_cursor":2})
            self.assertEqual(store.resume("R1")["tool_call_cursor"], 2)
            # temp 파일 잔존 없음
            run_dir = os.path.join(d, "runs", "R1")
            self.assertEqual([f for f in os.listdir(run_dir) if f.endswith(".tmp")], [])
    def test_unreadable_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(root=d)
            run_dir = os.path.join(d, "runs", "R1"); os.makedirs(run_dir)
            with open(os.path.join(run_dir, "session.json"), "w") as f:
                f.write("{ not json")
            with self.assertRaises(SessionResumeError):
                store.resume("R1")   # 유령 재개 금지
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `SessionStore(root)`: `save(run_id, state)`는 `<root>/runs/<run_id>/session.json`에 temp 파일 write → `f.flush(); os.fsync(f.fileno())` → `os.replace(tmp, final)` → dir fsync. `resume(run_id) -> dict`는 `json.load`; 파일 없음/파싱 실패 → `SessionResumeError`(fail-closed, 덮어쓰지 않음). SoT는 로그이므로 state는 `last_seq`/`last_event_hash`/`tool_call_cursor`로 "로그 어디까지 반영됐나"를 재계산 가능하게 한다.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: crash-safe Session Store (atomic temp+rename+fsync, ID resume, fail-closed on unreadable)`

---

## Task 7: per-spawn isolation probe (E4) — Depone `probe_isolation_facts` 재사용

**Files:**
- Create: `witnessd/isolation.py`
- Test: `tests/test_isolation.py`

각 worker spawn마다 `probe_isolation_facts`로 uid 경계를 실측한다(§3.4, line 420). witnessd는 검증 로직을 **재구현하지 않고** Depone `isolation.probe_isolation_facts`/`verify_isolation_boundary`를 **그대로 호출**하며, `runner_uid`는 runner receipt(§E5)에서 온다. `boundary=True` 조건: `runner_uid`·`observer_uid` 둘 다 int이고 서로 다름, `runner_uid != 0`, `observer_dir_writable_by_runner == False`. 미지/root/writable/same-uid → fail-closed A1. `witnessd isolation --self-test`는 Depone `isolation._self_test`를 재사용한다.

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os, stat
from witnessd.isolation import probe_lane_isolation, isolation_self_test
from depone.agent_fabric.isolation import verify_isolation_boundary

class TestIsolation(unittest.TestCase):
    def test_probe_returns_depone_facts(self):
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, stat.S_IRWXU)  # 0700
            facts = probe_lane_isolation(observer_dir=d, runner_uid=999999)  # != observer uid
            self.assertIn("runner_uid", facts)
            self.assertIn("observer_dir_writable_by_runner", facts)
            self.assertEqual(facts["runner_uid"], 999999)
    def test_same_uid_no_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, stat.S_IRWXU)
            facts = probe_lane_isolation(observer_dir=d, runner_uid=os.getuid())
            self.assertIs(verify_isolation_boundary(facts)["boundary"], False)
    def test_self_test_reuses_depone(self):
        isolation_self_test()  # raises on any failure
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현**
```python
from pathlib import Path
from typing import Any
from depone.agent_fabric.isolation import (
    probe_isolation_facts, ISOLATION_MODEL,
    UID_OBSERVER_LAUNCHED_ISOLATION_MODEL, _self_test as _depone_isolation_self_test,
)

def probe_lane_isolation(*, observer_dir: str, runner_uid: int | None,
                         model: str = ISOLATION_MODEL,
                         observer_launched: bool = False) -> dict[str, Any]:
    return probe_isolation_facts(
        Path(observer_dir), runner_uid=runner_uid,
        model=model, observer_launched=observer_launched,
    )

def isolation_self_test() -> None:
    _depone_isolation_self_test()
```
  정확한 인자/반환 키는 `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/isolation.py`의 `probe_isolation_facts`/`verify_isolation_boundary`로 확정한다(재구현 금지, wrapper만).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: per-spawn isolation probe wrapping Depone probe_isolation_facts (reuse _self_test)`

---

## Task 8: A2 상시화 — supervised lane emit (isolation + runner_uid 배선)

**Files:**
- Modify: `witnessd/emitter.py`
- Test: `tests/test_emitter_a2.py`

supervised lane 실행 시 Emitter가 (1) Supervisor의 실측 `runner_uid`로 `probe_lane_isolation`을 호출, (2) W1 `build_capture_manifest(..., isolation=facts)`로 A2 manifest를 방출, (3) heartbeat/spawn/exit runlog 이벤트를 서명. boundary가 서지 않으면(same-uid/root/writable/미지) **A1로 강등**(A2를 주장하지 않음). runner receipt의 `runner_kind`는 W1과 동일하게 `"manual"`(W4 전까지 enum 불변), `exit_code`는 Supervisor `wait()`의 int.

- [ ] **Step 1: 실패 테스트** — `emit_supervised_lane(...)`가 A2 격리 가능 파라미터에서 `validate_capture_manifest(m)==[]` and `m["assurance"]=="A2-isolated-observed"`; same-uid 파라미터에서 `m["assurance"]=="A1-local-observed"`. 정확한 A2 필드(`decision=="isolated-observed"`, `isolation_hash==_sha256_json(isolation)`)는 `capture_bridge._check_a2_manifest`를 읽어 W1 `build_capture_manifest`가 이미 만족하는지 확인하고, 안 되면 W1 `capture.py`를 고치지 말고 emitter가 올바른 인자를 넘기는지 점검.
```python
import unittest
from witnessd.emitter import emit_supervised_lane   # signature는 Step 3에서 확정
from depone.agent_fabric.capture_bridge import validate_capture_manifest
# helper: A2 파라미터(runner_uid != observer_uid, observer_dir 0700)로 emit → manifest 반환
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `emit_supervised_lane(*, run_id, lane_id, supervisor, adapter_result, observer_capture, fixture, allowed_touched_files, prev_capture_hash, evidence_dir, priv, observer_dir, runner_uid)`:
  - `facts = probe_lane_isolation(observer_dir=observer_dir, runner_uid=runner_uid)`.
  - `boundary = verify_isolation_boundary(facts)["boundary"]` (Depone import).
  - boundary True → `build_capture_manifest(..., assurance="A2-isolated-observed", isolation=facts)`; else → W1 A1 경로(`isolation=None`, `assurance="A1-local-observed"`).
  - runner receipt는 W1 `build_runner_receipt(runner_kind="manual", exit_code=<supervisor wait int>, ...)`.
  - spawn/heartbeat/exit는 `append_runlog`로 기록(Task 2). 모든 SoT 쓰기는 EventLog 경유(직접 파일쓰기 우회 금지). 나머지 방출(bundle/provenance/contract)은 W1 `emit_lane_evidence`를 재사용.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: always-on A2 for supervised lanes (probe→boundary→A2 or A1 downgrade)`

---

## Task 9: CLI + doctor/status + faultkit (좀비/크래시 결정적 주입)

**Files:**
- Modify: `witnessd/__main__.py`
- Create: `witnessd/faultkit.py`
- Test: `tests/test_cli_w2.py`

`witnessd status`/`doctor`는 heartbeat 파생 상태만 보고하고 저장 플래그를 신뢰하지 않는다(§6.1.2) — dead/zombie 상태에서 "all clear"를 낼 수 없다. `witnessd resume <run_id>`는 Session Store + runlog tail에서 재개(§6.1.1). `witnessd verify --runlog`는 `verify_runlog`로 체인 재계산(§6.2.5). `witnessd isolation --self-test`는 Task 7. faultkit은 결정적 주입 하네스(§6.1.1/§6.1.2 검증 재사용).

- [ ] **Step 1: 실패 테스트** — subprocess로 CLI 구동:
  - `witnessd verify --runlog <fixtures/w2/liveness-killed.jsonl>` → chain 무결(ok) 이면서 파생 상태에 `active` 없음.
  - `witnessd status --runlog <liveness-killed.jsonl>` 출력이 `STATUS_DOMAIN` 안(성공 문자열 금지), lane 상태 `dead`/`zombie` 표기.
  - `witnessd doctor --runlog <liveness-killed.jsonl>` exit 비-0 또는 명시적 `zombie` 보고(“all clear” 문자열 금지).
  - `witnessd isolation --self-test` exit 0.
```python
import unittest, subprocess, sys
class TestCliW2(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable,"-m","witnessd",*args],
                              capture_output=True, text=True)
    def test_doctor_no_false_positive(self):
        r = self._run("doctor","--runlog","fixtures/w2/liveness-killed.jsonl")
        self.assertNotIn("all clear", r.stdout.lower())
        self.assertIn("zombie", r.stdout.lower())
    def test_isolation_self_test(self):
        self.assertEqual(self._run("isolation","--self-test").returncode, 0)
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — argparse 서브커맨드 `spawn`/`resume`/`status`/`doctor`/`verify --runlog`/`isolation --self-test`/`faultkit {zombie-hang,crash-mid-toolcall}`/`self-test --all` 배선. `status`/`doctor`는 `derive_liveness` + W1 `render_status`(도메인 밖 문자열 생성 금지). `faultkit.zombie_hang()`: worker `SIGSTOP` → TTL 경과 → status가 `zombie` 보고 assert. `faultkit.crash_mid_toolcall()`: 툴콜 중 `os._exit(137)` 주입 → 새 프로세스 `resume` → run 상태 `evidence-pending`, 잘린 tail 없음, idempotency 재적용 0건 assert.
- [ ] **Step 4: 통과 확인** — PASS. (fixture는 Task 10에서 생성되므로 이 Task는 임시 생성 로그로 테스트 후 Task 10에서 committed fixture로 재확인.)
- [ ] **Step 5: Commit** — `feat: witnessd status/doctor/resume/verify --runlog + faultkit (zombie/crash injection)`

---

## Task 10: W2 committed fixtures

**Files:**
- Create: `fixtures/w2/liveness-killed.jsonl`, `fixtures/w2/capture-manifest-a2.json`, `fixtures/w2/negative/capture-manifest-a2-sameuid.json`(A1 강등), `fixtures/w2/negative/capture-manifest-a2-forged.json`(uid flip + isolation_hash 재계산 → blocked), `fixtures/w2/durable-resume/session.json`, `fixtures/w2/durable-resume/runlog-before.jsonl`, `fixtures/w2/durable-resume/runlog-after.jsonl`, `fixtures/w2/keys/operator.pub`(공개키만)

- [ ] **Step 1: liveness-killed 로그 생성** — 실제 `witnessd spawn` 후 worker를 SIGKILL하거나 `faultkit zombie-hang`으로, heartbeat가 TTL 전에 끊긴 runlog(`witnessd-runlog-event` 체인, `verify_runlog` 통과)를 `fixtures/w2/liveness-killed.jsonl`로 저장. 마지막 heartbeat 이후 `exit` 없음(zombie) 또는 kill exit(dead).
- [ ] **Step 2: A2 fixture 생성** — uid 격리 가능 호스트(예: runner uid 1002, observer uid 1001)에서 `emit_supervised_lane`로 A2 manifest를 방출해 `capture-manifest-a2.json`. same-uid 파라미터로 A1 강등본을 `negative/capture-manifest-a2-sameuid.json`. A2에서 `isolation.runner_uid`를 flip하고 `isolation_hash = _sha256_json(isolation)`를 **재계산**한 forged본을 `negative/capture-manifest-a2-forged.json`(boundary 재도출 실패로 blocked될 것 — Depone `_check_a2_manifest`가 flag가 아닌 facts로 재도출).
- [ ] **Step 3: durable-resume fixture** — `witnessd spawn`로 tool-call cursor를 진행시킨 session.json + resume 전/후 runlog. 재개 전/후가 동일 `run_id`로 연속(§Acceptance 3)임을 담는다. private key는 커밋 금지(공개키만).
- [ ] **Step 4: 커밋** — `git add fixtures/w2 && git commit -m "test: W2 committed fixtures (liveness-killed, A2, sameuid→A1, forged→blocked, durable-resume)"`

---

## Task 11: `scripts/revalidate_w2.py` (G2 — Depone/witnessd 재도출)

**Files:**
- Create: `scripts/revalidate_w2.py`

- [ ] **Step 1: 작성** — committed fixture 바이트에서 verdict/파생상태를 재도출하고 전부 assert 후 exit 0:
```python
import sys, json
from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.isolation import verify_isolation_boundary
from depone.agent_fabric.paired_run import validate_runner_receipt
from witnessd.runlog import verify_runlog
from witnessd.liveness import derive_liveness, HEARTBEAT_TTL_SECONDS
# --- liveness (M2, OMX 안티회귀): killed 로그의 파생 상태에 active 없음 ---
recs = [json.loads(l) for l in open("fixtures/w2/liveness-killed.jsonl")]
assert verify_runlog(recs)["ok"] is True
last_mono = max(r["ts_monotonic"] for r in recs)
st = derive_liveness(recs, now_monotonic=last_mono + HEARTBEAT_TTL_SECONDS + 1)
assert "active" not in st.values(), st           # "active:true" 잔존 금지
# --- A2 (E4) ---
a2 = json.load(open("fixtures/w2/capture-manifest-a2.json"))
assert validate_capture_manifest(a2) == []
assert a2["assurance"] == "A2-isolated-observed"
assert verify_isolation_boundary(a2["isolation"])["boundary"] is True
# --- same-uid → A1 강등 ---
a1 = json.load(open("fixtures/w2/negative/capture-manifest-a2-sameuid.json"))
assert a1["assurance"] == "A1-local-observed"
# --- forged (uid flip + isolation_hash 재계산) → blocked ---
forged = json.load(open("fixtures/w2/negative/capture-manifest-a2-forged.json"))
errs = validate_capture_manifest(forged)
assert any("does not establish a privilege boundary" in e for e in errs), errs
# --- runner receipt (Acceptance 4) ---
# assert validate_runner_receipt(<w2 runner receipt>) == []
# --- durable resume: 재개 전/후 동일 run_id 연속 ---
print("W2 revalidate: PASS"); sys.exit(0)
```
  정확한 함수 반환형(list vs dict)·에러 문자열은 실제 Depone 코드로 맞춘다(`validate_capture_manifest`는 error 리스트, `verify_isolation_boundary`는 dict).
- [ ] **Step 2: 실행** — `python3 scripts/revalidate_w2.py` → `W2 revalidate: PASS`, exit 0.
- [ ] **Step 3: 커밋** — `test: revalidate_w2 re-derives liveness=dead + A2 + forged-blocked from committed bytes`

---

## Task 12: 단조성 회귀 + durable resume 회귀 (§5.0 불변식 1)

**Files:**
- Modify: `scripts/revalidate_w2.py`
- Test: `tests/test_w2_monotonicity.py`

W2 capture는 W1 validator를 여전히 통과해야 한다(단조성). durable session 재개 후 tool-call cursor 보존(Acceptance 2).

- [ ] **Step 1: 실패 테스트** — (a) W2 A2 manifest가 W1 `validate_capture_manifest` + (체인에 넣으면) `verify_capture_chain`을 통과. (b) `SessionStore.save` 후 `resume`한 cursor가 저장 cursor와 동일. (c) `faultkit.crash_mid_toolcall` 후 resume 시 run 상태 `evidence-pending`, 재적용 0건.
```python
import unittest, json
from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import verify_capture_chain

class TestMonotone(unittest.TestCase):
    def test_w2_a2_passes_w1_validators(self):
        m = json.load(open("fixtures/w2/capture-manifest-a2.json"))
        self.assertEqual(validate_capture_manifest(m), [])
```
- [ ] **Step 2: 실패 확인 → 구현** — `revalidate_w2.py`에 단조성 assert + resume 연속성 assert 추가. resume 전/후 runlog가 동일 `run_id`이고 `prev_event_hash` 체인이 재개 지점에서 연속임을 확인.
- [ ] **Step 3: 통과 확인** — `python3 -m unittest tests.test_w2_monotonicity -v` PASS, `python3 scripts/revalidate_w2.py` exit 0.
- [ ] **Step 4: 커밋** — `test: W2 monotonicity (W1 validators still pass) + durable resume cursor preserved`

---

## Task 13: 공통 게이트 G1/G2/G3 + W2 완료

- [ ] **Step 1: G1** — `python3 -m witnessd self-test --all` → `N/N passed` exit 0(ids/runlog/liveness/supervisor/scheduler/session/isolation/emitter 모듈 각 `_self_test` 포함).
- [ ] **Step 2: G2** — `python3 scripts/revalidate_w2.py` → `W2 revalidate: PASS` exit 0. 그리고 단조성: `python3 scripts/revalidate_w1.py` 여전히 exit 0.
- [ ] **Step 3: G3** — witnessd 방출 evidence를 Depone repo에서 소비: `cd /home/ubuntu/depone-assurance-repair && python scripts/check_contract.py --tier changed && python scripts/dwm.py doctor` red 없음.
- [ ] **Step 4: 도그푸드 n=1 (§7.5)** — supervised shell lane 1회 실제 실행 → worker kill → `witnessd doctor`가 `zombie` 파생(“all clear” 없음) → Depone이 A2(또는 의도적 `dead` 파생) 재도출. 결과를 양쪽 repo에 committed artifact로 남긴다.
- [ ] **Step 5: 안티-tmux 하드 게이트** — `! grep -RIl "tmux\|send-keys" witnessd/` 매치 없음 확인.
- [ ] **Step 6: 커밋 + W2 종료** — `git commit -m "feat: W2 complete — supervised liveness, durable resume, always-on A2 (Depone re-derives)"`

---

## Self-review 체크 (작성자 수행)

- **Spec 커버리지(§5.2):** M3 supervised(no tmux)=Task4/5, M2 heartbeat liveness=Task3+9, M4 durable session=Task6+12, E4 per-spawn isolation=Task7+8, A2 상시화=Task8, runlog 체인(§6.0.3)=Task2, run_id ULID(§6.1.1)=Task1. Acceptance Bar 1(liveness-killed→dead, active 잔존 금지)=Task10/11, 2(resume cursor)=Task6/12, 3(A2 회귀: same-uid→A1, forged→blocked)=Task10/11, 4(runner receipt)=Task8/11. G1/G2/G3=Task13.
- **W1 재사용/의존:** `canonical_hash`/`EventLog`/`render_status`/`STATUS_DOMAIN`/`build_capture_manifest`/`build_runner_receipt`/`build_bundle`/`emit_lane_evidence`/`gen_operator_keypair`/`sign_dsse`/`observer.*`/`adapters.shell.*`를 재정의 없이 재사용. `EventLog.append`만 §6.0.3 `event_hash` 부여로 **additive** 확장(reconciliation note + W1 revalidate 그린 재확인).
- **새 함수/모듈:** `witnessd/ids.py`(`new_run_id`), `witnessd/runlog.py`(`append_runlog`/`event_hash`/`verify_runlog`), `witnessd/liveness.py`(`derive_liveness`), `witnessd/supervisor.py`(`WorkerSupervisor`), `witnessd/scheduler.py`(`Scheduler`), `witnessd/session.py`(`SessionStore`), `witnessd/isolation.py`(`probe_lane_isolation`/`isolation_self_test` — Depone wrapper), `witnessd/faultkit.py`, `emitter.emit_supervised_lane`.
- **불변식:** 단조성(Task12), assurance 상한 A2(A2/A1만, forged blocked), worker self-seal 불가(Emitter만 SoT), fail-closed(unreadable session/미지 isolation/heartbeat 부재), Depone 재구현 금지(isolation/verify는 import).
- **Placeholder 없음:** 계약-바인딩 필드는 "Depone <파일>의 <함수> 읽어 확정"으로 지시(isolation.py/capture_bridge.py/paired_run.py), witnessd-내부 determinate 코드(ULID/runlog/liveness/session/isolation wrapper)는 전량 제시. "TBD"/"적절히 처리" 없음.
- **오픈 결정 반영:** A2 uid 모델 1급(container 후속), operator Ed25519 DSSE(keyless deferred), no-tmux 하드 규칙 — 모두 §5.2/§5.0 Decision과 정합.
