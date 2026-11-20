"""자주 가는 사이트(바로가기) 트리의 순수 로직 — 순회·검증·정규화·검색. app.py 라우팅에서 분리.

파일 IO(sites.json 읽기/쓰기·아이콘 저장)는 경로에 묶여 app 에 남고, 여기엔 네트워크·파일 없는
순수 함수만 둔다(단위 테스트·재사용 쉽게).
"""
from __future__ import annotations


def iter_site_dicts(nodes):
    """폴더 안까지 포함해 모든 site 노드를 순회(아이콘 조회·정리용)."""
    for n in nodes:
        if isinstance(n, dict) and n.get("kind") == "folder":
            yield from (n.get("items") or [])
        elif isinstance(n, dict):
            yield n


def safe_icon(name: object) -> str:
    """아이콘 파일명에서 경로 탈출 문자가 있으면 거부(''). None·임의 입력도 안전하게 받는다."""
    s = str(name or "")
    return "" if ("/" in s or "\\" in s or ".." in s) else s


def san_node(n: dict):
    """클라이언트가 PUT으로 보낸 구조를 알려진 필드만 남겨 안전하게 정규화(폴더 1단계까지)."""
    if not isinstance(n, dict):
        return None
    nid = str(n.get("id") or "")[:32]
    if not nid:
        return None
    if n.get("kind") == "folder":
        items = [san_node(x) for x in (n.get("items") or [])]
        items = [x for x in items if x and x.get("kind") != "folder"]  # 폴더 중첩 금지(1단계)
        return {"id": nid, "kind": "folder", "title": str(n.get("title") or "폴더")[:60], "items": items}
    return {"id": nid, "kind": "site", "url": str(n.get("url") or "")[:2000],
            "title": str(n.get("title") or "")[:120], "icon": safe_icon(n.get("icon"))[:80],
            "added_at": str(n.get("added_at") or "")}


def find_site_node(sid: str, sites: list):
    """id로 site/folder-item 노드 찾기(폴더 안까지)."""
    return next((x for x in iter_site_dicts(sites) if x.get("id") == sid), None)
