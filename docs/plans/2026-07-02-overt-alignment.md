# W8 — OVERT 1.1 정렬: 스키마 레벨 + 정직한 conformance 선언 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 또는 superpowers:executing-plans.
> 이 문서는 자기완결적이다 — OVERT에 대한 필요한 사실은 아래 §Context에 있고, 원문은 overt.is에서 받는다.

**Goal:** witnessd/Depone을 **OVERT 1.1**(Glacis Technologies의 "Observable Verification Evidence for
Runtime Trust" 오픈 표준, 2026-06-11 발행, https://overt.is/)에 **스키마·문서 레벨에서** 정렬한다.
목표 산출물은 (1) 증거 스키마에 3개 additive 필드(POST_HOC flag, epoch/counter, parent attestation 참조),
(2) 정직한 conformance 선언문(`docs/conformance/OVERT.md`) — 달성 등급 **AAL-3, Agentic scope**,
미달성 항목은 Exclusions로 명시. **AAL-4로 가는 인프라(투명성 로그, 독립 notary)는 이 웨이브 범위 밖**이며
로드맵으로만 기재한다.

## Context — OVERT 요약 (구현에 필요한 사실만; 인용 전 원문 대조 필수)

- **AAL 사다리(Attestation Assurance Levels):** AAL-1 정책문서(자기주장) < AAL-2 프로세스 기록(자기증명) <
  AAL-3 자동 모니터링(운영자 통제 notary) < **AAL-4 암호 증명(운영자와 구조적으로 독립한 IAP notary +
  RFC6962 투명성 로그 + 독립 타임스탬프 + 공개검증 inclusion/consistency proof)**.
  런타임 AI 행동 통제(enforcement/kill/pause 등)는 AAL-4 지정. "Self-attestation is not compliant"(AAL-4에서).
- **역할 매핑:** OVERT *arbiter*(envelope 방출자) = **witnessd**. OVERT *notary/verifier*(receipt 발행) = **Depone**.
  provisional(로컬 서명, 동기) → final(notary, 비동기) 2단계 = witnessd emitter → Depone 비동기 검증과 동형.
- **Protocol Profile 분리:** 표준은 "무엇을 증명"(정규 코어)과 "어떻게"(등록된 Protocol Profile)를 분리.
  PP1.0(Glacis 레퍼런스)은 BLS threshold·CBOR·sampling PRF를 쓰지만 **다른 프로파일 허용** —
  우리는 Ed25519/DSSE 그대로 자체 프로파일 문서로 간다. JCS(RFC8785) 전환 안 함(기존 canonical_hash 유지,
  프로파일 문서에 명기).
- **Receipt의 flags 필드:** `0x00` = contemporaneous(실시간 관측), `0x01` = **POST_HOC**(사후 재구성).
  감사자는 이 필드로 실시간 증거와 소급 재구성을 구분한다.
- **Cross-boundary 링크:** 하류 receipt가 상류 receipt의 attestation_id의 SHA-256을
  `parent_attestation_id`로 참조(64-hex). 경계를 넘는 건 해시뿐(content-free 유지).
- **MEASURE 도메인:** OVERT는 sampling+Clopper-Pearson 통계 안전증명 지향. witnessd/Depone은
  **결정론적 per-action 검증** — 철학적 분기. 통계 주장을 하지 않으므로 **Exclusions로 선언**(합법적).
- **witnessd 기존 자산과의 대응(이미 정렬):** PRO-1 boundary arbiter+permit/deny receipt ≈ 어댑터+Depone verdict /
  ATT-1 non-egress ≈ content-free / ATT-3 three-phase ≈ emitter→Depone / RES-1 crypto-gated control loop ≈
  W5 게이트 / RES-5 failure-mode 선언 ≈ root fail-closed isolation.

**불변식(변경 금지):** assurance 상한 A2(A3 등급 없음 — operator DSSE는 report-level 축) /
worker self-seal 불가 / runtime stdlib+openssl only(depone import 금지) / Depone 계약 변경은 Depone PR 먼저
(moonweave/CLAUDE.md 규칙 3) / 새 필드는 전부 **additive**여야 하며 기존 W1~W5 fixture가 계속 재도출돼야 함.

---

## File Structure

```
witnessd/
  capture.py / substrate.py / emitter.py   # MODIFY — 3개 additive 필드 방출
docs/conformance/
  OVERT.md                    # NEW — conformance 선언문 (아래 Task 4 구조)
  witnessd-protocol-profile.md # NEW — 자체 프로파일 문서(Ed25519/DSSE/canonical_hash 명세)
tests/
  test_overt_fields.py        # NEW
fixtures/w8/                  # 새 필드 포함 fixture + POST_HOC negative
scripts/
  revalidate_w8.py            # NEW
```

## Task 0: 베이스라인 게이트
- [ ] 전체 테스트 + revalidate w1..w5,w7(존재 시),key_rotation 그린. OVERT 원문
  (`OVERT_1.1_Foundations.pdf`, `OVERT_1.1_Annexes.pdf` — Annex B/G) 다운로드해
  §Context의 사실을 인용 전 재확인(발행물이 갱신됐을 수 있음).

## Task 1: `evidence_mode` 필드 (OVERT flags 대응)
- [ ] **먼저 Depone 확인:** `validate_capture_manifest`/`ingest_signed_evidence_bundle`이 unknown 필드를
  거부하는지 확인. 거부하면 이 필드는 **Depone PR 먼저**(계약 프로토콜) — 이 플랜을 멈추고 Depone 쪽
  additive-field 허용 여부를 보고할 것.
- [ ] RED: capture-manifest/bundle에 `evidence_mode: "contemporaneous" | "post_hoc"` 방출.
  기본값 contemporaneous. 재구성 경로(예: kill runlog 재구성, 데모 fixture 생성)는 post_hoc.
  negative: post_hoc 증거가 contemporaneous로 각인되면 테스트 실패.
- [ ] GREEN + parity 테스트(변경이 Depone 재도출을 깨지 않음) + 커밋.

## Task 2: `epoch` + `monotonic_counter` (OVERT co-epoch 대응, 최소형)
- [ ] RED: emitter가 방출하는 bundle에 `epoch_seconds`(구성 가능, 기본 300)와 run 내 단조 카운터 추가.
  독립 타임스탬프 권위는 없음 — 필드 문서에 "operator clock, AAL-3 grade"를 명기(과대주장 금지).
- [ ] GREEN + 기존 fixture 재도출 그린 확인 + 커밋.

## Task 3: `parent_attestation_id` (cross-boundary 참조, 스키마만)
- [ ] RED: 레인 evidence가 상류 증거(예: 팀 ledger→레인, 레인→하위 툴콜)의 canonical_hash SHA-256을
  `parent_attestation_id`(64-hex)로 참조 가능. 없으면 필드 생략(OPTIONAL). HTTP 헤더 바인딩은 범위 밖.
- [ ] GREEN + 커밋.

## Task 4: conformance 선언문 + 자체 프로파일 문서
- [ ] `docs/conformance/OVERT.md` 작성 — 필수 구조:
  1. **Claim:** "witnessd+Depone, OVERT 1.1, **AAL-3**, scope **Agentic**" — 컨트롤별 매핑 표
     (PRO-1/ATT-1/ATT-3/RES-1/RES-5 ↔ 실제 모듈·테스트·fixture 경로).
  2. **Exclusions(정직):** ATT-4 투명성 로그(없음), ATT-5 독립 IAP notary(없음 — operator self-run),
     MEASURE 도메인 전체(결정론적 per-action 모델, 통계 주장 안 함), Agentic-Extended CAS/PoP(없음),
     RES-3 break-glass(없음). 각각 한 줄 아키텍처 정당화.
  3. **Roadmap(비약속):** AAL-4 = 투명성 로그 + IAP — 솔로 1.0 범위 밖.
  4. **금지 문구 검사:** 문서 어디에도 "AAL-4", "unforgeable", "OVERT certified"를 달성 주장으로 쓰지 않는다
     (게이트/키 회전 리뷰에서 확립된 no-overclaim 원칙).
- [ ] `docs/conformance/witnessd-protocol-profile.md` — canonical_hash 정의(바이트 정확),
  DSSE/Ed25519(openssl CLI), 키 관리(docs/ops/operator-key-rotation.md 참조), envelope↔OVERT 필드 대응표.
- [ ] README.md에 conformance 문서 링크 1줄 + 커밋.

## Task 5: fixture + `scripts/revalidate_w8.py`
- [ ] 새 필드 포함 fixture 커밋, Depone 재도출 + negative(post_hoc 위장) 검증. README 로드맵 갱신. 커밋.

## Final Validation Matrix
```bash
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests
for s in w1 w2 w3 w4 w5 w8 key_rotation; do PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_$s.py; done
grep -rn "AAL-4\|unforgeable\|OVERT certified" docs/conformance/ README.md   # 달성 주장으로 등장하면 실패
```

**Explicit Non-Changes:** 투명성 로그 구현 금지 / notary 서비스 구현 금지 / JCS 전환 금지 /
sampling·통계층 구현 금지 / HTTP 헤더 바인딩 구현 금지 / Depone repo 직접 수정 금지(필요 시 멈추고 보고).
