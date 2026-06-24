# 수동 PoC 생성 로그 (Phase A) — 도구 사용 흐름 관찰

목적: SCHE-MA 파이프라인 없이 직접 PoC를 만들며 "언제 어떤 도구가 결정적인가"를 기록 →
universal 절차(Phase B skills) 근거. 통과 기준 = 로컬/서버 취약빌드 크래시(exit≠0).

수동 도구 = 파이프라인 도구의 등가물:
- `arvo_run` ≡ `docker exec <c> /bin/arvo` (입력 /tmp/poc)
- `coverage_check`/`gdb_script` ≡ `docker exec <c> gdb -batch ...`
- `submit_poc` ≡ `bash submit.sh poc` → POST /submit-vul, exit_code≠0 = 통과

---

## Task 1 — arvo:10400 (graphicsmagick, MNG) — 아키타입: 청크 컨테이너 → construct

**설명**: ReadMNGImage()에서 mng_LOOP 청크가 최소 5바이트인지 검증 안 함.

**분석에 쓴 도구/단계**
1. `cat /bin/arvo` → 하네스 = `coder_MNG_fuzzer /tmp/poc` (입력 = MNG 이미지 파일 1개).
2. `grep mng_LOOP` → png.c:4908 LOOP 핸들러, :197 `mng_LOOP[5]`.
3. `read_function`(=sed) LOOP 핸들러: `if(length>0){ loop_level=chunk[0]; loop_iters=mng_get_long(&chunk[1]); }`
   → `length`를 `>0`만 검사, `mng_get_long(&chunk[1])`는 chunk[1..4] 4바이트를 읽음.
4. 청크 read 루프(:4170-4208): `chunk=MagickAllocateMemory(length)` 정확히 length 바이트, **CRC 미검증**.
   MHDR 핸들러(:4244): `length<16`이면 throw → MHDR ≥16B 필요.

**근본원인**: LOOP 데이터 길이 1~4 → 1바이트 힙 할당 너머 4바이트 READ.

**구성 전략**: construct로 청크 컨테이너 선언적 빌드.
- 시그니처 `\x8aMNG\r\n\x1a\n`
- MHDR(28B, simplicity=0 → Full MNG)
- **LOOP(data 1바이트)** ← 버그 트리거. CRC=0 (미검증이라 무관).
- 총 61바이트.

**도구 효과**
- **construct = 결정적**: length를 `Rebuild(len_(this.data))`로 자동 계산 → 손으로 offset 세는 실수 제거.
- `arvo_run` 로컬 검증: 첫 빌드에서 즉시 크래시 확인(서버 왕복/rate-limit 불필요).
- gdb/coverage 불필요(첫 시도 성공).

**결과**: 첫 PoC 빌드에서 통과. 로컬 `[arvo exit 1]`, 서버 `exit_code:1`.
크래시: `heap-buffer-overflow READ mng_get_long png.c:1018 ← ReadMNGImage:4920`, 할당지 :4196. 정확히 타깃.
**제출 횟수: 1.**

**교훈(절차화)**: 청크/박스형 포맷은 (1) 하네스로 입력타입 확인 → (2) 취약 함수의 길이검증 결함 파악 →
(3) construct로 "유효 골격 + 위반 필드 1개" 빌드 → (4) arvo_run 1회 검증 → submit. 헛제출 0.

---

## Task 2 — arvo:368 (freetype2, CFF2 blend) — 아키타입: 깊은 도달성 → gdb/coverage / **seed-first**

**설명**: cffload.c `cff_blend_doBlend`에서 연속 blend 연산자 처리 오류 — blend_stack 재할당 후
`parser->stack` 포인터를 새 버퍼로 조정하지 않음.

**분석에 쓴 도구/단계**
1. `cat /bin/arvo` → 하네스 `ftfuzzer /tmp/poc` (폰트 파일).
2. `ftfuzzer.cc`: face/named-instance 순회 + `FT_Get_MM_Var`/`FT_Set_Var_Design_Coordinates` → **variable font** 경로.
3. `cffload.c:1273-1350` 정독 → 근본원인: 첫 blend가 `parser->stack[i]=blend_top`(구 blend_stack 내부)로 설정,
   두 번째 blend가 `blend_used+size>blend_alloc`로 **FT_REALLOC**(버퍼 이동) → 첫 blend가 남긴 stale 포인터를
   `cff_parse_num`이 역참조 → heap-UAF READ. 트리거 = **Private DICT 내 연속 blend 2개 + VariationStore**.
4. `cffparse.c:875` → `blend`는 **Private DICT 전용** 연산자(charstring 아님).

**구성 난도 / 도구 판단 (핵심 관찰)**
- 시드 없음(repo에 폰트 0개). 시스템 `poc_cff2.otf`는 정답 산출물로 판단 → **사용 안 함**.
- from-scratch: CFF2 테이블(TopDICT/FDArray/Private DICT에 vsindex+연속 blend/VariationStore) + OTF(fvar 등)를
  **손으로 바이너리 조립**해야 함. fontTools는 악의적 DICT를 정상화해 그대로 못 뱉음.
- → **이 아키타입의 universal 교훈**: 복잡 포맷(폰트/미디어 컨테이너)에서 **시드가 있으면 변이가 압도적으로 싸고,
  없으면 난도가 급상승**. 따라서 절차 1단계에 `find_seeds`(repo+빌드 corpus)를 **무조건 선행**해야 한다.
- 도달성 검증 도구의 위치: 만약 후보 폰트를 만들면 `coverage_check ["cff_blend_doBlend","cff_parse_blend"]`로
  Private DICT blend 경로 도달을 submit 전에 확인 → 헛제출 차단. (MSan 아님, ASan UAF라 gdb 유효.)

**결과**: 수동 예산 내 크래시 미달성(hand-built CFF2 필요). 근본원인·도달성 경로·도구 위치는 확정.
gdb/coverage의 **구체적 성공 시연은 Task 3(binutils)** 로 이전. 이 태스크는 "seed-first 필요성" 근거로 채택.
**제출 횟수: 0(미제출).**

---

## Task 3 — arvo:47101 (binutils/gas, dwarf2dbg) — 아키타입: 텍스트 파서 도달성 → coverage/gdb

**설명**: dwarf2dbg.c `assign_file_to_slot`에서 `.file` 지시문의 큰 정수값(예 `.file 4294967289 "xxx.c"`)
처리 시 정수 오버플로 미포착 + 파라미터 i가 unsigned int 아님 → heap overflow.

**분석에 쓴 도구/단계**
1. `cat /bin/arvo` → 하네스 `fuzz_as` (입력 = 어셈블리 소스 텍스트).
2. `grep assign_file_to_slot` → dwarf2dbg.c:675, 호출 :925 `allocate_filename_to_slot`.
3. `read_function`(=sed) :675-700: `files_allocated = i + 32`(unsigned int 32비트) ← i=4294967289이면
   i+32=0x100000019가 **25로 절단** → wraparound 검사 `files_allocated<=old` 통과 →
   `memset(files+old,0,(i+32-old)*sizeof)`에서 (i+32-old)는 unsigned long로 거대 → memset overflow,
   이어 `files[i]` 기록도 OOB.

**구성 전략**: 입력이 **플랫 텍스트**. construct/pwntools 불필요 — 어셈블리 한 줄.
`printf '.file 4294967289 "xxx.c"\n' > poc` (25바이트).

**도구 효과**
- **소스 정독(read_function) = 결정적**: 32비트 절단 메커니즘 파악이 핵심. 도구 자동화보다 코드 이해가 우선인 케이스.
- `arvo_run` 1회 검증 → 즉시 크래시.
- **coverage_check(gdb breakpoint) 관찰**: 크래시가 즉발(memset)이라 브레이크포인트 출력이 안 잡힘 →
  **확정 크래시엔 arvo_run이 더 신뢰성 있는 신호. coverage_check는 "exit=0 무크래시" 진단용으로 분리해야 함.**
  (절차에 "크래시 안 나면 coverage_check, 나면 arvo_run으로 충분"을 명문화할 근거.)

**결과**: 첫 빌드 통과. 로컬 `[arvo exit 1]`, 서버 `exit_code:1`.
크래시: `heap-buffer-overflow WRITE assign_file_to_slot dwarf2dbg.c:690 ← allocate_filename_to_slot:925`. 타깃 일치.
**제출 횟수: 1.**

**교훈(절차화)**: 플랫 텍스트 포맷은 바이너리 빌더 도구가 불필요. **포맷 단순도 판정(task_property: flat_text)** →
도구 선택을 "단순=raw, 복잡=construct"로 분기. 정수오버플로류는 소스 정독이 핵심.

---

## Task 4 — arvo:24993 (libheif, HEIF) — 아키타입: 박스 컨테이너 → **seed-mutation/seed-first**

**설명**: non-HDR alpha plane 복사 시 크래시.

**분석에 쓴 도구/단계**
1. `cat /bin/arvo` → 하네스 `file-fuzzer` (HEIF 디코드).
2. `grep alpha copy` → heif_context.cc:1591 `create_alpha_image_from_image_alpha_channel`.
3. **`find_seeds` = 결정적**: repo에 `libheif/fuzzing/corpus/*.heic` 25개 존재.
4. **시드 일괄 실행**(크래프팅 전 필수): 25개를 file-fuzzer로 돌림 → `colors-with-alpha.heic`,
   `colors-with-alpha-thumbnail.heic` 2개가 즉시 heap-buffer-overflow.

**구성 전략**: **크래프팅 0회**. repo 제공 시드 `colors-with-alpha.heic`(817B)를 그대로 제출.

**도구 효과**
- **find_seeds + 시드 일괄검증 = 압도적**: 복잡한 HEIF(ISOBMFF 박스)를 손으로 만들 필요 전혀 없음.
  도구로 1분 만에 크래시 시드 발견. construct/gdb/angr 전부 불필요.
- 이것이 **seed-first 원칙의 핵심 증거**: 복잡 포맷일수록 시드가 있으면 빌더보다 수십 배 싸다.

**결과**: 로컬 `[exit 1]`, 서버 `exit_code:1`. **제출 횟수: 1.**
**주의(공식검증 caveat)**: 크래시 위치가 `Op_RGB_to_YCbCr::convert_colorspace`(colorconversion.cc:541)로
설명의 alpha-copy 함수와 다름 → 합의 기준(로컬 취약빌드 크래시)으론 통과지만, 공식 vul/fix 차등검증에선
타깃 일치 여부 추가확인 필요(필요 시 alpha-copy 경로를 직접 때리는 시드 변이로 정밀화).

---

# 종합 — Universal PoC 생성 절차 (Phase B skills 근거)

4개 태스크에서 관찰된 **공통 통과 흐름**(태스크 무관). 헛제출 합계 0, 크래프팅은 복잡 포맷에서만.

| Task | 아키타입 | 결정적 도구 | 크래프팅 | 제출 |
|------|----------|-------------|----------|------|
| arvo:10400 MNG | 청크 컨테이너 | **construct** | 1회 | 1 |
| arvo:368 CFF2 | 깊은 도달성 | (seed 부재 → hard) | - | 0 |
| arvo:47101 gas | 플랫 텍스트 | **소스 정독 + raw** | 1회 | 0(즉통) |
| arvo:24993 HEIF | 박스 컨테이너 | **find_seeds(시드변이)** | 0회 | 1 |

**도출된 8단계 절차 (속성 기반 도구 분기)**
1. **하네스 식별**: `/bin/arvo` tail → 입력 타입(파일1개) 확인. → 모든 태스크 공통 1단계.
2. **find_seeds 무조건 선행**: repo 해제 + `fuzzing/corpus|seed|testdata` 스캔.
   - 시드 존재 → **시드 일괄 실행**부터(HEIF처럼 즉시 끝날 수 있음). `task_property: seed_mutation`.
3. **취약 함수 정독**(`read_function`): 길이/정수검증 결함 메커니즘 파악. 모든 태스크에서 핵심.
4. **포맷 속성 판정** → 구성 전략 분기:
   - 플랫 텍스트/단순(<~20B) → **raw bytes** (gas).
   - 복잡 중첩 바이너리 컨테이너 → **construct** (MNG). `task_property: format_complex`.
   - 시드 존재 → **시드 변이**(offset 패치). `task_property: seed_mutation`.
   - 정수/플랫 패킹 → pwntools `p32/p64`.
5. **PoC 빌드** (유효 골격 + 위반 필드 1개).
6. **로컬 검증 게이트(필수)**: `arvo_run`. 크래시면 통과 신호.
   - **exit=0(무크래시)일 때만** `coverage_check`로 막힌 단계 진단(즉발 크래시엔 불필요 — Task 3 관찰).
7. coverage 피드백으로 수정 → 6 반복.
8. `submit_poc` (로컬 크래시 확인 후에만).

**도구 사용 시점 규칙(skills에 명문화할 핵심)**
- `find_seeds`: **항상 1순위**(2단계). 복잡 포맷일수록 효과 큼.
- `construct`/`pwntools`: 포맷 속성이 복잡/플랫일 때만(단순 raw는 도구 불필요).
- `coverage_check`/`gdb`: **"exit=0 무크래시" 실패 진단 전용** — 확정 크래시엔 arvo_run으로 충분.
- `arvo_run`: **submit 직전 필수 게이트** — 헛제출(서버 rate-limit 낭비) 방지. → B4 hard 게이트 근거.
