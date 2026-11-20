"""에이전트 ↔ 보관소 계약: ingest payload 스키마(정본, single source of truth).

구조 B에서 Claude Code 에이전트가 수집 결과를 이 JSON(payload)으로 만들어
`python -m ahkaiving.cli ingest <payload.json>` 로 넘긴다. 여기 정의가 그 계약의 정본이다 —
cli 가 이걸로 검증해서, 에이전트가 필드명을 틀리거나(오타) 구조를 틀리게 보내면 조용히 통과하지 않고 드러난다.

각 필드는 (타입, 설명). 타입: "str" | "list" | "bool". 전부 선택이지만 url 또는 original_file 권장.
"""
from __future__ import annotations

# 필드명 → (타입, 설명). 이 표가 payload 계약의 정본.
FIELDS: dict[str, tuple[str, str]] = {
    "url":            ("str",  "출처 URL"),
    "kind":           ("str",  "매체 유형(article/news/paper/social/forum/video/repo/jd/...) — 없으면 URL로 추정"),
    "category":       ("str",  "내용 분류(메인 축): trend·concept·case·opinion"),
    "category_guess": ("bool", "분류가 에이전트 추정값이면 true(UI에 '추정' 표시)"),
    "title":          ("str",  "제목(없으면 URL 도메인)"),
    "authors":        ("list", "저자 목록 [\"이름\", ...]"),
    "published_at":   ("str",  "발행일 '2026-11-10' 등"),
    "deadline":       ("str",  "지원 마감일(JD): 'YYYY-MM-DD' | '상시' | '마감'. 불명이면 생략"),
    "freshness":      ("str",  "high | mid | low"),
    "original_file":  ("str",  "내려받은 원본 파일 경로(PDF·이미지 등) → 원본 불변 보존"),
    "thumbnail":      ("str",  "전체페이지 스크린샷 파일 경로 → derived/cover.png(썸네일)"),
    "original_html":  ("str",  "원문 HTML 문자열 → original/snapshot.html 로 보존"),
    "text":           ("str",  "추출한 본문 텍스트(검색·열람용)"),
    "translation":    ("str",  "한국어 번역(에이전트 작성)"),
    "summary":        ("str",  "요약 HTML('핵심→근거→함의' 3단 구조)"),
    "tags":           ("list", "키워드 [\"...\", ...]"),
    "interpretation": ("str",  "해석(선택)"),
    "notes":          ("str",  "사용자 전용 메모 — 에이전트는 비워 둔다"),
    "provenance":     ("str",  "수집 경위 메모(에이전트 자동 주석)"),
    "glossary":       ("list", "[{\"term\", \"meaning\"}, ...]"),
    "segments":       ("list", "원문↔번역 문단 쌍(정렬 리더용) [{\"o\", \"k\"}, ...]"),
    "related":        ("list", "관련 자료 연결 [{\"id\", \"rel\", \"title\"}, ...]"),
    "attachments":    ("list", "본문에서 발견한 참고/영상 링크 [{\"kind\", \"name\", \"url\"}, ...]"),
}

_PYTYPE = {"str": str, "list": list, "bool": bool}


def validate_payload(data: object) -> tuple[list[str], list[str]]:
    """payload를 계약과 대조. (errors, warnings) 반환.

    errors   : 구조 위반(list여야 하는데 아님 등) → 저장 중단 권장(조용한 데이터 손실 방지).
    warnings : 알 수 없는 필드(오타 가능)·스칼라 타입 권고 → 저장은 진행.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return (["payload는 JSON 객체여야 합니다."], warnings)
    for key, val in data.items():
        if key not in FIELDS:
            warnings.append(f"알 수 없는 필드 '{key}' — 무시됩니다(필드명 오타 확인).")
            continue
        if val is None:
            continue
        tname = FIELDS[key][0]
        if tname == "list" and not isinstance(val, list):
            errors.append(f"'{key}'는 리스트여야 합니다(받음: {type(val).__name__}).")
        elif tname == "bool" and not isinstance(val, bool):
            warnings.append(f"'{key}'는 true/false 권장(받음: {type(val).__name__}).")
        elif tname == "str" and not isinstance(val, str):
            warnings.append(f"'{key}'는 문자열 권장(받음: {type(val).__name__}).")
    return errors, warnings
