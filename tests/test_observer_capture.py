import unittest

from witnessd.observer import build_observer_capture
from depone.agent_fabric.capture_bridge import (
    _check_observer_capture_shape,
    validate_capture_manifest,  # noqa: F401 — field-requirement reference
    REQUIRED_OBSERVER_FIELDS,
    OBSERVER_ID,
    VALID_TEST_STATUSES,
)


class TestObserverCapture(unittest.TestCase):
    def _capture(self, **overrides):
        kwargs = dict(
            command_receipts=[{"command": ["sh", "-c", "true"], "exit_code": 0}],
            touched_files=["f.txt"],
            allowed_touched_files=["f.txt"],
            test_output={"status": "passed"},
        )
        kwargs.update(overrides)
        return build_observer_capture(**kwargs)

    def test_observed_by_and_shape(self):
        oc = self._capture()
        self.assertEqual(oc["observed_by"], "depone-observer")
        self.assertEqual(oc["observed_by"], OBSERVER_ID)
        self.assertTrue(oc["command_receipts"])

    def test_matches_depone_required_shape(self):
        errors: list[str] = []
        _check_observer_capture_shape(self._capture(), errors)
        self.assertEqual(errors, [])

    def test_all_required_fields_present(self):
        oc = self._capture()
        for field in REQUIRED_OBSERVER_FIELDS:
            self.assertIn(field, oc)

    def test_test_output_status_enum(self):
        self.assertIn(self._capture()["test_output"]["status"], VALID_TEST_STATUSES)

    def test_command_receipt_exit_code_is_int(self):
        self.assertIsInstance(self._capture()["command_receipts"][0]["exit_code"], int)

    def test_touched_subset_of_allowed_valid_case(self):
        oc = self._capture()
        self.assertTrue(set(oc["touched_files"]) <= {"f.txt"})

    def test_out_of_range_touched_emitted_as_is(self):
        # Forgery detection is Depone's job: the builder must not silently drop
        # files outside the allow-list; it emits them so the manifest fails
        # closed at Depone validation.
        oc = self._capture(touched_files=["f.txt", "evil.txt"])
        self.assertIn("evil.txt", oc["touched_files"])


if __name__ == "__main__":
    unittest.main()
