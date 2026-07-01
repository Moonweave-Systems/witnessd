# W3 — Auto worktree + ownership lock + lane receipt + team-ledger fan-in (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (권장) 또는 `superpowers:executing-plans`. 각 Task는 bite-sized TDD 5스텝(실패 테스트 → 실패 확인 → 최소 구현 → 통과 확인 → commit)이며 Steps는 `- [ ]` 체크박스다.

**Goal:** "팀"을 켠다. 병렬 lane이 파일 소유 region을 **런타임 강제**로 claim/release하고(각 claim/release/conflict가 runlog event), auto worktree에서 실행되어 각 lane이 **read-only** worktree lane receipt(E5)를 남기며, overlap이 있으면 **passing merge receipt 없이는 머지 불가**하도록 team ledger(E10)가 fan-in한다. 완료 정의는 witnessd의 self-report가 아니라 별도 Depone(비실행 검증기)의 `build_team_ledger_verdict`가 witnessd가 방출한 바이트에서 `pass`/`blocked`(+`ERR_TEAM_LEDGER_*`)를 재도출하는 것이다.

**Architecture:** Python 3.10+ 표준 라이브러리만. W3은 W1이 세운 Evidence Emitter 위에 **얹기만** 한다 — 각 lane은 여전히 W1 `emit_lane_evidence`로 observer-분리 capture-manifest + prev_capture 체인 + operator Ed25519 DSSE를 방출하고(단조성), W3은 그 위에 (1) ownership-region lock, (2) auto worktree + worktree lane receipt, (3) evidence-next verdict, (4) team-ledger fan-in + merge receipt를 더한다. 검증은 전적으로 Depone이 한다. witnessd는 Depone 검증 함수를 **재구현하지 않고**, 이들이 받아들이는 아티팩트를 생산한다.

**Tech Stack:** Python stdlib(`json`, `hashlib`, `subprocess`, `pathlib`, `os`, `argparse`, `unittest`), `openssl` CLI(Ed25519, W1 `signing.py` 재사용), `git` CLI(worktree add / read-only diff). 외부 의존성/`pyproject` 금지.

**계약 근거 (정확한 필드는 아래 파일을 읽어 확정 — 추측 금지):**
- `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/worktree_receipt.py` — `build_worktree_lane_receipt`, `WORKTREE_LANE_RECEIPT_KIND="depone-worktree-lane-receipt"`, `WORKTREE_LANE_RECEIPT_SCHEMA_VERSION="0.1"`, `WorktreeReceiptError` 코드들.
- `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/team_ledger.py` — `build_team_ledger_verdict`, `validate_team_ledger`, `build_team_ledger_merge_receipt`, `_validate_ledger_header`, `_validate_lane`, `_validate_worktree_receipt`, `_validate_evidence_next_verdict`, `_validate_merge_receipt`, `_find_overlapping_touched_files`, `_observed_overlap_files`, 상수/enum(`TEAM_LEDGER_KIND`, `TEAM_LEDGER_SCHEMA_VERSION`, `TEAM_LEDGER_VERDICT_KIND`, `VALID_ENV_KINDS`, `VALID_ADAPTER_KINDS`, `VALID_LANE_VERIFICATION_STATES`) 및 `ERR_TEAM_LEDGER_*` 코드.

**W1에서 그대로 재사용(재정의 금지):** `canonical_hash`(`witnessd/canonical.py`), `EventLog`(`witnessd/eventlog.py`, `prev_event_hash`), `render_status`/`STATUS_DOMAIN`(`witnessd/status.py`), `assert_separated`/`build_observer_capture`(`witnessd/observer.py`), `run_shell_lane`(`witnessd/adapters/shell.py`), `build_capture_manifest`(`witnessd/capture.py`), `gen_operator_keypair`/`sign_dsse`(`witnessd/signing.py`), `build_runner_receipt`(`witnessd/receipt.py`), `build_bundle`/`build_evidence_contract`(`witnessd/substrate.py`), `emit_lane_evidence`(`witnessd/emitter.py`). **W2에서 재사용(재정의 금지):** `append_runlog`(`witnessd/runlog.py`, lock/audit event를 §6.0.3 형태로 남김), `WorkerSupervisor`/`WorkerHandle`(`witnessd/supervisor.py`), `probe_lane_isolation`(`witnessd/isolation.py`), `emit_supervised_lane`(`witnessd/emitter.py`, 각 lane을 supervised A2로 실행). W3 새 함수는 이들 **위에** 얹는다.

**불변식(§5.0, 협상 불가):** 단조성(각 lane capture가 W1 `validate_capture_manifest`+`verify_capture_chain`를 여전히 통과, W2 A2 격리 유지) · assurance 상한 A2(A3 없음) · worker self-seal 불가 · **Evidence Emitter만 SoT 쓰기**(W3 신규 아티팩트도 emitter/EventLog 경유) · fail-closed(부분점수 없음) · verdict boundary `raises_assurance=false`/`approves_merge=false`.

---

## File Structure (W3에서 신규/수정)

```
witnessd/
  lock.py            # NEW — ownership-region lock(M5): claim/release/conflict → runlog event, allowed_touched_files 산출
  worktree.py        # NEW — auto worktree(create_lane_worktree) + read-only worktree lane receipt(build_worktree_lane_receipt)
  team_ledger.py     # NEW — build_evidence_next_verdict, build_team_ledger, build_team_ledger_merge_receipt, classify_lane_kind
  fanin.py           # NEW — run_team(lock→worktree→W1 emit→receipt→evidence-next→ledger) 오케스트레이션
  __main__.py        # MODIFY — `witnessd team run` / `witnessd team-ledger` 서브커맨드 배선
tests/
  test_lock.py, test_worktree_receipt.py, test_evidence_next.py,
  test_lane_kind.py, test_team_ledger.py, test_team_fanin.py, test_cli_team.py   # NEW
fixtures/w3/
  team-ledger.json            # disjoint lanes, pass
  team-ledger-overlap.json    # 겹치는 touched files, merge receipt 없음 → blocked
  team-ledger-merged.json     # 겹치되 passing merge receipt 포함 → pass
  claim-conflict.jsonl        # 두 lane이 같은 region claim → 두 번째 거부 + claim-conflict event
  merge-receipt.json
  lane-a/ … lane-b/ …         # 각 lane evidence_dir(W1 아티팩트 + worktree-lane-receipt.json + evidence-next-verdict.json)
  keys/operator.pub           # 공개키만(개인키 커밋 금지)
scripts/
  revalidate_w3.py            # NEW — Depone validator로 재도출(G2)
```

---

## Task 0: W3 스캐폴드 + Depone team-ledger validator import 확인

**Files:**
- Create: `fixtures/w3/.gitkeep`
- Verify: W1 모듈 import 가능(회귀 없음)

- [ ] **Step 1: 디렉터리 + import 스모크**
```bash
cd /home/ubuntu/witnessd
mkdir -p fixtures/w3
touch fixtures/w3/.gitkeep
python3 -c "from witnessd.emitter import emit_lane_evidence; from witnessd.eventlog import EventLog; print('w1 ok')"
python3 -c "from depone.agent_fabric.team_ledger import build_team_ledger_verdict, build_team_ledger_merge_receipt; from depone.agent_fabric.worktree_receipt import build_worktree_lane_receipt, WORKTREE_LANE_RECEIPT_KIND; print('depone team ok')"
```
Expected: `w1 ok` / `depone team ok`. (실패 시 W1 완료 여부와 `pip install --no-deps /home/ubuntu/depone-assurance-repair` 확인.)
- [ ] **Step 2: 게이트 베이스라인(§5.0.5 순서 의존)** — `python3 scripts/revalidate_w1.py && python3 scripts/revalidate_w2.py` → `W1 revalidate: PASS` / `W2 revalidate: PASS`(W1·W2가 그린 상태여야 W3 착수 가능; W3 lane은 W2 supervised A2 위에 얹힌다).
- [ ] **Step 3: Commit** — `git commit -am "chore: scaffold W3 team fan-in wave"`

---

## Task 1: ownership-region lock (M5) — 런타임 강제 claim/release + claim-conflict event

**Files:**
- Create: `witnessd/lock.py`
- Test: `tests/test_lock.py`

lock은 dispatch **전** 파일/모듈 region을 claim하고, 같은 region을 두 lane이 claim하면 두 번째 dispatch를 **거부**하며 각 claim/release/conflict를 `EventLog`(W1)에 event로 남긴다. lock이 lane별로 확정한 region이 곧 그 lane의 `allowed_touched_files`(§4.3/§4.11 상한)다. 각 이벤트는 W2 `append_runlog`로 남기며 kind는 `witnessd-runlog-event`, 판별 필드는 §6.0.3 정본 키 `event ∈ {region-claim, region-release, claim-conflict}`(raw `type` 아님).

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.lock import OwnershipRegistry, ClaimConflictError

class TestLock(unittest.TestCase):
    def _reg(self, d):
        return OwnershipRegistry(EventLog(os.path.join(d, "runlog.jsonl")))

    def test_claim_returns_allowed_touched_files(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._reg(d)
            allowed = reg.claim(lane_id="lane-a", region=["pkg/a.py", "pkg/b.py"])
            self.assertEqual(allowed, ["pkg/a.py", "pkg/b.py"])

    def test_conflicting_region_second_claim_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._reg(d)
            reg.claim(lane_id="lane-a", region=["pkg/a.py"])
            with self.assertRaises(ClaimConflictError):
                reg.claim(lane_id="lane-b", region=["pkg/a.py"])

    def test_conflict_emits_claim_conflict_event(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "runlog.jsonl")
            reg = OwnershipRegistry(EventLog(path))
            reg.claim(lane_id="lane-a", region=["pkg/a.py"])
            try:
                reg.claim(lane_id="lane-b", region=["pkg/a.py"])
            except ClaimConflictError:
                pass
            import json
            types = [json.loads(l)["event"] for l in open(path)]
            self.assertIn("claim-conflict", types)

    def test_release_then_reclaim_ok(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self._reg(d)
            reg.claim(lane_id="lane-a", region=["pkg/a.py"])
            reg.release(lane_id="lane-a")
            reg.claim(lane_id="lane-b", region=["pkg/a.py"])  # no raise
```
- [ ] **Step 2: 실패 확인** — `python3 -m unittest tests.test_lock -v` → FAIL(module 없음).
- [ ] **Step 3: 최소 구현** — `OwnershipRegistry(event_log: EventLog, run_id: str = "team")`:
  - `claim(*, lane_id: str, region: list[str]) -> list[str]`: `region`을 정규화(정렬·중복제거, repo-relative posix)한 뒤, 이미 다른 lane이 소유한 파일과 교집합이 있으면 `claim-conflict` event를 append하고 `ClaimConflictError`를 raise(**부분 claim 금지** — 충돌 시 아무 region도 소유로 등록하지 않는다). 충돌 없으면 각 파일→lane_id 소유 매핑을 등록, `region-claim` event append, 정규화된 region 리스트 반환(= 이 lane의 `allowed_touched_files`).
  - `release(*, lane_id: str) -> None`: 해당 lane 소유 전부 해제 + `region-release` event append.
  - `owner_of(path: str) -> str | None` 조회 헬퍼.
  - event 형태: 각 claim/release/conflict를 W2 `append_runlog(self._log, self._run_id, event="region-claim"|"region-release"|"claim-conflict", payload={"lane_id":.., "region":[...], "conflict_files":[...](conflict일 때만)})`로 남긴다(§6.0.3 필수키 `event`/`run_id`/`seq`/`error_code`/`ts_*`/`payload` 구비 → `event` 기반 projection(liveness/pause)에 잡힘). **SoT 쓰기는 EventLog append만** — lock은 자체 파일을 따로 쓰지 않는다. (`from witnessd.runlog import append_runlog` 재사용, 재구현 금지.)
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_lock -v` → PASS.
- [ ] **Step 5: Commit** — `feat: ownership-region lock (runtime claim/release, claim-conflict runlog event)`

---

## Task 2: auto worktree 매니저 — create_lane_worktree (git worktree add)

**Files:**
- Create: `witnessd/worktree.py`
- Test: `tests/test_worktree_create.py`

lane마다 base commit에서 격리된 worktree를 자동 생성한다. runner sandbox = 이 worktree, evidence_dir는 그 **형제**(§4.1 포함관계 불변식: `runner_sandbox ∩ evidence_dir = ∅`).

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, subprocess, os
from pathlib import Path
from witnessd.worktree import create_lane_worktree

def _seed_repo(root):
    subprocess.run(["git","init","-q"], cwd=root, check=True)
    subprocess.run(["git","config","user.email","w@x.invalid"], cwd=root, check=True)
    subprocess.run(["git","config","user.name","w3"], cwd=root, check=True)
    (Path(root)/"seed.txt").write_text("x\n")
    subprocess.run(["git","add","-A"], cwd=root, check=True)
    subprocess.run(["git","commit","-qm","seed"], cwd=root, check=True)
    return subprocess.run(["git","rev-parse","HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()

class TestWorktreeCreate(unittest.TestCase):
    def test_creates_worktree_at_base_commit(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo"); os.mkdir(repo)
            base = _seed_repo(repo)
            wt = create_lane_worktree(repo_root=repo, lane_id="lane-a", base_commit=base, worktrees_dir=os.path.join(d,"worktrees"))
            self.assertTrue(os.path.isdir(wt))
            head = subprocess.run(["git","rev-parse","HEAD"], cwd=wt, capture_output=True, text=True).stdout.strip()
            self.assertEqual(head, base)
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 최소 구현** — `create_lane_worktree(*, repo_root: str, lane_id: str, base_commit: str, worktrees_dir: str) -> str`: `worktrees_dir/lane-<id>` 경로에 `git worktree add -b witnessd/<lane_id> <path> <base_commit>` 실행(브랜치 이름 충돌 시 `--force`/기존 정리 없이 결정적 이름 사용). 반환은 생성된 worktree 절대경로. git 실패는 `WorktreeError("ERR_WORKTREE_CREATE_FAILED", stderr)`로 fail-closed(부분 생성 금지). evidence_dir는 이 함수가 만들지 않는다(형제로 두는 책임은 Task 8 오케스트레이터).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: auto worktree manager (create_lane_worktree from base commit)`

---

## Task 3: worktree lane receipt (E5) — read-only, Depone 스키마 정합

**Files:**
- Modify: `witnessd/worktree.py`
- Test: `tests/test_worktree_receipt.py`

**계약 바인딩:** 필드/kind/schema/boundary는 `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/worktree_receipt.py::build_worktree_lane_receipt`를 읽어 **바이트 그대로** 맞춘다. 확인된 스키마: `kind="depone-worktree-lane-receipt"`, `schema_version="0.1"`, 필드 `worktree`(repo toplevel 절대경로 문자열), `branch`, `base_commit`, `head_commit`, `dirty`(bool), `dirty_files`(list), `changed_files`(= `git diff --name-only <base> HEAD --`, 정렬), `evidence_dir`(root-relative posix, 절대·`..` 금지), `command_receipts`(list[dict]), `boundary={"executes_commands":False,"launches_agents":False,"mutates_worktree":False,"git_read_only":True}`. **리시트는 read-only git state로만 생성**(커밋/머지 실행 금지). witnessd는 Depone의 `build_worktree_lane_receipt`를 **재구현**한다(별개 repo이므로 import해 쓰지 말고 동일 출력을 내는 witnessd 함수를 만든다 — Depone은 검증기이지 witnessd 런타임 의존성이 아니다).

Depone `team_ledger._validate_worktree_receipt`가 fan-in 시 강제하는 규칙(코드로 확인됨):
- 통과(passed) lane fan-in은 `dirty == False` 아니면 `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_DIRTY`.
- `changed_files ⊇ touched_files` 아니면 `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_TOUCHED_FILES_MISMATCH`.
- **passed lane(`required=True`)은 `changed_files == touched_files`**(under-report 금지) 아니면 `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_TOUCHED_FILES_UNDERREPORTED`. **주의:** 코드상 이 equality는 overlap 유무와 무관하게 **모든 passed lane**에 적용된다(`required = (state=="pass")`). §4.7 서술("overlap+merge-required lane equality, 그 외 superset")은 이 코드의 부분집합이므로, 안전하게 **fan-in 대상 lane은 항상 `touched_files == changed_files`로 방출**한다. `base_commit==lane.start_commit`/`head_commit==lane.end_commit`/`evidence_dir==lane.evidence_dir` 정합도 필수(불일치 시 각 `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_*_MISMATCH`).

- [ ] **Step 1: 실패 테스트** — 커밋된 clean worktree에서 witnessd `build_worktree_lane_receipt`가 Depone 산출과 동일 형태를 내는지: kind/schema 상수 일치, `dirty is False`, `changed_files == ["seed.txt"]`(변경 1건 커밋 후), `boundary["git_read_only"] is True`.
```python
import unittest, tempfile, subprocess, os
from pathlib import Path
from witnessd.worktree import build_worktree_lane_receipt
from depone.agent_fabric.worktree_receipt import (
    WORKTREE_LANE_RECEIPT_KIND, WORKTREE_LANE_RECEIPT_SCHEMA_VERSION)

class TestWReceipt(unittest.TestCase):
    def test_shape_and_clean_dirty(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)/"repo"; repo.mkdir()
            subprocess.run(["git","init","-q"], cwd=repo, check=True)
            subprocess.run(["git","config","user.email","w@x.invalid"], cwd=repo, check=True)
            subprocess.run(["git","config","user.name","w3"], cwd=repo, check=True)
            (repo/"seed.txt").write_text("a\n")
            subprocess.run(["git","add","-A"], cwd=repo, check=True)
            subprocess.run(["git","commit","-qm","seed"], cwd=repo, check=True)
            base = subprocess.run(["git","rev-parse","HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
            (repo/"seed.txt").write_text("b\n")
            subprocess.run(["git","commit","-am","change","-q"], cwd=repo, check=True)
            r = build_worktree_lane_receipt(worktree=str(repo), base_commit=base,
                                            evidence_dir="lane-a", commands=[{"command":"python3 -m unittest","exit_code":0}])
            self.assertEqual(r["kind"], WORKTREE_LANE_RECEIPT_KIND)
            self.assertEqual(r["schema_version"], WORKTREE_LANE_RECEIPT_SCHEMA_VERSION)
            self.assertIs(r["dirty"], False)
            self.assertEqual(r["changed_files"], ["seed.txt"])
            self.assertIs(r["boundary"]["git_read_only"], True)
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 최소 구현** — `build_worktree_lane_receipt(*, worktree: str, base_commit: str, evidence_dir: str, commands: list[dict] | None = None) -> dict`: read-only git(`rev-parse --show-toplevel`, `cat-file -e <base>^{commit}`, `rev-parse HEAD`, `branch --show-current`, `diff --name-only <base> HEAD --`, `status --porcelain=v1`)만 실행. `dirty = bool(dirty_files)`. 프로듀서 fail-closed 코드는 Depone `WorktreeReceiptError`와 동일 문자열: 빈 base_commit → `ERR_WORKTREE_RECEIPT_BASE_COMMIT_REQUIRED`, worktree 부재 → `ERR_WORKTREE_RECEIPT_REPO_MISSING`, git 실패 → `ERR_WORKTREE_RECEIPT_GIT_FAILED`, 절대경로/`..` evidence_dir → `ERR_WORKTREE_RECEIPT_PATH_INVALID`, non-object commands → `ERR_WORKTREE_RECEIPT_COMMAND_RECEIPTS_INVALID`. **정확한 필드 순서·경로 정규화는 worktree_receipt.py를 읽어 1:1로 맞춘다.**
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: read-only worktree lane receipt (E5, Depone worktree_receipt schema)`

---

## Task 4: evidence-next verdict emitter (passed lane 필수 아티팩트)

**Files:**
- Create: `witnessd/team_ledger.py`
- Test: `tests/test_evidence_next.py`

**계약 바인딩:** `team_ledger._validate_evidence_next_verdict`(확인됨)는 각 passed lane이 참조하는 verdict **파일**이 다음을 만족하길 요구한다 — 루트가 객체, `command == "evidence-next"`, `decision == "continue"`, `blocking_reasons`가 빈 리스트(없으면 `[]` 취급). 위반 코드: `ERR_TEAM_LEDGER_EVIDENCE_NEXT_VERDICT_INVALID`(command 불일치/비객체/비JSON), `ERR_TEAM_LEDGER_EVIDENCE_NEXT_NOT_CONTINUE`(decision≠continue 또는 blocking_reasons 존재). 파일은 ledger base dir에 **상대경로**(절대·base 이탈 → `ERR_TEAM_LEDGER_EVIDENCE_NEXT_VERDICT_PATH_INVALID`)이며 lane evidence_dir 아래에 둔다. witnessd는 이 verdict를 **자기 실행 결과의 정직한 파생**으로만 생성한다(성공 날조 금지 — lane이 blocked면 이 파일을 만들지 않는다).

- [ ] **Step 1: 실패 테스트**
```python
import unittest
from witnessd.team_ledger import build_evidence_next_verdict

class TestEvNext(unittest.TestCase):
    def test_continue_shape(self):
        v = build_evidence_next_verdict()
        self.assertEqual(v["command"], "evidence-next")
        self.assertEqual(v["decision"], "continue")
        self.assertEqual(v["blocking_reasons"], [])
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 최소 구현** — `build_evidence_next_verdict(*, blocking_reasons: list[str] | None = None) -> dict`: `{"command":"evidence-next","decision":"continue" if not blocking_reasons else "blocked","blocking_reasons": blocking_reasons or []}`. **정확한 최소 요구 키는 `_validate_evidence_next_verdict`를 읽어 확정**(위 3키가 검사 대상이며 그 외는 무시됨).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: evidence-next verdict emitter (team-ledger passed-lane requirement)`

---

## Task 5: lane_kind — read-only lane 처리(§4.12 구조적 에지케이스)

**Files:**
- Modify: `witnessd/team_ledger.py`
- Test: `tests/test_lane_kind.py`

**계약 근거(§4.12, 확정 처리):** Depone `_validate_lane`은 passed lane에 `touched_files ≥ 1`을 요구한다(`ERR_TEAM_LEDGER_TOUCHED_FILES_REQUIRED`). 따라서 파일을 하나도 바꾸지 않는 정당한 lane(검증-only/조사-only)은 team-ledger의 **passed(merge-bearing) lane으로 fan-in하지 않는다.** Orchestrator는 lane을 `lane_kind ∈ {"write","read-only"}`로 구분하고, read-only lane은 (a) 자기 축의 capture-manifest/observer_capture/runner-receipt는 정상 방출(W1 A0/A1/A2 재도출 그대로 가능), (b) team-ledger `lanes` 배열의 passed 코드 lane에는 **포함하지 않으며**, `verification_state:"blocked"`도 아니라 **runlog audit event로만** 남긴다.

- [ ] **Step 1: 실패 테스트**
```python
import unittest
from witnessd.team_ledger import classify_lane_kind

class TestLaneKind(unittest.TestCase):
    def test_no_touched_is_read_only(self):
        self.assertEqual(classify_lane_kind(touched_files=[]), "read-only")
    def test_touched_is_write(self):
        self.assertEqual(classify_lane_kind(touched_files=["pkg/a.py"]), "write")
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 최소 구현** — `classify_lane_kind(*, touched_files: list[str]) -> str`: `"write" if touched_files else "read-only"`. (오케스트레이터(Task 8)는 read-only lane을 ledger `lanes`에서 제외하고 W2 `append_runlog(log, run_id, event="read-only-lane-audit", payload={"lane_id":..})` runlog event로만 기록한다 — 그 배선은 Task 8에서 테스트.)
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: lane_kind classification (read-only lanes excluded from merge-bearing fan-in)`

---

## Task 6: team-ledger 빌더 + merge receipt — disjoint fan-in pass

**Files:**
- Modify: `witnessd/team_ledger.py`
- Test: `tests/test_team_ledger.py`

**계약 바인딩:** 헤더/lane 필드는 `team_ledger._validate_ledger_header`/`_validate_lane`을 읽어 확정. 확인된 요구:
- 헤더(전부 non-empty string): `kind="depone-team-ledger"`, `schema_version="0.1"`, `leader_objective`, `leader_id`, `start_commit`, `stop_rule`, `lanes`(비어있지 않은 리스트).
- lane 필드: `lane_id`(중복 금지 → `ERR_TEAM_LEDGER_LANE_ID_DUPLICATE`), `objective`, `start_commit`, `end_commit`, `evidence_dir`(passed lane은 실존 디렉터리여야 함 → 없으면 `ERR_TEAM_LEDGER_EVIDENCE_DIR_MISSING`), `env_kind ∈ {local,container,cloud}`, `runner_adapter_kind`/`team_adapter_kind ∈ VALID_ADAPTER_KINDS`(shell 포함), `verification_state ∈ {pass,blocked}`, `touched_files`(passed lane ≥1), `worktree_receipt`(passed lane 필수, base_dir 상대 JSON 경로), `evidence_next_verdict`(passed lane 필수, base_dir 상대 경로). cloud env passed lane은 `cloud_artifact`도 필수.
- `build_team_ledger_merge_receipt(*, lanes, files, conflict_events=None, decision="pass")`(Depone에 존재) → `{"command":"team-ledger-merge-receipt","schema_version":"1.0","decision","lanes","files","conflict_events"}`. witnessd는 동일 출력을 내는 함수를 제공(또는 Depone 함수 시그니처를 재현).

이 Task는 **disjoint(겹침 없음) lanes → `build_team_ledger_verdict(ledger, base_dir=...)["decision"] == "pass"`, `overlapping_touched_files == []`, `validate_team_ledger(...) == []`**를 목표로 한다. 테스트는 실제 git repo + worktree receipt 파일 + evidence-next verdict 파일을 base_dir에 배치해 Depone이 파일을 읽게 한다.

- [ ] **Step 1: 실패 테스트** — 헬퍼로 disjoint 2-lane 원장을 base_dir에 조립(각 lane: touched_files=changed_files 1건 서로 다른 파일, worktree-lane-receipt.json + evidence-next-verdict.json + evidence_dir 디렉터리 실존), `build_team_ledger` 반환을 Depone로 검증.
```python
import unittest, tempfile, json, os
from pathlib import Path
from witnessd.team_ledger import build_team_ledger, build_evidence_next_verdict
from witnessd.worktree import build_worktree_lane_receipt
from depone.agent_fabric.team_ledger import build_team_ledger_verdict, validate_team_ledger

class TestTeamLedger(unittest.TestCase):
    def test_disjoint_pass(self):
        # helper: two git lanes touching disjoint files, receipts+verdicts written under base_dir
        base_dir, ledger = _make_disjoint_ledger()   # returns (Path, dict)
        verdict = build_team_ledger_verdict(ledger, base_dir=base_dir)
        self.assertEqual(verdict["decision"], "pass")
        self.assertEqual(verdict["overlapping_touched_files"], [])
        self.assertEqual(validate_team_ledger(ledger, base_dir=base_dir), [])
        self.assertIs(verdict["boundary"]["raises_assurance"], False)
        self.assertIs(verdict["boundary"]["approves_merge"], False)
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 최소 구현** — `build_team_ledger(*, leader_objective, leader_id, start_commit, stop_rule, lanes: list[dict]) -> dict`: 헤더 상수(`kind`/`schema_version`)를 박고 lane dict 리스트를 조립. lane dict는 위 필수 필드를 담되 `worktree_receipt`/`evidence_next_verdict`는 **base_dir 상대 경로 문자열**이어야 한다(파일 자체는 Task 8 오케스트레이터가 방출). `build_team_ledger_merge_receipt(...)`도 witnessd 쪽에 제공(Depone과 동일 출력). **fan-in 대상은 write lane만**(Task 5). **정확한 lane 필드 키·env/adapter enum 값은 `_validate_lane`을 읽어 맞춘다.**
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: team-ledger builder + merge receipt (disjoint fan-in passes Depone verdict)`

---

## Task 7: overlap fan-in — merge receipt 없으면 blocked, 있으면 pass

**Files:**
- Modify: `tests/test_team_ledger.py`
- Test: 동일 파일에 overlap 케이스 추가

**계약 근거(확인됨):** `_find_overlapping_touched_files`는 **passed lane**들의 `_observed_overlap_files`(= worktree receipt의 `changed_files`, 없으면 `touched_files`)를 교차해 2개 이상 lane이 같은 파일을 소유하면 overlap으로 본다. overlap이 존재하면 `_validate_merge_receipt(required=True)` → merge_receipt 부재 시 `ERR_TEAM_LEDGER_MERGE_RECEIPT_REQUIRED`, 파일은 있으나 `decision != "pass"` → `ERR_TEAM_LEDGER_MERGE_RECEIPT_NOT_PASS`, 커버리지 부족(overlap 파일/lane_id 미포함) → `ERR_TEAM_LEDGER_MERGE_RECEIPT_COVERAGE_MISSING`. 에러가 하나라도 있으면 verdict `decision == "blocked"`.

- [ ] **Step 1: 실패 테스트** — (a) 겹치는 touched file을 가진 2 passed lane + merge_receipt 없음 → `decision=="blocked"`이고 `errors`에 `ERR_TEAM_LEDGER_MERGE_RECEIPT_REQUIRED` 포함, `overlapping_touched_files != []`. (b) 동일하되 passing `build_team_ledger_merge_receipt(lanes=[...], files=[overlap], decision="pass")`를 파일로 방출·참조 → `decision=="pass"`.
```python
    def test_overlap_without_merge_blocked(self):
        base_dir, ledger = _make_overlap_ledger(with_merge=False)
        v = build_team_ledger_verdict(ledger, base_dir=base_dir)
        self.assertEqual(v["decision"], "blocked")
        codes = {e["code"] for e in v["errors"]}
        self.assertIn("ERR_TEAM_LEDGER_MERGE_RECEIPT_REQUIRED", codes)
        self.assertTrue(v["overlapping_touched_files"])
    def test_overlap_with_passing_merge_pass(self):
        base_dir, ledger = _make_overlap_ledger(with_merge=True)
        v = build_team_ledger_verdict(ledger, base_dir=base_dir)
        self.assertEqual(v["decision"], "pass")
```
- [ ] **Step 2: 실패 확인** — FAIL(헬퍼 `_make_overlap_ledger` 미구현/미배선).
- [ ] **Step 3: 최소 구현** — 헬퍼가 두 lane의 changed_files/touched_files에 공통 파일을 넣도록 구성(단, passed lane은 `changed_files == touched_files` equality를 유지해야 하므로 overlap 파일을 두 lane 모두의 touched_files에 포함). `with_merge=True`면 witnessd `build_team_ledger_merge_receipt`로 receipt를 base_dir에 쓰고 ledger `merge_receipt`에 상대경로로 참조. **merge receipt의 `files`/`lanes`가 overlap 파일·lane_id를 모두 커버**하도록 구성(coverage 규칙).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: overlap fan-in requires passing merge receipt (blocked otherwise, ERR_TEAM_LEDGER_MERGE_RECEIPT_*)`

---

## Task 8: 팀 fan-in 오케스트레이터 — lock→worktree→W1 emit→receipt→evidence-next→ledger (단조성)

**Files:**
- Create: `witnessd/fanin.py`
- Test: `tests/test_team_fanin.py`

병렬 lane 파이프라인을 한 지점에서 배선한다. **각 lane은 W2 `WorkerSupervisor` + `emit_supervised_lane`(실측 `runner_uid` → `probe_lane_isolation` → A2)로 실행되며, 이는 내부적으로 W1 `emit_lane_evidence`를 재사용해 capture-manifest/runner-receipt/bundle/evidence-contract/provenance를 방출**(단조성 — W1 validator 전부 통과, 각 lane W2 A2 유지)하고, W3은 그 위에 lock claim → auto worktree → worktree lane receipt → evidence-next verdict → team-ledger를 얹는다. read-only lane은 ledger에서 제외하고 runlog audit event로만 남긴다. **모든 신규 아티팩트 쓰기도 `EventLog`(SoT)를 경유**한다(§5.0: Evidence Emitter만 SoT 쓰기).

- [ ] **Step 1: 실패 테스트** — `run_team(...)`가 (a) disjoint 2 write lane을 실행해 각 lane evidence_dir에 W1 아티팩트 + `worktree-lane-receipt.json` + `evidence-next-verdict.json`를 쓰고, (b) `team-ledger.json`을 방출해 `build_team_ledger_verdict(...)["decision"]=="pass"`, (c) **단조성**: 각 lane capture-manifest가 W1 `validate_capture_manifest(m)==[]`, 체인이 `verify_capture_chain([...])` 통과, (d) 같은 region을 claim하는 두 lane 구성 시 두 번째가 거부되고 runlog에 `claim-conflict` event.
```python
import unittest, json
from witnessd.fanin import run_team
from depone.agent_fabric.team_ledger import build_team_ledger_verdict
from depone.agent_fabric.capture_bridge import validate_capture_manifest

class TestFanin(unittest.TestCase):
    def test_disjoint_team_run_pass_and_monotone(self):
        result = run_team(_two_disjoint_lane_specs())   # returns dict with base_dir, ledger, lane manifests
        v = build_team_ledger_verdict(result["ledger"], base_dir=result["base_dir"])
        self.assertEqual(v["decision"], "pass")
        for m in result["lane_manifests"]:
            self.assertEqual(validate_capture_manifest(m), [])
    def test_claim_conflict_rejected(self):
        result = run_team(_two_conflicting_lane_specs())
        self.assertIn("claim-conflict", [e["event"] for e in result["runlog_events"]])
        self.assertNotIn("lane-b", {l["lane_id"] for l in result["ledger"]["lanes"]})  # 거부된 lane은 fan-in 안 됨
    def test_read_only_lane_excluded_from_ledger(self):
        result = run_team(_write_plus_readonly_lane_specs())
        ledger_ids = {l["lane_id"] for l in result["ledger"]["lanes"]}
        self.assertNotIn("lane-ro", ledger_ids)
        self.assertIn("read-only-lane-audit", [e["event"] for e in result["runlog_events"]])
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 최소 구현** — `run_team(lane_specs: list[dict], *, repo_root, out_dir, priv_key, pub_key) -> dict`:
  1. 단일 `EventLog`(SoT) + `OwnershipRegistry`(Task 1) 생성.
  2. 각 lane_spec에 대해: `registry.claim(lane_id, region=spec["region"])` — `ClaimConflictError`면 그 lane을 dispatch에서 제외하고 계속(거부는 이미 `claim-conflict` event로 기록됨).
  3. `create_lane_worktree`(Task 2)로 worktree 생성(runner sandbox), evidence_dir는 그 **형제**(§4.1 포함관계 불변식 준수, `assert_separated`로 재확인).
  4. worktree에서 lane worker를 W2 `WorkerSupervisor`(단일 supervisor, Task 8이 배선)로 spawn해 **실측 `runner_uid`**·exit_code를 얻고(SIGCHLD 확정) 변경을 lane 브랜치에 커밋(head_commit 확정). `build_observer_capture`(W1)로 observer capture, `allowed_touched_files`는 claim이 반환한 region.
  5. **W2 `emit_supervised_lane`로 evidence_dir에 방출** — supervisor 실측 `runner_uid` → W2 `probe_lane_isolation` → `verify_isolation_boundary` True면 A2(`isolation` 바인딩, `assurance="A2-isolated-observed"`), same-uid/root/writable/미지면 A1로 강등. `emit_supervised_lane`이 내부적으로 W1 `emit_lane_evidence`를 재사용하므로 W1 아티팩트(capture-manifest/runner-receipt/bundle/evidence-contract/provenance)도 그대로 방출(단조성 확보 + 각 lane W2 A2 유지, §5.3 Bar 4).
  6. `classify_lane_kind`(Task 5): read-only면 ledger에서 제외하고 W2 `append_runlog(log, run_id, event="read-only-lane-audit", payload={"lane_id":..})` event append; write면 `build_worktree_lane_receipt`(Task 3, `touched_files==changed_files` 보장) + `build_evidence_next_verdict`(Task 4)를 evidence_dir에 쓰고 ledger lane dict 구성.
  7. `registry.release(lane_id)`.
  8. write lane들로 `build_team_ledger`(Task 6) → `team-ledger.json` 방출. overlap이 있으면 `build_team_ledger_merge_receipt`로 merge receipt 방출·참조(Task 7).
  반환: `{"base_dir","ledger","lane_manifests","runlog_events"}`. **모든 파일 쓰기는 emitter/EventLog 경유** — fanin이 직접 SoT를 우회하지 않는다.
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_team_fanin -v` → PASS.
- [ ] **Step 5: Commit** — `feat: team fan-in orchestrator (lock→worktree→W1 emit→receipt→ledger, monotone)`

---

## Task 9: CLI — `witnessd team run` / `witnessd team-ledger`

**Files:**
- Modify: `witnessd/__main__.py`
- Test: `tests/test_cli_team.py`

- [ ] **Step 1: 실패 테스트** — `witnessd team run --repo <R> --out <O> --lane lane-a:pkg/a.py --lane lane-b:pkg/b.py`가 evidence_dir들 + `team-ledger.json`을 방출하고 exit 0; `witnessd team-ledger --ledger <O>/team-ledger.json --json`이 Depone `build_team_ledger_verdict` 결과를 pass-through로 출력(성공 문자열 날조 금지 — 검증 전 lane 상태는 `render_status`로 `evidence-pending`만). 같은 region 두 lane이면 두 번째 lane은 ledger에 없고 로그에 claim-conflict.
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — argparse 서브커맨드 `team run`(→ Task 8 `run_team` 배선), `team-ledger`(→ Depone `build_team_ledger_verdict` 호출해 verdict JSON 출력). 사용자향 상태는 `render_status`(W1)만 경유 — `VERIFIED`/`DONE`/`COMPLETE` 금지(§4.0-5). verdict의 `decision`/`errors`는 Depone 값 그대로 pass-through.
- [ ] **Step 4: 통과 확인** — PASS. 수동: 위 `team run`을 실제 git repo에서 돌려 파일 생성 확인.
- [ ] **Step 5: Commit** — `feat: witnessd team CLI (team run / team-ledger, evidence-pending gate)`

---

## Task 10: W3 fixtures committed (disjoint / overlap / merged / claim-conflict)

**Files:**
- Create: `fixtures/w3/team-ledger.json`, `fixtures/w3/team-ledger-overlap.json`, `fixtures/w3/team-ledger-merged.json`, `fixtures/w3/merge-receipt.json`, `fixtures/w3/claim-conflict.jsonl`, `fixtures/w3/lane-a/…`, `fixtures/w3/lane-b/…`(각 W1 아티팩트 + `worktree-lane-receipt.json` + `evidence-next-verdict.json`), `fixtures/w3/keys/operator.pub`(공개키만)

**Acceptance Bar(§5.3) 매핑:** committed `fixtures/w3/team-ledger.json`(disjoint, pass) + `fixtures/w3/team-ledger-overlap.json`(겹치는 touched files, merge receipt 없음) + `fixtures/w3/claim-conflict.jsonl`(claim-conflict 회귀).

- [ ] **Step 1: 생성 스크립트로 fixture 방출** — `witnessd team run`(Task 9)으로 disjoint 팀을 실행해 `fixtures/w3/` 아래 committed. overlap 원장은 동일 파일을 두 lane이 touch하도록 구성해 방출(merge receipt 없음 → blocked 재현). merged 원장은 passing merge receipt 포함. claim-conflict.jsonl은 같은 region 두 lane 실행의 runlog(포함: `region-claim`, `claim-conflict`). **개인키 커밋 금지(`.gitignore`), 공개키만.** 경로는 root-relative(§4.1 evidence_dir 규율): worktree는 evidence_dir의 형제.
- [ ] **Step 2: 로컬 재검증** — `python3 -c "from depone.agent_fabric.team_ledger import build_team_ledger_verdict; import json; ..."` 로 세 원장이 각각 pass/blocked/pass를 내는지 즉석 확인.
- [ ] **Step 3: 커밋** — `test: W3 committed team-ledger fixtures (disjoint pass / overlap blocked / merged pass / claim-conflict)`

---

## Task 11: `scripts/revalidate_w3.py` (G2 — Depone 재도출)

**Files:**
- Create: `scripts/revalidate_w3.py`

Acceptance Bar §5.3.2/§5.3.3/§5.3.4를 committed 바이트에서 Depone validator로 재도출하고 전부 assert 후 exit 0.

- [ ] **Step 1: 작성** — 설치된 Depone validator를 import해 재도출:
```python
import sys, json
from pathlib import Path
from depone.agent_fabric.team_ledger import build_team_ledger_verdict
from depone.agent_fabric.worktree_receipt import (
    WORKTREE_LANE_RECEIPT_KIND, WORKTREE_LANE_RECEIPT_SCHEMA_VERSION)
from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import verify_capture_chain

BASE = Path("fixtures/w3")

# 1) disjoint → pass, overlapping_touched_files == []
disjoint = json.loads((BASE/"team-ledger.json").read_text())
v = build_team_ledger_verdict(disjoint, base_dir=BASE)
assert v["decision"] == "pass", v["decision"]
assert v["overlapping_touched_files"] == [], v["overlapping_touched_files"]
assert v["boundary"]["raises_assurance"] is False
assert v["boundary"]["approves_merge"] is False

# 2) overlap without passing merge receipt → blocked + ERR_TEAM_LEDGER_* (merge receipt 관련)
overlap = json.loads((BASE/"team-ledger-overlap.json").read_text())
vo = build_team_ledger_verdict(overlap, base_dir=BASE)
assert vo["decision"] == "blocked", vo["decision"]
codes = {e["code"] for e in vo["errors"]}
assert any(c.startswith("ERR_TEAM_LEDGER_") and "MERGE_RECEIPT" in c for c in codes), codes

# 2b) merged (passing merge receipt) → pass
merged = json.loads((BASE/"team-ledger-merged.json").read_text())
assert build_team_ledger_verdict(merged, base_dir=BASE)["decision"] == "pass"

# 3) 각 lane receipt: kind/schema, fan-in lane dirty==False, changed_files == lane touched_files
for lane in disjoint["lanes"]:
    receipt = json.loads((BASE/lane["worktree_receipt"]).read_text())
    assert receipt["kind"] == WORKTREE_LANE_RECEIPT_KIND
    assert receipt["schema_version"] == WORKTREE_LANE_RECEIPT_SCHEMA_VERSION
    assert receipt["dirty"] is False
    assert sorted(receipt["changed_files"]) == sorted(lane["touched_files"])  # §4.7 passed-lane equality

# 4) 단조성: 각 lane capture-manifest가 W1 validator 통과 + 체인
for lane in disjoint["lanes"]:
    m = json.loads((BASE/lane["evidence_dir"]/"agent-fabric-capture-manifest.json").read_text())
    assert validate_capture_manifest(m) == [], m
    assert m["assurance"] == "A2-isolated-observed", m["assurance"]  # §5.3 Bar 4: 각 lane이 W2 A2 유지(uid 격리 호스트 생성 fixture)
# (체인 재도출: lane manifest들을 방출 순서대로 verify_capture_chain에 넣어 pass 확인 — 반환형은 W1 revalidate와 동일하게 맞춘다)

# 5) claim-conflict 회귀: 같은 region 두 번째 dispatch 거부 + claim-conflict event
events = [json.loads(l) for l in (BASE/"claim-conflict.jsonl").read_text().splitlines() if l.strip()]
assert any(e["event"] == "claim-conflict" for e in events)
# 거부된 lane(lane-b)이 disjoint/overlap 원장의 passed lanes에 fan-in되지 않았음도 별도 assert 가능

print("W3 revalidate: PASS"); sys.exit(0)
```
정확한 함수 반환형·필드는 실제 Depone 코드로 맞춘다(특히 `verify_capture_chain` 반환형은 W1 `revalidate_w1.py`와 동일하게).
- [ ] **Step 2: 실행** — `python3 scripts/revalidate_w3.py` → `W3 revalidate: PASS`, exit 0.
- [ ] **Step 3: 커밋** — `test: revalidate_w3 re-derives team fan-in verdicts from committed bytes via Depone`

---

## Task 12: negative/tamper 회귀 fixtures

**Files:**
- Create: `fixtures/w3/negative/{dirty_lane_receipt,touched_files_underreport,merge_receipt_not_pass,duplicate_lane_id}.json`
- Modify: `scripts/revalidate_w3.py`(각 변형이 대응 `ERR_TEAM_LEDGER_*`로 blocked됨을 assert)

fail-closed 규칙(§5.3) 회귀: (a) fan-in 대상 worktree가 `dirty==True` → `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_DIRTY`; (b) `changed_files ⊋ touched_files`(under-report) → `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_TOUCHED_FILES_UNDERREPORTED`; (c) merge receipt `decision != pass` → `ERR_TEAM_LEDGER_MERGE_RECEIPT_NOT_PASS`; (d) lane_id 중복 → `ERR_TEAM_LEDGER_LANE_ID_DUPLICATE`. 각각 verdict `decision=="blocked"`.

- [ ] **Step 1: 실패 테스트** — 각 tamper 변형본이 Depone에서 대응 코드로 blocked됨을 revalidate가 assert.
- [ ] **Step 2: 통과 확인** — `python3 scripts/revalidate_w3.py` 재실행 exit 0.
- [ ] **Step 3: 커밋** — `test: W3 negative/tamper regression (dirty/underreport/merge-not-pass/dup-lane)`

---

## Task 13: 공통 게이트 G1/G2/G3 통과 + W3 완료

- [ ] **Step 1: G1** — `python3 -m witnessd self-test --all` → `N/N passed` exit 0(W3 새 모듈 `lock`/`worktree`/`team_ledger`/`fanin` `_self_test()` 포함).
- [ ] **Step 2: G2** — `python3 scripts/revalidate_w3.py` → `W3 revalidate: PASS` exit 0. (그리고 회귀 없음: `python3 scripts/revalidate_w1.py` 여전히 PASS — 단조성.)
- [ ] **Step 3: G3** — witnessd가 방출한 evidence를 Depone repo에서 소비: `cd /home/ubuntu/depone-assurance-repair && python3 -m depone team-ledger --ledger /home/ubuntu/witnessd/fixtures/w3/team-ledger.json --json`(pass) 및 `python scripts/check_contract.py --tier changed && python scripts/dwm.py doctor` red 없음.
- [ ] **Step 4: W3 데모(서사)** — 두 lane이 같은 파일을 노려 병렬 dispatch되는 시나리오를 witnessd 관측하 재현 → lock이 두 번째를 claim-conflict로 거부하고, 그럼에도 overlap 원장을 억지로 만들면 Depone이 merge receipt 부재로 `blocked`를 재도출함을 보여주는 짧은 스크립트 커밋(OMX split-brain/merge 무증거 실패모드의 안티테제).
- [ ] **Step 5: 커밋 + W3 종료** — `git commit -m "feat: W3 complete — team fan-in with ownership lock, worktree receipts, merge-gated ledger"`

---

## Self-review 체크 (작성자 수행)

- **Spec 커버리지(§5.3):** ownership-lock M5=Task1, auto worktree=Task2, worktree lane receipt E5=Task3, evidence-next=Task4, read-only lane §4.12=Task5, team-ledger fan-in E10 disjoint=Task6, overlap+merge receipt=Task7, 오케스트레이터+단조성=Task8, CLI=Task9, Acceptance Bar 1~4=Task10/11/12, G1/G2/G3=Task13. 모두 Task 존재.
- **계약 grounding:** 모든 계약-바인딩 필드는 `worktree_receipt.py`/`team_ledger.py` 실제 코드로 확정(kind/schema 상수, `ERR_TEAM_LEDGER_*`/`ERR_WORKTREE_RECEIPT_*` 코드, enum, `changed_files==touched_files` equality의 코드상 범위). 추측/placeholder 없음.
- **W1 재사용:** `canonical_hash`/`EventLog`/`render_status`/`assert_separated`/`build_observer_capture`/`run_shell_lane`/`build_capture_manifest`/`sign_dsse`/`build_runner_receipt`/`build_bundle`/`emit_lane_evidence`를 재정의 없이 `fanin.run_team`에서 조립. 신규 함수(`OwnershipRegistry`/`create_lane_worktree`/`build_worktree_lane_receipt`/`build_evidence_next_verdict`/`classify_lane_kind`/`build_team_ledger`/`run_team`)만 추가.
- **불변식:** 단조성(Task8/11에서 각 lane capture가 W1 validator 통과 assert), Evidence Emitter만 SoT 쓰기(모든 신규 아티팩트 EventLog/emitter 경유), verdict boundary `raises_assurance=false`/`approves_merge=false`(Task6 assert), fail-closed(Task7/12 blocked 회귀), read-only lane §4.12 확정 처리(Task5).
- **오픈결정:** §5.3 Residual risk(실제 substrate 다양성·모델 라우팅·비용)는 W4로 명시 이월 — W3은 shell/단일 substrate 병렬만 증명하므로 이 wave 범위에 포함하지 않음.
```
