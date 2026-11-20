"""도메인 이벤트 — 설계서 「수집 흐름」·「확장 지점」. 보존(동기) 뒤 처리·학습·공유를 느슨하게 연결.

ItemPreserved 를 받은 ProcessWorker 가 추출·분류·요약을 비동기로 채운다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    item_id: str


@dataclass(frozen=True)
class ItemPreserved(Event):
    """원본 보존 완료(동기 경로 끝). 처리 워커의 시작 신호."""


@dataclass(frozen=True)
class ItemArchived(Event):
    """추출·분류·보강까지 끝. 2단계 학습 모듈이 구독."""


@dataclass(frozen=True)
class ItemUpdated(Event):
    """사용자 수정 또는 재처리."""


@dataclass(frozen=True)
class ItemFailed(Event):
    step: str = ""
    reason: str = ""
