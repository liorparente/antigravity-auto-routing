"""Unit tests for probe_models.py (ticket 45): the audited model catalog,
the exact CLI wire contracts, and the live availability probe.

Every probe seam (HTTP opener, `shutil.which`, `subprocess.run`) is injected,
so these tests exercise the real parsing and fallback code paths without a
network, a subprocess, or an installed CLI.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, ClassVar

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import probe_models, routing_config
else:
    import probe_models  # type: ignore[no-redef]
    import routing_config  # type: ignore[no-redef]


class CliContractTests(unittest.TestCase):
    """Ticket 45 AC: "Establish exact wire CLI model flags and reasoning
    effort parameters". The contracts must reproduce, token for token, the
    argv each installed CLI actually accepts."""

    def test_claude_contract_uses_model_and_effort_flags(self) -> None:
        contract = probe_models.PROVIDER_CLI_CONTRACTS["claude_code_cli"]
        self.assertEqual(contract.binary, "claude")
        self.assertEqual(
            contract.format_argv("claude-opus-5", "high"),
            ("--model", "claude-opus-5", "--effort", "high"),
        )

    def test_claude_contract_rejects_ultra_which_the_cli_does_not_accept(self) -> None:
        """`claude --effort` accepts low|medium|high|xhigh|max — never `ultra`,
        even though `ultra` is the project's own routing vocabulary."""
        contract = probe_models.PROVIDER_CLI_CONTRACTS["claude_code_cli"]
        self.assertNotIn("ultra", contract.accepted_efforts)
        with self.assertRaises(probe_models.UnsupportedEffortError):
            contract.format_argv("claude-opus-5", "ultra")

    def test_codex_contract_passes_effort_as_a_config_override(self) -> None:
        contract = probe_models.PROVIDER_CLI_CONTRACTS["codex_cli"]
        self.assertEqual(contract.binary, "codex")
        self.assertEqual(
            contract.format_argv("gpt-5.6-sol", "ultra"),
            ("--model", "gpt-5.6-sol", "-c", 'model_reasoning_effort="ultra"'),
        )

    def test_agy_contract_accepts_only_low_medium_high(self) -> None:
        contract = probe_models.PROVIDER_CLI_CONTRACTS["antigravity_cli"]
        self.assertEqual(contract.accepted_efforts, ("low", "medium", "high"))
        self.assertEqual(
            contract.format_argv("gemini-3.6-flash-high", "high"),
            ("--model", "gemini-3.6-flash-high", "--effort", "high"),
        )
        with self.assertRaises(probe_models.UnsupportedEffortError):
            contract.format_argv("gemini-3.6-flash-high", "xhigh")

    def test_lm_studio_has_no_cli_and_no_effort_parameter(self) -> None:
        contract = probe_models.PROVIDER_CLI_CONTRACTS["lm_studio_local"]
        self.assertIsNone(contract.binary)
        self.assertEqual(contract.accepted_efforts, ())
        with self.assertRaises(probe_models.ModelCatalogError):
            contract.format_argv("qwen3.8-27b-mlx", "medium")

    def test_format_argv_without_effort_omits_the_effort_flag(self) -> None:
        contract = probe_models.PROVIDER_CLI_CONTRACTS["codex_cli"]
        self.assertEqual(contract.format_argv("gpt-5.6-terra", None), ("--model", "gpt-5.6-terra"))

    def test_every_contract_covers_a_known_provider(self) -> None:
        self.assertEqual(set(probe_models.PROVIDER_CLI_CONTRACTS), set(probe_models.PROVIDER_IDS))

    def test_format_argv_checks_the_models_own_ladder_not_the_provider_union(self) -> None:
        """`gpt-5.6-sol` and `gpt-5.6-terra` reach `ultra`; `gpt-5.6-luna`'s
        audited ladder stops at `max`. All three share `codex_cli`'s
        provider-wide `accepted_efforts`, which does include `ultra` — so
        only checking the ladder pinned to this specific model catches the
        mismatch."""
        contract = probe_models.PROVIDER_CLI_CONTRACTS["codex_cli"]
        self.assertIn("ultra", contract.accepted_efforts)
        luna = probe_models.AUDITED_MODEL_CATALOG["gpt-5.6-luna"]
        self.assertNotIn("ultra", luna.supported_efforts)

        with self.assertRaises(probe_models.UnsupportedEffortError) as caught:
            contract.format_argv("gpt-5.6-luna", "ultra")
        message = str(caught.exception)
        # Pin the *full* ladder name, not just a substring: the override
        # spelling `_CROSS_PROVIDER_EFFORT_LADDERS[('codex_cli',
        # 'gpt-5.6-luna')]` also contains "gpt-5.6-luna", so a bare
        # `assertIn("gpt-5.6-luna", ...)` would not catch the audited branch
        # falsely naming the override table.
        self.assertIn("for model 'gpt-5.6-luna'", message)
        self.assertNotIn("_CROSS_PROVIDER_EFFORT_LADDERS", message)
        self.assertEqual(contract.format_argv("gpt-5.6-sol", "ultra")[:2], ("--model", "gpt-5.6-sol"))

    def test_format_argv_rejects_any_effort_for_a_model_with_no_ladder(self) -> None:
        """`claude-3-7-sonnet` predates the reasoning-effort ladder — its
        audited `supported_efforts` is empty even though `claude`'s CLI-wide
        enum accepts `low` for other models."""
        contract = probe_models.PROVIDER_CLI_CONTRACTS["claude_code_cli"]
        model = probe_models.AUDITED_MODEL_CATALOG["claude-3-7-sonnet"]
        self.assertEqual(model.supported_efforts, ())
        self.assertIn("low", contract.accepted_efforts)
        with self.assertRaises(probe_models.UnsupportedEffortError):
            contract.format_argv("claude-3-7-sonnet", "low")

    def test_format_argv_only_consults_the_audited_ladder_for_its_own_provider(self) -> None:
        """`claude-sonnet-4-6` is audited under `antigravity_cli` (`agy
        models` lists it, ladder `low|medium|high`), but the `claude` binary's
        own catalog also accepts it — with a longer ladder that reaches
        `max`. Before this fix, `format_argv` consulted the audited entry
        regardless of which provider it belonged to, so the `claude_code_cli`
        contract wrongly inherited `agy`'s narrower ladder and rejected `max`
        even though the `claude` CLI itself accepts it — the audited entry
        made the claude path strictly worse than an unaudited model. The agy
        path must still narrow to its own ladder; the claude path narrows to
        the `_CROSS_PROVIDER_EFFORT_LADDERS` override for this exact model
        instead — it does *not* fall back to its own provider-wide enum,
        which still contains `xhigh` (see the next test)."""
        audited = probe_models.AUDITED_MODEL_CATALOG["claude-sonnet-4-6"]
        self.assertEqual(audited.provider_id, "antigravity_cli")
        self.assertEqual(audited.supported_efforts, ("low", "medium", "high"))

        agy_contract = probe_models.PROVIDER_CLI_CONTRACTS["antigravity_cli"]
        with self.assertRaises(probe_models.UnsupportedEffortError):
            agy_contract.format_argv("claude-sonnet-4-6", "max")
        self.assertEqual(
            agy_contract.format_argv("claude-sonnet-4-6", "high"),
            ("--model", "claude-sonnet-4-6", "--effort", "high"),
        )

        claude_contract = probe_models.PROVIDER_CLI_CONTRACTS["claude_code_cli"]
        self.assertIn("max", claude_contract.accepted_efforts)
        self.assertEqual(
            claude_contract.format_argv("claude-sonnet-4-6", "max"),
            ("--model", "claude-sonnet-4-6", "--effort", "max"),
        )

    def test_format_argv_falls_back_to_the_providers_enum_for_a_model_audited_elsewhere(self) -> None:
        """Regression guard for finding F7 (live-model-catalog-audit.md §3):
        a model audited under a *different* provider, with no
        `_CROSS_PROVIDER_EFFORT_LADDERS` override for this exact pairing,
        must fall through to *this* provider's whole CLI enum rather than
        the other provider's narrower ladder.

        `claude-sonnet-4-6` no longer witnesses this on its own: the test
        above shows the `claude_code_cli` contract narrows it via the
        `_CROSS_PROVIDER_EFFORT_LADDERS` override (branch 2), not the plain
        provider-enum fallback (branch 3) — so no test reached branch 3 with
        an audited model until this one. `claude-opus-4-6-thinking` and
        `gpt-oss-120b-medium` are both audited under `antigravity_cli` too,
        but neither has an override entry for `claude_code_cli` or
        `codex_cli`, so routing them through those contracts must reach
        branch 3 and succeed at efforts their *own* audited ladder does not
        contain. Re-introducing the original F7 bug — consulting
        `audited.supported_efforts` whenever the model is audited at all,
        regardless of which provider it belongs to — makes both calls below
        raise instead of returning argv, because `claude-opus-4-6-thinking`'s
        audited ladder (`("low", "medium", "high")`) omits `max` and
        `gpt-oss-120b-medium`'s (`("medium",)`) omits `high`. This guard must
        survive ticket 46's re-keying of `AUDITED_MODEL_CATALOG`."""
        opus_thinking = probe_models.AUDITED_MODEL_CATALOG["claude-opus-4-6-thinking"]
        self.assertEqual(opus_thinking.provider_id, "antigravity_cli")
        self.assertNotIn(
            ("claude_code_cli", "claude-opus-4-6-thinking"),
            probe_models._CROSS_PROVIDER_EFFORT_LADDERS,
        )
        claude_contract = probe_models.PROVIDER_CLI_CONTRACTS["claude_code_cli"]
        self.assertEqual(
            claude_contract.format_argv("claude-opus-4-6-thinking", "max"),
            ("--model", "claude-opus-4-6-thinking", "--effort", "max"),
        )

        oss = probe_models.AUDITED_MODEL_CATALOG["gpt-oss-120b-medium"]
        self.assertEqual(oss.provider_id, "antigravity_cli")
        self.assertNotIn(
            ("codex_cli", "gpt-oss-120b-medium"),
            probe_models._CROSS_PROVIDER_EFFORT_LADDERS,
        )
        codex_contract = probe_models.PROVIDER_CLI_CONTRACTS["codex_cli"]
        self.assertEqual(
            codex_contract.format_argv("gpt-oss-120b-medium", "high"),
            ("--model", "gpt-oss-120b-medium", "-c", 'model_reasoning_effort="high"'),
        )

    def test_format_argv_rejects_xhigh_for_the_agy_hosted_claude_sonnet_4_6(self) -> None:
        """live-model-catalog-audit.md §3, finding F7: the doc previously claimed
        `claude-sonnet-4-6` carries `xhigh` on the `claude` side too, but the
        binary's own `xhigh` availability list names "Sonnet 5", not "Sonnet
        4.6" — only `max` is real there. `xhigh` *is* in `claude_code_cli`'s
        whole-provider `accepted_efforts` enum, so without the
        `_CROSS_PROVIDER_EFFORT_LADDERS` correction the fallback branch above
        would wave this exact pairing through as an argv the `claude` CLI
        itself rejects."""
        claude_contract = probe_models.PROVIDER_CLI_CONTRACTS["claude_code_cli"]
        self.assertIn("xhigh", claude_contract.accepted_efforts)
        with self.assertRaises(probe_models.UnsupportedEffortError):
            claude_contract.format_argv("claude-sonnet-4-6", "xhigh")
        self.assertEqual(
            claude_contract.format_argv("claude-sonnet-4-6", "max"),
            ("--model", "claude-sonnet-4-6", "--effort", "max"),
        )

    def test_the_cross_provider_override_names_its_own_table_not_the_providers_enum(self) -> None:
        """Mirrors the `assertIn("for model 'gpt-5.6-luna'", ...)` pinning on
        the audited branch above (`test_format_argv_checks_the_models_own_
        ladder_not_the_provider_union`): `UnsupportedEffortError`'s message
        must name the table that was actually consulted. Before this fix the
        override branch reused `f"provider {self.provider_id}"` even when
        `_CROSS_PROVIDER_EFFORT_LADDERS` supplied the ladder — sending a
        maintainer to `claude_code_cli`'s `accepted_efforts`, which *does*
        contain `xhigh`, so the plausible-looking fix (deleting `xhigh` from
        that tuple) would silently break `claude-opus-5`/`claude-sonnet-5`/
        `claude-fable-5`, which really do carry it."""
        claude_contract = probe_models.PROVIDER_CLI_CONTRACTS["claude_code_cli"]
        with self.assertRaises(probe_models.UnsupportedEffortError) as caught:
            claude_contract.format_argv("claude-sonnet-4-6", "xhigh")
        message = str(caught.exception)
        self.assertIn("_CROSS_PROVIDER_EFFORT_LADDERS", message)
        self.assertIn("claude-sonnet-4-6", message)
        self.assertNotIn(f"provider {claude_contract.provider_id}", message)
        self.assertIn("(accepted: ['low', 'medium', 'high', 'max'])", message)

    def test_format_argv_falls_back_to_the_provider_union_for_an_unaudited_model(self) -> None:
        """A model discovered live after this audit ran has no per-model
        ladder to consult, so the provider's whole CLI enum governs — this is
        what keeps a freshly-released model routable before the audit catches
        up to it.

        Mirrors the override branch's message pinning
        (`test_the_cross_provider_override_names_its_own_table_not_the_
        providers_enum`), which was itself hard-pinned while this branch's
        `ladder_name` was asserted nowhere: the fallback branch must name
        the *provider* whose whole enum was consulted, not the model and not
        the override table."""
        contract = probe_models.PROVIDER_CLI_CONTRACTS["antigravity_cli"]
        self.assertNotIn("brand-new-model-high", probe_models.AUDITED_MODEL_CATALOG)
        self.assertEqual(
            contract.format_argv("brand-new-model-high", "high"),
            ("--model", "brand-new-model-high", "--effort", "high"),
        )
        with self.assertRaises(probe_models.UnsupportedEffortError) as caught:
            contract.format_argv("brand-new-model-high", "xhigh")
        message = str(caught.exception)
        self.assertIn(f"for provider {contract.provider_id}", message)
        self.assertNotIn("brand-new-model-high", message)
        self.assertNotIn("_CROSS_PROVIDER_EFFORT_LADDERS", message)
        self.assertIn("(accepted: ['low', 'medium', 'high'])", message)


class AuditedCatalogTests(unittest.TestCase):
    """Ticket 45 AC: "Audit actual supported model identifiers from installed
    CLI providers"."""

    def test_codex_sol_records_the_full_six_level_effort_ladder(self) -> None:
        sol = probe_models.AUDITED_MODEL_CATALOG["gpt-5.6-sol"]
        self.assertEqual(sol.provider_id, "codex_cli")
        self.assertEqual(sol.supported_efforts, ("low", "medium", "high", "xhigh", "max", "ultra"))
        self.assertEqual(sol.default_effort, "low")
        self.assertEqual(sol.context_window, 272000)

    def test_codex_luna_stops_one_rung_below_sol(self) -> None:
        """Luna's embedded catalog entry has no `ultra` rung — routing it at
        `ultra` is a CLI error, not a slower run."""
        luna = probe_models.AUDITED_MODEL_CATALOG["gpt-5.6-luna"]
        self.assertEqual(luna.supported_efforts, ("low", "medium", "high", "xhigh", "max"))
        self.assertNotIn("ultra", luna.supported_efforts)

    def test_gemini_pro_offers_no_medium_rung(self) -> None:
        """`agy models` lists only high and low for Gemini 3.1 Pro."""
        efforts = {
            model.default_effort
            for model in probe_models.AUDITED_MODEL_CATALOG.values()
            if model.model_id.startswith("gemini-3.1-pro")
        }
        self.assertEqual(efforts, {"high", "low"})

    def test_agy_model_ids_bake_the_effort_into_the_identifier(self) -> None:
        flash = probe_models.AUDITED_MODEL_CATALOG["gemini-3.6-flash-high"]
        self.assertEqual(flash.provider_id, "antigravity_cli")
        self.assertEqual(flash.display_label, "Gemini 3.6 Flash (High)")
        self.assertEqual(flash.supported_efforts, ("high",))

    def test_the_codex_entries_track_the_live_catalog_not_the_stale_embedded_one(self) -> None:
        """`codex`'s embedded catalog still advertises a 372,000-token window
        and a `gpt-5.2`; `~/.codex/models_cache.json` — what the CLI actually
        uses — publishes 272,000 and has replaced it with Codex Spark."""
        self.assertNotIn("gpt-5.2", probe_models.AUDITED_MODEL_CATALOG)
        spark = probe_models.AUDITED_MODEL_CATALOG["gpt-5.3-codex-spark"]
        self.assertEqual(spark.default_effort, "high")
        self.assertEqual(spark.context_window, 128000)
        codex_windows = {
            model.context_window
            for model in probe_models.AUDITED_MODEL_CATALOG.values()
            if model.provider_id == "codex_cli" and model.model_id.startswith("gpt-5.6")
        }
        self.assertEqual(codex_windows, {272000})

    def test_claude_context_windows_three_read_from_the_binary_one_inferred(self) -> None:
        """Ground truth extracted from the installed binary at
        ~/.local/share/claude/versions/2.1.241: the three Claude 5 models
        publish context.window:1_000_000 literally. The pre-effort
        claude-3-7-sonnet's catalog entry carries no `context` key at all —
        its 200_000 is inferred (Claude 3.7 Sonnet's publicly documented
        context window), not read from the binary; see its `evidence` field.
        Pinned because `roles.*.capability_requirements.min_context` in
        routing-config.json (200000 / 128000 / 32000) cannot be gated
        against a `None` window by ticket 46's consumer."""
        windows = {
            model_id: probe_models.AUDITED_MODEL_CATALOG[model_id].context_window
            for model_id in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-3-7-sonnet")
        }
        self.assertEqual(
            windows,
            {
                "claude-opus-5": 1_000_000,
                "claude-sonnet-5": 1_000_000,
                "claude-fable-5": 1_000_000,
                "claude-3-7-sonnet": 200_000,
            },
        )

    def test_local_models_expose_no_reasoning_effort_parameter(self) -> None:
        local = probe_models.AUDITED_MODEL_CATALOG["qwen3.8-27b-mlx"]
        self.assertTrue(local.local_only)
        self.assertEqual(local.supported_efforts, ())
        self.assertIsNone(local.default_effort)

    def test_every_entry_carries_provenance(self) -> None:
        for model_id, model in probe_models.AUDITED_MODEL_CATALOG.items():
            with self.subTest(model_id=model_id):
                self.assertTrue(model.evidence.strip(), f"{model_id} has no evidence string")
                self.assertIn(model.provider_id, probe_models.PROVIDER_IDS)

    def test_default_effort_is_always_one_of_the_supported_efforts(self) -> None:
        for model_id, model in probe_models.AUDITED_MODEL_CATALOG.items():
            with self.subTest(model_id=model_id):
                if model.default_effort is not None:
                    self.assertIn(model.default_effort, model.supported_efforts)

    def test_supported_efforts_are_a_subset_of_the_providers_cli_enum(self) -> None:
        for model_id, model in probe_models.AUDITED_MODEL_CATALOG.items():
            contract = probe_models.PROVIDER_CLI_CONTRACTS[model.provider_id]
            with self.subTest(model_id=model_id):
                self.assertLessEqual(set(model.supported_efforts), set(contract.accepted_efforts))

    def test_cross_provider_effort_ladders_stay_inside_the_providers_enum_and_reachable(self) -> None:
        """Golden Rule 8: `_CROSS_PROVIDER_EFFORT_LADDERS` is `format_argv`'s
        other ladder table, and the test above gives `AUDITED_MODEL_CATALOG`
        a guard this one never got. Two invariants:
        (a) every override ladder is a subset of that provider's whole CLI
            enum — a value the CLI cannot parse (e.g. `"ultra"` for
            `claude_code_cli`) would make `format_argv` emit an argv the
            provider rejects, inverting the fail-closed contract described
            at probe_models.py:203-206.
        (b) no dead keys — a `(provider_id, model_id)` pair whose model is
            audited *under that same provider* takes `format_argv`'s audited
            branch (probe_models.py:236-239) before this table is ever
            consulted, so such a key could never fire."""
        for (provider_id, model_id), ladder in probe_models._CROSS_PROVIDER_EFFORT_LADDERS.items():
            with self.subTest(provider_id=provider_id, model_id=model_id):
                contract = probe_models.PROVIDER_CLI_CONTRACTS[provider_id]
                self.assertLessEqual(set(ladder), set(contract.accepted_efforts))

                audited = probe_models.AUDITED_MODEL_CATALOG.get(model_id)
                reachable = audited is None or audited.provider_id != provider_id
                self.assertTrue(
                    reachable,
                    f"({provider_id!r}, {model_id!r}) is audited under {provider_id!r} itself "
                    "and can never fall through to this table",
                )

    def test_claude_5_models_carry_the_full_five_rung_ladder_and_a_high_default(self) -> None:
        """`_CLAUDE_EVIDENCE` states, per model, `default_effort:"high"` and
        the five-rung ladder (low, medium, high, xhigh, max) read directly
        from the installed `claude` 2.1.241 binary's catalog — this
        project's own T3 workers. Only `context_window` was pinned before
        (`test_claude_context_windows_three_read_from_the_binary_one_inferred`
        above); narrowing `_CLAUDE_EFFORTS` to `("low", "medium", "high")`
        left the whole suite green until this test."""
        for model_id in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5"):
            with self.subTest(model_id=model_id):
                model = probe_models.AUDITED_MODEL_CATALOG[model_id]
                self.assertEqual(model.supported_efforts, ("low", "medium", "high", "xhigh", "max"))
                self.assertEqual(model.default_effort, "high")

    def test_gpt_oss_120b_is_audited_under_antigravity_not_codex(self) -> None:
        """The audit's own headline drift finding: `production_invoker.
        CODEX_MODELS` wrongly classifies `gpt-oss-120b-medium` as a Codex
        model, but `agy models` lists it, not `codex`'s catalog."""
        self.assertEqual(
            probe_models.AUDITED_MODEL_CATALOG["gpt-oss-120b-medium"].provider_id,
            "antigravity_cli",
        )

    def test_claude_opus_4_6_thinking_is_pinned_to_the_agy_three_rung_ladder(self) -> None:
        """Audited under `antigravity_cli` (`agy models`), so its ladder is
        the shorter `agy --help` enum (low|medium|high) rather than the
        `claude` binary's own five-rung one."""
        self.assertEqual(
            probe_models.AUDITED_MODEL_CATALOG["claude-opus-4-6-thinking"].supported_efforts,
            ("low", "medium", "high"),
        )


class DisplayLabelMappingTests(unittest.TestCase):
    """Ticket 45 AC: "Map human-readable display labels to exact CLI wire
    identifiers"."""

    def test_resolve_accepts_the_canonical_display_label(self) -> None:
        self.assertEqual(probe_models.resolve_model_id("Codex 5.6 Sol"), "gpt-5.6-sol")
        self.assertEqual(probe_models.resolve_model_id("Claude Opus 5 (Thinking)"), "claude-opus-5")

    def test_resolve_accepts_a_wire_identifier_unchanged(self) -> None:
        self.assertEqual(probe_models.resolve_model_id("gpt-5.6-sol"), "gpt-5.6-sol")

    def test_resolve_accepts_a_differently_cased_spelling_via_the_casefold_fallback(self) -> None:
        """`"GPT-5.6-Sol"` is in no model's `aliases` tuple — this exercises
        `resolve_model_id`'s casefold fallback, not the alias table."""
        self.assertEqual(probe_models.resolve_model_id("GPT-5.6-Sol"), "gpt-5.6-sol")

    def test_resolve_strips_surrounding_whitespace_before_the_casefold_fallback(self) -> None:
        self.assertEqual(probe_models.resolve_model_id("  GPT-5.6-Sol  "), "gpt-5.6-sol")

    def test_resolve_accepts_the_spellings_the_rest_of_the_repo_already_uses(self) -> None:
        """The alias column exists for the names already written into
        `routing-config.json` and `production_invoker.py`. Drop `aliases` from
        the index and these three stop resolving."""
        self.assertEqual(probe_models.resolve_model_id("gpt-oss-120b"), "gpt-oss-120b-medium")
        self.assertEqual(probe_models.resolve_model_id("Qwen3.8-27B-MLX-6bit"), "qwen3.8-27b-mlx")
        self.assertEqual(probe_models.resolve_model_id("Gemma 4 E4B"), "gemma-4-e4b-it-mlx")

    def test_resolve_rejects_an_unknown_label_rather_than_guessing(self) -> None:
        with self.assertRaises(probe_models.UnknownModelError):
            probe_models.resolve_model_id("Claude Opus 9 (Imaginary)")

    def test_every_catalog_entry_is_reachable_by_its_display_label(self) -> None:
        for model_id, model in probe_models.AUDITED_MODEL_CATALOG.items():
            with self.subTest(model_id=model_id):
                self.assertEqual(probe_models.resolve_model_id(model.display_label), model_id)

    def test_build_label_index_raises_on_a_genuine_label_collision(self) -> None:
        """`test_labels_and_aliases_never_resolve_two_models` below only
        asserts the *data invariant* holds on the finished, already-built
        `DISPLAY_LABEL_TO_MODEL_ID` — it never actually drives two colliding
        entries through `_build_label_index` and watches it raise. Replacing
        that function's `raise ModelCatalogError(...)` body with `pass` would
        leave every other test in this module green; only handing it a
        deliberately colliding pair catches it."""
        colliding = (
            probe_models.AuditedModel(
                model_id="model-a",
                display_label="Shared Label",
                provider_id="codex_cli",
                supported_efforts=(),
                default_effort=None,
                context_window=None,
                local_only=False,
                evidence="test fixture",
            ),
            probe_models.AuditedModel(
                model_id="model-b",
                display_label="Shared Label",
                provider_id="codex_cli",
                supported_efforts=(),
                default_effort=None,
                context_window=None,
                local_only=False,
                evidence="test fixture",
            ),
        )
        with self.assertRaises(probe_models.ModelCatalogError):
            probe_models._build_label_index(colliding)

    def test_labels_and_aliases_never_resolve_two_models(self) -> None:
        """A label that could mean two models is the ambiguity this table
        exists to remove — including when the two spellings differ only by
        case (`GPT-5.5` the label vs `gpt-5.5` the identifier)."""
        owners: dict[str, set[str]] = {}
        for label, model_id in probe_models.DISPLAY_LABEL_TO_MODEL_ID.items():
            owners.setdefault(label.casefold(), set()).add(model_id)
        self.assertEqual({label: ids for label, ids in owners.items() if len(ids) > 1}, {})


class LmStudioProbeTests(unittest.TestCase):
    """Ticket 45 AC: "Produce probe_models.py to probe live model availability
    dynamically" — the LM Studio half."""

    @staticmethod
    def _response(payload: object) -> io.BytesIO:
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def test_live_models_are_parsed_and_embedding_models_dropped(self) -> None:
        captured: dict[str, object] = {}

        def opener(url: str, timeout: float) -> io.BytesIO:
            captured["url"] = url
            captured["timeout"] = timeout
            return self._response(
                {
                    "data": [
                        {"id": "qwen3.8-27b-mlx", "object": "model"},
                        {"id": "text-embedding-nomic-embed-text-v1.5", "object": "model"},
                        {"id": "gemma-4-e4b-it-mlx", "object": "model"},
                    ]
                }
            )

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertTrue(probe.available)
        self.assertEqual(captured["url"], probe_models.LM_STUDIO_MODELS_ENDPOINT)
        self.assertEqual([model.model_id for model in probe.models], ["qwen3.8-27b-mlx", "gemma-4-e4b-it-mlx"])
        self.assertTrue(all(model.source == "live" for model in probe.models))
        self.assertIsNone(probe.error)

    def test_the_audited_label_wins_over_the_raw_lm_studio_model_id(self) -> None:
        """The twin of `CliProviderProbeTests.test_the_audited_label_wins_
        over_agys_own_listed_label` and `CodexCatalogCacheTests.test_the_
        audited_label_wins_over_codexs_own_display_name`, closed here on the
        LM Studio side: `_local_probed_model`'s `if audited is not None:`
        branch is what supplies the audited display label
        `"Qwen3.8 27B MLX (Local)"` rather than the raw id
        `"qwen3.8-27b-mlx"` LM Studio's `/v1/models` actually returns —
        `display_label` is the only field that differs between the audited
        and not-audited branches, so a test only checking `model_id` and
        `source` (as the test above does) cannot catch that branch being
        disabled."""

        def opener(url: str, timeout: float) -> io.BytesIO:
            return self._response({"data": [{"id": "qwen3.8-27b-mlx"}]})

        probe = probe_models.probe_lm_studio(opener=opener)

        qwen = probe.models[0]
        self.assertEqual(qwen.display_label, "Qwen3.8 27B MLX (Local)")
        self.assertNotEqual(qwen.display_label, "qwen3.8-27b-mlx")

    def test_probe_lm_studio_defaults_to_the_module_constant_timeout(self) -> None:
        """Golden Rule 6: the previous version of this test passed
        `timeout=0.2` explicitly and then asserted the captured value equals
        that same literal — a tautology that survives `DEFAULT_PROBE_TIMEOUT_
        SECONDS` being changed to anything. Calling without a `timeout=`
        override exercises the parameter's actual default."""
        captured: dict[str, object] = {}

        def opener(url: str, timeout: float) -> io.BytesIO:
            captured["timeout"] = timeout
            return self._response({"data": [{"id": "qwen3.8-27b-mlx"}]})

        probe_models.probe_lm_studio(opener=opener)

        # Literal, not the constant: spec 0013's non-blocking launch probe
        # commits to a 200ms budget against LM Studio. Comparing against
        # `probe_models.DEFAULT_PROBE_TIMEOUT_SECONDS` would be a tautology
        # that can't notice the constant itself drifting away from 0.2.
        self.assertEqual(captured["timeout"], 0.2)

    def test_probe_lm_studio_defaults_to_the_module_constant_endpoint(self) -> None:
        """`LM_STUDIO_MODELS_ENDPOINT` is the third member of the probe
        contract, alongside `DEFAULT_PROBE_TIMEOUT_SECONDS` (pinned just
        above) and `CLI_PROBE_TIMEOUT_SECONDS` — but it was the only one
        never pinned to a literal. The routing protocol documents this exact
        address as `127.0.0.1:1234/v1/models`."""
        captured: dict[str, object] = {}

        def opener(url: str, timeout: float) -> io.BytesIO:
            captured["url"] = url
            return self._response({"data": [{"id": "qwen3.8-27b-mlx"}]})

        probe_models.probe_lm_studio(opener=opener)

        # Literal, not the constant: the routing protocol documents LM
        # Studio's probe address as 127.0.0.1:1234/v1/models. Comparing
        # against `probe_models.LM_STUDIO_MODELS_ENDPOINT` would be a
        # tautology that can't notice the constant itself drifting away
        # from that address.
        self.assertEqual(captured["url"], "http://127.0.0.1:1234/v1/models")

    def test_probe_lm_studio_forwards_a_non_default_endpoint_to_the_opener(self) -> None:
        captured: dict[str, object] = {}

        def opener(url: str, timeout: float) -> io.BytesIO:
            captured["url"] = url
            return self._response({"data": [{"id": "qwen3.8-27b-mlx"}]})

        custom_endpoint = "http://127.0.0.1:9999/v1/models"
        self.assertNotEqual(custom_endpoint, probe_models.LM_STUDIO_MODELS_ENDPOINT)

        probe_models.probe_lm_studio(opener=opener, endpoint=custom_endpoint)

        self.assertEqual(captured["url"], custom_endpoint)

    def test_a_mixed_case_embedding_id_is_still_filtered_out(self) -> None:
        """`_is_embedding_model` casefolds before matching — LM Studio
        publishes mixed-case ids (the audit's own alias is
        `Qwen3.8-27B-MLX-6bit`), so an ``...-Embedding-...`` model with
        capitalized casing would otherwise slip past a marker match that
        only ever sees the lowercase spelling and be offered as a routable
        worker."""

        def opener(url: str, timeout: float) -> io.BytesIO:
            return self._response(
                {
                    "data": [
                        {"id": "qwen3.8-27b-mlx"},
                        {"id": "Text-Embedding-Ada-002"},
                    ]
                }
            )

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertEqual([model.model_id for model in probe.models], ["qwen3.8-27b-mlx"])

    def test_the_embed_dash_marker_drops_a_model_that_does_not_say_embedding(self) -> None:
        """`_EMBEDDING_MARKERS` has two entries — `"embedding"` and the
        shorter `"embed-"` — because some servers name embedding models
        without ever spelling out the word `embedding`."""

        def opener(url: str, timeout: float) -> io.BytesIO:
            return self._response(
                {
                    "data": [
                        {"id": "qwen3.8-27b-mlx"},
                        {"id": "nomic-embed-text-v1.5"},
                    ]
                }
            )

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertEqual([model.model_id for model in probe.models], ["qwen3.8-27b-mlx"])

    def test_an_entry_with_no_id_key_at_all_is_skipped_not_crashed_on(self) -> None:
        def opener(url: str, timeout: float) -> io.BytesIO:
            return self._response({"data": [{"object": "model"}, {"id": "qwen3.8-27b-mlx"}]})

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertEqual([model.model_id for model in probe.models], ["qwen3.8-27b-mlx"])

    def test_an_entry_with_an_empty_string_id_is_skipped_not_treated_as_a_model(self) -> None:
        """`not isinstance(model_id, str) or not model_id or ...` — the
        `not model_id` half rejects an empty string specifically, which is
        distinct from the "no id key at all" case above (`entry.get("id")`
        there returns `None`, not `""`)."""

        def opener(url: str, timeout: float) -> io.BytesIO:
            return self._response({"data": [{"id": ""}, {"id": "qwen3.8-27b-mlx"}]})

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertEqual([model.model_id for model in probe.models], ["qwen3.8-27b-mlx"])

    def test_a_live_model_absent_from_the_audit_still_reports_as_available(self) -> None:
        def opener(url: str, timeout: float) -> io.BytesIO:
            return self._response({"data": [{"id": "brand-new-local-model"}]})

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertTrue(probe.available)
        discovered = probe.models[0]
        self.assertEqual(discovered.model_id, "brand-new-local-model")
        self.assertEqual(discovered.display_label, "brand-new-local-model")
        self.assertEqual(discovered.supported_efforts, ())
        self.assertTrue(discovered.local_only)
        self.assertEqual(discovered.source, "live")

    def test_an_unreachable_server_degrades_instead_of_raising(self) -> None:
        def opener(url: str, timeout: float) -> io.BytesIO:
            raise OSError("connection refused")

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertFalse(probe.available)
        self.assertEqual(probe.models, ())
        self.assertIn("connection refused", probe.error or "")

    def test_malformed_json_degrades_instead_of_raising(self) -> None:
        def opener(url: str, timeout: float) -> io.BytesIO:
            return io.BytesIO(b"<html>not json</html>")

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertFalse(probe.available)
        self.assertIn("json", (probe.error or "").lower())

    def test_a_json_array_instead_of_an_object_degrades_instead_of_raising(self) -> None:
        def opener(url: str, timeout: float) -> io.BytesIO:
            return self._response(["qwen3.8-27b-mlx"])

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertFalse(probe.available)
        self.assertIn("expected an object", probe.error or "")

    def test_a_server_with_no_models_loaded_is_not_available(self) -> None:
        def opener(url: str, timeout: float) -> io.BytesIO:
            return self._response({"data": []})

        probe = probe_models.probe_lm_studio(opener=opener)

        self.assertFalse(probe.available)
        self.assertEqual(probe.models, ())


class CliProviderProbeTests(unittest.TestCase):
    """Ticket 45 AC: the CLI half of the live probe."""

    AGY_MODELS_STDOUT = (
        "Fetching available models...\n"
        "gemini-3.7-flash-high\tGemini 3.7 Flash (High)\n"
        "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)\n"
        "brand-new-model-high\tBrand New Model (High)\n"
    )

    def test_agy_models_output_is_parsed_into_live_entries(self) -> None:
        calls: list[list[str]] = []

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout=self.AGY_MODELS_STDOUT, stderr="")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli",
            which=lambda name: f"/usr/local/bin/{name}",
            runner=runner,
        )

        self.assertTrue(probe.available)
        self.assertEqual(calls, [["agy", "models"]])
        self.assertEqual(probe.binary_path, "/usr/local/bin/agy")
        by_id = {model.model_id: model for model in probe.models}
        self.assertIn("gemini-3.7-flash-high", by_id)
        self.assertEqual(by_id["gemini-3.7-flash-high"].source, "live")
        self.assertEqual(by_id["gemini-3.1-pro-low"].display_label, "Gemini 3.1 Pro (Low)")

    def test_the_audited_label_wins_over_agys_own_listed_label(self) -> None:
        """`agy models` could print any label for a model the audit already
        knows; the audited spelling must win so the probe payload agrees with
        `routing-config.json` and `DISPLAY_LABEL_TO_MODEL_ID` regardless of
        what this run's listing happens to say."""

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="gemini-3.6-flash-high\tSome Other Vendor Label\n", stderr=""
            )

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        flash = probe.models[0]
        self.assertEqual(flash.display_label, "Gemini 3.6 Flash (High)")
        self.assertNotEqual(flash.display_label, "Some Other Vendor Label")

    def test_a_model_agy_lists_but_the_audit_missed_is_still_reported(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, stdout=self.AGY_MODELS_STDOUT, stderr="")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        discovered = {model.model_id: model for model in probe.models}["brand-new-model-high"]
        self.assertEqual(discovered.display_label, "Brand New Model (High)")
        self.assertEqual(discovered.supported_efforts, ("high",))
        # Golden Rule 8: `test_a_listed_model_without_an_effort_suffix_keeps_
        # the_whole_enum` below asserts both halves of `_infer_efforts_from_
        # model_id`'s return for the no-suffix case; this is its suffixed
        # twin, and `default_effort` was the unasserted write-site here.
        self.assertEqual(discovered.default_effort, "high")

    def test_the_list_command_is_run_with_stdin_devnull_and_the_given_timeout(self) -> None:
        """Golden Rule 11: `agy -p`-style CLI workers hang on a missing TTY
        without an explicit `stdin`. Drives `timeout` to a non-default value
        too, so this cannot pass by coincidentally matching a default.

        Golden Rules 5 and 8: `captured.update(kwargs)` puts every kwarg the
        `runner(...)` call passes within reach, but only `stdin` and
        `timeout` used to be asserted — `check`, `text`, and `capture_output`
        were captured and silently ignored, so a mutation flipping any of
        them (`check=False`->`True`, `text=True`->`False`,
        `capture_output=True`->`False`) left the whole suite green even
        though each is load-bearing against the real `subprocess.run`:
        `check=True` would raise `CalledProcessError` instead of degrading on
        a non-zero exit, `text=False` would raise `TypeError` on the `.strip()`
        calls downstream, and `capture_output=False` would silently drop
        every live model."""
        captured: dict[str, object] = {}

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, stdout=self.AGY_MODELS_STDOUT, stderr="")

        probe_models.probe_cli_provider(
            "antigravity_cli",
            which=lambda name: "/usr/local/bin/agy",
            runner=runner,
            timeout=7.5,
        )

        self.assertIs(captured["stdin"], subprocess.DEVNULL)
        self.assertEqual(captured["timeout"], 7.5)
        self.assertIs(captured["check"], False)
        self.assertIs(captured["text"], True)
        self.assertIs(captured["capture_output"], True)

    def test_a_missing_binary_reports_unavailable_without_running_anything(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise AssertionError("runner must not be called when the binary is absent")

        probe = probe_models.probe_cli_provider("antigravity_cli", which=lambda name: None, runner=runner)

        self.assertFalse(probe.available)
        self.assertEqual(probe.models, ())
        self.assertIsNone(probe.binary_path)
        self.assertIn("not installed", (probe.error or "").lower())

    def test_a_provider_without_a_list_command_falls_back_to_the_audit(self) -> None:
        """`claude` and `codex` publish no `models` subcommand, so an installed
        binary means "the audited catalog is callable", not "nothing is known"."""

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise AssertionError("claude has no list-models command to run")

        probe = probe_models.probe_cli_provider(
            "claude_code_cli", which=lambda name: "/usr/local/bin/claude", runner=runner
        )

        self.assertTrue(probe.available)
        self.assertTrue(probe.models)
        self.assertTrue(all(model.source == "audited" for model in probe.models))
        model_ids = [model.model_id for model in probe.models]
        self.assertIn("claude-opus-5", model_ids)
        # `_audited_models_for`'s own provider filter: a degrade-to-snapshot
        # path must not leak another provider's models into this probe.
        # Mutating `if model.provider_id == provider_id` to `if True` keeps
        # every assertion above green while this one catches it.
        self.assertNotIn("gpt-5.6-sol", model_ids)
        self.assertNotIn("gemini-3.6-flash-high", model_ids)
        self.assertTrue(all(model.provider_id == "claude_code_cli" for model in probe.models))

    def test_a_failing_list_command_falls_back_to_the_audit_and_records_the_error(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="agy: not logged in")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        self.assertTrue(probe.available)
        self.assertIn("not logged in", probe.error or "")
        self.assertTrue(all(model.source == "audited" for model in probe.models))

    def test_a_nonzero_exit_with_empty_stderr_reports_the_exit_status(self) -> None:
        """`reason = (completed.stderr or "").strip() or f"exit status
        {completed.returncode}"` — every other non-zero-exit test in this
        class supplies a non-empty stderr, so the `or f"exit status ..."`
        fallback itself is never reached without this one."""

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 3, stdout="", stderr="")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        self.assertIn("exit status 3", probe.error or "")

    def test_a_timed_out_list_command_falls_back_to_the_audit(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=5.0)

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        self.assertTrue(probe.available)
        self.assertIn("timed out", (probe.error or "").lower())
        self.assertTrue(all(model.source == "audited" for model in probe.models))

    def test_a_list_command_that_cannot_be_launched_falls_back_to_the_audit(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise OSError("Exec format error")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        self.assertTrue(probe.available)
        self.assertIn("Exec format error", probe.error or "")
        self.assertEqual([model.source for model in probe.models], ["audited"] * len(probe.models))

    def test_a_listing_of_pure_progress_chatter_falls_back_to_the_audit(self) -> None:
        """`agy models` prints "Fetching available models..." before its rows.
        A run that prints only that listed nothing."""

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, stdout="Fetching available models...\n", stderr="")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        self.assertIn("listed no models", probe.error or "")
        self.assertTrue(probe.models)

    def test_a_line_starting_with_a_tab_has_an_empty_id_and_is_skipped(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="\tOrphaned Label\ngemini-3.6-flash-high\tGemini 3.6 Flash (High)\n", stderr=""
            )

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        self.assertEqual([model.model_id for model in probe.models], ["gemini-3.6-flash-high"])
        self.assertNotIn("", [model.model_id for model in probe.models])

    def test_a_line_with_a_blank_label_falls_back_to_the_model_id(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, stdout="brand-new-model-high\t\n", stderr="")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        discovered = probe.models[0]
        self.assertEqual(discovered.model_id, "brand-new-model-high")
        self.assertEqual(discovered.display_label, "brand-new-model-high")

    def test_a_listed_model_without_an_effort_suffix_keeps_the_whole_enum(self) -> None:
        """`agy` bakes the effort into most identifiers but not all — its two
        Claude entries carry none, so the session `--effort` flag governs and
        no single rung can be inferred."""

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="some-future-model\tSome Future Model\n", stderr=""
            )

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        discovered = probe.models[0]
        self.assertEqual(discovered.supported_efforts, ("low", "medium", "high"))
        self.assertIsNone(discovered.default_effort)

    def test_skipping_the_list_command_keeps_the_probe_local(self) -> None:
        """Spec 0013 wants a non-blocking launch probe, and `agy models`
        fetches over the network."""

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise AssertionError("list_models=False must not run the list command")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli",
            which=lambda name: "/usr/local/bin/agy",
            runner=runner,
            list_models=False,
        )

        self.assertTrue(probe.available)
        self.assertTrue(all(model.source == "audited" for model in probe.models))

    def test_probe_cli_provider_defaults_to_the_module_constant_cli_timeout(self) -> None:
        captured: dict[str, object] = {}

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, stdout=self.AGY_MODELS_STDOUT, stderr="")

        probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        # Literal, not the constant: the module docstring commits CLI
        # provider probes to a 15-second timeout. Comparing against
        # `probe_models.CLI_PROBE_TIMEOUT_SECONDS` would be a tautology
        # that can't notice the constant itself drifting away from 15.0.
        self.assertEqual(captured["timeout"], 15.0)

    def test_probe_all_forwards_a_non_default_cli_timeout_to_cli_providers(self) -> None:
        """`probe_all`'s own `cli_timeout` parameter, driven away from
        `CLI_PROBE_TIMEOUT_SECONDS` and checked at the runner it must reach."""
        captured: list[object] = []

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured.append(kwargs["timeout"])
            return subprocess.CompletedProcess(argv, 0, stdout=self.AGY_MODELS_STDOUT, stderr="")

        def opener(url: str, timeout: float) -> io.BytesIO:
            return io.BytesIO(json.dumps({"data": []}).encode("utf-8"))

        def cache_reader(path: Path) -> str:
            raise FileNotFoundError(f"no such file: {path}")

        probe_models.probe_all(
            opener=opener,
            which=lambda name: f"/bin/{name}",
            runner=runner,
            cache_reader=cache_reader,
            cli_timeout=42.0,
        )

        self.assertNotEqual(42.0, probe_models.CLI_PROBE_TIMEOUT_SECONDS)
        self.assertTrue(captured)
        self.assertTrue(all(value == 42.0 for value in captured))

    def test_probe_all_forwards_a_non_default_endpoint_to_lm_studio(self) -> None:
        """Golden Rule 6: `probe_all`'s `endpoint` parameter has a sibling
        test for `timeout` and `cli_timeout` ("reaches probe_all") but was
        itself never driven away from `LM_STUDIO_MODELS_ENDPOINT` anywhere in
        this module — mutating `probe_lm_studio(endpoint=endpoint, ...)` to
        `probe_lm_studio(endpoint=LM_STUDIO_MODELS_ENDPOINT, ...)` inside
        `probe_all` left the whole suite green."""
        captured: dict[str, object] = {}

        def opener(url: str, timeout: float) -> io.BytesIO:
            captured["url"] = url
            return io.BytesIO(json.dumps({"data": []}).encode("utf-8"))

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, stdout=self.AGY_MODELS_STDOUT, stderr="")

        def cache_reader(path: Path) -> str:
            raise FileNotFoundError(f"no such file: {path}")

        custom_endpoint = "http://127.0.0.1:9999/v1/models"
        self.assertNotEqual(custom_endpoint, probe_models.LM_STUDIO_MODELS_ENDPOINT)

        probe_models.probe_all(
            endpoint=custom_endpoint,
            opener=opener,
            which=lambda name: f"/bin/{name}",
            runner=runner,
            cache_reader=cache_reader,
        )

        self.assertEqual(captured["url"], custom_endpoint)

    def test_probe_all_probes_providers_in_provider_ids_order(self) -> None:
        """`probe_all`'s docstring commits to "in `PROVIDER_IDS` order", but
        every other test here only checks the *set* of probed providers —
        `reversed(PROVIDER_IDS)` would pass them all. Recording the order
        `which` (the three CLI providers) and `opener` (LM Studio) are called
        in is what actually pins the sequence."""
        call_order: list[str] = []

        def which(name: str) -> str:
            call_order.append(name)
            return f"/bin/{name}"

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, stdout=self.AGY_MODELS_STDOUT, stderr="")

        def opener(url: str, timeout: float) -> io.BytesIO:
            call_order.append("lm_studio")
            return io.BytesIO(json.dumps({"data": []}).encode("utf-8"))

        def cache_reader(path: Path) -> str:
            raise FileNotFoundError(f"no such file: {path}")

        probe_models.probe_all(opener=opener, which=which, runner=runner, cache_reader=cache_reader)

        self.assertEqual(call_order, ["claude", "codex", "agy", "lm_studio"])

    def test_probe_all_defaults_both_its_own_timeouts_to_the_module_constants(self) -> None:
        """`probe_all`'s `timeout` and `cli_timeout` parameters each default
        to a module constant. Literal, not the constants themselves — a
        tautology comparing against `probe_models.DEFAULT_PROBE_TIMEOUT_
        SECONDS` / `CLI_PROBE_TIMEOUT_SECONDS` could not notice either
        default silently becoming `99.0`."""
        captured: dict[str, object] = {}

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured["cli_timeout"] = kwargs["timeout"]
            return subprocess.CompletedProcess(argv, 0, stdout=self.AGY_MODELS_STDOUT, stderr="")

        def opener(url: str, timeout: float) -> io.BytesIO:
            captured["lm_studio_timeout"] = timeout
            return io.BytesIO(json.dumps({"data": []}).encode("utf-8"))

        def cache_reader(path: Path) -> str:
            raise FileNotFoundError(f"no such file: {path}")

        probe_models.probe_all(opener=opener, which=lambda name: f"/bin/{name}", runner=runner, cache_reader=cache_reader)

        self.assertEqual(captured["cli_timeout"], 15.0)
        self.assertEqual(captured["lm_studio_timeout"], 0.2)

    def test_lm_studio_is_rejected_with_the_probe_it_should_have_used(self) -> None:
        with self.assertRaises(probe_models.ModelCatalogError) as caught:
            probe_models.probe_cli_provider("lm_studio_local")
        self.assertIn("probe_lm_studio", str(caught.exception))

    def test_an_unrecognized_provider_id_raises_unknown_provider_error(self) -> None:
        """`UnknownProviderError` is exported in `__all__`, but before this
        test nothing reached `probe_cli_provider`'s `if contract is None:
        raise UnknownProviderError(...)` guard — the twin guard three lines
        below (the `lm_studio_local` case just above) already had a test.
        Swapping the raised class for the module's own `ModelCatalogError`
        base class left the whole suite green until this pinned the
        subclass specifically."""
        with self.assertRaises(probe_models.UnknownProviderError) as caught:
            probe_models.probe_cli_provider("totally_unknown_provider")
        self.assertIn("totally_unknown_provider", str(caught.exception))


class CodexCatalogCacheTests(unittest.TestCase):
    """`codex` publishes no list command but caches the catalog it fetched.
    Reading it is the difference between a live probe and a snapshot that
    silently lags the account's real entitlements."""

    CACHE: ClassVar[dict[str, Any]] = {
        "models": [
            {
                "slug": "gpt-5.6-sol",
                "display_name": "GPT-5.6-Sol",
                "visibility": "list",
                "context_window": 272000,
                "default_reasoning_level": "low",
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "ultra"}],
            },
            {
                "slug": "codex-auto-review",
                "display_name": "Codex Auto Review",
                "visibility": "hide",
                "context_window": 272000,
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [{"effort": "medium"}],
            },
        ]
    }

    def _probe(self, reader: probe_models.CacheReader) -> probe_models.ProviderProbe:
        return probe_models.probe_cli_provider(
            "codex_cli", which=lambda name: "/usr/local/bin/codex", cache_reader=reader
        )

    def test_the_cache_supersedes_the_audited_snapshot(self) -> None:
        seen: list[Path] = []

        def reader(path: Path) -> str:
            seen.append(path)
            return json.dumps(self.CACHE)

        probe = self._probe(reader)

        # Literal, not the constant: the module docstring commits Codex's
        # catalog cache to ~/.codex/models_cache.json. Comparing against
        # `probe_models.CODEX_MODELS_CACHE_PATH` would be a tautology that
        # can't notice the constant itself drifting away from that path.
        self.assertEqual(seen, [Path.home() / ".codex" / "models_cache.json"])
        sol = {model.model_id: model for model in probe.models}["gpt-5.6-sol"]
        self.assertEqual(sol.source, "live")
        self.assertEqual(sol.supported_efforts, ("low", "ultra"))
        self.assertEqual(sol.default_effort, "low")
        self.assertEqual(sol.context_window, 272000)

    def test_the_audited_label_wins_over_codexs_own_display_name(self) -> None:
        """The cache's `display_name` for `gpt-5.6-sol` is `"GPT-5.6-Sol"`,
        but `routing-config.json` and `DISPLAY_LABEL_TO_MODEL_ID` both spell
        it `"Codex 5.6 Sol"` — the audited spelling must win so the probe
        payload and the config name the model the same way."""
        probe = self._probe(lambda path: json.dumps(self.CACHE))
        sol = {model.model_id: model for model in probe.models}["gpt-5.6-sol"]
        self.assertEqual(sol.display_label, "Codex 5.6 Sol")
        self.assertNotEqual(sol.display_label, "GPT-5.6-Sol")

    def test_models_the_cli_hides_are_not_offered_as_assignable(self) -> None:
        probe = self._probe(lambda path: json.dumps(self.CACHE))
        self.assertNotIn("codex-auto-review", [model.model_id for model in probe.models])

    def test_a_listable_entry_with_no_slug_is_skipped_not_crashed_on(self) -> None:
        """The twin of `LmStudioProbeTests.
        test_an_entry_with_no_id_key_at_all_is_skipped_not_crashed_on`, closed
        here on the cache side: `if isinstance(entry, dict) and entry.get(
        "visibility") == "list" and entry.get("slug")` guards `_cached_entry_
        to_model`'s unconditional `entry["slug"]` lookup. Mutating `and
        entry.get("slug")` to `and True` lets a malformed `visibility:
        "list"` entry with no `slug` key reach `entry["slug"]` and raise
        `KeyError` — out of a module this repo documents as never raising on
        a malformed provider response."""
        malformed = {"display_name": "No Slug", "visibility": "list", "context_window": 1000}
        payload = {"models": [dict(self.CACHE["models"][0]), malformed]}

        probe = self._probe(lambda path: json.dumps(payload))

        self.assertEqual([model.model_id for model in probe.models], ["gpt-5.6-sol"])

    def test_a_missing_cache_falls_back_to_the_audited_snapshot(self) -> None:
        def reader(path: Path) -> str:
            raise FileNotFoundError(f"no such file: {path}")

        probe = self._probe(reader)

        self.assertTrue(probe.available)
        self.assertIn("unreadable", probe.error or "")
        self.assertIn("gpt-5.6-sol", [model.model_id for model in probe.models])
        self.assertTrue(all(model.source == "audited" for model in probe.models))

    def test_a_corrupt_cache_falls_back_to_the_audited_snapshot(self) -> None:
        probe = self._probe(lambda path: "{not json")
        self.assertTrue(probe.available)
        self.assertIn("not valid JSON", probe.error or "")
        self.assertTrue(probe.models)

    def test_a_cache_that_is_not_an_object_falls_back(self) -> None:
        probe = self._probe(lambda path: "[]")
        self.assertIn("not a JSON object", probe.error or "")
        self.assertTrue(probe.models)

    def test_a_cache_entry_with_no_display_name_falls_back_to_the_audited_label(self) -> None:
        entry = dict(self.CACHE["models"][0])
        del entry["display_name"]
        probe = self._probe(lambda path: json.dumps({"models": [entry]}))
        sol = {model.model_id: model for model in probe.models}["gpt-5.6-sol"]
        self.assertEqual(sol.display_label, "Codex 5.6 Sol")

    def test_a_cache_entry_with_no_effort_levels_falls_back_to_the_provider_enum(self) -> None:
        entry = dict(self.CACHE["models"][0])
        del entry["supported_reasoning_levels"]
        probe = self._probe(lambda path: json.dumps({"models": [entry]}))
        sol = probe.models[0]
        codex_contract = probe_models.PROVIDER_CLI_CONTRACTS["codex_cli"]
        self.assertEqual(sol.supported_efforts, codex_contract.accepted_efforts)

    def test_a_non_int_context_window_is_dropped_rather_than_passed_through(self) -> None:
        entry = dict(self.CACHE["models"][0])
        entry["context_window"] = "272k"
        probe = self._probe(lambda path: json.dumps({"models": [entry]}))
        sol = probe.models[0]
        self.assertIsNone(sol.context_window)

    def test_a_cache_with_nothing_selectable_falls_back(self) -> None:
        hidden_only = {"models": [dict(self.CACHE["models"][1])]}
        probe = self._probe(lambda path: json.dumps(hidden_only))
        self.assertIn("no selectable models", probe.error or "")
        self.assertTrue(probe.models)

    def test_skipping_the_list_command_still_reads_the_cache(self) -> None:
        probe = probe_models.probe_cli_provider(
            "codex_cli",
            which=lambda name: "/usr/local/bin/codex",
            cache_reader=lambda path: json.dumps(self.CACHE),
            list_models=False,
        )
        self.assertEqual([model.source for model in probe.models], ["live"])


class SnapshotTests(unittest.TestCase):
    """The unified payload ticket 51's `GET /api/model-capabilities` serves."""

    @staticmethod
    def _snapshot() -> probe_models.CatalogSnapshot:
        def opener(url: str, timeout: float) -> io.BytesIO:
            return io.BytesIO(json.dumps({"data": [{"id": "qwen3.8-27b-mlx"}]}).encode("utf-8"))

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                argv, 0, stdout="gemini-3.7-flash-low\tGemini 3.7 Flash (Low)\n", stderr=""
            )

        def cache_reader(path: Path) -> str:
            # Injected so the suite never reads the developer's own
            # ~/.codex/models_cache.json — a machine with one and a machine
            # without would otherwise take different branches here.
            raise FileNotFoundError(f"no such file: {path}")

        return probe_models.probe_all(
            opener=opener,
            which=lambda name: f"/bin/{name}",
            runner=runner,
            cache_reader=cache_reader,
        )

    def test_snapshot_covers_every_provider(self) -> None:
        snapshot = self._snapshot()
        self.assertEqual({probe.provider_id for probe in snapshot.providers}, set(probe_models.PROVIDER_IDS))

    def test_snapshot_merges_live_and_audited_models_without_duplicates(self) -> None:
        snapshot = self._snapshot()
        model_ids = [model.model_id for model in snapshot.models()]
        self.assertEqual(len(model_ids), len(set(model_ids)))
        self.assertIn("qwen3.8-27b-mlx", model_ids)
        self.assertIn("gpt-5.6-sol", model_ids)

    @staticmethod
    def _shared_id_model(source: str) -> probe_models.ProbedModel:
        return probe_models.ProbedModel(
            model_id="shared-model",
            display_label=f"Shared Model ({source})",
            provider_id="antigravity_cli",
            supported_efforts=(),
            default_effort=None,
            context_window=None,
            local_only=False,
            source=source,  # type: ignore[arg-type]
        )

    def _probe_with(self, provider_id: str, source: str) -> probe_models.ProviderProbe:
        return probe_models.ProviderProbe(
            provider_id=provider_id,  # type: ignore[arg-type]
            available=True,
            binary_path="/bin/x",
            endpoint=None,
            models=(self._shared_id_model(source),),
            error=None,
        )

    def test_a_live_entry_beats_an_audited_one_seen_before_it(self) -> None:
        """The fixture-based tests above never have a model id appear twice,
        so `CatalogSnapshot.models()`'s live-beats-audited branch never runs
        for them — `if existing is None:` alone would pass every test in this
        class. Two hand-built probes that genuinely share a model id are what
        actually exercises the branch."""
        snapshot = probe_models.CatalogSnapshot(
            providers=(self._probe_with("antigravity_cli", "audited"), self._probe_with("codex_cli", "live"))
        )

        merged = {model.model_id: model for model in snapshot.models()}

        self.assertEqual(merged["shared-model"].source, "live")

    def test_two_live_entries_sharing_an_id_keep_the_first_one_seen(self) -> None:
        """Mutation check: dropping the `existing.source != "live"` half of
        `CatalogSnapshot.models()`'s guard — leaving `existing is None or
        model.source == "live"` — still resolves `.source` to `"live"`
        either way when both entries are live, so a test that only checks
        `.source` cannot catch it. Two live entries that share a model id but
        differ in another field are what actually distinguishes which one the
        merge kept."""
        first_live = self._shared_id_model("live")
        second_live = probe_models.ProbedModel(
            model_id="shared-model",
            display_label="Shared Model (second live)",
            provider_id="codex_cli",
            supported_efforts=(),
            default_effort=None,
            context_window=None,
            local_only=False,
            source="live",
        )
        snapshot = probe_models.CatalogSnapshot(
            providers=(
                probe_models.ProviderProbe(
                    provider_id="antigravity_cli",
                    available=True,
                    binary_path="/bin/agy",
                    endpoint=None,
                    models=(first_live,),
                    error=None,
                ),
                probe_models.ProviderProbe(
                    provider_id="codex_cli",
                    available=True,
                    binary_path="/bin/codex",
                    endpoint=None,
                    models=(second_live,),
                    error=None,
                ),
            )
        )

        merged = {model.model_id: model for model in snapshot.models()}

        self.assertEqual(merged["shared-model"].display_label, first_live.display_label)

    def test_a_live_entry_seen_first_is_not_overwritten_by_an_audited_one(self) -> None:
        snapshot = probe_models.CatalogSnapshot(
            providers=(self._probe_with("codex_cli", "live"), self._probe_with("antigravity_cli", "audited"))
        )

        merged = {model.model_id: model for model in snapshot.models()}

        self.assertEqual(merged["shared-model"].source, "live")

    def test_probed_model_to_dict_has_exactly_ticket_51s_key_set(self) -> None:
        """Ticket 51's `GET /api/model-capabilities` contract. Renaming e.g.
        `"source"` here is a silent breaking change to that payload that no
        other test in this module would catch."""
        model = probe_models.ProbedModel(
            model_id="x",
            display_label="X",
            provider_id="codex_cli",
            supported_efforts=("low",),
            default_effort="low",
            context_window=1000,
            local_only=False,
            source="audited",
        )
        self.assertEqual(
            set(model.to_dict()),
            {
                "display_label",
                "provider_id",
                "supported_efforts",
                "default_effort",
                "context_window",
                "local_only",
                "source",
            },
        )

    def test_provider_probe_to_dict_has_exactly_ticket_51s_key_set(self) -> None:
        """Renaming e.g. `"model_ids"` here is a silent breaking change to
        the same dashboard contract."""
        probe = probe_models.ProviderProbe(
            provider_id="codex_cli",
            available=True,
            binary_path="/bin/codex",
            endpoint=None,
            models=(),
            error=None,
        )
        self.assertEqual(
            set(probe.to_dict()),
            {"provider_id", "available", "binary_path", "endpoint", "error", "model_ids"},
        )

    def test_to_dict_is_json_serializable_and_keeps_capability_fields(self) -> None:
        """Golden Rule 5: `assertIn("providers", round_tripped)` used to pass
        even when `CatalogSnapshot.to_dict()`'s `providers` list (probe_
        models.py's `to_dict` methods) was replaced with hardcoded
        constants — it never checked the list's actual contents. This also
        pins `ProbedModel.to_dict()`'s `local_only`, `context_window`,
        `source`, and `display_label`, which were unasserted the same way
        (`provider_id`, `supported_efforts`, and `default_effort` were
        already pinned above)."""
        payload = self._snapshot().to_dict()
        round_tripped = json.loads(json.dumps(payload))
        sol = round_tripped["models"]["gpt-5.6-sol"]
        self.assertEqual(sol["provider_id"], "codex_cli")
        self.assertEqual(sol["supported_efforts"], ["low", "medium", "high", "xhigh", "max", "ultra"])
        self.assertEqual(sol["default_effort"], "low")
        # The codex cache reader in `_snapshot()` raises `FileNotFoundError`,
        # so this entry is the audited-snapshot fallback, not a live one.
        self.assertEqual(sol["source"], "audited")
        self.assertEqual(sol["context_window"], 272000)
        self.assertFalse(sol["local_only"])
        self.assertEqual(sol["display_label"], "Codex 5.6 Sol")

        providers = round_tripped["providers"]
        self.assertEqual(len(providers), 4)
        by_provider = {provider["provider_id"]: provider for provider in providers}
        self.assertEqual(set(by_provider), set(probe_models.PROVIDER_IDS))

        agy = by_provider["antigravity_cli"]
        self.assertTrue(agy["available"])
        self.assertEqual(agy["binary_path"], "/bin/agy")
        self.assertIsNone(agy["endpoint"])
        self.assertIsNone(agy["error"])
        self.assertEqual(agy["model_ids"], ["gemini-3.7-flash-low"])

        lm_studio = by_provider["lm_studio_local"]
        self.assertTrue(lm_studio["available"])
        self.assertIsNone(lm_studio["binary_path"])
        # Literal, not `probe_models.LM_STUDIO_MODELS_ENDPOINT`: that
        # constant is exactly what `probe_all`'s default `endpoint=` reaches
        # this field through, so comparing against it would be a tautology.
        self.assertEqual(lm_studio["endpoint"], "http://127.0.0.1:1234/v1/models")
        self.assertIsNone(lm_studio["error"])
        self.assertEqual(lm_studio["model_ids"], ["qwen3.8-27b-mlx"])


class ConfigDriftTests(unittest.TestCase):
    """Ticket 45 AC: the audit itself, made executable — what the checked-in
    routing config claims versus what the installed CLIs actually accept."""

    def test_a_provider_model_outside_the_audit_is_reported(self) -> None:
        config = routing_config.load_routing_config()
        findings = probe_models.audit_config_drift(config)
        kinds = {(finding.kind, finding.subject) for finding in findings}
        self.assertIn(("unknown_model", "providers.lm_studio_local"), kinds)

    def test_an_effort_the_provider_cli_cannot_accept_is_reported(self) -> None:
        findings = probe_models.audit_config_drift(
            self._config_with_provider("claude_opus_5", "claude-opus-5", "ultra")
        )
        matches = [
            finding
            for finding in findings
            if finding.kind == "unsupported_effort" and finding.subject == "providers.claude_opus_5"
        ]
        self.assertEqual(len(matches), 1)
        self.assertIn("ultra", matches[0].detail)

    def test_a_conforming_provider_produces_no_finding(self) -> None:
        findings = probe_models.audit_config_drift(
            self._config_with_provider("codex_sol", "gpt-5.6-sol", "high")
        )
        self.assertEqual(
            [finding for finding in findings if finding.subject == "providers.codex_sol"], []
        )

    def test_a_provider_with_a_mismatched_adapter_is_reported(self) -> None:
        findings = probe_models.audit_config_drift(
            self._config_with_provider(
                "wrong_adapter", "gpt-5.6-sol", "high", adapter="claude_code_cli"
            )
        )

        matches = [
            finding
            for finding in findings
            if finding.kind == "mismatched_provider" and finding.subject == "providers.wrong_adapter"
        ]
        self.assertEqual(len(matches), 1)
        self.assertIn("'codex_cli'", matches[0].detail)
        self.assertIn("'claude_code_cli'", matches[0].detail)

    def test_an_unknown_provider_adapter_does_not_crash_the_audit(self) -> None:
        findings = probe_models.audit_config_drift(
            self._config_with_provider(
                "unknown_adapter", "gpt-5.6-sol", "high", adapter="unknown_adapter"
            )
        )

        self.assertEqual(
            [
                (finding.kind, finding.subject)
                for finding in findings
                if finding.subject == "providers.unknown_adapter"
            ],
            [("mismatched_provider", "providers.unknown_adapter")],
        )

    def test_cross_provider_model_with_matching_override_produces_no_mismatched_provider(self) -> None:
        findings = probe_models.audit_config_drift(
            self._config_with_provider(
                "claude_sonnet_4_6", "claude-sonnet-4-6", "max", adapter="claude_code_cli"
            )
        )

        self.assertEqual(
            [
                finding
                for finding in findings
                if finding.subject == "providers.claude_sonnet_4_6"
            ],
            [],
        )

    def test_a_fallback_only_probe_does_not_count_as_a_live_replacement(self) -> None:
        """`_active_model_catalogs`' `source == "live"` check is what decides
        whether a probe is authoritative. Every fixture elsewhere in this
        file that exercises the live-replacement path uses an all-`"live"`
        probe, so that check has only ever been proven in its True direction.

        `claude_code_cli` publishes no list command, so `probe_cli_provider`
        degrades to `_cli_fallback` on *every* real run and returns the
        audited catalog with `source="audited"` throughout — never `"live"`.
        That fallback-shaped probe must be treated exactly like "no snapshot
        for this provider": it must not suppress the
        `_CROSS_PROVIDER_EFFORT_LADDERS` overlay that lets `claude_code_cli`
        accept `claude-sonnet-4-6` at `max` even though the model is audited
        under `antigravity_cli`. Mutating `source == "live"` to `True` makes
        this fallback probe look authoritative, which drops the overlay and
        turns this into a `mismatched_provider` finding instead."""
        findings = probe_models.audit_config_drift(
            self._config_with_provider(
                "claude_sonnet_4_6", "claude-sonnet-4-6", "max", adapter="claude_code_cli"
            ),
            snapshot=self._snapshot_with_claude_fallback_probe(),
        )

        self.assertEqual(
            [finding for finding in findings if finding.subject == "providers.claude_sonnet_4_6"],
            [],
        )

    def test_live_snapshot_models_reconcile_without_unknown_model_finding(self) -> None:
        snapshot = self._snapshot_with_live_model()
        findings = probe_models.audit_config_drift(
            self._config_with_provider("live_model", "live-model", "high", adapter="codex_cli"),
            snapshot=snapshot,
        )

        self.assertNotIn("unknown_model", [finding.kind for finding in findings])

    def test_live_snapshot_replaces_audited_catalog_for_live_probed_provider(self) -> None:
        findings = probe_models.audit_config_drift(
            self._config_with_provider("codex_sol", "gpt-5.6-sol", "high", adapter="codex_cli"),
            snapshot=self._snapshot_with_live_model(),
        )

        self.assertEqual(
            [
                (finding.kind, finding.subject)
                for finding in findings
                if finding.subject == "providers.codex_sol"
            ],
            [("unknown_model", "providers.codex_sol")],
        )

    def test_authoritative_live_omission_is_unknown_not_stale_provider_mismatch(self) -> None:
        findings = probe_models.audit_config_drift(
            self._config_with_provider(
                "omitted_model", "gpt-5.6-sol", "high", adapter="claude_code_cli"
            ),
            snapshot=self._snapshot_with_live_model(),
        )

        self.assertEqual(
            [
                (finding.kind, finding.subject)
                for finding in findings
                if finding.subject == "providers.omitted_model"
            ],
            [("unknown_model", "providers.omitted_model")],
        )

    def test_live_snapshot_resolves_display_labels_in_supported_models_and_fallbacks(self) -> None:
        base = routing_config.get_default_routing_config().to_dict()
        base["providers"] = {}
        base["supported_models"] = ["Live Model"]
        base["roster_topology"] = {"role_fallback_chains": {"planner": ["live model"]}}
        config = routing_config.parse_routing_config(base)

        findings = probe_models.audit_config_drift(config, snapshot=self._snapshot_with_live_model())

        self.assertEqual([finding for finding in findings if finding.kind == "unmapped_label"], [])

    def test_live_omission_reports_resolved_supported_and_fallback_labels_as_unmapped(self) -> None:
        """Resolution through the audited index is insufficient when the
        corresponding provider was live-probed and no longer publishes it."""
        base = routing_config.get_default_routing_config().to_dict()
        base["providers"] = {}
        base["supported_models"] = ["Claude Opus 5 (Thinking)"]
        base["roster_topology"] = {
            "role_fallback_chains": {"planner": ["Claude Opus 5 (Thinking)"]}
        }
        config = routing_config.parse_routing_config(base)
        snapshot = self._snapshot_with_live_claude_model()

        findings = probe_models.audit_config_drift(config, snapshot=snapshot)

        unmapped = [finding for finding in findings if finding.kind == "unmapped_label"]
        self.assertEqual(
            [(finding.subject, finding.detail) for finding in unmapped],
            [
                (
                    "roster_topology.planner[Claude Opus 5 (Thinking)]",
                    "label 'Claude Opus 5 (Thinking)' maps to no active wire identifier",
                ),
                (
                    "supported_models[Claude Opus 5 (Thinking)]",
                    "label 'Claude Opus 5 (Thinking)' maps to no active wire identifier",
                ),
            ],
        )

    def test_live_claude_catalog_is_not_changed_by_cross_provider_ladders(self) -> None:
        """A live listing is authoritative: overlays neither replace its
        ladder nor restore cross-provider models that it omitted."""
        snapshot = self._snapshot_with_live_claude_model(
            model_id="claude-sonnet-4-6", supported_efforts=("high",)
        )

        catalog = probe_models._active_model_catalogs(snapshot)["claude_code_cli"]

        self.assertEqual(catalog["claude-sonnet-4-6"].supported_efforts, ("high",))
        self.assertNotIn("claude-opus-5", catalog)

    def test_live_snapshot_checks_provider_ownership_and_effort_ladders(self) -> None:
        findings = probe_models.audit_config_drift(
            self._config_with_provider(
                "live_model", "Live Model", "ultra", adapter="claude_code_cli"
            ),
            snapshot=self._snapshot_with_live_model(),
        )

        self.assertEqual(
            {finding.kind for finding in findings if finding.subject == "providers.live_model"},
            {"mismatched_provider"},
        )

    def test_a_supported_models_label_with_no_wire_identifier_is_reported(self) -> None:
        config = routing_config.load_routing_config()
        findings = probe_models.audit_config_drift(config)
        unmapped = {finding.subject for finding in findings if finding.kind == "unmapped_label"}
        self.assertIn("supported_models[Gemini 3.7 Flash]", unmapped)

    def test_a_roster_topology_label_with_no_wire_identifier_is_reported(self) -> None:
        """Ticket 45 checkbox 4 ("map human-readable display labels to exact
        CLI wire identifiers") covers `roster_topology.role_fallback_chains`
        too — a chain entry reaches a provider's `--model` flag exactly the
        way a `supported_models` entry does. `critic_b`'s chain names the bare
        `"Gemini 3.6 Flash"`, which resolves to nothing: only the
        effort-suffixed spellings (`Gemini 3.6 Flash (High)`, etc.) are in the
        audited catalog."""
        config = routing_config.load_routing_config()
        findings = probe_models.audit_config_drift(config)
        unmapped = {finding.subject for finding in findings if finding.kind == "unmapped_label"}
        self.assertIn("roster_topology.critic_b[Gemini 3.6 Flash]", unmapped)

    def test_a_roster_topology_chain_of_only_valid_labels_produces_no_finding(self) -> None:
        base = routing_config.get_default_routing_config().to_dict()
        base["providers"] = {}
        base["supported_models"] = []
        base["roster_topology"] = {
            "role_fallback_chains": {"planner": ["Codex 5.6 Sol", "Claude Opus 5 (Thinking)"]}
        }
        config = routing_config.parse_routing_config(base)

        findings = probe_models.audit_config_drift(config)

        self.assertEqual([f for f in findings if f.subject.startswith("roster_topology.")], [])

    def test_a_repeated_unmapped_label_produces_one_finding_not_two(self) -> None:
        """`supported_models` repeating the same unmapped label must collapse
        to one finding — `dict.fromkeys(findings)` is what dedupes them."""
        base = routing_config.get_default_routing_config().to_dict()
        base["providers"] = {}
        base["supported_models"] = ["Not A Real Model", "Not A Real Model"]
        config = routing_config.parse_routing_config(base)

        findings = probe_models.audit_config_drift(config)

        matches = [f for f in findings if f.subject == "supported_models[Not A Real Model]"]
        self.assertEqual(len(matches), 1)

    def test_findings_are_sorted_by_kind_even_though_insertion_order_differs(self) -> None:
        """`unsupported_effort` findings are appended while walking
        `providers` — before the `supported_models` loop that appends
        `unmapped_label` findings — but `"unmapped_label"` sorts before
        `"unsupported_effort"` alphabetically. A config that yields one of
        each proves the result is actually sorted rather than just happening
        to already be in the right order."""
        config = self._config_with_provider("claude_opus_5", "claude-opus-5", "ultra")
        base = config.to_dict()
        base["supported_models"] = ["Not A Real Model"]
        config = routing_config.parse_routing_config(base)

        findings = probe_models.audit_config_drift(config)

        kinds = [finding.kind for finding in findings]
        self.assertIn("unsupported_effort", kinds)
        self.assertIn("unmapped_label", kinds)
        self.assertEqual(kinds.index("unmapped_label"), 0)
        self.assertEqual(kinds, sorted(kinds))

    def test_drift_findings_are_sorted_and_hashable(self) -> None:
        findings = probe_models.audit_config_drift(routing_config.load_routing_config())
        self.assertEqual(list(findings), sorted(findings, key=lambda f: (f.kind, f.subject)))
        self.assertEqual(len(set(findings)), len(findings))

    def test_the_checked_in_config_drift_is_pinned_to_the_audited_set(self) -> None:
        """The audit's regression guard. These six are the drift ticket 45
        found and deliberately did not fix — changing routing behavior belongs
        to the registry work in ticket 46. A seventh appearing here means a
        config edit introduced an identifier no installed provider accepts;
        one disappearing means it was fixed and this pin should shrink."""
        findings = probe_models.audit_config_drift(routing_config.load_routing_config())
        self.assertEqual(
            [(finding.kind, finding.subject) for finding in findings],
            [
                ("unknown_model", "providers.gemini_flash_high"),
                ("unknown_model", "providers.gemini_pro"),
                ("unknown_model", "providers.lm_studio_local"),
                ("unmapped_label", "roster_topology.critic_b[Gemini 3.6 Flash]"),
                ("unmapped_label", "supported_models[Gemini 3.7 Flash]"),
                ("unmapped_label", "supported_models[LM Studio (Local Model)]"),
            ],
        )

    @staticmethod
    def _config_with_provider(
        provider_id: str, model: str, effort: str, *, adapter: str | None = None
    ) -> routing_config.RoutingConfig:
        if adapter is None:
            audited = probe_models.AUDITED_MODEL_CATALOG.get(model)
            adapter = audited.provider_id if audited is not None else "claude_code_cli"
        base = routing_config.get_default_routing_config().to_dict()
        base["providers"] = {
            provider_id: {"adapter": adapter, "model": model, "default_reasoning_effort": effort}
        }
        base["supported_models"] = []
        return routing_config.parse_routing_config(base)

    @staticmethod
    def _snapshot_with_live_model() -> probe_models.CatalogSnapshot:
        return probe_models.CatalogSnapshot(
            providers=(
                probe_models.ProviderProbe(
                    provider_id="codex_cli",
                    available=True,
                    binary_path="/bin/codex",
                    endpoint=None,
                    models=(
                        probe_models.ProbedModel(
                            model_id="live-model",
                            display_label="Live Model",
                            provider_id="codex_cli",
                            supported_efforts=("low", "high"),
                            default_effort="high",
                            context_window=None,
                            local_only=False,
                            source="live",
                        ),
                    ),
                    error=None,
                ),
            )
        )

    @staticmethod
    def _snapshot_with_live_claude_model(
        *, model_id: str = "live-claude-model", supported_efforts: tuple[str, ...] = ("low", "high")
    ) -> probe_models.CatalogSnapshot:
        return probe_models.CatalogSnapshot(
            providers=(
                probe_models.ProviderProbe(
                    provider_id="claude_code_cli",
                    available=True,
                    binary_path="/bin/claude",
                    endpoint=None,
                    models=(
                        probe_models.ProbedModel(
                            model_id=model_id,
                            display_label="Live Claude Model",
                            provider_id="claude_code_cli",
                            supported_efforts=supported_efforts,
                            default_effort="high",
                            context_window=None,
                            local_only=False,
                            source="live",
                        ),
                    ),
                    error=None,
                ),
            )
        )

    @staticmethod
    def _snapshot_with_claude_fallback_probe() -> probe_models.CatalogSnapshot:
        """The exact shape `probe_cli_provider` returns for `claude_code_cli`
        on every real run: it has no list command, so it always degrades to
        `_cli_fallback`, whose models are the audited catalog with
        `source="audited"` — never `"live"`."""
        return probe_models.CatalogSnapshot(
            providers=(
                probe_models.ProviderProbe(
                    provider_id="claude_code_cli",
                    available=True,
                    binary_path="/bin/claude",
                    endpoint=None,
                    models=probe_models._audited_models_for("claude_code_cli"),
                    error=None,
                ),
            )
        )


class TextReportRenderingTests(unittest.TestCase):
    """`_render_text_report`'s own defensive fallbacks — exercised directly
    against a hand-built `CatalogSnapshot` rather than through `main()`,
    since none of the fixture data used elsewhere in this module produces a
    provider with neither a binary path nor an endpoint, or a model with
    neither supported efforts nor a default."""

    def test_falls_back_to_dashes_for_a_provider_with_no_location_and_a_model_with_no_efforts(self) -> None:
        snapshot = probe_models.CatalogSnapshot(
            providers=(
                probe_models.ProviderProbe(
                    provider_id="claude_code_cli",
                    available=False,
                    binary_path=None,
                    endpoint=None,
                    models=(
                        probe_models.ProbedModel(
                            model_id="mystery-model",
                            display_label="Mystery Model",
                            provider_id="claude_code_cli",
                            supported_efforts=(),
                            default_effort=None,
                            context_window=None,
                            local_only=False,
                            source="audited",
                        ),
                    ),
                    error=None,
                ),
            )
        )

        rendered = probe_models._render_text_report(snapshot)
        lines = rendered.splitlines()

        provider_line = next(line for line in lines if "claude_code_cli" in line)
        # `location = probe.binary_path or probe.endpoint or "-"` — neither is set.
        self.assertTrue(provider_line.rstrip().endswith("-"))
        model_line = next(line for line in lines if "mystery-model" in line)
        # `efforts = ",".join(...) or "-"` and `default = ... or "-"`.
        self.assertIn("efforts=-", model_line)
        self.assertIn("default=-", model_line)


class CliEntryPointTests(unittest.TestCase):
    """The `python3 probe_models.py` report — the shipped caller of every
    probe and of `audit_config_drift`."""

    def test_json_output_is_machine_readable(self) -> None:
        stream = io.StringIO()

        exit_code = probe_models.main(["--json"], stdout=stream,
            opener=self._online_opener,
            which=self._installed,
            runner=self._agy_listing,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["models"]["gpt-5.6-sol"]["provider_id"], "codex_cli")
        self.assertIn("qwen3.8-27b-mlx", payload["models"])

    def test_text_output_renders_a_row_per_provider_and_per_model(self) -> None:
        stream = io.StringIO()

        exit_code = probe_models.main([], stdout=stream,
            opener=self._online_opener,
            which=self._installed,
            runner=self._agy_listing,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 0)
        rendered = stream.getvalue()
        for provider_id in probe_models.PROVIDER_IDS:
            self.assertIn(provider_id, rendered)
        self.assertIn("gpt-5.6-sol", rendered)
        self.assertIn("low,medium,high,xhigh,max,ultra", rendered)
        self.assertIn("online", rendered)

    def test_an_offline_provider_is_reported_rather_than_omitted(self) -> None:
        stream = io.StringIO()

        exit_code = probe_models.main(
            [],
            stdout=stream,
            opener=self._offline_opener,
            which=lambda name: None,
            runner=self._unused_runner,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 0)
        rendered = stream.getvalue()
        self.assertIn("offline", rendered)
        self.assertIn("not installed", rendered)

    def test_fast_flag_reaches_probe_all_and_skips_the_list_command(self) -> None:
        """Spec 0013's non-blocking launch probe: `--fast` must reach
        `probe_all(list_models=False)`, which is otherwise unreachable from
        the CLI."""
        stream = io.StringIO()

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise AssertionError("--fast must not run agy's network-backed list command")

        exit_code = probe_models.main(
            ["--fast"],
            stdout=stream,
            opener=self._online_opener,
            which=self._installed,
            runner=runner,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("gpt-5.6-sol", stream.getvalue())

    def test_timeout_flag_reaches_probe_all(self) -> None:
        """`--timeout` (the LM Studio probe deadline) is otherwise
        unreachable from `main` even though `probe_all(timeout=...)`
        exists — distinct from `--cli-timeout`, which governs the CLI
        providers instead."""
        stream = io.StringIO()
        captured: dict[str, object] = {}

        def opener(url: str, timeout: float) -> io.BytesIO:
            captured["timeout"] = timeout
            return self._online_opener(url, timeout)

        exit_code = probe_models.main(
            ["--timeout", "3.5"],
            stdout=stream,
            opener=opener,
            which=self._installed,
            runner=self._agy_listing,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["timeout"], 3.5)

    def test_cli_timeout_flag_reaches_probe_all(self) -> None:
        """`--cli-timeout` is otherwise unreachable from `main` even though
        `probe_all(cli_timeout=...)` exists."""
        stream = io.StringIO()
        captured: dict[str, object] = {}

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured.update(kwargs)
            return self._agy_listing(argv, **kwargs)

        exit_code = probe_models.main(
            ["--cli-timeout", "42"],
            stdout=stream,
            opener=self._online_opener,
            which=self._installed,
            runner=runner,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["timeout"], 42.0)

    def test_audit_flag_reports_config_drift_and_exits_nonzero(self) -> None:
        stream = io.StringIO()

        exit_code = probe_models.main(
            ["--audit"],
            stdout=stream,
            config_loader=routing_config.load_routing_config,
            opener=self._online_opener,
            which=self._installed,
            runner=self._agy_listing,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("unmapped_label", stream.getvalue())

    def test_json_and_audit_together_include_drift_in_the_payload_and_exit_nonzero(self) -> None:
        stream = io.StringIO()

        exit_code = probe_models.main(
            ["--json", "--audit"],
            stdout=stream,
            config_loader=routing_config.load_routing_config,
            opener=self._online_opener,
            which=self._installed,
            runner=self._agy_listing,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stream.getvalue())
        self.assertIn("drift", payload)
        self.assertTrue(payload["drift"])
        kinds = {finding["kind"] for finding in payload["drift"]}
        self.assertIn("unmapped_label", kinds)
        for finding in payload["drift"]:
            self.assertEqual(set(finding), {"kind", "subject", "detail"})

    def test_json_without_audit_carries_no_drift_key(self) -> None:
        stream = io.StringIO()

        probe_models.main(
            ["--json"],
            stdout=stream,
            opener=self._online_opener,
            which=self._installed,
            runner=self._agy_listing,
            cache_reader=self._unused_cache_reader,
        )

        self.assertNotIn("drift", json.loads(stream.getvalue()))

    def test_json_and_audit_together_exit_zero_when_the_config_matches_the_catalog(self) -> None:
        stream = io.StringIO()

        exit_code = probe_models.main(
            ["--json", "--audit"],
            stdout=stream,
            config_loader=self._clean_config,
            opener=self._online_opener,
            which=self._installed,
            runner=self._agy_listing,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stream.getvalue())["drift"], [])

    def test_audit_flag_exits_zero_when_the_config_matches_the_catalog(self) -> None:
        stream = io.StringIO()

        exit_code = probe_models.main(
            ["--audit"],
            stdout=stream,
            config_loader=self._clean_config,
            opener=self._online_opener,
            which=self._installed,
            runner=self._agy_listing,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("none —", stream.getvalue())

    def test_main_passes_the_live_snapshot_to_audit_config_drift(self) -> None:
        """`main()`'s whole `--audit` payoff is that it hands `audit_config_drift`
        the *same* snapshot it just probed, not a fresh `snapshot=None` — that is
        what lets a config be checked against what providers publish *right now*
        rather than only the frozen audited catalog. No existing `--audit` test
        can tell the two apart: `_clean_config` names "Gemini 3.7 Flash (Low)",
        which `_agy_listing` also publishes live, so it resolves identically
        whether or not the snapshot reaches `audit_config_drift`.

        This config instead names "Gemini 3.1 Pro (High)" — audited under
        `antigravity_cli`, but *not* part of `_agy_listing`'s live output.
        `_active_model_catalogs` treats a provider's live listing as
        authoritative once one exists (see `_snapshot_with_live_model` and
        `_active_model_catalogs`'s docstring), so wiring the snapshot through
        correctly makes this model disappear from the active catalog and
        report `unknown_model`. Dropping `snapshot=snapshot` at the
        `audit_config_drift` call site inside `main()` instead leaves
        `audit_config_drift` building its catalogs from `snapshot=None`, which
        falls back to the full *audited* antigravity roster — where "Gemini
        3.1 Pro (High)" still resolves — and the finding silently disappears."""
        stream = io.StringIO()

        exit_code = probe_models.main(
            ["--json", "--audit"],
            stdout=stream,
            config_loader=self._stale_antigravity_provider_config,
            opener=self._online_opener,
            which=self._installed,
            runner=self._agy_listing,
            cache_reader=self._unused_cache_reader,
        )

        self.assertEqual(exit_code, 1)
        payload = json.loads(stream.getvalue())
        provider_findings = [
            (finding["kind"], finding["subject"])
            for finding in payload["drift"]
            if finding["subject"] == "providers.gemini_pro"
        ]
        self.assertEqual(provider_findings, [("unknown_model", "providers.gemini_pro")])

    @staticmethod
    def _stale_antigravity_provider_config() -> routing_config.RoutingConfig:
        base = routing_config.get_default_routing_config().to_dict()
        base["providers"] = {
            "gemini_pro": {
                "adapter": "antigravity_cli",
                "model": "Gemini 3.1 Pro (High)",
                "default_reasoning_effort": "high",
            }
        }
        base["supported_models"] = []
        return routing_config.parse_routing_config(base)

    @staticmethod
    def _clean_config() -> routing_config.RoutingConfig:
        base = routing_config.get_default_routing_config().to_dict()
        base["providers"] = {
            "codex_sol": {
                "adapter": "codex_cli",
                "model": "Codex 5.6 Sol",
                "default_reasoning_effort": "high",
            }
        }
        # `_agy_listing` is a live, authoritative antigravity catalog, so
        # this fixture must use its published model rather than the stale
        # audited Gemini 3.1 Pro entry.
        base["supported_models"] = ["Codex 5.6 Sol", "Gemini 3.7 Flash (Low)"]
        # The checked-in default `roster_topology.critic_b` chain names the
        # unmapped bare "Gemini 3.6 Flash" (see ConfigDriftTests), which would
        # make this "matches the catalog" fixture carry drift of its own.
        base["roster_topology"] = {
            "role_fallback_chains": {"planner": ["Codex 5.6 Sol", "Gemini 3.7 Flash (Low)"]}
        }
        return routing_config.parse_routing_config(base)

    @staticmethod
    def _online_opener(url: str, timeout: float) -> io.BytesIO:
        return io.BytesIO(json.dumps({"data": [{"id": "qwen3.8-27b-mlx"}]}).encode("utf-8"))

    @staticmethod
    def _installed(name: str) -> str:
        return f"/usr/local/bin/{name}"

    @staticmethod
    def _agy_listing(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 0, stdout="gemini-3.7-flash-low\tGemini 3.7 Flash (Low)\n", stderr=""
        )

    @staticmethod
    def _offline_opener(url: str, timeout: float) -> io.BytesIO:
        raise OSError("connection refused")

    @staticmethod
    def _unused_runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("no CLI binary is installed in this test")

    @staticmethod
    def _unused_cache_reader(path: Path) -> str:
        raise FileNotFoundError(f"no such file: {path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
