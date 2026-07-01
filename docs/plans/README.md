# witnessd 구현 계획 — 로드맵 (W1–W5)

> SoT는 `/home/ubuntu/witnessd/SPEC.md`. 이 로드맵은 spec을 웨이브별 실행 계획으로 파생한 것이다.
> 각 웨이브는 **자기완결(working, testable software)** 이며, 앞 웨이브의 모든 Depone validator를 계속 통과해야 한다(§5.0 단조성).

## 실행 규율 (모든 웨이브 공통)

- **언어/제약:** Python 3.10+, **표준 라이브러리만**(Depone와 동일 이식성). 서명은 `openssl` CLI(subprocess), 파이썬 crypto 패키지 금지. `pyproject`/외부 의존성 금지.
- **TDD:** 모든 태스크는 실패 테스트 → 실패 확인 → 최소 구현 → 통과 확인 → 커밋.
- **공통 게이트(웨이브 완료 정의, §5.0):**
  - **G1** `witnessd self-test --all` → `N/N passed` (exit 0).
  - **G2** `python3 scripts/revalidate_wN.py` → committed fixture를 **설치된 Depone 패키지의 validator로** 재도출, exit 0.
  - **G3** Depone repo에서 witnessd evidence 소비 → `python scripts/check_contract.py --tier changed` + `python scripts/dwm.py doctor` red 없음.
- **canonical_hash 규약(Depone와 바이트 동일해야 함):**
  `sha256(json.dumps(obj, sort_keys=True, separators=(",",":")).encode("utf-8")).hexdigest()`
- **불변식:** worker는 자기 성공 seal 불가, Evidence Emitter만 SoT에 씀, verifier는 assurance 상향 불가, fail-closed(부분점수 없음), assurance 상한 **A2**(A3 등급 없음 — operator 서명은 별도 report-level 축).
- **전제:** Depone은 로컬에 설치돼 있어야 함(`python3 -m depone ...` 동작). W1 착수 전 `pip install --no-deps /home/ubuntu/depone-assurance-repair` 로 validator import 가능하게.

## 웨이브 순서 · 의존 · 산출물

| 웨이브 | 계획 파일 | 산출(working software) | 핵심 Depone validator(재도출) | 의존 |
|---|---|---|---|---|
| **W1** | `2026-07-01-w1-evidence-substrate.md` | shell lane 1개를 실행→관측자 분리 캡처→capture-manifest+DSSE 서명→A1/(uid 호스트)A2를 Depone이 바이트에서 재도출 | `enforce_observer_separation`, `validate_capture_manifest`, `verify_capture_chain`, `verify_signed_bundle`, `ingest_signed_evidence_bundle`, `validate_runner_receipt`, `validate_trusted_observer_provenance`, `validate_evidence_contract` | — |
| **W2** | `2026-07-01-w2-supervised-liveness.md` | supervised 자식 프로세스 + 서명된 heartbeat 기반 liveness(죽은 팀 구조적 탐지) + durable session ID 재개 + spawn별 uid isolation → A2 상시화 | `verify_isolation_boundary`, `_check_a2_manifest`(재도출), W1 전부 | W1 |
| **W3** | `2026-07-01-w3-team-fanin.md` | auto worktree + ownership-region lock + worktree lane receipt + team-ledger fan-in(overlap→merge receipt 필수) | `build_team_ledger_verdict`, `validate_worktree_receipt`, W1/W2 전부 | W2 |
| **W4** | `2026-07-01-w4-adapters-routing-cost.md` | Codex→Claude/OpenCode 어댑터(동일 runner-receipt 스키마) + 모델 라우팅 solved abstraction + 비용 서킷브레이커 | `validate_runner_receipt`(`VALID_RUNNERS`), W1–W3 전부 | W3 |
| **W5** | `2026-07-01-w5-autonomy-safety.md` | 자동 학습 캡처(provenance 링크) + hard pause(auto-continuation override) + 테스트된 kill-switch + atomic installer | W1–W4 전부 + 학습 provenance 재도출 | W4 |

## 열린 결정 (실행 중 정지 안 함 — SPEC §8.2)

전부 W1–W5 **구현 경로 밖**이거나 기본값 있음. 단, **key 회전 정책(§8.2-3)은 첫 프로덕션 배포 전 반드시 확정**(구현은 안 막지만 롤아웃 하드 게이트). Codex 상태격리 메커니즘(§8.2-4)은 W4 착수 시 조사+기본값(별도 상태 디렉터리+lock) 적용.

## 착수

W1 계획(`2026-07-01-w1-evidence-substrate.md`)부터. 실행은 superpowers:subagent-driven-development(권장) 또는 executing-plans.
