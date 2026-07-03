# W9 — CI + v1.0 완주선: 하드닝 스윕, CI, 릴리스 서사 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans.
> 이 웨이브의 절반은 코드가 아니라 **검증과 글**이다. 과대주장 금지 원칙이 최우선 게이트다.

**Goal:** witnessd를 "끝없는 웨이브"가 아니라 **못 박힌 1.0**으로 만든다. (1) 잔여 결함 스윕 —
문서에 남은 이전 리뷰 지적이 실제로 닫혔는지 **재현으로** 확인하고 낡은 주장 제거, (2) GitHub Actions CI
(테스트 + revalidate + decoupling 가드), (3) README를 대표작 서사로 재작성, (4) `v1.0.0` 태그.
완료 정의: CI 그린 배지 + 태그 + "무엇을 주장하고 무엇을 주장하지 않는가"가 문서로 정확한 상태.

**전제 의존:** W7(team-adapter-wiring), W8(overt-alignment)이 먼저 머지돼 있어야 한다.
(순서: W7 → W8 → W9. W6a keyless readiness는 별도 트랙 — `2026-07-02-w6-keyless-signing.md` 참조,
production gate가 blocked인 동안 W9와 독립.)

**불변식:** runtime stdlib+openssl only / evidence-pending 상태 규율(VERIFIED 단독 출력 금지) /
assurance 상한 A2 / 문서 어디에도 미달성 항목을 달성으로 쓰지 않는다.

---

## Task 0: 잔여 결함 스윕 (검증-후-종결, 추측 금지)

아래는 2026-07-02 총리뷰·수리 기록에 남은 항목들이다. 각각 **재현 시도 → 닫혔으면 문서에서 잔여 표기 제거,
열려 있으면 최소 수리(TDD)**:

- [x] **A2 real-host proof:** `fixtures/w12/capture-manifest.json`이 real A2 bytes를 담고
  `scripts/revalidate_w12.py`가 strict로 재도출하는지 확인. W1의 A2 fixture는 W12 manifest와
  byte-for-byte 동일해야 하며, README·conformance 문서는 "committed W12 A2 evidence bytes"와
  dedicated observer uid 호스트 요건으로만 표기한다(과대주장 스윕).
- [x] **kill CLI runlog 재구성:** `witnessd kill --all`이 live pid를 runlog에서 재구성해 실제로 죽이는지,
  아니면 정직한 no-op + `ERR_WITNESSD_KILL_NO_TARGETS`인지 현재 동작을 테스트로 고정(8bbc83e 이후 상태 확인).
  2026-07-03 확인: 대상 0개 경로는 `ERR_WITNESSD_KILL_NO_TARGETS`와
  `all_confirmed_dead:false`를 반환하고 거짓 종료 증거를 만들지 않는다
  (`tests.test_cli_w5.TestCliW5.test_kill_all_without_supervised_children_is_not_success`,
  `tests.test_killswitch.TestKillSwitch.test_kill_all_without_known_children_does_not_claim_success`).
  live pid가 runlog에서 재구성되는 경로는 별도 테스트로 유지된다.
- [x] **테스트 cwd 독립성(P2-1):** repo 밖 임의 디렉토리에서
  `uv run python3 -m unittest discover -s /home/ubuntu/moonweave/witnessd/tests -t /home/ubuntu/moonweave/witnessd`
  가 통과하는지 확인(tests/__init__.py sys.path 주입으로 닫혔을 것). 통과하면 SPEC/플랜의 잔여 표기 제거.
  2026-07-03 확인: `/tmp`에서 위 명령이 `Ran 255 tests ... OK`.
- [x] **W8 evidence_mode 정직성(2026-07-02 W8 리뷰 follow-up):** `docs/conformance/OVERT.md`에
  "`evidence_mode`는 **self-declared/미강제** 필드다 — witnessd에 live notary co-sign·co-epoch 앵커가
  없어 contemporaneous vs post_hoc를 바이트로 증명하지 못한다. OVERT DELAYED_NOTARY(0x01)는 모델링하지
  않는다"를 명시(과대주장 방지). 그리고 `scripts/revalidate_w8.py`의 negative
  `_assert_negative_post_hoc_detected`가 **tautology**(미스라벨 fixture 필드만 assert, 실제 탐지기 없음)임을
  인정: 탐지기가 없으므로 그 함수를 "self-attestation은 미강제"를 **문서화하는** 형태로 바꾸거나
  제거하고, "guard/detected" 네이밍을 버린다(과거 W4 tautology negative 안티패턴 반복 금지).
  2026-07-03 확인: `docs/conformance/OVERT.md`와
  `docs/conformance/witnessd-protocol-profile.md`가 self-declared temporality와
  `DELAYED_NOTARY` 제외를 명시하고, `scripts/revalidate_w8.py`는
  `_assert_self_attestation_unenforced_fixture`로 정직성 fixture를 보존한다.
- [x] **스윕 결과를 커밋 메시지에 요약** (닫힘 N건 / 수리 M건 / 환경 의존 K건 / 정직성 표기 L건).
  v2.0.0 릴리스 커밋 요약 대상: 닫힘 3건 / 수리 0건 / 환경 의존 0건 /
  정직성 표기 1건.

## Task 1: GitHub Actions CI — witnessd

- [ ] `.github/workflows/ci.yml` 작성. 잡 구성(전부 required):
  1. **unit** — `python3 -m unittest discover -s tests` (ubuntu-latest, Python 3.10 + 3.12 매트릭스).
     Depone은 dev/test 의존: `git clone https://github.com/Moonweave-Systems/Depone` 후 `PYTHONPATH`로 주입
     (private repo면 `secrets.DEPONE_TOKEN` + `actions/checkout@v4` `repository:`/`token:` 사용 — 워크플로
     주석에 토큰 세팅 방법 명기).
  2. **revalidate** — `scripts/revalidate_w1.py` … 존재하는 전부 + `revalidate_key_rotation.py`.
  3. **decoupling-guard** — depone을 **설치하지 않은** 잡에서 `python3 -c "import witnessd.emitter, witnessd.__main__"`
     + 셸 레인 E2E(`witnessd run … --allow out.txt`) → evidence-pending 출력 확인. 이 잡이 이 제품의
     "runtime is stdlib-only" 주장의 CI 증거다.
  4. **no-overclaim** — `grep -rn "VERIFIED" witnessd/ | grep -v render_status` 류의 금지 문자열 검사
     (기존 status.py 규율의 CI 강제).
- [ ] 로컬에서 `act` 없이는 CI를 실행 못 하므로, 각 잡의 커맨드를 로컬에서 1회씩 그대로 실행해 그린 확인 후 커밋.
- [ ] **Depone repo CI는 이 플랜 범위 밖** — 별도 항목으로 Depone repo에 동일 패턴 제안만 기록
  (moonweave 규칙: Depone 변경은 Depone PR).

## Task 2: README 재작성 — 대표작 서사

- [ ] 구조(정확성 게이트: 모든 주장에 코드/fixture/스크립트 경로를 링크):
  1. **한 줄 논제:** "done is signed bytes, not a self-reported string" (기존 유지).
  2. **왜:** 기존 오케스트레이터의 self-report 문제(자기채점) — 구체 사례는 링크로만, 비방 없이.
  3. **어떻게:** 2-제품 아키텍처 다이어그램(witnessd=arbiter/실행, Depone=verifier/비실행),
     증거 흐름(emit → bytes → 오프라인 재도출), 위조 시도가 서명에서 죽는 negative fixture 링크.
  4. **증명:** n=1 재도출(8/8) 재현 커맨드 블록 — 독자가 직접 돌릴 수 있게
     (`witnessd run … && PYTHONPATH=<depone> python3 scripts/revalidate_w1.py` 패턴).
  5. **OVERT:** conformance 문서 링크 + "AAL-3 Agentic, Exclusions 명시" 한 줄(W8 산출물).
  6. **정직한 한계:** A2 데모, 투명성 로그/독립 notary 없음(AAL-4 로드맵), keyless gate blocked.
  7. **설계 문서:** SPEC.md, docs/plans/, docs/ops/ 링크.
- [ ] 커밋.

## Task 3: 태그 + 릴리스

- [ ] 최종 그린 확인(아래 Matrix) → `git tag -a v1.0.0 -m "..."`.
  태그 메시지에 포함: 웨이브 W1~W8 요약, conformance 선언 요지, 알려진 한계 3줄.
- [ ] **push는 하지 않는다** — main push는 가드로 사용자 승인 필요. 최종 보고에
  `git push origin main --tags` 커맨드를 제시하고 멈춘다.
- [ ] (선택, 사용자 결정 대기) repo 공개 전환 여부는 이 플랜이 결정하지 않는다.

## Final Validation Matrix

```bash
cd /home/ubuntu/moonweave/witnessd
PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 -m unittest discover -s tests
for s in scripts/revalidate_*.py; do PYTHONPATH=/home/ubuntu/moonweave/depone uv run python3 "$s"; done
uv run python3 -m witnessd self-test --all
# depone-free 환경에서: import + run E2E → evidence-pending
# make test && make dogfood (moonweave 워크스페이스에서)
# README의 모든 재현 커맨드 블록을 실제로 1회 실행해 출력 일치 확인
```

**Explicit Non-Changes:** 새 기능 추가 금지(이 웨이브는 굳히기) / production gate 상태 변경 금지 /
push·공개 전환은 사용자 결정 / Depone repo 수정 금지.
