# witnessd — 런타임 설계 Spec

Status: 설계 spec (design), 2026-07-01. 별도 repo(#2), Depone(비실행 검증기)과 분리.
One-line: 완료(done)를 자기보고 텍스트가 아니라 관측자-서명 바이트로 정의하는, provable-by-construction을 1급 목표로 삼은 팀 코딩 런타임.

## 목차
- [1. 개요 · 논제 · 범위 · 비목표 · 용어집](#)
- [2. 아키텍처](#)
- [3. 신뢰 · 보안 모델](#)
- [4. Depone 증거 계약 (witnessd가 방출하는 것)](#)
- [5. 구현 웨이브 (W1–W5)](#)
- [6. 에지 케이스 · 예외 처리 (포괄)](#)
- [7. 테스트 · 수용 기준 · 롤아웃 · 도그푸드](#)
- [8. 오픈 결정 · 리스크 · 향후](#)

---
## 1. 개요 · 논제 · 범위 · 비목표 · 용어집

## 1.1 제품 한 줄 정의

**witnessd**는 팀 코딩 작업의 "완료(done)"를 자기보고 텍스트가 아니라 관측자-분리(observer-separated), 해시-바인딩, 서명된 바이트로 정의하고 그 재도출을 provable-by-construction 1급 목표로 삼는 실행 런타임이다. CLI 이름은 `witnessd`. `witnessd`는 실행(worker spawn, retry, worktree, schedule)을 전담하고, 검증은 별도 repo인 Depone(non-executing verifier)이 오프라인 바이트 재검증으로 전담한다. 두 repo는 물리적으로 분리된다.

> **Decision (재검토 가능):** 이름은 `witnessd`, CLI 바이너리도 `witnessd`. Depone과 별도 repo 2개로 물리 분리한다.
> **Rationale:** observer-separation이 이 런타임의 유일무이한 코어 정체성이며, `witnessd`("관측자 데몬")라는 이름이 그 정체성을 정확히 담는다. repo를 물리 분리하는 이유는 "non-executing 검증기의 독립성"이 신뢰의 근거이기 때문이다 — 같은 모노레포에 있으면 "검증기가 실행 계층과 공모하지 않는다"는 주장이 약해진다. 두 repo가 공유하는 유일한 계약은 `canonical_hash` 규약과 스키마이며, `witnessd`가 오염되어도 공개키(evidence 밖에 존재)를 위조하지 못하는 한 A1/A2 assurance나 그 위에 얹히는 서명된 report-level 신뢰를 만들 수 없다.

## 1.2 Leapfrog 논제 — 왜 지금, 경쟁 맹점 요약

경쟁하는 팀 코딩 런타임(OMX, LazyCodex/OMO, Claude Code Teams, Cursor, Devin, Factory)은 실행·오케스트레이션 UX에서는 치열히 경쟁하지만, 예외 없이 **완료 신호가 자기보고(self-report) 텍스트**다. 실측된 사례들:

- OMO는 트랜스크립트에서 파싱한 `<promise>VERIFIED</promise>` 태그가 신뢰의 뿌리이고, `task_update`는 검증 없이 아무 태스크나 completed로 마킹 가능하다.
- OMX는 같은 팀 런에 대해 `run-state.json`(active:true)과 `team-state.json`(active:false, cancelled)이 서로 모순하며, tmux pane이 사라진 지 일주일인데 `active:true`로 남아 있고, `omx doctor`는 그 상태에서 "all clear"를 반환한다 — 가장 위험한 실패모드(조용히 죽은 팀)에서 헬스체크가 false-positive를 낸다.
- Devin은 "모든 자동 체크를 통과하는 확신에 찬 환각"이 명명된 실패모드이고, Factory는 검증 단계로 자동 전이하지 않은 채 코드를 배송한다.

즉 아무도 "실행된 행위 하나하나가, 방출 시점에 관측자-분리 + 해시-바인딩 + 서명된 증거를 남겨, 실행하지 않는 외부 검증기가 오프라인에서 assurance 등급을 재도출할 수 있게" 런타임을 만들지 않았다. Depone의 계약(`capture_bridge.py`, `observe.py::enforce_observer_separation`, `isolation.py`, `sign.py`)이 그 재도출 규칙을 이미 정의해 두었으므로, `witnessd`의 유일한 의무는 그 계약을 만족하는 아티팩트를 native로 방출하는 것이다. 이렇게 되면 역설적으로 더 공격적인 자율성(auto-retry, auto-spawn, auto-worktree)을 안전하게 밀 수 있다 — 모든 행위가 사후에 반증 가능(falsifiable)하므로 자율성이 신뢰 리스크가 아니게 되기 때문이다.

## 1.3 대상 사용자

- 팀 규모로 여러 코딩 에이전트(shell/Codex/Claude Code/OpenCode)를 병렬 lane으로 굴리는 개발자·개발팀. "완료했다"는 주장을 사람이 매번 재검토하지 않아도 되는 신뢰를 원한다.
- OMX/LazyCodex류 도구를 이미 쓰고 있어 상태 오염 없이 공존시켜야 하는 사용자.
- 규제·감사 산업의 팀: 실행 로그가 아니라 air-gapped 환경에서 Depone만으로 오프라인 재검증 가능한 증거를 요구하는 조직(§1.6 포지셔닝 참조).

## 1.4 범위 (In-scope)

`witnessd`가 자유롭게 수행하는 실행 책임(Depone spec의 "Not implemented yet" 목록 전체를 `witnessd`가 인수한다):

- worker(하위 에이전트/어댑터) spawn, supervised 프로세스 관리, retry.
- durable 세션 소유·재개(reboot/host 이동 포함).
- lane별 git worktree 자동 생성·관리.
- Codex CLI, Claude Code, OpenCode, shell을 통한 실행(어댑터 계층).
- merge receipt를 위한 실제 git merge/rebase 시도.
- 위 모든 실행 행위 각각에 대해 Depone-verifiable 증거(capture-manifest, observer_capture, isolation facts, runner/worktree receipt, DSSE 서명, evidence bundle, team ledger, prev_capture 체인)를 **런타임 native로**(사후 bolt-on이 아니라 실행과 동시에) 방출.
- 단일 append-only 서명 이벤트 로그를 substrate로 삼아 그 위에서 run-state/team-state를 projection으로 파생.
- ownership-region 락, heartbeat liveness, idempotency key, 비용/토큰 서킷브레이커, hard pause/interrupt, kill-switch, 자동 학습 캡처 — 모두 W1 이후 웨이브에서 evidence emitter 위에 얹는다(§5 진행 방향은 이 spec의 다른 섹션에서 다룬다).

## 1.5 비목표 (Non-Goals)

Depone spec(`depone-native-team-runtime-spec.md`)의 Non-Goals를 그대로 계승하며, 계승 항목은 Depone 쪽 책임으로 남고 `witnessd`가 대신 떠맡지 않는다. 여기에 런타임 확장분을 더한다.

**Depone으로부터 계승(검증기 쪽 비목표, `witnessd`가 침범하지 않음):**
- Depone은 절대 worker를 launch/run하지 않으며, `witnessd`가 방출한 바이트만 오프라인에서 소비한다.
- Depone은 assurance를 상향(raise)하지 않는다 — assurance verdict는 blocked/refuted/A0/A1/A2 중 하나이며 부분점수 없음. assurance 정수 상한은 A2이고, operator DSSE 서명은 이 등급을 올리지 않는 별도 report-level 신뢰 축(§3.1)이다.
- No public benchmark or superiority claim(Depone spec 계승). `witnessd`는 별도 repo로서 이 제약을 직접 받지는 않지만, §1.8 성공기준과 §1.2 논제에 반하는 "VERIFIED/ORCHESTRATION COMPLETE" 류 마케팅 문구는 UX 규율(§1.7)로 금지한다.
- No product alias/personality renaming in schema ids — `witnessd`가 방출하는 아티팩트의 `kind`/`schema_version`은 Depone이 정의한 값을 그대로 사용하며 브랜드 이름으로 대체하지 않는다.

**런타임 확장 비목표(`witnessd` 자체가 하지 않는 것):**
- No cloud provisioning, no secrets management — `witnessd`는 실행 substrate(로컬 프로세스/컨테이너)만 관리하고 클라우드 자원을 프로비저닝하지 않는다.
- No provider-specific SDK 강제 의존 — 어댑터 계층은 CLI/subprocess 경계로 substrate를 흡수하며, 특정 벤더 SDK에 런타임 코어를 결합하지 않는다.
- No silent自동 branch deletion, no automatic PR merge without a passing Team Ledger 검증.
- Depone의 non-executing 경계를 절대 침범하지 않는다 — `witnessd`가 검증 로직(assurance 재도출, hash 재계산, chain 검증)을 자체 구현해 verdict를 자체 발급하지 않는다. 검증은 항상 별도 Depone 프로세스가 수행한다.
- Sigstore Fulcio keyless + Rekor를 이 spec의 범위에서 명시적으로 deferred — signing 단계는 operator-held Ed25519 DSSE(§1.9 Glossary DSSE 항목)로 한정하고, keyless 시맨틱을 주장하지 않는다.
- 컴플라이언스 프레임워크(SOC2/ISO 등) 인증 대행은 하지 않는다 — evidence bundle을 재료로 제공할 뿐, 인증 발급 주체가 아니다.

> **Decision (재검토 가능):** A2 격리는 uid 모델(`runner_uid != observer_uid != 0`, observer_dir가 0700이고 runner가 쓸 수 없음)을 1급으로 삼고, docker 컨테이너 모델(`container-boundary-unwritable-observer-dir`)은 후속 옵션으로 둔다.
> **Rationale:** uid 모델은 가볍고 잠긴 기업 머신에서도 세팅 가능하며, `isolation.py::_verify_uid_isolation_boundary`가 이미 이 모델을 fail-closed로 정의해 재사용할 수 있다. 컨테이너 모델은 강하지만 OpenHands류 도구가 겪은 "잠긴 기업 환경에서 Docker-in-Docker 마찰"을 상속하므로 1급에서 제외한다.

> **Decision (재검토 가능):** 서명은 operator-held Ed25519 DSSE로 한정한다. sign(서명 생성)은 `witnessd`(런타임)가 수행하고, verify(서명 검증)는 Depone이 out-of-band로 전달받은 공개키로 수행한다. private key는 verify 경로에 절대 존재하지 않는다.
> **Rationale:** Depone `agent_fabric/sign.py`가 이미 "operator-key signing step, public-key verifiable, not keyless, not Fulcio-backed, not Rekor-logged"임을 명시하고 있고, `witnessd`가 이 경계를 그대로 계승해야 두 repo 간 신뢰 계약이 일관된다. Sigstore keyless(keyless 서명 축)는 Depone 쪽에서도 명시적으로 deferred 상태이므로 `witnessd`가 먼저 주장해서는 안 된다.

## 1.6 포지셔닝

`witnessd`는 두 시장을 동시에 겨눈다: (1) 개발자 툴 — 팀 규모 코딩 자동화를 안전하게 미는 실무 도구, (2) 규제/감사 wedge — air-gapped 환경에서 Depone만 반입해 evidence bundle을 오프라인 재검증하는 컴플라이언스 도구. 후자는 시장이 좁지만 "실행 로그를 신뢰하지 않아도 되는" 요구를 정조준한다.

## 1.7 UX 규율

> **Decision (재검토 가능):** "evidence-pending"을 하드 규칙으로 강제한다. Depone 외부 검증이 통과하기 전에는 어떤 UI/CLI 출력도 "VERIFIED", "DONE", "COMPLETE", "ORCHESTRATION COMPLETE" 같은 self-report theater 문구를 표시하지 않는다.
> **Rationale:** OMO의 `<promise>VERIFIED</promise>` 태그 오인식(#4963/#4620/#4683)과 OMX `doctor`의 false-positive "all clear"가 이 spec이 조롱한 정확한 실패모드다. `witnessd`가 같은 문구를 자기 완료 신호로 재사용하면 논제(§1.2) 자체를 스스로 배신한다. 완료 상태의 유일한 표현은 "evidence emitted, pending Depone verification" 및 그 뒤의 Depone verdict(A0/A1/A2/blocked/refuted) 문자열과 그에 부수하는 signing_status여야 한다.

## 1.8 성공기준

`witnessd`가 이 spec을 만족했다고 주장하려면 다음이 모두 참이어야 한다(각 항목은 이후 섹션에서 구체 명령/아티팩트로 재정의된다):

1. `witnessd`가 방출한 evidence bundle 하나를, `witnessd` 프로세스가 완전히 종료된 후 Depone이 단독으로(오프라인, bytes-only) 재검증해 A0/A1/A2 중 하나 또는 blocked/refuted를 재도출할 수 있다.
2. 동일 evidence bundle에 대해 두 번 실행한 Depone 검증이 동일한 verdict를 낸다(재현성).
3. observer_capture의 부모 디렉터리가 runner sandbox 밖에 있음을 `enforce_observer_separation` 동등 로직으로 확인할 수 있다.
4. uid 격리 모델을 선언한 캡처는 `runner_uid != observer_uid`, `runner_uid != 0`, `observer_dir_writable_by_runner == False`를 실측 facts로 제시하며, 이 중 하나라도 미지(unknown)면 boundary가 False로 fail-closed된다.
5. 이벤트 로그가 substrate이고 run-state/team-state가 그 로그의 pure projection임을, 로그를 재생(replay)해 동일한 상태를 재구성하는 테스트로 증명한다.
6. 모든 사용자향 상태 출력이 단일 `render_status()`(출력 도메인이 상태 enum으로 고정)를 경유함을 확인한다 — §1.7 위반 문구는 그 enum 값 집합에 애초에 존재하지 않으므로, 테스트는 enum 값만 검사해 구조적으로 강제한다(§7.6).
7. private key가 verify 경로 코드/의존성 어디에도 존재하지 않음을 확인한다(Depone verify 진입점이 공개키 파일 경로만 받는지 검사).

## 1.9 용어집 (Glossary)

이 문서의 다른 모든 섹션은 아래 정의를 그대로 재사용한다. 재정의하지 않는다.

| 용어 | 정의 |
|---|---|
| **observer** | runner와 별도 프로세스(및 A2에서는 별도 uid)로 실행되어 command 실행 결과·diff·test 출력을 직접 관측·기록하는 주체. `observed_by == 'depone-observer'`로 캡처에 명시된다. |
| **runner** | 실제 작업(코드 실행, 파일 변경)을 수행하는 프로세스/에이전트. 자기 자신의 성공을 seal하거나 validate할 권한이 없다. |
| **lane** | 하나의 독립적으로 실행·검증되는 작업 단위(보통 하나의 worktree + 하나의 어댑터 실행에 대응). Team Ledger는 여러 lane을 fan-in한다. `lane_id`로 식별한다. |
| **capture-manifest** | `kind: agent-fabric-capture-manifest`, `schema_version: "1.0"`인 최상위 증거 아티팩트. fixture hash, observer_capture, isolation facts, chain 바인딩을 하나로 묶는다(`capture_bridge.py`). |
| **observer_capture** | observer가 직접 생성한 원시 캡처 자료(command_receipts, diff_summary, touched_files, test_output 등)를 담은 블록. capture-manifest에 해시로 바인딩된다. |
| **assurance (A0/A1/A2)** | 증거의 신뢰 등급. `A0-claims-only`(자기보고만, 관측 없음) → `A1-local-observed`(same-uid observer가 분리 프로세스로 관측) → `A2-isolated-observed`(uid 또는 container 특권 경계로 관측 분리 실증). Depone `capture_bridge.py`에는 이 셋(`ASSURANCE_A0`/`ASSURANCE_A1`/`ASSURANCE_A2`)뿐이고 그 외 값은 `validate_capture_manifest`가 거부한다 — **A3라는 assurance 등급은 존재하지 않으며 assurance 정수 상한은 A2다.** 등급은 오직 실측 facts에서 재도출되며 부분점수 없이 fail-closed로 하향된다. operator DSSE 서명은 등급을 올리지 않고 그 위에 얹히는 별도 report-level 신뢰 축이다(아래 signing_status 항 및 §3.1). |
| **receipt** | 어떤 실행이 실제로 일어났음을 증명하는 구조화 아티팩트의 총칭. 이 spec에서 쓰이는 구체 종류: runner receipt(`kind: agent-fabric-runner-receipt`, `schema_version: "1.0"`), worktree lane receipt(`kind: depone-worktree-lane-receipt`, `schema_version: "0.1"`). |
| **event-log** | `witnessd`가 소유하는 단일 append-only, hash-chained, 서명된 상태전이 로그. team start/cancel, dispatch, delivery, merge 등 모든 상태전이가 여기 append되며, run-state/team-state는 이 로그의 pure projection이다(별도의 mutable JSON 파일로 독립 존재하지 않는다). |
| **evidence bundle** | in-toto Statement v1 + DSSE 봉투 + OTel GenAI span을 묶어 Depone `evidence_substrate.ingest_signed_evidence_bundle`이 소비할 수 있는 단위. 각 subject digest가 재계산 가능해야 하며, 서명이 없으면 `signatures == []`가 정확히 명시되어야 한다(그렇지 않으면 blocked). |
| **adapter** | 특정 실행 substrate(shell, Codex CLI, Claude Code, OpenCode)를 동일한 runner-receipt 스키마로 흡수하는 `witnessd` 내부 계층. Depone은 어댑터 종류와 무관하게 동일한 검증 로직을 적용한다. |
| **ownership-region** | dispatch 전에 lane에 배타적으로 claim되는 파일/모듈 범위. claim/release 각각이 event-log 이벤트로 기록되며, 겹치는 region은 락으로 차단된다. |
| **DSSE** | Dead Simple Signing Envelope. 이 spec에서는 operator-held Ed25519 키로 `witnessd`가 서명(sign)하고, Depone이 out-of-band 공개키로 검증(verify)한다. `DSSEv1` PAE(Pre-Authentication Encoding) 포맷을 따른다. private key는 verify 경로에 존재하지 않는다. |
| **canonical_hash** | `sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()`(ensure_ascii 기본값 True). 이 spec 전체에서 모든 콘텐츠 주소화 해시·subject digest·체인 링크의 유일한 정의이며, 재구현 시 이 바이트 규약과 정확히 일치해야 한다. **단, DSSE 서명 payload 인코딩은 예외**로 §4.0-note (a)(evidence-substrate 번들은 `ensure_ascii=False`)/(b)(provenance binding은 `ensure_ascii=True`)를 따른다 — subject digest와 매니페스트/리시트 해시 자체는 언제나 이 canonical_hash로 계산한다. |
| **prev_capture_hash / capture chain** | 각 capture-manifest가 직전 manifest의 canonical_hash를 `prev_capture_hash`로 참조해 append-only 순서를 증명하는 체인. genesis는 `prev_capture_hash: null`. drop/reorder/tamper는 `verify_capture_chain`에 의해 blocked로 판정된다. |
| **isolation facts** | observer가 실측해 제시하는 특권 경계 원시 사실(`runner_uid`, `observer_uid`, `observer_dir_writable_by_runner`, container 관련 필드 등). `verify_isolation_boundary`가 이 facts에서만 `boundary: bool`을 재도출하며, 미지 필드는 항상 boundary를 False로 유지한다(위조 불가). |
| **fail-closed** | 미지의 fact, hash mismatch, stale `source_fixture_hash`, 범위 밖 `touched_files`, 서명 부재, openssl 부재(`ERR_OPENSSL_UNAVAILABLE`), chain 단절 등 어느 조건이든 감지되면 즉시 A0/blocked/refuted로 떨어지며 부분점수를 주지 않는다는 이 spec 전역의 불변식. |
| **team ledger** | `kind: depone-team-ledger`(스키마 `0.1`)로 여러 lane 결과를 fan-in 검증하는 아티팩트. 겹치는 `touched_files`가 있으면 통과한 merge receipt가 없는 한 전체가 통과할 수 없다. verdict는 `raises_assurance: false, approves_merge: false`로, 그 자체로는 assurance를 올리거나 머지를 승인하지 않는다. |
| **evidence-contract.json** | `schema_version: "v105.verify_wedge"`로 root-relative하게 선언되는 계약 아티팩트. 최소 1개 이상의 enforcement directive를 포함하며, `git-diff.patch`에서 test-weakening 시도를 구조적으로 탐지한다. |
| **evidence-pending** | Depone 외부 검증이 통과하기 전 `witnessd`가 UX에 표시해야 하는 유일한 완료 상태 표현(§1.7). "VERIFIED/DONE/COMPLETE" 등 self-report 완료 문구의 대체어이며 하드 규칙으로 강제된다. |
| **signing_status (report-level 신뢰 축)** | assurance 정수 등급과 **직교**하는 신뢰 축. operator-held Ed25519 DSSE 서명이 A1/A2 manifest 위에 얹혀 `trusted-observer-provenance` 레코드와 `signing_status`(`"signed-ed25519-operator-key"` = `sign.SIGNING_STATUS_OPERATOR_KEY`)를 남긴다. 이는 서명자(trusted-observer/operator)의 provenance를 증명할 뿐 assurance를 A2 위로 올리지 않는다(`boundary.raises_assurance=false`). 미서명은 `signatures==[]`·`"unsigned-content-addressed"`로 정직히 표기한다. Sigstore Fulcio keyless + Rekor는 deferred(§3.10). |
| **Depone / keelplane** | 검증 계층의 두 이름을 확정한다: 배포·디렉터리(검증기 repo)는 `keelplane`, 그 안의 Python 패키지·CLI·import 경로는 `depone`(`python3 -m depone`, `from depone.agent_fabric...`). 본 spec에서 "Depone"은 검증기 역할·패키지를, "keelplane"은 그 repo/배포 단위를 가리킨다. |
| **run_id / session_id / lane_id** | 스코프 계층. `run_id`(ULID, §6.1.1)는 하나의 witnessd 실행 세션 전체를 식별하며 runlog 체인·idempotency namespace·durable session의 최상위 키다. `session_id`(§2.4.4)는 Session Store 재개 단위로 본 spec에서 `run_id`와 1:1 대응한다(재개는 `run_id`로 한다). `lane_id`는 그 run 안의 개별 작업 단위(보통 worktree+어댑터 1개)로 **1 run = N lanes**다. capture-manifest·runner/worktree receipt·evidence bundle·evidence-contract는 `lane_id`로, runlog·team-ledger fan-in·idempotency_key는 `run_id`로 스코프된다. |

관련 근거 파일(절대경로): `/home/ubuntu/depone-assurance-repair/docs/depone-native-team-runtime-spec.md`, `/home/ubuntu/depone-assurance-repair/depone/agent_fabric/{capture_bridge,observe,isolation,observer_provenance,sign,seal,paired_run,worktree_receipt,team_ledger,evidence_substrate,claim_gate}.py`, `/home/ubuntu/depone-assurance-repair/depone/verify/{engine,evidence_contract}.py`, `/tmp/claude-1001/-home-ubuntu/0635c8fb-2912-4427-8a95-18dd335994b3/scratchpad/new_runtime_report.md`.

---

## 2. 아키텍처

이 절은 witnessd 런타임의 구조를 규정한다. 목표는 단 하나다. 런타임이 수행하는 **모든 행위가, 실행하지 않는 외부 검증기(Depone)가 오프라인·바이트만으로 A0/A1/A2를 재도출할 수 있는 서명된 증거를 방출 시점에 남기게** 만드는 것. 신뢰/서명 세부 규칙은 §3, 증거 스키마의 완전한 필드 정의는 §4의 소관이며, 이 절은 그 둘을 참조하는 컴포넌트 경계·인터페이스·데이터 흐름만 확정한다.

## 2.1 2-repo 경계와 공유 계약

> **Decision (재검토 가능): witnessd와 Depone은 물리적으로 분리된 2개의 repo다.**
> Rationale: Depone은 "실행하지 않는 검증기"라는 점 자체가 신뢰의 근거다. 검증 로직이 런타임과 같은 repo·같은 프로세스·같은 배포 단위에 있으면 "런타임이 오염되면 검증도 오염된다"는 반론을 구조적으로 막을 수 없다. 물리 분리 + 공개키 out-of-band 전달이라야 "런타임이 통째로 악의적이어도 서명·검증 경계를 넘지 못한다"를 주장할 수 있다. monorepo는 계약 공유(2.1.3)를 편하게 하지만 이 신뢰 서사를 포기하므로 채택하지 않는다.

### 2.1.1 witnessd가 하는 일 (실행 계층)

witnessd는 **자유롭게 실행한다**: worker/observer 프로세스 spawn, durable 세션 재개, auto worktree, retry, schedule, 팀 fan-in. 유일한 의무는 각 행위가 끝날 때 §4 계약을 만족하는 아티팩트를 방출하고 operator 키로 서명하는 것이다. witnessd는 자신의 verdict를 스스로 상향하지 않는다(자기 성공 seal 금지, 2.4.8).

### 2.1.2 Depone이 하는 일 (검증 계층)

Depone은 launch/run/retry/raise를 **하지 않는다**. witnessd가 넘긴 바이트 번들만 받아 `validate_capture_manifest`, `verify_isolation_boundary`, `verify_capture_chain`, `validate_trusted_observer_provenance`, `ingest_signed_evidence_bundle`, `validate_runner_receipt`, `build_team_ledger_verdict` 등으로 verdict를 재도출한다. 모든 검증기의 `boundary.raises_assurance`는 `false`다. witnessd 코드는 Depone repo에 존재하지 않는다.

### 2.1.3 공유하는 유일한 계약 = canonical hashing 규약 + 스키마

두 repo가 공유하는 것은 코드가 아니라 **바이트 규약**이다. 이것이 어긋나면 모든 hash가 어긋나 fail-closed된다.

- **Canonical hash (불변식).** 모든 content-address는 정확히
  `sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()`
  이다. Depone에서 이 함수는 `depone.agent_fabric.claim_gate.canonical_hash`이며 `capture_bridge._sha256_json`과 바이트 단위로 일치한다. witnessd는 이 규약을 **재구현하지 말고 동일 바이트를 산출**해야 한다(아래 검증 참조). ensure_ascii 기본값(True) 및 `separators=(",", ":")`를 반드시 지킨다.
- **공유 스키마(=witnessd가 방출, Depone이 소비하는 kind들).** `agent-fabric-capture-manifest`(schema 1.0), `agent-fabric-runner-receipt`(1.0), observer capture 블록(required fields: `observed_by`, `source_fixture_hash`, `diff_summary`, `touched_files`, `test_output`, `command_receipts`), isolation facts(`uid-boundary-unwritable-observer-dir` 등 model), `trusted-observer-provenance`(1.0, DSSE-Ed25519), `depone-evidence-substrate-bundle`(1.0, in-toto Statement v1 + DSSE), `depone-worktree-lane-receipt`(0.1), `depone-team-ledger`(0.1), `evidence-contract.json`(schema `v105.verify_wedge`).
- **공개키만 out-of-band.** private 서명키는 witnessd 안에만, 공개키는 Depone 검증 경로에만. private 키는 verify 경로에 **절대** 나타나지 않는다(§3).

> **검증 (계약 정합성 게이트).** witnessd CI는 다음을 강제한다.
> (1) `canonical_hash` 파리티 테스트: 고정 fixture 집합에 대해 witnessd가 계산한 hex가 Depone `claim_gate.canonical_hash` 출력과 문자 단위로 일치.
> (2) round-trip 테스트: witnessd가 방출한 각 아티팩트를 Depone의 대응 `validate_*`/`ingest_*`에 넣어 오류 리스트가 `[]`이고 `assurance`/`decision`이 기대값인지 assert.
> (3) 스키마 버전 상수는 witnessd에 하드코딩하지 말고 Depone 모듈 상수(`CAPTURE_MANIFEST_VERSION` 등)를 단일 출처로 참조하는 fixture-golden 테스트로 고정한다.

## 2.2 신뢰 근거: event-log가 substrate, 상태는 projection

이 런타임의 핵심 구조 결정이다.

- **단일 append-only 서명 event log가 유일한 substrate(SoT)다.** 팀 start/cancel, dispatch, worker spawn/exit, heartbeat, ownership claim/release, worktree lane, delivery, merge, evidence emit — 모든 상태전이는 witnessd 내부 **runlog 체인**(`prev_event_hash`, kind `witnessd-runlog-event`, §6.0.3)에 **append**된다. 이 runlog 체인은 capture-manifest 체인(§4.10, `prev_capture_hash`, Depone `verify_capture_chain` 대상)과 **별개의 체인**이다 — 상태전이 이벤트는 capture-manifest가 아니므로 `verify_capture_chain`의 대상이 아니고, capture-manifest만이 그 함수의 입력이다(두 체인의 분리는 아래 §2.2 마지막 항에서 명시).
- **`run-state`와 `team-state`는 로그의 pure projection**이다. 별도로 mutate되는 파일이 아니라 로그를 fold해서 매번 재계산되는 파생 뷰다. 따라서 "run-state는 active:true인데 team-state는 cancelled" 같은 split-brain이 **구조적으로 발생 불가**하다(두 뷰가 동일 로그에서 나오므로).
- **두 체인의 분리(명시).** witnessd는 두 개의 서로 다른 hash-chain을 방출하며 이를 절대 섞지 않는다.
  - **(a) witnessd 내부 runlog 체인** — `prev_event_hash`로 링크되는 상태전이 로그(heartbeat/dispatch/claim/release/merge/spawn/exit 등, kind `witnessd-runlog-event`). genesis head는 `prev_event_hash=null`, 이후 각 이벤트는 직전 이벤트의 `event_hash`를 담는다. 이 체인의 무결성은 witnessd 자체 검증기(`witnessd verify --runlog`, §6.2.5)가 재계산하며, **Depone의 검증 대상이 아니다**(Depone `verify_capture_chain`은 이 이벤트들을 입력으로 받지 않는다).
  - **(b) capture-manifest 체인** — `prev_capture_hash`로 링크되는 **capture-manifest 전용** 체인(§4.10). genesis head는 `prev_capture_hash=null`, 이후 각 manifest는 직전 **manifest**의 canonical hash를 담는다. drop/reorder/tamper는 downstream 링크를 깨고 Depone `verify_capture_chain(manifests)`이 `blocked`을 낸다 — 이 함수의 입력은 오직 구조적으로 유효한 capture-manifest 리스트다.
  두 체인 모두에서 "무엇이 어떤 순서로 일어났나"가 사후에 반증 가능하되, Depone이 재도출하는 것은 (b)뿐이고 (a)는 witnessd 내부 무결성이다. run-state/team-state는 (a) runlog 체인의 pure projection이며, lane별 assurance는 (b) capture-manifest 체인에서 재도출된다.
- **liveness도 projection이다.** `active`는 파일 플래그가 아니라 "N초 이내에 서명된 heartbeat 이벤트가 로그에 관측됨"으로 파생된다(2.4.3). 죽은 worker가 `active:true`로 남는 zombie 상태가 나올 수 없다.

## 2.3 데이터 흐름

```
 repo #1: witnessd (실행)                                    repo #2: Depone (검증, non-executing)
 ┌──────────────────────────────────────────────┐          ┌───────────────────────────────────┐
 │  Orchestrator / Planner                        │          │  validate_capture_manifest         │
 │   lane packet · ownership · budget · stop      │          │  verify_isolation_boundary         │
 └───────────────┬────────────────────────────────┘          │  verify_capture_chain              │
                 │ dispatch(idempotency_key)                  │  validate_trusted_observer_        │
 ┌───────────────▼─────────┐   ┌─────────────────┐            │      provenance (public key)       │
 │ Scheduler               │   │ Session Store    │           │  ingest_signed_evidence_bundle     │
 │  restart-safe, no tmux  │◀─▶│  crash-safe,     │           │  validate_runner_receipt           │
 └───────────────┬─────────┘   │  ID resume       │           │  build_team_ledger_verdict         │
                 │ spawn        └─────────────────┘           │  validate_evidence_contract        │
 ┌───────────────▼───────────────────────────┐               └────────────────▲──────────────────┘
 │ Worker Supervisor (durable)                │                                │
 │  SIGCHLD/exit code · heartbeat             │                                │  bytes-only, offline
 │  ownership-region lock                     │                                │  재도출 (raises_assurance=false)
 └───────┬──────────────────────┬─────────────┘                                │
         │ runner (runner_uid)   │ worktree                                    │
 ┌───────▼──────────┐   ┌────────▼─────────────┐                               │
 │ Adapter 계층      │   │ Worktree Manager     │        ┌──────────────────────┴─────────────┐
 │ shell/Codex/      │   │  auto worktree,      │        │  evidence bundle (파일):             │
 │ Claude/OpenCode   │   │  lane receipt, lock  │        │   capture-manifest.json              │
 │ → runner-receipt  │   └──────────────────────┘        │   observer_capture.json (sandbox 밖) │
 └───────┬──────────┘                                    │   isolation facts + isolation_hash   │
         │ 실행을 관측 (별도 uid, sandbox 밖)             │   runner/worktree lane receipt       │
 ┌───────▼──────────────────────────────────┐            │   trusted-observer-provenance (DSSE) │
 │ Observer 프로세스                          │            │   evidence-substrate-bundle+OTel     │
 │  command_receipts · diff · test_output    │─── 방출 ──▶│   evidence-contract.json + git-diff  │
 │  isolation facts probe                    │            │   prev_capture chain / team-ledger   │
 │  observed_by = 'depone-observer'          │            └──────────────────────────────────────┘
 └───────┬──────────────────────────────────┘                    ▲              공개키 out-of-band
         │                                                        │              (evidence 밖)
 ┌───────▼──────────────────────────────────┐                    │
 │ Evidence Emitter  (유일한 SoT 쓰기 지점)   │────────────────────┘  DSSE 서명 (operator Ed25519 private key)
 │  append-only signed event log             │
 └───────────────────────────────────────────┘
```

데이터 흐름 규칙: worker/adapter는 코드를 쓰지만 SoT에 **직접 쓰지 못한다**. 모든 상태전이는 Observer가 관측 → Emitter가 로그에 append + 서명 → Depone이 소비하는 단방향이다. Depone→witnessd 역방향 채널은 공개키 전달 하나뿐이며, verdict는 witnessd 상태를 변경하지 않는다.

## 2.4 런타임 내부 컴포넌트

각 컴포넌트는 [책임 / 인터페이스 / 의존 / 안 하는 것 / 검증]으로 규정한다. "검증"은 에이전트가 구현 완료를 반증 가능하게 증명하는 테스트·아티팩트·명령이다.

### 2.4.1 Orchestrator · Planner

- **책임.** 사용자 목표를 lane packet 집합으로 분해하고, 각 lane에 ownership-region(파일/모듈 claim 집합), 예산(토큰·달러·depth), stop rule을 부여한다. 각 lane packet은 §4의 fixture/invocation과 `allowed_touched_files`(= capture-manifest의 `allowed_touched_files`가 될 값)를 확정한다.
- **인터페이스.** `plan(goal) -> list[LanePacket]`, `dispatch(lane_packet) -> DispatchEvent(idempotency_key)`. dispatch는 로그에 append되는 이벤트이며 side-effect 없는 순수 계획 산출과 분리한다.
- **의존.** Scheduler(디스패치 대상), Session Store(재개 시 계획 복원), event log(dispatch 이벤트 기록).
- **안 하는 것.** 프로세스 spawn·git 조작·서명. 성공/완료 판정도 하지 않는다(그것은 Observer+Depone의 몫).
- **검증.** 동일 goal·동일 seed로 `plan()`을 두 번 호출하면 canonical hash가 동일한 lane packet 리스트가 나오는 결정성 테스트. `allowed_touched_files`가 lane별로 disjoint(또는 overlap 시 team-ledger merge receipt 필수 경로로 라우팅)임을 assert.

### 2.4.2 Scheduler (restart-safe, no tmux)

- **책임.** 준비된 lane을 동시성 예산 내에서 Worker Supervisor에 넘기고, 프로세스 재시작/reboot 후에도 로그 projection에서 "무엇이 아직 미완인가"를 재계산해 이어서 디스패치한다.
- **인터페이스.** `schedule(dispatch_event)`, `reconcile() -> list[LanePacket]`(로그 projection 기준 미완 lane 재도출). 동시성은 per-task concurrency key로 제한.
- **의존.** event log(projection), Worker Supervisor.
- **안 하는 것.** **tmux/pane/send-keys를 쓰지 않는다.** 상태를 pane·터미널 세션에 바인딩하지 않으며, IPC를 터미널 키 입력으로 흉내내지 않는다. in-memory 큐를 SoT로 삼지 않는다(SoT는 로그).
- **검증.** Scheduler 프로세스를 SIGKILL 후 재기동했을 때 `reconcile()`이 완료 lane을 재디스패치하지 않고 미완 lane만 재개하는 crash-injection 테스트. `grep -R "tmux\|send-keys" src/`가 비어야 한다(no-tmux 하드 규칙).

### 2.4.3 Worker Supervisor (durable)

- **책임.** worker를 durable 자식 프로세스/컨테이너로 spawn하고 감시한다. exit code와 `SIGCHLD`를 받아 종료를 확정하고, worker가 bounded interval로 방출하는 서명된 heartbeat를 로그에 중계하며, 각 worker의 ownership-region lock을 claim/release한다.
- **인터페이스.** `spawn(lane_packet) -> WorkerHandle(pid, runner_uid)`, `on_exit(pid, exit_code)`, `heartbeat(worker_id, ts)`, `claim_region(region)/release_region(region)`. exit code는 그대로 runner-receipt의 `exit_code`(int)로 흘러간다(§4 `validate_runner_receipt`가 int 강제).
- **의존.** Adapter 계층(실제 실행 substrate), Observer(관측 대상 프로세스), Evidence Emitter(heartbeat·lock 이벤트 append), OS uid 모델(2.4.7).
- **안 하는 것.** 서명·verdict 산출을 하지 않는다. 3.5초 타임아웃 후 send-keys fallback 같은 IPC 우회를 하지 않는다. worker의 자기보고 텍스트를 완료 신호로 해석하지 않는다(exit code + Observer capture만 신뢰).
- **검증.** worker를 강제 kill했을 때 supervisor가 `SIGCHLD`/exit code로 종료를 감지해 로그에 exit 이벤트를 append하고, heartbeat가 끊긴 뒤 projection상 `active`가 false로 뒤집히는 테스트. 두 worker가 겹치는 region을 claim하려 하면 두 번째가 block되는 lock 테스트.

### 2.4.4 Session Store (crash-safe, ID resume)

- **책임.** 각 세션의 last prompt, tool-call cursor, worktree 경로, ownership 상태를 crash-safe하게 영속화하여 다른 host/프로세스/reboot에서 **세션 ID로 재개**할 수 있게 한다.
- **인터페이스.** `save(session_id, state)`(atomic write: temp+rename, fsync), `resume(session_id) -> SessionState`. state는 로그 offset을 포함해 "로그의 어디까지 반영됐나"를 재계산 가능하게 한다.
- **의존.** event log(재개 시 SoT는 로그, Session Store는 인덱스/캐시), 파일시스템 원자적 rename.
- **안 하는 것.** 세션을 tmux pane·PID에 바인딩하지 않는다. 부분 기록(torn write)을 남기지 않는다(atomic rename만). Session Store를 SoT로 착각하지 않는다 — 불일치 시 로그가 우선.
- **검증.** `save()` 도중 프로세스를 kill해도 이전 유효 상태가 온전히 남고(반쪽 파일 없음), `resume(id)`가 로그 offset부터 정확히 이어지는 crash-safe 테스트.

### 2.4.5 Adapter 계층 (shell / Codex / Claude Code / OpenCode)

- **책임.** 이질적 실행 substrate를 흡수해 **동일한 runner-receipt 스키마**를 방출한다. Depone이 어댑터에 무관하게 검증할 수 있게 하는 것이 핵심.
- **공통 출력 = runner-receipt.** kind `agent-fabric-runner-receipt`, schema `1.0`. 필드: `runner_kind`, `arm`, `task_id`, `worktree`, `invocation`(비어있지 않은 문자열 리스트), `transcript_path`, `exit_code`(int), `touched_files`, `started_at`/`ended_at`, `human_intervened`(bool), `source_hashes.receipt = canonical_hash(receipt-without-source_hashes)`(§4.6과 동일 — `source_hashes`를 넣기 **전** receipt를 해시). Depone `validate_runner_receipt`가 이를 강제한다.

> **Decision (재검토 가능): 첫 어댑터 순서는 shell(W1) → Codex(W4) → Claude Code/OpenCode.**
> Rationale: shell 어댑터는 substrate 의존이 없어 W1에서 A1/A2 계약을 가장 빨리 end-to-end로 닫는다. Codex는 OMX/LazyCodex 생태계를 흡수하는 wedge이지만, 그들과 **동시 실행 시 상태가 조용히 오염**되므로(설계 리포트 실측) 어댑터는 자신의 세션/상태 디렉터리를 격리하고, 공유 상태 파일에 쓰지 않으며, 어떤 상태를 읽었는지 로그에 기록해야 한다. Claude Code/OpenCode는 그 뒤.

- **주의(계약 갭).** Depone의 현행 `validate_runner_receipt`는 `runner_kind ∈ {codex-cli, manual}`, `arm ∈ {direct, governed}`로 제한한다. shell/claude/opencode 어댑터를 1급 runner_kind로 검증하려면 이 enum 확장이 **Depone 측 스키마 변경**으로 선행돼야 한다. W1의 shell 어댑터는 그때까지 `runner_kind="manual"`로 방출하거나, enum 확장 PR을 Depone에 먼저 넣는다(둘 중 후자를 권장 — 어댑터 종류가 곧 provenance이므로). 이 결정은 §4 스키마 절과 동기화한다.
- **의존.** Worker Supervisor(프로세스 수명), 각 substrate CLI(예: `resolve_codex_command`류 stale-shim-안전 해석), Observer(receipt와 짝을 이루는 관측).
- **안 하는 것.** transcript의 `<promise>VERIFIED</promise>` 같은 self-report 태그를 완료로 파싱하지 않는다. 서명하지 않는다. 자기 touched_files를 스스로 승인하지 않는다(Observer가 독립 관측).
- **검증.** 4개 어댑터 각각이 방출한 runner-receipt를 동일한 `validate_runner_receipt`에 넣어 `[]`가 나오는 어댑터-무관 테스트. Codex 어댑터를 OMX/LazyCodex와 동시 기동해도 서로의 상태 디렉터리를 건드리지 않음을 확인하는 격리 테스트.

### 2.4.6 Worktree Manager

- **책임.** lane별 auto worktree를 생성/정리하고, fan-in 시점에 read-only git 상태로 worktree lane receipt를 산출하며, ownership-region lock과 연동해 lane 간 파일 충돌을 방지한다.
- **인터페이스.** `create_lane_worktree(lane) -> path`, `build_lane_receipt(worktree, base_commit, evidence_dir)` — Depone `worktree_receipt.build_worktree_lane_receipt`와 동형(kind `depone-worktree-lane-receipt`, schema `0.1`). 산출물은 `base_commit`/`head_commit`/`branch`/`changed_files`/`dirty`/`dirty_files`/`command_receipts`/`boundary`.
- **의존.** git(read-only 조회만), event log(worktree 이벤트), Team Ledger fan-in(2.5).
- **안 하는 것.** dirty worktree를 경고만 하고 launch하지 않는다 — fan-in 대상 lane은 `dirty=false`여야 한다(Depone `_validate_worktree_receipt`가 `dirty=True`를 `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_DIRTY`로 refute). receipt 생성 시 worktree를 mutate하지 않는다(`boundary.git_read_only=true`, `mutates_worktree=false`).
- **검증.** 한 커밋을 만든 worktree에서 receipt를 만들어 `changed_files`가 실제 변경과 일치하고 `dirty=false`인지, 그리고 그 receipt를 Team Ledger에 넣었을 때 §4.7의 정본 규칙 — **overlap이 있어 merge receipt가 required인 lane은 `changed_files == touched_files`(equality), 그 외 lane은 `changed_files ⊇ touched_files`(superset)** — 을 만족하는지 확인(누락은 항상 위반, 초과는 non-overlap lane에서만 허용).

### 2.4.7 Observer 프로세스 (별도 uid, runner 밖)

- **책임.** runner 샌드박스 **밖**에서 별도 프로세스로 실행을 관측해 observer capture를 산출한다: `command_receipts`(각 `command`+int `exit_code`), `diff_summary.changed_files`, `touched_files`, `test_output.status`(`not-run`/`passed`/`failed`/`error`), 그리고 isolation facts를 probe한다. `observed_by`는 반드시 `depone-observer`.
- **인터페이스.** `enforce_observer_separation(runner_sandbox, out_path, log_path)` — out/log의 부모가 runner sandbox 안이면 `ERR_OBSERVER_NOT_SEPARATED`로 fail-closed(Depone `observe.enforce_observer_separation`). `probe_isolation_facts(observer_dir, runner_uid=…)` → uid 모델 facts. 출력 디렉터리는 `0700`, runner-not-writable.

> **Decision (재검토 가능): A2 격리는 uid 모델을 1급으로, docker 모델은 후속 옵션.**
> Rationale: uid 모델(`ISOLATION_MODEL = "uid-boundary-unwritable-observer-dir"`)은 `runner_uid != observer_uid`, 둘 다 정수, `runner_uid != 0`, `observer_dir_writable_by_runner == False`를 요구하며 Depone `verify_isolation_boundary`가 facts에서 boundary를 재도출한다(플래그 위조로 A2 상승 불가). 이는 가볍고 잠긴 기업 머신에서 Docker-in-Docker 마찰이 없다. container 모델(`CONTAINER_ISOLATION_MODEL`)은 더 강하지만 Docker 의존 마찰을 상속하므로 후속. 따라서 witnessd는 `runner_uid`(runner-not-observer)와 `observer_uid`(observer-launched)를 서로 다른 uid로 구성하고, observer_dir을 observer 소유·`0700`으로 만든다. observer-launched 강화가 필요하면 `UID_OBSERVER_LAUNCHED_ISOLATION_MODEL`을 쓰되 `observer_launched=true` 영수증을 함께 방출한다.

- **의존.** OS uid/권한, Worker Supervisor(runner_uid 출처는 runner receipt), Evidence Emitter(capture append).
- **안 하는 것.** runner 샌드박스 안에 쓰지 않는다. post-hoc 로그 스크래핑으로 관측을 대체하지 않는다(캡처 시점 분리). `boundary.privilege_boundary`를 facts 없이 주장하지 않는다(same-uid면 A1에 머문다).
- **검증.** observer 출력을 runner sandbox 안 경로로 지정하면 `ERR_OBSERVER_NOT_SEPARATED`가 나고 **아무 파일도 쓰이지 않음**을 확인. 서로 다른 uid + `observer_dir_writable_by_runner=False`로 구성한 실제 host에서 `verify_isolation_boundary(facts).boundary is True`이고, same-uid로 구성하면 A1로 캡됨을 확인.

### 2.4.8 Evidence Emitter (유일한 SoT 쓰기 지점)

- **책임.** **단 하나의 SoT 쓰기 지점.** Observer capture + runner receipt + isolation facts를 묶어 capture-manifest를 만들고, `prev_capture_hash`로 직전 항목에 체인 링크하고, operator Ed25519 private key로 DSSE 서명한 뒤 append-only 서명 event log에 append한다. evidence bundle(in-toto Statement v1 + DSSE + OTel GenAI spans), trusted-observer-provenance, evidence-contract.json, team-ledger fan-in도 여기서 방출한다.
- **인터페이스.** `emit_capture(observer_capture, fixture, allowed_touched_files, prev_capture_hash, isolation) -> manifest`(Depone `build_capture_manifest`와 동형), `sign(bundle) -> dsse_envelope`(§3, `sign_dsse_envelope`/`sign_evidence_bundle`), `append(event)`(hash-chained). 서명 상태는 `SIGNING_STATUS_OPERATOR_KEY = "signed-ed25519-operator-key"`.

> **Decision (재검토 가능): 서명은 operator-held Ed25519 DSSE. Sigstore Fulcio keyless + Rekor는 deferred.**
> Rationale: sign은 런타임에서, verify는 Depone이 공개키로. private key는 verify 경로에 절대 없다(§3, `verify_signed_bundle`은 `public_key_path`만 받음). Depone `operator_key_signature_boundary`는 `keyless_identity=false, transparency_logged=false`를 명시한다. witnessd는 서명 단계를 swappable로 두되 **keyless 시맨틱을 주장하지 않는다** — keyless 서명 축(Fulcio/Rekor)은 명시적으로 후속. openssl 부재 시 `ERR_OPENSSL_UNAVAILABLE`로 fail-closed.

> **Decision (재검토 가능): 완료 UX는 "evidence-pending"을 하드 규칙으로.**
> Rationale: Emitter가 방출을 끝내도 witnessd는 `VERIFIED`/`DONE`/`COMPLETE`/`ORCHESTRATION COMPLETE` 같은 성공 문자열을 SoT로 삼지 않는다. lane의 표시 상태는 Depone 외부 검증이 통과하기 전까지 **evidence-pending**이며, 이후에도 witnessd가 아니라 Depone verdict가 등급을 말한다. 이 규율은 우리가 조롱한 self-report theater 실패모드의 재발을 막는 하드 규칙이다(테스트로 강제: 아래).

- **의존.** Observer(capture 입력), Adapter(runner receipt), operator private key(§3), event log 파일.
- **안 하는 것.** worker가 자기 성공을 seal하도록 허용하지 않는다(Emitter만 서명, worker 프로세스는 서명키 접근 불가 — uid 경계로 강제). assurance를 스스로 상향하지 않는다(bundle `boundary.raises_assurance=false`, `approves_public_claim=false` 고정). 미지 fact/hash mismatch/범위 밖 touched/서명 부재/chain 단절에 부분점수를 주지 않는다(fail-closed → A0/blocked/refuted).
- **검증.**
  (1) 서명된 bundle을 공개키로 `verify_signed_bundle`→`ingest_signed_evidence_bundle`에 넣어 `signature_verified=True`, subject digest 전부 `verified`; private key를 verify 인자에 넘길 API가 존재하지 않음을 타입/시그니처로 확인.
  (2) capture-manifest의 `touched_files ⊄ allowed_touched_files`면 Depone이 `unexpected touched files`로 refute.
  (3) 체인에서 중간 항목을 drop/reorder/tamper하면 `verify_capture_chain`이 `blocked`.
  (4) UX 규칙 테스트: 방출 직후 lane 표시 상태가 `evidence-pending`이고, 코드베이스에 `VERIFIED`/`ORCHESTRATION COMPLETE` 류 성공 문자열이 SoT 판정 경로에 등장하지 않음을 grep-gate로 강제.

## 2.5 팀 fan-in과 컴포넌트↔계약 매핑

여러 lane을 합칠 때 Evidence Emitter는 `depone-team-ledger`(schema `0.1`)를 방출한다. lane별 `touched_files`가 겹치면(overlap) Depone `build_team_ledger_verdict`는 passing `merge_receipt`를 **필수**로 요구하고, 없으면 `blocked`. ledger의 `boundary`는 `raises_assurance=false, approves_merge=false`로 고정된다(검증기가 병합을 승인하지 않는다). 각 passed lane은 2.4.6의 worktree lane receipt(`dirty=false`, `changed_files`는 §4.7 규칙 — overlap+merge-required lane은 `touched_files`와 equality, 그 외는 superset)와 `evidence_next_verdict`(Depone `team_ledger.py`가 실제로 요구하는 필드, 스키마는 §4.12) 파일 경로를 첨부해야 passed로 잡힌다. read-only lane(파일 무변경) 처리는 §4.12를 따른다.

아래 표는 각 컴포넌트가 만족시켜야 하는 Depone 계약과 그 검증 지점을 고정한다(구현 완료의 반증 기준).

| 컴포넌트 | 방출 아티팩트 (kind / schema) | Depone 검증 (함수) | fail-closed 트리거 (error/verdict) |
|---|---|---|---|
| Adapter 계층 | `agent-fabric-runner-receipt` / 1.0 | `validate_runner_receipt` | exit_code 비-int, invocation 빈 리스트, runner_kind/arm enum 위반 |
| Observer | observer capture 블록 (in capture-manifest) | `_check_observer_capture_shape`, `enforce_observer_separation` | `observed_by≠depone-observer`, command_receipts 빈 리스트, sandbox 안 출력 → `ERR_OBSERVER_NOT_SEPARATED` |
| Observer (isolation) | isolation facts + `isolation_hash` | `verify_isolation_boundary` | same-uid/runner_uid=0/writable dir/미지 fact → boundary False (A1 cap) |
| Evidence Emitter | `agent-fabric-capture-manifest` / 1.0 | `validate_capture_manifest` | `source_fixture_hash`/`observer_capture_hash` mismatch, `touched_files ⊄ allowed_touched_files` |
| Evidence Emitter (chain) | `prev_capture_hash` 링크 | `verify_capture_chain` | non-genesis head, drop/reorder/tamper → `blocked` |
| Evidence Emitter (sign) | `trusted-observer-provenance` / 1.0, `depone-evidence-substrate-bundle` / 1.0 | `validate_trusted_observer_provenance`, `verify_signed_bundle`, `ingest_signed_evidence_bundle` | 서명 부재/불일치 → `ERR_TRUSTED_PROVENANCE_*`; openssl 부재 → `ERR_OPENSSL_UNAVAILABLE` |
| Worktree Manager | `depone-worktree-lane-receipt` / 0.1 | `_validate_worktree_receipt` | `dirty=true` → `..._DIRTY`; `changed_files ⊉ touched_files`(누락) → `..._TOUCHED_FILES_MISMATCH`; overlap+merge-required lane에서 `changed_files ≠ touched_files`(초과 under-report) → `..._TOUCHED_FILES_UNDERREPORTED` (§4.7) |
| Orchestrator + fan-in | `depone-team-ledger` / 0.1 | `build_team_ledger_verdict` | overlap touched + merge_receipt 부재 → `blocked` |
| (전 lane) | `evidence-contract.json` / `v105.verify_wedge` + `git-diff-name-only.txt`/`git-diff.patch`/`exit-code.txt` | `validate_evidence_contract` | enforcement directive 0개 → `ERR_EVIDENCE_CONTRACT_INVALID`; 금지 파일 touch → `ERR_FORBIDDEN_FILE_TOUCHED`; 테스트 약화 → `ERR_TEST_WEAKENED` |

이 표의 모든 행은 witnessd CI가 "방출 → 대응 Depone 함수 통과(무오류) 또는 기대 fail-closed 코드 발생"으로 재현 가능해야 한다. 어느 한 행이라도 witnessd가 부분점수를 만들어내면 그것은 아키텍처 위반이다 — 이 런타임의 유일한 방어 가능한 해자는 evidence-native이고, 그 해자는 위 fail-closed 경계 전부가 활선(live)일 때만 성립한다.

---

## 3. 신뢰 · 보안 모델

이 절은 witnessd 런타임이 방출하는 모든 증거의 신뢰 근거를 정의한다. 핵심 명제는 하나다: **witnessd는 자유롭게 실행(spawn/retry/worktree/schedule)하되 자신의 성공을 스스로 판정하지 않는다.** "done"의 최종 권한은 witnessd 밖 — private key를 절대 갖지 않는 별도 검증기 Depone(패키지·CLI `depone`, repo `keelplane`, §1.9) — 에 있고, Depone은 오프라인·non-executing으로 서명된 바이트에서만 A0/A1/A2(assurance)와 그 위의 signing_status를 재도출한다. 아래 모든 요건은 Depone의 실제 계약 코드(`capture_bridge.py`, `observe.py`, `isolation.py`, `sign.py`, `observer_provenance.py`, `evidence_substrate.py`, `verify/evidence_contract.py`)가 이미 강제하는 규칙과 1:1로 정렬한다. 새 규칙을 발명하지 않는다 — witnessd의 유일한 의무는 그 계약을 만족하는 아티팩트를 native로 방출하는 것이다.

### 3.0 공유 불변식 (Depone 계승, 협상 불가)

두 repo가 공유하는 유일한 계약은 canonical hashing 규약과 스키마다. 이는 witnessd 방출기와 Depone 검증기 양쪽에서 **바이트 단위로 동일**해야 한다.

- **canonical_hash 규약**: 모든 hash는 `hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()`. Depone의 `capture_bridge._sha256_json`, `claim_gate.canonical_hash`, `seal._canonical_bytes`가 모두 이 바이트열에 합의한다. witnessd의 emitter는 다른 직렬화(pretty-print, 다른 separator, 비정렬 키)로 hash를 계산해서는 안 된다. 검증 명령: emitter가 계산한 `observer_capture_hash`/`source_fixture_hash`/`isolation_hash`/`prev_capture_hash`가 Depone 재계산과 정확히 일치해야 하며, 불일치 시 `observer_capture_hash mismatch`·`source_fixture_hash mismatch`·`isolation_hash mismatch`로 fail-closed된다.
- **worker는 자기 성공을 seal·validate 불가.** 코드를 쓰는 주체와 그 결과를 관측·서명하는 주체는 분리된다(§3.2, §3.3).
- **verifier는 assurance를 상향 불가.** Depone은 어떤 경로로도 A0→A1→A2 upgrade를 하지 않으며(assurance 상한 A2, A3 등급 없음), `raises_assurance=false`를 반환한다(`evidence_substrate._blocked_verdict`, `boundary.raises_assurance=False`).
- **부분점수 없음.** 미지 fact / hash mismatch / 범위 밖 touched file / 서명 부재 / chain 단절은 전부 A0 또는 `blocked`/`refuted`로 떨어진다. "거의 통과"는 통과가 아니다.

### 3.1 Assurance 사다리 (A0 / A1 / A2) + report-level 서명 축

> **Decision (재검토 가능):** witnessd는 Depone의 **3단 assurance 사다리(A0/A1/A2)**를 native 방출 대상 등급으로 채택한다. A0/A1/A2는 `capture_bridge.py`의 상수(`ASSURANCE_A0`/`ASSURANCE_A1`/`ASSURANCE_A2`)와 정확히 일치하며, **assurance 정수 상한은 A2다 — A3라는 assurance 등급은 Depone에 존재하지 않는다**(`validate_capture_manifest`는 `"A0-claims-only"`/`"A1-local-observed"`/`"A2-isolated-observed"` 외 값을 거부하고, `evidence_substrate`의 번들 assurance는 `capture_manifest.get("assurance")`를 그대로 복사하므로 서명해도 A2가 상한이다). operator-held Ed25519 DSSE 서명은 **등급을 올리지 않는 별도의 report-level 신뢰 축**이다 — A1/A2 manifest 위에 얹히는 `trusted-observer-provenance` 레코드 + `signing_status`(`"signed-ed25519-operator-key"`)이며, `boundary.raises_assurance=false`로 assurance 정수를 A2 위로 올리지 않는다. **Rationale:** 등급을 새로 만들면 Depone과 계약이 어긋나 독립 검증기의 신뢰 근거가 무너진다. Depone `sign.py` self-test는 `"A3-keyless-signed"`를 오히려 **위조가 반드시 verify 실패해야 함을 증명하는 음성 문자열**로만 쓴다. 각 등급/축은 "무엇이 그것을 부여하는가"와 "무엇이 그것을 차단하는가"가 코드로 판정 가능해야 한다. (Sigstore Fulcio keyless + Rekor는 §3.10에서 deferred.)

각 등급의 정확한 조건:

**A0 — `A0-claims-only` (decision: `claims-only`)**
- 부여: 관측자 캡처가 전혀 없는 자기보고(self-report)만 존재. `build_capture_manifest`에 `observer_capture=None`을 넘긴 경우.
- 상태: `observer_capture`와 `observer_capture_hash`가 모두 `None`이어야 유효(`_check_a0_manifest`). 하나라도 값이 있으면 `A0 manifest must not include observer_capture`로 거부.
- 의미: **"VERIFIED"/"DONE"/"COMPLETE" 텍스트는 전부 여기서 멈춘다.** OMO의 `<promise>VERIFIED</promise>`, OMX `doctor`의 "18 passed", Factory의 "검증 phase 없이 배송"은 구조적으로 A0을 넘지 못한다. witnessd UX는 A0 산출물을 "evidence-pending"으로만 표기하며(§3.11) 절대 성공 문자열을 SoT로 쓰지 않는다.

**A1 — `A1-local-observed` (decision: `observed-local-capture`)**
- 부여: 별도 observer 프로세스(§3.3)가 runner 샌드박스 **밖** 디렉터리에 기록한 `observer_capture`가 존재하고, 그것이 hash-bound되어 아래 shape 검사를 전부 통과.
- 필수 observer 필드(`REQUIRED_OBSERVER_FIELDS`): `observed_by`, `source_fixture_hash`, `diff_summary`, `touched_files`, `test_output`, `command_receipts`. 하나라도 없으면 `observer_capture missing required field: <name>`.
- `observed_by`는 정확히 `"depone-observer"`(상수 `OBSERVER_ID`). 아니면 `observer_capture.observed_by must be 'depone-observer'`.
- `command_receipts`는 **비어 있지 않은** 객체 리스트여야 하고, 각 원소는 `command` 키와 `int`형 `exit_code`를 가져야 한다. 빈 리스트는 `command_receipts must be non-empty for A1`.
- `test_output.status`는 `{not-run, passed, failed, error}`(`VALID_TEST_STATUSES`) 중 하나.
- `touched_files`와 `diff_summary.changed_files`는 모두 `allowed_touched_files`의 부분집합이어야 한다(범위 밖 쓰기 = refute). 초과 시 `unexpected touched files: [...]` / `unexpected diff files: [...]`.
- `observer_capture_hash == sha256(observer_capture)`이고, `observer_capture.source_fixture_hash == manifest.source_fixture_hash == sha256(fixture)`.
- 차단: 위 중 하나라도 실패, 또는 hash tamper, 또는 stale source_fixture_hash. 전부 fail-closed로 A0/`blocked`.
- **cap 이유(중요):** A1은 observer-owned이지만 same-uid다 — runner가 원리상 observer 출력을 덮어쓸 수 있으므로 A1이 상한이다(`isolation.py` 모듈 docstring). uid 경계가 증명되기 전엔 절대 A2로 올라가지 않는다.

**A2 — `A2-isolated-observed` (decision: `isolated-observed`)**
- 부여: A1의 모든 조건 + `isolation` facts가 **실제 privilege boundary**를 성립시킴(`verify_isolation_boundary(isolation)["boundary"] is True`). §3.4 참조.
- `isolation_hash == sha256(isolation)`이어야 하고, Depone은 저장된 flag를 믿지 않고 **facts에서 boundary를 재도출**한다(`_check_a2_manifest`). flag만 flip한 위조는 `A2 isolation does not establish a privilege boundary`로 거부.
- 차단: uid 미상 / runner_uid == observer_uid / runner_uid == 0 / observer_dir가 runner에 의해 writable / isolation_hash mismatch. 어느 하나라도 A1로 강등(upgrade 실패는 조용히 A1에 머무름, 위조 시도는 명시적 refute).

**Report-level 서명 축 — operator-held Ed25519 DSSE (assurance 등급 아님, 이번 스코프)**
- 성격: **assurance 정수 등급을 올리지 않는 별도 신뢰 축**이다. A1 또는 A2 manifest 위에 얹혀 서명자(trusted-observer/operator)의 provenance를 증명하고 `signing_status="signed-ed25519-operator-key"`를 남길 뿐, `boundary.raises_assurance=false`이므로 assurance는 여전히 A1/A2에 머문다.
- 부여: A1/A2 manifest에 대해 operator가 보유한 Ed25519 private key로 서명한 `trusted-observer-provenance`(DSSE) 레코드가 존재하고, Depone이 **out-of-band public key**로 서명을 검증(`observer_provenance.validate_trusted_observer_provenance`, `sign.verify_signed_bundle`). report-level 신뢰의 트러스트 루트.
- 이유: content-addressed manifest도 evidence 디렉터리 안에 있으면 여전히 runner-controlled input이다. report-level 신뢰는 observer/operator가 만든 별도 서명 레코드를 요구한다(`observer_provenance.py` docstring).
- 차단: 서명 부재, public key 부재, openssl 부재(`ERR_OPENSSL_UNAVAILABLE`), 서명 불일치(`trusted observer provenance signature verification failed`), binding 불일치(`signed binding mismatch`). §3.5. (서명이 없어도 assurance는 A1/A2로 유지되며, 다만 signing_status가 unsigned일 뿐이다 — 서명 실패가 곧 assurance 강등은 아니되, 서명을 **요구**하는 report-level 경로에서는 `blocked`.)
- **주의:** 이 서명은 public-key verifiable이지만 keyless identity도, transparency-logged도 아니다(`operator_key_signature_boundary`: `keyless_identity=False`, `transparency_logged=False`). "keyless" 시맨틱을 주장하면 안 되고, top-level `assurance`를 `"A3-*"`로 올리는 위조는 `verify_signed_bundle`이 `False`로 잡는다(§3.5).

**Fulcio keyless + Rekor (deferred, §3.10) — 이 역시 assurance 등급이 아니라 서명 축의 후속 진화다.**

### 3.2 역할 분리 (worker ≠ observer ≠ verifier)

| 역할 | 할 수 있는 것 | 절대 못 하는 것 |
|------|--------------|----------------|
| **worker / runner** (adapter가 구동) | 코드 편집, 명령 실행, worktree 안 커밋 | 자기 결과를 seal/validate, 자기 assurance 부여, observer 출력 dir에 쓰기 |
| **observer** (별도 프로세스·별도 uid) | command_receipts·diff·test_output·isolation facts 캡처, `observed_by="depone-observer"` 서명 | assurance 상향, verdict 발행 |
| **operator** | Ed25519 private key 보유·서명(report-level 서명 축, §3.1) | private key를 verify 경로/evidence 번들에 넣기, assurance 등급을 서명으로 올리기 |
| **verifier** (Depone) | 바이트에서 A0/A1/A2(assurance) + signing_status 재도출, `blocked`/`refuted` | launch/run/retry, assurance 상향(`raises_assurance` 항상 `false`) |

이 표는 협상 불가다. witnessd의 Evidence Emitter가 유일한 SoT 쓰기 지점이며, worker는 코드를 쓰지만 emitter 경로를 통해서만 증거가 봉인된다.

### 3.3 Observer 분리 요건

> **Decision (재검토 가능):** observer는 반드시 **별도 프로세스**로 실행되고, 그 출력(`observer_capture`, verify-log)은 반드시 **runner 샌드박스 밖** 디렉터리에 기록된다. witnessd는 방출 전에 Depone의 `observe.enforce_observer_separation`를 그대로 호출해 fail-closed를 게이트한다. **Rationale:** OMX/OMO의 모든 증거가 self-report였고, 툴 자신의 `doctor`가 유일한 관측자였으며 틀렸다. 캡처 시점에 관측자를 분리하지 않으면(= post-hoc 로그 스크래핑) A1 floor 자체가 성립하지 않는다.

구현 요건(각각 검증 가능):

1. **별도 프로세스.** observer는 runner와 다른 PID로 실행되고, `observer_independence.observer_pid = os.getpid()`(observer의 pid)를 기록한다(`enforce_observer_separation` 반환). runner 프로세스가 자기 자신을 관측하는 경로는 금지.
2. **출력 위치 강제.** emitter는 `--out`(observer_capture)과 `--log`(verify-log)를 방출하기 전에 `enforce_observer_separation(runner_sandbox=..., out_path=..., log_path=...)`를 호출한다. 다음 중 하나라도 참이면 `PairedRunError("ERR_OBSERVER_NOT_SEPARATED")`로 **쓰기 전에** 중단하고 파일을 남기지 않는다:
   - `--out`의 부모(observer_dir)가 runner_sandbox 안/동일
   - `--out` 자체가 runner_sandbox 안
   - `--log` 또는 그 부모가 runner_sandbox 안
   - observer_dir가 runner_sandbox와 동일 경로
   경로 비교는 `os.path.abspath` + `normcase` + `commonpath` 기반이므로 심볼릭 링크/상대경로 우회를 막는다. 임의의 추가 observer-owned 경로는 `enforce_path_outside_runner_sandbox(runner_sandbox=, path=, label=)`로 개별 게이트한다.
3. **independence 레코드.** 성공 시 observer_capture에 `observer_independence` 블록을 붙인다: `model="separate-process-observer-owned-dir"`, `out_is_outside_sandbox=True`, **그리고 정직하게** `privilege_boundary=False`, `tamper_resistant_same_uid=False`. 즉 순수 프로세스·디렉터리 분리만으로는 A1이 상한이며, A2는 §3.4의 uid/container 경계를 별도로 요구한다. witnessd는 이 두 필드를 A1 단계에서 `True`로 주장해서는 안 된다(`observe._self_test`가 `privilege_boundary is not False`를 AssertionError로 잡는다).
4. **검증 명령.** `witnessd observe --self-test`는 (a) 샌드박스 안 출력이 `ERR_OBSERVER_NOT_SEPARATED`로 차단되고 파일이 안 써짐, (b) 샌드박스 밖 출력이 A1 manifest로 검증되고 `capture_hash == canonical_hash(capture)`, (c) A1 캡처가 `privilege_boundary=False`를 유지함을 반드시 통과해야 한다. 이는 `observe._self_test`를 어댑터-무관하게 재사용한다.

### 3.4 Isolation uid 모델 (A2 boundary)

> **Decision (재검토 가능):** A2의 1급 격리 모델은 **uid 경계**다 — `ISOLATION_MODEL = "uid-boundary-unwritable-observer-dir"`, 그리고 witnessd가 runner를 observer가 launch할 때는 `UID_OBSERVER_LAUNCHED_ISOLATION_MODEL = "uid-boundary-observer-launched-unwritable-observer-dir"`. Docker 모델(`CONTAINER_ISOLATION_MODEL`)은 검증기가 이미 지원하되 **후속 옵션**으로 둔다. **Rationale:** 컨테이너는 강하지만 OpenHands가 겪은 "잠긴 기업 머신에서 Docker-in-Docker 마찰"을 상속한다. uid 모델은 가볍고 non-root 환경에서 세팅 가능하며, 규제/air-gapped wedge에서 Docker 의존을 강제하지 않는다. Depone의 `verify_isolation_boundary`가 세 모델을 모두 판정하므로, 어댑터는 uid facts만 정확히 방출하면 된다.

uid 모델 성립 조건(전부 참이어야 `boundary=True`, `_verify_uid_isolation_boundary`):
- `runner_uid`와 `observer_uid`가 **둘 다 알려진 int**. 하나라도 미상(None/비-int)이면 `runner_uid and observer_uid must both be known integers` → fail-closed A1. (POSIX uid가 없는 플랫폼(WSL1 등)은 자동으로 A1로 떨어진다.)
- `runner_uid != observer_uid`. 같으면 `runner and observer share the same uid (no privilege boundary)`.
- **`runner_uid != 0` (root 금지).** root runner는 디렉터리 permission bit를 override할 수 있으므로 uid 경계를 성립시킬 수 없다: `root runner uid cannot establish a uid privilege boundary`. root가 필요하면 container 모델로 가야 하고, 거기서만 root가 컨테이너에 scoped된다.
- `observer_dir_writable_by_runner == False` (정확히 boolean False). observer가 자신의 출력 디렉터리를 stat하여 foreign owner 또는 group/other write bit가 있으면 `True`가 되고 boundary가 깨진다(`probe_isolation_facts`). 즉 **observer_dir는 0700이고 runner uid로 쓸 수 없어야** 한다.
- observer-launched 모델을 쓸 경우 추가로 `observer_launched == True`(`runner must be observer-launched`). runner를 observer가 spawn했다는 receipt가 없으면 A1.

boundary 재도출 규칙(위조 방지의 핵심):
- witnessd는 `isolation` 블록에 **원시 facts**(runner_uid, observer_uid, observer_dir_writable_by_runner, [model], [observer_launched])를 넣고 `isolation_hash = sha256(isolation)`을 방출한다. Depone은 저장된 `boundary` 플래그를 신뢰하지 않고 `verify_isolation_boundary(isolation)`로 **facts에서 boundary를 다시 계산**한다. 따라서 `boundary=True`나 uid를 손으로 위조해도 hash mismatch(`isolation_hash mismatch`) 또는 재도출 실패(`A2 isolation does not establish a privilege boundary`)로 무조건 걸린다.
- 미지 model 문자열은 fail-closed(`unknown isolation model`).

구현 요건: W2 어댑터는 spawn마다 `probe_isolation_facts(observer_dir, runner_uid=<from runner receipt>, model=..., observer_launched=...)`를 호출해 facts를 채우고, runner_uid는 runner receipt(§E5)에서 온다. 검증 명령: `witnessd isolation --self-test`는 (다른 uid + unwritable → boundary), (same uid → no boundary), (writable dir → no boundary), (missing facts → fail-closed), (root runner → no boundary), (observer-launched receipt 없음 → no boundary)를 전부 통과해야 하며 `isolation._self_test`를 재사용한다.

### 3.5 DSSE 서명 · operator key 관리 (operator 서명 축)

> **Decision (재검토 가능):** 서명은 **operator-held Ed25519 DSSE**다. scheme은 `"DSSE-Ed25519-openssl-cli"`, signing status는 `"signed-ed25519-operator-key"`(`sign.SIGNING_STATUS_OPERATOR_KEY`). **sign은 witnessd 런타임에서, verify는 Depone이 public key로.** private key는 verify 경로·evidence 번들에 절대 오지 않는다. **Rationale:** 런타임이 오염돼도 evidence 밖의 public key를 위조 못 하면 A1/A2 assurance나 그 위에 얹히는 서명된 report-level 신뢰를 만들 수 없다 — 이것이 두 repo 물리 분리의 신뢰 근거다.

**DSSE 방출 규약** (Depone `sign.py`와 바이트 호환):
- PAE는 DSSE v1: `b"DSSEv1 " + len(payloadType) + b" " + payloadType + b" " + len(payload) + b" " + payload`(`dsse_pae`). 벡터 `dsse_pae("x", b"abc") == b"DSSEv1 1 x 3 abc"`.
- `trusted-observer-provenance` 레코드는 binding `{kind:"trusted-observer-provenance-binding", schema_version:"1.0", evidence_path, manifest_hash: canonical_hash(manifest), observer_capture_hash}`를 canonical JSON으로 직렬화해 payload로 삼고, payloadType은 `"application/vnd.depone.trusted-observer-provenance.v1+json"`. witnessd는 `observer_provenance.build_signed_trusted_observer_provenance(manifest, evidence_path=, private_key_path=, key_id=)`를 그대로 쓴다.
- evidence-substrate 번들의 경우 `sign.sign_evidence_bundle(bundle, private_key_path, key_id=)`이 in-toto Statement를 DSSE로 서명하고 top-level에 `signing_status`·`signature_boundary`를 붙인다.
- 서명은 openssl CLI로 수행: `openssl pkeyutl -sign -inkey <priv> -rawin -in <pae> -out <sig>`. key 생성은 `openssl genpkey -algorithm Ed25519`. openssl이 PATH에 없으면 `ERR_OPENSSL_UNAVAILABLE`, 서명 실패는 `ERR_DSSE_SIGN_FAILED`.

**operator key 생명주기** (구현 요건):
- **생성:** `witnessd key gen`이 Ed25519 키쌍을 만든다(`openssl genpkey -algorithm Ed25519` → private PEM, `openssl pkey -pubout` → public PEM). private key는 서명 호스트에만, `0600` 권한으로.
- **보관:** private key는 서명하는 witnessd 호스트의 operator-owned 경로에만 존재. evidence 디렉터리·repo·번들 어디에도 들어가지 않는다. `key_id`는 비밀이 아닌 라벨이며 절대 key에서 파생하지 않는다(seal.py의 `key_id must not be the key` 규율 계승).
- **배포:** public key는 **out-of-band**로 Depone에 전달되어 `DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE`로 지정된다. 이 파일은 반드시 evidence 밖에 있어야 하며, evidence 안의 key로 검증하는 경로는 금지(runner-writable = 위조 가능). Depone은 서명 검증에 이 public key만 사용한다.
- **회전:** 새 키쌍 생성 → 새 public key를 out-of-band 배포 → 이후 방출은 새 key_id로 서명. 과거 evidence는 과거 public key로 계속 검증 가능(append-only, §3.6의 chain은 재서명 불필요). private key 유출 시 즉시 회전하고 유출 key_id로 서명된 이후 evidence를 신뢰 목록에서 제외.
- **swappable signing step:** signing 단계는 어댑터에서 교체 가능하게 두되(keyless 서명 축으로 가는 길), **keyless 시맨틱을 주장하지 않는다**. `operator_key_signature_boundary`가 `keyless_identity=False`, `transparency_logged=False`를 못박는다.

**verify_signed_bundle의 anti-upgrade 검사** (Depone이 강제, witnessd가 위조할 수 없는 이유): 서명은 in-toto statement만 덮으므로, top-level 필드가 서명된 내용과 어긋나면 검증 실패다. 구체적으로 `statement == 서명된 payload`, `bundle.assurance == predicate.assurance`, `bundle.boundary`의 각 키가 서명된 boundary와 일치, `signing_status == "signed-ed25519-operator-key"`, `signature_boundary == operator_key_signature_boundary()` 전부 확인. 따라서 유효한 operator-key 서명을 떼어내 top-level `assurance`를 `"A3-keyless-signed"`로 올리거나 `signature_boundary.keyless_identity=True`로 바꾸면 `verify_signed_bundle`이 `False`를 반환한다(`sign._self_test`가 두 시나리오를 명시 검증).

### 3.6 Fail-closed 규칙 전체 목록

아래 조건 중 **하나라도** 참이면 결과는 A0 또는 `blocked`/`refuted`다. 부분점수·silent downgrade·"거의 통과" 없음.

1. **미지/부분 isolation fact** → boundary False → A1 상한(A2 차단). uid 미상, root runner, same-uid, writable observer_dir, 미지 model. (`verify_isolation_boundary`)
2. **hash mismatch** — `source_fixture_hash mismatch`, `observer_capture_hash mismatch`, `isolation_hash mismatch` 중 어느 것이든 refute.
3. **stale source** — `observer_capture.source_fixture_hash != manifest.source_fixture_hash` → `source_fixture_hash is stale`.
4. **범위 밖 touched file** — `touched_files` 또는 `diff_summary.changed_files`가 `allowed_touched_files` 초과 → `unexpected touched files` / `unexpected diff files`.
5. **observer 분리 실패** — 출력이 runner_sandbox 안 → `ERR_OBSERVER_NOT_SEPARATED`, 파일 미기록.
6. **observed_by 위반** — `"depone-observer"`가 아니면 거부.
7. **빈/불량 command_receipts** — 빈 리스트, `command` 누락, 비-int `exit_code` → 거부.
8. **불량 test_output.status** — `{not-run,passed,failed,error}` 밖 → 거부.
9. **서명 부재/불량** — provenance 없음(`ERR_TRUSTED_PROVENANCE_MISSING`), 불일치(`ERR_TRUSTED_PROVENANCE_MISMATCH`), 서명 검증 실패(`ERR_TRUSTED_PROVENANCE_SIGNATURE_FAILED`), openssl 부재(`ERR_OPENSSL_UNAVAILABLE`), binding mismatch, public key 부재.
10. **번들에 검증 불가 서명** — evidence-substrate 번들의 DSSE envelope `signatures`가 `[]`가 아닌데 Depone이 독립 검증할 수 없으면 `blocked`("DSSE envelope contains unverifiable signatures"). 미서명 번들은 `signatures==[]`로 **정직하게** 표기해야 하며, 이 경우 `raises_assurance=False`로 A0/A1 유지.
11. **chain 단절** — `verify_capture_chain`: genesis head가 non-null이거나, 어떤 manifest의 `prev_capture_hash`가 직전 predecessor의 canonical hash와 불일치(drop/reorder/tamper), 또는 구조적으로 불량한 manifest → `blocked`. `prev_capture_hash`는 null이거나 64자 sha256 hex여야 한다.
12. **evidence-contract 위반** — `evidence-contract.json`이 스키마 `"v105.verify_wedge"`가 아니거나(`ERR_EVIDENCE_CONTRACT_INVALID`), 없거나(`ERR_EVIDENCE_CONTRACT_MISSING`), shadowed(`ERR_EVIDENCE_CONTRACT_SHADOWED`), enforcement directive가 0개, 필수 test evidence 누락(`ERR_REQUIRED_TEST_EVIDENCE_MISSING`), exit code 불일치(`ERR_TEST_EXIT_CODE_MISMATCH`), 금지 파일 편집(`ERR_FORBIDDEN_FILE_TOUCHED`), test 약화 탐지(`ERR_TEST_WEAKENED`).
13. **team ledger 위반** — overlap touched lane에 passing merge receipt 부재, dirty worktree fan-in 등(`team_ledger.build_team_ledger_verdict`). boundary는 `raises_assurance=false, approves_merge=false`.

fail-closed 원칙: witnessd 완료 UX는 이 중 어느 것도 통과하지 못하면 절대 성공 문자열을 SoT로 쓰지 않는다. Depone 재검증(`python3 -m depone ...` 오프라인) 전까지는 "evidence-pending Depone verification"만 표시한다.

### 3.7 위협 모델: 오염된 runner/worker가 할 수 있는 것 vs 없는 것

전제: worker/runner adapter는 임의 코드를 실행하므로 **오염 가능**하다고 가정한다. 신뢰는 runner의 선의가 아니라 구조적 분리에서 나온다.

**오염된 runner가 할 수 있는 것:**
- 자기 샌드박스 안의 임의 파일 편집·명령 실행.
- self-report 텍스트에 "VERIFIED"/"success"를 쓰기 — 그러나 이는 A0에서 멈춘다(§3.1).
- allowed 범위 안에서 코드를 바꾸기.

**오염된 runner가 할 수 없는 것 (왜 위조 불가):**
- **observer_capture 위조:** observer는 별도 프로세스·별도 uid이고 observer_dir는 runner uid로 not-writable(§3.3, §3.4). runner가 observer 출력을 덮어쓰면 same-uid가 되어 애초에 A2가 성립 안 하고, uid 경계가 있으면 write 자체가 permission으로 막힌다.
- **hash 우회:** manifest의 모든 subject(`fixture`, `observer_capture`, `isolation`)는 canonical hash로 bound되고 Depone이 재계산한다. 내용을 바꾸면 hash가 어긋나 refute(§3.6.2).
- **범위 확장:** allowed_touched_files 밖을 건드리면 `unexpected touched files`로 refute(§3.6.4).
- **A2 flag 위조:** `boundary=True`를 손으로 써도 Depone이 facts에서 재도출하므로 무효(§3.4).
- **서명 위조:** operator private key가 evidence 밖·verify 경로 밖이므로, runner는 유효한 DSSE 서명을 만들 수 없다. public key로 검증되는 서명만 report-level trusted-provenance(signing_status)를 부여하며(assurance 등급은 올리지 않음), top-level assurance upgrade 위조는 `verify_signed_bundle`이 잡는다(§3.5).
- **chain 조작:** prev_capture_hash가 predecessor에 bound되어 drop/reorder/tamper가 전 downstream 링크를 깨뜨린다(§3.6.11).
- **verdict 발행:** verdict는 Depone만 발행하고 witnessd는 실행만 한다. verifier는 assurance를 상향하지 않는다(`raises_assurance=false`).

결론: runner가 완전히 오염돼도 얻을 수 있는 최대치는 A0(자기보고)이고, A1 이상은 runner가 통제하지 못하는 별도 프로세스·uid·key가 있어야만 가능하다. 이것이 "provable-by-construction"의 실체다 — 더 공격적인 자율성(auto-retry/spawn/worktree)이 신뢰 리스크가 아닌 이유.

### 3.8 대칭키(HMAC) 잔재 주의 — Ed25519-only 권장

> **Decision (재검토 가능):** report-level 트러스트 루트에는 **Ed25519 asymmetric 서명만** 사용한다. Depone의 `seal.py`가 제공하는 HMAC-SHA256 대칭 seal(`ALG="HMAC-SHA256"`)은 witnessd의 신뢰 경계로 **채택하지 않는다**. **Rationale:** `seal.py`가 스스로 명시하듯 대칭 HMAC은 `symmetric=True, public_verifiable=False, forge_proof_same_uid=False` — key 보유자에게만 integrity를 주고, **same-uid runner가 key를 읽으면 위조 가능**하다. witnessd의 위협 모델은 runner 오염을 가정하므로, key를 evidence 근처에 두는 어떤 대칭 방식도 위조 벡터가 된다. `validate_trusted_observer_provenance`는 sealed(HMAC) 레코드와 signed(DSSE) 레코드를 둘 다 받지만, witnessd는 **오직 DSSE 경로**(`scheme="DSSE-Ed25519-openssl-cli"`)만 방출하고, HMAC seal은 evidence 번들에 넣지 않는다. sealed provenance 코드 경로는 Depone에 존재하되 witnessd에서는 사용 금지(dead path로 남기지 말고 아예 방출 안 함).

### 3.9 self-upgrade 금지

> **Decision (재검토 가능):** witnessd는 자기 자신을 auto-upgrade하지 않으며, verifier는 assurance를 auto-upgrade하지 않는다. **Rationale:** OMO installer churn(#95/#80/#84/#74/#92)과 orphan bin shim, unreadable config 덮어쓰기가 신뢰를 갉아먹은 실패모드다.

- **런타임 self-upgrade 금지:** witnessd 바이너리/skill은 실행 중 자기 자신을 조용히 교체하지 않는다. install/upgrade는 원자적(atomic)이고 명시적 명령으로만, unreadable config에는 fail-safe(덮어쓰기 금지, orphan shim 없음). "어느 skill 버전이 이 행위를 실행했나"는 append-only event log가 SoT다.
- **assurance self-upgrade 금지:** 어떤 등급도 재검증 없이 상위 등급으로 승격되지 않는다. Depone은 `raises_assurance=false`를 불변으로 반환하며, witnessd는 검증기 역할을 겸하지 않는다. A0→A1→A2(assurance 정수, 상한 A2)는 오직 §3.1의 조건을 새로 만족하는 **새 증거**로만 올라가고, 과거 판정을 소급 상향하지 않는다. operator DSSE 서명은 이 정수 축과 직교하는 report-level 신뢰 축이므로, 서명을 붙여도 A2가 A3로 바뀌는 일은 없다(그런 등급 자체가 없음).

### 3.10 keyless 서명 축 (Rekor/Fulcio) deferred 이유

> **Decision (재검토 가능):** Sigstore Fulcio keyless + Rekor transparency log(keyless 서명 축)는 **명시적으로 deferred**한다. 이번 스코프는 operator 서명 축(operator-held Ed25519 DSSE)까지다. **Rationale (3가지):**
> 1. **Depone이 이미 deferred로 선언.** `sign.py` 모듈 docstring이 "not keyless identity, not Fulcio-backed, not transparency-logged in Rekor. Sigstore keyless signing remains deferred"라고 못박고, `operator_key_signature_boundary`가 `keyless_identity=False`/`transparency_logged=False`를 강제한다. 두 repo가 계약을 공유하므로 witnessd가 앞서 keyless를 주장하면 검증기와 어긋난다.
> 2. **stdlib/openssl-only 제약과 air-gapped wedge.** Depone은 no-external-deps이고, 규제/감사(air-gapped Depone 소비) wedge에서는 Fulcio(OIDC 발급)·Rekor(공개 transparency log)로의 네트워크 접근 자체가 불가능하거나 금지된다. operator-key 서명은 오프라인·no-network로 완결되어 air-gapped 소비와 맞는다.
> 3. **swappable로 남겨 후속 진화.** signing 단계는 어댑터에서 교체 가능하게 설계하되(§3.5), 그때까지 witnessd는 **절대 keyless/transparency-logged 시맨틱을 주장하지 않는다.** keyless 서명 축은 Fulcio 인증서 체인 검증 + Rekor inclusion proof를 Depone verify 경로에 추가하는 별도 wave에서 다룬다.

### 3.11 UX 규율 (하드 규칙)

> **Decision (재검토 가능):** "evidence-pending"을 하드 규칙으로 강제한다. Depone 외부 검증이 통과하기 전에는 `VERIFIED`/`DONE`/`COMPLETE`/`ORCHESTRATION COMPLETE` 같은 성공 문자열을 SoT로 표시하는 것을 **금지**한다. **Rationale:** 우리가 teardown에서 조롱한 실패모드(OMO `<promise>VERIFIED</promise>`, OMX `doctor`의 false-positive "all clear", Devin "confident hallucination that passes every automated check")를 스스로 반복하지 않기 위함. 완료 UX는 정확히 "evidence-pending Depone verification"이며, 등급은 오직 §3.1의 재도출을 통과한 뒤에만 A0/A1/A2로 표기된다(+ signing_status). 이 규율은 CI 게이트(`witnessd`가 성공 문자열을 출력하는 코드 경로에 evidence 검증 통과를 선행 조건으로 강제)로 검증한다.

---

## 4. Depone 증거 계약 (witnessd가 방출하는 것)

이 절은 witnessd가 실행 중·직후에 `evidence_dir`로 방출해야 하는 아티팩트를 **하나도 빠짐없이** 정의한다. 각 아티팩트는 자기보고(self-report)가 아니라 관측자-분리 + 해시-바인딩 + (필요 시) 서명된 바이트이며, 실행하지 않는 Depone이 오프라인에서 정확히 재도출할 수 있어야 한다. 여기서 "요구"는 전부 Depone의 실제 검증 함수·error code로 검증 가능하다. 스키마 키, kind, schema_version, error code는 전부 Depone 코드에서 확인한 원문이며, witnessd는 이 값을 문자 그대로 방출해야 한다(오타 하나가 fail-closed를 유발한다).

### 4.0 계약 불변식 (Depone 계승 — Decision, 재검토 가능)

**Decision.** witnessd는 아래 5개 불변식을 Depone과 **공유 계약**으로 계승한다. 이 불변식들은 witnessd 쪽에서 재협상되지 않으며, 위반 시 Depone이 아티팩트를 `A0`/`blocked`/`refuted`로 떨어뜨린다.

1. **Canonical hashing 규약(단일 SoT).** 모든 콘텐츠 해시는
   `hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()`
   로 계산한다. 이것은 Depone의 `depone/agent_fabric/claim_gate.py::canonical_hash`(line 16-19)와 `capture_bridge.py::_sha256_json`(line 45-46)가 **바이트 단위로 일치**하는 규약이다. witnessd의 emitter는 이 함수를 그대로 재구현하고, 한 곳(예: `witnessd/canonical.py`)에서만 정의해 전 아티팩트가 참조해야 한다.
   - **주의(구현 정밀도).** 두 갈래의 예외를 정확히 지켜라. (a) evidence-substrate 번들의 **DSSE payload 인코딩**은 `evidence_substrate.py::_canonical_json`(line 32-33)로 `ensure_ascii=False`(UTF-8 바이트)를 쓴다. (b) trusted-observer-provenance의 **binding payload**는 `observer_provenance.py::_unsigned_dsse_envelope`(line 264-270)로 `json.dumps(binding, sort_keys=True, separators=(",", ":"))`(기본 `ensure_ascii=True`)를 쓴다. subject **digest** 값과 매니페스트/리시트 해시는 (a)/(b)와 무관하게 항상 `canonical_hash`(ensure_ascii 기본)로 계산한다. 서명은 바이트에 대한 것이므로 이 세 인코딩을 혼동하면 서명 검증이 실패한다.

2. **Fail-closed(부분점수 없음).** 미지의 isolation model/fact, hash mismatch, stale `source_fixture_hash`, 범위 밖 touched file, 서명 부재/불일치, `prev_capture_hash` 체인 단절, openssl 부재 중 **하나라도** 발생하면 verdict는 `A0`/`blocked`/`refuted`이며 중간 점수가 없다. Depone의 각 검사가 이 규칙으로 짜여 있으므로(예: `isolation.py::verify_isolation_boundary`가 unknown fact를 `boundary: False`로), witnessd가 "대충 채운" 아티팩트는 절대 assurance를 얻지 못한다.

3. **Worker는 자기 성공을 seal·validate 못 한다.** 코드를 작성한 runner와 그것을 관측·서명하는 observer는 **다른 프로세스·다른 출력 디렉터리**(A2에서는 다른 uid)여야 한다. `observe.py::enforce_observer_separation`이 이 분리를 구조적으로 강제한다.

4. **Verifier는 assurance를 상향 못 한다.** Depone이 방출하는 모든 verdict의 `boundary.raises_assurance == false`. witnessd는 Depone이 이 값을 뒤집을 수 없다는 사실에 기대어, aggressive autonomy를 안전하게 밀 수 있다.

5. **UX 규율(하드 규칙).** witnessd CLI/로그는 Depone 검증 **전**에는 lane 상태를 `evidence-pending`으로만 표시한다. `VERIFIED`/`DONE`/`COMPLETE`/`ORCHESTRATION COMPLETE` 같은 self-report 문자열을 SoT로 노출하는 것을 **금지**한다(우리가 teardown에서 조롱한 OMO `<promise>VERIFIED</promise>` 실패모드의 재발 방지). 성공 문자열은 오직 Depone의 `python3 -m depone ...` verdict가 `pass`/`A1`/`A2`를 낸 뒤에만 파생된다.

*Rationale.* 이 5개는 Depone이 "실행하지 않고도 신뢰를 재도출"할 수 있게 하는 유일한 근거다. witnessd가 이 중 하나라도 완화하면, 두 repo가 공유하는 계약이 깨지고 witnessd는 teardown이 실증한 self-report 오케스트레이터로 회귀한다.

### 4.1 evidence_dir 레이아웃

lane 하나당 witnessd는 `evidence_dir`(예: `lane-1/`)를 방출한다. observer 소유 캡처·verify-log와 개인키/공개키는 **이 디렉터리 밖**, 그리고 **runner sandbox 밖**에 있어야 한다(§4.11 핸드오프 참조).

**포함관계 불변식(§3.3 분리요건의 근거).** 세 경로 집합의 관계를 다음으로 못박는다.
- `runner_sandbox`(= lane git worktree, runner가 코드를 쓰는 유일한 writable 트리) **∩** `evidence_dir` **= ∅**, 그리고 `runner_sandbox` **∩** `observer-owned/` **= ∅**.
- 즉 `evidence_dir`(lane-N/)와 `observer-owned/`는 결코 worktree 안이 아니다 — worktree의 형제(sibling)로 둔다. 따라서 observer의 `--out`/`--log`가 항상 runner sandbox 밖이 되어 `enforce_observer_separation`이 통과한다(evidence_dir가 worktree 안으로 잡히면 §6.2.2로 fail-closed). 개인/공개키는 두 집합 **모두** 밖.
- worktree receipt의 `evidence_dir`는 이 관계가 성립하는 root-relative 경로여야 한다(아래 경로 규율).

```
<repo-root>/
  worktrees/lane-1/                      # runner_sandbox (= lane worktree, runner-writable). evidence_dir와 분리.
  lane-1/                                # evidence_dir (worktree 밖, root-relative, team-ledger가 참조)
    agent-fabric-capture-manifest.json   # E2 (kind=agent-fabric-capture-manifest, v1.0)
    runner-receipt.json                  # E5 (kind=agent-fabric-runner-receipt, v1.0)
    worktree-lane-receipt.json           # E5 (kind=depone-worktree-lane-receipt, v0.1)
    evidence-substrate-bundle.json       # E7 (kind=depone-evidence-substrate-bundle, v1.0)
                                         #    OTel GenAI span은 이 번들의 인라인 키 `otel_spans`다(별도 파일 아님, §4.9)
    trusted-observer-provenance.json     # E6 (kind=trusted-observer-provenance, v1.0, DSSE)
    evidence-contract.json               # E9 (schema_version=v105.verify_wedge)
    git-diff-name-only.txt               # E9 (touched files, root-relative)
    git-diff.patch                       # E9 (test-weakening 구조 탐지 대상)
    exit-code.txt                        # E9 (기대 exit code 대조)
  team-ledger.json                       # E10 (kind=depone-team-ledger, v0.1) — 팀 실행 시
<evidence_dir 밖 · runner sandbox 밖, observer/operator 소유>
  observer-owned/observer-capture.json   # E1 (observed_by=depone-observer) — 0700, runner-not-writable
  observer-owned/verify-command.json     # E3 observer command receipt 로그 (log_path 참조 대상) — sandbox 밖
  operator-ed25519.pub.pem               # E6 공개키 (out-of-band, evidence 밖)
```

경로 규율(검증 가능): `evidence-contract.json`, `git-diff-name-only.txt`, `git-diff.patch`는 반드시 **evidence 루트 직속**이어야 한다. 중첩(`sub/evidence-contract.json`)은 `evidence_contract.py::_find_control_shadow`가 `ERR_EVIDENCE_CONTRACT_SHADOWED`로 거부한다. worktree receipt의 `evidence_dir`는 반드시 상대경로이며 `..` 금지(`worktree_receipt.py::_normalize_relative_path` → `ERR_WORKTREE_RECEIPT_PATH_INVALID`). observer의 `--out`/`--log`(= `observer-owned/`의 두 파일)는 위 포함관계 불변식에 따라 runner sandbox 밖이며, 안이면 `ERR_OBSERVER_NOT_SEPARATED`로 파일 미기록(§6.2.2).

### 4.2 observer_capture — E1 (관측자-분리 캡처)

- **파일명:** `observer-owned/observer-capture.json` (반드시 runner sandbox **밖**)
- **kind/식별:** `observed_by` = `"depone-observer"` (상수 `capture_bridge.OBSERVER_ID`)
- **필수 필드(`REQUIRED_OBSERVER_FIELDS`, capture_bridge.py:28-37):** `observed_by`, `source_fixture_hash`, `diff_summary`, `touched_files`, `test_output`, `command_receipts`
- **분리 요구:** witnessd의 observer 프로세스는 `--out`/`--log`를 runner sandbox 밖으로 잡아야 한다. Depone의 `observe.py::enforce_observer_separation`(line 74-108)은 `out` 부모 디렉터리, `out` 자체, `log` 디렉터리·파일이 runner sandbox 안에 있거나 sandbox와 동일하면 `PairedRunError` code `ERR_OBSERVER_NOT_SEPARATED`를 던지고 **출력을 쓰지 않는다**(fail-closed). witnessd는 캡처 방출 전에 동일 검사를 emitter 내부에서 재현해야 한다.
- **canonical hash 바인딩:** observer_capture 전체가 매니페스트의 `observer_capture_hash`로 봉인된다(§4.3). witnessd는 `observe.py::write_observer_capture`처럼 파일을 pretty-print로 쓰되, **해시는 항상 canonical(compact) 직렬화**로 계산해야 한다.
- **Depone 검사(함수·error code):** `capture_bridge.py::_check_observer_capture_shape`(line 279-334). `observed_by != "depone-observer"`면 `"observer_capture.observed_by must be 'depone-observer'"`; `command_receipts`가 빈 리스트면 `"observer_capture.command_receipts must be non-empty for A1"`(§4.4). A2 목표 시 uid 격리는 §4.5.

### 4.3 agent-fabric-capture-manifest — E2 (증거 매니페스트)

- **파일명:** `lane-1/agent-fabric-capture-manifest.json`
- **kind:** `"agent-fabric-capture-manifest"` (`CAPTURE_MANIFEST_KIND`)
- **schema_version:** `"1.0"` (`CAPTURE_MANIFEST_VERSION`)
- **필수 top-level 필드(`_check_top_level`, capture_bridge.py:178-211):** `schema_version`, `kind`, `source_fixture_hash`, `fixture`, `assurance`, `decision`, `allowed_touched_files`, `observer_capture`, `observer_capture_hash`, `required_observer_fields`. append-only 링크용 `prev_capture_hash`도 포함(§4.9).
- **assurance/decision enum:** `A0-claims-only`/`claims-only`, `A1-local-observed`/`observed-local-capture`, `A2-isolated-observed`/`isolated-observed`. witnessd는 이 값을 직접 조작하지 말고 `build_capture_manifest`와 동일한 구성 로직으로 **facts로부터 파생**시켜야 한다.
- **canonical hash 바인딩(2중):**
  - `source_fixture_hash == canonical_hash(fixture)`. 불일치 시 `"source_fixture_hash mismatch"`.
  - `observer_capture_hash == canonical_hash(observer_capture)`. 불일치 시 `"observer_capture_hash mismatch"`. 또한 `observer_capture.source_fixture_hash == manifest.source_fixture_hash` 아니면 `"observer_capture.source_fixture_hash is stale"`(변조·재사용 방지).
- **Depone 검사(함수):** `capture_bridge.py::validate_capture_manifest`(line 131-175). 빈 리스트 반환 = 통과. A1은 `_check_a1_manifest`, A2는 `_check_a2_manifest`.
- **범위 밖 쓰기 refute(핵심):** `_check_observed_block`(line 242-253)이 `touched_files ⊆ allowed_touched_files`와 `diff_summary.changed_files ⊆ allowed_touched_files`를 강제한다. 초과 시 `"unexpected touched files: [...]"` / `"unexpected diff files: [...]"`. → witnessd의 ownership-region lock(M5)이 방출하는 `allowed_touched_files`가 곧 이 계약의 상한이다.
- **테스트 상태 enum:** `test_output.status ∈ {not-run, passed, failed, error}`(`VALID_TEST_STATUSES`). 벗어나면 `"...test_output.status=... not in [...]"`.

### 4.4 command_receipts + diff/touched/test_output — E3

- **위치:** observer_capture 내부(§4.2)
- **필수 형태(`_check_observer_capture_shape`, capture_bridge.py:320-334):** `command_receipts`는 **비어있지 않은** `list[dict]`이고, 각 원소는 `command` 키가 있어야 하며 `exit_code`는 **int**여야 한다.
  - 빈 리스트 → `"observer_capture.command_receipts must be non-empty for A1"`
  - `command` 누락 → `"observer_capture.command_receipts[{i}] missing command"`
  - 비-int exit_code → `"observer_capture.command_receipts[{i}].exit_code must be an int"`
- **부수 필드:** `diff_summary.changed_files`는 `list[str]`, `touched_files`는 `list[str]`, `test_output`는 객체이고 `status`는 §4.3 enum.
- **방출 방법(참조 구현):** witnessd의 observer는 Depone의 `paired_run.py::build_observer_capture`(line 303-332) 형태를 따른다 — 검증 명령을 실행하기 **전후**로 `diff_summary`를 찍고, 단일 caller-declared 검증 명령만 실행하며(임의 transcript 명령 실행 금지), 그 결과를 `command_receipts[0]`(with `command`, `cwd`, `exit_code`, `log_path`, `status`)로 남긴다. `status`는 `exit_code == 0 → "passed"`, 아니면 `"failed"`, 타임아웃 `"error"`(exit 124), not-found `"error"`(exit 127).
- **Depone 검사:** 위 shape 검사가 통과해야 A1 candidate가 성립한다.

### 4.5 isolation facts + isolation_hash — E4 (A2 특권 경계 증명)

- **위치:** A2 목표 시 매니페스트의 `isolation` 객체 + `isolation_hash`
- **A2 격리 모델(Decision — 재검토 가능):** witnessd는 **uid 모델**을 1급으로 삼는다: `model` = `"uid-boundary-unwritable-observer-dir"`(`isolation.ISOLATION_MODEL`), 필드 `runner_uid`(int), `observer_uid`(int), `observer_dir_writable_by_runner`(bool). 경계 성립 조건은 `runner_uid != observer_uid`, `runner_uid != 0`(root는 퍼미션 비트를 무시하므로 경계 불성립), `observer_dir_writable_by_runner == False`. 추가로 observer_dir는 `0700` & runner-not-writable로 세팅한다. docker 모델(`"container-boundary-unwritable-observer-dir"`)은 **후속 옵션**으로 deferred하며, 채택 시 `_verify_container_isolation_boundary`가 요구하는 `container.{runtime==docker, container_id, running==True, observer_launched==True, observer_dir_mounted_rw==False, mounts:list}`를 전부 채워야 한다.
  - *Rationale:* uid 모델은 잠긴 기업 머신에서 Docker-in-Docker 마찰(OpenHands가 겪은)을 피하면서 A2 floor를 얻는다. 컨테이너는 강하지만 후순위.
- **facts 채취(참조):** `isolation.py::probe_isolation_facts`(line 206-245)가 observer 프로세스의 `os.getuid()`와 observer_dir의 `st_uid`/그룹·기타 쓰기 비트로 `observer_dir_writable_by_runner`를 산출한다. `runner_uid`는 runner receipt(§4.6)에서 가져온다. POSIX uid가 없으면 fact가 `None`이 되어 fail-closed로 A1에 머문다.
- **canonical hash 바인딩:** `isolation_hash == canonical_hash(isolation)`. 불일치 시 `"isolation_hash mismatch"`.
- **위조 불가(플래그 뒤집기 방지):** `_check_a2_manifest`(capture_bridge.py:262-276)는 매니페스트가 A2를 주장해도 `verify_isolation_boundary(isolation).boundary`가 True가 아니면 `"A2 isolation does not establish a privilege boundary"`를 낸다. 즉 facts 자체가 경계를 세워야 하며, `assurance` 문자열만 A2로 바꿔서는 통과 불가. same-uid facts는 `verify_isolation_boundary`가 `"runner and observer share the same uid"`로 `boundary: False` → A1 유지.
- **Depone 검사(함수·핵심):** `isolation.py::verify_isolation_boundary`(line 33-74) + `capture_bridge._check_a2_manifest`.

### 4.6 runner receipt — E5

- **파일명:** `lane-1/runner-receipt.json`
- **kind:** `"agent-fabric-runner-receipt"` (`RUNNER_RECEIPT_KIND`)
- **schema_version:** `"1.0"` (`RUNNER_RECEIPT_VERSION`)
- **필수 필드(`build_runner_receipt`, paired_run.py:335-365):** `kind`, `schema_version`, `runner_kind`, `arm`, `task_id`, `worktree`, `invocation`(비어있지 않은 `list[str]`), `transcript_path`, `exit_code`(int), `touched_files`(`list[str]`), `started_at`, `ended_at`, `human_intervened`(bool), 그리고 `source_hashes.receipt == canonical_hash(receipt-without-source_hashes)`.
- **enum:** `runner_kind ∈ {codex-cli, manual}` (`VALID_RUNNERS`), `arm ∈ {direct, governed}` (`VALID_ARMS`). 첫 어댑터가 shell/Codex임을 감안, shell lane은 관측 계약 상 observer_capture로 표현하고 runner receipt의 `runner_kind`는 현재 enum(`codex-cli`/`manual`)을 따른다; 새 어댑터(Claude Code/OpenCode) 추가 시 이 enum 확장이 Depone 계약 변경 지점임을 명시(§오픈이슈).
- **Depone 검사(함수·error code):** `paired_run.py::validate_runner_receipt`(line 368-390). kind/schema/enum/필드 타입 위반 시 각각 문자열 에러. 팀 문맥에서는 `build_paired_run_report`가 arm 불일치 `ERR_PAIRED_RUN_ARM_MISMATCH`, receipt 무효 `ERR_PAIRED_RUN_RUNNER_RECEIPT_INVALID`, 비정상 종료 `ERR_PAIRED_RUN_RUNNER_FAILED`, 검증 미통과 `ERR_PAIRED_RUN_VERIFICATION_NOT_PASSED`로 blocker를 낸다.

### 4.7 worktree lane receipt — E5 (팀 lane)

- **파일명:** `lane-1/worktree-lane-receipt.json`
- **kind:** `"depone-worktree-lane-receipt"` (`WORKTREE_LANE_RECEIPT_KIND`)
- **schema_version:** `"0.1"` (`WORKTREE_LANE_RECEIPT_SCHEMA_VERSION`)
- **필수 필드(`build_worktree_lane_receipt`, worktree_receipt.py:22-72):** `kind`, `schema_version`, `worktree`, `branch`, `base_commit`, `head_commit`, `dirty`(bool), `dirty_files`, `changed_files`(= `git diff --name-only base..HEAD`), `evidence_dir`(root-relative), `command_receipts`, `boundary`(전부 read-only: `executes_commands=false` 등). witnessd는 이 리시트를 **read-only git state**로만 만들어야 한다(커밋/머지 실행 금지).
- **canonical hash 바인딩:** 리시트는 team-ledger의 subject로 들어가고, 그 hash 정합은 team-ledger verdict(§4.10)가 확인한다.
- **Depone 검사(함수·error code):** `team_ledger.py::_validate_worktree_receipt`(line 1255~) + `_validate_worktree_receipt_files`. 핵심 규칙:
  - fan-in(passed lane) 시 `dirty == False` 아니면 `ERR_...`("worktree_receipt dirty must be false for passed lane fan-in").
  - `changed_files ⊇ lane.touched_files` 아니면 `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_TOUCHED_FILES_MISMATCH`.
  - overlap이 있어 merge receipt가 required일 때 `changed_files == lane.touched_files`(under-report 금지) 아니면 `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_TOUCHED_FILES_UNDERREPORTED`.
  - `evidence_dir` 불일치 시 `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_EVIDENCE_DIR_MISMATCH`.
  - kind/schema 위반 시 각각 `ERR_...`("worktree_receipt kind must be ...", "... schema_version must be 0.1").
  - 프로듀서 측 실패는 `WorktreeReceiptError` code `ERR_WORKTREE_RECEIPT_BASE_COMMIT_REQUIRED`/`ERR_WORKTREE_RECEIPT_REPO_MISSING`/`ERR_WORKTREE_RECEIPT_GIT_FAILED`/`ERR_WORKTREE_RECEIPT_PATH_INVALID`/`ERR_WORKTREE_RECEIPT_COMMAND_RECEIPTS_INVALID`.

### 4.8 trusted-observer-provenance — E6 (Ed25519 DSSE)

- **파일명:** `lane-1/trusted-observer-provenance.json`
- **kind:** `"trusted-observer-provenance"` (`PROVENANCE_KIND`)
- **schema_version:** `"1.0"` (`PROVENANCE_SCHEMA_VERSION`)
- **scheme(Decision):** `"DSSE-Ed25519-openssl-cli"` (`DSSE_PROVENANCE_SCHEME`). **operator-held Ed25519 개인키로 witnessd가 서명**하고, Depone은 **공개키로만 verify**한다. 개인키는 verify 경로에 절대 로드되지 않는다. Sigstore Fulcio keyless + Rekor는 **명시적 deferred** — witnessd는 signing step을 swappable로 두되 keyless/transparency 시맨틱을 주장하지 않는다(`sign.py::operator_key_signature_boundary`가 `keyless_identity=False, transparency_logged=False`를 못박음).
- **필수 필드(`build_signed_trusted_observer_provenance`, observer_provenance.py:62-87):** `kind`, `schema_version`, `evidence_path`, `manifest_hash`(= `canonical_hash(manifest)`), `observer_capture_hash`(= manifest의 값), `scheme`, `key_id`(non-empty), `dsse_envelope`.
- **서명 대상(binding):** `_binding`(line 254-261) = `{kind:"trusted-observer-provenance-binding", schema_version:"1.0", evidence_path, manifest_hash, observer_capture_hash}`를 `json.dumps(sort_keys=True, separators=(",",":"))`로 직렬화한 payload를 DSSE PAE(`sign.py::dsse_pae`: `b"DSSEv1 " + len(payloadType) + " " + payloadType + " " + len(payload) + " " + payload`)로 감싸 openssl `pkeyutl -sign -rawin`으로 서명. `payloadType` = `"application/vnd.depone.trusted-observer-provenance.v1+json"`.
- **canonical hash 바인딩:** provenance가 참조하는 `manifest_hash`/`observer_capture_hash`가 실제 매니페스트와 정확히 일치해야 한다. 하나라도 어긋나면 미스매치.
- **Depone 검사(함수·error code):** `observer_provenance.py::validate_trusted_observer_provenance`(line 90-126) → `_signed_record_errors`. 
  - provenance 없음 → `ERR_TRUSTED_PROVENANCE_MISSING`("trusted observer provenance missing").
  - `evidence_path` 후보는 있으나 불일치 → `ERR_TRUSTED_PROVENANCE_MISMATCH`.
  - 공개키 경로 누락 → `"trusted observer provenance public key missing"`.
  - **openssl 부재 → `ERR_OPENSSL_UNAVAILABLE`(fail-closed).**
  - 서명 검증 실패 → `ERR_TRUSTED_PROVENANCE_SIGNATURE_FAILED`.
  - 서명된 binding이 매니페스트에서 재계산한 기대 binding과 다르면 `"trusted observer provenance signed binding mismatch"`.

### 4.9 evidence-substrate 번들 — E7 (in-toto Statement v1 + DSSE + OTel GenAI)

- **파일명:** `lane-1/evidence-substrate-bundle.json` (단일 파일 — OTel span은 sibling이 아니라 이 번들의 인라인 키다)
- **kind:** `"depone-evidence-substrate-bundle"`, **schema_version:** `"1.0"` (`evidence_substrate.build_evidence_bundle`, line 658-683)
- **번들 구조(Depone `build_evidence_bundle` 실제 반환 형태와 일치):** 최상위 키는 `kind`, `schema_version`, `statement`(in-toto), `dsse_envelope`(DSSE), **`otel_spans`(인라인 리스트)**, `assurance`(= `capture_manifest.get("assurance")`, 상한 A2), `signing_status`, `boundary`. 즉 **OTel span은 번들 JSON 안의 `otel_spans` 키**이며 별도 `otel-genai-spans.json` 파일로 분리하지 않는다(self-test가 `bundle["otel_spans"]`을 직접 읽는다). `ingest_signed_evidence_bundle`은 서명된 statement의 subject digest를 디스크 아티팩트에서 재계산하고, OTel은 이 인라인 리스트를 구조 검증한다(`validate_external_otel_spans`).
- **구성(3부):**
  1. **in-toto Statement v1** (`build_intoto_statement_from_capture`, line 45-132): `_type` = `"https://in-toto.io/Statement/v1"`(`INTOTO_STATEMENT_TYPE`), `predicateType` = `"https://depone.dev/attestations/evidence/v1"`(`DEPONE_PREDICATE_TYPE`), `subject`는 각 `{name, digest.sha256}`: `depone-capture-manifest`(=`canonical_hash(manifest)`), `source_fixture`(=`source_fixture_hash`), `observer_capture`(=`observer_capture_hash`), `runner_receipt`(=`canonical_hash(runner_receipt)`), 있으면 `prev_capture`(=`prev_capture_hash`). `predicate.boundary` = `{raises_assurance:false, signed:false, signing_status:"unsigned-content-addressed"}`.
  2. **DSSE envelope** (`wrap_statement_in_dsse`, line 135-143): `payloadType` = `"application/vnd.in-toto+json"`(`DSSE_PAYLOAD_TYPE`), `payload` = base64(`_canonical_json(statement)`, **`ensure_ascii=False`**), `signatures`. 서명 시 `sign.py::sign_evidence_bundle`이 `signing_status`를 `"signed-ed25519-operator-key"`로, `signature_boundary`를 operator-key 프로파일로 붙인다.
  3. **OTel GenAI spans** (`build_otel_genai_spans`, line 593-655): `invoke_agent`(root) + command receipt당 `execute_tool` span. 각 span은 `trace_id`, `span_id`, `parent_span_id`, `name`, `attributes.gen_ai.operation.name`. **관측되지 않은 `gen_ai.usage.*` 필드를 발명하면 안 된다**(self-test가 이를 금지).
- **canonical hash 바인딩:** 모든 subject digest가 §4.0 규약으로 재계산 가능해야 한다.
- **Depone 검사(함수·error code):**
  - 서명 번들: `evidence_substrate.py::ingest_signed_evidence_bundle`(line 509-590). `sign.py::verify_signed_bundle`로 공개키 검증 실패 시 `signing_status="unverifiable-signature"`, `signature_verified=false`, decision `blocked`. 검증 후 각 subject digest를 **디스크의 실제 아티팩트에서 재계산**(`resolve_present_artifact_digests`)해 대조 — 누락은 `status:"missing"`(blocked), 존재하나 해시 불가는 `status:"unreadable"`(blocked), 불일치는 `status:"mismatch"`(blocked).
  - 미서명 번들: `ingest_dsse_envelope`(line 353-393)는 `signatures != []`면 `"DSSE envelope contains unverifiable signatures"`(blocked)로 못박아, **서명이 없으면 정확히 `signatures==[]`여야** 한다. 아니면 blocked.
  - 어느 경로든 `boundary.raises_assurance == false`.

### 4.10 append-only prev_capture_hash 체인 — E8

- **위치:** 각 매니페스트의 `prev_capture_hash` 필드(§4.3) — genesis는 `null`, 이후는 직전 매니페스트의 `canonical_hash`.
- **형식 검사:** `validate_capture_manifest`(capture_bridge.py:164-173)는 `prev_capture_hash`가 `null` 또는 64자 소문자 hex여야 한다. 아니면 `"prev_capture_hash must be null or a 64-char sha256 hex string"`.
- **체인 검사(함수):** `evidence_substrate.py::verify_capture_chain`(line 742-815). genesis head가 `null`이 아니면 `blocked`("chain head must be genesis"); 각 스텝의 `prev_capture_hash`가 직전 스텝의 canonical hash와 다르면 `blocked`("chain[{i}] prev_capture_hash does not match predecessor"); 매니페스트 자체가 무효면 `blocked`. **drop/reorder/tamper 모두 downstream 링크를 깨서 blocked**. 빈 리스트는 `inconclusive`. 링크는 in-toto statement의 `prev_capture` subject로도 실려 ingest 경로에서 재검증된다(broken link = digest mismatch = blocked).
- **witnessd 요구:** 단일 append-only 서명 이벤트 로그(M1)에서 lane별 매니페스트를 **방출 순서대로** 체인해야 하며, run-state/team-state는 이 로그의 projection이어야 한다(split-brain 구조적 불가).

### 4.11 evidence-contract.json — E9

- **파일명:** `lane-1/evidence-contract.json` (evidence 루트 직속)
- **schema_version:** `"v105.verify_wedge"` (`_CONTRACT_SCHEMA_VERSION`). 불일치 시 `ERR_EVIDENCE_CONTRACT_INVALID`.
- **최소 요구:** `_has_enforcement_directive`(evidence_contract.py:102-126)를 만족하는 **enforcement directive ≥1개**. 후보 키: `required_evidence`/`required_paths`/`required_evidence_paths`(비어있지 않은 `list[str]`), `required_commands`(각 `{log_path 또는 expected_exit_code}`), top-level `expected_exit_code`(int), `allowed_touched_files`/`allowed_files`, `forbidden_touched_files`/`forbidden_files`, `forbidden_test_files`, `forbid_test_weakening:true` + `test_file_patterns`. 하나도 없으면 `ERR_EVIDENCE_CONTRACT_INVALID`("must declare at least one enforcement directive").
- **동반 아티팩트:** `git-diff-name-only.txt`(touched files), `git-diff.patch`(패치 본문), `exit-code.txt`(실제 exit code).
- **Depone 검사(함수·error code):** `verify/evidence_contract.py::validate_evidence_contract`(line 222-351).
  - 계약 파일 없음 → `ERR_EVIDENCE_CONTRACT_MISSING`.
  - 루트 밖 중첩 control 파일 → `ERR_EVIDENCE_CONTRACT_SHADOWED`.
  - 요구 증거 누락 → `ERR_REQUIRED_TEST_EVIDENCE_MISSING`.
  - `expected_exit_code`와 `exit-code.txt` 불일치 → `ERR_TEST_EXIT_CODE_MISMATCH`.
  - 허용 밖/금지 touched file → `ERR_FORBIDDEN_FILE_TOUCHED`.
  - **test-weakening 구조 탐지:** `_forbidden_test_file_weakened`(line 194-208)가 `git-diff.patch`에서 forbidden test 파일의 `assert`/`self.assert`/`pytest.raises(`/`pytest.fail(`/`raise AssertionError`/`pass` 삭제 라인 또는 `+assert True` 추가를 잡아 `ERR_TEST_WEAKENED`.
- **witnessd 요구:** ownership-region lock(M5)이 만든 `allowed_touched_files`가 이 계약의 `allowed_touched_files`와 §4.3의 매니페스트 `allowed_touched_files`에 **동일하게** 반영되어야 한다.

### 4.12 team-ledger — E10 (팀 fan-in)

- **파일명:** `team-ledger.json` (repo 루트)
- **kind:** `"depone-team-ledger"` (`TEAM_LEDGER_KIND`), **schema_version:** `"0.1"` (`TEAM_LEDGER_SCHEMA_VERSION`)
- **필수 헤더(`_validate_ledger_header`, team_ledger.py:187-207):** `kind`, `schema_version`, `leader_objective`, `leader_id`, `start_commit`, `stop_rule`(전부 non-empty string), `lanes`(비어있지 않은 리스트).
- **lane별 필드:** `lane_id`(중복 금지 → `ERR_TEAM_LEDGER_LANE_ID_DUPLICATE`), `verification_state ∈ {pass, blocked}`(`VALID_LANE_VERIFICATION_STATES`), `env_kind ∈ {local, container, cloud}`(`VALID_ENV_KINDS`), adapter kind(`VALID_ADAPTER_KINDS`), `evidence_dir`, `touched_files`(passed lane은 ≥1개 → 없으면 `ERR_TEAM_LEDGER_TOUCHED_FILES_REQUIRED`), worktree receipt(§4.7), `evidence_next_verdict`(아래).
- **`evidence_next_verdict`(passed lane 필수, Depone `team_ledger.py::_validate_evidence_next_verdict`에서 실재).** 이는 별도 kind가 아니라 **ledger base dir에 상대적인 JSON 파일 경로**(문자열)이며, 그 파일은 `evidence-next` 서브커맨드(§4.13; "Re-validate an evidence-run directory and select the next safe action")가 생성한 verdict 객체다. 파일 요건: 루트가 객체, `command == "evidence-next"`, `decision == "continue"`, `blocking_reasons`가 빈 리스트. fail-closed 코드: 미포함(passed lane) → `ERR_TEAM_LEDGER_EVIDENCE_NEXT_VERDICT_REQUIRED`; 절대경로·base 이탈 → `ERR_TEAM_LEDGER_EVIDENCE_NEXT_VERDICT_PATH_INVALID`; 파일 부재 → `ERR_TEAM_LEDGER_EVIDENCE_NEXT_VERDICT_MISSING`; 비-JSON/객체아님/`command` 불일치 → `ERR_TEAM_LEDGER_EVIDENCE_NEXT_VERDICT_INVALID`; `decision≠continue` 또는 blocking_reasons 존재 → `ERR_TEAM_LEDGER_EVIDENCE_NEXT_NOT_CONTINUE`. witnessd는 이 verdict 파일을 lane evidence_dir 아래에 방출하고 ledger lane에 상대경로로 참조한다.
- **read-only lane 처리(구조적 에지케이스, §6 참조).** passed lane은 `touched_files ≥ 1`을 요구하므로(`ERR_TEAM_LEDGER_TOUCHED_FILES_REQUIRED`), 파일을 하나도 바꾸지 않는 정당한 lane(검증-only, 조사-only)은 team-ledger의 **passed(merge-bearing) lane으로 fan-in하지 않는다.** witnessd Orchestrator는 lane을 `lane_kind ∈ {write, read-only}`로 구분하고, read-only lane은 (a) 자기 assurance 축의 capture-manifest/observer_capture/runner-receipt를 정상 방출하되(A0/A1/A2 재도출은 그대로 가능), (b) team-ledger `lanes` 배열의 passed 코드 lane에는 포함하지 않는다(머지·파일소유와 무관하므로). read-only lane을 원장에 반드시 기록해야 하면 `verification_state:"blocked"`가 아니라 별도 감사 이벤트로 runlog에만 남기고, `touched_files` 강제 대상에서 제외한다. 이 정책은 §8.2 오픈결정이 아니라 본 spec의 확정 처리다.
- **overlap → merge receipt 필수(핵심):** `build_team_ledger_verdict`(line 68-145)가 `_find_overlapping_touched_files`로 lane 간 touched file 겹침을 찾고, 겹치면 `merge_receipt`를 **required**로 만든다(`build_team_ledger_merge_receipt`가 `command:"team-ledger-merge-receipt", schema_version:"1.0", decision∈{pass,blocked}, lanes, files, conflict_events`). merge receipt decision 무효 시 `ERR_TEAM_LEDGER_MERGE_RECEIPT_DECISION_INVALID`.
- **canonical hash 바인딩:** verdict의 `source_hashes.team_ledger == canonical_hash(ledger)`.
- **verdict boundary:** `{raises_assurance:false, approves_merge:false, executes_commands:false, launches_agents:false, calls_live_models:false, inspects_cloud_runtime:false}` — Depone은 머지를 승인하지도 assurance를 올리지도 않는다.
- **Depone 검사(함수):** `team_ledger.py::build_team_ledger_verdict` / `validate_team_ledger`. lane/커밋스코프/PR·cloud artifact 관련 다수의 `ERR_TEAM_LEDGER_*` code(예: `ERR_TEAM_LEDGER_COMMIT_SCOPE_SUBJECT_MISMATCH`, `ERR_TEAM_LEDGER_PR_ARTIFACT_STALE`, `ERR_TEAM_LEDGER_PR_ARTIFACT_CHECKS_NOT_PASSING`)로 fail-closed.

### 4.13 Depone 핸드오프 절차 (오프라인 재검증)

witnessd는 **서명된 바이트 + 공개키만** Depone에 넘긴다. 실행 능력(worker launch, retry, worktree, schedule)은 Depone이 절대 하지 않는다.

1. **방출:** lane 실행 → observer 프로세스가 runner sandbox 밖(0700, runner-not-writable)에 `observer-capture.json` 기록 → emitter가 `evidence_dir`에 §4.3~4.12 아티팩트를 방출하고 operator Ed25519 개인키로 DSSE 서명(§4.8).
2. **공개키 out-of-band:** 공개키(`operator-ed25519.pub.pem`)는 **`evidence_dir` 밖** 경로로 전달하고 `DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE` 환경변수로 지정한다. Depone(`verify/adapters/generic.py`, line 116)은 이 경로가 evidence 디렉터리 **안**으로 resolve되면 **무시**한다(runner-writable 키로 A1/A2 승격 불가). 개인키는 verify 경로에 절대 없다.
3. **오프라인 재검증 명령(air-gapped Depone 소비):**
   ```bash
   # observer 분리 + DSSE provenance 경로 스모크
   python3 -m depone agent-fabric-observe --self-test
   # 팀 원장 재검증 (skeleton 회귀)
   python3 -m depone team-ledger --ledger lane-1/team-ledger.json --json
   # changed-tier 릴리스 계약 게이트 (repo 계약 공유 확인)
   python3 scripts/check_contract.py --tier changed
   python3 scripts/dwm.py doctor
   ```
   각 웨이브(W1→W5)의 산출물은 committed fixture로 재검증 가능해야 하며, 위 명령들이 verdict `pass`/`A1`/`A2` 또는 **의도적으로 문서화된 blocked**를 내야 한다.
4. **Fail-closed 종결:** 미지 isolation fact, hash mismatch, stale `source_fixture_hash`, 범위 밖 touched file, openssl 부재(`ERR_OPENSSL_UNAVAILABLE`), 서명 불일치, `prev_capture` 단절 중 하나라도 있으면 `A0`/`blocked`/`refuted`. 부분점수 없음. witnessd UX는 이 verdict가 나오기 전까지 lane을 `evidence-pending`으로만 표기한다(§4.0-5).

### 4.14 witnessd emitter 수용 기준 (구현 검증 체크리스트)

에이전트가 그대로 구현·검증할 수 있도록, emitter는 아래를 **모두** 통과해야 한다(각 항목은 위 함수로 검증 가능):

- [ ] `witnessd/canonical.py`가 `claim_gate.canonical_hash`와 바이트 동일(고정 벡터 테스트).
- [ ] observer 출력이 runner sandbox 안이면 emitter가 `ERR_OBSERVER_NOT_SEPARATED`로 **쓰기 전에** 중단(파일 미생성 확인).
- [ ] shell lane 1개로 A1 매니페스트 방출 → `validate_capture_manifest` 빈 리스트.
- [ ] uid 격리(runner_uid≠observer_uid≠0, observer_dir_writable_by_runner=false) 방출 → A2, `verify_isolation_boundary().boundary is True`.
- [ ] `allowed_touched_files` 밖 파일을 touched로 넣으면 `"unexpected touched files"`로 refute(negative test).
- [ ] DSSE provenance가 `validate_trusted_observer_provenance` 통과(openssl 있을 때) / 없을 때 `ERR_OPENSSL_UNAVAILABLE`.
- [ ] 서명 번들이 `ingest_signed_evidence_bundle`에서 `signature_verified=true` & 전 subject `verified`.
- [ ] 3-스텝 체인(genesis→link→link)이 `verify_capture_chain` decision `pass`; 중간 drop/reorder/tamper는 `blocked`.
- [ ] `evidence-contract.json` + `git-diff-*`/`exit-code.txt`가 `validate_evidence_contract` 빈 리스트; test-weakening 패치는 `ERR_TEST_WEAKENED`.
- [ ] overlap lane 2개 + merge receipt가 `build_team_ledger_verdict` decision `pass`; merge receipt 누락 시 blocked.

---

## 5. 구현 웨이브 (W1–W5)

이 섹션은 witnessd 런타임을 다섯 개의 웨이브로 쪼개, 각 웨이브를 에이전트가 그대로 구현할 수 있는 수준으로 규정한다. 핵심 규율은 하나다 — **능력(capability)은 언제나 이미 증명 가능한 증거 방출(evidence emission) 위에만 얹는다.** 어떤 웨이브도 "먼저 실행 UX를 쌓고 증거는 나중에 bolt-on" 하지 않는다. 각 웨이브의 완료는 witnessd 자신의 self-report가 아니라, **별도 repo인 Depone(`keelplane`)의 non-executing validator가 witnessd가 방출한 바이트에서 verdict를 재도출**하는 것으로만 정의된다.

> **Decision (재검토 가능) — 진행 방식은 Approach C(evidence-emitter 우선)로 고정, 웨이브 순서 W1→W5는 불변.**
> Rationale: 설계 리포트 §5가 실증하듯 Approach B(능력 우선, 증거 나중)는 teardown이 지적한 모든 실패모드(OMX split-brain, zombie `active:true`, `omx doctor` false-positive, OMO `<promise>VERIFIED</promise>` 파싱)를 그대로 상속한다. 증거를 나중에 붙이는 것은 2026 문헌이 조롱한 bolt-on 관측성 안티패턴이다. 유일하게 방어 가능한 해자는 evidence-native이므로, 능력 증가분이 항상 자동으로 falsifiable 증거를 남기도록 순서를 못박는다. 이렇게 해야 "aggressive autonomy가 신뢰 리스크가 아니다"라는 논제가 매 웨이브에서 유지된다.

> **Decision (재검토 가능) — 이름은 witnessd(CLI `witnessd`), Depone과 물리적으로 분리된 repo 2개.**
> Rationale: "실행하지 않는 검증기의 독립성"이 신뢰의 근거다. 검증기가 실행기와 같은 repo·같은 프로세스·같은 권한에 있으면 그 독립성 주장이 무너진다. 두 repo가 공유하는 유일한 계약은 canonical hashing 규약과 스키마뿐이며, witnessd가 오염돼도 evidence 밖의 public key를 위조하지 못하면 A1/A2 assurance나 그 위에 얹히는 서명된 report-level 신뢰를 조립할 수 없다.

## 5.0 웨이브 간 의존·순서 불변식

모든 웨이브에 걸쳐 아래 불변식이 성립해야 하며, 각 웨이브의 Acceptance Bar는 이 불변식을 자동 검사에 포함한다.

1. **단조성(monotonicity) — 능력 추가는 assurance floor를 절대 낮추지 않는다.** W_n에서 방출한 아티팩트는 W_1…W_{n-1}의 모든 Depone validator를 여전히 통과해야 한다. 즉 W3의 team run이 만든 각 lane capture는 W1의 `validate_capture_manifest`와 `verify_capture_chain`을 그대로 통과한다.
2. **체인 연속성.** witnessd 전체 수명에서 capture-manifest는 하나의 append-only `prev_capture_hash` 체인으로 묶인다(상태전이 이벤트를 담는 runlog 체인과는 별개 — §2.2). genesis만 `prev_capture_hash == null`이고, 이후 모든 capture-manifest의 `prev_capture_hash`는 직전 **manifest**의 canonical hash(`sha256(json.dumps(obj, sort_keys=True, separators=(",",":")).encode("utf-8")).hexdigest()`)와 정확히 일치한다. drop/reorder/tamper는 `evidence_substrate.verify_capture_chain`(입력: capture-manifest 리스트)에서 blocked.
3. **역할 분리 불변식(Depone 계승).** worker는 코드를 쓰지만 자기 성공을 seal·validate 못 한다. Evidence Emitter만이 SoT(append-only signed event log)에 쓰는 유일한 지점이다. verifier(Depone)는 assurance를 상향하지 못한다 — 모든 Depone verdict의 `boundary.raises_assurance == false`, `boundary.approves_merge == false`.
4. **fail-closed 총칙.** 미지의 isolation fact, hash mismatch, stale `source_fixture_hash`, 범위 밖 touched file, 서명 부재(`ERR_OPENSSL_UNAVAILABLE`/빈 `signatures`), prev_capture 단절 → 항상 A0/blocked/refuted. 부분점수 없음.
5. **순서 의존.** W2는 W1의 capture/chain/DSSE 위에서만 spawn별 isolation facts를 얹을 수 있다. W3는 W2의 spawn별 isolation facts가 있어야 각 lane에 A2를 부여할 수 있다. W4 어댑터는 W3의 lane receipt 스키마를 재사용한다. W5의 hard pause/kill-switch는 W2의 supervised 프로세스 모델이 있어야 실제로 자식을 죽일 수 있고, learning capture는 W1 체인에 링크되며 W4의 비용·라우팅 계약이 있어야 안전하게 자율성을 연다.

**공통 Acceptance 게이트(모든 웨이브 공유).** 각 웨이브는 다음 3중 게이트를 통과해야 완료로 인정된다. Depone spec의 revalidatable committed fixture 스타일을 그대로 재사용한다.

- **(G1) witnessd self-test 전부 green.** 해당 웨이브가 건드린 모든 witnessd 모듈은 `--self-test`를 갖고, `witnessd self-test --all`이 `N/N passed`로 종료(exit 0).
- **(G2) committed fixture를 Depone validator로 재검증.** 웨이브 산출물은 witnessd repo의 `fixtures/wN/` 아래에 committed되고, `scripts/revalidate_wN.py`가 **설치된 Depone 패키지의 validator를 import**해 fixture 바이트에서 verdict를 재도출한다(아래 각 웨이브의 Acceptance Bar에 정확한 함수·기대값 명시). 이 스크립트는 `python3 scripts/revalidate_wN.py` 단독 실행으로 exit 0.
- **(G3) Depone contract 게이트.** witnessd가 방출한 evidence를 Depone repo에서 소비해 `python scripts/check_contract.py --tier changed`와 `python scripts/dwm.py doctor`가 red 없이 통과. 이 게이트가 두 repo의 계약 공유(canonical_hash 규약·스키마)를 병렬 진화 상태로 유지한다.

---

## 5.1 W1 — Evidence substrate + observer 분리 + A1/A2 실증(shell 어댑터)

### 목표
"완료(done)를 관측자-서명 바이트로 정의"하는 최소 end-to-end 루프를 첫날부터 닫는다. shell 어댑터 하나로 A1을, uid 격리가 가능한 호스트에서 A2까지 실증한다. 이 웨이브가 논제 전체의 증명이다.

### 범위(무엇을 만드나)
- **단일 append-only 서명 이벤트 로그(M1).** 모든 상태전이가 하나의 hash-chained 로그에 append되고, run-state/team-state 류는 로그의 pure projection. 별도 mutable JSON store 금지.
- **shell 어댑터 1개.** witnessd가 셸 lane을 실행하고, 별도 observer 프로세스가 runner 샌드박스 **밖** 디렉터리에 observer_capture를 기록.
- **capture-manifest + prev_capture 체인 + operator Ed25519 DSSE 서명.**
- **runner-receipt(E5)** — shell lane도 `build_runner_receipt`로 방출(`runner_kind="manual"`), 그리고 **evidence-substrate 번들(E7)** — in-toto Statement v1 + DSSE + 인라인 OTel span. 둘 다 W1 범위이며, §1.8.1의 최상위 성공기준("evidence bundle을 `ingest_signed_evidence_bundle`이 재검증")이 W1에서 실제로 닫힌다.
- **per-lane evidence-contract.json(E9)** + `git-diff-name-only.txt`/`git-diff.patch`/`exit-code.txt`.

### 새 컴포넌트(§2 참조)
Orchestrator(최소), shell Adapter, Observer 프로세스, Evidence Emitter, Event Log. Session Store/Supervisor는 W2로 미룬다.

### 방출/검증 증거(§4 참조)
| 증거 | witnessd 방출 | Depone 재검증 함수 / 기대값 |
|---|---|---|
| E1 관측자-분리 캡처 | observer가 `--out`/`--log`을 runner 샌드박스 밖에 기록 | `observe.enforce_observer_separation` — 밖이 아니면 `PairedRunError("ERR_OBSERVER_NOT_SEPARATED")` |
| E2 capture-manifest | `build_capture_manifest`로 kind=`agent-fabric-capture-manifest`, schema `1.0` | `capture_bridge.validate_capture_manifest` → `[]` (에러 없음), `source_fixture_hash == _sha256_json(fixture)`, `observer_capture_hash == _sha256_json(observer_capture)` |
| E3 command_receipts/diff/test_output | observer_capture에 6개 required field | `observed_by == "depone-observer"`; `command_receipts` 비어있지 않음 & 각 원소 `command` + int `exit_code`; `test_output.status ∈ {not-run,passed,failed,error}`; `touched_files ⊆ allowed_touched_files` |
| E5 runner-receipt | shell lane도 `build_runner_receipt`로 kind=`agent-fabric-runner-receipt`, schema `1.0` 방출(shell은 `runner_kind="manual"`) | `paired_run.validate_runner_receipt` → `[]`; `source_hashes.receipt == canonical_hash(receipt-without-source_hashes)` |
| E7 evidence-substrate 번들 + OTel | `evidence_substrate.build_evidence_bundle`로 in-toto Statement v1 + DSSE + 인라인 `otel_spans`(§4.9), operator 키로 서명 | `evidence_substrate.ingest_signed_evidence_bundle(bundle, public_key, artifact_paths)` → `signature_verified=true`, 전 subject `verified`; 미서명이면 `signatures==[]`로 정직 표기 |
| E6 trusted-observer-provenance | `build_signed_trusted_observer_provenance`(operator Ed25519 DSSE) | `observer_provenance.validate_trusted_observer_provenance(..., public_key_path=<out-of-band>)` → `[]` |
| E8 prev_capture 체인 | manifest에 `prev_capture_hash` | `evidence_substrate.verify_capture_chain` — genesis head null, 각 prev == 직전 canonical hash |
| E9 evidence-contract | `evidence-contract.json`(schema `v105.verify_wedge`) + diff/exit 아티팩트 | `verify/evidence_contract.validate_evidence_contract` — ≥1 enforcement directive, git-diff.patch 구조적 test-weakening 탐지 |

> **Decision (재검토 가능) — 서명은 operator-held Ed25519 DSSE. sign은 witnessd 런타임, verify는 Depone가 public key로. private key는 verify 경로에 절대 없음. Sigstore Fulcio keyless + Rekor는 명시적으로 deferred.**
> Rationale: `sign.operator_key_signature_boundary()`가 못박듯 이 서명의 신뢰는 operator-held key와 배포된 public key에 뿌리를 두며 `keyless_identity == false`, `transparency_logged == false`다. witnessd는 signing step을 swappable로 두되(추후 Fulcio/Rekor 교체 가능), 절대 keyless 시맨틱을 주장하지 않는다. public key는 `DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE`로 out-of-band 전달되며 evidence 디렉터리 밖에 있어야만 유효.

> **Decision (재검토 가능) — 완료 UX는 "evidence-pending"을 하드 규칙으로 강제. "VERIFIED/DONE/COMPLETE" 자기보고 문자열을 SoT로 표시하는 것을 금지.**
> Rationale: 우리가 조롱한 실패모드(OMO `<promise>VERIFIED</promise>`, `task_update`가 검증 없이 completed 마킹)의 재발 방지. Depone 외부 체크가 통과하기 전까지 CLI/로그/상태 projection은 오직 `evidence-pending`만 표시한다. `witnessd status`는 성공 문자열을 절대 생성하지 않고 "N captures pending Depone verification"만 낸다.

### Acceptance Bar (W1 완료 정의)
1. `witnessd run --adapter shell --runner-sandbox <S> --out <O>/capture.json --log <O>/verify.log …` 실행 시, `<O>`가 `<S>` 밖일 때만 성공하고, 안이면 `ERR_OBSERVER_NOT_SEPARATED`로 fail-closed하며 **observer 출력 파일을 쓰지 않는다**(부분 산출 금지).
2. committed fixture `fixtures/w1/capture-manifest.json`(A1)과 `fixtures/w1/capture-manifest-a2.json`(A2, uid 격리 호스트에서 생성)이 존재.
3. `python3 scripts/revalidate_w1.py`가 다음을 모두 assert하고 exit 0:
   - A1 manifest: `validate_capture_manifest(m) == []` and `m["assurance"] == "A1-local-observed"`.
   - A2 manifest: `validate_capture_manifest(m) == []` and `m["assurance"] == "A2-isolated-observed"`.
   - 체인 fixture `fixtures/w1/chain/*.json`: `verify_capture_chain(...)`가 통과, reorder/tamper 변형본은 blocked.
   - DSSE: `verify_signed_bundle(bundle, public_key)` True; 상위 `assurance`를 (존재하지 않는) `A3-*`로 위조하면 False.
   - runner-receipt(E5): `validate_runner_receipt(receipt) == []`, `source_hashes.receipt == canonical_hash(receipt-without-source_hashes)`.
   - evidence-substrate 번들(E7): `ingest_signed_evidence_bundle(bundle, public_key, artifact_paths)`가 `signature_verified == True`이고 전 subject `verified`; 번들 `assurance`가 manifest assurance(상한 A2)와 동일; 인라인 `otel_spans` 구조 검증 통과.
   - provenance: `validate_trusted_observer_provenance(m, evidence_path=..., provenance=[p], public_key_path=<out-of-band>) == []`.
   - evidence-contract: `validate_evidence_contract(...)`가 enforcement directive ≥1 확인.
4. **negative fixtures**(tamper 회귀)도 committed: `observer_capture_hash mismatch`, `source_fixture_hash is stale`, `unexpected touched files`가 각각 검출됨을 assert.
5. 공통 게이트 G1/G2/G3 green.

### fail-closed 규칙(W1)
- observer 출력이 runner 샌드박스 안 → `ERR_OBSERVER_NOT_SEPARATED`, 산출 없음.
- `observed_by != "depone-observer"`, 빈 `command_receipts`, non-int `exit_code`, 범위 밖 `touched_files`/`changed_files` → manifest invalid.
- fixture hash mismatch / stale `source_fixture_hash` / manifest tamper → invalid.
- `openssl` 부재 → `ERR_OPENSSL_UNAVAILABLE`; 서명 실패 → `ERR_DSSE_SIGN_FAILED`. 서명 없는 번들은 `signatures == []`로 정확히 표기되고 Depone에서 blocked(assurance 미상향).

### Residual risk → 다음 웨이브
W1의 A2는 "밖 디렉터리 + operator 서명"까지지만, **worker 프로세스 생사(liveness)는 아직 증명되지 않는다** — 셸 lane은 단발 실행이라 zombie 문제를 노출하지 않는다. observer 분리 모델도 W1에서는 same-uid(`separate-process-observer-owned-dir`, `privilege_boundary=false`)까지가 기본이며, uid 경계는 실증만 했다. W2가 supervised 프로세스 + heartbeat로 liveness를 증명하고, spawn마다 uid isolation facts를 강제해 A2를 상시화한다.

---

## 5.2 W2 — Supervised worker + heartbeat liveness + durable session (A2 상시화)

### 목표
"조용히 죽은 팀"(OMX zombie `%199` + `omx doctor` false-positive)을 구조적으로 불가능하게 만든다. worker는 supervised 자식 프로세스이고, `active`는 자기보고가 아니라 "N초 내 서명된 heartbeat 관측"에서 파생되며, spawn마다 uid isolation facts가 A2를 확립한다.

### 범위(무엇을 만드나)
- **supervised 프로세스 모델(M3).** tmux `send-keys` IPC 폐기. worker를 exit code/SIGCHLD를 받는 durable 자식 프로세스로 spawn하고 supervisor가 감시.
- **증명되는 liveness(M2).** worker/leader가 bounded interval로 로그에 서명된 heartbeat를 방출. `active` = "마지막 heartbeat가 임계 내" 파생.
- **크래시-세이프 durable 세션(M4).** last prompt, tool-call cursor, worktree를 영속화 → ID로 다른 host/reboot에서 재개.
- **spawn별 isolation facts(E4).** 각 worker spawn마다 `probe_isolation_facts`로 uid 경계 실측, manifest에 `isolation` + `isolation_hash` 바인딩.

> **Decision (재검토 가능) — A2 격리는 uid 모델을 1급으로 채택. `runner_uid != observer_uid != 0` 이고 `observer_dir`은 0700 & runner-not-writable. docker 모델(`container-boundary-unwritable-observer-dir`)은 후속 옵션으로 지원하되 기본 아님.**
> Rationale: `isolation.verify_isolation_boundary`가 `boundary: true`를 내려면 uid 모델에서 (a) `runner_uid`·`observer_uid` 둘 다 int이고 서로 다름, (b) `runner_uid != 0`(root는 권한 경계 성립 불가), (c) `observer_dir_writable_by_runner == False`가 필요하다. docker 모델은 OpenHands가 겪은 "잠긴 기업 머신의 Docker-in-Docker 마찰"을 상속하므로 기본에서 뺀다. uid 모델은 가볍고, 이미 Depone A2-first fixture(`deponerun` uid 1002 / observer uid 1001)로 실측 검증됐다.

### 새 컴포넌트(§2 참조)
Worker Supervisor(durable, SIGCHLD/exit 수신), Scheduler(restart-safe, no tmux), Session Store(crash-safe, ID resume). isolation probe는 observer 프로세스에 편입.

### 방출/검증 증거(§4 참조)
- E4 isolation facts: `probe_isolation_facts(observer_dir, runner_uid=<runner receipt>)` → facts. Depone `verify_isolation_boundary(facts)["boundary"] is True`여야 A2. same-uid/root/writable/미지 fact는 A1로 fail-closed.
- capture manifest의 `assurance == "A2-isolated-observed"`, `isolation_hash == _sha256_json(isolation)`, 그리고 `_check_a2_manifest`가 facts에서 boundary 재도출(flag flip 위조 불가).
- heartbeat/liveness는 witnessd **runlog 체인**(`prev_event_hash`, kind `witnessd-runlog-event`, §6.0.3)에 append되며, W1의 canonical_hash·DSSE 규약을 그대로 재사용한다. 이는 capture-manifest 체인(`prev_capture_hash`, Depone `verify_capture_chain` 대상)과 **별개의 체인**이다 — heartbeat/dispatch 이벤트는 capture-manifest가 아니므로 `verify_capture_chain`에 넣지 않는다(§2.2).

### Acceptance Bar (W2 완료 정의)
1. **liveness 회귀:** worker 프로세스를 강제 kill한 뒤 `witnessd status`는 heartbeat 임계 초과를 관측해 즉시 `dead`로 파생해야 한다. **"active:true" 잔존 금지** — committed fixture `fixtures/w2/liveness-killed.jsonl`(heartbeat 끊긴 로그)에 대해 `scripts/revalidate_w2.py`가 파생 상태 `dead`를 assert. (OMX false-positive의 안티-회귀.)
2. **재개 회귀:** durable 세션을 ID로 재개해 tool-call cursor가 보존됨을 assert(재개 후 last cursor == 저장 cursor).
3. **A2 회귀:** `fixtures/w2/capture-manifest-a2.json`에 대해 `validate_capture_manifest == []` and `assurance == "A2-isolated-observed"`. same-uid 변형(`runner_uid == observer_uid`)은 A1로 강등, `isolation.runner_uid`를 flip해 `isolation_hash`를 재계산한 forged fixture는 `"does not establish a privilege boundary"`로 blocked.
4. G1/G2/G3 green.

### fail-closed 규칙(W2)
- heartbeat 부재/임계 초과 → `active` 파생 불가, `dead`. 헬스체크가 dead 상태에서 "all clear"를 낼 수 없다(구조적으로 파생값이라).
- `runner_uid == observer_uid`, `runner_uid == 0`, `observer_dir_writable_by_runner != False`, 미지/부분 facts, `unknown isolation model` → `boundary: False` → manifest는 A1을 넘지 못함.
- durable 세션 복원 실패(unreadable state) → fail-safe: 덮어쓰지 않고 blocked, 유령 재개 금지.

### Residual risk → 다음 웨이브
W2는 단일 worker의 liveness·격리를 증명하지만, **다중 lane 병렬 시 파일 충돌·머지 안전은 아직 없다.** W3가 auto worktree + ownership-region lock + lane receipt + team-ledger fan-in으로 병렬 격리와 머지 증거를 닫는다.

---

## 5.3 W3 — Auto worktree + ownership lock + lane receipt + team-ledger fan-in

### 목표
"팀"을 켠다. 병렬 lane이 파일 소유 region을 런타임 강제로 claim/release하고, 각 lane이 read-only worktree receipt를 남기며, overlap 시 passing merge receipt 없이는 머지가 불가능하도록 team ledger가 fan-in한다.

### 범위(무엇을 만드나)
- **auto worktree** + **런타임 강제 ownership-region 락(M5).** dispatch 전 파일/모듈 region claim, 각 claim/release가 로그 event.
- **worktree lane receipt(E5).** lane마다 `build_worktree_lane_receipt`로 base/head commit·changed_files 기록. fan-in 대상 lane은 `dirty == False`.
- **team-ledger fan-in(E10) + merge receipt.** `build_team_ledger_verdict`로 lane별 레코드를 집계, overlap touched files 존재 시 passing merge receipt 필수.

### 새 컴포넌트(§2 참조)
Worktree Manager(auto worktree, lane receipt, lock), Team Ledger fan-in(Orchestrator 확장).

### 방출/검증 증거(§4 참조)
- E5 worktree lane receipt: kind=`depone-worktree-lane-receipt`, schema `0.1`. `worktree_receipt`가 read-only git 상태로 생성(`boundary.git_read_only == True`). fan-in 대상은 `dirty == False`, `changed_files`는 §4.7 규칙(overlap+merge-required lane은 `touched_files`와 equality, 그 외는 superset).
- E10 team ledger: kind=`depone-team-ledger`, schema `0.1`. `team_ledger.build_team_ledger_verdict(ledger)` → verdict kind `depone-team-ledger-verdict`. `overlapping_touched_files`가 비어있지 않으면 `merge_receipt` required. verdict `decision ∈ {pass, blocked, blocked-explicit}`, `boundary.raises_assurance == false`, `boundary.approves_merge == false`.

### Acceptance Bar (W3 완료 정의)
1. committed `fixtures/w3/team-ledger.json`(disjoint lanes, pass)과 `fixtures/w3/team-ledger-overlap.json`(겹치는 touched files, merge receipt 없음).
2. `scripts/revalidate_w3.py`:
   - disjoint: `build_team_ledger_verdict(m)["decision"] == "pass"`, `overlapping_touched_files == []`.
   - overlap without passing merge receipt: `decision == "blocked"`, 에러에 `ERR_TEAM_LEDGER_*`(merge receipt 관련) 포함.
   - 각 lane receipt: kind/schema 일치, fan-in lane `dirty == False`, `changed_files`가 lane의 `allowed_touched_files`와 정합.
3. **ownership-lock 회귀:** 두 lane이 같은 region을 claim하면 두 번째 dispatch가 거부되고 로그에 claim-conflict event가 남음을 assert(committed `fixtures/w3/claim-conflict.jsonl`).
4. **단조성 회귀:** 각 lane capture가 W1 `validate_capture_manifest` + `verify_capture_chain`을 여전히 통과, W2 A2 격리를 각 lane이 유지.
5. G1/G2/G3 green.

### fail-closed 규칙(W3)
- fan-in 대상 worktree가 dirty(`dirty == True`) → lane receipt fan-in 거부.
- overlap touched files 존재 + passing merge receipt 부재 → team ledger verdict `blocked`, 머지 금지.
- worktree receipt 생성 중 git 실패/절대경로/`..` → `ERR_WORKTREE_RECEIPT_*`로 fail-closed.
- ledger 헤더 kind/schema 불일치, lane_id 중복, touched_files 누락 → `ERR_TEAM_LEDGER_*`, verdict blocked.

### Residual risk → 다음 웨이브
W3는 shell/단일 substrate 기준으로만 병렬을 증명한다. **실제 substrate(Codex/Claude/OpenCode) 다양성, 모델 라우팅, 비용 폭주는 아직 미결.** W4가 어댑터를 확장하고 라우팅을 solved abstraction으로, 비용을 서킷브레이커로 닫는다.

---

## 5.4 W4 — Codex/Claude/OpenCode 어댑터 + 모델 라우팅 solved abstraction + 비용 서킷브레이커

### 목표
능력 breadth를 연다. 여러 substrate 어댑터가 동일한 runner-receipt 스키마를 방출해 Depone가 어댑터 무관하게 검증하게 하고, 모델 라우팅을 버그 원천이 아닌 solved abstraction으로, 비용을 하드 상한으로 만든다.

### 범위(무엇을 만드나)
- **어댑터 확장:** Codex CLI(W4의 첫 어댑터), 이어 Claude Code(Task/subagent), OpenCode. 각 어댑터는 `build_runner_receipt` 스키마(kind=`agent-fabric-runner-receipt`, schema `1.0`)를 방출.
- **모델 라우팅 solved abstraction(M8):** quick/agentic/frontier 라우팅 + `model_not_supported` 재시도 + per-task concurrency key + graceful degradation 계약. silent task death 금지.
- **토큰/비용 서킷브레이커(M10):** per-task 토큰·달러 예측, 하드 상한, delegation depth/spend 예산, 실측을 같은 event log에 기록.

> **Decision (재검토 가능) — 첫 어댑터는 shell(W1) → Codex(W4) → Claude Code/OpenCode 순. OMX/LazyCodex와 동시실행 시 상태 격리는 필수 요건.**
> Rationale: Codex 생태계(OMX/LazyCodex) 흡수가 채택 유입에 가장 크지만, teardown 실측상 OMX/LazyCodex 동시실행이 상태를 조용히 오염한다. 따라서 witnessd Codex 어댑터는 자기 event log·session store·worktree 네임스페이스를 그들과 물리적으로 분리(별도 상태 디렉터리 + lock)하며, 공존은 하되 상태 공유는 절대 하지 않는다.

**runner_kind 계약 노트(cross-repo).** 현재 Depone `paired_run.VALID_RUNNERS == {"codex-cli", "manual"}`이다. Codex 어댑터는 `runner_kind="codex-cli"`로 즉시 검증 가능하다. Claude Code/OpenCode 어댑터를 A-등급으로 승격하려면 Depone의 `VALID_RUNNERS` 확장이 선행되어야 하며(공유 계약 변경), 그 전까지 해당 어댑터는 `runner_kind="manual"`로 방출한다. **이 확장은 Depone repo에서 별도 contract PR로 게이트**되며, witnessd가 임의 runner_kind를 위조해 검증을 통과시킬 수 없다(fail-closed: 미지 runner_kind → receipt invalid).

### 새 컴포넌트(§2 참조)
Adapter 계층(Codex/Claude/OpenCode), 모델 라우터, 비용 서킷브레이커(Orchestrator 예산 트리).

### 방출/검증 증거(§4 참조)
- runner receipt: `validate_runner_receipt(receipt) == []`, `arm ∈ {direct, governed}`, `runner_kind ∈ VALID_RUNNERS`, `invocation` 비어있지 않음, `source_hashes.receipt == canonical_hash(receipt-without-source_hashes)`(§4.6).
- 비용/라우팅 실측은 witnessd **runlog 체인**(`prev_event_hash`, §6.0.3; capture-manifest 체인과 별개)에 event로 append한다. E7 evidence-substrate 번들의 인라인 OTel GenAI span에 라우팅 메타를 정적 span으로 기록하되 **usage 필드를 날조하지 않는다**(`build_otel_genai_spans`가 발명 금지).

### Acceptance Bar (W4 완료 정의)
1. Codex 어댑터로 실제 lane 실행 → `fixtures/w4/runner-receipt-codex.json` committed, `validate_runner_receipt == []`.
2. Claude/OpenCode 어댑터 fixture는 `runner_kind="manual"`(Depone `VALID_RUNNERS` 확장 전) 또는 확장된 값으로 검증 통과.
3. **라우팅 회귀:** `model_not_supported` 주입 시 어댑터가 silent stop 없이 재시도/graceful degradation 후 lane을 blocked로 명시 종료(committed `fixtures/w4/route-degrade.jsonl`); 로그에 `evidence-pending`만 표기, 성공 문자열 금지.
4. **비용 회귀:** per-task 상한 초과 시 서킷브레이커가 lane을 하드 중단하고 예산 초과 event를 로그에 남김을 assert.
5. **상태 격리 회귀:** OMX/LazyCodex 상태 디렉터리를 mock한 상태에서 witnessd Codex 어댑터가 자기 네임스페이스만 쓰고 외부 store를 건드리지 않음을 assert.
6. G1/G2/G3 green + 단조성(각 어댑터 capture가 W1~W3 validator 통과).

### fail-closed 규칙(W4)
- 미지 `runner_kind`/`arm`, 빈 `invocation`, `source_hashes` 불일치 → receipt invalid.
- 라우팅 실패가 재시도·degradation 계약을 소진하면 silent stop이 아니라 명시적 blocked로 종료.
- 예산(토큰/달러/depth) 초과 → 하드 중단, 진행 중 산출은 evidence-pending으로만 표기.
- 어댑터가 외부(OMX/LazyCodex) 상태를 쓰려 하면 거부.

### Residual risk → 다음 웨이브
W4는 능력을 넓히지만 **자율성 안전판(사용자 개입 우선순위, 전체 정지)과 학습의 provenance는 아직 없다.** W5가 hard pause·kill-switch·자동 학습 캡처로 자율성을 안전하게 최대화한다.

---

## 5.5 W5 — 자동 학습 캡처 + hard pause + kill-switch

### 목표
"aggressive autonomy가 신뢰 리스크가 아니다"의 마지막 조각을 채운다. 반복 교정이 provenance와 함께 버전드 delta로 승격되고, 사용자 개입은 어떤 auto-continuation도 override할 수 없으며, 전체 harness를 테스트된 kill-switch로 즉시 정지할 수 있다.

### 범위(무엇을 만드나)
- **자동 학습 캡처 native(M9).** 반복 교정/패턴을 provenance(어느 run·어느 evidence·어느 승인)와 함께 버전드 AGENTS.md/skill delta로 승격. ephemeral↔persistent 갭 제거.
- **하드 pause/interrupt 경계(M6).** 사용자 "wait"/"stop"은 즉시 모든 continuation hook을 중단하고 명시적 재활성화를 요구. auto-continuation override 불가.
- **테스트된 kill-switch + 원자적 install/upgrade(M11).** 전체 harness pause CLI, unreadable config에 fail-safe(덮어쓰기 금지), orphan bin shim 없음.

### 새 컴포넌트(§2 참조)
Learning Capture(provenance 링크), Pause/Interrupt 경계(Supervisor 확장), Kill-switch CLI + atomic installer.

### 방출/검증 증거(§4 참조)
- 학습 delta는 그 자체가 append-only 체인의 event로 방출되며, provenance 포인터(해당 capture의 canonical hash + provenance record + 승인 event)를 담아 **어느 서명 증거가 이 학습을 정당화했는가**가 재도출 가능. 승인 없는 학습 delta는 blocked(fail-closed).
- pause/kill 이벤트도 서명된 로그 event로 남아 사후 감사 가능(W1 DSSE·체인 재사용).

### Acceptance Bar (W5 완료 정의)
1. **pause 회귀(안티-OMO):** worker가 continuation hook로 파일 편집 중일 때 사용자 "wait" 주입 → 200ms 이내 모든 continuation 중단, 이후 편집·commit event 없음을 committed `fixtures/w5/pause-override.jsonl`로 assert. (OMO `todo-continuation-enforcer` #89 재발 방지.)
2. **kill-switch 회귀:** `witnessd kill --all` 후 supervisor가 모든 자식 프로세스에 종료를 보내고, heartbeat 파생 상태가 전부 `dead`, 로그에 kill event 기록됨을 assert.
3. **학습 provenance 회귀:** `fixtures/w5/learning-delta.json`이 승인·증거 포인터를 갖고, `scripts/revalidate_w5.py`가 포인터가 실제 committed capture의 canonical hash와 일치함을 assert. 포인터 없는 delta 변형본은 blocked.
4. **installer 회귀:** unreadable config에서 installer가 덮어쓰지 않고 fail-safe로 중단, orphan shim 미생성 assert.
5. G1/G2/G3 green + 단조성.

### fail-closed 규칙(W5)
- 학습 delta에 승인/증거 provenance 부재 → blocked, 승격 금지.
- pause 상태에서 continuation hook가 실행되려 하면 거부(명시적 재활성화 event 없이는 재개 불가).
- unreadable/손상 config → 덮어쓰기 금지, fail-safe 중단.
- kill-switch가 자식을 확실히 종료하지 못하면 상태를 `active`로 파생하지 않는다(W2 liveness 규약).

### Residual risk → 이후(웨이브 밖)
W5 완료 후 남는 것은 **keyless 서명 축(Sigstore Fulcio keyless + Rekor transparency log)** 로의 서명 업그레이드와 docker/container isolation 모델의 1급 승격이다. 둘 다 이번 W1–W5 범위에서 명시적으로 deferred이며, signing step과 isolation probe를 swappable로 설계해 둔 덕분에 계약 변경 없이 후속 웨이브에서 교체 가능하다. 이때도 불변식(§5.0)은 그대로 유지된다 — 새 서명·격리 모델도 Depone validator가 바이트에서 재도출하지 못하면 assurance를 얻지 못한다.

---

## 6. 에지 케이스 · 예외 처리 (포괄)

### 6.0 공통 규율 (모든 케이스가 상속하는 불변식)

이 섹션의 모든 처리는 §3의 event-log substrate와 fail-closed 규칙, §4의 Depone 계약과 **일관해야 한다**. 개별 케이스를 읽기 전에 다음 공통 규율을 먼저 확정한다. 이것은 재검토 대상이 아닌 하드 규칙이다.

**Decision 6.0.1 (fail-closed severity lattice).** witnessd가 관측·방출·재개 중 마주치는 모든 예외는 아래 격자의 **가장 낮은(가장 안전한) 값**으로 강등된다. 상향은 오직 Depone이 바이트에서 재도출할 때만 일어난다.

```
A2-isolated-observed  (isolation boundary 증명됨)
  ⊐ A1-local-observed (observer 분리 캡처만)
    ⊐ A0-claims-only  (self-report만, 관측자 증거 없음)
      ⊐ blocked       (Depone이 바이트에서 검증을 완료할 수 없음)
        ⊐ refuted     (증거가 계약 위반을 적극 증명)
```

경계선을 명확히 한다(§6.6 표는 이 기준으로 정렬한다):
- **`blocked` = hash / 서명 / 체인 무결성의 구조적 실패.** 구체적으로 `source_fixture_hash` mismatch, `observer_capture_hash` mismatch, **stale `source_fixture_hash`**, `isolation_hash` mismatch, 서명 부재/검증 불가, `prev_capture_hash` 체인 단절, 구조 붕괴(파싱 불가/누락 subject). 이는 `evidence_substrate.ingest_signed_evidence_bundle` / `verify_capture_chain` / `capture_bridge`의 hash·서명 검사가 `blocked`(또는 manifest invalid)로 떨어뜨리는 상태다. "무결성을 확인할 수 없다"가 blocked의 본질이다.
- **`refuted` = 관측된 행위가 계약을 적극 위반.** 범위 밖 `touched_files`/`changed_files`, forbidden 파일 편집, test-weakening, overlap lane의 merge receipt 부재 등 — `capture_bridge`(범위 밖 쓰기) / `evidence_contract`(test 약화·금지 파일) / `team_ledger`(overlap-merge)가 위반을 **적극 탐지**한 상태다. "증거가 위반을 증명한다"가 refuted의 본질이다.
- 부분점수는 없다. 어떤 예외도 "A1의 절반" 같은 중간값을 만들지 않는다.

**Decision 6.0.2 (2축 상태 모델 + evidence-pending as terminal-until-verified).** witnessd의 run 상태는 **직교하는 두 축**으로 나뉘며, 이 둘을 섞지 않는다.
- **(축 A) assurance 상태** — Depone이 바이트에서 재도출하는 증거 등급/판정: `{evidence-pending, emit-refused, A0, A1, A2, blocked, refuted}` 중 하나. (A3 없음 — assurance 상한 A2.) Depone 외부 재검증이 A1/A2/pass를 재도출하기 전까지 이 축의 SoT 상태는 최대 `evidence-pending`이다.
- **(축 B) 실행 lifecycle 상태** — 프로세스 생사·제어를 나타내는 런타임 축: `{running, paused, zombie, dead, error}` 중 하나. 이 축은 heartbeat/supervisor projection에서 파생되며(§6.1), assurance 등급을 부여하지 않는다.

한 lane은 두 축의 값을 **동시에** 갖는다(예: lifecycle `paused` + assurance `evidence-pending`). `VERIFIED`/`DONE`/`COMPLETE`/`ORCHESTRATION COMPLETE` 문자열은 두 축 어디에도 존재하지 않는다 — §1이 조롱한 self-report theater의 재발 방지다. worker는 자기 성공을 seal할 수 없고(§4 Roles), verifier는 assurance를 상향할 수 없다(`raises_assurance=false`).

assurance 재도출은 `depone verify`(plan-대조 검증기)가 아니라 다음 실재 경로로 한다(§4.13): `python3 -m depone agent-fabric-observe --self-test` + inline `validate_capture_manifest`/`verify_isolation_boundary`/`verify_capture_chain`, 또는 서브커맨드 `evidence-ingest`/`evidence-chain`. 
검증: 모든 사용자향 상태 출력이 §7.6의 `render_status()` enum을 경유하므로 금칙어가 enum에 부재함을 테스트가 확인한다(`witnessd doctor --forbid-self-report`의 구조적 서브체크).

**Decision 6.0.3 (every exception leaves a signed runlog event).** 모든 예외 경로는 예외를 surface하기 **전에** append-only `runlog`에 이벤트를 append해야 한다. runlog 레코드 스키마:

```json
{"schema_version":"1.0","kind":"witnessd-runlog-event","run_id":"<ulid>",
 "seq":<int>,"event":"<name>","error_code":"ERR_...|null",
 "ts_wall":"<RFC3339>","ts_monotonic":<float>,"payload":{...},
 "prev_event_hash":"<hex|null>","event_hash":"<hex>"}
```

`event_hash = canonical_hash(record without {event_hash})`, `canonical_hash(x) = sha256(json.dumps(x, sort_keys=True, separators=(",",":")).encode("utf-8")).hexdigest()` — Depone의 `claim_gate.canonical_hash` / `capture_bridge._sha256_json`과 **바이트 단위로 동일**하다(이 runlog 체인은 §2.2의 (a) 축이며 Depone `verify_capture_chain`의 대상이 아니다). 삼켜진 예외(runlog 레코드 없이 사라진 실패)는 그 자체로 계약 위반이며, fault-injection 테스트가 이를 잡는다.
검증: 아래 각 케이스마다 명시하는 `witnessd faultkit <case>` 결정적 주입 하네스는 (a) 해당 error_code를 가진 runlog 이벤트가 정확히 하나 append됐고, (b) run 상태가 격자에서 기대한 값 이하이며, (c) Depone 재검증이 기대 verdict를 재도출함을 assert한다.

---

### 6.1 프로세스 · 세션 생명주기

#### 6.1.1 프로세스 크래시 후 ID 재개

- **트리거**: leader/supervisor/worker 프로세스가 SIGKILL·OOM·호스트 reboot로 소멸. tmux pane/호스트가 사라짐.
- **감지**: 각 run은 `.witnessd/runs/<run_id>/` 아래 durable session state(마지막 prompt, tool-call cursor, worktree 경로, 마지막 `runlog.seq`, 마지막 `event_hash`)를 fsync된 상태로 보유(M4). 재기동 시 `witnessd resume <run_id>`가 runlog tail을 읽어 마지막 커밋된 seq를 복원한다. run_id는 ULID(시간 정렬 가능)이며 tmux pane·호스트에 바인딩되지 않는다.
- **처리(fail-closed)**: 재개는 **관측된 사실만** 신뢰한다. runlog가 마지막으로 커밋한 `event_hash`에서 이어지고, 그 이후 부분 기록된 tail(§6.3.3 partial write와 동일 규칙)은 truncate. seal되지 않은(=capture-manifest 미방출) tool-call은 미완료로 간주 → run 상태는 크래시 이전 최대치가 아니라 `evidence-pending`으로 강등. 크래시 시점에 in-flight였던 side-effect 툴콜은 §6.5.3 idempotency key로 double-apply 없이 재평가.
- **복구/재개**: `witnessd resume <run_id>`는 새 프로세스/새 호스트에서 동일 worktree·동일 idempotency namespace로 이어간다. Claude Code Teams의 `/resume`이 유령 teammate에게 메시지 보내는 실패모드를 방지하기 위해, 재개 대상 worker는 heartbeat(§6.1.2) 재확립 전까지 `stale`로 표기되고 dispatch 대상에서 제외된다.
- **남는 증거**: 마지막 커밋된 hash-chained runlog(`crash` 이벤트 포함 시엔 그것까지), durable session snapshot. 재개 자체가 `resume{from_seq,from_hash}` 이벤트로 로그에 남는다.
- **검증**: `witnessd faultkit crash-mid-toolcall` — 툴콜 중간에 `os._exit(137)` 주입 → 새 프로세스에서 resume → run 상태 `evidence-pending`, 잘린 tail은 없고 idempotency로 재적용 0건임을 assert.

#### 6.1.2 zombie 탐지 (heartbeat 만료)

- **트리거**: worker/leader가 crash했으나 supervisor가 SIGCHLD를 놓쳤거나(원격 호스트), 프로세스가 hang. OMX의 `%199` pane이 일주일째 `active:true`인 실패모드.
- **감지**: `active`는 저장된 flag가 **아니라** 파생값이다 — "최근 `heartbeat_ttl_seconds`(기본 30s) 이내에 서명된 heartbeat 이벤트가 runlog에 관측됨"으로만 참(M2). heartbeat 이벤트는 monotonic clock 기반(§6.4.4 clock skew 참조)이며 bounded interval(기본 10s)로 방출.
- **처리(fail-closed)**: TTL 경과 → 해당 lane은 즉시 `zombie`로 파생되고 dispatch에서 배제. `omx doctor`가 죽은 팀에 "18 passed"를 반환한 false-positive를 구조적으로 불가능하게: `witnessd doctor`는 heartbeat 파생 상태만 보고하며 stored flag를 신뢰하지 않는다.
- **복구/재개**: zombie lane은 §6.1.1 resume 경로로만 되살아난다. 재기동 없이 stored flag를 뒤집는 경로는 존재하지 않는다.
- **남는 증거**: 마지막 heartbeat 이벤트의 timestamp, `zombie_detected{lane_id,last_heartbeat_seq}` 이벤트.
- **검증**: `witnessd faultkit zombie-hang` — worker를 SIGSTOP → TTL 경과 후 `witnessd status`가 해당 lane을 `zombie`로 보고하고 `witnessd doctor`가 false-positive "all clear"를 내지 않음을 assert.

#### 6.1.3 실행 중 user hard pause / interrupt

- **트리거**: 사용자가 "wait"/"stop"/Ctrl-C를 실행 중에 발화. OMO `todo-continuation-enforcer`가 200ms 후 user "wait"를 무시하고 파일 편집·commit한 #89 재발 방지.
- **감지**: `witnessd pause <run_id>` 또는 SIGINT가 supervisor에 도달.
- **처리(hard boundary, override 불가)**: pause 신호는 **모든 continuation hook·auto-retry·auto-spawn을 즉시 중단**하고, in-flight side-effect 툴콜은 완료 후 커밋하되 새 side-effect는 시작하지 않는다(M6). continuation 로직이 pause를 override할 경로는 코드에 존재하지 않는다 — pause 상태에서 dispatch를 시도하면 `ERR_WITNESSD_PAUSED`로 fail-closed. 재개는 명시적 `witnessd resume --confirm`만.
- **복구/재개**: 사용자의 명시적 재활성화 전까지 run은 `paused`(evidence-pending의 하위 상태)에 고정.
- **남는 증거**: `user_pause{ts,source:signal|cli}` 이벤트와, pause 이후 side-effect가 0건임을 증명하는 runlog 구간.
- **검증**: `witnessd faultkit pause-race` — 툴콜 dispatch 직후 200ms에 SIGINT 주입 → pause 이후 어떤 write/commit 이벤트도 runlog에 없음을 assert(OMO #89 회귀 테스트).

---

### 6.2 관측자 · 증거 파이프라인

#### 6.2.1 observer 프로세스 실패 / 미기동

- **트리거**: observer 프로세스가 spawn 실패, 별도 uid setup 실패, 또는 캡처 중 크래시.
- **감지**: emitter는 방출 전 observer_capture가 존재하고 `observed_by == "depone-observer"`(`capture_bridge.OBSERVER_ID`)이며 `REQUIRED_OBSERVER_FIELDS`(observed_by, source_fixture_hash, diff_summary, touched_files, test_output, command_receipts) 전부를 갖췄는지 확인한다.
- **처리(fail-closed → A0)**: observer 증거가 아예 없으면 manifest는 `build_capture_manifest(fixture)`로 **observer_capture=None** 경로를 타 `assurance:"A0-claims-only"`, `decision:"claims-only"`가 된다. self-report만으로는 절대 A1에 도달하지 못한다(`_check_a0_manifest`는 A0에 observer_capture가 있으면 오히려 reject). observer가 부분 기동 후 죽어 `command_receipts`가 빈 리스트면 `_check_observer_capture_shape`가 "command_receipts must be non-empty for A1"으로 A1을 거부.
- **복구/재개**: observer를 재기동해 캡처를 다시 수행 → 새 manifest 방출. 이전 A0 manifest는 체인에 남되 상향되지 않는다.
- **남는 증거**: A0 capture-manifest(관측 실패 사실이 명시적으로 A0으로 기록됨), `observer_spawn_failed{reason}` 이벤트.
- **검증**: `witnessd faultkit observer-nostart` → 방출된 manifest의 `assurance=="A0-claims-only"`이고 `validate_capture_manifest`가 통과(A0로서 유효)함을 assert.

#### 6.2.2 observer 출력이 runner sandbox 안 (분리 위반)

- **트리거**: 설정 오류로 observer의 `--out`/`--log` 부모가 runner sandbox 안, 또는 observer_dir == runner sandbox.
- **감지**: `observe.enforce_observer_separation(runner_sandbox, out_path, log_path)`가 out/log 및 그 부모가 sandbox 밖인지, observer_dir가 sandbox와 같지 않은지 resolve 후 검사.
- **처리(fail-closed)**: 위반 시 `PairedRunError("ERR_OBSERVER_NOT_SEPARATED", ...)`를 raise하고 **observer 출력을 아예 쓰지 않는다**(부분 파일도 남기지 않음). A1은 도달 불가.
- **복구/재개**: observer_dir를 sandbox 밖(별도 uid 소유, 0700)으로 재설정 후 재캡처.
- **남는 증거**: `ERR_OBSERVER_NOT_SEPARATED` runlog 이벤트. observer 출력 파일은 생성되지 않음(테스트가 부재를 assert).
- **검증**: Depone `observe._self_test`와 동형 — sandbox 안 out_path로 캡처 시도 시 예외가 나고 `inside.exists()==False`.

#### 6.2.3 부분 증거 (중간 종료)

- **트리거**: worker가 작업 도중 종료(timeout·crash·pause)되어 command_receipts는 일부만, test_output은 미완.
- **감지**: `_check_observer_capture_shape`가 `test_output.status ∈ {not-run, passed, failed, error}`(`VALID_TEST_STATUSES`)를 강제. 중간 종료는 `status:"error"` 또는 `"not-run"`으로 기록되어야 하며 임의 문자열은 거부.
- **처리(fail-closed)**: 부분 증거는 그 사실 그대로 방출된다 — 미완 테스트를 `passed`로 위장할 수 없다. touched_files가 allowed를 벗어나면 §6.3.2로 refuted. 부분 command_receipts라도 각 항목은 `command` + int `exit_code`를 가져야 하며, 없으면 A1 거부. 미완 run은 `evidence-pending`.
- **복구/재개**: §6.1.1 resume으로 이어 실행 후 완전한 캡처 재방출.
- **남는 증거**: `status:"error"`인 test_output을 담은 A1-후보 manifest(하지만 exit code mismatch로 §6.5의 evidence-contract에서 걸릴 수 있음), `partial_capture` 이벤트.
- **검증**: `witnessd faultkit kill-mid-test` → test_output.status가 `error`/`not-run`으로 기록되고 `passed`가 아님을 assert.

#### 6.2.4 disk full / 부분 쓰기

- **트리거**: 증거 방출·runlog append 중 ENOSPC 또는 프로세스 소멸로 마지막 레코드가 절단.
- **감지**: 모든 증거 아티팩트와 runlog 레코드는 **temp 파일 + fsync + atomic rename**으로 쓴다. runlog는 NDJSON 라인 단위이며, 각 라인 끝의 `event_hash`가 그 라인의 canonical bytes와 일치해야 유효.
- **처리(fail-closed)**: rename 전 실패 → 아티팩트는 존재하지 않음(부분 파일 없음). runlog tail의 마지막 라인이 파싱 불가하거나 `event_hash` 불일치면 그 라인을 truncate하고 이전 유효 hash에서 재개(§6.1.1). 절단된 capture-manifest는 canonical hash가 어긋나 Depone `ingest_signed_evidence_bundle`이 `decision:"blocked"`.
- **복구/재개**: 디스크 확보 후 재방출. atomic rename이므로 half-written manifest가 SoT가 되는 일은 없다.
- **남는 증거**: 마지막 **완전한** runlog 라인까지. `disk_full{path,errno:ENOSPC}` 이벤트(디스크 확보 후 flush).
- **검증**: `witnessd faultkit enospc-emit` (loopback tmpfs quota) → 방출 실패 시 대상 경로에 부분 파일이 없고 마지막 runlog 라인의 hash가 검증됨을 assert.

#### 6.2.5 손상 / 재정렬된 event-log

- **트리거**: runlog 라인이 수동 편집·재정렬·삭제되거나 비트 손상.
- **감지**: runlog는 hash-chain이다 — 각 라인 `prev_event_hash`가 직전 라인의 `event_hash`와 같아야 한다. `witnessd verify --runlog`가 head부터 재계산.
- **처리(blocked)**: 첫 불일치 지점 이후 전부 신뢰 불가 → run은 `blocked`. run-state/team-state는 runlog의 pure projection이므로(M1) 재정렬로 인한 split-brain(OMX run-state vs team-state 모순)이 구조적으로 불가능. 손상된 로그로는 어떤 done도 주장 불가.
- **복구/재개**: 마지막 무결한 prefix까지만 신뢰하고 그 지점에서 resume. 손상 구간은 폐기(사용자 확인 필요, 파괴적 작업).
- **남는 증거**: 무결한 prefix 전체 + `runlog_chain_break{at_seq}` 진단 이벤트(별도 무결 로그에).
- **검증**: Depone `evidence_substrate.verify_capture_chain`과 동형의 runlog 검증기 self-test — reorder/drop/tamper 3종이 모두 `blocked`.

#### 6.2.6 prev_capture chain 단절

- **트리거**: capture-manifest 체인에서 중간 manifest가 drop·reorder되거나 predecessor가 tamper됨. 또는 non-genesis manifest가 head로 제출됨.
- **감지**: Depone `evidence_substrate.verify_capture_chain(manifests)` — 각 non-genesis manifest의 `prev_capture_hash`가 직전 manifest의 `canonical_hash`와 일치해야 하고, head는 `prev_capture_hash==null`(genesis)이어야 함.
- **처리(blocked)**: 불일치 시 `decision:"blocked"`, reasons에 `"chain head must be genesis (prev_capture_hash=null)"` 또는 `"chain[<i>] prev_capture_hash does not match predecessor"`. `prev_capture_hash` 형식(null 또는 64-char sha256 hex) 위반은 `validate_capture_manifest`가 개별로도 거부. 부분점수 없음.
- **복구/재개**: emitter는 방출 시 항상 직전 커밋된 manifest의 canonical hash를 `prev_capture_hash`로 박는다. 단절 발견 시 마지막 무결 manifest부터 체인을 다시 이어 방출.
- **남는 증거**: blocked verdict + 단절 인덱스. 무결 prefix manifest들은 그대로 유효.
- **검증**: Depone `verify_capture_chain` self-test(dropped-middle/reordered/non-genesis-head → 모두 blocked)를 witnessd 방출물에 대해 재실행.

---

### 6.3 해시 · 계약 무결성

#### 6.3.1 source_fixture_hash stale

- **트리거**: fixture가 방출 후 변경됐는데 observer_capture의 `source_fixture_hash`가 옛 값을 가리킴(또는 그 반대).
- **감지**: `capture_bridge._check_observed_block` — `manifest.source_fixture_hash`와 `_sha256_json(fixture)`가 일치해야 하고, `observer_capture.source_fixture_hash`가 manifest의 것과 일치해야 함.
- **처리(blocked)**: 불일치 시 `"source_fixture_hash mismatch"`(fixture vs manifest) 또는 `"observer_capture.source_fixture_hash is stale"`(observer vs manifest) 에러 → manifest invalid, A1 도달 불가. 이는 해시 무결성 실패이므로 severity lattice(§6.0.1)상 **blocked**로 분류한다(범위 밖 행위 위반의 refuted와 구분).
- **복구/재개**: emitter는 fixture를 `deepcopy` 후 즉시 hash를 계산해 박고, observer에 그 hash를 넘긴다(`build_separated_observer_capture(source_fixture_hash=...)`). fixture가 바뀌면 새 캡처.
- **남는 증거**: stale 사실을 담은 invalid manifest + 에러 리스트.
- **검증**: `capture_bridge._self_test`의 stale 케이스와 동형 — `source_fixture_hash="stale"`로 방출 시 "source_fixture_hash is stale" 에러.

#### 6.3.2 범위 밖 touched_files

- **트리거**: worker가 `allowed_touched_files` 밖의 파일을 수정.
- **감지**: `_check_observed_block` — `observer_capture.touched_files ⊆ allowed_touched_files`, 그리고 `diff_summary.changed_files ⊆ allowed_touched_files`. lane 컨텍스트에선 `evidence_contract`의 `forbidden` 규칙과 `team_ledger` touched_files 검사가 중복 방어.
- **처리(refuted)**: 위반 시 `"unexpected touched files: [...]"` / `"unexpected diff files: [...]"` → manifest invalid. evidence-contract 경로에선 `ERR_FORBIDDEN_FILE_TOUCHED`. 이것은 blocked보다 강한 **refuted**: 증거가 계약 위반을 적극 증명.
- **복구/재개**: worker를 ownership-region(M5) 안으로 재구속하거나 allowed 집합을 명시적으로 확장(사용자 결정) 후 재실행.
- **남는 증거**: 초과 touched 목록이 박힌 invalid manifest.
- **검증**: `capture_bridge._self_test`의 extra-touched 케이스와 동형.

#### 6.3.3 서명 검증 실패

- **트리거**: DSSE 서명이 위조·손상됐거나, 잘못된 키로 서명됨, 또는 trusted-observer-provenance가 manifest와 불일치.
- **감지**: Depone `observer_provenance.validate_trusted_observer_provenance(manifest, evidence_path, provenance, public_key_path=...)`가 DSSE 서명을 `sign.verify_dsse_envelope`로 재검증하고 서명된 binding(`{evidence_path, manifest_hash, observer_capture_hash}`)이 manifest에서 재계산한 값과 일치하는지 확인. public key는 evidence 밖의 `DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE`에서만 로드(runner-writable 경로 금지).
- **처리(blocked/refuted)**: provenance 부재 → `ERR_TRUSTED_PROVENANCE_MISSING`. binding 불일치 → `ERR_TRUSTED_PROVENANCE_MISMATCH`("trusted observer provenance signed binding mismatch"). 서명 실패 → `ERR_TRUSTED_PROVENANCE_SIGNATURE_FAILED`. `ingest_signed_evidence_bundle`은 서명이 없으면 `signatures==[]`를 **정확히** 요구하고 위조 서명은 `blocked`. private key는 verify 경로에 절대 존재하지 않는다(report-level 서명 축의 조건).
- **복구/재개**: 올바른 operator key로 재서명. 키 회전 시 새 public key를 out-of-band로 재배포.
- **남는 증거**: 실패한 provenance 레코드 + 에러 코드. DSSE envelope는 그대로 감사 가능.
- **검증**: `observe._self_test`의 provenance 경로(openssl 있으면 서명 검증 통과, 없으면 `ERR_OPENSSL_UNAVAILABLE`)와, 위조 서명 주입 시 `ERR_TRUSTED_PROVENANCE_SIGNATURE_FAILED`.

#### 6.3.4 operator key / openssl 부재

- **트리거**: openssl 바이너리가 PATH에 없음, 또는 operator private key 파일 부재.
- **감지**: `sign.openssl_path()`가 `None`. 서명 시 `sign.sign_dsse_envelope`가 `DsseSigningError(ERR_OPENSSL_UNAVAILABLE, ...)`. 검증 시 `observer_provenance._signed_record_errors`가 `sign.ERR_OPENSSL_UNAVAILABLE`을 에러 리스트에 추가.
- **처리(fail-closed)**: openssl 부재는 서명·검증 모두 **fail-closed** — 조용히 unsigned로 강등하지 않는다(unsigned 경로는 `signatures==[]`로 명시적이며 A0/blocked에 머문다). private key 부재 시 `ERR_DSSE_SIGN_FAILED`("key_id must be non-empty" 등). run은 서명 없이는 최대 A1(local-observed)에 머물고 trusted-provenance-요구 경로에선 `blocked`.
- **복구/재개**: openssl 설치 후 재서명. keyless(Fulcio/Rekor, keyless 서명 축)는 명시적으로 deferred이므로 openssl 부재를 keyless로 우회하는 경로는 **없다** — signing step은 swappable이되 keyless 시맨틱을 주장하지 않는다.
- **남는 증거**: `ERR_OPENSSL_UNAVAILABLE` 이벤트, unsigned envelope(`signatures:[]`).
- **검증**: `PATH=` 비운 환경에서 방출 → `witnessd doctor`가 `ERR_OPENSSL_UNAVAILABLE`을 보고하고 run이 A1/blocked를 넘지 않음을 assert.

---

### 6.4 격리 · 동시성 경쟁

#### 6.4.1 동시 레인의 파일 겹침 · ownership 경쟁

- **트리거**: 두 lane이 같은 파일을 touch. Cursor의 semantic merge 방치 실패모드.
- **감지**: dispatch 전 ownership-region lock(M5)이 파일/모듈 claim을 runlog 이벤트로 기록. fan-in 시 Depone `team_ledger._find_overlapping_touched_files`가 lane별 observed touched_files에서 겹침을 재도출.
- **처리(fail-closed → merge_receipt 필수)**: 겹침이 있으면 통과한 lane들은 **passing merge_receipt를 반드시 포함**해야 함("overlapping passed lanes must include merge_receipt"). 없으면 `ERR_TEAM_LEDGER_MERGE_RECEIPT_REQUIRED`, 충돌 존재 시 `ERR_TEAM_LEDGER_MERGE_RECEIPT_CONFLICTS_PRESENT`, merge가 pass 아니면 `ERR_TEAM_LEDGER_MERGE_RECEIPT_NOT_PASS`, 겹침 파일 커버리지 부족 시 `ERR_TEAM_LEDGER_MERGE_RECEIPT_COVERAGE_MISSING`. team_ledger의 boundary는 `raises_assurance=false, approves_merge=false` — ledger는 관측할 뿐 승인하지 않는다.
- **복구/재개**: 실제 merge를 수행해 conflict를 해소하고 merge_receipt(대상 clean, 충돌 이벤트 없음)를 방출 후 재fan-in.
- **남는 증거**: `overlapping_touched_files` 목록이 박힌 team-ledger verdict + 필요한 merge_receipt 부재 코드.
- **검증**: `team_ledger.build_team_ledger_verdict`에 겹침 있고 merge_receipt 없는 lane 2개를 넣어 `ERR_TEAM_LEDGER_MERGE_RECEIPT_REQUIRED`.

#### 6.4.2 두 레인이 같은 worktree 공유

- **트리거**: 설정 오류로 lane 두 개가 동일 worktree를 사용 → base/head commit이 서로 오염.
- **감지**: 각 lane worktree receipt(`worktree_receipt.build_worktree_lane_receipt`)는 `base_commit`/`head_commit`/`changed_files`/`dirty`를 read-only git 상태로 기록. team_ledger가 receipt의 base/head가 lane 선언과 일치하는지 재도출.
- **처리(fail-closed)**: base 불일치 → `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_BASE_COMMIT_MISMATCH`, head 불일치 → `..._HEAD_COMMIT_MISMATCH`, changed_files가 lane touched와 다르면 `..._TOUCHED_FILES_MISMATCH`, 누락 시 `..._TOUCHED_FILES_UNDERREPORTED`. 공유 worktree는 두 lane의 head가 상호 오염되어 이 검사들에서 반드시 걸린다.
- **복구/재개**: lane마다 독립 worktree를 강제(worktree manager가 dispatch 전 unique 경로 assert). 재프렙 후 재실행.
- **남는 증거**: 충돌하는 base/head를 담은 두 worktree receipt.
- **검증**: `witnessd faultkit shared-worktree` — 두 lane을 같은 worktree로 프렙 → team_ledger가 base/head mismatch를 방출.

#### 6.4.3 OMX / LazyCodex 동시실행 상태 오염

- **트리거**: 같은 머신에서 OMX/LazyCodex가 동시에 돌며 Codex CLI 세션·`~/.omx`·repo `.omx/state`를 건드림(teardown 실측: split-brain, zombie).
- **감지**: witnessd는 **자체 상태를 오직 `.witnessd/`와 별도 idempotency namespace**에만 둔다. Codex 어댑터는 spawn 시 격리된 `CODEX_HOME`/작업 디렉터리·전용 config를 주입해 OMX/LazyCodex의 상태 저장소와 물리적으로 분리. 시작 시 `witnessd doctor`가 외부 도구의 활성 세션이 witnessd worktree/락과 겹치는지 검사.
- **처리(fail-closed)**: 외부 도구가 같은 worktree·같은 idempotency key namespace를 점유하면 dispatch 거부(`ERR_WITNESSD_STATE_CONTENTION`). witnessd의 SoT는 hash-chained runlog 하나뿐이므로 외부 mutable JSON 오염이 witnessd 상태로 전파될 경로가 없다.
- **복구/재개**: 격리된 namespace로 재기동하거나 외부 도구 종료 후 재개.
- **남는 증거**: `state_contention{external_tool,path}` 이벤트.
- **검증**: `witnessd faultkit omx-coexist` — 더미 OMX state를 같은 repo에 심고 launch 시 격리 경로가 유지되고 witnessd runlog가 오염되지 않음을 assert.

#### 6.4.4 clock skew / 시간 위조

- **트리거**: 호스트 wall clock이 뒤로 점프·조작되어 heartbeat·timestamp 순서가 뒤집힘.
- **감지**: liveness·순서 판단은 `ts_monotonic`(단조 증가 clock)에만 의존하고 `ts_wall`(RFC3339)은 기록용. runlog 순서는 wall clock이 아니라 hash-chain의 `seq`/`prev_event_hash`로 결정(§6.2.5).
- **처리(fail-closed)**: wall clock이 역행해도 heartbeat TTL(monotonic 기반)·체인 순서는 영향받지 않는다. isolation·서명 검증은 시간에 의존하지 않는다(Ed25519 DSSE는 timestamp 검증이 아님). wall이 monotonic과 크게 어긋나면 `clock_skew{delta}` 경고 이벤트만 남기고 순서 판정엔 미사용.
- **복구/재개**: 조치 불필요(순서·liveness가 시간 위조에 불변). NTP 동기화는 운영 권고.
- **남는 증거**: monotonic 기반 heartbeat 시퀀스 + wall/monotonic delta 진단.
- **검증**: `witnessd faultkit clock-rewind` — wall clock을 -1h로 점프 → heartbeat TTL 판정과 runlog 순서가 불변임을 assert.

---

### 6.5 어댑터 · 외부 의존

#### 6.5.1 adapter 부재 / 버전 불일치

- **트리거**: Codex/Claude/OpenCode CLI가 미설치이거나, 어댑터가 Depone이 모르는 `runner_kind`/스키마 버전을 방출.
- **감지**: preflight가 어댑터 가용성을 검사(`ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE`). runner receipt는 `paired_run.validate_runner_receipt`로 검증되며 `runner_kind ∈ VALID_RUNNERS`({`codex-cli`, `manual`}), `arm ∈ VALID_ARMS`({`direct`, `governed`}), `kind=="agent-fabric-runner-receipt"`, `schema_version=="1.0"`을 요구.
- **처리(fail-closed)**: 어댑터 부재 → 해당 lane dispatch 거부, run은 `evidence-pending`에 머물고 launch 안 함. 새 어댑터가 enum 밖 `runner_kind`를 내면 receipt가 `"runner_kind must be one of [...]"`로 거부되어 A1 도달 불가 — 즉 **버전 불일치는 조용히 통과하지 못한다**. 새 어댑터를 추가하려면 Depone의 `VALID_RUNNERS`/스키마를 계약으로 함께 올려야 한다.
- **복구/재개**: 어댑터 설치 또는 스키마 정렬 후 재실행. 첫 어댑터 우선순위는 shell(W1) → Codex(W4) → Claude Code/OpenCode.
- **남는 증거**: preflight 실패 이벤트 또는 invalid runner receipt + 에러 리스트.
- **검증**: `validate_runner_receipt`에 미지 `runner_kind`를 넣어 거부 에러를 assert; preflight에 미설치 어댑터로 `ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE`.

#### 6.5.2 tool-call timeout & 멱등키 재시도 (double-apply 방지)

- **트리거**: git push / DB write / API call이 timeout됐으나 서버 측에선 이미 적용됐을 수 있음.
- **감지**: 모든 side-effect 툴콜은 `idempotency_key = canonical_hash({run_id, lane_id, tool, args, attempt_scope})`를 부여받고, 실행 전/후 상태를 runlog에 기록(M7). timeout 시 재시도 전에 동일 key의 완료 이벤트가 이미 있는지 조회. **`attempt_scope` 정의(exactly-once 불변식의 핵심):** `attempt_scope`는 "재시도 간 **불변**인 side-effect 논리 단위"의 식별자다 — 같은 논리적 부작용(예: 같은 커밋을 같은 remote에 push)을 노리는 모든 재시도는 **동일한** `attempt_scope`를 공유해야 하며, 따라서 동일한 `idempotency_key`를 얻는다. 재시도마다 증가하는 값(attempt 번호·timestamp·PID)은 절대 `attempt_scope`에 넣지 않는다 — 넣으면 재시도가 새 key를 얻어 double-apply를 막지 못하기 때문이다(재시도 횟수는 별도 `attempt` 필드로 runlog에만 기록하고 key에는 넣지 않는다). 서로 다른 논리적 부작용에만 서로 다른 `attempt_scope`를 부여한다.
- **처리(fail-closed, exactly-once)**: 동일 idempotency_key의 성공 이벤트가 있으면 **재적용하지 않고** 기존 결과를 재사용. 없으면 재시도. observer는 `command_receipts[i].exit_code`(int)로 실제 결과를 관측하므로 double-apply는 touched_files/diff에서 사후 탐지 가능. timeout 자체는 `test_output.status:"error"`로 정직하게 기록(§6.2.3).
- **복구/재개**: idempotency namespace는 §6.1.1 resume에도 보존되어 크래시 후 재개 시에도 double-apply를 막는다.
- **남는 증거**: `tool_call{idempotency_key, attempt, exit_code, timed_out}` 이벤트 시퀀스.
- **검증**: `witnessd faultkit push-timeout` — push 후 응답 전 kill → resume 시 동일 key로 재적용 0건(원격에 커밋 1개)임을 assert.

#### 6.5.3 네트워크 단절 (어댑터)

- **트리거**: 모델 API·git remote·MCP 서버로의 네트워크가 실행 중 끊김.
- **감지**: 어댑터 호출이 timeout/연결 실패. supervisor가 exit code로 관측(M3, tmux send-keys IPC 아님).
- **처리(fail-closed)**: 네트워크 실패는 §6.5.2 idempotency로 재시도하되, 재시도 예산(횟수·backoff) 초과 시 lane을 `error`로 종료하고 부분 증거를 방출(§6.2.3). silent task death(OMO MiniMax #2578류)를 방지: 무응답은 `passed`가 아니라 `test_output.status:"error"` + `network_fault` 이벤트로 표면화. push 대상 remote 단절은 idempotency로 exactly-once 유지.
- **복구/재개**: 네트워크 복구 후 resume; 미완 side-effect는 idempotency로 안전 재적용.
- **남는 증거**: `network_fault{endpoint,attempt}` 이벤트, 부분 command_receipts.
- **검증**: `witnessd faultkit netcut-adapter` — 어댑터 호출 중 endpoint 차단 → lane이 `error`로 표면화되고 silent stop이 아님을 assert.

#### 6.5.4 worktree 충돌 · dirty · 경로 이탈

- **트리거**: worktree가 dirty한 채 launch, 또는 evidence_dir가 절대경로/`..` 이탈, base commit 부재.
- **감지**: `worktree_receipt.build_worktree_lane_receipt`가 `git status --porcelain`으로 `dirty`/`dirty_files`를 기록하고, `_normalize_relative_path`가 절대경로·`..` 포함 경로를 거부, `_verify_commit`이 base commit 존재를 확인.
- **처리(fail-closed)**: fan-in 시 dirty worktree는 `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_DIRTY`로 거부(clean만 merge 가능). 경로 이탈은 `ERR_WORKTREE_RECEIPT_PATH_INVALID`, base 부재는 `ERR_WORKTREE_RECEIPT_BASE_COMMIT_REQUIRED`, git 실패는 `ERR_WORKTREE_RECEIPT_GIT_FAILED`, worktree 디렉터리 부재는 `ERR_WORKTREE_RECEIPT_REPO_MISSING`. OMX의 "dirty worktree 경고만 하고 launch"와 달리 fail-closed.
- **복구/재개**: worktree를 commit/stash로 clean화하거나 evidence_dir를 root-relative로 교정 후 재프렙.
- **남는 증거**: `dirty_files` 목록이 박힌 worktree receipt 또는 경로/커밋 에러 코드.
- **검증**: `worktree_receipt._self_test`(clean은 dirty=False)와, dirty·`../escape`·미존재 base 3종이 각각 대응 코드로 거부됨을 assert.

#### 6.5.5 예산 초과 (토큰 / 비용)

- **트리거**: per-task 토큰·달러가 하드 상한을 넘거나 delegation 트리의 depth/spend 예산 초과. Cursor $2,000/2일·OpenHands 무한루프류.
- **감지**: 각 툴콜/spawn 전에 예측 비용을 예산에서 차감하고 실측을 같은 runlog에 기록(M10). 서킷브레이커가 상한 도달을 감시.
- **처리(fail-closed)**: 상한 도달 시 continuation·auto-spawn을 즉시 중단(`ERR_WITNESSD_BUDGET_EXCEEDED`)하고 lane을 `paused`(§6.1.3와 동일 하드 경계)로 전환 — 예산 초과가 "조용한 청구"가 아니라 명시적 정지가 되도록. depth 예산 초과 시 하위 spawn 거부. 부분 증거는 정직하게 방출.
- **복구/재개**: 사용자가 예산을 명시적으로 상향(`witnessd resume --budget ...`)해야만 재개. 자동 상향 경로 없음.
- **남는 증거**: `budget_exceeded{metric,limit,observed}` 이벤트 + 실측 spend 시퀀스.
- **검증**: `witnessd faultkit budget-blowout` — 토큰 상한을 낮게 설정 → 도달 시 spawn 0건, 상태 `paused`, 명시 상향 없이는 재개 불가임을 assert.

---

### 6.6 케이스 → Depone verdict 매핑 요약표 (구현자용 체크리스트)

"상태 축" 열은 최종 상태 값이 **assurance 축**(§6.0.2 축 A)인지 **lifecycle 축**(축 B)인지, 아니면 순수 진단인지를 명시한다. 두 축은 직교하므로 한 lane은 각 축에서 값을 하나씩 가질 수 있다.

| 케이스 | 감지 지점 (Depone/runtime) | error code / reason | 최종 상태 | 상태 축 |
|---|---|---|---|---|
| 6.1.1 crash+resume | runlog tail hash | (truncate stale tail) | evidence-pending | assurance |
| 6.1.2 zombie | heartbeat TTL (monotonic) | `zombie_detected` | zombie | lifecycle |
| 6.1.3 hard pause | pause signal | `ERR_WITNESSD_PAUSED` | paused | lifecycle |
| 6.2.1 observer 미기동 | `build_capture_manifest(observer=None)` | (A0 경로) | A0-claims-only | assurance |
| 6.2.2 분리 위반 | `enforce_observer_separation` | `ERR_OBSERVER_NOT_SEPARATED` | emit-refused | assurance |
| 6.2.3 부분 증거 | `VALID_TEST_STATUSES` | status: error/not-run | evidence-pending | assurance |
| 6.2.4 disk full | atomic rename + hash | ENOSPC (부분 파일 없음) | evidence-pending | assurance |
| 6.2.5 로그 손상 | runlog hash-chain | chain break | blocked | assurance |
| 6.2.6 chain 단절 | `verify_capture_chain` | genesis/predecessor mismatch | blocked | assurance |
| 6.3.1 stale fixture | `_check_observed_block` | source_fixture_hash mismatch / stale (해시 무결성) | blocked | assurance |
| 6.3.2 범위 밖 touched | `_check_observed_block` | unexpected touched/diff files, `ERR_FORBIDDEN_FILE_TOUCHED` | refuted | assurance |
| 6.3.3 서명 실패 | `validate_trusted_observer_provenance` | `ERR_TRUSTED_PROVENANCE_MISSING/MISMATCH/SIGNATURE_FAILED` | blocked | assurance |
| 6.3.4 openssl/key 부재 | `sign.openssl_path()` | `ERR_OPENSSL_UNAVAILABLE`, `ERR_DSSE_SIGN_FAILED` | ≤A1 / blocked | assurance |
| 6.4.1 파일 겹침 | `_find_overlapping_touched_files` | `ERR_TEAM_LEDGER_MERGE_RECEIPT_REQUIRED/CONFLICTS_PRESENT/NOT_PASS/COVERAGE_MISSING` | refuted | assurance |
| 6.4.2 공유 worktree | team_ledger receipt 검사 | `..._WORKTREE_RECEIPT_BASE/HEAD_COMMIT_MISMATCH` (커밋 해시 무결성) | blocked | assurance |
| 6.4.3 OMX 오염 | `witnessd doctor` | `ERR_WITNESSD_STATE_CONTENTION` | emit-refused | assurance |
| 6.4.4 clock skew | monotonic 순서 | `clock_skew` (진단만) | 불변 | 진단 |
| 6.5.1 adapter 부재/버전 | preflight, `validate_runner_receipt` | `ERR_TEAM_LAUNCH_PREFLIGHT_ADAPTER_UNAVAILABLE`, runner_kind not in VALID_RUNNERS | evidence-pending | assurance |
| 6.5.2 timeout+idempotency | idempotency_key runlog | (재적용 0건) | exactly-once(불변) | lifecycle |
| 6.5.3 network 단절 | supervisor exit code | `network_fault`, status: error | error | lifecycle |
| 6.5.4 dirty/경로 이탈 | `build_worktree_lane_receipt` | `ERR_TEAM_LEDGER_WORKTREE_RECEIPT_DIRTY`(행위 위반→refuted), `ERR_WORKTREE_RECEIPT_PATH_INVALID/BASE_COMMIT_REQUIRED/GIT_FAILED/REPO_MISSING`(emit-refused) | refuted / emit-refused | assurance |
| 6.5.5 예산 초과 | 서킷브레이커 | `ERR_WITNESSD_BUDGET_EXCEEDED` | paused | lifecycle |

`ERR_WITNESSD_*` 접두 코드(PAUSED, STATE_CONTENTION, BUDGET_EXCEEDED)는 witnessd 런타임 고유(Depone에 없음)이며, 나머지 `ERR_*` 및 문자열 reason은 **모두 위에서 인용한 Depone 계약 코어의 실제 error code**다. 어떤 예외도 이 표의 최종 상태보다 높은 assurance를 witnessd가 스스로 부여하지 못하며, 상향은 오직 Depone의 오프라인 재검증(`agent-fabric-observe --self-test` + inline `validate_capture_manifest`/`verify_isolation_boundary`/`verify_capture_chain`, 또는 `evidence-ingest`/`evidence-chain` 서브커맨드 — `depone verify`는 plan-대조 검증기이지 assurance 재도출기가 아님) 통과로만 일어난다(§6.0.2/§4.13). 위 `witnessd faultkit <case>` 하네스는 이 표의 각 행을 결정적으로 재현·assert하는 회귀 스위트이며, `witnessd doctor --self-test`가 그 전체를 CI에서 게이트한다.

---

## 7. 테스트 · 수용 기준 · 롤아웃 · 도그푸드

### 7.0 원칙

이 섹션의 모든 게이트는 두 개의 물리적으로 분리된 repo(`witnessd`, `depone`/`keelplane`)에 걸쳐 있다는 것을 전제로 한다. `witnessd`는 evidence bundle을 방출하는 것 이상을 검증하지 않는다 — 검증은 항상 Depone이, `witnessd` 프로세스가 종료되고 나서 오프라인으로, 바이트만 보고 재도출한다. 따라서 "테스트가 통과했다"는 witnessd 내부 주장은 그 자체로 아무것도 증명하지 않으며, 이 섹션이 정의하는 게이트는 예외 없이 **(a) witnessd 자체 self-test**, **(b) committed fixture의 revalidation**, **(c) Depone의 독립 재검증** 세 층 중 최소 두 층을 통과해야 "그린"으로 인정한다. 어느 웨이브든 이 세 층을 스킵한 채 "구현 완료"라고 선언하는 것은 §1의 논제를 스스로 배신하는 것이므로 금지한다.

### 7.1 테스트 전략: stdlib-only self-test 기반 TDD

**Decision (재검토 가능): witnessd는 Depone과 동일하게 pure-stdlib Python(3.11+)으로 작성하고, 서드파티 테스트 프레임워크(pytest 등)에 의존하지 않는다. 각 모듈은 `--self-test` CLI 플래그로 자기 검증한다.**

- rationale: Depone의 `scripts/*.py --self-test` 관례를 그대로 계승하면 (1) `witnessd`가 자기 자신을 실행 환경 밖에서 검증할 수 있어야 한다는 논제와 일치하고 — 즉 witnessd 자신의 self-test도 "관측자 없는 자기보고"가 아니라 결정적 재실행 가능 코드 경로여야 한다 — (2) CI에 아무 외부 패키지 설치 없이 `python3 -m witnessd.<module> --self-test`만으로 게이트를 걸 수 있어 air-gapped 감사 wedge(포지셔닝 결정)에도 그대로 재사용된다. pytest 같은 프레임워크는 이 재사용성을 깨고, 감사 환경에서 "witnessd를 검증하려면 또 다른 서드파티 의존성 그래프를 신뢰해야 한다"는 반론을 만든다.
- TDD 순서: 신규 모듈은 반드시 (1) 실패하는 `--self-test` 케이스를 먼저 작성 → (2) 최소 구현으로 통과 → (3) fail-closed 케이스(스키마 위반, hash mismatch, 서명 부재, uid 경계 위반)를 self-test에 추가해 회귀 고정, 순서로 만든다. 이 순서를 어기고 구현부터 쓴 PR은 reviewer 게이트(§7.4)에서 반려한다.
- 각 self-test는 최소한 다음 두 종류의 assertion을 포함해야 한다: (i) happy-path 아티팩트가 스키마·해시 규약을 만족한다는 positive assertion, (ii) 의도적으로 깨뜨린 입력(누락 필드, 미지 fact, 범위 밖 touched file, 체인 단절, 서명 없음)이 정확한 `ERR_*` 코드로 fail-closed된다는 negative assertion. Depone 쪽의 `capture_bridge.py`, `evidence_contract.py`가 이미 이 패턴(예: `ERR_EVIDENCE_CONTRACT_INVALID`, `ERR_FORBIDDEN_FILE_TOUCHED`, `ERR_TEST_EXIT_CODE_MISMATCH`)을 쓰므로, witnessd가 방출하는 아티팩트의 negative assertion은 반드시 Depone 쪽 대응 `ERR_*`로 blocked되는 것까지 확인해야 한다(§7.3의 도그푸드 루프가 이 확인을 수행).

### 7.2 웨이브별 acceptance bar

각 웨이브(W1~W5)는 §5 Approach C의 순서를 따른다. 웨이브는 아래 표의 모든 항목이 참일 때만 "완료"로 선언할 수 있고, 다음 웨이브는 이전 웨이브의 acceptance bar가 그린인 상태에서만 시작한다(레드 상태에서 다음 웨이브 착수 금지).

| 웨이브 | 범위(§5 재인용) | acceptance bar |
|---|---|---|
| **W1** | event log substrate(M1) + observer 분리(E1) + capture-manifest + prev_capture 체인(E2/E8) + **runner-receipt(E5)** + **evidence-substrate 번들+OTel(E7)** + operator Ed25519 DSSE(E6), shell 어댑터 1개 | 1. `python3 -m witnessd emit --self-test` pass.<br>2. committed fixture `fixtures/w1/shell-lane/` 아래 capture-manifest.json + runner-receipt.json + evidence-substrate-bundle.json(인라인 `otel_spans`) + observer_capture.json(observer 소유 디렉터리, runner sandbox 밖)이 존재하고 로컬 재검증 가능.<br>3. 같은 fixture를 Depone에 전달해 `agent-fabric-observe --self-test` + inline `validate_capture_manifest(m) == []`가 `m["assurance"]`로 `A1-local-observed`(또는 uid 격리 사실이 있으면 `verify_isolation_boundary(iso).boundary is True`로 `A2-isolated-observed`)를 재도출한다. **`depone verify --evidence`는 쓰지 않는다**(그것은 plan-대조 검증기이지 assurance 재도출기가 아니다, §4.13).<br>4. prev_capture 체인이 **≥3개의 capture-manifest**(예: 순차 lane/재시도로 방출된 manifest genesis→link→link)로 append-only 검증됨(`evidence_substrate.verify_capture_chain`은 manifest 리스트만 입력으로 받는다; heartbeat/dispatch 같은 runlog 이벤트는 이 함수의 대상이 아님).<br>5. **runner-receipt(E5)** `validate_runner_receipt == []`, **evidence-substrate 번들(E7)** `ingest_signed_evidence_bundle(bundle, public_key, artifact_paths)`가 `signature_verified == True` & 전 subject `verified` — §1.8.1의 최상위 성공기준이 여기서 닫힌다.<br>6. DSSE 서명이 operator 공개키로 검증되고, private key가 witnessd 프로세스 종료 후 verify 경로 어디에도 나타나지 않음(강제: Depone 쪽 verify는 `DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE`만 참조, private key 파일 경로가 verify 인자에 존재하면 self-test가 실패하도록 assertion 추가).<br>7. `witnessd doctor`(§7.4)와 `python3 scripts/check_contract.py --tier changed`(Depone 쪽, 변경분 한정) 모두 그린. |
| **W2** | supervised worker(M3) + heartbeat liveness(M2) + durable session(M4) + isolation facts per spawn(E4) | 1. worker 프로세스가 SIGCHLD/exit code로 감지됨을 self-test가 강제 kill 시나리오로 증명(좀비 상태 재현 불가 assertion: heartbeat 미수신 N초 후 `active=false`로 파생됨을 event log projection에서 확인).<br>2. isolation facts(uid 모델)가 spawn마다 기록되고 Depone `isolation.verify_isolation_boundary`가 flag flip 위조 없이 재도출.<br>3. durable session이 프로세스 재시작 후 ID로 재개되는 fixture(`fixtures/w2/durable-resume/`) 커밋, 재개 전/후 event log가 동일 세션 ID로 연속.<br>4. Depone `paired_run.validate_runner_receipt`가 이 wave의 runner receipt를 blocked 없이 통과. |
| **W3** | auto worktree + ownership-region lock(M5) + worktree lane receipt(E5) + team-ledger fan-in(E10) | 1. 두 lane이 겹치는 파일을 claim하려는 시도가 event log에 거부 이벤트로 남고, 락 없이 진행된 write는 fail-closed.<br>2. worktree lane receipt(fixture 커밋)가 Depone `team_ledger._validate_worktree_receipt`를 통과 — `dirty=False`, 그리고 §4.7 정본 규칙: overlap+merge-required lane은 `changed_files == touched_files`(equality), non-overlap lane은 `changed_files ⊇ touched_files`(superset; 정당한 초과 변경 허용).<br>3. 겹치는 두 lane이 merge receipt 없이는 fan-in 통과 못 함을 negative self-test로 고정. |
| **W4** | Codex(W4)→Claude Code/OpenCode 어댑터 + 모델 라우팅(M8) + 비용 서킷브레이커(M10) | 1. 각 신규 어댑터가 동일한 runner-receipt 스키마를 방출하고 W1의 Depone verify 파이프라인이 어댑터 무관하게 같은 verdict를 낸다(어댑터별 fixture 3종 커밋: shell/codex/claude 또는 opencode).<br>2. OMX/LazyCodex와 동시 실행 시 상태 격리 fixture: 같은 머신에서 OMX 프로세스가 떠 있는 상태로 witnessd self-test가 자신의 event log/디렉터리가 OMX 상태 파일과 겹치지 않음을 확인.<br>3. 비용 서킷브레이커가 예산 초과 시 dispatch를 거부하는 negative assertion. |
| **W5** | 자동 학습 캡처(M9) + hard pause(M6) + kill-switch(M11) | 1. hard pause 발동 후 200ms 이내 continuation hook 재개 시도가 전부 로그에 거부로 기록됨(OMO `todo-continuation-enforcer` 재발 실패모드의 회귀 테스트로 명시 고정).<br>2. kill-switch가 전체 harness를 정지시키고, unreadable config에서 fail-safe(덮어쓰기 금지) 동작을 negative fixture로 고정.<br>3. 학습 캡처 승격 아티팩트가 provenance(run/증거/승인)를 포함해 Depone evidence_substrate로 ingest 가능. |

각 웨이브 표의 항목은 그대로 PR 체크리스트 문구로 재사용한다. "acceptance bar 전부 그린"이 아닌 상태에서 웨이브 완료를 커밋 메시지나 문서에 "done/complete"로 적는 것 자체가 §1이 금지하는 self-report theater이므로 금지한다.

### 7.3 revalidatable committed fixture 정책

**Decision (재검토 가능): 모든 웨이브는 최소 1개의 committed fixture 디렉터리(`fixtures/w<N>/<scenario>/`)를 repo에 커밋하고, 그 fixture는 CI 러너 재기동/타 머신에서도 결정적으로 동일한 verdict를 재현해야 한다.**

- rationale: Depone의 `docs/a2-first-isolated-evidence/`, `docs/team-launch-preflight/` 관례를 그대로 계승. fixture가 커밋되어 있지 않으면 "언젠가 통과했다"는 주장은 재실행 불가능한 self-report와 동급이다.
- fixture는 반드시 다음을 포함한다: 입력 JSON(capture-manifest, observer_capture, isolation facts 등), 실행 커맨드 텍스트(정확히 복붙 가능해야 함), 기대 verdict(pass/blocked/refuted 중 하나와 그 이유), 그리고 negative fixture 최소 1개(의도적으로 깨진 버전과 그 `ERR_*` 코드).
- 결정성 보장: fixture 재검증은 wall-clock, 프로세스 PID, 호스트명에 의존해서는 안 된다. canonical_hash(`sha256(json.dumps(obj, sort_keys=True, separators=(",",":")).encode("utf-8")).hexdigest()`)만을 비교 기준으로 삼는다. timestamp 필드가 존재해야 하는 경우(heartbeat 등) self-test는 값 자체가 아니라 "단조 증가 + bounded interval" 같은 구조적 속성만 검사한다.
- 회귀 고정: 한 번 재현된 실패모드(OMX split-brain류, OMO continuation override류)는 대응 negative fixture로 영구 committed되어야 하며, 이후 어떤 리팩터에서도 그 fixture를 삭제/완화하는 diff는 reviewer가 거부한다(evidence_contract의 test-weakening 탐지와 같은 정신).

### 7.4 CI 게이트

witnessd 자체 게이트(신규, repo #2 전용):

- `python3 -m witnessd doctor` — Depone `scripts/dwm.py doctor`와 동형: witnessd가 advertise하는 모든 커맨드 경로가 실재하고, event log의 append-only 무결성(순서/체인)과 dogfood hash ledger가 일치함을 확인. 실패 시 `ERR_WITNESSD_DOCTOR_FAILED`로 종료.
- `python3 -m witnessd contract --tier {smoke,changed,full}` — Depone `scripts/check_contract.py --tier changed`의 tiering 개념(`docs/v91-contract-tiering-spec.md`)을 그대로 witnessd에 이식: `smoke`는 각 모듈 `--self-test`만, `changed`는 변경된 모듈 + 그 모듈이 방출하는 fixture의 revalidation, `full`은 전체 웨이브 fixture 스위트 + Depone 왕복 검증까지 포함. PR 게이트는 최소 `changed` tier 필수.

Depone 쪽 게이트(기존 스크립트 재사용, 신규 작성 금지):

- `python3 scripts/check_contract.py --tier changed`(Depone repo에서 실행) — witnessd가 방출한 evidence bundle을 Depone이 소비하는 새 fixture/어댑터가 추가될 때마다 Depone 쪽 changed-tier에도 그 fixture 검증 스텝이 등록되어 있어야 한다. 즉 witnessd 쪽 PR과 짝을 이루는 Depone 쪽 PR이 필요할 수 있다(예: 새 evidence schema 필드 추가 시 Depone `evidence_contract.py`/`capture_bridge.py`에도 검증 스텝 추가).
- `python3 scripts/dwm.py doctor`(Depone repo에서 실행) — witnessd 관련 fixture 경로가 Depone의 advertised-command 목록에 등록돼 있으면 doctor가 그 경로 존재를 검사한다.
- **신규 스크립트·코드를 Depone repo에 작성하지 않는다.** witnessd는 Depone의 검증 로직을 import(`from depone.agent_fabric.capture_bridge import validate_capture_manifest` 등)하거나 CLI로 호출만 하며, Depone 쪽 코드를 witnessd 전용으로 수정하지 않는다 — 이것이 물리 분리 결정의 실질적 의미다.
- **증거 파일 반입/코드 반입 경계(도그푸드 예외 명시).** witnessd가 방출한 evidence bundle(파일)을 Depone이 **read-only로 소비**하는 것은 허용된다(그것이 검증기의 본 역할이다). 그러나 그 증거 파일을 **Depone repo 안에 커밋**하지는 않는다 — 도그푸드 산출물은 Depone repo 밖의 공유 evidence 디렉터리(또는 witnessd repo)에 두고, Depone은 그 경로를 인자로 받아 소비만 한다(§7.5). 즉 "증거 파일의 read-only 소비는 허용, 코드/스크립트/산출물의 Depone repo 반입은 금지"가 독립성 서사를 유지하는 경계다.

CI 파이프라인 순서(둘 다 그린이어야 머지 가능):

```
witnessd repo CI:
  1. python3 -m witnessd contract --tier changed
  2. python3 -m witnessd doctor
  3. (fixture 변경 시) 도그푸드 루프(§7.5) 재실행 → Depone verdict 첨부

depone repo CI (witnessd 연동 PR에 한함):
  1. python3 scripts/check_contract.py --tier changed
  2. python3 scripts/dwm.py doctor
```

### 7.5 witnessd ↔ Depone 도그푸드 루프 (n=1 증명)

**Decision (재검토 가능): 모든 웨이브 완료 선언 전에 반드시 "witnessd가 실제로 무언가를 실행 → Depone이 그 결과를 독립 재검증"하는 최소 1회의 실측 루프(n=1)를 수행하고, 그 결과를 양쪽 repo에 committed artifact로 남긴다.**

- rationale: Depone repo의 `docs/v126-*`(실제 Codex direct-vs-governed 런 캡처→A1 fixture 승격) 선례를 그대로 계승. "이론적으로 스키마가 맞는다"와 "실제로 실행한 결과가 검증됐다"는 다른 주장이며, §1의 논제("provable-by-construction")는 후자만 증명으로 인정한다.
- 루프 절차(명령 그대로 실행 가능해야 함):
  1. witnessd repo에서: `python3 -m witnessd run --adapter shell --lane w1-dogfood --out out/dogfood/w1/` — 실제 shell 커맨드(예: 간단한 파일 생성+테스트 실행)를 observer 분리 하에 실행하고 evidence bundle을 `out/dogfood/w1/`에 방출.
  2. 그 디렉터리를 **Depone repo 밖의 공유 evidence 디렉터리**(예: `~/witnessd-evidence/w1/`)로 실제 파일 복사(symlink 아님 — evidence는 파일로만 핸드오프): `cp -r out/dogfood/w1/ ~/witnessd-evidence/w1/`. **Depone repo에는 증거 파일을 커밋하지 않는다**(§7.4 독립성 경계).
  3. Depone은 그 공유 경로를 **read-only 인자로 받아** 소비만 한다 — Depone repo 안에서: `python3 - <<'PY'` 스크립트로 `capture_bridge.validate_capture_manifest` + `isolation.verify_isolation_boundary` + `evidence_substrate.verify_capture_chain`을 `~/witnessd-evidence/w1/`의 바이트에 대해 순서대로 호출해 verdict를 프린트(Depone repo 파일은 하나도 생성/수정하지 않음).
  4. 결과 verdict(JSON)는 **witnessd repo**의 `fixtures/w1/dogfood-verdict.json`으로 커밋한다(witnessd 산출물이므로 witnessd repo가 SoT). Depone repo는 이 도그푸드로 인해 어떤 파일도 변경되지 않는다 — Depone이 read-only 소비자였음을 repo diff가 비어 있음으로 증명한다.
  5. verdict가 `blocked`/`refuted`면 웨이브는 완료 선언 불가 — 원인 fix 후 루프 재실행. verdict가 `A1` 이상이면 그 웨이브의 n=1 증명으로 인정.
- 도그푸드는 웨이브당 최소 1회지만, evidence schema에 필드가 추가/변경될 때마다(예: W2에서 isolation facts 필드 추가) 재실행해 verdict가 여전히 유효함을 재확인한다 — schema drift가 조용히 verdict를 무너뜨리는 것을 막기 위함(OMX의 3-store 파편화 실패모드와 동일 계열의 리스크).
- 도그푸드 결과는 마케팅에 "VERIFIED"로 재서술하지 않는다. 정확히 "Depone이 이 evidence bundle에서 재도출한 assurance는 A0/A1/A2 중 X였다(+ signing_status)"는 문장으로만 기록한다(§7.6 UX 규율과 동일 원칙).

### 7.6 UX 규율의 테스트 가능한 강제

§ 앞서 결정된 "evidence-pending" 하드 규칙은 이 섹션에서 **구조적으로 검증 가능한** 형태로 강제한다. plain grep은 주석·식별자(`is_complete`, `completed_at`)·import(`Depone`)·문서에서 대량 오탐하므로 acceptance 기준으로 쓰지 않는다. 대신 **출력 도메인을 enum으로 고정**한다:

- **단일 출력 경로 강제.** witnessd의 모든 사용자향 상태 출력(CLI stdout, 향후 대시보드)은 예외 없이 단일 함수 `render_status(state) -> str`을 경유한다. 상태 문자열을 직접 print/log하는 다른 경로는 존재하지 않는다.
- **출력 도메인을 enum으로 고정.** `render_status`가 낼 수 있는 문자열은 열거형으로 못박힌 유한 집합뿐이다 — assurance 축 `{evidence-pending, emit-refused, A0-claims-only, A1-local-observed, A2-isolated-observed, blocked, refuted}`(§6.0.2 축 A, Depone이 반환한 값을 그대로 pass-through) + lifecycle 축 `{running, paused, zombie, dead, error}`(축 B). `VERIFIED`/`DONE`/`COMPLETE`/`ORCHESTRATION COMPLETE`는 이 enum에 **원천적으로 없다.**
- **테스트는 enum만 검사(구조적).** self-test는 소스 전체 grep이 아니라 (i) `render_status`의 출력 도메인 == 위 enum 집합임을 assert하고, (ii) 사용자향 출력이 모두 `render_status`를 경유함을 정적으로 확인한다(직접 print 경로 부재). 따라서 금칙어 부재는 grep 오탐과 무관하게 enum 값 집합 검사로 결정적으로 증명된다. `witnessd doctor --forbid-self-report`가 이 두 검사를 서브체크로 등록한다.
- **등급 노출은 Depone 재도출 후에만.** Depone verdict가 `A1`/`A2`(assurance 상한 A2)로 확정된 이후에만 상태 문자열이 `assurance: A2-isolated-observed`처럼 Depone이 실제로 반환한 등급 문자열 그대로를(+ signing_status) 노출할 수 있다(witnessd가 임의 재서술/과장 금지 — 등급 문자열은 Depone 응답 pass-through).

### 7.7 롤아웃 단계

1. **내부 도그푸드 (W1~W2 기간)**: 작성자 본인 머신 + 격리 서버 1대에서만 witnessd 실행. 모든 실사용 세션이 §7.5 도그푸드 루프의 실측 데이터가 된다. 외부 사용자 없음. 이 단계의 종료 조건은 W2 acceptance bar 전체 그린 + 최소 5회의 독립 도그푸드 루프(서로 다른 lane 시나리오)가 Depone에서 A1 이상으로 재확인된 것.
2. **소수 사용자 비공개 알파 (W3~W4 기간)**: 사용자가 지정하는 소수(3~5인 규모)에게 repo 접근 권한과 함께 배포. 각 알파 참가자의 세션은 최소 1회 evidence bundle을 Depone(참가자 자신의 로컬 clone 또는 air-gapped 환경)로 넘겨 재검증하는 것을 온보딩 필수 스텝으로 강제 — 참가자가 "잘 작동했다"는 구두/텍스트 보고만 남기는 것은 이 프로젝트의 논제상 증거로 인정하지 않는다.
3. **공개 전 게이트**: W4 acceptance bar 그린 + 알파 기간 중 발생한 모든 verdict `blocked`/`refuted` 사례가 근본원인 fix + 회귀 fixture로 committed. 벤치마크/우월성 주장은 Depone의 non-goal(§6 근거)을 witnessd도 자율적으로 계승해, 공개 시점에도 "OMX보다 빠르다/우수하다" 류의 정량 마케팅 주장 없이 evidence 스키마와 재현 가능한 fixture만 공개한다.
4. **일반 공개 (W5 이후)**: kill-switch/hard pause가 실전 검증된 이후에만. 이 단계 이전 공개는 M6/M10 부재 상태에서의 공격적 자율성 노출이므로 금지(§6 오픈퀘스천 4의 보수적 기본값 결정을 그대로 적용).

각 롤아웃 단계 전환은 사람의 명시적 승인을 요구하며(§ 다른 섹션의 Roles 표 — verifier는 evidence를 보고하되 assurance를 못 올림, operator는 상태를 보고하되 게이트를 우회 못 함 — 을 롤아웃 승인에도 동일 적용), 승인 자체도 텍스트 합의가 아니라 "해당 단계의 acceptance bar + 도그푸드 verdict가 committed artifact로 존재한다"는 사실에 근거해야 한다.

---

## 8. 오픈 결정 · 리스크 · 향후

### 8.1 확정 Decision 요약 (재검토 가능)

각 항목은 "Decision (재검토 가능)"으로 spec 본문에 이미 기록되어 있으며, 여기서는 rationale만 재확인한다. 재검토가 필요해지면 이 rationale이 깨진 전제를 먼저 지목해야 한다.

- **이름/repo 분리**: `witnessd`, Depone과 물리적으로 별도 repo 2개. rationale — "non-executing 검증기의 독립성"이 Depone 신뢰 모델의 근거이므로, 런타임 코드가 검증기 코드와 같은 repo·같은 CI·같은 릴리스 사이클을 공유하면 "검증기가 실행기에 물리적으로 닿을 수 없다"는 주장 자체가 약해진다. monorepo `runtime/` + `verify/` 서브디렉터리안은 계약(`canonical_hash` 규약, 스키마)은 동일하게 공유 가능하지만, 외부 감사자에게 "두 코드베이스가 별개 신뢰 경계"라는 것을 repo 경계 자체로 증명할 수 없다.
- **논제**: done을 자기보고 텍스트가 아니라 관측자-서명 바이트로 정의. rationale — §1/§2(E1~E10)에서 실증했듯 OMO `<promise>VERIFIED</promise>`, OMX `run-state.json`/`team-state.json` split-brain, `omx doctor` false-positive가 전부 "완료 신호 = 자기보고 텍스트"라는 동일 근본원인에서 나온다. provable-by-construction을 1급 목표로 못박지 않으면 능력 기능(W2 이후)이 먼저 자라 다시 self-report theater로 회귀할 유인이 구조적으로 생긴다.
- **진행 순서 Approach C**: evidence-emitter(W1) 우선, 능력(W2~W5)을 그 위에 얹는다. rationale — Approach B(어댑터 먼저, 증거는 나중에 bolt-on)는 teardown이 지적한 모든 실패모드(split-brain, zombie liveness, false-positive health check)를 그대로 상속하는 안티패턴이다. W1이 Depone의 `capture_bridge.py`/`observe.py::enforce_observer_separation`/`sign.py` 계약을 첫날부터 만족시켜야, W2 이후 추가되는 모든 능력이 "자동으로 증거를 방출"하는 상태에서 시작한다.
- **A2 격리 = uid 모델 1급, docker는 후속 옵션**: `runner_uid != observer_uid != 0`, `observer_dir` 권한 `0700`이며 runner가 write 불가. rationale — Docker 모델은 boundary가 더 강하지만 OpenHands류 "잠긴 기업 머신에서 Docker-in-Docker 마찰"을 그대로 상속해 채택 장벽이 된다. uid 모델은 `isolation.probe_isolation_facts`가 이미 지원하는 경로이고, root 권한 없이도(설치 시 별도 시스템 유저 하나만 프로비저닝하면) 재현 가능해 첫 어댑터(W1, shell)에서 즉시 A2를 실증할 수 있다. `verify_isolation_boundary`가 facts에서 boundary를 재도출하므로(`_check_a2_manifest`), 나중에 docker 모델을 추가해도 verify 쪽 코드 변경 없이 새 `isolation_model` 값만 인식시키면 된다.
- **서명 = operator-held Ed25519 DSSE**: sign은 런타임, verify는 Depone이 public key로만. private key는 verify 경로에 존재하지 않는다. rationale — `sign.py`/`observer_provenance.py`가 이미 `DEPONE_TRUSTED_OBSERVER_PUBLIC_KEY_FILE`을 out-of-band 공개키 경로로 정의해 두었고, `ERR_OPENSSL_UNAVAILABLE`을 fail-closed 조건으로 명시한다. Sigstore Fulcio keyless + Rekor는 Depone spec에서 이미 명시적으로 deferred이므로, 새 런타임이 이를 먼저 구현해 "keyless"를 주장하면 두 repo 간 assurance 시맨틱이 어긋난다. signing step은 swappable(추상 인터페이스)로 두되, 현재 구현이 keyless를 제공한다고 마케팅하지 않는다.
- **첫 어댑터 순서 = shell(W1) → Codex(W4) → Claude Code/OpenCode**: rationale — shell 어댑터는 observer 프로세스 분리·isolation facts probe·DSSE 서명 전부를 substrate 복잡도 없이 검증할 수 있는 최소 표면이다. Codex는 OMX/LazyCodex 생태계와 사용자 기반이 겹치므로 "그들이 못 하는 증명"을 가장 빨리 대비시켜 보여줄 수 있다. Claude Code/OpenCode는 Task/subagent 모델이 있어 어댑터 스키마(runner-receipt)를 substrate 무관하게 검증하는 마지막 단계로 적절하다.
- **UX 규율 = evidence-pending 하드 규칙**: rationale — Depone 검증이 끝나기 전에는 "VERIFIED/DONE/COMPLETE" 류의 self-report 성공 문자열을 어떤 CLI 출력·로그·notification에도 노출하지 않는다. 이는 §1에서 조롱한 실패모드(OMO의 `<promise>VERIFIED</promise>`, OMX `doctor`의 "18 passed, 0 failed" false-positive)를 이 프로젝트 자신이 반복하지 않기 위한 자기구속이다.
- **포지셔닝 = 개발자 툴 + 규제/감사 wedge**: rationale — air-gapped Depone 소비(공개키만 반출, private key/런타임 코드 반출 불필요)는 규제/감사 세그먼트에 구조적으로 맞는 기능이지 마케팅 장식이 아니다. 개발자 툴 세그먼트에서는 evidence-native가 능력 UX(팀/병렬/worktree)의 신뢰 기반이 되어, 더 공격적인 자율성(auto-retry/auto-spawn)을 안전하게 판매하는 근거가 된다.
- **불변식 계승**: `canonical_hash = sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()`; fail-closed(미지 fact/hash mismatch/범위밖 touched/서명부재/chain단절 → `A0`/`blocked`/`refuted`, 부분점수 없음); worker는 자기 성공을 seal 불가; verifier는 assurance를 상향 불가(`team_ledger.py`의 `raises_assurance: False, approves_merge: False`가 실제 코드에서 boundary로 이미 강제됨). rationale — 이 세 불변식이 깨지면 "provable-by-construction"이라는 논제 자체가 성립하지 않으므로, 이들은 spec 어느 웨이브에서도 예외를 두지 않는 유일한 항목이다.

### 8.2 남은 오픈 결정

다음 항목은 이 spec 시점에 확정하지 않고, 명시된 트리거 조건이 충족되면 재논의한다.

1. **모노레포 vs 2-repo 최종안**: 현재는 2-repo(§8.1)로 확정했으나, 두 repo 간 계약(스키마 버전, canonical hashing 규약)을 유지보수하는 비용이 커지면(예: 스키마 변경이 매번 두 repo에서 조율돼야 하는 빈도가 릴리스당 2회를 넘으면) 계약을 별도 "contract" 패키지(pip/npm)로 추출해 두 repo가 그 패키지에만 의존하는 3-repo 구조를 재검토한다. 이는 모노레포로 되돌아가는 것이 아니라 계약을 더 명시적으로 물리 분리하는 방향이다.
2. **A2 uid vs docker 1급 전환**: uid 모델을 1급으로 확정했으나, 멀티테넌트 SaaS 배포(다수 팀이 같은 호스트를 공유)가 로드맵에 오르면 docker 모델(또는 gVisor/Firecracker 등 강한 격리)을 1급으로 승격할지 재검토한다. 트리거: 단일 호스트에서 동시에 3개 이상의 독립 팀이 witnessd를 실행하는 배포 시나리오가 확정되는 시점.
3. **key 회전 정책**: operator Ed25519 key는 90일 주기 또는 compromise 의심 즉시 회전한다. 과거 evidence는 retired public key archive로 계속 재검증 가능하게 유지하고, compromised key는 compromise 시점 이후 evidence를 신뢰 목록에서 제외한다. 첫 프로덕션 팀 배포 전 hard gate는 `docs/ops/operator-key-rotation.md`를 실제로 실행하고 `scripts/revalidate_key_rotation.py`가 archive/canary evidence를 통과하는 것이다. 현재 committed archive는 로컬 canary만 증명하며 `production_gate.status="blocked"`로 남긴다. keyless(Fulcio/Rekor)는 실제 production team deployment에서 이 gate가 운영 검증되기 전까지 착수 금지.
4. **첫 어댑터 세부 스펙**: Codex 어댑터가 OMX/LazyCodex와 동시 실행될 때 상태 격리를 어떤 메커니즘으로 보장할지(별도 상태 디렉터리 네임스페이스 vs 프로세스 그룹 격리 vs 파일 lock)는 W4 착수 시점에 Codex CLI의 실제 상태 저장 경로를 조사한 뒤 확정한다. 현재는 "격리가 필수"라는 요구만 확정이고 메커니즘은 미정.
5. **포지셔닝 세그먼트 우선순위**: 개발자 툴과 규제/감사 wedge 중 어느 쪽을 초기 GTM 자원 배분에서 우선할지는 W1~W3 완료 후 실제 초기 채택 신호(개발자 커뮤니티 반응 vs 규제 산업 인바운드 문의)를 관측한 뒤 결정한다.

### 8.3 주요 리스크와 완화

| 리스크 | 완화 |
|---|---|
| W1이 A(얇은 MVP)보다 무거워 첫 데모가 늦어지고, 능력 UX 없이 "검증 장난감"으로 오인될 위험 | W1 완료 즉시 shell 어댑터로 "OMX doctor가 false-positive를 내는 정확히 그 시나리오(zombie 프로세스)"를 witnessd로 재현해 A0/blocked verdict를 실측 데모로 보여준다. 능력이 아니라 실패모드 재현이 초기 서사다. |
| operator private key 유출 시 임의 evidence 위조 가능 | key는 evidence bundle 밖에만 보관(런타임 프로세스 메모리/HSM/OS keychain), verify 경로에 절대 로드하지 않음을 코드 리뷰 게이트로 강제. key 회전 런북(`docs/ops/operator-key-rotation.md`)과 archive revalidation(`scripts/revalidate_key_rotation.py`) 없이 프로덕션 배포 금지. |
| uid 모델이 공유 호스트에서 우회 가능(예: sudo 오설정으로 runner가 observer_dir에 접근) | `isolation.probe_isolation_facts`가 매 spawn마다 `observer_dir` 권한(`0700`)과 uid 분리를 실측하고, `verify_isolation_boundary`가 facts에서 boundary를 재도출한다. 설치 스크립트가 사전 조건(별도 시스템 유저 존재, 권한 `0700`)을 `--self-test`로 강제 검증한 뒤에만 실행을 허용한다. |
| 능력이 늘어날수록(W2~W5) evidence emitter가 어댑터별로 파편화되어 다시 self-report로 회귀 | 모든 어댑터는 동일한 runner-receipt 스키마를 방출해야 하며, Depone은 어댑터를 알지 못한 채 검증한다(§3 아키텍처). 신규 어댑터 추가 시 Depone의 changed-tier contract(`scripts/check_contract.py --tier changed`)를 게이트로 재사용해 스키마 이탈을 CI에서 차단한다. |
| "evidence-pending" 규율이 시간이 지나며 UX 압박(빠른 완료 신호를 원하는 사용자 요구)에 잠식 | 이 규율은 spec 불변식으로 코드 레벨 lint/테스트에 고정한다(예: CLI 출력 문자열에 대한 금칙어 테스트: `VERIFIED`, `DONE`, `COMPLETE` 단독 출력 금지, 반드시 `evidence-pending` 또는 verdict 값과 함께 출력). |
| 비용/자율성 서킷브레이커(M10)가 W5까지 없어 초기 웨이브에서 폭주 비용 리스크 | W1~W4 동안은 자율성 기본값을 보수적으로 유지(auto-retry/auto-spawn 기본 off, 명시적 opt-in만 허용)하고, M10 도달 전까지 이 기본값을 낮추지 않는다. |
| gh/npm/도메인 이름 충돌 | 실측 완료(2026-07-01): npm·gh org `witnessd`·witnessd.dev/.io/.sh/.app 전부 비어있음. 무관 소형 레포 `roadkell/witnessd`(systemd inotify 감시기)만 존재하며 org·패키지·도메인 미충돌 — `witnessd` 확정. |

### 8.4 향후 로드맵

- **keyless 서명 축 (Sigstore Fulcio keyless + Rekor transparency log)**: W1~W5 완료 후 별도 웨이브(W6 후보)로 착수. 전제조건 — operator-key 서명 축(현재 스코프, assurance 등급 아님)이 최소 1개 프로덕션 팀 배포에서 key 회전 런북과 archive revalidation과 함께 운영 검증을 마친 뒤에만 keyless로 확장한다. keyless 도입 시에도 operator-key 경로를 deprecate하지 않고 병행 지원(두 서명 모델 모두 Depone이 재도출 가능해야 함).
- **추가 어댑터**: Codex/Claude Code/OpenCode 이후, W4 완료 시점의 실제 채택 신호에 따라 추가 substrate(예: Cursor, 사내 CI 러너)를 어댑터로 편입한다. 신규 어댑터는 항상 기존 runner-receipt 스키마를 만족해야 하며 스키마 확장이 필요하면 Depone 쪽 변경을 선행 승인 받는다.
- **관측 UI**: append-only 서명 이벤트 로그(M1/E8) 위에 read-only dashboard를 얹어 team-ledger·heartbeat·isolation facts를 실시간 조회할 수 있게 한다. 이 UI는 evidence emitter의 SoT를 절대 우회하지 않는 순수 projection이어야 하며(런타임 상태를 쓰지 않음), "ORCHESTRATION COMPLETE" 류 문구를 UI에도 동일하게 금지한다(§8.1 UX 규율 적용 범위는 CLI뿐 아니라 UI 전체).
- **벤치마크/과대광고 방지 규율 재확인**: Depone spec의 "No public benchmark or superiority claim" non-goal은 별도 repo인 witnessd에는 직접 적용되지 않지만, witnessd도 자체적으로 다음 규율을 채택한다 — (1) "evidence-pending"을 하드 규칙으로 유지하고 이를 우회하는 UX 변경은 별도 RFC 없이 병합 금지, (2) 공개 마케팅에서 "VERIFIED"/"proven"/"guaranteed" 류 표현을 사용할 때는 반드시 어떤 assurance 레벨(A1/A2, 상한 A2)이 어떤 조건에서 재도출되었는지(+ signing_status) 구체적으로 명시하고, Depone 외부 검증기가 실제로 실행한 명령을 함께 공개한다, (3) 경쟁 제품과의 직접 비교 벤치마크는 이 spec의 범위 밖이며 별도 문서에서 별도 승인 절차를 거친다.
