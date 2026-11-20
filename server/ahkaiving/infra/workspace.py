"""FsWorkspace — 수집 중 쓰는 staging 공간. 보관소와 같은 파일시스템이라 최종 rename 이 원자적."""
from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..domain.ports import Workspace


class FsWorkspace(Workspace):
    def __init__(self, archive_root: Path):
        self.root = Path(archive_root)
        self.staging_base = self.root / "items" / ".staging"
        self.staging_base.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def staging(self) -> Iterator[Path]:
        d = self.staging_base / uuid.uuid4().hex
        d.mkdir(parents=True, exist_ok=False)
        try:
            yield d
        finally:
            # 성공 시 repo.add 가 이미 밖으로 옮겨 사라졌다. 남아 있으면 버려진 것 → 정리.
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    def cleanup_leftovers(self) -> int:
        n = 0
        if not self.staging_base.exists():
            return 0
        for d in self.staging_base.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        return n
