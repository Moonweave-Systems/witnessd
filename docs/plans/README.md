# witnessd 구현 계획 — 로드맵 (W1–W5)

> SoT는 `/home/ubuntu/witnessd/SPEC.md`. 이 로드맵은 spec을 웨이브별 실행 계획으로 파생한 것이다.
> 각 웨이브는 **자기완결(working, testable software)** 이며, 앞 웨이브의 모든 Depone validator를 계속 통과해야 한다(§5.0 단조성).

## 실행 규율 (모든 웨이브 공통)

- **언어/제약:** Python 3.10+, **표준 라이브러리만**(Depone와 동일 이식성). 서명은 `openssl` CLI(subprocess), 파이썬 crypto 패키지 금지. `pyproject`/외부 의존성 금지.
- **TDD:** 모든 태스크는 실패 테스트 → 실패 확인 → 최소 구현 → 통과 확인 → 커밋.
- **공통 게이트(웨이브 완료 정의, §5.0):**
  - **G1** `witnessd self-test --all` → `N/N passed` (exit 0).
  - **G2** `python3 scripts/revalidate_wN.py` → committed fixture를 **설치된 Depone 패키지의 validator로** 재도출, exit 0.
  - **G3** Depone repo에서 witnessd evidence 소비 → `python3 -m depone validate-contracts --self-test` + `python3 -m depone doctor --self-test` red 없음. (2026-07-03 DWM 은퇴로 `check_contract.py`/`dwm.py doctor` 게이트가 패키지 self-test로 대체됨.)
- **canonical_hash 규약(Depone와 바이트 동일해야 함):**
  `sha256(json.dumps(obj, sort_keys=True, separators=(",",":")).encode("utf-8")).hexdigest()`
- **불변식:** worker는 자기 성공 seal 불가, Evidence Emitter만 SoT에 씀, verifier는 assurance 상향 불가, fail-closed(부분점수 없음), assurance 상한 **A2**(A3 등급 없음 — operator 서명은 별도 report-level 축).
- **전제:** Depone은 로컬에 설치돼 있어야 함(`python3 -m depone ...` 동작). 현재 canonical 로컬 repo는 `/home/ubuntu/moonweave/depone`이며, witnessd 검증은 `PYTHONPATH=/home/ubuntu/moonweave/depone`로 validator를 import한다.

## 웨이브 순서 · 의존 · 산출물

| 웨이브 | 계획 파일 | 산출(working software) | 핵심 Depone validator(재도출) | 의존 |
|---|---|---|---|---|
| **W1** | `2026-07-01-w1-evidence-substrate.md` | shell lane 1개를 실행→관측자 분리 캡처→capture-manifest+DSSE 서명→A1/(uid 호스트)A2를 Depone이 바이트에서 재도출 | `enforce_observer_separation`, `validate_capture_manifest`, `verify_capture_chain`, `verify_signed_bundle`, `ingest_signed_evidence_bundle`, `validate_runner_receipt`, `validate_trusted_observer_provenance`, `validate_evidence_contract` | — |
| **W2** | `2026-07-01-w2-supervised-liveness.md` | supervised 자식 프로세스 + 서명된 heartbeat 기반 liveness(죽은 팀 구조적 탐지) + durable session ID 재개 + uid isolation 계약/fixture 재도출(실제 A2는 uid-isolated host 필요) | `verify_isolation_boundary`, `_check_a2_manifest`(재도출), W1 전부 | W1 |
| **W3** | `2026-07-01-w3-team-fanin.md` | auto worktree + ownership-region lock + worktree lane receipt + team-ledger fan-in(overlap→merge receipt 필수) | `build_team_ledger_verdict`, `validate_worktree_receipt`, W1/W2 전부 | W2 |
| **W4** | `2026-07-01-w4-adapters-routing-cost.md` | Codex→Claude/OpenCode 어댑터(동일 runner-receipt 스키마) + 모델 라우팅 solved abstraction + 비용 서킷브레이커 | `validate_runner_receipt`(`VALID_RUNNERS`), W1–W3 전부 | W3 |
| **W5** | `2026-07-01-w5-autonomy-safety.md` | 자동 학습 캡처(provenance 링크) + hard pause(auto-continuation override) + 테스트된 kill-switch + atomic installer | W1–W4 전부 + 학습 provenance 재도출 | W4 |

## 열린 결정 (실행 중 정지 안 함 — SPEC §8.2)

전부 W1–W5 **구현 경로 밖**이거나 기본값 있음. **key 회전 정책(§8.2-3)은 `docs/ops/operator-key-rotation.md` + `scripts/revalidate_key_rotation.py`로 로컬 canary/archive 재검증을 갖췄지만, `external-team-pilot` required evidence 5종이 아직 `missing`이므로 keyless gate는 blocked 상태다.** Codex 상태격리 메커니즘(§8.2-4)은 W4에서 별도 상태 디렉터리+lock으로 확정 적용했다.

## 현재 상태

W1-W5 구현 및 committed fixture revalidation 완료. 2026-07-02에 런타임 depone 의존 제거
(`ad5b9d5`, 이제 runtime은 진짜 stdlib+openssl only — depone 없는 환경에서 실행·방출 가능,
parity 가드 `tests/test_depone_replica_conformance.py`). W7 team adapter wiring은 `fixtures/w7/` +
`scripts/revalidate_w7.py`로 어댑터 레인 팀 fan-in 재도출을 고정했다. production keyless gate는 blocked 유지.

## 로드맵 — 최종판(v2.0.0)까지의 전체 아크 (2026-07-02 확정)

> **현재 위치(2026-07-03): v2.0.0 릴리스 준비 완료. 다음 = 운영 트랙 P1 → W6a.**
> Part II 스펙(왜/무엇이 최종판인가)은 **`SPEC2.md`** — 조사 다시 하지 말고 그 문서부터 읽을 것.

### 완료 (v1.0.x)
| 웨이브 | 산출 | 상태 |
|---|---|---|
| W1–W5 | 증거 substrate → 자율성 안전 (위 표) | ✅ revalidate PASS |
| W7 | 팀 fan-in에 진짜 어댑터 배선 (`2026-07-02-team-adapter-wiring.md`) | ✅ |
| W8 | OVERT 1.1 스키마 정렬 + AAL-3 conformance 문서 (`2026-07-02-overt-alignment.md`) | ✅ |
| W9 | CI + 릴리스 서사 + 태그 (`2026-07-02-hardening-and-release.md`) | ✅ v1.0.0/v1.0.1 |
| W10 | 진짜 Codex CLI 1레인 live run → `fixtures/w10/` committed evidence → Depone 재도출 (`2026-07-02-w10-live-agent-e2e.md`) | ✅ one real-agent attestation |
| W11 | Planner: goal → sealed plan → deterministic dispatch → `team plan-run` shell fallback (`2026-07-02-w11-planner.md`) | ✅ revalidate PASS |
| W12 | Dedicated observer uid real A2 fixture (`2026-07-02-w12-real-a2.md`) | ✅ strict revalidate PASS |
| v2.0.0 | one-command `team plan-run` demo with a real Codex lane → `fixtures/v2-demo/` → Depone revalidation | ✅ local tag, push left to operator |

### 운영 트랙 (v2.0.0 이후)
| 순서 | 플랜 파일 | 산출 | 의존 | 성격 |
|---|---|---|---|---|
| **P1** | `2026-07-02-external-team-pilot.md` | production gate 5종 증거 — **파일럿 실행·게이트 전환은 운영자 전용** | v2.0.0 권장 | 운영 트랙 |
| **W6a** | `2026-07-02-w6-keyless-signing.md` | keyless(Sigstore) readiness — gate open 전까지 blocked | P1 | 코드(별도 트랙) |

공통 규율(전 웨이브): TDD / additive-only 스키마(기존 fixture 재도출 유지) / runtime depone import 금지 /
worker self-seal 금지 / `production_gate.status` 자동 전환 절대 금지 / 과대주장 금지 /
Depone 계약 변경은 Depone PR 먼저.
**영구 범위 밖(재논의 금지, SPEC2 §1):** 에이전트 페르소나 역할 시스템(팀 정의=레인+ownership-region),
투명성 로그(RFC6962), 독립 IAP notary, MEASURE 통계층 — conformance 문서 Roadmap/Exclusions로만 기재.
