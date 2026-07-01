# W1 — Evidence substrate + observer 분리 + A1/A2 실증 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (권장) 또는 superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** shell lane 하나를 실행해 관측자-분리 캡처 → capture-manifest + prev_capture 체인 + operator Ed25519 DSSE 서명을 native 방출하고, 별도 Depone(비실행 검증기)이 그 바이트에서 A1(및 uid 격리 호스트에서 A2)을 재도출하게 만든다. 이 웨이브가 "완료=관측자-서명 바이트" 논제 전체의 증명이다.

**Architecture:** Python 3.10+ 표준 라이브러리만. Evidence Emitter가 append-only 서명 event log(SoT)에 쓰는 유일한 지점이고, run-state/status는 그 projection. 서명은 `openssl` CLI(subprocess). witnessd는 Depone 계약(`capture-manifest`/`runner-receipt`/`evidence-substrate` 스키마, canonical hashing)을 만족하는 아티팩트만 방출하고, 검증은 전적으로 Depone이 한다.

**Tech Stack:** Python stdlib(`json`, `hashlib`, `subprocess`, `pathlib`, `os`, `argparse`, `unittest`), `openssl` CLI(Ed25519). 외부 의존성/`pyproject` 금지.

**계약 근거(정확한 필드는 아래 파일에서 확인):** `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/{capture_bridge,observe,isolation,sign,seal,observer_provenance,paired_run,evidence_substrate}.py`, `/depone/verify/{engine,evidence_contract}.py`. 이 repo들은 별개다 — witnessd는 이 함수들을 **재구현하지 않고**, 이들이 **받아들이는 아티팩트를 생산**한다.

---

## 사전 준비 (Task 0)

### Task 0: Repo 스캐폴드 + Depone 설치

**Files:**
- Create: `witnessd/__init__.py`, `tests/__init__.py`, `fixtures/w1/.gitkeep`, `scripts/.gitkeep`, `README.md`, `.gitignore`

- [ ] **Step 1: witnessd repo git-init + 디렉터리**
```bash
cd /home/ubuntu/witnessd && git init
mkdir -p witnessd/adapters tests fixtures/w1 scripts docs/plans
printf '__pycache__/\n*.pyc\n.venv/\nkeys/\n' > .gitignore
touch witnessd/__init__.py witnessd/adapters/__init__.py tests/__init__.py
```
- [ ] **Step 2: Depone validator import 가능 확인**
```bash
python3 -c "from depone.agent_fabric.capture_bridge import validate_capture_manifest; print('depone ok')"
```
Expected: `depone ok` (안 되면 `pip install --no-deps /home/ubuntu/depone-assurance-repair`).
- [ ] **Step 3: Commit**
```bash
git add -A && git commit -m "chore: scaffold witnessd repo for W1"
```

---

## Task 1: canonical_hash (Depone와 바이트 동일)

**Files:**
- Create: `witnessd/canonical.py`
- Test: `tests/test_canonical.py`

- [ ] **Step 1: 실패 테스트** — witnessd canonical_hash가 Depone `_sha256_json`/`canonical_hash`와 **정확히 같은 바이트**를 내는지 교차검증.
```python
import unittest
from witnessd.canonical import canonical_hash
from depone.agent_fabric.claim_gate import canonical_hash as depone_hash

class TestCanonical(unittest.TestCase):
    def test_matches_depone(self):
        obj = {"b": 1, "a": [3, 2], "nested": {"z": "x"}}
        self.assertEqual(canonical_hash(obj), depone_hash(obj))
    def test_key_order_independent(self):
        self.assertEqual(canonical_hash({"a":1,"b":2}), canonical_hash({"b":2,"a":1}))
```
- [ ] **Step 2: 실패 확인** — `python3 -m unittest tests.test_canonical -v` → FAIL (module 없음).
- [ ] **Step 3: 최소 구현**
```python
import hashlib, json
from typing import Any

def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
```
- [ ] **Step 4: 통과 확인** — `python3 -m unittest tests.test_canonical -v` → PASS. (Depone 해시와 불일치면 Depone `claim_gate.canonical_hash`/`_sha256_json` 실제 구현을 읽어 정확히 맞출 것.)
- [ ] **Step 5: Commit** — `git commit -am "feat: canonical_hash byte-identical to Depone"`

---

## Task 2: EventLog — append-only runlog 체인 (M1, SoT 쓰기 유일 지점)

**Files:**
- Create: `witnessd/eventlog.py`
- Test: `tests/test_eventlog.py`

runlog 체인은 capture-manifest 체인(`prev_capture_hash`, Depone 대상)과 **별개**다. 이벤트 kind=`witnessd-runlog-event`, 링크 필드 `prev_event_hash`(§6.0.3). genesis만 `prev_event_hash == null`.

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.eventlog import EventLog
from witnessd.canonical import canonical_hash

class TestEventLog(unittest.TestCase):
    def test_chain_links_and_genesis_null(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            e1 = log.append({"kind": "witnessd-runlog-event", "event": "team-start"})
            e2 = log.append({"kind": "witnessd-runlog-event", "event": "dispatch"})
            self.assertIsNone(e1["prev_event_hash"])
            self.assertEqual(e2["prev_event_hash"], canonical_hash(e1))
    def test_append_only_no_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(os.path.join(d, "runlog.jsonl"))
            log.append({"kind":"witnessd-runlog-event","event":"a"})
            self.assertFalse(hasattr(log, "update") or hasattr(log, "delete"))
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `EventLog(path)`: `append(event)`가 `prev_event_hash`(직전 이벤트의 canonical_hash, 첫 이벤트는 None)와 `seq`를 붙여 jsonl 한 줄로 append하고 그 이벤트 dict를 반환. 파일에 오직 append만(재작성/수정 메서드 없음). state projection은 별도(Task 11 status)에서 읽기전용으로 파생.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: append-only hash-chained runlog (event-log substrate)`

---

## Task 3: status render — evidence-pending 하드 규칙 (§7.6 구조적 게이트)

**Files:**
- Create: `witnessd/status.py`
- Test: `tests/test_status.py`

모든 사용자향 상태 출력은 `render_status()` 하나를 경유하고, 출력 도메인은 enum으로 고정. "VERIFIED"/"DONE"/"COMPLETE" 단독 성공 문자열 금지.

- [ ] **Step 1: 실패 테스트**
```python
import unittest
from witnessd.status import render_status, STATUS_DOMAIN

class TestStatus(unittest.TestCase):
    def test_output_in_enum_domain(self):
        self.assertIn(render_status(pending=3, verdict=None), STATUS_DOMAIN)
    def test_no_success_theater(self):
        for s in STATUS_DOMAIN:
            self.assertNotIn("VERIFIED", s); self.assertNotIn("COMPLETE", s)
    def test_pending_shown_until_depone(self):
        self.assertIn("evidence-pending", render_status(pending=3, verdict=None))
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `STATUS_DOMAIN` = 유한 집합(`evidence-pending`, `emit-refused`, `blocked`, `refuted`, 그리고 Depone verdict를 pass-through한 `A0`/`A1`/`A2`만). `render_status(pending, verdict)`는 verdict가 없으면 `"{n} captures pending Depone verification (evidence-pending)"`, 있으면 Depone 값 pass-through. 임의 문자열/성공 함의어 생성 금지.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: render_status enum gate (evidence-pending hard rule)`

---

## Task 4: observer 분리 강제 (E1)

**Files:**
- Create: `witnessd/observer.py`
- Test: `tests/test_observer_separation.py`

Depone 계약(`observe.enforce_observer_separation`): observer 출력(`--out`/`--log`)의 부모가 runner 샌드박스 **안**이면 `ERR_OBSERVER_NOT_SEPARATED`. 불변식(§4.1 B6): `runner_sandbox ∩ evidence_dir = ∅`, `∩ observer-owned = ∅`.

- [ ] **Step 1: 실패 테스트**
```python
import unittest, tempfile, os
from witnessd.observer import assert_separated, ObserverSeparationError

class TestSep(unittest.TestCase):
    def test_inside_sandbox_refused(self):
        with tempfile.TemporaryDirectory() as s:
            out = os.path.join(s, "capture.json")   # inside runner sandbox
            with self.assertRaises(ObserverSeparationError):
                assert_separated(runner_sandbox=s, out_path=out)
    def test_outside_ok(self):
        with tempfile.TemporaryDirectory() as s, tempfile.TemporaryDirectory() as o:
            assert_separated(runner_sandbox=s, out_path=os.path.join(o, "capture.json"))  # no raise
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `assert_separated(runner_sandbox, out_path)`: `os.path.commonpath` 로 out_path의 부모가 runner_sandbox 내부면 `ObserverSeparationError("ERR_OBSERVER_NOT_SEPARATED")`. **부분 산출 금지** — 검사가 실패하면 어떤 파일도 쓰지 않는다. (Depone `observe.enforce_observer_separation` 시맨틱과 일치시킬 것.)
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: enforce observer/runner separation (fail-closed, no partial output)`

---

## Task 5: shell 어댑터 — lane 실행 + command_receipts (E3)

**Files:**
- Create: `witnessd/adapters/shell.py`
- Test: `tests/test_shell_adapter.py`

- [ ] **Step 1: 실패 테스트** — 셸 커맨드 리스트를 실행하고 각 커맨드의 `command`(list[str])와 int `exit_code`를 담은 `command_receipts`(비어있지 않음), `touched_files`, `test_output.status ∈ {not-run,passed,failed,error}` 를 반환.
```python
import unittest, tempfile
from witnessd.adapters.shell import run_shell_lane

class TestShell(unittest.TestCase):
    def test_receipts_shape(self):
        with tempfile.TemporaryDirectory() as s:
            r = run_shell_lane(sandbox=s, commands=[["sh","-c","echo hi > f.txt"]])
            self.assertTrue(r["command_receipts"])
            self.assertIsInstance(r["command_receipts"][0]["exit_code"], int)
            self.assertIn(r["test_output"]["status"], {"not-run","passed","failed","error"})
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `run_shell_lane(sandbox, commands, ...)`: 각 커맨드를 `subprocess.run(cwd=sandbox, capture_output=True)`로 실행, receipt에 `command`+`exit_code`(+ stdout/stderr 요약) 기록. touched files는 실행 전후 파일 스냅샷 diff. 프로하이빗 에이전트 토큰(codex/claude/opencode) 방지는 W4 어댑터 관심사이나, shell lane도 argv 스캔 훅을 남겨둠(no-op).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: shell adapter with command_receipts + touched-files diff`

---

## Task 6: observer_capture 빌더 (E3 shape, observed_by 강제)

**Files:**
- Modify: `witnessd/observer.py`
- Test: `tests/test_observer_capture.py`

- [ ] **Step 1: 실패 테스트** — `build_observer_capture(lane_result, ...)`가 `observed_by == "depone-observer"`, `command_receipts` 비어있지 않음, `test_output.status` enum, `touched_files ⊆ allowed_touched_files` 를 만족하는 dict를 반환. Depone `capture_bridge` required 필드 전부 포함.
```python
import unittest
from witnessd.observer import build_observer_capture
from depone.agent_fabric.capture_bridge import validate_capture_manifest  # 필드 요구 확인용

class TestOC(unittest.TestCase):
    def test_observed_by_and_shape(self):
        oc = build_observer_capture(command_receipts=[{"command":["sh","-c","true"],"exit_code":0}],
                                    touched_files=["f.txt"], allowed_touched_files=["f.txt"],
                                    test_output={"status":"passed"})
        self.assertEqual(oc["observed_by"], "depone-observer")
        self.assertTrue(oc["command_receipts"])
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `build_observer_capture(...)`가 정확한 필드로 dict 생성. **정확한 required 키·중첩 구조는 `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/capture_bridge.py`의 `_check_observer_capture_shape`를 읽어 1:1로 맞춘다**(observed_by, command_receipts[*].{command,exit_code}, test_output.status, touched_files, allowed_touched_files 등). 범위 밖 touched → 이후 manifest invalid가 되게 그대로 방출(위조 방지는 Depone이).
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: observer_capture builder matching Depone capture_bridge shape`

---

## Task 7: capture-manifest + prev_capture 체인 (E2/E8)

**Files:**
- Create: `witnessd/capture.py`
- Test: `tests/test_capture_manifest.py`

- [ ] **Step 1: 실패 테스트** — `build_capture_manifest(fixture, observer_capture, assurance, prev_capture_hash, isolation=None)` 이 `validate_capture_manifest(m) == []` 를 만족(A1). 체인: 두 manifest에 대해 Depone `verify_capture_chain([m1,m2])` 통과, m2.prev == canonical_hash(m1).
```python
import unittest
from witnessd.capture import build_capture_manifest
from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import verify_capture_chain
from witnessd.canonical import canonical_hash

class TestManifest(unittest.TestCase):
    def test_a1_manifest_valid(self):
        m = _make_a1_manifest()   # helper uses build_capture_manifest
        self.assertEqual(validate_capture_manifest(m), [])
        self.assertEqual(m["assurance"], "A1-local-observed")
    def test_chain(self):
        m1 = _make_a1_manifest(prev=None)
        m2 = _make_a1_manifest(prev=canonical_hash(m1))
        self.assertEqual(verify_capture_chain([m1, m2])["ok"] if isinstance(verify_capture_chain([m1,m2]),dict) else None, None)  # adapt to real return
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `build_capture_manifest(...)`: kind=`agent-fabric-capture-manifest`, `schema_version`=Depone 값(`capture_bridge.py`에서 확인), `assurance`(A1/A2), `source_fixture_hash == _sha256_json(fixture)`, `observer_capture` + `observer_capture_hash == _sha256_json(observer_capture)`, `prev_capture_hash`, A2면 `isolation` + `isolation_hash == _sha256_json(isolation)`. **A2 `isolation` dict는 손수 작성하지 않고 Depone `from depone.agent_fabric.isolation import probe_isolation_facts`를 직접 import해 실측 facts로 채운다**(runner receipt의 `runner_uid` 사용, uid 격리 호스트). **필드·해시함수는 capture_bridge.py 실제 코드로 확정**(`_sha256_json` vs canonical_hash 일치 여부 포함). `verify_capture_chain`의 실제 입력/반환 형태를 `evidence_substrate.py`에서 확인해 테스트 assertion을 맞춘다.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: capture-manifest + prev_capture chain (Depone-valid A1)`

---

## Task 8: Ed25519 DSSE 서명 (E6/E7 signing) — openssl CLI

**Files:**
- Create: `witnessd/signing.py`
- Test: `tests/test_signing.py`

- [ ] **Step 1: 실패 테스트** — ephemeral Ed25519 keypair(openssl) 생성 → witnessd가 DSSE 봉투 서명 → Depone `sign.verify_dsse_envelope(env, pub)` True; tamper/위조는 False.
```python
import unittest, tempfile
from witnessd.signing import gen_operator_keypair, sign_dsse
from depone.agent_fabric.sign import verify_dsse_envelope

class TestSign(unittest.TestCase):
    def test_roundtrip_and_forgery(self):
        with tempfile.TemporaryDirectory() as d:
            priv, pub = gen_operator_keypair(d)
            env = sign_dsse({"payloadType":"application/vnd.in-toto+json","payload":"e30="}, priv, key_id="op1")
            self.assertTrue(verify_dsse_envelope(env, pub))
            env["payload"] = "eyJ4IjoxfQ=="   # tamper
            self.assertFalse(verify_dsse_envelope(env, pub))
```
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `gen_operator_keypair(dir)`: `openssl genpkey -algorithm Ed25519`. `sign_dsse(envelope, priv, key_id)`: DSSE PAE(`"DSSEv1 <len> <payloadType> <len> <payload>"`)를 openssl로 서명해 signatures 추가. **PAE 인코딩·scheme 문자열은 Depone `sign.py`(`sign_dsse_envelope`, scheme `DSSE-Ed25519-openssl-cli`)와 정확히 일치**시켜야 `verify_dsse_envelope`가 통과. openssl 부재 → `ERR_OPENSSL_UNAVAILABLE`, 서명 실패 → `ERR_DSSE_SIGN_FAILED`.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: Ed25519 DSSE signing via openssl CLI (Depone-verifiable)`

---

## Task 9: runner-receipt (E5, runner_kind=manual)

**Files:**
- Create: `witnessd/receipt.py`
- Test: `tests/test_runner_receipt.py`

- [ ] **Step 1: 실패 테스트** — `build_runner_receipt(...)`가 `paired_run.validate_runner_receipt(r) == []`, `source_hashes.receipt == canonical_hash(receipt without source_hashes)`(§4.6), `runner_kind == "manual"`(∈ `VALID_RUNNERS`).
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — kind=`agent-fabric-runner-receipt`, schema Depone 값, `runner_kind="manual"`, `arm`, `invocation`(비어있지 않음), `source_hashes` 계산 시 **자기 자신(source_hashes 키)을 제외**하고 canonical_hash. 정확한 required 필드는 `paired_run.py`의 `validate_runner_receipt`로 확정.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: runner-receipt (runner_kind=manual, self-hash excludes source_hashes)`

---

## Task 10: evidence-substrate 번들 + OTel + evidence-contract (E7/E9)

**Files:**
- Create: `witnessd/substrate.py`
- Test: `tests/test_substrate.py`

- [ ] **Step 1: 실패 테스트** — `build_bundle(manifest, artifacts, priv, pub)`가 in-toto Statement v1 + DSSE(서명) + **인라인 `otel_spans`** 를 담고, Depone `evidence_substrate.ingest_signed_evidence_bundle(bundle, pub, artifact_paths)` 이 `signature_verified == True` + 전 subject `verified`; 미서명이면 `signatures == []`로 정직 표기(assurance 미상향). `validate_evidence_contract(...)`가 enforcement directive ≥1 확인.
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — `build_bundle`: subject digest = 각 아티팩트의 sha256, in-toto Statement v1 predicate에 `assurance`(manifest에서 복사, 상한 A2) 포함, DSSE 서명(Task 8), `otel_spans`는 **날조 금지**(실측 usage만; 없으면 정적 span). `build_evidence_contract`: `evidence-contract.json`(schema `v105.verify_wedge`) + `git-diff-name-only.txt`/`git-diff.patch`/`exit-code.txt`. 정확한 구조는 `evidence_substrate.build_evidence_bundle`/`verify/evidence_contract.py`로 확정.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: evidence-substrate bundle (in-toto+DSSE+inline OTel) + evidence-contract`

---

## Task 11: Evidence Emitter (SoT 쓰기 유일 지점) + trusted-observer-provenance (E6)

**Files:**
- Create: `witnessd/emitter.py`
- Test: `tests/test_emitter.py`

- [ ] **Step 1: 실패 테스트** — `emit_lane_evidence(lane_result, evidence_dir, priv, ...)`가 capture-manifest + runner-receipt + bundle + evidence-contract + `trusted-observer-provenance`(Ed25519 DSSE, Task 8/`observer_provenance.build_signed_trusted_observer_provenance` 형태)를 evidence_dir에 방출하고, 모든 쓰기가 EventLog(Task 2)를 경유함을 assert(직접 파일쓰기 우회 금지). `validate_trusted_observer_provenance(m, evidence_path=, provenance=[p], public_key_path=<out-of-band>) == []`.
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — Emitter가 Task 6~10 산출을 조립해 evidence_dir(runner sandbox 밖)에 쓰고, 각 방출을 runlog event로 기록. public key는 evidence_dir **밖**(`DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE`)에 둔다. worker/observer는 Emitter 외 경로로 SoT에 못 씀.
- [ ] **Step 4: 통과 확인** — PASS.
- [ ] **Step 5: Commit** — `feat: Evidence Emitter (sole SoT writer) + trusted-observer-provenance`

---

## Task 12: CLI — `witnessd run` / `status` / `self-test`

**Files:**
- Create: `witnessd/__main__.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 실패 테스트** — `witnessd run --adapter shell --runner-sandbox <S> --out <O>/capture.json --log <O>/verify.log -- sh -c "echo hi"`: `<O>`가 `<S>` 밖이면 evidence 방출, 안이면 `ERR_OBSERVER_NOT_SEPARATED` + 산출 없음. `witnessd status`는 `evidence-pending`만(성공 문자열 금지). `witnessd self-test --all` exit 0.
- [ ] **Step 2: 실패 확인** — FAIL.
- [ ] **Step 3: 구현** — argparse CLI가 Task 4(분리 검사) → Task 5(shell) → Task 6(observer_capture) → Task 11(emit)을 배선. `status`는 Task 3 render_status. `self-test --all`은 각 모듈 `_self_test()` 실행.
- [ ] **Step 4: 통과 확인** — PASS. 수동: 위 run 명령을 실제로 돌려 evidence_dir에 파일 생성 확인.
- [ ] **Step 5: Commit** — `feat: witnessd CLI (run/status/self-test)`

---

## Task 13: W1 fixtures (A1, A2, chain) committed

**Files:**
- Create: `fixtures/w1/capture-manifest.json`(A1), `fixtures/w1/capture-manifest-a2.json`(A2), `fixtures/w1/chain/*.json`, `fixtures/w1/bundle.json`, `fixtures/w1/runner-receipt.json`, `fixtures/w1/evidence-contract.json`, `fixtures/w1/provenance.json`, `fixtures/w1/keys/operator.pub`(공개키만)

- [ ] **Step 1: 생성 스크립트로 fixture 방출** — `witnessd run`으로 A1 lane 실행, uid 격리 가능 호스트면 별도 시스템 유저로 A2 lane도 실행해 `fixtures/w1/` 아래 committed. **A2 fixture의 `isolation`은 Depone `from depone.agent_fabric.isolation import probe_isolation_facts`를 직접 import해 실측 facts(runner receipt의 `runner_uid`)로 채운다 — 손수 작성 금지.** 호스트가 uid 격리를 제공하지 못하면 A2 fixture를 파일 헤더 주석에 **"demonstration (host lacks uid isolation)"**으로 명시 한정하고, 이 경우 Bar 3의 A2 assert(`assurance == "A2-isolated-observed"`)는 **uid 격리 호스트 조건부**임을 revalidate가 인지하게 한다(uid 호스트에서만 A2 강제). private key는 커밋 금지(`.gitignore`), 공개키만.
- [ ] **Step 2: 커밋** — `git add fixtures/w1 && git commit -m "test: W1 committed evidence fixtures (A1/A2/chain)"`

---

## Task 14: `scripts/revalidate_w1.py` (G2 — Depone 재도출)

**Files:**
- Create: `scripts/revalidate_w1.py`

- [ ] **Step 1: 작성** — 설치된 Depone validator를 import해 committed fixture 바이트에서 재도출, 전부 assert 후 exit 0:
```python
import sys
from depone.agent_fabric.capture_bridge import validate_capture_manifest
from depone.agent_fabric.evidence_substrate import verify_capture_chain, ingest_signed_evidence_bundle
from depone.agent_fabric.sign import verify_signed_bundle
from depone.agent_fabric.paired_run import validate_runner_receipt
from depone.agent_fabric.observer_provenance import validate_trusted_observer_provenance
from depone.verify.evidence_contract import validate_evidence_contract
# ... load fixtures, assert:
#  A1: validate_capture_manifest(m1)==[] and m1["assurance"]=="A1-local-observed"
#  A2 (uid 격리 호스트 조건부): validate_capture_manifest(m2)==[]; uid 호스트 생성 fixture면 m2["assurance"]=="A2-isolated-observed", demonstration fixture면 A2 assert 스킵
#  chain ok, reorder/tamper blocked
#  verify_signed_bundle(bundle, pub) True; forged assurance "A3-*" -> False
#  validate_runner_receipt(r)==[]; ingest_signed_evidence_bundle(...).signature_verified True
#  validate_trusted_observer_provenance(...)==[]; validate_evidence_contract(...) directive>=1
print("W1 revalidate: PASS"); sys.exit(0)
```
정확한 함수 반환형은 실제 Depone 코드로 맞출 것.
- [ ] **Step 2: 실행** — `python3 scripts/revalidate_w1.py` → `W1 revalidate: PASS`, exit 0.
- [ ] **Step 3: 커밋** — `test: revalidate_w1 re-derives verdicts from committed bytes via Depone`

---

## Task 15: negative fixtures (tamper 회귀)

**Files:**
- Create: `fixtures/w1/negative/{observer_capture_hash_mismatch,stale_source_fixture_hash,unexpected_touched_files,forged_a3}.json`
- Modify: `scripts/revalidate_w1.py` (각 변형본이 blocked/refuted/False로 검출됨을 assert)

- [ ] **Step 1: 실패 테스트** — 각 tamper 변형본이 Depone에서 검출(invalid/blocked)됨을 revalidate가 assert. forged `A3-*`는 `verify_signed_bundle` False.
- [ ] **Step 2: 통과 확인** — `python3 scripts/revalidate_w1.py` 재실행 exit 0.
- [ ] **Step 3: 커밋** — `test: W1 negative/tamper regression fixtures`

---

## Task 16: 공통 게이트 G1/G2/G3 통과 + W1 완료

- [ ] **Step 1: G1** — `python3 -m witnessd self-test --all` → `N/N passed` exit 0.
- [ ] **Step 2: G2** — `python3 scripts/revalidate_w1.py` → PASS exit 0.
- [ ] **Step 3: G3** — witnessd가 방출한 evidence를 Depone repo에서 소비: `cd /home/ubuntu/depone-assurance-repair && python scripts/check_contract.py --tier changed && python scripts/dwm.py doctor` red 없음.
- [ ] **Step 4: W1 데모(서사)** — OMX `doctor`가 false-positive를 내는 zombie 시나리오를 witnessd 관측하 재현 → Depone이 A0/blocked를 재도출함을 보여주는 짧은 스크립트 커밋.
- [ ] **Step 5: 커밋 + W1 종료** — `git commit -m "feat: W1 complete — observer-signed evidence, Depone re-derives A1/A2"`

---

## Self-review 체크 (작성자 수행)

- **Spec 커버리지:** §5.1 W1 범위 항목(M1 event log=Task2, shell 어댑터=Task5, observer 분리=Task4, capture-manifest+chain=Task7, DSSE=Task8, runner-receipt E5=Task9, evidence-substrate E7=Task10, evidence-contract E9=Task10, provenance E6=Task11, Acceptance Bar=Task14/15/16) 모두 태스크 존재.
- **Placeholder:** 계약-바인딩 필드는 "Depone 파일에서 확정"으로 정확히 지시(추측 금지) — 별개 repo 계약이라 이게 올바른 grounding. 그 외 determinate 코드는 전량 제시.
- **타입 일관성:** `canonical_hash`/`build_capture_manifest`/`build_runner_receipt`/`build_bundle`/`emit_lane_evidence` 시그니처가 태스크 간 일치.
