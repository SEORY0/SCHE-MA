# SCHE-MA Agent 기법 연구·분석 보고서

작성일: 2026-06-02

## 1. 목적

SCHE-MA(Security CHallenge Exploitation-Multi Agent)는 CyberGym Level 1 조건에서 Mythos 수준을 넘는 성능을 목표로 한다. 이 문서는 구현 계획이 아니라, SCHE-MA의 **토큰 최적화**와 **PoC 생성 성공률 향상**을 위해 조사·비교해야 할 AI Agent 기법을 정리한다.

핵심 판단 기준은 단순 정확도 상승이 아니다. CyberGym은 대형 코드베이스, 제한된 설명, 별도 submission server, 다양한 fuzz harness가 동시에 작동하는 벤치마크이므로, 각 기법은 다음 네 가지 질문에 답해야 한다.

| 질문 | 의미 |
|---|---|
| 어떤 실패 유형을 줄이는가 | wrong location, wrong harness, bad PoC structure, insufficient iteration, token overuse |
| 토큰을 얼마나 늘리는가 | 입력 토큰, 출력 토큰, 반복 호출 수, cache hit 가능성 |
| 검증 가능한가 | 10-task subset 또는 50-task dev set에서 ablation 가능 여부 |
| leaderboard 조건에 안전한가 | Level 1 입력 밖의 ground-truth성 정보 누수 여부 |

## 2. CyberGym/SCHE-MA 문제 정의

CyberGym 논문은 1,507개 실세계 취약점과 188개 프로젝트를 포함하며, agent는 주어진 취약점 설명과 취약 버전 코드베이스만으로 PoC를 만들어 취약점을 재현해야 한다고 설명한다. 논문 초록 기준 top-performing 조합도 약 20% 성공률에 머물렀고, 이는 단일 모델 성능보다 시스템 설계가 중요하다는 신호다. [CyberGym arXiv](https://arxiv.org/abs/2506.02548)

Microsoft MDASH는 2026년 5월 공개 자료에서 CyberGym Level 1 기준 88.45% 성공률을 보고했고, 다음 entry인 83.1%보다 약 5%p 높다고 밝혔다. 같은 글은 harness-format mismatch, 예를 들어 libFuzzer 형식으로 만든 입력이 실제로는 honggfuzz 형식을 요구해 실패한 사례를 언급한다. [Microsoft Security Blog](https://www.microsoft.com/en-us/security/blog/2026/05/12/defense-at-ai-speed-microsofts-new-multi-model-agentic-security-system-tops-leading-industry-benchmark/)

SCHE-MA 로컬 분석 자료(`/data/seory0/projects/CyberMAS`) 기준 CyberGym 1,507개 태스크는 다음 구조를 가진다.

| 축 | 주요 분포 |
|---|---|
| difficulty_estimate | easy 817, medium 448, hard 242 |
| crash_type_category | memory_access 756, uninit_value 250, wild_address 185, use_after_free 168 |
| input_format | unknown 1,137, other 177, image 68, document 37, network 32 |
| sanitizer | asan 1,094, msan 265, ubsan 100 |

따라서 SCHE-MA의 우선순위는 모든 문제를 깊게 푸는 것이 아니라, **대다수 easy/medium 태스크에서 불필요한 토큰을 줄이고**, hard 태스크에는 **정확한 위치·harness·PoC 구조 추론**을 위해 토큰을 집중하는 것이다.

## 3. 실패 유형 분류

SCHE-MA의 연구와 실험은 실패 원인을 다음 5개로 고정해 기록해야 한다.

| 실패 유형 | 정의 | 줄일 수 있는 기법 |
|---|---|---|
| wrong location | 취약 파일/함수 후보가 틀림 | localization ensemble, retrieval gating, planner-worker |
| wrong harness | fuzz target, argv/stdin/file mode, libFuzzer/honggfuzz 형식 오해 | harness inference agent, situational context, tool-use |
| bad PoC structure | 취약 경로는 맞지만 파일 포맷·길이·magic·chunk 구조가 불충분 | candidate swarm, format template memory, verifier loop |
| insufficient iteration | 실패 피드백을 잘못 해석하거나 너무 빨리 포기 | Reflexion, critic/verifier, budget-aware stopping |
| token overuse | 큰 코드 원문·반복 로그·장황한 agent handoff로 비용 증가 | evidence packet, prompt caching, context compression |

depthfirst는 CyberGym에서 situational context, instrumentation, subagent 설계를 적용해 성능을 끌어올렸다고 설명한다. 특히 PoC가 sanitized fuzz harness에서 실행된다는 환경 설명과 runtime instrumentation이 중요했다고 보고했다. [depthfirst 분석](https://depthfirst.com/post/agent-capability-is-a-system-design-problem-lessons-from-a-90-improvement-on-cybergym)

## 4. Agent 기법 조사

### 4.1 Planner-Worker

Planner가 전체 전략과 예산을 정하고 Worker가 좁은 작업을 수행하는 구조다. CyberGym에서는 planner가 “무엇을 더 읽을지”를 정하는 순간마다 토큰 비용이 커질 수 있으므로, planner는 장황한 사고를 만들기보다 `next_action`, `budget`, `stop_condition`만 내야 한다.

SCHE-MA 적용:

| 항목 | 판단 |
|---|---|
| 기대 효과 | wrong location, insufficient iteration 감소 |
| 토큰 비용 | 중간. planner가 매 turn 호출되면 비싸므로 stage boundary에서만 호출 |
| 추천 형태 | always-on planner가 아니라 routing/planning checkpoint |
| 우선순위 | Priority 2 |

### 4.2 ReAct / Tool-Augmented Agent

ReAct는 reasoning trace와 task-specific action을 번갈아 생성해 외부 도구와 추론을 결합하는 방식이다. [ReAct arXiv](https://arxiv.org/abs/2210.03629)

CyberGym에서는 `rg`, `file`, `strings`, `xxd`, build script inspection, submit feedback parsing이 모두 도구 사용에 해당한다. 다만 모델이 도구를 너무 많이 호출하면 token overuse가 생긴다.

SCHE-MA 적용:

| 항목 | 판단 |
|---|---|
| 기대 효과 | wrong location, wrong harness 감소 |
| 토큰 비용 | 낮음~중간. 도구 출력 clipping 정책이 있으면 효율적 |
| 추천 형태 | “도구 호출 후 전체 로그 붙이기” 금지, 20~80줄 evidence만 전달 |
| 우선순위 | Priority 1 |

### 4.3 Reflection / Reflexion

Reflexion은 scalar 또는 free-form feedback을 언어적 기억으로 변환해 다음 시도에 반영하는 기법이다. 논문은 coding, sequential decision-making, reasoning task에서 baseline 대비 개선을 보고했다. [Reflexion arXiv](https://arxiv.org/abs/2303.11366)

CyberGym에서는 submit 결과를 “다음 시도에서 피해야 할 가설”로 압축하는 데 적합하다. 단, 무제한 reflection은 비용이 빠르게 증가한다.

SCHE-MA 적용:

| 항목 | 판단 |
|---|---|
| 기대 효과 | insufficient iteration, bad PoC structure 감소 |
| 토큰 비용 | 중간~높음. loop cap 필수 |
| 추천 형태 | 실패 2회 이상 또는 hard 태스크에서만 호출 |
| 우선순위 | Priority 2 |

### 4.4 Critic / Verifier

Verifier는 PoC 후보가 취약 경로, harness mode, sanitizer crash 조건을 만족하는지 별도로 검토한다. 단일 agent 자기검열은 쉽게 rubber-stamp가 되므로, verifier는 생성 agent와 다른 prompt·입력·모델로 분리하는 편이 낫다.

SCHE-MA 적용:

| 항목 | 판단 |
|---|---|
| 기대 효과 | bad PoC structure, wrong harness 감소 |
| 토큰 비용 | 중간. 모든 후보를 검증하면 비쌈 |
| 추천 형태 | 후보 batch 생성 후 상위 3~5개만 verifier 통과 |
| 우선순위 | Priority 1 |

### 4.5 Debate

Debate는 여러 agent가 서로 다른 해석을 제시하고 judge가 결론을 고르는 방식이다. CyberGym에서는 취약 위치 후보가 갈릴 때 유용하지만, 모든 태스크에 적용하면 비용 대비 효율이 나쁘다.

SCHE-MA 적용:

| 항목 | 판단 |
|---|---|
| 기대 효과 | wrong location 감소 |
| 토큰 비용 | 높음 |
| 추천 형태 | Stage 1 localization 후보가 충돌할 때만 제한 적용 |
| 우선순위 | Priority 3 |

### 4.6 Self-Consistency / Multi-Candidate Generation

Self-consistency는 여러 추론 경로를 샘플링하고 일관된 답을 선택하는 방식이다. [Self-Consistency arXiv](https://arxiv.org/abs/2203.11171)

CyberGym에서는 “정답 텍스트”보다 “실행 가능한 PoC”가 목표이므로, reasoning voting보다 candidate swarm이 더 실용적이다. 즉, 여러 PoC 후보를 생성하고 submit feedback으로 선택한다.

SCHE-MA 적용:

| 항목 | 판단 |
|---|---|
| 기대 효과 | bad PoC structure 감소 |
| 토큰 비용 | 낮음~중간. 후보 생성을 한 번에 묶으면 효율적 |
| 추천 형태 | 10~50개 PoC 후보를 family별로 생성 |
| 우선순위 | Priority 1 |

### 4.7 Adaptive Routing / Bandit Orchestration

현재 CyberMAS는 정적 `difficulty_estimate`로 easy/medium/hard를 분리한다. 이 방식은 시작점으로 충분하지만, 50~100개 실행 뒤에는 실제 성공률과 비용 데이터를 반영해야 한다.

SCHE-MA 적용:

| 항목 | 판단 |
|---|---|
| 기대 효과 | token overuse 감소, hard 태스크 성능 향상 |
| 토큰 비용 | 낮음. LLM 호출보다 orchestration 정책 |
| 추천 형태 | category/project/input_format별 성공률과 비용으로 route prior 갱신 |
| 우선순위 | Priority 1 |

### 4.8 Memory / Retrieval

Memory는 이전 태스크의 성공 PoC 구조, 실패 원인, 프로젝트별 harness 관찰을 재사용하는 방식이다. CyberGym에는 같은 프로젝트가 반복되므로 프로젝트 메모리는 효과가 있을 수 있다. 단, Level 1 외 ground-truth 정보와 성공 PoC 원문을 무분별하게 저장하면 평가 누수 문제가 생긴다.

SCHE-MA 적용:

| 항목 | 판단 |
|---|---|
| 기대 효과 | wrong harness, bad PoC structure 감소 |
| 토큰 비용 | 낮음~중간. retrieval gating 필수 |
| 추천 형태 | project/harness/input_format별 짧은 template memory |
| 우선순위 | Priority 2 |

## 5. 토큰 최적화 기법

### 5.1 Prompt Caching

Anthropic 문서는 prompt caching이 cache breakpoint까지의 prompt prefix를 재사용해 처리 시간과 비용을 줄이며, 기본 5분 TTL과 1시간 TTL 옵션을 제공한다고 설명한다. 같은 문서는 cache hit token이 base input token의 0.1배 가격이라고 제시한다. [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

OpenAI도 1,024 tokens 이상 공통 prefix에 대해 자동 prompt caching을 적용하고, `cached_tokens`를 usage에 표시한다고 설명한다. [OpenAI prompt caching](https://openai.com/index/api-prompt-caching/)

SCHE-MA 적용 원칙:

| Prompt 영역 | 캐시 전략 |
|---|---|
| system role, CyberGym situational context, output schema | 고정 prefix로 캐시 |
| agent별 instruction | stage별 고정 block으로 캐시 |
| task description, selected snippets, submit feedback | cache 뒤 동적 영역 |
| timestamps, random ids, per-run labels | cache prefix 앞에 두지 않음 |

### 5.2 Evidence Packet

Stage 간 자연어 요약은 보기에는 친절하지만 토큰을 많이 쓰고, 후속 agent가 근거와 추측을 섞어 읽게 만든다. SCHE-MA는 각 stage handoff를 다음 구조로 제한해야 한다.

```json
{
  "task_id": "arvo:10400",
  "route": "medium",
  "hypotheses": [
    {
      "file": "coders/mvg.c",
      "function": "ReadMVGImage",
      "reason": "description keyword and parser entry match",
      "confidence": 0.72,
      "snippet_ref": "S1"
    }
  ],
  "harness": {
    "target": "mvg_fuzzer",
    "input_mode": "file",
    "required_format": "MVG text file"
  },
  "next_action": "instrument_or_generate"
}
```

핵심은 코드 원문을 전달하지 않고 `snippet_ref`와 최소 evidence만 넘기는 것이다.

### 5.3 Retrieval Gating

코드베이스 전체를 LLM에게 주지 않고, retrieval 후보를 좁힌 뒤 필요한 snippet만 제공한다.

| 단계 | 정책 |
|---|---|
| lexical search | crash keyword, fuzz target, parser name, file extension |
| structural search | call graph, harness entry, input read function |
| LLM ranking | 후보 20개 이하일 때만 사용 |
| context admission | file/function/snippet/reason/confidence가 있는 evidence만 허용 |

### 5.4 Feedback Compression

submit loop에서는 같은 실패 로그를 매번 붙이지 않는다. 실패 로그는 다음 필드로 압축한다.

| 필드 | 예시 |
|---|---|
| observed_exit | no crash, sanitizer crash, timeout |
| reached_harness | yes/no/unknown |
| parser_error | invalid magic, short read, checksum mismatch |
| changed_since_last | length increased, header fixed, chunk added |
| rejected_hypothesis | “single-byte input reaches parser” |

### 5.5 Budget-Aware Stopping

중단 조건은 단순 “N회 실패”가 아니라 route와 evidence quality를 반영해야 한다.

| Route | 기본 반복 | 연장 조건 | 중단 조건 |
|---|---:|---|---|
| easy | 2~3 | harness 확실, 후보가 다양함 | 같은 no-crash 2회 |
| medium | 4~6 | parser 진입 증거 있음 | location confidence < 0.5 |
| hard | 6~10 | instrumentation이 새 정보 제공 | reflection 후 새 가설 없음 |

## 6. 성능 향상 기법

### 6.1 Localization Ensemble

Stage 1의 성공 기준은 “좋은 요약”이 아니라 취약 위치 top-k hit rate다. 따라서 서로 다른 시각의 저비용 locator를 병렬 실행한다.

| Locator | 역할 |
|---|---|
| keyword locator | description/crash type과 symbol/file name 매칭 |
| harness locator | fuzz target에서 parser entry까지 추적 |
| sanitizer locator | ASAN/MSAN/UBSAN별 취약 sink 패턴 검색 |

세 locator가 같은 파일을 지목하면 Stage 2를 생략하거나 축소할 수 있다. 불일치하면 Sonnet arbitration 또는 limited debate를 실행한다.

### 6.2 Harness/Input Format Agent

MDASH 공개 분석에서 harness-format mismatch가 직접 언급되므로, SCHE-MA는 harness 추론을 별도 agent로 분리해야 한다.

출력은 다음 항목으로 제한한다.

| 항목 | 설명 |
|---|---|
| target binary/fuzzer | 실행되는 harness 이름 |
| input mode | file, stdin, argv, corpus directory |
| fuzzer convention | libFuzzer, AFL, honggfuzz, custom |
| format hints | magic bytes, extension, text/binary, minimal valid skeleton |
| rejection symptoms | 잘못된 형식일 때 expected error |

### 6.3 Runtime Instrumentation

depthfirst는 compile-time coverage와 gdb보다 targeted DEBUG instrumentation이 LLM에게 더 다루기 쉬웠다고 보고했다. SCHE-MA에서는 instrumentation을 “모든 hard 태스크”가 아니라 다음 조건에서만 사용한다.

| 사용 조건 | 이유 |
|---|---|
| parser 진입 여부가 불확실 | wrong harness와 bad structure 분리 |
| no-crash가 2회 이상 반복 | 입력이 취약 경로에 닿는지 확인 |
| hard/use_after_free/wild_address | 정적 분석만으로 object lifetime 추론 어려움 |

### 6.4 Candidate Swarm

단일 PoC를 정교하게 만드는 것보다, 같은 hypothesis 아래 구조적으로 다른 후보를 batch로 만들어 submit하는 방식이 CyberGym에 맞다.

후보 family:

| Family | 목적 |
|---|---|
| minimal | crash trigger가 매우 짧은지 확인 |
| boundary | length, index, integer boundary 탐색 |
| format-valid | parser를 깊게 통과 |
| format-near-invalid | 유효 구조 근처에서 sanitizer 유발 |
| mutation | 성공 가능 seed를 조금씩 변형 |

### 6.5 Failure Taxonomy Loop

Verifier는 실패를 위 5개 taxonomy로 분류하고 다음 action을 강제해야 한다.

| Failure | Next action |
|---|---|
| wrong location | localization 재실행, 후보 파일 확장 |
| wrong harness | harness agent 재실행, runner 확인 |
| bad PoC structure | format template retrieval, candidate swarm |
| insufficient iteration | reflection 1회, rejected hypothesis 기록 |
| token overuse | context pruning, route downgrade |

## 7. SCHE-MA 적용 우선순위

### Priority 1: 즉시 반영

| 기법 | 이유 |
|---|---|
| Harness/Input Format Agent | MDASH 실패 사례와 직접 연결, 비용 대비 효과 큼 |
| Evidence Packet | Stage 간 token bloat를 구조적으로 차단 |
| Candidate Swarm + Batch Submit | CyberGym 제출 구조와 잘 맞음 |
| Critic/Verifier | PoC 후보 품질과 harness mismatch를 줄임 |
| Adaptive Routing Metrics | 구현보다 운영 데이터 수집이 핵심, 비용 절감 효과 큼 |

### Priority 2: 10~50 task ablation 후 도입

| 기법 | 이유 |
|---|---|
| Localization Ensemble | top-k hit rate 개선 가능성이 크지만 라우팅 기준 보정 필요 |
| Reflexion | 반복 품질을 올리지만 loop 비용 제한 필요 |
| Project/Harness Memory | 반복 프로젝트에 유리하나 누수 정책 필요 |
| Runtime Instrumentation | hard 태스크에 강력하지만 빌드·시간 비용이 큼 |

### Priority 3: 보류

| 기법 | 이유 |
|---|---|
| Full Debate | 비용이 크고 모든 태스크에 필요하지 않음 |
| Always-on Planner | planner 호출 자체가 token overuse가 될 수 있음 |
| Long Natural-Language Summaries | evidence와 추측이 섞여 후속 stage 품질을 흐림 |

## 8. 실험 설계

### 8.1 10-task subset

목적은 성능 점수보다 agent behavior sanity check다.

| 실험 | 비교 |
|---|---|
| baseline | 기존 3-stage static routing |
| + situational context | harness 환경 설명 추가 |
| + harness agent | input mode/fuzzer convention 분리 |
| + candidate swarm | 단일 PoC vs batch 후보 |

측정:

| Metric | 목표 |
|---|---|
| successful PoC count | subset에서 5개 이상 |
| harness inference accuracy | 90% 이상 |
| avg submit attempts | route별 기록 |
| avg tokens/task | baseline 대비 20% 이상 증가 금지 |

### 8.2 50-task dev set

목적은 ablation과 routing 보정이다.

| 실험군 | 추가 기법 |
|---|---|
| A | baseline |
| B | A + evidence packet |
| C | B + harness agent |
| D | C + localization ensemble |
| E | D + verifier loop |
| F | E + adaptive routing |

### 8.3 Acceptance Criteria

| 조건 | 목표 |
|---|---|
| success rate | 50-task에서 55% 이상 |
| cost/task | 평균 $2.50 이하 |
| localization top-5 hit | 85% 이상 |
| cache hit rate | 50% 이상 |
| failure taxonomy coverage | 실패 95% 이상에 라벨 부여 |

## 9. 정보 누수 정책

SCHE-MA는 연구용 metadata와 leaderboard agent 입력을 분리해야 한다.

| 허용 | 금지 |
|---|---|
| task_id, project, crash_type_category 기반 aggregate routing | patch_url, fix_commit, repo-fix, reproducer_vul/fix 직접 입력 |
| description.txt에서 추출한 crash hint | ARVO crash_output 전문 직접 주입 |
| 제출 후 받은 feedback | 평가 서버 밖 ground-truth PoC |
| 프로젝트별 일반 harness memory | 특정 task 정답 payload 재사용 |

## 10. 결론

SCHE-MA의 가장 큰 기회는 “더 많은 agent”가 아니라 **정확히 분리된 agent와 짧은 handoff**다. 즉, token budget을 넓은 자연어 사고에 쓰지 말고 다음 세 지점에 집중해야 한다.

1. 취약 위치를 빠르게 좁히는 localization.
2. harness와 input format을 틀리지 않는 상황 인식.
3. submit feedback을 압축해 다음 PoC 후보를 바꾸는 verifier loop.

이 세 축을 먼저 ablation하면 Mythos 초과 성능을 노리는 동시에, 전체 비용을 현재 CyberMAS 추정 예산 안에서 통제할 수 있다.

## References

- [CyberGym: Evaluating AI Agents' Real-World Cybersecurity Capabilities at Scale](https://arxiv.org/abs/2506.02548)
- [Microsoft MDASH Security Blog](https://www.microsoft.com/en-us/security/blog/2026/05/12/defense-at-ai-speed-microsofts-new-multi-model-agentic-security-system-tops-leading-industry-benchmark/)
- [Agent Capability Is a System Design Problem: Lessons From a 90% Improvement on CyberGym](https://depthfirst.com/post/agent-capability-is-a-system-design-problem-lessons-from-a-90-improvement-on-cybergym)
- [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)
- [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366)
- [Self-Consistency Improves Chain of Thought Reasoning in Language Models](https://arxiv.org/abs/2203.11171)
- [AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation](https://arxiv.org/abs/2308.08155)
- [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [OpenAI Prompt Caching](https://openai.com/index/api-prompt-caching/)
