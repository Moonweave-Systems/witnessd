# W7 — Team Adapter Wiring: 팀 fan-in 레인이 진짜 에이전트를 몬다 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 또는 superpowers:executing-plans. Steps use `- [ ]` checkboxes.
> 모든 태스크는 TDD: 실패 테스트 → 실패 확인 → 최소 구현 → 통과 → 커밋.

**Goal:** 현재 `witnessd team-run`의 레인은 **플레이스홀더 셸 명령**(`printf lane_id > 파일`)만 실행한다
(`witnessd/__main__.py`의 `_default_team_lane_command`). 즉 W3 팀 기계(worktree·ownership lock·ledger)와
W4 어댑터(codex/claude/opencode)가 **서로 배선돼 있지 않다**. 이 웨이브는 그 배선을 만든다:
팀 레인 스펙에 `adapter` + `prompt`를 추가해, 각 레인이 자기 worktree sandbox 안에서
**진짜 어댑터 lane**(`run_adapter_lane` 경로)을 몰고, 레인별 runner-receipt·budget·routing 이벤트가
team-ledger에 합류하며, Depone이 그 전체를 바이트에서 재도출한다.

**완료 정의(자기보고 아님):** (a) committed fixture(`fixtures/w7/*`)에서 Depone `build_team_ledger_verdict` +
`validate_runner_receipt`가 어댑터-레인 팀을 재도출, (b) 플레이스홀더 레인과 어댑터 레인이 한 팀에 섞여도
ledger가 정확히 구분, (c) 어댑터 실패(blocked/budget) 레인이 ledger에서 fail-closed.

**Architecture:** 기존 심볼 재사용, 재정의 금지:
- W3: `run_team`/`_run_write_lane`(`witnessd/fanin.py`), `create_lane_worktree`(`worktree.py`),
  `OwnershipRegistry`(`lock.py`), `build_team_ledger`(`team_ledger.py`)
- W4: `run_adapter_lane`(`adapter_run.py`), `route_model`(`router.py`), 어댑터들(`adapters/{codex,claude,opencode,shell}.py`),
  `RUNNER_KIND_BY_ADAPTER`(`adapters/base.py`), budget(`budget.py`)
- W1: `emit_lane_evidence`(`emitter.py`), `EventLog`(`eventlog.py`), `render_status`(`status.py`)

핵심 설계 결정(이 플랜의 고정점):
1. **레인 스펙 확장은 additive.** 기존 `lane_id:path1,path2` 문법은 그대로 두고(후방호환),
   새 문법 `lane_id:adapter=codex:tier=agentic:region=path1,path2:prompt=...`를 추가한다.
   adapter 미지정 → 기존 플레이스홀더 셸 경로(회귀 없음).
2. **어댑터 레인의 sandbox = 그 레인의 worktree.** `run_adapter_lane(root=..., sandbox=<worktree>)`로
   호출해 W3의 격리·소유권과 W4의 어댑터 실행이 같은 경계를 쓴다. observer 출력은 worktree **밖**
   (`assert_separated` 기존 규칙 그대로).
3. **역할(planner/coder 등)은 이 웨이브에서 도입하지 않는다.** 팀 정의는 계속 "레인+소유영역"이다.
   역할은 prompt 내용의 문제이지 런타임 스키마의 문제가 아니다(스코프 크리프 금지).
4. **worker self-seal 불변식 유지:** 어댑터 레인 결과도 emitter만 SoT에 쓰고, 상태 출력은
   `render_status` 경유(evidence-pending), VERIFIED 단독 출력 금지.
5. **runtime은 stdlib+openssl only** — depone import 금지(2026-07-02 `ad5b9d5` decoupling 이후 확립).
   parity가 필요한 새 아티팩트는 `tests/test_depone_replica_conformance.py` 패턴으로 등가 테스트 추가.

**Tech Stack:** Python stdlib + openssl CLI. 외부 의존/`pyproject` 금지.

---

## File Structure

```
witnessd/
  fanin.py           # MODIFY — lane spec에 adapter 분기: _run_adapter_lane(worktree sandbox) 추가
  __main__.py        # MODIFY — _parse_team_lane 확장(adapter=/tier=/prompt= 키), team-run 헬프 갱신
  team_ledger.py     # MODIFY — lane entry에 runner_kind/model/budget 요약 필드(additive, Depone 계약 확인 후)
tests/
  test_team_adapter_wiring.py   # NEW — 파싱/분기/sandbox=worktree/fail-closed
  test_cli_team.py              # MODIFY — 새 문법 CLI 왕복
fixtures/w7/
  team-ledger.json              # 어댑터 레인 1 + 셸 레인 1 혼합 팀의 ledger
  lanes/<lane_id>/...           # 레인별 evidence (capture-manifest, runner-receipt, bundle, provenance)
  keys/operator.pub             # 공개키만
  negative/ledger-budget-blocked.json   # budget 초과 레인 → 레인 fail-closed
scripts/
  revalidate_w7.py   # NEW — Depone이 committed 바이트에서 어댑터-팀을 재도출(G2)
```

---

## Task 0: 베이스라인 게이트

- [ ] **Step 1:** 이전 웨이브 전부 그린 확인 (레드면 착수 금지)
```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests   # 207+ OK
for s in w1 w2 w3 w4 w5 key_rotation; do PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_$s.py; done
```
- [ ] **Step 2:** decoupling 가드 그린 확인(`tests/test_runtime_depone_decoupling.py`) — 이 웨이브 내내 유지해야 함.

## Task 1: 레인 스펙 파서 확장 (RED→GREEN)

- [ ] RED: `test_team_adapter_wiring.py` — `_parse_team_lane("L1:adapter=codex:tier=agentic:region=a.txt,b.txt:prompt=do X")`가
  `{"lane_id":"L1","adapter":"codex","tier":"agentic","region":["a.txt","b.txt"],"prompt":"do X"}` 반환;
  구형 `"L1:a.txt,b.txt"`는 기존과 동일 dict(adapter 키 없음); `adapter=frobnicate`는 `ValueError("ERR_TEAM_LANE_ADAPTER")`;
  adapter 있는데 prompt 없음 → `ValueError("ERR_TEAM_LANE_PROMPT")`.
- [ ] GREEN: `_parse_team_lane` 최소 구현. 유효 adapter 집합은 `RUNNER_KIND_BY_ADAPTER` + `"shell"`에서 파생(하드코딩 금지).
- [ ] 커밋.

## Task 2: fanin 어댑터 분기 — sandbox=worktree

- [ ] RED: spec에 `adapter`가 있으면 `run_team`이 해당 레인을 `_run_adapter_lane`으로 보내고,
  `run_adapter_lane`이 **그 레인의 worktree를 sandbox로** 받는지 assert(monkeypatch로 캡처).
  observer/evidence 경로는 worktree 밖(`assert_separated` 통과)인지도 assert.
- [ ] GREEN: `fanin._run_adapter_lane` 구현 — `create_lane_worktree` → `run_adapter_lane(root=repo_root, sandbox=worktree, adapter=..., prompt=..., tier=..., budget=...)` → 레인 커밋 → worktree receipt → ledger entry.
  실제 codex/claude 바이너리는 테스트에서 fake binary(기존 `adapters/codex.py`의 fake 패턴 재사용)로 대체.
- [ ] RED→GREEN: 어댑터 레인이 `LaneBlocked`(budget/routing) 던지면 레인 verdict가 fail-closed로 ledger에 기록되고
  다른 레인은 계속 진행.
- [ ] 커밋.

## Task 3: ledger 계약 — Depone 실코드 확인 후 additive 필드

- [ ] **먼저 Depone 계약 확인(추측 금지):** `/home/ubuntu/moonweave/depone/depone/agent_fabric/team_ledger.py`를 읽고
  `build_team_ledger_verdict`가 거부하지 않는 additive 필드 위치를 확정한다. 만약 lane entry에
  `runner_kind`/`model` 같은 필드를 넣었을 때 Depone이 거부하면 **필드를 넣지 말고** 레인 evidence dir의
  runner-receipt로만 남긴다(계약 변경은 Depone PR 먼저 — moonweave/CLAUDE.md 규칙 3).
- [ ] RED: 혼합 팀(셸 1 + codex 1) ledger를 Depone `build_team_ledger_verdict`가 pass로 재도출.
- [ ] GREEN + 커밋.

## Task 4: CLI 왕복 + fixture 커밋

- [ ] `witnessd team-run --repo <r> --out <o> --lane "L1:a.txt" --lane "L2:adapter=codex:tier=quick:region=b.txt:prompt=write b"`
  E2E(fake codex binary) → evidence-pending 출력, ledger 생성.
- [ ] `fixtures/w7/` 생성 스크립트로 fixture 커밋(개인키 커밋 금지, negative 포함).
- [ ] 커밋.

## Task 5: `scripts/revalidate_w7.py` (G2)

- [ ] W1~W5 revalidate 패턴 복제: committed 바이트만 로드 → Depone validator로
  ledger verdict + 각 레인 runner-receipt(`validate_runner_receipt`, codex 레인은 `runner_kind=="codex-cli"`) +
  capture-manifest + bundle 서명 재도출. negative: budget-blocked ledger가 pass로 재도출되면 AssertionError.
- [ ] `docs/plans/README.md` 로드맵 표에 W7 행 추가.
- [ ] 커밋.

## Final Validation Matrix (전부 그린이어야 완료)

```bash
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests
for s in w1 w2 w3 w4 w5 w7 key_rotation; do PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 scripts/revalidate_$s.py; done
uv run python3 -m witnessd self-test --all
# decoupling 유지: depone 미설치 환경에서 witnessd import + team-run(셸 레인) 동작
```

**Explicit Non-Changes:** 역할 시스템 도입 금지 / Depone repo 수정 금지 / 새 assurance 등급 금지 /
플레이스홀더 셸 레인 경로 제거 금지(후방호환) / VALID_RUNNERS 확장 금지(필요하면 Depone PR 먼저).
