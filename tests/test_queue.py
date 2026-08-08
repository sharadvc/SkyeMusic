import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import _env  # noqa: E402  (isolated XDG_CONFIG_HOME/TMPDIR for the suite)
from tune.queue import QueueState, Track, shuffle_no_adjacent  # noqa: E402


class TestTrack(unittest.TestCase):
    def test_as_json_roundtrip(self):
        t = Track(query="coldplay", title="Yellow", url="https://youtu.be/x",
                  duration=60.5, channel="Coldplay")
        d = t.as_json()
        self.assertEqual(Track(**d), t)


class TestQueueState(unittest.TestCase):
    def setUp(self):
        _env.clean()  # isolate each test from the previous one's persisted files
    def _q(self):
        q = QueueState()
        q.tracks = [
            Track("a", "Alpha", "u1", 100.0, "ArtistA"),
            Track("b", "Beta", "u2", 200.0, "ArtistB"),
        ]
        q.index = 1
        q.volume = 55
        q.speed = 1.5
        q.positions = {"u1": 42.5}
        return q

    def test_save_load_roundtrip(self):
        q = self._q()
        q.save()
        q2 = QueueState()
        q2.load()
        self.assertEqual([t.url for t in q2.tracks], ["u1", "u2"])
        self.assertEqual(q2.index, 1)
        self.assertEqual(q2.volume, 55)
        self.assertEqual(q2.speed, 1.5)
        self.assertAlmostEqual(q2.positions["u1"], 42.5, places=1)

    def test_load_missing_file(self):
        q = QueueState()
        q.load()  # should not raise
        self.assertEqual(q.tracks, [])

    def test_current(self):
        q = self._q()
        self.assertEqual(q.current().title, "Beta")


class TestShuffleNoAdjacent(unittest.TestCase):
    def _tracks(self, channels):
        return [Track(str(i), f"T{i}", f"u{i}", 10.0, c)
                for i, c in enumerate(channels)]

    def test_permutation(self):
        src = self._tracks(["A", "A", "B", "B", "C"])
        out = shuffle_no_adjacent(src, key=lambda t: t.channel)
        self.assertEqual(sorted(t.url for t in out), sorted(t.url for t in src))

    def test_separates_two_groups(self):
        src = self._tracks(["A", "A", "A", "B", "B", "B"])
        out = shuffle_no_adjacent(src, key=lambda t: t.channel)
        for a, b in zip(out, out[1:]):
            self.assertNotEqual(a.channel, b.channel)

    def test_respects_prev(self):
        src = self._tracks(["A", "B", "B"])
        out = shuffle_no_adjacent(src, key=lambda t: t.channel, prev="A")
        self.assertNotEqual(out[0].channel, "A")
        for a, b in zip(out, out[1:]):
            self.assertNotEqual(a.channel, b.channel)


if __name__ == "__main__":
    unittest.main()
