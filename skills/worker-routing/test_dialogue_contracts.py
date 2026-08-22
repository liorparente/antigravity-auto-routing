"""Focused, hermetic tests for the pure VerdictContract parser."""
from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import dialogue_contracts
else:
    import dialogue_contracts  # type: ignore[no-redef]


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


class StructuredFindingsAndPerspectiveContractsTests(unittest.TestCase):
    def test_extract_perspective_tag_from_bracketed_form(self) -> None:
        self.assertEqual(
            dialogue_contracts.extract_perspective_tag(
                "Some prose.\n[PERSPECTIVE: reviewer_security]\nMore prose."
            ),
            "reviewer_security",
        )

    def test_extract_perspective_tag_from_plain_line_form(self) -> None:
        self.assertEqual(
            dialogue_contracts.extract_perspective_tag("PERSPECTIVE: reviewer_risk\nRationale."),
            "reviewer_risk",
        )

    def test_extract_perspective_tag_from_lowercase_alias_line(self) -> None:
        self.assertEqual(
            dialogue_contracts.extract_perspective_tag("perspective: architecture\nRationale."),
            "reviewer_architecture",
        )

    def test_extract_perspective_tag_from_json_field(self) -> None:
        text = '```json\n{"perspective": "maintainability", "findings": []}\n```'

        self.assertEqual(dialogue_contracts.extract_perspective_tag(text), "reviewer_maintainability")

    def test_extract_perspective_tag_returns_none_when_absent(self) -> None:
        self.assertIsNone(dialogue_contracts.extract_perspective_tag("No tag here at all."))

    def test_extract_perspective_tag_discards_unknown_bracketed_tag(self) -> None:
        self.assertIsNone(
            dialogue_contracts.extract_perspective_tag("[PERSPECTIVE: reviewer_ux]\nRationale.")
        )

    def test_extract_perspective_tag_discards_unknown_line_tag(self) -> None:
        self.assertIsNone(
            dialogue_contracts.extract_perspective_tag("PERSPECTIVE: performance\nRationale.")
        )

    def test_extract_perspective_tag_ignores_bracket_tag_inside_a_quote_line(self) -> None:
        text = (
            'QUOTE: "the plan says [PERSPECTIVE: reviewer_security]"\n'
            "VERDICT: APPROVE"
        )

        self.assertIsNone(dialogue_contracts.extract_perspective_tag(text))

    def test_extract_perspective_tag_ignores_standalone_line_tag_inside_a_quote_line(self) -> None:
        text = 'QUOTE: "PERSPECTIVE: reviewer_risk"\nVERDICT: APPROVE'

        self.assertIsNone(dialogue_contracts.extract_perspective_tag(text))

    def test_extract_perspective_tag_still_matches_outside_quote_lines(self) -> None:
        text = (
            'QUOTE: "unrelated artifact text"\n'
            "[PERSPECTIVE: reviewer_maintainability]\n"
            "VERDICT: APPROVE"
        )

        self.assertEqual(
            dialogue_contracts.extract_perspective_tag(text), "reviewer_maintainability"
        )

    def test_extract_structured_findings_from_tagged_lines(self) -> None:
        text = (
            "Rationale.\n"
            "FINDING: [id=f1 severity=HIGH category=auth] Missing auth check on the endpoint.\n"
            "FINDING: [id=f2 severity=low] No category given.\n"
            "VERDICT: REVISE"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0].id, "f1")
        self.assertEqual(findings[0].severity, "high")
        self.assertEqual(findings[0].category, "auth")
        self.assertEqual(findings[0].claim, "Missing auth check on the endpoint.")
        self.assertEqual(findings[1].category, "general")

    def test_extract_structured_findings_from_json_block(self) -> None:
        text = (
            "Rationale.\n"
            "```json\n"
            '{"findings": [{"id": "f1", "severity": "critical", "category": "security", '
            '"claim": "SQL injection in query builder.", "evidence_references": ["line 42"], '
            '"recommendation": "Use parameterized queries.", "confidence": 0.9}]}\n'
            "```\n"
            "VERDICT: BLOCK"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.id, "f1")
        self.assertEqual(finding.severity, "critical")
        self.assertEqual(finding.claim, "SQL injection in query builder.")
        self.assertEqual(finding.evidence_references, ("line 42",))
        self.assertEqual(finding.recommendation, "Use parameterized queries.")
        self.assertAlmostEqual(finding.confidence, 0.9)

    def test_extract_structured_findings_from_bare_json_list(self) -> None:
        text = (
            "Rationale.\n"
            "```json\n"
            '[{"id": "f1", "severity": "critical", "category": "security", '
            '"claim": "SQL injection in query builder."}]\n'
            "```\n"
            "VERDICT: BLOCK"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].id, "f1")
        self.assertEqual(findings[0].severity, "critical")
        self.assertEqual(findings[0].claim, "SQL injection in query builder.")

    def test_extract_structured_findings_evidence_references_accepts_a_bare_string(self) -> None:
        text = (
            "```json\n"
            '{"findings": [{"id": "f1", "severity": "high", "claim": "A claim.", '
            '"evidence_references": "line 7"}]}\n'
            "```\n"
            "VERDICT: REVISE"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(findings[0].evidence_references, ("line 7",))

    def test_extract_structured_findings_evidence_references_fails_closed_for_an_integer(
        self,
    ) -> None:
        text = (
            "```json\n"
            '{"findings": [{"id": "f1", "severity": "high", "claim": "A claim.", '
            '"evidence_references": 42}]}\n'
            "```\n"
            "VERDICT: REVISE"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence_references, ())

    def test_extract_structured_findings_evidence_references_fails_closed_for_a_dict(
        self,
    ) -> None:
        text = (
            "```json\n"
            '{"findings": [{"id": "f1", "severity": "high", "claim": "A claim.", '
            '"evidence_references": {"file": "a.py", "line": 7}}]}\n'
            "```\n"
            "VERDICT: REVISE"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence_references, ())

    def test_extract_structured_findings_evidence_references_drops_null_list_entries(
        self,
    ) -> None:
        text = (
            "```json\n"
            '{"findings": [{"id": "f1", "severity": "high", "claim": "A claim.", '
            '"evidence_references": ["line 1", null, "line 2"]}]}\n'
            "```\n"
            "VERDICT: REVISE"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(findings[0].evidence_references, ("line 1", "line 2"))

    def test_extract_structured_findings_normalizes_out_of_vocabulary_severity(self) -> None:
        text = "FINDING: [id=f1 severity=catastrophic] Something bad.\nVERDICT: REVISE"

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(findings[0].severity, "medium")

    def test_extract_structured_findings_applies_default_perspective(self) -> None:
        text = "FINDING: [id=f1 severity=low] No perspective declared.\nVERDICT: REVISE"

        findings = dialogue_contracts.extract_structured_findings(
            text, default_perspective="reviewer_risk"
        )

        self.assertEqual(findings[0].perspective, "reviewer_risk")

    def test_extract_structured_findings_disambiguates_duplicate_ids_instead_of_dropping(
        self,
    ) -> None:
        text = (
            "```json\n"
            '{"findings": [{"id": "f1", "severity": "high", "claim": "From JSON."}]}\n'
            "```\n"
            "FINDING: [id=f1 severity=low] From tagged line.\n"
            "FINDING: [id=f1 severity=low] Third duplicate.\n"
            "VERDICT: REVISE"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(len(findings), 3)
        self.assertEqual([f.id for f in findings], ["f1", "f1-1", "f1-2"])
        self.assertEqual(findings[0].claim, "From JSON.")
        self.assertEqual(findings[1].claim, "From tagged line.")
        self.assertEqual(findings[2].claim, "Third duplicate.")

    def test_extract_structured_findings_disambiguation_skips_an_id_already_taken_literally(
        self,
    ) -> None:
        """A disambiguated id must never collide with a real id a reviewer
        happened to write literally — here "f1-1" is a genuine finding, so
        the second "f1" must skip straight past it to "f1-2", not overwrite
        it with a colliding "f1-1"."""
        text = (
            "FINDING: [id=f1 severity=low] First f1.\n"
            "FINDING: [id=f1-1 severity=low] A genuine, unrelated f1-1.\n"
            "FINDING: [id=f1 severity=low] Second f1, must not collide.\n"
            "VERDICT: REVISE"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(len(findings), 3)
        self.assertEqual([f.id for f in findings], ["f1", "f1-1", "f1-2"])
        self.assertEqual(findings[1].claim, "A genuine, unrelated f1-1.")
        self.assertEqual(findings[2].claim, "Second f1, must not collide.")

    def test_extract_structured_findings_confidence_is_clamped_to_unit_range(self) -> None:
        text = (
            "```json\n"
            '{"findings": ['
            '{"id": "f1", "severity": "high", "claim": "Over one.", "confidence": 5}, '
            '{"id": "f2", "severity": "high", "claim": "Under zero.", "confidence": -3}, '
            '{"id": "f3", "severity": "high", "claim": "Not finite.", "confidence": NaN}, '
            '{"id": "f4", "severity": "high", "claim": "Not numeric.", "confidence": "bogus"}, '
            '{"id": "f5", "severity": "high", "claim": "In range.", "confidence": 0.4}'
            "]}\n"
            "```\n"
            "VERDICT: REVISE"
        )

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertEqual(findings[0].confidence, 1.0)
        self.assertEqual(findings[1].confidence, 0.0)
        self.assertEqual(findings[2].confidence, 1.0)
        self.assertEqual(findings[3].confidence, 1.0)
        self.assertAlmostEqual(findings[4].confidence, 0.4)

    def test_structured_finding_severity_is_a_strict_finding_severity_value(self) -> None:
        text = "FINDING: [id=f1 severity=HIGH] A claim.\nVERDICT: REVISE"

        findings = dialogue_contracts.extract_structured_findings(text)

        self.assertIn(findings[0].severity, dialogue_contracts.FINDING_SEVERITIES)

    def test_extract_structured_findings_returns_empty_tuple_when_none_present(self) -> None:
        self.assertEqual(dialogue_contracts.extract_structured_findings("Just prose.\nVERDICT: APPROVE"), ())

    def test_parse_perspective_review_approves_with_verified_quote(self) -> None:
        artifact = "Retry failed writes up to three times before returning an error."
        response = (
            "[PERSPECTIVE: reviewer_risk]\n"
            'QUOTE: "failed writes up to three times"\n'
            "VERDICT: APPROVE"
        )

        result = dialogue_contracts.parse_perspective_review(response, artifact)

        self.assertEqual(result.verdict, "approved")
        self.assertEqual(result.verified_quote_count, 1)
        self.assertEqual(result.perspective, "reviewer_risk")
        self.assertEqual(result.findings, ())
        self.assertEqual(result.raw_vote, "VERDICT: APPROVE")

    def test_parse_perspective_review_reads_block_verdict_without_a_quote(self) -> None:
        response = (
            "[PERSPECTIVE: reviewer_security]\n"
            "FINDING: [id=f1 severity=critical category=auth] Auth bypass found.\n"
            "VERDICT: BLOCK"
        )

        result = dialogue_contracts.parse_perspective_review(response, "Artifact text.")

        self.assertEqual(result.verdict, "blocked")
        self.assertEqual(result.perspective, "reviewer_security")
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].severity, "critical")

    def test_parse_perspective_review_falls_back_to_default_perspective(self) -> None:
        response = 'QUOTE: "artifact"\nVERDICT: APPROVE'

        result = dialogue_contracts.parse_perspective_review(
            response, "artifact", default_perspective="reviewer_maintainability"
        )

        self.assertEqual(result.perspective, "reviewer_maintainability")

    def test_parse_perspective_review_default_perspective_overrides_self_declared_tag(
        self,
    ) -> None:
        response = (
            "[PERSPECTIVE: reviewer_security]\n"
            'QUOTE: "artifact"\n'
            "VERDICT: APPROVE"
        )

        result = dialogue_contracts.parse_perspective_review(
            response, "artifact", default_perspective="reviewer_risk"
        )

        self.assertEqual(result.perspective, "reviewer_risk")

    def test_parse_perspective_review_unknown_self_declared_tag_does_not_pollute_perspective(
        self,
    ) -> None:
        response = (
            "[PERSPECTIVE: reviewer_ux]\n"
            'QUOTE: "artifact"\n'
            "VERDICT: APPROVE"
        )

        result = dialogue_contracts.parse_perspective_review(response, "artifact")

        self.assertIsNone(result.perspective)

    def test_parse_perspective_review_invalid_default_perspective_becomes_none(self) -> None:
        response = 'QUOTE: "artifact"\nVERDICT: APPROVE'

        result = dialogue_contracts.parse_perspective_review(
            response, "artifact", default_perspective="not-a-real-perspective"
        )

        self.assertIsNone(result.perspective)

    def test_parse_perspective_review_invalid_default_perspective_falls_back_to_self_declared(
        self,
    ) -> None:
        response = (
            "[PERSPECTIVE: reviewer_risk]\n"
            'QUOTE: "artifact"\n'
            "VERDICT: APPROVE"
        )

        result = dialogue_contracts.parse_perspective_review(
            response, "artifact", default_perspective="not-a-real-perspective"
        )

        self.assertEqual(result.perspective, "reviewer_risk")

    def test_parse_perspective_review_default_perspective_accepts_short_alias(self) -> None:
        response = 'QUOTE: "artifact"\nVERDICT: APPROVE'

        result = dialogue_contracts.parse_perspective_review(
            response, "artifact", default_perspective="security"
        )

        self.assertEqual(result.perspective, "reviewer_security")

    def test_parse_perspective_review_unreadable_response_fails_closed(self) -> None:
        result = dialogue_contracts.parse_perspective_review("", "Artifact.")

        self.assertEqual(result.verdict, "unparseable")
        self.assertEqual(result.verified_quote_count, 0)
        self.assertEqual(result.objection_count, 0)
        self.assertEqual(result.findings, ())
        self.assertIsNone(result.raw_vote)

    def test_reviewer_perspective_constants_are_the_four_council_perspectives(self) -> None:
        self.assertEqual(
            dialogue_contracts.REVIEWER_PERSPECTIVES,
            (
                "reviewer_architecture",
                "reviewer_risk",
                "reviewer_maintainability",
                "reviewer_security",
            ),
        )

    def test_finding_severities_constant(self) -> None:
        self.assertEqual(
            dialogue_contracts.FINDING_SEVERITIES, ("critical", "high", "medium", "low")
        )

    def test_structured_finding_is_frozen_with_documented_defaults(self) -> None:
        finding = dialogue_contracts.StructuredFinding(id="f1", severity="high", claim="A claim.")

        self.assertEqual(finding.category, "general")
        self.assertEqual(finding.evidence_references, ())
        self.assertEqual(finding.recommendation, "")
        self.assertEqual(finding.rationale, "")
        self.assertEqual(finding.confidence, 1.0)
        self.assertIsNone(finding.perspective)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            finding.severity = "low"  # type: ignore[misc]

    def test_perspective_review_result_is_frozen_with_documented_defaults(self) -> None:
        result = dialogue_contracts.PerspectiveReviewResult(
            verdict="approved", verified_quote_count=1, objection_count=0
        )

        self.assertEqual(result.findings, ())
        self.assertIsNone(result.perspective)
        self.assertIsNone(result.raw_vote)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.verdict = "revise"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
