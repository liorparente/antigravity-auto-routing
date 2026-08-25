# Live Model Catalog & CLI Provider Capability Audit

* **Date:** 2026-08-25
* **Ticket:** 45 — Live Model Catalog & CLI Provider Capability Audit ([#21](https://github.com/liorparente/antigravity-auto-routing/issues/21))
* **Spec:** [0013 — Role & Model Configuration Matrix Dashboard](../specs/0013-role-and-model-matrix-dashboard.md)
* **Executable form:** [`skills/worker-routing/probe_models.py`](../../skills/worker-routing/probe_models.py)

## Why this audit exists

Before it, "which models can we call, and at which reasoning efforts?" had three
disagreeing answers in this repo — the human labels in `routing-config.json`'s
`supported_models`, the `MODEL_ALIASES` / `CODEX_MODELS` / `AGY_MODELS` tables in
`production_invoker.py`, and the prose matrix in `CLAUDE.md`. None was derived
from an installed CLI. Spec 0013's reactive effort binding cannot be built on
top of that: an effort dropdown is only safe if the effort ladders it renders
are the ones the providers actually parse.

Everything below was read off the installed toolchain, not from documentation
or memory. Each catalog entry in `probe_models.py` carries its provenance in an
`evidence` field.

## Environment audited

| Provider | Binary | Version | How the catalog was obtained |
|---|---|---|---|
| Claude Code | `claude` | 2.1.241 | `claude --help`; effort enum and model identifiers read from the shipped binary |
| Codex | `codex` | codex-cli 0.144.1 | `~/.codex/models_cache.json` — the catalog the CLI fetched from the API (`supported_reasoning_levels` per model) |
| Antigravity | `agy` | 1.1.20 | `agy models`, `agy --help` |
| LM Studio | — (HTTP) | — | `GET http://127.0.0.1:1234/v1/models` |

## 1. Wire CLI flags and reasoning-effort parameters

Encoded as `probe_models.PROVIDER_CLI_CONTRACTS`; `CliContract.format_argv()`
produces the argv fragment and refuses an effort the provider cannot parse.

| Provider | Model flag | Effort parameter | Effort enum the CLI accepts |
|---|---|---|---|
| `claude_code_cli` | `--model <id>` | `--effort <level>` | `low`, `medium`, `high`, `xhigh`, `max` |
| `codex_cli` | `--model <slug>` | `-c model_reasoning_effort="<level>"` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `antigravity_cli` | `--model <id>` | `--effort <level>` | `low`, `medium`, `high` |
| `lm_studio_local` | HTTP `model` field | *(none)* | — |

Notes:

* `claude --effort` also accepts the alias `med` → `medium`, and silently
  accepts `ultracode` → `xhigh` too — `claude --help` lists only the
  five-value enum (`low`, `medium`, `high`, `xhigh`, `max`), so this alias is
  undocumented there, not rejected as an invalid `--effort` value.
* `claude --model` additionally accepts the latest-model aliases `fable`, `opus`,
  and `sonnet`. They are deliberately **not** in `DISPLAY_LABEL_TO_MODEL_ID`:
  each resolves to whatever is latest at call time, so pinning one to a wire
  identifier would encode a claim that silently expires.
* `codex` takes its effort as a TOML config override, not a flag — a difference
  the routing protocol's command templates already get right.
* `agy` publishes only three rungs, and most of its model identifiers already
  encode the effort as a suffix (see §2), so `--model` and `--effort` overlap.
* LM Studio's OpenAI-compatible API has no reasoning-effort parameter at all.

## 2. Model identifiers actually published

Full table in `probe_models.AUDITED_MODEL_CATALOG`. Highlights:

**Codex** (`default_reasoning_level` and `supported_reasoning_levels` are the
vendor's own fields):

| Slug | Default effort | Supported efforts | Context |
|---|---|---|---|
| `gpt-5.6-sol` | `low` | low, medium, high, xhigh, max, **ultra** | 272,000 |
| `gpt-5.6-terra` | `medium` | low, medium, high, xhigh, max, **ultra** | 272,000 |
| `gpt-5.6-luna` | `medium` | low, medium, high, xhigh, max | 272,000 |
| `gpt-5.5` / `gpt-5.4` / `gpt-5.4-mini` | `medium` | low, medium, high, xhigh | 272,000 |
| `gpt-5.3-codex-spark` | `high` | low, medium, high, xhigh | 128,000 |

> **Read the cache, not the binary.** The catalog *embedded* in the `codex`
> executable disagrees with the one the CLI actually uses: it still advertises
> a 372,000-token window for the 5.6 family and a `gpt-5.2` that the live
> catalog has replaced with Codex Spark. This audit's first draft transcribed
> the embedded copy and was wrong on both counts. `probe_cli_provider` now
> reads `~/.codex/models_cache.json` directly, so the snapshot below is a
> fallback rather than the source of truth. `codex-auto-review` is in the cache
> at `visibility: "hide"` and is filtered out: real, but not assignable.

**Antigravity** — `agy models` returns `<id>\t<label>` pairs whose identifiers
bake the effort in: `gemini-3.7-flash-{high,medium,low}`,
`gemini-3.6-flash-{high,medium,low}`, `gemini-3.5-flash-{high,medium,low}`,
`gemini-3.1-pro-{high,low}`, `gpt-oss-120b-medium`, plus two agy-hosted Claude
models (`claude-sonnet-4-6`, `claude-opus-4-6-thinking`). **Gemini 3.1 Pro has
no `medium` rung** — exactly the heterogeneity spec 0013 was written for.

**Claude Code** publishes no list command, but the installed binary
(`~/.local/share/claude/versions/2.1.241`) carries its own model catalog, and
`default_effort` and `context.window` are read directly from it rather than
inferred:

| Model | Default effort | Context window |
|---|---|---|
| `claude-opus-5` | `high` | 1,000,000 |
| `claude-sonnet-5` | `high` | 1,000,000 |
| `claude-fable-5` | `high` | 1,000,000 |
| `claude-3-7-sonnet` | *(none published)* | 200,000 |

The audited entries therefore cover the models this project routes —
`claude-opus-5`, `claude-sonnet-5`, `claude-fable-5`, and the pre-effort
`claude-3-7-sonnet` — and record the CLI-level effort enum rather than a
per-model snapshot that would go stale silently. The binary documents `xhigh`
as available on *Fable 5, Opus 4.7+, Sonnet 5* and `max` on *Fable 5, Opus
4.6+, Sonnet 4.6+*. The `default_effort: "high"` on the three Claude 5 entries
is a value the catalog states literally per model, not the CLI's own
`?? "high"` fallback: that fallback exists in the binary for a model whose
catalog entry omits a default, but it never fires for these three —
`claude-3-7-sonnet` is the entry that actually omits one, and it is recorded
as `None` rather than defaulted to `"high"`.

`agy models` is a **network** call (it prints "Fetching available models…"
first). Spec 0013 asks for a non-blocking launch probe, so
`probe_cli_provider(..., list_models=False)` skips the listing while leaving the
local cache and snapshot paths intact.

**LM Studio** is inherently dynamic. At audit time it served `qwen3.8-27b-mlx`
and `gemma-4-e4b-it-mlx` (plus two embedding models the probe filters out).
Those two are recorded only so the offline dashboard has something truthful to
render; `probe_lm_studio()` is the authority at runtime.

## 3. Findings

**F1 — `ultra` is not a universal effort level.** The project's canonical
vocabulary (`learning_journal.VALID_EFFORTS`) is `low|medium|high|ultra`, and
`CLAUDE.md` invites `effort: ultra` in any routing declaration. But `ultra` is
accepted **only** by `codex` with Sol or Terra. `claude --effort ultra` and
`agy --effort ultra` are both CLI errors, and `gpt-5.6-luna` tops out at `max`.
Conversely the project vocabulary has no name for `xhigh` or `max`, which three
of the four providers do accept.

**F2 — `agy` model identifiers in the config are unroutable.** `routing-config.json`
configures `gemini_flash_high` → `gemini-3.6-flash` and `gemini_pro` →
`gemini-3.1-pro`. Neither string appears in `agy models`; the real identifiers
carry an effort suffix. This is latent rather than live only because
`production_invoker.build_worker_command()` currently drops the resolved model
and effort for the `agy` family and emits a bare `["agy", "-p", prompt]`.

**F3 — `gpt-oss-120b` is an Antigravity model, not a Codex one.**
`production_invoker.CODEX_MODELS` includes it, so a role resolving to it would
be dispatched to `codex --model gpt-oss-120b`. `agy models` lists it as
`gpt-oss-120b-medium`; the codex catalog does not contain it at all.

**F4 — the configured local model is not loaded.** `providers.lm_studio_local`
names `qwen3-coder-30b`; LM Studio serves `qwen3.8-27b-mlx` and
`gemma-4-e4b-it-mlx`. `roster_topology` separately names
`Qwen3.8-27B-MLX-6bit` and `Gemma 4 E4B`, neither of which is a served
identifier either (both are carried as aliases in the audited catalog).

**F5 — three labels are not models.** `supported_models` names `Gemini 3.7
Flash`, which has no bare identifier (only the three effort-suffixed ones),
and `LM Studio (Local Model)`, a placeholder for whatever is loaded.
`roster_topology.role_fallback_chains.critic_b` separately names the bare
`Gemini 3.6 Flash` — the same shape of drift, in the fallback chains rather
than in `supported_models`: `resolve_model_id("Gemini 3.6 Flash")` raises
`UnknownModelError` exactly as the other two do.

**F6 — local models have no effort ladder.** `providers.lm_studio_local`
declares `default_reasoning_effort: "medium"`, but the OpenAI-compatible API
exposes no such parameter. Spec 0013's effort dropdown should be hidden, not
defaulted, for `local_only` models.

**F7 — a flat model-id keying cannot express a model two providers both
publish.** `agy models` lists `claude-sonnet-4-6`, and the `claude` binary's
own catalog also carries it — with a *different*, longer effort ladder
(`low`, `medium`, `high`, `max` vs. `agy`'s `low`, `medium`, `high`; the
binary's `xhigh` availability list names "Sonnet 5", not "Sonnet 4.6", so
`xhigh` is not part of the `claude`-side ladder for this model either).
`AUDITED_MODEL_CATALOG` is keyed by `model_id` alone, so it can only hold one
entry per identifier; it holds the `antigravity_cli` entry. Before this round,
`CliContract.format_argv` consulted that single audited entry regardless of
which provider was asking, so `PROVIDER_CLI_CONTRACTS["claude_code_cli"]
.format_argv("claude-sonnet-4-6", "max")` raised `UnsupportedEffortError`
even though the `claude` CLI itself accepts `max` for that model — the
audited entry made the claude path strictly *worse* than an unaudited one.
The narrow fix applied here: `format_argv` now consults the audited ladder
only when `audited.provider_id == self.provider_id`; otherwise it falls back
to the provider-wide `accepted_efforts` union, exactly as it already does for
a model the audit does not list at all — *except* for `claude-sonnet-4-6` on
`claude_code_cli` specifically, where `_CROSS_PROVIDER_EFFORT_LADDERS` now
narrows that union to exclude `xhigh`: the whole-provider enum contains
`xhigh`, but this model does not carry it on the `claude` side, and a
config-drift round found `format_argv` waving that exact pairing through
before this narrow, explicit correction (a data-level patch, not a re-keying
of `AUDITED_MODEL_CATALOG`). This is latent rather than live
today — `routing-config.json` configures no agy-hosted Claude model, so no
config currently drives `claude_code_cli.format_argv("claude-sonnet-4-6",
...)` — but the fix closes the gap before the registry work reopens it.
Re-keying `AUDITED_MODEL_CATALOG` to `(provider_id, model_id)` so both
providers' entries for this model can coexist is deliberately **not** done
here: the registry schema is ticket 46's to design, and `_build_label_index`
raising on a duplicate id is load-bearing today (`test_build_label_index_
raises_on_a_genuine_label_collision`). Ticket 46 should decide the keying
when it builds the capability registry this module currently has no caller
for.

## 4. What this ticket changed, and what it deliberately did not

Delivered: the audited catalog, the CLI wire contracts, the label → identifier
mapping, and the live probe — plus `audit_config_drift()`, which turns **F2, F4
and F5** into assertions rather than prose, walking both `supported_models` and
`roster_topology.role_fallback_chains` for the `unmapped_label` check.
`test_probe_models.py` pins the resulting six findings exactly, so a config
edit that adds a seventh fails CI and one that fixes an existing finding forces
the pin to shrink deliberately.

F1, F3 and F6 are *not* machine-checked, and deliberately so: F1 and F3 are
facts about `learning_journal.py` and `production_invoker.py` rather than about
the config, and F6 cannot fire while F4 stands — `providers.lm_studio_local`
names a model that fails to resolve at all, so the drift audit reports
`unknown_model` and never reaches the effort check. Fixing F4 will surface F6.

F7 is also not machine-checked, and for a similar reason: it is a fact about
`AUDITED_MODEL_CATALOG`'s keying (a registry-design question) rather than
about `routing-config.json`, and it is latent — no configured provider
currently names an agy-hosted Claude model — so `audit_config_drift()` has
nothing to observe yet. Unlike F1–F6, this round did apply a narrow code fix
for the consequence F7 describes (`CliContract.format_argv` no longer
misattributes another provider's audited ladder to itself); the underlying
single-key schema is unchanged and is called out explicitly as ticket 46's to
decide.

Not changed: `routing-config.json`, `production_invoker.py`, the effort
vocabulary, and `AUDITED_MODEL_CATALOG`'s single-key-per-model-id schema.
Correcting F1–F4 alters which model a role actually dispatches to, which
belongs with ticket 46's capability registry and its consumers — not with the
audit that found them. Re-keying the catalog to admit two providers
publishing the same model id is the same kind of registry-design decision,
and belongs there too (see F7).

**No production caller yet** (Golden Rule 20). Outside its own CLI entry point
and tests, nothing imports this module: ticket 46 is where
`resolve_model_id` / `AUDITED_MODEL_CATALOG` acquire a consumer, and it is
declared *blocked by 45* precisely for that reason. Until then the model
knowledge in `production_invoker.MODEL_ALIASES` / `CODEX_MODELS` / `AGY_MODELS`
stays in place and this module duplicates part of it — a known, temporary cost
of auditing before rewiring rather than during it.

## 5. Refreshing this audit

```bash
python3 skills/worker-routing/probe_models.py --audit
```

Reports live provider status, the merged live/audited model list, and current
config drift; exits non-zero when any drift exists. Use `--json` for the
payload spec 0013's `GET /api/model-capabilities` serves.

Two more flags tune the probe itself, both passed through to `probe_all()`:

* `--fast` — skip `agy models`'s network-backed listing (spec 0013's
  non-blocking launch probe); every CLI provider stays local, at the cost of
  `agy`'s live listing degrading to the audited snapshot.
* `--cli-timeout SECONDS` — the deadline for a CLI provider's list command
  (default `CLI_PROBE_TIMEOUT_SECONDS`, 15s). `--timeout SECONDS` is the
  separate, much shorter deadline for the LM Studio HTTP probe (default
  `DEFAULT_PROBE_TIMEOUT_SECONDS`, 0.2s).
