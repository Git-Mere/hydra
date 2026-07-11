# Discord Multi-Bot — 진행 상황 및 핸드오프

작성일: 2026-07-07
목적: 다음 세션이 이 문서만 읽고 이어서 작업할 수 있도록 현재 상태, 결정, 미해결 사항, 할 일을 기록.

리포: `/home/aero-mere/hydra/hydra`  · 프로젝트 디렉터리: `discord-multibot/`
원격: github.com/Git-Mere/hydra  · 기본 브랜치: `main`

---

## 1. 이 프로젝트가 하는 일

하나의 Discord 봇이 **채널별로 다르게** 동작한다. 채널마다 mode(translate/chat)와 trigger(auto/mention)를 지정하고, 모든 LLM 호출은 OpenRouter 키 하나로 라우팅한다.

- translate 모드: 메시지를 한국어 <-> 영어로 번역 (trigger auto = 모든 메시지).
- chat 모드: 질문에 답변 (trigger mention = @멘션 시에만). 이제 웹서치 툴 사용.

핵심 원칙: 채널 추가/설정은 이제 파일 편집이 아니라 Discord 안에서 `/setup` 슬래시 커맨드로 한다.

---

## 2. 현재 상태 (무엇이 끝났나)

작업은 전부 "구현 사브에이전트 -> 다른 벤더 교차 리뷰 -> 사람이 머지" 흐름으로 진행. 폴리(오케스트레이터)는 머지하지 않음.

| 작업 | 브랜치 | 상태 | 리뷰 |
|---|---|---|---|
| Phase 1 MVP (봇 기본 골격) | feat/discord-multibot-phase1 | 머지됨 | codex, blocking 0 |
| uv/pyproject 전환 | feat/discord-multibot-uv | 머지됨 | codex, blocking 0 |
| 번역 프롬프트 업그레이드 | feat/discord-multibot-translate-prompt | 머지됨 (main 96371ce) | claude_code, blocking 0 |
| /setup 슬래시 커맨드 + 로컬 JSON 저장소 | feat/discord-multibot-setup-cmd | **머지 대기** (HEAD 9f767ed) | codex, blocking 1건 발견->수정됨 |
| 챗 모드 웹서치 (Tavily MCP) | feat/discord-multibot-chat-websearch | **머지 대기** (HEAD 57b07f4, setup 브랜치에 스택) | codex, blocking 0 |

컴파일 게이트 통과. 테스트: setup 브랜치 16/16, 웹서치 브랜치 23/23 (전부 mock, 실네트워크 없음).

머지 대기 PR 열기 URL:
- setup: https://github.com/Git-Mere/hydra/compare/main...feat/discord-multibot-setup-cmd?expand=1
- 웹서치: https://github.com/Git-Mere/hydra/compare/main...feat/discord-multibot-chat-websearch?expand=1

---

## 3. 지금 당장 할 일 (다음 세션 최우선)

### 3.1 머지 마무리 (현재 막혀 있음)
`git merge`가 로컬 미커밋 변경 때문에 막힌 상태다. main 작업트리의 `discord-multibot/channels.yaml`이 로컬 수정돼 있는데, setup PR은 이 파일을 삭제하므로 git이 거부한다. channels.yaml은 이제 /setup + JSON 저장소로 대체되어 삭제되는 파일이라 로컬 수정은 버려도 된다.

```bash
cd /home/aero-mere/hydra/hydra
git checkout -- discord-multibot/channels.yaml     # 로컬 수정 폐기 (삭제될 파일)
git merge --no-ff feat/discord-multibot-setup-cmd
git merge --no-ff feat/discord-multibot-chat-websearch
git push origin main
# 확인: ls discord-multibot/channels.yaml  -> "No such file" 나오면 정상
```
스택 구조라 반드시 setup 먼저, 웹서치 나중 순서로 머지.

### 3.2 머지 후 실행 준비
```bash
cd discord-multibot
uv sync                       # 새 의존성 mcp 설치됨
# .env 확인 (아래 4절), Discord 재초대 (아래 5절)
uv run python bot.py
```

### 3.3 기본 모델 설정
머지 후엔 채널별 모델이 없어지고 전 채널 공용 `DEFAULT_MODEL` 하나만 쓴다. 현재 기본값(코드): `meta-llama/llama-3.3-70b-instruct:free`. 단 이 모델은 테스트 중 Venice 업스트림 혼잡으로 429가 잦았다. `.env`에서 오버라이드 권장:
```bash
echo 'DEFAULT_MODEL=openai/gpt-oss-20b:free' >> discord-multibot/.env   # 사용자가 200 확인한 모델
```
트레이드오프: llama-3.3-70b는 번역 2해석/챗 툴호출에 더 똑똑하지만 무료 혼잡 때 429 잦음. gpt-oss-20b는 가볍고 응답 잘 뜨지만 복잡 형식엔 약간 약할 수 있고 툴호출 지원 여부 확인 필요. 챗 웹서치가 이상하면 llama-3.3-70b 또는 qwen/qwen3-next-80b-a3b-instruct:free로 바꿔볼 것. .env의 DEFAULT_MODEL 한 줄 수정 후 봇 재시작이면 반영.

---

## 4. .env (로컬 전용, gitignore됨 — 커밋 금지)

경로: `discord-multibot/.env`. 현재 들어있는 키:
- `DISCORD_TOKEN` — Discord 봇 토큰
- `OPENROUTER_API_KEY` — OpenRouter 키 (sk-or-...)
- `TAVILY_API_KEY` — 챗 웹서치용 (없으면 챗은 검색 없이 정상 답변 = graceful degradation)
- (선택) `DEFAULT_MODEL` — 전 채널 공용 모델. 미설정 시 코드 기본값 사용.
- (선택) `OPENROUTER_HTTP_REFERER`, `OPENROUTER_X_TITLE` — OpenRouter 앱 식별 헤더.

보안: TAVILY_API_KEY와 OpenRouter 키가 채팅 대화에 평문으로 노출된 적 있음. **운영 전 두 키 모두 재발급(rotate) 권장.** 노출된 키는 유효한 동안 누구나 사용 가능.

---

## 5. Discord 설정 체크리스트 (봇 사용 전)

1. Developer Portal -> Bot -> **MESSAGE CONTENT INTENT** 활성화 (안 하면 메시지 내용이 빈 값).
2. OAuth2 URL Generator -> 스코프 **`bot` + `applications.commands` 둘 다** 선택 (두 번째가 슬래시 커맨드용). 권한: View Channels, Send Messages. -> 생성 URL로 초대.
   - **이전 버전으로 이미 초대했다면 반드시 재초대** (applications.commands 스코프 추가). 안 하면 /setup이 서버에 안 뜬다.
3. 채널에서 `/setup mode:<translate|chat> trigger:<auto|mention>` 실행 (Manage Channels 권한 필요). `/setup-off`로 해제.
4. 설정은 `channel_config.json`(gitignore, 런타임 생성)에 저장됨.

사용법:
- translate 채널: 그냥 메시지 입력 -> 번역. (trigger auto)
- chat 채널: @멘션 -> 웹서치 후 한국어 답변. (trigger mention)

---

## 6. 그동안 겪은 이슈 / 해결 기록

- **잘못된 모델 ID로 400**: 초기 channels.yaml의 `qwen/qwen-3-8b:free`, `deepseek/deepseek-r1:free`는 현재 OpenRouter에 존재하지 않는 ID. 유효 모델로 교체해야 함. 유효 무료 모델 목록은:
  `curl -s https://openrouter.ai/api/v1/models | grep -o '"id":"[^"]*:free"'`
- **429 Too Many Requests (업스트림 혼잡)**: 무료 모델은 업스트림 제공자(예: Venice) 용량을 공유해 일시 429가 남. 응답 본문에 `retry_after_seconds`(예: 30)가 옴. 회피: 다른 무료 모델로 교체, OpenRouter $10 충전(한도 상향), 또는 BYOK. 계정 한도 확인: `curl -s https://openrouter.ai/api/v1/auth/key -H "Authorization: Bearer $OPENROUTER_API_KEY"`.
- **setup PR 리뷰 blocking**: 기본 모델이 존재하지 않는 `deepseek/deepseek-r1:free`였음 -> `meta-llama/llama-3.3-70b-instruct:free`로 수정 완료.
- **번역 프롬프트 리뷰**: blocking 0. 소형 모델 대비 예시 격리 규칙 + 플레이스홀더 대괄호 제거 반영함.
- **웹서치 구현 중 실버그**: MCP 세션 teardown 에러가 정상 답변을 폐기하던 버그를 구현 중 리뷰가 잡아 수정(answer를 async with 밖에서 캡처) + 회귀테스트 추가.
- **pi 리뷰어 사용 불가**: 이 배포에 pi용 API 키가 없어 부팅 실패. 교차 리뷰는 claude_code <-> codex로 진행함. (다음 세션도 동일할 수 있음.)

---

## 7. 미해결 / 비블로킹 후속 (리뷰에서 나온 것)

### 챗 웹서치 PR (blocking 0, 아래는 선택 개선)
1. **툴 결과 길이 제한(truncate)** — 무료 모델 토큰 한도가 빡빡한데 Tavily 검색 결과가 크면 컨텍스트를 잡아먹음. 실질적으로 유용. (권장)
2. 툴 에러 메시지를 원문 예외(`Tool error: {exc}`) 대신 일반 문구로 (사소한 위생).
3. 툴 스키마 함수명이 OpenRouter 허용 포맷과 맞는지 확인 (Tavily 툴명은 대체로 괜찮음).

### 초기 번역기(단순 번역) 리뷰에서 나왔던 선택 개선 (아직 미반영, 낮은 우선순위)
- 링크 감지가 `http(s)://`만 -> bare 도메인(www., discord.gg) 미감지.
- 이모지-only 감지가 키캡 이모지(1️⃣ 등) 미필터.
- 분할 전송(send) 실패 가드.
- on_message 상단 주석의 필터 순서 실제와 반대.

---

## 8. 다음 단계 후보 (사용자와 논의된 것 + 자연스러운 확장)

### 8.1 모델 자동 전환 (사용자가 명시적으로 "나중에 논의" 한 큰 항목)
목표: 429/모델 오류 시 봇이 알아서 되는 모델로 넘어가기. 권장 설계(폴러 대신):
- OpenRouter 네이티브 폴백: 요청 본문에 `models: [주모델, 대체1, ...]` 배열 -> 주 모델 실패 시 자동 라우팅.
- 429 응답의 `Retry-After` / `retry_after_seconds` 반영해 그만큼 대기 (현재는 2s/4s 백오프라 30s 쿨다운을 못 버팀 — 이게 사용자가 실제 겪은 문제).
- (선택) 무료 모델 목록을 시작 시 + 가끔 /models로 갱신 (무료 조회, 유저 한도 안 씀).
- "상시 헬스 폴링"은 지양 — 무료 한도를 스스로 까먹어 429를 유발.

### 8.2 챗 멀티턴 히스토리 (선택)
현재 챗은 단발성(툴 루프는 한 턴 내부만, 대화 기억 없음). 옵션: 채널 최근 N개 메시지 컨텍스트 / 유저·스레드 단위 로컬 저장 / 히스토리 길이 상한(토큰 절약).

### 8.3 웹서치 후속
7절의 truncate + 일반 에러 문구 반영.

### 8.4 명시적으로 범위에서 뺀 것
- **Apify 쇼핑 스크래퍼**: 사용자가 실수로 넣었다며 제외 요청. 넣지 말 것.
- 멀티 길드/DB는 이미 JSON 저장소가 (guild_id, channel_id) 키로 커버.

---

## 9. 아키텍처 메모 / 확장 방법

- 진입점 `bot.py`: on_message 흐름 = 자기메시지 무시 -> DM(길드없음) 무시 -> (guild,channel) 설정 조회 -> enabled -> trigger(auto/mention) -> 무의미(빈/이모지/링크only) 필터 -> mode 핸들러 -> 2000자 분할 전송. 슬래시 커맨드(/setup, /setup-off)는 on_ready에서 길드별 sync.
- `config.py`: JSON 저장소(JsonStore), guild->channel->{mode,trigger,enabled}, 원자적 쓰기. get_channel_config / set_channel_config / disable_channel / get_default_model.
- `llm/client.py`: OpenRouter 래퍼. `complete()`(단발, translate와 챗 폴백), `complete_with_tools()`(async, 챗 툴 루프, 429 백오프 공유). blocking 호출은 asyncio.to_thread로 오프로드.
- `llm/tavily_search.py`: Tavily MCP 세션(streamable-http), MCP 툴 -> OpenAI 함수스키마 매핑, async executor. 키 없으면 TavilyUnavailable.
- `llm/prompts.py`: TRANSLATE_SYSTEM(2해석/3말투), CHAT_SYSTEM(일상비서, 웹서치 우선).
- `handlers/`: translate.py(sync), chat.py(async, graceful degradation).

**새 mode 추가 방법** (예: summarize):
1. `llm/prompts.py`에 SUMMARIZE_SYSTEM 추가.
2. `handlers/summarize.py` 추가 (기존 핸들러 복사, 시그니처 `handle(cfg, text) -> str`, 챗처럼 툴 쓸 거면 async).
3. `bot.py`의 `_HANDLERS` 딕셔너리에 `"summarize": summarize_handler.handle` 등록.
4. `/setup`의 mode Choice 목록(app_commands.choices)에 summarize 추가.
전부 코드 변경이므로 구현 사브에이전트 + 교차 리뷰로 처리.

---

## 10. 워크플로 규칙 리마인더 (오케스트레이터용)

- 모든 코드/테스트 변경은 사브에이전트(claude_code / codex 등)에 위임. 폴리는 코드 안 씀.
- 리뷰는 항상 구현자와 다른 벤더가 수행 (claude_code <-> codex). pi는 이 머신에서 부팅 불가.
- 리뷰어에겐 diff + 계약만 전달 (워크트리 접근 금지).
- 구현자만 PR을 염. 머지는 사람(리포 소유자)이 함.
- 게이트(compile/test)는 워크트리의 `discord-multibot/` 하위에서 실행할 것 (루트에서 실행하면 파일 못 찾음).

---

## 11. 세션 2 업데이트 (2026-07-07 이어서)

이 세션에서 한 것 (전부 구현 사브에이전트 + 교차 리뷰 흐름):

### 머지 완료 (main에 반영됨)
- **Discord 길드 커맨드 sync 버그 픽스** (commit 4f8ce9c 머지): `bot.py` on_ready의 `tree.copy_global_to_guild(guild)` 는 discord.py 2.7.1에 없는 메서드라 AttributeError -> `/setup`이 서버에 안 떴음. `tree.copy_global_to(guild=guild)` 로 수정. 이제 `/setup`/`/setup-off` 정상 등록됨. (봇 로그: `Synced app commands to guild ...`)
- **모델 자동 폴백 체인** (commit 073aa9b/ffc6a7a, merge f980929): 무료 모델 429 시 자동으로 다른 모델로 넘어감. OpenRouter 네이티브 `models` 배열(요청당 최대 3개, 서버사이드 폴백) + 클라이언트 배치 루프. `extra_body={"models": batch}` 로 전달(최상위 kwarg는 openai SDK가 400/ TypeError). 배치1 전멸 -> 배치2 -> 전부 429면 백오프(Retry-After 존중, 8초 상한) 후 전체 재시도(최대 3패스). 실제 서빙 모델은 `OpenRouter completion served by model <id>` 로그로 확인 가능.
  - 교차 리뷰가 실제로 blocking 버그 1건(최상위 `models=` -> 전 호출 크래시)을 잡아냄. mock이 `**kwargs`로 삼켜서 헛통과했던 것 -> mock을 keyword-only 시그니처로 엄격화해 재발 방지.

### 머지 대기 (PR 열어서 머지해야 함) -- 이번 세션 최종 산출물
- 브랜치 `feat/websearch-speed-antihallucination` (HEAD 249c3c6). 교차 리뷰 PASS(blocking 0), 32 tests passed.
- PR URL: https://github.com/Git-Mere/hydra/compare/main...feat/websearch-speed-antihallucination?expand=1
- 내용 3가지:
  1. **속도/latency 개선**: reasoning 토큰이 최대 범인이었음(단순 번역에도 수백 토큰 사고). 모델별 reasoning 제어 추가 -- nemotron계열 `{enabled:false}`, gpt-oss계열 `{effort:low}`(gpt-oss는 완전 비활성 시 400), qwen/llama/gemma는 생략. 같은 reasoning끼리 <=3 배치로 묶어 `extra_body`에 reasoning 실어 보냄. 체인도 **속도 우선 순서**로 재배치: nemotron-nano -> nemotron-super -> gpt-oss-20b -> qwen -> llama -> gemma. 실측상 번역/단순 응답 2.5~4s -> 0.6~1.5s 기대.
  2. **`chat` 모드 -> `websearch`("Web Searching") 전면 개명**: MODES/`/setup` 선택지(value=`websearch`, name="Web Searching")/`_HANDLERS`/`handlers/chat.py`->`handlers/websearch.py`/`CHAT_SYSTEM`->`WEBSEARCH_SYSTEM`/테스트/README. 기존 저장된 `chat` 설정은 로드 시 `websearch`로 자동 마이그레이션. (OpenAI SDK `client.chat.completions`는 안 건드림.)
  3. **안티-할루시네이션**: WEBSEARCH_SYSTEM이 반드시 web_search 호출 + 결과로만 답하고, 검색이 안 나오거나 실패하면 지어내지 말고 한국어로 "검색 실패/못 찾음" 안내. Tavily 자체 불가(키 없음/MCP 에러) 시에도 기억 기반 답변 대신 "웹 검색 사용 불가" 메시지 반환(graceful degradation의 memory 답변 제거).

### 머지 후 할 것
```bash
cd /home/aero-mere/hydra/hydra
git checkout main && git merge --no-ff origin/feat/websearch-speed-antihallucination && git push origin main
cd discord-multibot && uv run python bot.py   # 재시작하면 반영
```
- 기존에 `/setup mode:chat`으로 만든 채널 있으면 자동으로 websearch로 옮겨짐. 새로 만들 땐 `/setup mode:websearch`.
- 실제 폴백/속도는 `served by model` 로그로 확인.

### 다음 후보 (아직 안 함)
- **챗(웹서치) 툴 루프 축소** (4->2~3회) + Tavily MCP 세션 오버헤드 점검 -- 검색 챗이 느리면 이게 다음 레버 (이번엔 사용자가 reasoning만 먼저 하자고 해서 제외).
- 8.1 나머지, 7절 truncate 등은 그대로 유효.
- 보안: TAVILY_API_KEY / OPENROUTER_API_KEY 로테이트(4절) 아직 권장 상태.

### 세션 2 후속 수정 -- 체인 품질 우선으로 되돌림 (머지 대기)
- **문제**: 위 "속도 우선" 체인(nemotron-nano 1순위)을 머지·실행했더니 번역이 엉망으로 나옴. nemotron-nano가 번역 프롬프트(3레지스터 형식)를 못 따라가고 사고과정/타 언어(아랍어/러시아어/중국어)/깨진 토큰을 답변에 흘리고 오역함. (로그상 전부 `served by model nvidia/nemotron-3-nano-30b-a3b:free`.) 실측으로 확인함: gpt-oss-20b는 깨끗·정확, nemotron-nano는 쓰레기.
- **원인**: reasoning 버그가 아니라 "속도 위해 넣은 작은 모델"이 번역 품질이 형편없던 것.
- **수정** (브랜치 `quality-first-model-chain`, 교차리뷰 PASS, 32 passed): DEFAULT_MODEL_CHAIN을 **품질 우선**으로 재배치 -- openai/gpt-oss-20b(1순위) -> qwen -> llama -> gemma -> nemotron-super -> nemotron-nano(최후 보루). config.py만 변경(+테스트 2개). 배치: [gpt-oss(effort:low)] / [qwen,llama,gemma(none)] / [nemotron x2(enabled:false)].
- PR: https://github.com/Git-Mere/hydra/pull/new/quality-first-model-chain
- 교훈: 무료 소형 모델은 속도는 빠르나 복잡한 형식 프롬프트(번역 다중 레지스터)에서 품질이 무너짐. gpt-oss-20b가 속도/품질 균형점. 번역 프롬프트가 복잡한 만큼 1순위 모델 품질이 중요.
- **상태**: 사용자가 quality-first 체인 머지 후 실행 -> 번역 품질 복구 확인됨. (gpt-oss-20b가 주로 서빙.)

### 속도 관련 결론 (이번 세션에서 조사, 코드 변경 안 함)
- 느림의 원인은 reasoning이 아님. gpt-oss는 reasoning 완전 비활성 불가(400)라 `effort:low`가 최소인데 실측 reasoning 토큰 9개로 이미 최소. 남은 지연(2~4s)은 **무료 tier 모델의 큐 대기 시간**이 사실상 하한. config로 더 못 깎음.
- 유료 모델 단가 조사함(OpenRouter, 100만 토큰당): gpt-4o-mini in$0.15/out$0.60, gpt-4.1-nano/gemini-2.5-flash-lite in$0.10/out$0.40 등. 번역 1건 ~$0.0001~0.0002, 웹서치 1건 ~$0.0005~0.002. $10이면 번역 수만 건, 웹서치 수천~수만 건. **속도 원하면 gpt-4o-mini 1순위 + 무료는 폴백이 정답.**
- **사용자 결정: 지금은 돈 안 쓰고 무료로 감수.** (구독 계정 ChatGPT Plus/Claude Pro는 API 접근 불가라 봇에 못 붙음 -- API는 별도 과금. OpenRouter $10 이미 충전돼 있고 그게 유일한 결제 경로.) 유료 전환은 나중 옵션으로 보류. 전환 시 gpt-4o-mini를 DEFAULT_MODEL_CHAIN 맨 앞에 넣고 MODEL_REASONING엔 None(비-reasoning 모델), 무료 6종은 뒤에 폴백으로 유지.

---

## 12. 다음 세션 작업 -- 번역 프롬프트/셋업 개편 (사용자 지시, 최우선)

**목표**: 번역 출력을 지금의 3레지스터(공식적/적당한/캐주얼) / 2해석 다중 형식에서 -> **가장 자연스러운 문장 딱 1개**만 나오도록 단순화. 톤은 채널 셋업에서 미리 정함. (부수 효과: 출력 토큰 줄어 약간 빨라짐.)

**구체 요구사항 (사용자 원문 취지):**
1. `/setup`에서 mode가 translate일 때 **번역 톤을 선택**하게 하기: **공손(polite) 또는 캐주얼(casual)** 둘 중 하나.
2. 번역 결과는 **가장 자연스러운 1개 문장만** 출력 (레지스터 여러 개 X, 해석 여러 개 X, 라벨 X, 부연 X).

**구현 스케치 (다음 세션에서 사양 확정 후 사브에이전트 위임 + 교차 리뷰):**
- `config.py`:
  - `ChannelConfig`에 번역 톤 필드 추가 (예: `tone: "polite" | "casual"`, translate 모드에서만 의미). 기본값 정해야 함(제안: casual). channel_config.json에 저장.
  - 기존 translate 채널(톤 필드 없음) 로드 시 기본값으로 마이그레이션 (11절 `_MODE_MIGRATIONS` 방식 참고).
- `bot.py` `/setup`:
  - 선택적 파라미터 `tone` 추가 (app_commands.choices: 공손/캐주얼). translate일 때만 의미 있음 -- websearch로 셋업 시 무시 or 안내. 저장 로직에 tone 전달.
  - (검토) discord app_commands에서 조건부 파라미터는 안 되므로, tone은 optional 파라미터로 두고 translate가 아닐 때 들어오면 무시하거나 경고.
- `llm/prompts.py`:
  - `TRANSLATE_SYSTEM`을 **단일 자연 번역 1개** 출력으로 재작성. 방향 자동 감지(한->영/영->한)는 유지. 톤 반영: 공손이면 존댓말/formal, 캐주얼이면 반말/casual. 라벨/대안/노트 전부 제거.
  - 톤을 프롬프트에 주입하는 방식: 시스템 프롬프트를 톤에 따라 포맷하거나(예: `TRANSLATE_SYSTEM.format(tone=...)`) 공손/캐주얼 2개 변형 상수를 두기.
- `handlers/translate.py`: `handle(cfg, text)`에서 `cfg.tone`을 읽어 해당 톤 프롬프트로 `client.complete(get_model_plan(), <tone별 시스템프롬프트>, text)` 호출.
- `tests/test_bot.py`: 톤 필드 마이그레이션/기본값, /setup tone 선택, 톤별 프롬프트 선택 로직 테스트 추가.
- README/HANDOFF 갱신.
- 게이트: `cd discord-multibot && uv run python -m compileall ... && uv run --with pytest python -m pytest -q`.

**다음 세션 시작 시 사용자에게 확인할 것:**
- 톤 기본값(공손 vs 캐주얼) 뭐로 할지.
- 한->영, 영->한 양방향 다 톤 적용할지 (공손/캐주얼 개념이 영어엔 formal/casual로 매핑).
- 기존 번역 프롬프트의 좋은 규칙(이메일 프레이밍 금지, 예시문 echo 금지 등)은 유지할지.

**주의**: 전부 코드/테스트 변경이므로 구현 사브에이전트(claude_code/codex) + 다른 벤더 교차 리뷰로 처리. 폴리는 코드 안 씀.

---

## 13. 세션 3 업데이트 (2026-07-08) — 웹서치 안정화 + Gemini 이전 계획

이번 세션 작업은 전부 main에 머지됨. 테스트는 최종 기준 **53 passed**. 리포는 `/home/aero-mere/hydra/hydra`, 프로젝트 디렉터리는 `discord-multibot/`. 게이트는 항상 `discord-multibot/` 안에서 실행:

```bash
uv run python -m compileall ...
uv run --with pytest python -m pytest -q
```

작업 흐름은 기존과 동일하게 구현 사브에이전트 + 교차 리뷰(claude_code <-> codex)로 진행. pi 리뷰어는 이 머신에서 키가 없어 부팅 불가, opencode/cursor/hermes는 설치돼 있지 않음. 폴리는 머지하지 않고, 머지는 사람이 함. 이 머신에는 `gh`가 없어 PR은 compare URL로 열거나 로컬 `git merge --no-ff`로 처리.

### 이번 세션 머지 완료 (순서대로)

1. `feat/translate-tone-single`
   - `/setup`에 번역 톤(`casual`/`polite`) 파라미터 추가.
   - 번역 출력을 기존 3레지스터/2해석 다중 형식에서 **가장 자연스러운 문장 1개**로 단순화.
   - 기본 톤은 `casual`. 한->영/영->한 양방향 적용: `casual`은 반말/casual English, `polite`는 존댓말/formal English.
   - 기존 좋은 규칙(예시 echo 금지, 이메일 프레이밍 금지 등)은 유지.
   - `config.ChannelConfig`에 `tone` 필드 추가. 기본값 `casual`, 기존 채널 로드시 `casual`로 마이그레이션, 잘못된 값 정규화, disable 시 tone 보존.
   - `llm/prompts.py`에 `get_translate_system(tone)` / `TRANSLATE_SYSTEM_CASUAL` / `TRANSLATE_SYSTEM_POLITE` 추가.

2. `fix/websearch-tool-error`
   - 웹서치 에러 보고를 정정하고 툴 루프 자가수정이 가능하게 함.
   - `handlers/websearch.py`가 anyio teardown이 감싼 `BaseExceptionGroup`에서 내부 `LLMError`를 풀어 재전파(`_find_llm_error`)하도록 수정. 모델 실패를 "Tavily MCP unavailable"로 오분류하지 않음. 진짜 Tavily 장애/성공 답변 보존은 유지.
   - `llm/tavily_search.py` 실행기가 `isError`일 때 실제 에러와 인자를 로깅하고, `"(no result)"` 대신 `"Tool error: <내용>"` 마커를 반환.
   - `llm/client.py`가 툴 인자 JSON 파싱 실패 시 `{}`로 호출하지 않고 에러 피드백을 모델에 전달.

3. `fix/openrouter-null-choices`
   - OpenRouter가 HTTP 200에 에러 본문(`choices=None`)을 줄 때 `resp.choices[0]`에서 `TypeError`로 크래시하던 문제 방어.
   - `_create_completion`이 빈 choices를 감지해 에러 페이로드(`resp.error` 또는 `resp.model_extra["error"]`)를 로깅하고 soft-retry로 다음 배치로 넘어감. 전 배치 소진 시에만 끝단 `LLMError`.
   - `"served by model None"` 로그 제거.

4. `diag/websearch-toolloop-logging`
   - `[websearch-diag]` INFO 로깅 임시 추가: 반복 인덱스, `tool_call` query, Tavily result 스니펫, `tool_result` original_len/fed_len, cap reached, final answer.
   - **임시 계측임. 원인 확정 후 제거하거나 DEBUG로 다운그레이드 예정.**

5. `fix/websearch-speed`
   - `llm/client.py`에 `MAX_TOOL_RESULT_CHARS=6000` 추가. 툴 결과를 모델에 되먹이기 전에 6K자로 truncate하고 `"...[truncated N chars]"` 마커를 붙임.
   - `config.py`에 웹서치 전용 체인 `WEBSEARCH_MODEL_CHAIN` 추가:
     `qwen/qwen3-next-80b-a3b-instruct:free`,
     `meta-llama/llama-3.3-70b-instruct:free`,
     `nvidia/nemotron-3-super-120b-a12b:free`,
     `google/gemma-4-31b-it:free`,
     `openai/gpt-oss-20b:free`.
   - `gpt-oss`는 큰 컨텍스트에서 500이 나므로 웹서치 체인 맨 뒤로 이동.
   - `_build_plan(chain)` 헬퍼로 리팩터. `get_model_plan()`은 번역용(gpt-oss-first 그대로), `get_websearch_model_plan()`은 웹서치용.
   - `handlers/websearch.py`가 `get_websearch_model_plan()` 사용.
   - `WEBSEARCH_MODEL_CHAIN` env 오버라이드 지원. 번역 env(`DEFAULT_MODEL`/`MODEL_CHAIN`)와 격리.

### 런타임 검증 결과 (로그 기반 진단, 코드 변경 아님)

- 속도는 크게 개선됨. 웹서치가 대략 140초 수준에서 쿼리당 약 6~23초로 줄었음. `gpt-oss`를 맨 뒤로 보내 매 반복 11~41초짜리 500을 우회한 영향이 큼.
- 무료 모델 품질 한계가 명확함. 실제로는 `nvidia/nemotron-3-super-120b-a12b:free`만 일함. `qwen`/`llama`는 매번 429, `gpt-oss`는 도달하지 않음.
- 관측된 품질 문제:
  - 한국어 "시장" 중의성 오해: mayor를 market으로 해석해 H마트 등을 나열.
  - 환각: 밸뷰 시장 질의에서 검색 결과에 없는 "Rynn Robinson"을 지어냄. grounding 규칙 위반.
  - 잘못된 툴 인자 생성: `country: "kr"` 400, `region: "kr"` unexpected keyword. 다만 Fix2의 `"Tool error: ..."` 마커 덕에 자가복구.
- 시애틀 내일 날씨 질의는 "내일 예보 없음"이라고 답했으나, 진단 로그 스니펫이 300자로 잘려 실제 결과 전체는 확인하지 못함. 미해결 확인항목.

### 다음 세션 최우선 계획 -- Gemini(Google AI Studio 무료티어)로 이전

사용자 결정: OpenRouter 무료 모델 품질이 너무 낮아 Gemini로 이전. 번역과 웹검색 모두 Gemini를 기본으로 쓰고, OpenRouter는 폴백으로 열어둠.

- 번역 모델: `gemini-2.5-flash-lite`
- 웹검색 모델: `gemini-2.5-flash`
- Gemini 우선. `GEMINI_API_KEY`가 없으면 기존 OpenRouter 체인으로 폴백.
- 키는 `discord-multibot/.env`에 `GEMINI_API_KEY=...`로 넣을 예정. `.env`는 gitignore 대상이며 커밋 금지. `GOOGLE_API_KEY`도 허용.

공식 문서로 검증된 통합 사실:

- OpenAI 호환 엔드포인트는 `base_url = https://generativelanguage.googleapis.com/v1beta/openai/`, 키는 openai SDK의 `api_key`로 전달. 기존 `openai` 패키지를 그대로 사용하면 되며, 새 의존성(`google-genai`)은 필요 없음. 출처: https://ai.google.dev/gemini-api/docs/openai
- Tool/function calling은 이 호환 엔드포인트에서 표준 OpenAI 포맷(`assistant tool_calls` -> `role: "tool"` + `tool_call_id`)으로 동작하므로 기존 Tavily 툴 루프를 재사용 가능. 네이티브 `function_result`/`call_id` 포맷은 쓰지 말 것. 출처: https://ai.google.dev/gemini-api/docs/function-calling
- OpenRouter 전용 파라미터는 반드시 제거. `extra_body`의 `models`(폴백 배열), `reasoning`은 Gemini에 유효하지 않음. 웹서치/번역 Gemini 호출은 별도 OpenAI 클라이언트 인스턴스(다른 `base_url` + `api_key`, `extra_body` 없음)로 처리.
- 무료 한도는 Google이 공식 수치를 문서에서 내렸고, 각자 AI Studio 콘솔에서만 확인 가능: https://aistudio.google.com/rate-limit. 서드파티 추정으로 `2.5-flash`는 약 10 RPM/250 RPD, `2.5-flash-lite`는 RPD가 더 높음. 출처: https://ai.google.dev/gemini-api/docs/rate-limits
- 주의: OpenAI 호환 레이어는 기능 완전성을 보장하지 않음. 멀티툴/parallel tool call/`tool_choice` 값은 실제 Tavily 루프로 1회 실측 필요.

구현 스케치(다음 세션에서 implement-subagent + 교차 리뷰로):

- `config.py`: Gemini 모델 상수 추가(`GEMINI_TRANSLATE_MODEL=gemini-2.5-flash-lite`, `GEMINI_WEBSEARCH_MODEL=gemini-2.5-flash`) + provider 선택(`GEMINI_API_KEY` 있으면 Gemini, 없으면 OpenRouter).
- `llm/client.py`: Gemini 클라이언트 팩토리 추가. base_url 상수 + `GEMINI_API_KEY`, 지연 생성, 키 없으면 `None`. Gemini 완성 경로는 OpenRouter `extra_body` 없이 단일 모델 호출.
- `complete()`(번역)와 `complete_with_tools()`(웹서치)는 "Gemini 우선 -> 실패/키없음 시 OpenRouter 폴백"으로 라우팅.
- 기존 방어로직 보존: null-choices 가드, 툴 인자 검증, 6K truncate, `ExceptionGroup` 언랩(`handlers/websearch.py`), 4회 캡.
- `handlers/translate.py` / `handlers/websearch.py`: 새 라우팅 사용. 각각 해당 Gemini 모델로 호출.
- 테스트:
  - 키 있을 때 Gemini 경로 사용.
  - 키 없을 때 OpenRouter 폴백.
  - Gemini 호출에 `extra_body.models`/`extra_body.reasoning` 미포함.
  - 번역/웹서치가 각자 올바른 모델로 라우팅.
  - 툴 루프 mock.
- 게이트/워크플로: `discord-multibot/` 하위에서 compileall + pytest, 사후 교차리뷰(claude_code <-> codex), PR은 compare URL(또는 로컬 merge, `gh` 없음), 머지는 사람이.

### 남은 후속 (낮은 우선순위, 여전히 유효)

- 안티-할루시네이션 프롬프트 강화: 검색 결과에 문자 그대로 없는 인명/숫자/사실 출력 금지 + 한국어 "시장" 같은 중의어 처리. Gemini로 가면 상당 부분 완화될 수 있으나 유지 권장.
- 시애틀 "내일 날씨" 결과 전체 확인. 진단 스니펫 상한을 임시 상향하거나 Gemini 전환 후 재확인.
- `[websearch-diag]` 로깅은 Gemini 이전으로 원인 해결이 확인되면 제거하거나 DEBUG로 다운그레이드.
- 보안: `TAVILY_API_KEY` / `OPENROUTER_API_KEY` 로테이션 권장(과거 노출). 이제 `GEMINI_API_KEY`도 `.env`로만 보관.

---

## 14. 세션 4 업데이트 (2026-07-11) — Gemini provider 도입 + 웹서치 grounding 전환 결정

### 머지 대기 (실테스트 후 사람이 push) — 이번 세션 산출물
- 브랜치 `feat/gemini-provider` (커밋 `12f0590`, main에서 분기). 구현 Coder(sonnet) + 리뷰 Reviewer(sonnet) PASS(blocking 0). **59 passed** (기존 53 + 신규 6, 전부 mock).
- 내용: **Gemini(Google AI Studio, OpenAI-compat 엔드포인트)를 번역·웹서치 공통 1순위 provider로 도입, OpenRouter는 폴백.**
  - 라우팅은 플랜 레벨에서만: `config.get_model_plan()`/`get_websearch_model_plan()`이 키 있으면 `{"provider":"gemini","models":["gemini-2.5-flash"],"reasoning":None}` 배치를 맨 앞에 prepend. `handlers/`는 무수정(이미 플랜 빌더를 호출하므로 라우팅이 그대로 흘러감).
  - `llm/client.py`: `_get_gemini_client()`(별도 OpenAI 인스턴스, `base_url=.../v1beta/openai/`, `extra_body` 없음 — `models` 폴백배열/`reasoning` 미전송). `_create_completion`이 배치의 `provider`로 클라이언트 선택.
  - `APIError`/`APITimeout`을 즉시 하드실패에서 **soft(로그+다음 배치)**로 완화 → Gemini 배치 실패 시 OpenRouter로 fall-through. (트레이드오프: OpenRouter 영구에러도 이제 전 배치+백오프 재시도 후 실패 = 최대 ~16s. 실패는 드물어 수용.)
  - 번역/웹서치 **둘 다 `gemini-2.5-flash`** (사용자 지시. flash-lite 아님). `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY` 있으면 활성, 없으면 OpenRouter 체인 그대로.
  - 보존: null-choices 가드, 툴인자 검증, 6K truncate, `ExceptionGroup` 언랩, 4-iteration 캡, `[websearch-diag]` 로깅 전부 유지.

### 머지 후/실테스트 (사용자가 진행 중)
```bash
echo 'GEMINI_API_KEY=...' >> discord-multibot/.env
cd discord-multibot && uv run python bot.py     # 번역 + 웹서치 실측
git push origin feat/gemini-provider            # 이상 없으면
```
- 실측 확인 포인트: 서빙 모델 로그(`served by model gemini-2.5-flash`), **웹서치에서 Gemini가 OpenAI 포맷 tool_calls로 Tavily를 실제로 부르는지**(HANDOFF 13절 미해결 항목). 키 지우면 즉시 OpenRouter 폴백.
- mock 한계: 59 테스트 전부 mock이라 실제 Gemini 호출/툴루프는 미검증.

### 다음 세션 최우선 — 웹서치 모드를 네이티브 Gemini **Google Search grounding**으로 전환 (Tavily 완전 제거)

사용자 결정: 웹서치는 grounding 단일 경로로 가고 **Tavily/에이전트 툴루프를 전부 제거**. (무료 OpenRouter 검색은 어차피 못 쓸 품질이었고, grounding이 출처/안티할루시네이션에 더 강함. Gemini 다운 시 웹서치 불가 = graceful 메시지로 수용.)

**결정적 제약 (이번 세션에 공식 문서로 확인함, 다음 세션 재조사 불필요):**
- **Google Search grounding은 OpenAI-compat 엔드포인트(`.../v1beta/openai/`)의 일반 chat completion에서 안 됨.** 공식 문서상 `tools:[{"google_search":{}}]`는 `gemini-3-pro-image-preview`(이미지 엔드포인트)에만 열려 있고, compat 레이어는 function calling만 통과시킴. 포럼에서도 2.5 chat에 넣으면 에러.
  출처: https://ai.google.dev/gemini-api/docs/openai , https://discuss.ai.google.dev/t/does-openai-api-support-google-search-grounding/107542
- 따라서 grounding은 **네이티브 Gemini API**(`google-genai` SDK 또는 네이티브 REST `generateContent`)로 구현해야 함. 요청/응답이 OpenAI 메시지가 아니라 네이티브 `contents` + `groundingMetadata` 파싱. **웹서치 전용 별도 클라이언트/코드 경로**가 생김. (번역 쪽 compat 경로는 그대로 유지.)
- `gemini-2.5-flash`는 grounding 지원함(네이티브 기준). 무료 tier ~**1,500 grounded req/day**(2.5), 유료 **$35/1000 grounded prompt**(모델이 실행하는 검색 쿼리당 과금). Discord 봇 볼륨엔 무료로 충분.
  출처: https://ai.google.dev/gemini-api/docs/google-search , https://ai.google.dev/gemini-api/docs/pricing

**구현 스케치 (implement-subagent + 교차리뷰):**
- 새 의존성 `google-genai` 추가(또는 네이티브 REST 직접) → pyproject.
- `handlers/websearch.py`를 grounding 단일 호출로 재작성: `gemini-2.5-flash` + `google_search` 툴, `groundingMetadata`에서 출처 URL 추출해 한국어 답변에 포함. 안티할루시네이션 프롬프트 유지.
- 제거 대상: `llm/tavily_search.py`, `complete_with_tools`의 Tavily 관련 경로, `TavilyUnavailable`, `ExceptionGroup` 언랩, `MAX_TOOL_RESULT_CHARS` truncate, `WEBSEARCH_MODEL_CHAIN`/`get_websearch_model_plan`, `TAVILY_API_KEY`. `[websearch-diag]` 로깅도 이때 제거.
- Gemini 불가(키 없음/에러) 시 `WEBSEARCH_UNAVAILABLE_MESSAGE` 반환(메모리 답변 금지) 유지. OpenRouter 검색 폴백은 없앰.
- 번역은 이번 `feat/gemini-provider` 그대로.

---

## 15. 세션 4 이어서 (2026-07-11) — 실측 후 모델 교체 + grounding 포기 + 툴루프 수정

`feat/gemini-provider`를 사람이 main에 머지·push 후 실키로 돌려본 결과, 계획이 두 번 바뀜. 전부 main에 머지·push 완료.

### (a) `gemini-2.5-flash` 404 → `gemini-3.5-flash`로 교체 (머지됨, main)
- 실측: `gemini-2.5-flash`(및 `-lite`)는 신규 키에 404 "no longer available to new users". 그래서 Gemini가 매 호출 404 → 전부 OpenRouter 폴백 중이었음(번역·웹서치 둘 다). **폴백 설계 자체는 실전에서 완벽 동작 확인.**
- 네 키로 compat 호출 검증: `gemini-3.5-flash`/`gemini-flash-latest`/`gemini-3-flash-preview` = 200, `gemini-2.5-flash-lite` = 404.
- 수정: `config.GEMINI_MODEL = "gemini-3.5-flash"` (커밋 `aad55f7`). 번역 Gemini 서빙 확인됨(`served by model gemini-3.5-flash`).
- 무료 한도: Google이 모델별 수치를 콘솔로만 노출(https://aistudio.google.com/rate-limit). flash 계열 대략 10 RPM / 250K TPM / 1,500 RPD 추정. 초과해도 OpenRouter 폴백이라 치명적 아님.

### (b) 웹서치 grounding 마이그레이션 → **포기 (무료티어 빌링 게이트)**
- 실측: 네 무료 키로 native grounding(`generateContent` + `tools:[{google_search:{}}]`) 첫 호출부터 **429 RESOURCE_EXHAUSTED "check your plan and billing"**. `gemini-3.5-flash`/`gemini-flash-latest` 둘 다 동일. 반면 **툴 없는 일반 generateContent는 정상**. → grounding만 별도 쿼터가 0 = 사실상 빌링 전용.
- 사용자 결정 유지("무료로 감수") → **grounding 안 감. Tavily 유지.** (14절 grounding 계획은 폐기. 유료 전환 시에만 재검토: Gemini 3 grounding ~$14/1000.)

### (c) 웹서치 Gemini+Tavily 툴루프 수정 — thought_signature (머지 대기: `fix/gemini-tool-thought-signature`, 커밋 `6a3b0ee`)
- 실측: 웹서치에서 Gemini가 OpenAI 포맷 tool_calls **정상 발생**(HANDOFF 미해결 항목 해소 = 됨). 그러나 iteration 2에서 **400 "Function call is missing a thought_signature in functionCall parts"**.
- 원인: Gemini 3 계열은 각 tool_call에 `thought_signature`를 붙여줌(compat 응답의 `tool_call.extra_content.google.thought_signature` = `tc.model_extra["extra_content"]`). 다음 턴에 그 assistant 메시지를 되돌려줄 때 이 서명을 같이 보내야 함. `_assistant_message_dict`가 이걸 버려서 400 → OpenRouter(nemotron) 폴백으로 마무리되고 있었음(= 웹서치가 여전히 저품질 무료모델로 답함).
- 수정: `llm/client.py` `_assistant_message_dict`가 tool_call마다 `extra_content`가 있으면 통과시키도록(제너릭 pass-through; OpenRouter tool_call엔 없음). 실 API 2턴 호출로 검증함(되돌려주면 turn2 OK, 안 하면 400 재현). 리뷰 PASS, 61 passed.
- 머지 후 실측 확인 포인트: 웹서치 질의 시 툴루프 전 구간이 `served by model gemini-3.5-flash`로 돌고 400/OpenRouter 폴백이 안 나면 성공.

### (d) `gemini-3.5-flash` 무료 20 RPD + 종합 실패 → `gemini-flash-lite-latest`로 교체
- 실측: `gemini-3.5-flash` 무료 한도가 **하루 20건**(`GenerateRequestsPerDayPerProjectPerModel-FreeTier: 20`). 봇 운영엔 못 씀. `gemini-2.0-flash`도 429(무료 quota 낮음/소진). 살아있는 건 `gemini-flash-lite-latest`.
- 실측: `gemini-3.5-flash`는 웹서치 툴루프에서 **종합을 안 함** — 좋은 검색결과를 먹여도 거의 같은 쿼리로 재검색만 반복(4회 캡) → 강제 최종콜이 빈 응답 → 유저 실패메시지. (thought_signature 수정 자체는 정상 동작, 문제는 모델 행동.)
- 실측: `gemini-flash-lite-latest`는 **정상 종합** — iter1 검색(thought_signature 있음=Gemini 3 계열) → iter2에서 검색결과로 한국어 답변+출처 완성. 검색 1회, 빠름. → 이 모델이 무료 웹서치의 정답.
- 수정: `config.GEMINI_MODEL = "gemini-flash-lite-latest"` (번역·웹서치 공통). flash-lite도 thought_signature 계열이라 (c)의 수정이 **필수 전제**. 그래서 (c) thought_signature 수정 + (d) 모델 교체를 `fix/gemini-tool-thought-signature` 한 브랜치로 묶어 함께 머지(3.5-flash+수정이 main에 들어가 웹서치가 빈 답 내는 중간 상태 회피).
- flash-lite 무료 RPD 정확값은 콘솔(https://aistudio.google.com/rate-limit)에서 확인 권장. 3.5-flash(20)보다는 확실히 큼(종일 테스트에도 살아있었음).

### 현재 상태 요약 (갱신)
- 번역: Gemini `gemini-flash-lite-latest` (compat), OpenRouter 폴백.
- 웹서치: Gemini `gemini-flash-lite-latest` + Tavily 툴루프 (compat, thought_signature 수정 포함), OpenRouter 폴백.
- grounding: 무료티어 빌링 게이트로 보류. 유료 전환 시 재검토.
- 참고: 무료 최신 모델(3.5-flash 20 RPD 등) 한계로, 볼륨이 커지면 결국 유료 전환(OpenRouter $10 이미 충전됨)이 정답. 번역은 OpenRouter gpt-oss로도 무료로 양호.

### 남은 후속
- `fix/gemini-tool-thought-signature` 실측 확인 후 머지·push.
- `[websearch-diag]` 로깅: 이제 원인(thought_signature) 확정됐으니 제거 또는 DEBUG 다운그레이드 가능. (별도 정리 태스크)
- 혼합 provider 엣지: Gemini가 루프 도중 429로 OpenRouter로 넘어가면, 직전 Gemini turn의 `extra_content`가 OpenRouter로 감(보통 무시됨). 드문 경로라 방치. 문제되면 non-Gemini 전송 시 strip.
- 보안: TAVILY/OPENROUTER/GEMINI 키 로테이션 권장(과거 노출).
