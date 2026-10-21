"""tools/archive_replay.py: a day's archive streamed once, only the alerted aircraft kept, a missing one not looked
for again - on a small tar made here (no network)."""
import gzip
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import archive_replay  # noqa: E402


def fake_archive(hexes):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for h in hexes:
            data = gzip.compress(json.dumps({"icao": h, "timestamp": 0, "trace": []}).encode())
            info = tarfile.TarInfo(f"traces/{h[-2:]}/trace_full_{h}.json")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


class ArchiveReplayTests(unittest.TestCase):
    def test_only_the_wanted_traces_are_kept_and_a_missing_one_is_remembered(self):
        cache = tempfile.mkdtemp()
        with mock.patch.object(archive_replay, "Parts", lambda day: fake_archive(["738a69", "4d22a0", "06a014"])):
            got = archive_replay.fetch_day("2026-10-18", {"4d22a0", "abcdef"}, cache)
        self.assertEqual(got, 1)
        self.assertEqual(sorted(os.listdir(cache)), ["2026-10-18_4d22a0.json", "2026-10-18_not_in_archive.txt"])
        self.assertEqual(archive_replay.load_traces(cache, "4d22a0")[0]["icao"], "4d22a0")
        with mock.patch.object(archive_replay, "Parts", side_effect=AssertionError("streamed again")):
            self.assertEqual(archive_replay.fetch_day("2026-10-18", {"4d22a0", "abcdef"}, cache), 0)

    def test_a_day_not_published_yet_is_skipped(self):
        with mock.patch.object(archive_replay, "Parts", lambda day: io.BytesIO(b"")):
            self.assertEqual(archive_replay.fetch_day("2026-10-21", {"4d22a0"}, tempfile.mkdtemp()), 0)


if __name__ == "__main__":
    unittest.main()
