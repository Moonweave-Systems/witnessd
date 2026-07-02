# W10 — Live-Agent E2E: 진짜 에이전트 1개 레인의 증거를 최초 실증 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. TDD, `- [ ]` checkboxes.
> 스펙 근거: `SPEC2.md` §2 (D10-1~D10-4). 신뢰/계약은 `SPEC.md` §3·§4가 우선.

**Goal:** 지금까지 모든 n=1 증거는 셸 레인(`echo`)이거나 테스트의 fake 바이너리였다. 이 웨이브는
**진짜 codex 에이전트**가 실제 코드 작업을 수행한 live run 1회의 증거 바이트를 `fixtures/w10/`에
봉인하고, Depone이 재도출하게 한다. 이것이 제품 헤드라인("진짜 에이전트를 몰면서 완료를 서명
바이트로 증명한다")의 최초 end-to-end 실증이다.

**불변식:** runtime stdlib+openssl only / evidence-pending 출력 / worker self-seal 금지 /
개인키 커밋 금지(공개키만) / 새 스키마 필드 발명 금지.

## Task 0: 베이스라인 + 어댑터 실측
- [ ] 전체 테스트+revalidate 그린 확인(w1~w8, key_rotation).
- [ ] `codex --version` 실측(호스트 설치 확인됨: `~/.local/bin/codex`). 실패 시 claude로 대체하고
  보고(플랜 중단 아님, D10-3 우선순위).
- [ ] `witnessd run --adapter codex`의 현재 인자·수용 기준을 `adapter_run.py`/`adapters/codex.py`
  실코드에서 확인(추측 금지) — 특히 evidence_dir/keys 배선(W7에서 추가된 형태).

## Task 1: live run 실행 스크립트 (재현 가능하게)
- [ ] `scripts/run_w10_live.py` 작성: sandbox 준비(작은 파이썬 모듈 + 실패 테스트 1개 배치) →
  `witnessd run --adapter codex --tier agentic` 으로 프롬프트 투입("이 실패 테스트를 통과시키는
  함수를 구현하라" 수준의 **진짜 코드 작업**, D10-2) → evidence_dir 산출.
  스크립트는 사람이 다시 돌릴 수 있게 인자화(어댑터/프롬프트/출력 경로).
- [ ] 이 스크립트 자체는 runtime 아님(scripts/) — depone import 없어야 하는 건 witnessd/ 만이지만,
  scripts도 실행엔 depone 불필요하게 유지(검증은 별도).

## Task 2: live run 1회 실행 + 증거 검토 (사람 확인 지점)
- [ ] 실제 실행: `uv run python3 scripts/run_w10_live.py --adapter codex --out fixtures/w10/`.
  비용 발생함(실 API) — **1회로 끝나도록 사전에 프롬프트·sandbox를 Task 1 테스트로 검증해둘 것.**
- [ ] 산출 검토: diff가 실제 코드 생성인지(echo류면 폐기·재실행), runner_kind=codex-cli,
  상태 출력 evidence-pending, 개인키가 fixture에 없는지.
- [ ] `fixtures/w10/PROMPT.md`에 사용 프롬프트·어댑터·날짜 기록(제3자 판단 가능성, D10-2).

## Task 3: `scripts/revalidate_w10.py` (G2)
- [ ] W1 패턴 복제하되 대상은 live 바이트: capture-manifest 검증, provenance 바인딩, DSSE 서명,
  ingest pass, runner-receipt(`runner_kind=="codex-cli"` — claude 대체 시 "manual"과 어댑터 필드로
  구분), 위조 A3 승격 서명 실패 negative, **diff 비자명성 체크**(변경 파일 수>0, 생성 라인>N 최소기준).
- [ ] 기존 w1~w8 revalidate 회귀 그린 재확인.

## Task 4: 문서 반영
- [ ] README "Reproduce the core proof"에 live-agent 재도출 블록 추가(fixture 기준 — API 키 없이도
  재검증 가능함을 명시). docs/plans/README.md 로드맵 갱신(W10 완료 표시).
- [ ] 과대주장 금지: "실제 에이전트 1회 실증"이지 "모든 에이전트 검증됨"이 아님 — 문구 주의.

## Final Validation Matrix
```bash
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests
for s in w1 w2 w3 w4 w5 w7 w8 w10 key_rotation; do PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_$s.py; done
# depone-free: witnessd import + 셸 E2E 여전히 동작
```

**Explicit Non-Changes:** 스키마 필드 발명 금지 / Planner 선반영 금지(W11) / 실 API 재실행 최소화
(fixture 봉인 후엔 재도출만) / production_gate·depone repo 불변.
