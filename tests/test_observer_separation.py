import os
import tempfile
import unittest

from witnessd.observer import assert_separated, ObserverSeparationError


class TestSep(unittest.TestCase):
    def test_inside_sandbox_refused(self):
        with tempfile.TemporaryDirectory() as s:
            out = os.path.join(s, "capture.json")  # inside runner sandbox
            with self.assertRaises(ObserverSeparationError):
                assert_separated(runner_sandbox=s, out_path=out)

    def test_outside_ok(self):
        with tempfile.TemporaryDirectory() as s, tempfile.TemporaryDirectory() as o:
            assert_separated(
                runner_sandbox=s, out_path=os.path.join(o, "capture.json")
            )  # no raise

    def test_out_dir_equals_sandbox_refused(self):
        with tempfile.TemporaryDirectory() as s:
            # observer dir == runner sandbox is not separation
            with self.assertRaises(ObserverSeparationError):
                assert_separated(runner_sandbox=s, out_path=os.path.join(s, "x.json"))

    def test_error_code(self):
        with tempfile.TemporaryDirectory() as s:
            with self.assertRaises(ObserverSeparationError) as ctx:
                assert_separated(runner_sandbox=s, out_path=os.path.join(s, "c.json"))
            self.assertEqual(str(ctx.exception), "ERR_OBSERVER_NOT_SEPARATED")


if __name__ == "__main__":
    unittest.main()
