# P1 — External Team Pilot: production gate를 증거로 여는 프로토콜 (Operator + Tooling Plan)

> **성격:** 이 문서의 절반은 **운영자(사람) 프로토콜**이고 절반은 지원 툴링이다.
> 에이전트는 툴링 태스크만 수행할 수 있으며, **게이트 상태를 바꾸는 결정과 파일럿 실행 자체는
> 운영자(사용자)만 한다.** 에이전트가 `production_gate.status`를 `open`으로 바꾸는 것은
> 어떤 경우에도 금지된다 — 그것이 이 제품이 막으려는 증거 위조 그 자체다.

**Goal:** `fixtures/key-rotation/operator-key-archive.json`의 `production_gate.status = "blocked"`를
**진짜 external-team-pilot 증거 5종**으로 여는 경로를 준비한다. 게이트가 열려야 W6(keyless,
Sigstore Fulcio/Rekor)가 언블록된다. 규범 원문: `docs/ops/operator-key-rotation.md`(런북),
`scripts/revalidate_key_rotation.py`(검증기), `docs/plans/2026-07-02-w6-keyless-signing.md`(W6a).

## 무엇이 "external-team-pilot"으로 인정되는가 (런북 §Keyless Gate, 재서술)

- 로컬 개발자 dogfood **아님**, 손제작 fixture **아님**, CI-only run **아님**.
- 배포된 witnessd 런타임으로 실행된 **named team run**이고, Depone이 persisted 바이트에서 재도출 가능해야 함.
- 실무 해석(운영자 참고): 이 VM에서 개발 세션 중 돌리는 run은 전부 dogfood다. 인정받으려면 최소한
  (a) 설치된(installer 경유) witnessd로, (b) 개발 목적이 아닌 실제 팀 작업 1건을, (c) 시작·종료가
  deployment_record로 남게 실행해야 한다. 애매하면 **blocked 유지가 정답**(fail-closed).

## 요구 증거 5종 (전부 있어야 게이트 open; revalidate가 경로+SHA-256 강제)

| # | record | kind | 핵심 필드 |
|---|---|---|---|
| 1 | `deployment_record` | `witnessd-external-team-pilot-deployment` | deployment id/operator/team scope/start·end ts/witnessd git SHA, `deployed_runtime=true`, `local_dogfood=false`, `ci_only=false` |
| 2 | `rotated_key_archive` | `witnessd-operator-key-rotation-record` | retired/current key id, `rotated_to` 연속성, canary bundle 경로 |
| 3 | `canary_bundle` | signed Depone evidence bundle | predicate `source_kind == "operator-key-rotation-canary"`, 단일 서명, key id == 현재 런타임 key id |
| 4 | `depone_verification` | `depone-verification-transcript` | `verifier="depone"`, `all_passed=true`, production+canary 결과 |
| 5 | `operator_review` | `witnessd-operator-review` | 사람 리뷰, `decision="approve-keyless-gate"`, `local_dogfood=false`, 개인키 미노출 확인 |

---

## Part A — 툴링 태스크 (에이전트 수행 가능, TDD)

지금 canary/archive fixture는 손제작이고 생성 스크립트가 없다. 파일럿 때 사람이 JSON을 손으로 만들면
그 자체가 오류·위조 벡터다. **생성은 자동화하고, 게이트 판정은 계속 revalidate 스크립트가 한다.**

- [ ] **Task A1: `witnessd pilot init`** — deployment_record 스켈레톤 생성.
  RED: `witnessd pilot init --operator <name> --team-scope <desc> --out <dir>` →
  kind/rollout_stage/deployment id/시작 ts/git SHA 채워진 record + `end_ts: null`.
  `local_dogfood`/`ci_only`는 **기본 true/true로 생성**(정직 기본값) — 운영자가 명시 플래그
  `--deployed-runtime --not-dogfood --not-ci`를 다 줘야 false로 바뀜(실수로 인정 요건 충족 불가).
- [ ] **Task A2: `witnessd pilot close`** — end ts 기입 + record의 SHA-256 출력.
- [ ] **Task A3: canary 방출 헬퍼** — 런북 절차 7("Emit one canary evidence bundle")의 자동화.
  RED: `witnessd pilot canary --keys-dir <dir> --out <dir>` → 현재 런타임 key로
  `source_kind="operator-key-rotation-canary"` predicate의 서명 bundle 방출(단일 서명).
  기존 emitter/substrate 경로 재사용 — 새 서명 경로 발명 금지. negative: 서명 2개면 실패,
  source_kind 불일치면 실패(revalidate_key_rotation.py의 기존 검사와 동일 기준).
- [ ] **Task A4: verification transcript 캡처** — Depone 검증 실행의 stdout/exit를
  `depone-verification-transcript` JSON으로 기록하는 래퍼 스크립트(`scripts/pilot_verify.py`).
  transcript의 `all_passed`는 **실제 exit code에서만 파생**(하드코딩 금지).
- [ ] **Task A5: archive 갱신 경로** — `operator-key-archive.json`에 evidence 경로+SHA 기입하는
  헬퍼. **status 필드는 건드리지 않는다** — status 전환은 운영자가 수동 편집하고
  `revalidate_key_rotation.py`가 5종 전부 검증될 때만 open을 통과시킨다(기존 동작 유지 확인 테스트).

## Part B — 운영자 프로토콜 (사람 전용, 순서 고정)

1. W7(팀 어댑터 배선)과 W9 Task 1(CI)이 끝난 뒤에 시도할 것 — 파일럿이 "진짜 팀 작업"이려면
   어댑터 레인이 실제로 돌아야 하고, 배포 SHA가 CI 그린이어야 설득력 있음.
2. 런북 절차 1~6으로 **새 키 생성·회전**(개발 키 재사용 금지).
3. `witnessd pilot init`(A1) → installer로 배포된 런타임에서 실제 팀 작업 1건 실행 →
   `pilot close`(A2) → `pilot canary`(A3) → `pilot_verify`(A4).
4. `operator_review` 작성 — 이 문서만은 자동화하지 않는다(사람 판단이 요지).
5. archive에 5종 기입(A5) → status를 open으로 수동 전환 → `revalidate_key_rotation.py` PASS 확인.
   PASS 못 하면 status를 blocked로 되돌린다(부분 점수 없음).
6. 이후 W6a 플랜(`2026-07-02-w6-keyless-signing.md`)의 stop-condition 재평가.

## Final Validation Matrix (Part A 완료 기준)
```bash
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_key_rotation.py   # 여전히 PASS, gate 여전히 blocked
# pilot init 기본값이 local_dogfood=true인지 테스트로 고정돼 있는지 확인
```

**Explicit Non-Changes:** `production_gate.status` 자동 전환 금지 / 데모·dogfood를 파일럿으로 라벨링 금지 /
개인키 커밋·evidence dir 기록 금지 / revalidate 검증 기준 완화 금지.
