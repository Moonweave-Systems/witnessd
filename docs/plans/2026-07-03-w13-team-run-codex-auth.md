# W13 — team run에 codex 인증·격리 배선 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. TDD, main 로컬 커밋, push 금지.
> 근거: 2026-07-03 첫 실전 파일럿 시도가 드러낸 런타임 갭.

**문제(실측):** 진짜 codex 에이전트를 실제 외부 repo 작업에 몰면서 유효한 재도출 증거를 내려면
**(1) codex 구독 세션을 격리 CODEX_HOME에 seed + (2) 올바른 ownership region** 둘 다 필요하다.
그런데 v2.0.0엔 그 둘을 동시에 만족하는 경로가 없다:
- `team plan-run`: `_seed_codex_auth`로 인증 ✓ 이지만 heuristic planner가 **placeholder region**
  (`w11/…txt`) 생성 → `allowed_touched_files=registry.claim(region)`이라 codex가 실파일을 고치면
  Depone이 "unexpected touched files"로 거부.
- `team run`: `--lane`으로 region 정확히 지정 ✓ 이지만 **`--codex-auth-source`/`--state-root` 인자가
  없어** codex 레인이 401. lane-spec은 콜론 든 프롬프트도 못 실음(별도 문제, 이 웨이브 범위 밖).

**Goal:** `team run`에 `--codex-auth-source`·`--state-root`(+필요시 프롬프트 파일 주입)를 추가해,
`team run --lane "L:adapter=codex:tier=agentic:region=<실파일>:prompt=<...>"`가 인증+정확한 region으로
진짜 codex 레인을 돌리고 Depone이 재도출하게 한다. **plan-run의 기존 배선을 재사용**(새 서명/실행 경로
발명 금지).

**불변식:** runtime stdlib+openssl only(depone import 0) / worker self-seal 금지 / evidence-pending /
auth.json은 state 디렉토리에만·evidence·fixture 미유출 / production_gate 불변 / depone 불변.

## 설계 결정
- **D1. 재사용:** `_seed_codex_auth`, `_team_plan_state_root`(또는 동등), state-root↔out 겹침 검사
  (`ERR_PLAN_RUN_STATE_ROOT_INSIDE_OUTPUT`와 동일 정신)를 team run에도 적용. 코드 중복 대신 두 커맨드가
  같은 헬퍼를 공유하도록 소폭 리팩터(단, plan-run 동작 회귀 0).
- **D2. 다중 codex 레인 가드:** team run은 `--lane` 다중이다. 여러 codex 레인이 **같은 CODEX_HOME을
  공유하면 세션 충돌** 위험 → 레인별 `state_root/<lane_id>/codex-home`으로 분리하거나, codex 레인이 2개
  이상이면 명시 플래그 없이는 `ERR_TEAM_RUN_MULTI_CODEX_UNISOLATED`로 fail-closed. (파일럿은 단일 레인이라
  당장 무해하지만 조용한 충돌 방지.)
- **D3. lane-spec 콜론 한계는 이 웨이브에서 안 고침**(별도). 대신 `--lane-prompt-file LANE_ID=PATH`
  옵션을 추가해 콜론/복잡 프롬프트를 파일로 주입 가능하게 한다(선택적, 파일럿 프롬프트가 콜론을 포함하므로
  실용상 필요). 미지정 시 기존 인라인 prompt 그대로.

## File Structure
```
witnessd/__main__.py   # MODIFY — team run 파서에 --codex-auth-source/--state-root/--lane-prompt-file,
                       #          _cmd_team_run에서 seed+state-root 배선(plan-run 헬퍼 재사용)
witnessd/fanin.py      # MODIFY(필요시) — run_team이 state_root/레인별 codex-home 전달 지원(이미 있으면 재사용)
tests/test_cli_team.py # MODIFY — 신규 인자 왕복 + fake codex로 인증 seed 확인 + 다중 codex 가드 negative
```

## Tasks
### Task 0: 베이스라인
- [ ] 전체 그린 확인: `PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests`(267 OK) +
  revalidate w1~w12,v2_demo,key_rotation PASS. plan-run 경로가 기준선(회귀 감시 대상).

### Task 1: team run 파서 + seed 배선 (RED→GREEN)
- [ ] RED: `team run --lane "L:adapter=codex:...:region=a.txt:prompt=do x" --codex-auth-source <path>
  --state-root <dir>`가 격리 CODEX_HOME에 auth.json을 seed하는지(fake codex로) 테스트. 인자 없으면 기존 동작.
- [ ] GREEN: plan-run의 seed/state-root 헬퍼 재사용. state-root가 out 안이면 fail-closed.
- [ ] 커밋.

### Task 2: 다중 codex 레인 격리/가드 (RED→GREEN, D2)
- [ ] RED: codex 레인 2개 + state-root 지정 시 레인별 codex-home 분리(또는 미분리면
  `ERR_TEAM_RUN_MULTI_CODEX_UNISOLATED`). 단일 codex 레인은 정상.
- [ ] GREEN + 커밋.

### Task 3: --lane-prompt-file (D3, 선택적이나 파일럿에 필요)
- [ ] RED: `--lane-prompt-file impl=/path/prompt.txt`가 lane_id=impl 레인의 prompt를 파일 내용으로
  덮어씀(콜론/복잡 프롬프트 우회). 미지정 시 인라인 prompt 유지.
- [ ] GREEN + 커밋.

### Task 4: 검증 + fixture
- [ ] `fixtures/w13/`에 team-run-codex(fake) 레인 팀 ledger committed + `revalidate_w13.py`
  (Depone이 codex-cli runner_kind + region-정합 touched_files 재도출; negative: region 밖 touched → blocked).
- [ ] 전체 회귀 그린(w1~w12,v2_demo,w13,key_rotation) + depone-free 스모크 + README/로드맵 갱신.

## Final Validation Matrix
```bash
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests
for s in w1 w2 w3 w4 w5 w7 w8 w10 w11 w12 v2_demo w13 key_rotation; do PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_$s.py; done
env -u PYTHONPATH uv run python3 -c "import witnessd.emitter, witnessd.__main__"   # depone-free
grep -rn "import depone\|from depone" witnessd/ | grep -v __pycache__ | grep -vE "fixture.py:(9|10):" || echo "runtime CLEAN"
```

**Explicit Non-Changes:** plan-run 동작 회귀 0 / heuristic planner region 재작성 안 함(별도) /
새 서명·실행 경로 발명 금지 / depone import·production_gate 불변 / push·태그 금지(운영자).
