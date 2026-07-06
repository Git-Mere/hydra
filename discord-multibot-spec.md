# Discord Multi-Bot 프로젝트 스펙

> 코딩 에이전트 투입용 초안. 한 디스코드 서버 안에서 **채널마다 다른 역할의 AI 봇**이 동작하는 구조.
> 예: `#번역` 채널 → 번역봇, `#질문` 채널 → 일상질문봇. 모두 봇 인스턴스 하나 + OpenRouter 키 하나로 처리.

---

## 1. 목표

- 디스코드 봇 **1개**가 여러 채널에서 **채널별로 다른 모델 + 다른 시스템 프롬프트 + 다른 트리거 방식**으로 동작.
- 모델 호출은 전부 **OpenRouter**(OpenAI 호환 API, 키 1개)로 통일.
- **Phase 1**: 혼자 쓰는 MVP (config 파일 기반, 하드코딩 최소화).
- **Phase 2**: 여러 서버에 배포 (길드별 설정 + 슬래시 커맨드 + DB). 지금은 이 확장을 **염두에 둔 구조**로만 잡고, 실제 구현은 Phase 1 완료 후.

---

## 2. 기술 스택

| 항목 | 선택 | 비고 |
|------|------|------|
| 언어 | Python 3.11+ | |
| 디스코드 | `discord.py` | `Intents.message_content` 필수 |
| LLM 게이트웨이 | OpenRouter | base URL `https://openrouter.ai/api/v1`, OpenAI SDK 호환 |
| HTTP/LLM 클라이언트 | `openai` SDK 또는 `httpx` | OpenAI SDK 쓰면 base_url만 교체 |
| 설정 | `channels.yaml` (Phase 1) → DB (Phase 2) | |
| 실행 | 라즈베리파이 or 무료 클라우드(Railway/Fly.io) | 봇 프로세스만 상시 구동 |

---

## 3. 아키텍처 (Phase 1)

```
디스코드 메시지 수신
      │
      ▼
[봇 자기 메시지면 무시]  ← message.author.bot 체크 (무한루프 방지)
      │
      ▼
[채널 ID로 config 조회]  ← channels.yaml
      │  없으면 무시
      ▼
[trigger 검사]
   ├─ auto     : 모든 메시지 처리 (번역용)
   └─ mention  : @봇 멘션된 메시지만 처리 (질문용)
      │
      ▼
[mode별 핸들러]
   ├─ translate : 번역 프롬프트 → OpenRouter 호출
   └─ chat      : 대화 프롬프트 → OpenRouter 호출
      │
      ▼
[결과를 해당 채널에 reply/embed로 출력]
```

---

## 4. 디렉터리 구조

```
discord-multibot/
├── bot.py                # 엔트리포인트: 클라이언트 + on_message 이벤트
├── config.py             # channels.yaml 로드 & 조회 헬퍼
├── channels.yaml         # 채널별 설정 (아래 5번)
├── llm/
│   ├── __init__.py
│   ├── client.py         # OpenRouter 호출 래퍼 (재시도/429 처리 포함)
│   └── prompts.py        # mode별 시스템 프롬프트 정의
├── handlers/
│   ├── __init__.py
│   ├── translate.py      # 번역 모드
│   └── chat.py           # 질문 모드
├── .env                  # 시크릿 (git 제외)
├── .env.example
├── requirements.txt
└── README.md
```

---

## 5. 설정 구조 (`channels.yaml`)

```yaml
# 채널 ID를 키로 사용. 봇이 반응할 채널만 등록.
channels:
  "111111111111111111":       # 예: #번역 채널 ID
    mode: translate
    model: "qwen/qwen-3-8b:free"
    trigger: auto             # 모든 메시지 자동 번역
    enabled: true

  "222222222222222222":       # 예: #질문 채널 ID
    mode: chat
    model: "deepseek/deepseek-r1:free"
    trigger: mention          # @봇 멘션 시에만 응답
    enabled: true
    system_override: null     # 필요시 이 채널만의 프롬프트로 덮어쓰기
```

**필드 정의**
- `mode`: `translate` | `chat` — 핸들러 결정.
- `model`: OpenRouter 모델 ID (`:free` 접미사 = 무료 모델).
- `trigger`: `auto` | `mention` — 처리 조건.
- `enabled`: `false`면 이 채널 봇 비활성.
- `system_override`: 값 있으면 기본 프롬프트 대신 사용 (선택).

> 채널 추가 = yaml 항목 하나 추가. **코드 수정 불필요.**

---

## 6. 시스템 프롬프트 (`llm/prompts.py`)

```python
TRANSLATE_SYSTEM = (
    "You are a translation engine. "
    "If the input is Korean, translate it to natural English. "
    "Otherwise, translate it to natural Korean. "
    "Output ONLY the translation. No explanations, no notes, no quotes."
)

CHAT_SYSTEM = (
    "너는 친근한 일상 도우미야. "
    "사용자 질문에 자연스럽고 간결하게 한국어로 답해."
)
```

---

## 7. OpenRouter 호출 래퍼 (`llm/client.py`) 요구사항

- 입력: `model`, `system_prompt`, `user_message` → 출력: 텍스트 문자열.
- base URL: `https://openrouter.ai/api/v1`, 키: `OPENROUTER_API_KEY`.
- **429 (rate limit) 처리**: 지수 백오프로 최대 2~3회 재시도, 실패 시 사용자에게 "잠시 후 다시" 안내.
- **타임아웃** 설정 (예: 30초).
- 권장 헤더: `HTTP-Referer`, `X-Title` (OpenRouter 앱 식별용, 선택).

OpenAI SDK 예시:
```python
from openai import OpenAI
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)

resp = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ],
)
text = resp.choices[0].message.content
```

---

## 8. 핵심 구현 주의사항 (반드시 반영)

1. **무한 루프 방지**: `if message.author.bot: return` — 봇 자기 출력을 또 처리하면 안 됨.
2. **Message Content Intent**: 디스코드 개발자 포털에서 활성화 + 코드에서 `intents.message_content = True`. 안 켜면 메시지 내용이 빈 문자열로 옴.
3. **빈/불필요 메시지 스킵**: 이모지만/링크만/공백 메시지는 번역 호출 안 함 (무료 한도·비용 절약).
4. **긴 메시지 처리**: 디스코드 출력 2000자 제한 → 초과 시 분할 전송.
5. **에러 응답**: LLM 실패 시 조용히 죽지 말고 해당 채널에 짧은 에러 메시지.
6. **trigger=mention**: 멘션 파싱 후 멘션 텍스트 제거하고 순수 질문만 모델에 전달.

---

## 9. 무료 모델 rate limit (설계 반영)

- OpenRouter 무료 모델: **분당 20회**, **일 50회**(크레딧 $10 미만) / **일 1,000회**($10 1회 충전 시, 영구).
- 번역채널 + 질문채널이 **같은 키 → 한도 합산**됨.
- 대응: 429 재시도 로직 + (선택) 채널별 간단 요청 큐/쿨다운.
- 운영 팁: 트래픽 생기면 $10 충전으로 일 1,000회 확보 권장.

---

## 10. 환경 변수 (`.env.example`)

```
DISCORD_TOKEN=your_discord_bot_token
OPENROUTER_API_KEY=sk-or-xxxxxxxx
```

---

## 11. 빌드 순서 (Phase 1 MVP)

1. `requirements.txt` + `.env.example` + 기본 스캐폴딩.
2. `config.py`: yaml 로드 + `get_channel_config(channel_id)`.
3. `llm/client.py`: OpenRouter 호출 + 429 재시도.
4. `llm/prompts.py`: 프롬프트 2종.
5. `handlers/translate.py`, `handlers/chat.py`.
6. `bot.py`: `on_message`에서 위 흐름(3번 아키텍처) 연결.
7. `channels.yaml`에 내 서버의 실제 채널 2개 등록 후 로컬 테스트.
8. `README.md`: 셋업/실행 가이드.

**Phase 1 완료 기준 (acceptance)**
- [ ] `#번역` 채널에 한국어 치면 영어로, 영어 치면 한국어로 자동 번역 출력.
- [ ] `#질문` 채널에서 `@봇 질문` 하면 답변 출력, 멘션 없는 일반 메시지엔 무반응.
- [ ] 봇 자기 메시지에 재반응하지 않음.
- [ ] yaml에 채널 하나 추가 시 코드 수정 없이 동작.
- [ ] LLM 오류/429 시 크래시 없이 안내 메시지.

---

## 12. Phase 2 (배포 — 지금은 구조만 열어둠)

> Phase 1에서 채널 config를 **딕셔너리/추상 계층으로 분리**해두면 그대로 확장됨. 지금 구현하지 말 것.

- **길드별 설정**: config 키를 `channel_id` → `(guild_id, channel_id)`로 확장, 저장소를 yaml → DB(SQLite 등).
- **슬래시 커맨드**: `/setup channel mode model trigger` 로 서버 관리자가 직접 설정.
- **BYOK (Bring Your Own Key)**: 서버별로 자기 OpenRouter 키 등록.
  - ⚠️ 남의 키 저장 시 **반드시 암호화**, 로그에 키 노출 금지.
  - 대안: **셀프호스팅 배포**(각자 자기 `.env`에 키) — 키 보관 책임 0, 진입장벽↑.
- **rate limit 격리**: 서버별 자기 키 → 한도 자동 분리 (배포 시 장점).

---

## 13. 스코프 밖 (지금 하지 않음)

- 대화 맥락(멀티턴 히스토리) 유지 — Phase 1 질문봇은 단발 응답으로 시작.
- 음성/이미지 번역.
- 웹 대시보드.
