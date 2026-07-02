# witnessd SPEC Part II — 실행 절반 완성 (v1.0 → v2.0.0 최종판)

> SoT 관계: `SPEC.md`(Part I)는 그대로 유효하다 — 증거 계약(§4), 신뢰 모델(§3), 컴포넌트 정의(§2.4)를
> 재정의하지 않는다. 이 문서는 Part I이 정의했으나 v1.0에서 **구현되지 않은 실행 절반**을 최종판까지
> 끌고 가는 스펙이다. 충돌 시 Part I §3(신뢰)·§4(계약)가 우선한다.

## 0. 왜 Part II인가 — 전략 근거 (2026-07-02 확정)

v1.0 결산: 증거·검증 축은 완성(W1–W9, CI 그린, OVERT AAL-3 문서화)됐으나 **실행 축이 얇다**:
진짜 LLM 에이전트를 한 번도 몰지 않았고(셸/fake 바이너리만), Planner(§2.4.1) 미구현(레인 손지정),
A2는 W12에서 로컬 real-host proof가 추가됐지만 CI 재현은 선택 트랙이다. 시장 조사(2026-07-02): 검증 레이어는 붐비기 시작(OVERT/EQTY/MS/Agent
Receipts), **"증거 네이티브 실행 런타임" 자리는 여전히 빈 자리**. 실행 절반을 채우지 않으면
프로젝트는 빈 자리를 비워둔 채 붐비는 자리로 표류한다. Part II = 그 실행 절반.

**v2.0.0 최종판 정의 (이것이 결승선, 이후는 P1/W6a 운영 트랙만 남는다):**
> 한 커맨드로 — 목표(goal)를 주면 Planner가 레인을 짜고, 진짜 에이전트(codex/claude/opencode)들이
> worktree에서 병렬로 일하고, 모든 행동이 서명 증거로 봉인되고, Depone이 그 바이트에서 전 과정을
> 오프라인 재도출한다. 이 데모 한 번의 증거 트리가 committed fixture로 남아 누구든 재검증할 수 있다.

## 1. 범위 / 비범위

**In-scope (Part II):**
- **W10 Live-Agent E2E** — 진짜 codex/claude 에이전트 1개 레인이 실제 코드 작업을 수행하고,
  그 live 증거 번들이 committed fixture가 되어 Depone이 재도출 (헤드라인 주장 최초 실증).
- **W11 Planner/Orchestrator** — SPEC §2.4.1 구현: `plan(goal) -> list[LanePacket]` + dispatch.
- **W12 Real A2** — 이 호스트에 uid 격리 셋업(sudo 가능 실측 확인됨), W1 A2 strict fixture 전환,
  진짜 `A2-isolated-observed` 방출·재도출.
- **v2.0.0 릴리스** — 위 셋을 묶은 one-command 데모 + README/conformance 갱신 + 태그.

**Out-of-scope (영구 확정 — 재논의 금지, 문서에 이미 울타리):**
- 에이전트 페르소나 역할 시스템(planner/coder/reviewer 카탈로그) — **witnessd의 팀 정의는
  "레인 + ownership-region"이다.** 역할은 prompt 내용이지 런타임 스키마가 아니다. (2026-07-02 확정)
- 투명성 로그(RFC6962)·독립 IAP notary·MEASURE 통계층 — OVERT AAL-4 트랙, 솔로 범위 밖.
- tmux/pane, cloud provisioning, PyPI — Part I Non-Goals 계승.

## 2. W10 — Live-Agent E2E (설계 결정)

- **D10-1. live 증거도 committed fixture다.** 증거는 설계상 오프라인 재검증 가능하므로, 진짜
  에이전트 run 1회의 evidence_dir 바이트(capture-manifest/receipt/bundle/provenance/runlog)를
  `fixtures/w10/`에 커밋하고 `revalidate_w10.py`가 재도출한다. live라서 fixture가 못 되는 게 아니라,
  **live인데도 fixture가 되는 것이 제품 논제다.**
- **D10-2. 작업은 실제 코드 작업이어야 한다.** `echo` 류 금지. 최소 기준: 에이전트가 sandbox 안
  파일을 읽고 **새 코드/텍스트를 생성**하며 diff가 남는 작업(예: 함수 하나 구현+테스트). 프롬프트와
  결과 diff가 fixture에 함께 남아 "진짜 일"임을 제3자가 판단 가능해야 한다.
- **D10-3. 어댑터 우선순위 codex → claude → opencode.** 셋 다 이 호스트에 설치 실측(2026-07-02).
  codex가 일급 receipt(`runner_kind=codex-cli`)이므로 헤드라인은 codex로.
- **D10-4. 비결정성 처리.** LLM 출력은 비결정적이므로 revalidate는 "동일 출력 재생성"이 아니라
  **"봉인된 바이트의 무결성·서명·계약 재도출"**을 검증한다(기존 W1 패턴 그대로). evidence_mode는
  contemporaneous(실제 실행 시점 봉인).

## 3. W11 — Planner (설계 결정)

- **D11-1. 2단 구조: draft는 자유, 봉인은 순수.** LLM으로 goal을 분해해도 된다(그 draft run 자체가
  W10 방식의 증거 레인). 단 **채택된 plan은 canonical_hash로 봉인된 plan artifact**가 되고,
  `dispatch(sealed_plan)`는 순수 함수다. §2.4.1의 결정성 테스트는 (a) 봉인 plan → dispatch 이벤트
  열이 결정적, (b) 휴리스틱(비-LLM) planner 경로는 동일 goal·seed → 동일 hash, 두 층에서 만족한다.
- **D11-2. LanePacket 스키마 = 기존 레인 스펙의 상위 집합.** `{lane_id, adapter, tier, region[],
  prompt, budget, stop_rule}` — W7 레인 문법과 1:1 대응(새 실행 경로 발명 금지, `run_team`에 그대로
  투입 가능해야 함).
- **D11-3. region disjoint 강제.** overlap이 필요한 plan은 planner가 merge-lane을 명시적으로
  생성해야 하며(W3 merge receipt 경로), 암묵 overlap은 plan 봉인 단계에서 fail-closed.
- **D11-4. Planner는 spawn·git·서명·verdict를 하지 않는다**(§2.4.1 "안 하는 것" 그대로).

## 4. W12 — Real A2 (설계 결정)

- **D12-1. uid 모델 1급(Part I Decision 계승).** sudo로 전용 observer 계정(예: `witnessd-observer`)
  생성, observer_dir 0700, runner가 쓸 수 없음. `isolation.py` 기존 fail-closed 로직 재사용.
- **D12-2. W1 A2 strict 전환은 증거로만.** 진짜 격리 run이 `A2-isolated-observed`를 방출하고
  Depone strict assert가 통과할 때만 W1 A2 fixture를 W12 manifest와 byte-for-byte 동일하게 전환한다
  (문서 먼저 제거 금지).
- **D12-3. CI에서의 A2는 선택.** GitHub 러너도 sudo가 되므로 CI job으로 실격리 재현을 시도하되,
  실패 시 로컬 fixture 재도출로 대체(정직 표기).

## 5. 수용 기준 (v2.0.0 태그 조건)

1. `witnessd team plan-run "<goal>"` 한 커맨드가: plan 봉인 → 레인 dispatch → 진짜 에이전트 실행 →
   증거 트리 → 종료까지 도달하고, 출력은 evidence-pending(자기 verdict 금지).
2. 그 run의 증거 트리에서 Depone이 오프라인으로: plan artifact hash, 각 레인 A1(격리 레인은 A2),
   team-ledger verdict, 서명 전부 재도출.
3. committed fixture: `fixtures/w10/`(live agent), `fixtures/w11/`(sealed plan+dispatch),
   `fixtures/w12/`(real A2) + revalidate_w10/11/12 전부 PASS + 기존 w1~w8 회귀 그린 + CI 그린.
4. README/conformance 갱신: "실제 에이전트 E2E 실증" 추가, A2는 committed W12 evidence bytes와
   dedicated observer uid 호스트 요건으로 표기(W12 후),
   과대주장 금지 유지.

## 6. 웨이브 순서와 근거

**W10 → W11 → W12 → v2.0.0.** W10이 먼저다: 헤드라인 주장("진짜 에이전트를 몰면서 증명")의 최초
실증이고 가장 작다. W11은 W10의 live 레인을 재료로 쓴다(planner draft가 곧 에이전트 레인). W12는
환경 작업이 섞여 병렬 가능하나 릴리스 전 완료. 이후 남는 것은 운영 트랙(P1 파일럿 → W6a keyless)뿐.
