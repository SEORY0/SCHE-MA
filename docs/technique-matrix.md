# SCHE-MA Agent 기법 매트릭스

작성일: 2026-06-02

평가 기준:

| 등급 | 의미 |
|---|---|
| Token Cost 낮음 | 추가 LLM 호출이 없거나 소형 모델 1회 이하 |
| Token Cost 중간 | 소형/중형 모델 호출 또는 제한된 반복 |
| Token Cost 높음 | 복수 agent round, long-context, 반복 reflection |
| Fit 높음 | CyberGym 실패 유형과 직접 연결 |
| Leakage Risk 높음 | Level 1 밖의 ground-truth성 정보 사용 위험 |

## 1. 종합 매트릭스

| 기법 | 목적 | 기대 효과 | Token Cost | 구현 난이도 | CyberGym Fit | 주요 실패 위험 | 우선순위 |
|---|---|---|---|---|---|---|---|
| Situational Context | PoC 실행 환경을 명확히 주입 | insufficient iteration 감소 | 낮음 | 낮음 | 높음 | 너무 장황하면 cache prefix 비대화 | P1 |
| Evidence Packet | Stage 간 handoff 압축 | token overuse 감소 | 낮음 | 중간 | 높음 | schema가 느슨하면 자연어 요약으로 회귀 | P1 |
| Harness/Input Format Agent | fuzzer convention과 입력 모드 추론 | wrong harness 감소 | 낮음~중간 | 중간 | 매우 높음 | runner 오해 시 전체 PoC 후보 실패 | P1 |
| Candidate Swarm | 다양한 PoC 후보 batch 생성 | bad PoC structure 감소 | 낮음~중간 | 중간 | 매우 높음 | 후보 다양성이 낮으면 submit 낭비 | P1 |
| Critic/Verifier | 후보 PoC와 경로 가설 검증 | bad PoC structure, wrong harness 감소 | 중간 | 중간 | 높음 | self-rubber-stamp, 과도한 재작업 | P1 |
| Adaptive Routing | 실제 성공률/비용으로 route 조정 | token overuse 감소 | 낮음 | 중간 | 높음 | 초기 표본 편향 | P1 |
| Retrieval Gating | LLM 투입 전 파일·snippet 제한 | token overuse, wrong location 감소 | 낮음 | 중간 | 높음 | 검색 키워드가 틀리면 recall 하락 | P1 |
| Localization Ensemble | 다각도 취약 위치 후보 생성 | wrong location 감소 | 중간 | 중간 | 높음 | 후보 병합 실패, false confidence | P2 |
| Reflexion | 실패 피드백을 다음 시도 지침으로 변환 | insufficient iteration 감소 | 중간~높음 | 중간 | 중간~높음 | 반복 비용 폭증 | P2 |
| Runtime Instrumentation | 런타임 도달성·데이터 흐름 확인 | wrong location, bad structure 감소 | 중간~높음 | 높음 | 높음 | 빌드 실패, 시간 초과 | P2 |
| Project/Harness Memory | 반복 프로젝트 지식 재사용 | wrong harness, bad structure 감소 | 낮음~중간 | 중간 | 중간~높음 | task-specific 정답 누수 | P2 |
| Planner-Worker | 전략과 작업 실행 분리 | wrong location, iteration 품질 개선 | 중간 | 중간 | 중간 | planner 호출 비용 중복 | P2 |
| Multi-Agent Debate | 상충 가설 검토 | wrong location 감소 | 높음 | 중간 | 제한적 | 비용 대비 효과 낮음 | P3 |
| Always-on Planner | 매 turn 전략 재계획 | 일부 hard case 개선 | 높음 | 중간 | 낮음~중간 | token overuse | P3 |
| Long Natural Summary | 코드베이스를 자연어로 요약 | 후속 agent 이해 보조 | 높음 | 낮음 | 낮음 | 근거 손실, 비용 증가 | P3 |

## 2. 실패 유형별 추천 기법

| 실패 유형 | 1차 기법 | 2차 기법 | 보류 기법 |
|---|---|---|---|
| wrong location | Retrieval Gating, Localization Ensemble | Planner-Worker, Runtime Instrumentation | Full Debate |
| wrong harness | Harness/Input Format Agent, Situational Context | Project/Harness Memory, Verifier | Long Summary |
| bad PoC structure | Candidate Swarm, Verifier | Runtime Instrumentation, Reflexion | Debate |
| insufficient iteration | Budget-Aware Stopping, Reflexion | Planner checkpoint | Always-on Planner |
| token overuse | Evidence Packet, Prompt Caching | Adaptive Routing, Feedback Compression | Long Summary |

## 3. Agent 역할별 권장 입력/출력

### 3.1 Locator Agent

| 항목 | 권장 |
|---|---|
| 입력 | description, crash_type_category, fuzz_target name, file index/search results |
| 출력 | top-k file/function candidates, evidence, confidence |
| 금지 | 전체 코드베이스 요약, 장황한 reasoning |
| 성공 지표 | top-5 localization hit rate |

### 3.2 Harness Agent

| 항목 | 권장 |
|---|---|
| 입력 | README, submit.sh, build/run scripts, fuzzer source, target binary name |
| 출력 | input_mode, fuzzer convention, minimal format skeleton, rejection symptoms |
| 금지 | 취약점 exploit reasoning |
| 성공 지표 | harness format accuracy |

### 3.3 Generator Agent

| 항목 | 권장 |
|---|---|
| 입력 | evidence packet, harness packet, selected snippets, previous compressed feedback |
| 출력 | candidate family, candidate payloads or scripts, expected failure/success signal |
| 금지 | 새 파일 탐색을 무제한 수행 |
| 성공 지표 | successful submit count, attempts-to-success |

### 3.4 Verifier Agent

| 항목 | 권장 |
|---|---|
| 입력 | 후보 PoC, harness packet, relevant snippets, submit feedback summary |
| 출력 | pass/reject, failure taxonomy, next action |
| 금지 | 생성 agent와 동일한 장황한 chain 반복 |
| 성공 지표 | rejected bad candidates, improved next-attempt success |

### 3.5 Router

| 항목 | 권장 |
|---|---|
| 입력 | difficulty_estimate, crash_type_category, input_format, prior success/cost stats |
| 출력 | route, model allocation, max attempts, escalation condition |
| 금지 | LLM 장문 판단 의존 |
| 성공 지표 | cost/task, success/cost frontier |

## 4. 토큰 최적화 매트릭스

| 기법 | 절감 대상 | 절감 방식 | 측정 지표 | 위험 |
|---|---|---|---|---|
| Prompt Caching | 반복 system/context tokens | 고정 prefix, cache breakpoint | cached_tokens, cache hit rate | 동적 값이 prefix에 섞이면 cache miss |
| Evidence Packet | Stage handoff tokens | JSON schema와 snippet_ref | handoff token count | 중요한 근거 누락 |
| Retrieval Gating | 코드 원문 tokens | search -> rank -> admit | tokens per snippet, top-k hit | recall 손실 |
| Feedback Compression | submit loop tokens | 로그를 failure fields로 압축 | feedback tokens/attempt | 오류 메시지 세부 정보 손실 |
| Budget-Aware Stopping | 반복 호출 tokens | route별 max attempts와 escalation | attempts/task, cost/task | 너무 이른 포기 |
| Small Model First | 초기 분석 비용 | Haiku/Sonnet 선별 후 Opus 투입 | Opus token share | 쉬운 태스크 misroute |
| Batch Stage 1 | 대량 분석 비용 | 동일 prefix와 bulk calls | batch cost/task | batch latency |

## 5. 성능 향상 매트릭스

| 기법 | 개선 대상 | 적용 조건 | 측정 지표 | 예상 효과 |
|---|---|---|---|---|
| Localization Ensemble | 취약 위치 탐색 | medium/hard 또는 locator 불일치 | top-1/top-5 hit | 중간~높음 |
| Harness Agent | 입력 형식 추론 | 모든 태스크, 특히 unknown input_format | harness accuracy | 높음 |
| Runtime Instrumentation | 도달성 확인 | hard, no-crash 반복 | reached parser, new evidence | 중간~높음 |
| Candidate Swarm | PoC 다양성 | harness가 확실한 태스크 | success per batch | 높음 |
| Verifier Loop | 후보 품질 | 후보 5개 이상 또는 실패 반복 | verifier precision | 중간 |
| Project Memory | 반복 프로젝트 | 동일 project 2회 이상 | reused template success | 중간 |
| Adaptive Routing | 비용 대비 성공률 | 50개 이상 실행 후 | success/cost by route | 중간~높음 |

## 6. 우선순위 결론

SCHE-MA가 먼저 검증해야 할 조합은 다음이다.

1. `Situational Context + Evidence Packet + Harness Agent`
2. `Candidate Swarm + Verifier`
3. `Localization Ensemble`
4. `Adaptive Routing`
5. `Runtime Instrumentation + Reflexion`

이 순서는 토큰 효율을 먼저 확보한 뒤 성능 향상 기법을 추가하는 구조다. 반대로 debate, always-on planner, long natural summary는 비용이 먼저 커지기 때문에 초기에 배제한다.
