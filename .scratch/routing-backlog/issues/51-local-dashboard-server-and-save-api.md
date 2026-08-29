# 51 — Local Dashboard Server & Atomic Save API

**GitHub Issue:** [#27](https://github.com/liorparente/antigravity-auto-routing/issues/27)

**What to build:** An embedded lightweight HTTP server in `learning_report.py` (`--serve [PORT]`) providing a `POST /api/config` endpoint that validates and atomically saves updated configurations directly to disk.

**Blocked by:** 50 — Live JSON Drawer & Clipboard Export

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** done

- [x] Add `--serve [PORT]` flag to `skills/worker-routing/learning_report.py` using standard `http.server`.
- [x] Implement `POST /api/config` handler receiving updated JSON payloads.
- [x] Pass payload through `routing_config.parse_routing_config` fail-closed validator.
- [x] Write to `skills/worker-routing/routing-config.json` via atomic temporary file swap (`os.replace`).
- [x] Return 200 OK or 400 Bad Request with error details.

## Delivered

`create_dashboard_server`/`serve_dashboard` bind a stdlib `http.server.
HTTPServer` to a `_ConfigApiHandler`. As shipped by *this* ticket it
answered two routes — `POST /api/config` and `GET /api/model-capabilities`,
the latter named by spec 0013 §1 though absent from this ticket's own
checklist (ticket 53 later added a third, `GET /` — see the note below).
`POST /api/config`: read the body, `json.loads` it, validate through
`routing_config.parse_routing_config(payload, fallback_on_missing=True)`,
and on success reuse `learning_report._atomic_text_write` (the same
temp-file-then-`os.replace` helper the Markdown report already used) to
persist `config.to_dict()` to disk. Malformed JSON, a schema violation, or
an unknown path return `400`/`404` with a JSON `{"error": ...}` body and
write nothing; a valid payload returns `200 {"status": "ok"}`. `--serve`
(default port 8080) is the only CLI flag that does not require `--now`, since
it never renders a report — `main` now checks for that flag before enforcing
`--now`.

**One scope boundary, deliberately not crossed *by this ticket*.** The save
path validates and writes whatever full `RoutingConfig`-shaped JSON it is
given; it did not translate the role cards' reduced `{roles: {role_id:
{model, effort}}}` preview (tickets 48/50) into a valid
`RoleConfig`/`ProviderConfig` payload, and at the time this ticket closed
nothing POSTed to the endpoint from the page itself. Ticket 50's own note
anticipated this ticket would "own validating a payload through
`routing_config.parse_routing_config`" — it does, exactly that, and no more.

> **Superseded by ticket 53.** That follow-up landed: `buildFullConfigPayload`
> now reconciles the reduced client shape into a full config, "שמור שינויים"
> POSTs it in server mode, and `GET /` was added because without it the page
> could only be opened over `file://` — where `isServerMode()` is false, which
> had left this endpoint unreachable from the dashboard in practice. Ticket 53
> added exactly one route to the two above, bringing the handler to three:
> `GET /`, `POST /api/config`, and `GET /api/model-capabilities`.

Tested with real HTTP round trips (`http.client.HTTPConnection` against a
server bound to an OS-assigned port), not direct calls into handler
internals: a valid full-config payload saves atomically and leaves no
stray temp file; a malformed JSON body, a schema-invalid payload (the
reduced role/effort shape above, used deliberately as the schema-violation
fixture), and an unknown path all return their documented status and write
nothing to disk.

**One addition beyond the checklist above, added after review.** This
ticket's own checklist never lists `GET /api/model-capabilities`, but Spec
0013 §1 names that endpoint and its own Testing Decisions (§5) group it
with `--serve` explicitly — "Test `--serve` argument parsing and API
endpoint validation (`POST /api/config` and `GET /api/model-capabilities`)"
— and `learning_report_html._dashboard_config_json`'s docstring already
points at "the local-server work" (this ticket) as where it belongs.

**Revised after a second review pass.** The first cut of this endpoint
served the static `build_model_capabilities_registry()` and deliberately
skipped live re-probing as out of scope. A second Spec review caught that
this was wrong, not merely conservative: `probe_models.probe_all` and
`probe_models.CatalogSnapshot.to_dict` were built by ticket 45 *for this
exact endpoint* — their own docstrings say so verbatim ("Pass
`list_models=False` for spec 0013's launch probe"; "Shaped by `to_dict`
into the capability payload spec 0013's dashboard reads — but it is a
plain value object, not an HTTP concern"). `GET /api/model-capabilities`
now calls `probe_models.probe_all(list_models=False)` — `_ConfigApiServer`
takes an injectable `capability_snapshot` callable (mirroring the existing
injectable `config_path`) so tests exercise this without depending on
which CLIs happen to be installed on the machine running them.
`list_models=False` keeps every provider probe local and fast (a 200ms
socket probe to LM Studio, a `PATH` lookup plus a local cache read per CLI
provider, no subprocess ever run) — the same non-blocking launch probe
spec §1 describes, not a slow one merely relabeled for this route.

**Revised again, a third review pass.** Returning `CatalogSnapshot.
to_dict()` verbatim turned out to reintroduce finding F7 itself: its
`"models"` map is deduplicated by bare `model_id` across providers, so two
CLI adapters offering the same model id with genuinely different effort
ladders (`probe_models._CROSS_PROVIDER_EFFORT_LADDERS` records a real one:
`claude-sonnet-4-6` is `low/medium/high` under `antigravity_cli` but adds
`max` under `claude_code_cli`) would silently collapse to one entry, and
the payload carried no `tier` field at all despite spec §1's Registry
Schema naming it. `do_GET` now walks `snapshot.providers` itself, keys
each entry `{provider_id}::{model_id}` (the dashboard's own key shape,
never collapsing across providers), and cross-references
`routing_config.build_model_capabilities_registry()` for `tier` — `null`
for a live-probed pair the audited catalog doesn't know yet, mirroring
`RoleModelBinding.capability`'s existing "`None` is a known drift state,
not an error" contract rather than inventing a new one.