import os
import stat
import tempfile
import unittest

from witnessd.isolation import (
    isolation_self_test,
    probe_lane_isolation,
    verify_isolation_boundary,
)


class TestIsolation(unittest.TestCase):
    def test_probe_returns_isolation_facts(self):
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, stat.S_IRWXU)

            facts = probe_lane_isolation(observer_dir=d, runner_uid=999999)

            self.assertIn("runner_uid", facts)
            self.assertIn("observer_dir_writable_by_runner", facts)
            self.assertEqual(facts["runner_uid"], 999999)

    def test_same_uid_no_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, stat.S_IRWXU)

            facts = probe_lane_isolation(observer_dir=d, runner_uid=os.getuid())

            self.assertIs(verify_isolation_boundary(facts)["boundary"], False)

    def test_self_test_runs_local_verifier(self):
        isolation_self_test()


if __name__ == "__main__":
    unittest.main()
