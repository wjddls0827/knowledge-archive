import shutil
import tempfile
import time
import unittest
from pathlib import Path

from ahkaiving.infra.jobs import beat_fresh, prune_jobs


class _StubRepo:
    """find_by_source 만 흉내 — hit=True 면 '같은 URL 자료가 이미 있음'."""
    def __init__(self, hit=False):
        self.hit = hit

    def find_by_source(self, _sr):
        return object() if self.hit else None


class JobsPruneTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ahk_jobs_"))
        (self.dir / "data" / "collect_beats").mkdir(parents=True)
        (self.dir / "data" / "collect_inbox").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _beat(self, jid, ts=None):
        (self.dir / "data" / "collect_beats" / jid).write_text(
            str(time.time() if ts is None else ts), encoding="utf-8")

    def test_expired_job_pruned(self):
        jobs = {"j1": {"started": time.time() - 1000, "url": None}}
        prune_jobs(jobs, self.dir, _StubRepo(), ttl=900)
        self.assertNotIn("j1", jobs)

    def test_completed_url_pruned(self):
        jobs = {"j2": {"started": time.time(), "url": "https://x"}}
        prune_jobs(jobs, self.dir, _StubRepo(hit=True), ttl=900)  # repo: 그 URL 자료 생김
        self.assertNotIn("j2", jobs)

    def test_fresh_beat_survives(self):
        self._beat("j3")  # 지금 시각 → 살아있음
        jobs = {"j3": {"started": time.time() - 20, "url": None}}  # >12s 지났어도
        prune_jobs(jobs, self.dir, _StubRepo(), ttl=900)
        self.assertIn("j3", jobs)

    def test_dead_job_without_beat_pruned(self):
        jobs = {"j4": {"started": time.time() - 20, "url": None}}  # beat 없음 + >12s
        prune_jobs(jobs, self.dir, _StubRepo(), ttl=900)
        self.assertNotIn("j4", jobs)

    def test_beat_fresh_helper(self):
        self._beat("b1")
        self.assertTrue(beat_fresh(self.dir, "b1"))
        self.assertFalse(beat_fresh(self.dir, "missing"))
        self._beat("b2", ts=time.time() - 100)  # 오래된 beat
        self.assertFalse(beat_fresh(self.dir, "b2"))


if __name__ == "__main__":
    unittest.main()
