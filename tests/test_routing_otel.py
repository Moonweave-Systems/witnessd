import unittest

from depone.agent_fabric.evidence_substrate import (
    build_otel_genai_spans,
    validate_external_otel_spans,
)


class TestRoutingOtel(unittest.TestCase):
    def test_static_spans_carry_runner_kind_no_usage_invented(self):
        manifest = {
            "assurance": "A1-local-observed",
            "decision": "A1",
            "observer_capture": {
                "command_receipts": [
                    {
                        "command": ["sh", "-c", "true"],
                        "exit_code": 0,
                        "status": "passed",
                    }
                ]
            },
        }
        receipt = {"runner_kind": "codex-cli", "arm": "direct"}

        spans = build_otel_genai_spans(manifest, runner_receipt=receipt)

        self.assertEqual(validate_external_otel_spans(spans), [])
        root = spans[0]["attributes"]
        self.assertEqual(root["gen_ai.agent.name"], "codex-cli")
        self.assertEqual(root["depone.arm"], "direct")
        for span in spans:
            for key in span["attributes"]:
                self.assertFalse(key.startswith("gen_ai.usage."))


if __name__ == "__main__":
    unittest.main()
