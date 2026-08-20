"""Focused, hermetic tests for the pure VerdictContract parser."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from . import dialogue_contracts
except (ImportError, ValueError):
    import dialogue_contracts


class DialogueContractsTests(unittest.TestCase):
    def test_public_quote_and_objection_extractors_normalize_contract_lines(self) -> None:
        body = [
            ' QUOTE: "copied artifact text" ',
            "QUOTE: unquoted text",
            " 1. Explain rollback behavior.",
            "not an objection",
        ]

        self.assertEqual(
            dialogue_contracts.extract_quotes(body),
            ["copied artifact text", "unquoted text"],
        )
        self.assertEqual(
            dialogue_contracts.extract_objections(body),
            ["1. Explain rollback behavior."],
        )

    def test_public_quote_verification_returns_only_verbatim_matches(self) -> None:
        quotes = ["exact bytes", "different case", ""]

        self.assertEqual(
            dialogue_contracts.verify_quotes(quotes, "prefix exact bytes suffix"),
            ["exact bytes"],
        )

    def test_public_contract_parser_is_the_compatible_entry_point(self) -> None:
        response = 'QUOTE: "artifact text"\nVERDICT: APPROVE'

        self.assertEqual(
            dialogue_contracts.parse_verdict_contract(response, "artifact text"),
            dialogue_contracts._parse_critic_verdict(response, "artifact text"),
        )

    def test_approve_requires_a_quote_verified_against_the_artifact(self) -> None:
        artifact = "Retry failed writes up to three times before returning an error."
        response = 'QUOTE: "failed writes up to three times"\nVERDICT: APPROVE'

        result = dialogue_contracts._parse_critic_verdict(response, artifact)

        self.assertEqual(result.verdict, "approved")
        self.assertEqual(result.verified_quote_count, 1)
        self.assertEqual(result.objection_count, 0)

    def test_nonmatching_quote_does_not_authorize_approval(self) -> None:
        response = 'QUOTE: "fabricated source passage"\nVERDICT: APPROVE'

        result = dialogue_contracts._parse_critic_verdict(response, "Actual artifact.")

        self.assertEqual(result.verdict, "unparseable")
        self.assertEqual(result.verified_quote_count, 0)

    def test_bare_approval_fails_closed(self) -> None:
        result = dialogue_contracts._parse_critic_verdict(
            "VERDICT: APPROVE", "Actual artifact."
        )

        self.assertEqual(result.verdict, "unparseable")
        self.assertEqual(result.verified_quote_count, 0)
        self.assertEqual(result.objection_count, 0)

    def test_tolerant_revise_accepts_separator_but_not_word_prefix(self) -> None:
        self.assertEqual(
            dialogue_contracts._parse_critic_verdict(
                "VERDICT: REVISE - add a rollback plan", "Artifact."
            ).verdict,
            "revise",
        )
        self.assertEqual(
            dialogue_contracts._parse_critic_verdict(
                "VERDICT: REVISED PLAN ATTACHED", "Artifact."
            ).verdict,
            "unparseable",
        )

    def test_numbered_objections_are_counted_but_cannot_replace_a_quote(self) -> None:
        response = (
            "1. Define the rollback path.\n"
            "2. State the default feature-flag value.\n"
            "VERDICT: APPROVE"
        )

        result = dialogue_contracts._parse_critic_verdict(response, "Artifact.")

        self.assertEqual(result.verdict, "unparseable")
        self.assertEqual(result.verified_quote_count, 0)
        self.assertEqual(result.objection_count, 2)

    def test_empty_responses_are_unparseable(self) -> None:
        for response in ("", " \n\n "):
            with self.subTest(response=repr(response)):
                result = dialogue_contracts._parse_critic_verdict(response, "Artifact.")
                self.assertEqual(result.verdict, "unparseable")
                self.assertEqual(result.verified_quote_count, 0)
                self.assertEqual(result.objection_count, 0)


if __name__ == "__main__":
    unittest.main()
