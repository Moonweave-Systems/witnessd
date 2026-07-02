import unittest

from witnessd.liveness import derive_liveness, HEARTBEAT_TTL_SECONDS


def _hb(lane, mono):
    return {"event": "heartbeat", "payload": {"lane_id": lane}, "ts_monotonic": mono}


def _spawn(lane, mono):
    return {"event": "spawn", "payload": {"lane_id": lane}, "ts_monotonic": mono}


def _exit(lane, mono, code):
    return {
        "event": "exit",
        "payload": {"lane_id": lane, "exit_code": code},
        "ts_monotonic": mono,
    }


class TestLiveness(unittest.TestCase):
    def test_recent_heartbeat_is_active(self):
        recs = [_spawn("L1", 0.0), _hb("L1", 100.0)]
        self.assertEqual(derive_liveness(recs, now_monotonic=105.0)["L1"], "active")

    def test_expired_heartbeat_no_exit_is_zombie(self):
        recs = [_spawn("L1", 0.0), _hb("L1", 10.0)]
        st = derive_liveness(recs, now_monotonic=10.0 + HEARTBEAT_TTL_SECONDS + 5)
        self.assertEqual(st["L1"], "zombie")
        self.assertNotEqual(st["L1"], "active")  # OMX false-positive 안티회귀

    def test_clean_exit_is_dead(self):
        recs = [_spawn("L1", 0.0), _hb("L1", 5.0), _exit("L1", 6.0, 0)]
        self.assertEqual(derive_liveness(recs, now_monotonic=1000.0)["L1"], "dead")

    def test_resumed_without_heartbeat_is_stale(self):
        recs = [_spawn("L1", 0.0)]
        st = derive_liveness(recs, now_monotonic=1.0, resumed_lanes=frozenset({"L1"}))
        self.assertEqual(st["L1"], "stale")


if __name__ == "__main__":
    unittest.main()
