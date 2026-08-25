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


class DisplayLabelMappingTests(unittest.TestCase):
    """Ticket 45 AC: "Map human-readable display labels to exact CLI wire
    identifiers"."""

    def test_resolve_accepts_the_canonical_display_label(self) -> None:
        self.assertEqual(probe_models.resolve_model_id("Codex 5.6 Sol"), "gpt-5.6-sol")
        self.assertEqual(probe_models.resolve_model_id("Claude Opus 5 (Thinking)"), "claude-opus-5")

    def test_resolve_accepts_a_wire_identifier_unchanged(self) -> None:
        self.assertEqual(probe_models.resolve_model_id("gpt-5.6-sol"), "gpt-5.6-sol")

    def test_resolve_accepts_a_vendor_alias(self) -> None:
        self.assertEqual(probe_models.resolve_model_id("GPT-5.6-Sol"), "gpt-5.6-sol")

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

        probe = probe_models.probe_lm_studio(opener=opener, timeout=0.2)

        self.assertTrue(probe.available)
        self.assertEqual(captured["url"], probe_models.LM_STUDIO_MODELS_ENDPOINT)
        self.assertEqual(captured["timeout"], 0.2)
        self.assertEqual([model.model_id for model in probe.models], ["qwen3.8-27b-mlx", "gemma-4-e4b-it-mlx"])
        self.assertTrue(all(model.source == "live" for model in probe.models))
        self.assertIsNone(probe.error)

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

    def test_a_model_agy_lists_but_the_audit_missed_is_still_reported(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, stdout=self.AGY_MODELS_STDOUT, stderr="")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        discovered = {model.model_id: model for model in probe.models}["brand-new-model-high"]
        self.assertEqual(discovered.display_label, "Brand New Model (High)")
        self.assertEqual(discovered.supported_efforts, ("high",))

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
        self.assertIn("claude-opus-5", [model.model_id for model in probe.models])

    def test_a_failing_list_command_falls_back_to_the_audit_and_records_the_error(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="agy: not logged in")

        probe = probe_models.probe_cli_provider(
            "antigravity_cli", which=lambda name: "/usr/local/bin/agy", runner=runner
        )

        self.assertTrue(probe.available)
        self.assertIn("not logged in", probe.error or "")
        self.assertTrue(all(model.source == "audited" for model in probe.models))

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

    def test_lm_studio_is_rejected_with_the_probe_it_should_have_used(self) -> None:
        with self.assertRaises(probe_models.ModelCatalogError) as caught:
            probe_models.probe_cli_provider("lm_studio_local")
        self.assertIn("probe_lm_studio", str(caught.exception))


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

        self.assertEqual(seen, [probe_models.CODEX_MODELS_CACHE_PATH])
        sol = {model.model_id: model for model in probe.models}["gpt-5.6-sol"]
        self.assertEqual(sol.source, "live")
        self.assertEqual(sol.supported_efforts, ("low", "ultra"))
        self.assertEqual(sol.default_effort, "low")
        self.assertEqual(sol.context_window, 272000)

    def test_models_the_cli_hides_are_not_offered_as_assignable(self) -> None:
        probe = self._probe(lambda path: json.dumps(self.CACHE))
        self.assertNotIn("codex-auto-review", [model.model_id for model in probe.models])

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

    def test_live_entries_win_over_audited_ones_for_the_same_id(self) -> None:
        snapshot = self._snapshot()
        by_id = {model.model_id: model for model in snapshot.models()}
        self.assertEqual(by_id["qwen3.8-27b-mlx"].source, "live")

    def test_to_dict_is_json_serializable_and_keeps_capability_fields(self) -> None:
        payload = self._snapshot().to_dict()
        round_tripped = json.loads(json.dumps(payload))
        sol = round_tripped["models"]["gpt-5.6-sol"]
        self.assertEqual(sol["provider_id"], "codex_cli")
        self.assertEqual(sol["supported_efforts"], ["low", "medium", "high", "xhigh", "max", "ultra"])
        self.assertEqual(sol["default_effort"], "low")
        self.assertIn("providers", round_tripped)


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

    def test_a_supported_models_label_with_no_wire_identifier_is_reported(self) -> None:
        config = routing_config.load_routing_config()
        findings = probe_models.audit_config_drift(config)
        unmapped = {finding.subject for finding in findings if finding.kind == "unmapped_label"}
        self.assertIn("supported_models[Gemini 3.7 Flash]", unmapped)

    def test_drift_findings_are_sorted_and_hashable(self) -> None:
        findings = probe_models.audit_config_drift(routing_config.load_routing_config())
        self.assertEqual(list(findings), sorted(findings, key=lambda f: (f.kind, f.subject)))
        self.assertEqual(len(set(findings)), len(findings))

    def test_the_checked_in_config_drift_is_pinned_to_the_audited_set(self) -> None:
        """The audit's regression guard. These five are the drift ticket 45
        found and deliberately did not fix — changing routing behavior belongs
        to the registry work in ticket 46. A sixth appearing here means a
        config edit introduced an identifier no installed provider accepts;
        one disappearing means it was fixed and this pin should shrink."""
        findings = probe_models.audit_config_drift(routing_config.load_routing_config())
        self.assertEqual(
            [(finding.kind, finding.subject) for finding in findings],
            [
                ("unknown_model", "providers.gemini_flash_high"),
                ("unknown_model", "providers.gemini_pro"),
                ("unknown_model", "providers.lm_studio_local"),
                ("unmapped_label", "supported_models[Gemini 3.7 Flash]"),
                ("unmapped_label", "supported_models[LM Studio (Local Model)]"),
            ],
        )

    @staticmethod
    def _config_with_provider(provider_id: str, model: str, effort: str) -> routing_config.RoutingConfig:
        base = routing_config.get_default_routing_config().to_dict()
        base["providers"] = {
            provider_id: {"adapter": "x", "model": model, "default_reasoning_effort": effort}
        }
        base["supported_models"] = []
        return routing_config.parse_routing_config(base)


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
        base["supported_models"] = ["Codex 5.6 Sol", "Gemini 3.1 Pro (Low)"]
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
