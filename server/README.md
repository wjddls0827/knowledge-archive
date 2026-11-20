# AH!카이빙 백엔드 (Python)

「자료 아카이빙 에이전트 설계서」(정본)를 구현하는 백엔드. **헥사고날(포트-어댑터)** 구조.
스택: Python 3.12 · FastAPI(예정) · 로컬 FS + SQLite FTS5(예정).

## 구조
```
server/
  ahkaiving/
    domain/           # 순수 도메인 (의존성 0) — 바깥을 모름
      model.py        # Kind·Format·ContentHash·SourceRef·ItemStatus·ItemMeta·ArchiveItem
      ports.py        # 인터페이스: ItemRepository·Workspace (+예외)
      events.py       # ItemPreserved·ItemArchived·ItemUpdated·ItemFailed
    infra/            # 어댑터 (포트 구현)
      workspace.py    # FsWorkspace — staging + 원자적 이동
      fs_repository.py# FsItemRepository — archive/items/YYYY/MM/<id>/
  tests/              # 설치 없이 도는 표준 unittest
```

## 테스트
```
cd server
python -m unittest discover -s tests -v
```
현재 7개 통과: 원자성(staging→rename, 실패 시 흔적 없음) · 불변성(원본 수정 불가) ·
ID/내용 중복 · 낙관적 잠금(version) · 경로 탈출 거부 · trash(복구 가능 이동).

## 데이터 레이아웃 (진실의 원천)
```
archive/items/YYYY/MM/<id>/
  meta.json      자료 메타(원본 데이터)
  original/      업로드 원본 — 읽기전용·불변
  derived/       파생본(슬라이드 이미지·PDF변환·content.md…) — 예정
archive/items/.staging/   수집 중 작업공간(완료 전)
archive/items/.trash/     삭제 대신 이동(복구 가능)
archive/index.sqlite      검색 색인(재생성 가능) — 예정
archive/hashes.sqlite     해시·URL→경로(재생성 가능) — 예정
```

## 구현 로드맵 (설계서 순서)
- [x] ① 도메인 모델 · ItemRepository(FS) · Workspace · 원자성/불변성 테스트
- [ ] ② HandlerRegistry + FallbackHandler (무엇이든 원본 보존 확보)
- [ ] ③ 수집 파이프라인 7단계 + ArchiveService
- [ ] ④ 핸들러: PDF → 이미지(OCR) → 웹링크 → pptx → Git
- [ ] ⑤ EventBus · ProcessWorker · 추출·분류·요약 · SQLite 색인
- [ ] ⑥ 웹 UI: 링크 입력·드래그 업로드 · 목록·검색 · ViewerSpec별 뷰어 · 메타 수정
- [ ] ⑦ inbox 폴더 감시 · 색인 재생성·무결성 점검

## 프론트
기존 `dashboard/`(HTML/JS 카탈로그)는 유지 → 이 백엔드의 API·원문 뷰어에 재배선 예정.
