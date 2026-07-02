import io
import unittest
from contextlib import redirect_stdout

from witnessd.__main__ import main
from witnessd.adapters import base, codex
from witnessd import budget, preflight, router, state


class TestSelftestW4(unittest.TestCase):
    def test_w4_module_self_tests_run(self):
        for module in (base, codex, preflight, router, budget, state):
            module._self_test()

    def test_self_test_all_includes_w4_modules(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["self-test", "--all"])

        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("adapter_base", text)
        self.assertIn("codex_adapter", text)
        self.assertIn("router", text)
        self.assertIn("budget", text)
        self.assertIn("state", text)
        self.assertIn("preflight", text)


if __name__ == "__main__":
    unittest.main()
