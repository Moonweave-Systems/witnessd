import unittest


class TestRevalidateW10(unittest.TestCase):
    def test_w10_live_fixture_revalidates(self):
        from scripts import revalidate_w10

        self.assertEqual(revalidate_w10.main(), 0)

    def test_w10_runlog_binds_emitted_artifact_hashes(self):
        from scripts import revalidate_w10

        revalidate_w10._assert_runlog_artifact_hashes()

    def test_w10_auxiliary_command_and_transcript_are_bound(self):
        from scripts import revalidate_w10

        revalidate_w10._assert_auxiliary_command_and_transcript()


if __name__ == "__main__":
    unittest.main()
