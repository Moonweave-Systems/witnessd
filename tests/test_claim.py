from __future__ import annotations

import unittest

from witnessd.claim import (
    Claim,
    ClaimEffect,
    ClaimEvaluation,
    ClaimFreshness,
    ClaimIntegrity,
    ClaimObservation,
    ClaimSource,
)


class ClaimVocabularyTests(unittest.TestCase):
    def test_axis_vocabularies_are_finite_and_exact(self) -> None:
        self.assertEqual(
            {item.value for item in ClaimSource},
            {"operator", "producer", "observer", "verifier"},
        )
        self.assertEqual(
            {item.value for item in ClaimObservation},
            {"requested", "observed", "missing"},
        )
        self.assertEqual(
            {item.value for item in ClaimIntegrity},
            {"unbound", "sealed", "bundle-bound", "signature-checked"},
        )
        self.assertEqual(
            {item.value for item in ClaimEvaluation},
            {"unevaluated", "pass", "fail", "inconclusive", "refuted"},
        )
        self.assertEqual(
            {item.value for item in ClaimEffect},
            {"advisory", "blocking"},
        )
        self.assertEqual(
            {item.value for item in ClaimFreshness},
            {"pending", "current", "stale"},
        )

        with self.assertRaises(ValueError):
            ClaimSource("agent")

    def test_producer_factory_has_no_evaluation_input(self) -> None:
        claim = Claim.from_producer(
            value={"requested_model": "m1"},
            observation=ClaimObservation.REQUESTED,
            integrity=ClaimIntegrity.UNBOUND,
            effect=ClaimEffect.ADVISORY,
            freshness=ClaimFreshness.PENDING,
        )

        self.assertEqual(claim.source, ClaimSource.PRODUCER)
        self.assertEqual(claim.evaluation, ClaimEvaluation.UNEVALUATED)
        with self.assertRaises(TypeError):
            Claim.from_producer(
                value={"requested_model": "m1"},
                observation=ClaimObservation.OBSERVED,
                integrity=ClaimIntegrity.UNBOUND,
                evaluation=ClaimEvaluation.PASS,
                effect=ClaimEffect.ADVISORY,
                freshness=ClaimFreshness.CURRENT,
            )

    def test_non_verifier_cannot_carry_evaluation(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "only verifier claims may carry an evaluation",
        ):
            Claim(
                value=True,
                source=ClaimSource.PRODUCER,
                observation=ClaimObservation.OBSERVED,
                integrity=ClaimIntegrity.SEALED,
                evaluation=ClaimEvaluation.PASS,
                effect=ClaimEffect.ADVISORY,
                freshness=ClaimFreshness.CURRENT,
            )

    def test_verifier_factory_sets_source_and_evaluation_together(self) -> None:
        claim = Claim.from_verifier(
            value={"decision": "pass"},
            observation=ClaimObservation.OBSERVED,
            integrity=ClaimIntegrity.SIGNATURE_CHECKED,
            evaluation=ClaimEvaluation.PASS,
            effect=ClaimEffect.BLOCKING,
            freshness=ClaimFreshness.CURRENT,
        )

        self.assertEqual(claim.source, ClaimSource.VERIFIER)
        self.assertEqual(claim.evaluation, ClaimEvaluation.PASS)

    def test_verifier_result_must_be_evaluated(self) -> None:
        with self.assertRaisesRegex(ValueError, "verifier result must be evaluated"):
            Claim.from_verifier(
                value=None,
                observation=ClaimObservation.MISSING,
                integrity=ClaimIntegrity.UNBOUND,
                evaluation=ClaimEvaluation.UNEVALUATED,
                effect=ClaimEffect.BLOCKING,
                freshness=ClaimFreshness.PENDING,
            )


if __name__ == "__main__":
    unittest.main()
