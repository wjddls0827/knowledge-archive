# AH!카이빙 — 에이전트 운영 지침

관심 자료(웹 링크·PDF·이미지·문서)를 받아 **원본을 그대로 보존**하고, **추출 → 분류·요약**으로
가공해 쌓는 개인 지식 아카이브. 핵심은 "자동 생성"이 아니라 **원본을 신성하게 지키며 함께 쌓는 것**.

**구조 B(역할 분리)**: 웹 대시보드는 *읽기 뷰어 + 수집 입구*일 뿐이고, 실제 수집·번역·요약
지능은 **Claude Code(에이전트)가 대행**한다. 서버는 받아서 원자적으로 보관만 한다.

---

## 실행

- **대시보드/서버**: 레포 루트의 `run.bat` 더블클릭. 처음엔 `server\.venv` 만들고 설치(몇 분),
  이후엔 바로 `http://127.0.0.1:8800` 이 열린다. (내부적으로 `cd server` 후
  `python -m uvicorn ahkaiving.api.app:app --port 8800` — uvicorn은 반드시 `server\`에서 실행해야 패키지가 import됨.)
- **CLI 수집 입구**(완성 payload를 보관소에 밀어넣기):
  ```
  cd server
  python -m ahkaiving.cli ingest <payload.json>   # 완성 자료 1건 저장
  python -m ahkaiving.cli ingest -                # stdin 으로 payload JSON
  python -m ahkaiving.cli list                    # 보관 목록 점검
  ```
  payload 필드 정의는 `ahkaiving/cli.py` 상단 독스트링(정본).
- **테스트**: `cd server && python -m unittest discover -s tests -v` (설치 없이 도는 표준 unittest).

## 아키텍처 (헥사고날 / 포트-어댑터)

```
server/ahkaiving/
  domain/        순수 도메인(의존성 0) — model.py(ArchiveItem·ItemStatus·Kind·Format·ContentHash)
                 ports.py(ItemRepository·Workspace) events.py
  application/   archive_service.py — 수집 오케스트레이션(내용중복→유형판별→원본수집→보관확정→추출)
  infra/         fs_repository·workspace(원자적 이동)·registry(유형판별)·extract·summarizer·translator
  api/           app.py — FastAPI 라우트 + "/"에서 web/index.html 서빙
  web/           index.html — 뷰어 대시보드(수집 입구 포함)
```
도메인은 바깥(FS·네트워크·LLM)을 모른다. 모든 쓰기는 `ArchiveItem` 애그리거트를 통해서만 — 불변식 보호.

## 데이터 레이아웃 (진실의 원천)

루트: 환경변수 `AHK_ARCHIVE` (기본 `server/data/archive`).
```
archive/items/YYYY/MM/<id>/
  meta.json          자료 메타(원본 데이터) — to_meta_dict() 직렬화
  original/          업로드·수집 원본 — 읽기전용·불변(절대 수정/요약 금지)
  derived/           파생본(표지·페이지 이미지·변환본 등) — 재생성 가능한 캐시
archive/items/.staging/   수집 중 작업공간(완료 전) — 시작 시 자동 청소
archive/items/.trash/     삭제 대신 이동(복구 가능)
```
`<id>` = `YYYYMMDD-<slug>-<6hex>`. 레포 루트의 옛 `archive/`(node 시절 예시)와는 **다른 저장소**다.

## 상태 모델 (실제 코드 — model.py `ItemStatus`)

```
pending → preserved → extracted → enriched        (실패 시 failed)
수집시작   원본보존끝   본문추출     분류·요약·태그
          (= 안전)
```
- **preserved**: 원본이 원자적으로 보관된 안전 지점. 동기 경로의 끝.
- **extracted**: 추출 가능한 형식(PDF·HTML·텍스트)에서 본문 텍스트가 `extensions.text`(최대 20000자)에 담김.
- **enriched**: 요약(`meta.summary`) + 키워드(`meta.tags`)까지 채워짐. PATCH로 요약을 수동 입력해도 전진.
  - **요약 포맷(고정)**: `meta.summary`는 '핵심→근거→함의' 3단 구조의 **HTML**로 쓴다 —
    `<p><b>한 줄 요약</b><br>…</p>` · `<p><b>핵심 포인트</b></p><ul><li>…</li>(2~4개)</ul>` · `<p><b>왜 중요한가</b><br>…</p>`.
    대시보드가 HTML로 렌더한다(평문도 허용). 근거 없는 단정·과장 금지.
- 삭제는 영구삭제가 아니라 **trash로 이동**(복구 가능).

## 수집 흐름

- **파일 업로드** `POST /api/materials` (multipart): 내용 해시 중복 체크 → `registry.detect`로 유형 판별 →
  `original/` 보존 → `mark_preserved` → 가능한 형식이면 동기 추출.
- **링크** `POST /api/materials/link` `{url}`: URL 중복 체크 → best-effort fetch(certifi로 HTTPS 검증) →
  내용이 PDF/HTML이면 `original/`(pdf 또는 `snapshot.html`)로 보존·추출, 아니면 **링크만 보관**하고
  `extensions.fetch_error`에 사유 기록(로그인·봇차단·미리보기 불가 등).
- **Claude Code 대행 수집** `POST /api/collect` `{url | item_id}`: 서버가 사용자 PC에서 돌기에
  **Windows에서 `claude` CLI 창을 띄워** 수집·번역·요약을 에이전트에게 맡긴다(구조 B의 핵심).
  `claude`가 PATH에 있어야 함. url=새 링크 / item_id=이미 올린 자료 보강.
- **재처리 라우트**(기존 자료): `POST …/{id}/extract`(본문 재추출) · `…/{id}/enrich`(요약+태그, **API 키 필요**) ·
  `…/{id}/translate`(한국어 번역, **API 키 필요**).
- **수동 편집** `PATCH /api/materials/{id}`: title·summary·tags·notes·provenance·interpretation·translation·glossary.
  - **`notes`(메모)는 사용자 전용이다. 에이전트는 절대 쓰지 않는다.** 수집 경위·판단(번역 생략 이유, 날짜
    추정 근거, 댓글 수집 통계 등)은 **`provenance`(자동 수집 메모)** 필드에 적는다. 대시보드도 둘을 분리해
    보여준다(메모 탭 상단에 provenance를 읽기전용으로, 아래에 사용자 메모 입력칸).
  요약이 채워지면 자동으로 `enriched`로 전진하고 `user_confirmed=true`.
- **원문 서빙**: `GET …/{id}/original/{path}` · `…/{id}/derived/{path}` — 원문 그대로 inline.
- **사용자 첨부자료**(원문에 안 잡힌 이미지·영상·파일을 사용자가 수동 추가): `POST …/{id}/attachments`(multipart 파일) ·
  `POST …/{id}/attachments/link` `{url,title?}`(영상·링크) · `DELETE …/{id}/attachments/{att_id}` · `GET …/{id}/attachments/{path}`(서빙).
  파일은 `attachments/`(원본과 분리·쓰기 가능)에 저장, 메타는 `extensions.attachments=[{id,kind:image|video|audio|file|link,name,path?|url?,mime?,added_at}]`.
  **사용자가 올린 첨부(파일·이미지)는 에이전트가 건드리지 않는다**(사용자 전용). 대시보드 상세뷰의 '첨부' 탭에서 관리.
  - **예외 — 수집 중 발견한 출처·참고 링크**: 긁어온 본문 '안에' 원문 출처 링크나 따로 첨부된 영상·파일 링크가 있으면,
    에이전트가 **link/video 첨부로** 넣는다(광고·내비·추천 말고 본문이 실제로 가리키는 것만). 신규 수집은 ingest payload의
    `attachments=[{kind:"link"|"video",name,url}]`로, 기존 자료 보강은 `POST …/attachments/link`로 **append**(사용자 파일 첨부는 덮지 않음).
    링크 첨부는 '첨부' 탭에 **북마크 카드**(제목·도메인·파비콘)로, 영상 URL(YouTube·Vimeo·.mp4 등)은 **미리보기**로 뜬다.
    첨부에 이미지가 있으면 카드 썸네일이 그 이미지로 바뀐다.

## 형식 · 뷰어 · 분류

- `format` → 뷰어: `pdf`→pdf · `image`→image-gallery · `html`→html-sandbox · `link`→link · `text`/`markdown`→raw.
- `kind`는 **열린 값 객체**(Enum 아님): article·news·paper·social·video·repo·forum… URL 도메인으로 추정하되 새 값 자유.
  SNS(social)는 본문과 함께 댓글 맥락도 수집 가치가 있음.
  - **분류 규칙**: kind는 `_kind_for_url`(도메인 매핑)을 존중한다. 임의로 덮어쓰지 말 것.
    특히 **GeekNews(`news.hada.io`)=news** — 댓글·토론이 있어도 뉴스 애그리게이터는 `news`(forum 아님).
    forum은 Reddit·HN처럼 토론 자체가 1차인 곳에만.

## 수집 품질 규칙 (가공물 공통)

- **썸네일/원문 캡처**: 모든 수집물은 **전체 페이지(스크롤 끝까지, full-page) 스크린샷**을 떠서 `thumbnail`(파일경로)로
  넘긴다 → `derived/cover.png`. 한 화면(뷰포트)만 찍으면 원문 캡처가 잘린다 — 문서 전체 높이를 한 장에 담아라
  (불가한 도구면 가능한 한 길게, 여러 장 세로 연결도 가능). 대시보드 카드 썸네일 + 상세의 '원문 캡처' 뷰 공용. (PDF는 1페이지 커버 자동.)
- **타이포 계층 보존**: 원문의 제목·소제목 위계를 `text`·`translation`에 **markdown 헤딩**(`#`·`##`·`###`),
  목록은 `- ` 불릿으로 반영한다. 대시보드(readBody·mdLite)가 헤딩·불릿으로 렌더한다.
- **도식·차트 재현**: 원문에 도식·차트·표가 있으면 텍스트를 비우지 말고 **mermaid 코드블록**(```` ```mermaid ```` )
  이나 **markdown 표**로 재구성해 해당 위치에 포함한다(대시보드가 렌더). 도식 위주 페이지가 공백으로 남지 않게.
- **요약 포맷**: [위 상태 모델의 enriched 참조] — '핵심→근거→함의' 3단 HTML.
- **원문↔번역 정렬**: 문단 쌍 `segments`[{o,k}]를 함께 넣는다(나란히 보기 정렬).

## 불변식 · 원칙

- **원본은 신성하다**: `original/`은 읽기전용·불변. 절대 요약·편집·덮어쓰기 금지.
- **원자적 보관**: staging에서 완성 후 rename. 실패하면 흔적 없음.
- **중복 차단**: 내용 해시(sha256) + 출처 URL. 수정은 재수집(새 ID) 말고 **같은 ID로 PATCH**.
- **낙관적 잠금**: `version`으로 동시 수정 충돌 방지. **경로 탈출 거부**.
- **사실만**: 근거 없는 숫자·단정 금지. 추정은 "추정"으로 명시. 번역·요약은 원문을 왜곡하지 않는다.
- **외부 공개·발행은 사람 승인(HITL) 후**: 자동으로 소셜 게시하지 않는다.
- **저작권 경계**: 이 코드 자체는 오픈소스(MIT)지만, **네가 수집한 원본 전문(PDF·HTML 캡처 등)은
  저작권이 있으므로 공개 저장소에 커밋하지 말 것.** `.gitignore`가 `server/data/`를 기본 제외한다 —
  수집 자료는 네 로컬에만 둔다. 포크를 공개로 두더라도 `server/data/`는 비워진 상태로 유지한다.

## 선택: 서버 자체 요약·번역

`/enrich`·`/translate`를 서버가 직접 처리하려면 `anthropic` 설치 + `ANTHROPIC_API_KEY`(또는 `ant auth login`).
모델: `AHK_SUMMARY_MODEL`·`AHK_TRANSLATE_MODEL` (기본 `claude-opus-5-5`, 저렴하게 `claude-haiku-5-5`).
구조 B 기본값에서는 **불필요** — 번역·요약은 Claude Code가 payload로 넘긴다.

## 로드맵

`ArchiveItem.extensions`에 **learning · sharing** 슬롯이 예약돼 있다(다음 단계 확장점).
설계서 7단계 중 현재 슬라이스는 ①내용중복 ②유형판별 ④원본수집 ⑤추출 ⑥보관확정까지.
이후: ③저장가능성 판정 · 핸들러(pptx·git·OCR) · EventBus/ProcessWorker · SQLite FTS 색인 · inbox 폴더 감시.
