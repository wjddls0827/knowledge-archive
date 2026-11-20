# 📚 AH!카이빙 (Knowledge Archive)

관심 자료(웹 링크·PDF·이미지·문서)를 받아 **원본을 그대로 보존**하고 **추출 → 분류·요약**으로
쌓는 개인 지식 아카이브. 웹 대시보드는 *읽기 뷰어 + 수집 입구*이고, 수집·번역·요약 지능은
**Claude Code(CLI 에이전트)가 대행**한다(이걸 "구조 B"라고 부른다). 백엔드는 받아서 원자적으로 보관만 한다.

로컬에서 도는 단일 FastAPI 서버 + 단일 HTML 대시보드다. 데이터는 전부 네 컴퓨터의
`server/data/archive/` 아래 파일로만 저장된다 — 외부 서버로 나가지 않는다.

---

## ⚠️ 먼저 — 이게 돌아가려면 Claude Code CLI가 필요하다

이 프로젝트의 핵심인 **"수집" 버튼은 서버가 직접 하는 게 아니라, 네 PC의 `claude` CLI를 띄워서
대행**시킨다. 그래서 수집·번역·요약을 쓰려면:

1. **[Claude Code CLI](https://claude.com/claude-code)** 를 설치하고
2. **로그인(인증)** 돼 있어야 하며 (`claude` 명령이 PATH에 있어야 함)
3. 수집·번역에 드는 토큰 비용은 **네 Claude 계정**으로 청구된다.

Claude Code 없이도 **앱 자체(대시보드·뷰어·파일 업로드·수동 요약/태그/메모·캘린더·갤러리)** 는
그대로 쓸 수 있다. 다만 "AI 자동 수집/번역/요약"만 CLI 연결이 필요하다.

> 대안: 서버가 직접 요약·번역하게 하려면 `ANTHROPIC_API_KEY`를 넣으면 된다(아래 "선택: 서버 자체 요약·번역").

---

## 요구사항

- **Python 3.12+**
- **Windows** (런처 `run.bat` 기준 — macOS/Linux는 아래 수동 실행 참고)
- (수집 기능용) **Claude Code CLI** — 설치 + 로그인

## 설치 & 실행

### Windows
레포 루트의 **`run.bat` 더블클릭.** 처음엔 `server\.venv`를 만들고 의존성을 설치(몇 분),
이후엔 바로 `http://127.0.0.1:8800` 대시보드가 열린다.

### macOS / Linux (수동)
```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn ahkaiving.api.app:app --port 8800
# → http://127.0.0.1:8800
```
> uvicorn 은 반드시 `server/`에서 실행해야 `ahkaiving` 패키지가 import 된다.

## 쓰는 법

- **수동 수집**: 좌상단 **＋ 아카이빙** → 링크 붙여넣기 또는 파일 업로드. 원본 보존 + (가능하면) 본문 추출.
- **AI 대행 수집**: 링크를 넣고 수집하면 **Claude Code 창이 열려** 번역·요약까지 처리해 보관소에 넣는다
  (Claude Code CLI 필요).
- **읽기**: PDF·이미지·HTML 스냅샷·외부 링크를 원문 그대로. 번역·요약·메모·태그·마감일(채용공고) 편집.
- **보기**: 리스트 / 갤러리 / 캘린더(채용공고는 마감일 기준, 나머지는 수집일 기준).

## 상태 모델

```
pending → preserved → extracted → enriched        (실패 시 failed)
수집시작   원본보존끝   본문추출     분류·요약·태그
```
삭제는 영구삭제가 아니라 **trash로 이동**(복구 가능).

## 구조

```
run.bat                 루트 런처 (→ server 로 들어가 uvicorn 실행)
server/
  ahkaiving/            헥사고날 백엔드 (domain·application·infra·api·web)
    web/index.html      단일 파일 대시보드(뷰어 + 수집 입구)
    cli.py              payload 보관 입구 (python -m ahkaiving.cli ingest <payload.json>)
    collect_runner.py   수집 창 1개 = 작업 1개 (claude 를 띄워 대행)
  data/archive/items/   자료 저장소(진실의 원천) — YYYY/MM/<id>/{meta.json, original/, derived/}
  requirements.txt      실행 의존성
  tests/                표준 unittest (설치 없이 실행)
CLAUDE.md               에이전트 운영 지침(정본) — 상태·수집흐름·불변식
ARCHITECTURE.md         제품 비전·로드맵 초안 (일부만 구현됨)
DESIGN.md               정보구조·유저플로우 초안 (일부만 구현됨)
```

## 테스트
```bash
cd server
python -m unittest discover -s tests -v
```

## 선택: 서버 자체 요약·번역

`/enrich`·`/translate`를 서버가 직접 처리하려면 `anthropic` 설치 + `ANTHROPIC_API_KEY`가 필요하다.
모델은 `AHK_SUMMARY_MODEL`·`AHK_TRANSLATE_MODEL` 환경변수로 바꾼다. (`.env.example` 참고.)
구조 B 기본값에서는 불필요하다 — 요약·번역은 Claude Code가 대행한다.

## 데이터 & 프라이버시

- 네가 모은 자료는 전부 **로컬** `server/data/archive/` 에만 저장된다. 이 레포엔 빈 골격만 들어있다.
- **원본 전문(PDF·HTML 캡처 등)을 공개 저장소에 커밋하지 말 것** — 저작권 문제가 될 수 있다.
  `.gitignore` 가 `server/data/` 를 기본 제외한다.
- `.env`(키), 파비콘 캐시, 수집 큐 파일도 커밋되지 않는다.

## 라이선스

[MIT](./LICENSE).
