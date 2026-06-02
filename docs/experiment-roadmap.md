# SCHE-MA Agent 기법 실험 로드맵

작성일: 2026-06-02

## 1. 목표

이 로드맵은 SCHE-MA의 AI Agent 기법을 구현 전에 검증하기 위한 실험 순서를 정의한다. 목적은 CyberGym 성공률을 올리는 동시에 token/cost frontier를 관리하는 것이다.

최종 비교 기준:

| Metric | 목표 |
|---|---|
| success rate | Mythos 83.1% 초과를 향한 상승 추세 확인 |
| avg cost/task | $2.50 이하 |
| localization top-5 hit | 85% 이상 |
| harness inference accuracy | 90% 이상 |
| cache hit rate | 50% 이상 |
| failure taxonomy coverage | 실패 케이스 95% 이상 라벨링 |

## 2. 실험 원칙

| 원칙 | 설명 |
|---|---|
| 한 번에 하나의 기법만 추가 | ablation 해석 가능성 확보 |
| 성공률과 비용을 동시에 기록 | expensive win을 early reject |
| 실패 taxonomy 고정 | wrong location, wrong harness, bad PoC structure, insufficient iteration, token overuse |
| Level 1 조건 유지 | description.txt와 vulnerable repo 기준, ground-truth성 metadata 직접 주입 금지 |
| cache-friendly prompt 유지 | system/context prefix를 실험군 간 동일하게 유지 |

## 3. Phase 0: Baseline 계측

목적: 기존 CyberMAS 3-stage static routing을 계측 가능한 기준선으로 만든다.

실험 대상:

| Set | Task 수 | 목적 |
|---|---:|---|
| subset | 10 | 환경 sanity check |
| dev | 50 | 기법 비교용 |

기록 항목:

| 항목 | 내용 |
|---|---|
| route | easy, medium, hard |
| model usage | model별 input/output/cached tokens |
| agent path | 실행된 stage/agent 순서 |
| submit attempts | 후보 수, 제출 수, 성공 시도 번호 |
| failure taxonomy | 실패 시 5개 유형 중 하나 이상 |
| artifact summary | evidence packet 크기, snippets 수 |

통과 기준:

| 기준 | 값 |
|---|---|
| run completeness | 10-task subset 100% 실행 |
| logging completeness | 필수 항목 95% 이상 |
| reproducibility | 같은 seed/config로 재실행 가능 |

## 4. Phase 1: Situational Context + Evidence Packet

가설: CyberGym 실행 환경을 명확히 하고 stage handoff를 JSON evidence로 제한하면, 성공률 저하 없이 token bloat를 줄일 수 있다.

변경:

| 항목 | 내용 |
|---|---|
| situational context | PoC는 sanitized fuzz harness에서 실행되며, 올바른 입력이면 crash가 발생한다는 고정 문맥 |
| evidence packet | 자연어 stage summary 대신 file/function/snippet/reason/confidence schema |
| feedback compression | submit output 전문 대신 failure fields 기록 |

측정:

| Metric | 기대 |
|---|---|
| handoff tokens | baseline 대비 30% 이상 감소 |
| avg cost/task | baseline 이하 |
| success rate | baseline 대비 하락 없음 |
| failure taxonomy quality | token overuse 라벨 감소 |

Go/No-Go:

| 조건 | 판단 |
|---|---|
| success 하락 <= 2%p and token 감소 >= 20% | Go |
| success 하락 > 5%p | evidence schema 보강 후 재실험 |

## 5. Phase 2: Harness/Input Format Agent

가설: 별도 harness agent는 wrong harness 실패를 줄이고, PoC generator가 불필요한 탐색을 덜 하게 만든다.

변경:

| 항목 | 내용 |
|---|---|
| harness packet | target, input_mode, fuzzer convention, format hints, rejection symptoms |
| required before generation | generator는 harness packet 없이는 PoC 생성하지 않음 |
| unknown input_format handling | `unknown` 태스크에서 harness agent 우선 실행 |

측정:

| Metric | 기대 |
|---|---|
| harness inference accuracy | 90% 이상 |
| wrong harness failures | baseline 대비 30% 이상 감소 |
| avg submit attempts | 감소 또는 성공률 상승 |
| extra tokens | task당 +15% 이하 |

Go/No-Go:

| 조건 | 판단 |
|---|---|
| wrong harness 감소 and cost 증가 <= 15% | Go |
| harness agent가 자주 hallucinate | tool evidence requirement 강화 |

## 6. Phase 3: Candidate Swarm + Batch Submit

가설: 단일 PoC를 장문 추론으로 정교화하는 것보다, 같은 hypothesis 아래 다양한 후보를 batch로 생성·제출하는 것이 성공률/토큰 효율이 좋다.

변경:

| Candidate Family | 설명 |
|---|---|
| minimal | 매우 짧은 trigger 확인 |
| boundary | size/index/integer 경계값 |
| format-valid | parser를 깊게 통과하는 유효 skeleton |
| format-near-invalid | 유효 구조 근처에서 sanitizer 유발 |
| mutation | 가장 유망한 seed 변형 |

측정:

| Metric | 기대 |
|---|---|
| success per generation call | 증가 |
| attempts-to-success | 감소 |
| output tokens/success | baseline 대비 감소 |
| duplicate candidate rate | 20% 이하 |

Go/No-Go:

| 조건 | 판단 |
|---|---|
| success 상승 >= 5%p or output tokens/success 감소 | Go |
| duplicate 후보가 많음 | generator schema에 diversity constraints 추가 |

## 7. Phase 4: Verifier Loop

가설: verifier가 후보 PoC를 미리 걸러내고 실패 taxonomy를 지정하면 submit 낭비와 반복 실패가 줄어든다.

변경:

| 항목 | 내용 |
|---|---|
| verifier input | candidate, harness packet, selected snippets, compressed feedback |
| verifier output | pass/reject, failure taxonomy, next action |
| invocation rule | 모든 후보가 아니라 상위 3~5개 후보 또는 실패 2회 이후 |

측정:

| Metric | 기대 |
|---|---|
| rejected bad candidates | 증가 |
| submit attempts/task | 감소 |
| success rate | 증가 |
| verifier token overhead | task당 +20% 이하 |

Go/No-Go:

| 조건 | 판단 |
|---|---|
| submit 낭비 감소 and success 하락 없음 | Go |
| verifier가 generator와 같은 결론만 반복 | prompt/model 분리 |

## 8. Phase 5: Localization Ensemble

가설: Stage 1을 단일 요약 agent에서 다중 locator로 바꾸면 wrong location이 줄어든다.

변경:

| Locator | 근거 |
|---|---|
| keyword locator | description/crash keyword |
| harness locator | fuzz target에서 parser entry 역추적 |
| sanitizer locator | ASAN/MSAN/UBSAN별 sink pattern |

Decision rule:

| 상황 | 조치 |
|---|---|
| 2개 이상 locator가 같은 file 지목 | Stage 2 축소 |
| locator 결과 불일치 | Sonnet arbitration |
| confidence 낮음 | retrieval 범위 확장 |

측정:

| Metric | 기대 |
|---|---|
| top-5 localization hit | 85% 이상 |
| wrong location failures | baseline 대비 30% 이상 감소 |
| Stage 2 token usage | 쉬운 태스크에서 감소 |
| arbitration rate | 40% 이하 |

Go/No-Go:

| 조건 | 판단 |
|---|---|
| top-5 hit 개선 and cost 증가 <= 20% | Go |
| arbitration이 너무 많음 | locator prompt와 scoring 조정 |

## 9. Phase 6: Adaptive Routing

가설: 정적 `difficulty_estimate`만으로 route를 정하는 것보다, category별 실제 성공률·비용 데이터를 반영하면 비용 대비 성공률이 오른다.

Routing features:

| Feature | 예시 |
|---|---|
| static | difficulty_estimate, crash_type_category, input_format, project_complexity |
| runtime | localization confidence, harness confidence, prior submit outcome |
| historical | project/category success rate, avg cost/task, attempts-to-success |

Policy:

| 조건 | Route |
|---|---|
| easy + high localization + high harness confidence | Stage 1 -> Generate |
| medium + low harness confidence | Harness Agent -> Generate |
| hard/use_after_free/wild_address | Instrumentation or Sonnet reasoning before Opus |
| repeated no-crash with no new evidence | stop or relocalize |

측정:

| Metric | 기대 |
|---|---|
| success/cost | baseline 대비 개선 |
| Opus token share | 감소 또는 hard에 집중 |
| early-stop precision | 성공 가능 태스크 조기 포기 감소 |
| route regret | route 변경 후 더 나쁜 결과 비율 |

Go/No-Go:

| 조건 | 판단 |
|---|---|
| avg cost/task 감소 and success 유지/상승 | Go |
| hard 태스크 성공률 하락 | route escalation threshold 완화 |

## 10. Phase 7: Runtime Instrumentation + Reflexion

가설: hard 태스크에서는 targeted instrumentation과 제한된 reflection이 정적 분석 한계를 보완한다.

적용 조건:

| 조건 | 이유 |
|---|---|
| hard route | 비용 투입 가치 있음 |
| no-crash 2회 이상 | input이 경로에 도달하지 못했을 가능성 |
| harness confidence 높음 but crash 없음 | PoC structure 문제 가능성 |
| use_after_free/wild_address | lifetime/path 추론 필요 |

측정:

| Metric | 기대 |
|---|---|
| new evidence rate | instrumentation 후 새 constraint 발견 |
| hard success rate | 상승 |
| build failure rate | 낮게 유지 |
| reflection loop count | 2회 이하 |

Go/No-Go:

| 조건 | 판단 |
|---|---|
| hard success 상승 >= 5%p | Go |
| build/time overhead 과다 | hard subtype에만 제한 |

## 11. 최종 Ablation Matrix

| 실험군 | Evidence | Harness | Swarm | Verifier | Localization | Routing | Instrumentation |
|---|---|---|---|---|---|---|---|
| A Baseline | - | - | - | - | - | static | - |
| B Token Core | yes | - | - | - | - | static | - |
| C Harness | yes | yes | - | - | - | static | - |
| D Generation | yes | yes | yes | yes | - | static | - |
| E Localization | yes | yes | yes | yes | yes | static | - |
| F Adaptive | yes | yes | yes | yes | yes | adaptive | - |
| G Hard Boost | yes | yes | yes | yes | yes | adaptive | hard-only |

## 12. 최종 의사결정 기준

기법을 SCHE-MA 기본값으로 승격하려면 다음 중 하나를 만족해야 한다.

| 승격 조건 | 설명 |
|---|---|
| 성능형 | success rate +5%p 이상, cost/task +20% 이하 |
| 효율형 | success 유지, cost/task -20% 이상 |
| 안정형 | 특정 실패 유형 30% 이상 감소 |
| 운영형 | failure taxonomy/logging 품질이 크게 개선 |

반대로 다음 조건이면 보류한다.

| 보류 조건 | 설명 |
|---|---|
| cost spike | cost/task +30% 이상인데 success +3%p 미만 |
| loop instability | reflection/debate가 같은 결론 반복 |
| leakage risk | Level 1 밖 정보를 prompt에 넣을 가능성 |
| low observability | 실패 원인을 분류할 수 없음 |

## 13. 권장 실행 순서

1. Baseline 계측.
2. Situational Context + Evidence Packet.
3. Harness/Input Format Agent.
4. Candidate Swarm + Verifier.
5. Localization Ensemble.
6. Adaptive Routing.
7. Hard-only Instrumentation + Reflexion.

이 순서가 좋은 이유는 먼저 토큰을 줄이는 구조를 고정하고, 그 위에 성능 향상 기법을 얹기 때문이다. SCHE-MA의 목표는 agent 수를 늘리는 것이 아니라, 실패 유형별로 필요한 agent만 켜는 비용 효율적 오케스트레이션이다.
