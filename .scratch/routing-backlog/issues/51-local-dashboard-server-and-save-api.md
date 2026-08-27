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
HTTPServer` to a `_ConfigApiHandler` that answers exactly `POST
/api/config`: read the body, `json.loads` it, validate through
`routing_config.parse_routing_config(payload, fallback_on_missing=True)`,
and on success reuse `learning_report._atomic_text_write` (the same
temp-file-then-`os.replace` helper the Markdown report already used) to
persist `config.to_dict()` to disk. Malformed JSON, a schema violation, or
an unknown path return `400`/`404` with a JSON `{"error": ...}` body and
write nothing; a valid payload returns `200 {"status": "ok"}`. `--serve`
(default port 8080) is the only CLI flag that does not require `--now`, since
it never renders a report — `main` now checks for that flag before enforcing
`--now`.

**One scope boundary, deliberately not crossed.** The save path validates
and writes whatever full `RoutingConfig`-shaped JSON it is given; it does
not translate the role cards' reduced `{roles: {role_id: {model, effort}}}`
preview (tickets 48/50) into a valid `RoleConfig`/`ProviderConfig` payload,
and nothing yet POSTs to this endpoint from the page itself. Ticket 50's own
note anticipated this ticket would "own validating a payload through
`routing_config.parse_routing_config`" — it does, exactly that, and no
more; reconciling the reduced client shape into a full config, and wiring
the "שמור שינויים" button to call this endpoint in server mode, is
unticketed follow-up work, not something this ticket's own checklist asked
for.

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
points at "the local-server work" (this ticket) as where it belongs. Added
it scoped conservatively: it serves `routing_config.
build_model_capabilities_registry()`'s current in-process registry, keyed
`{provider}::{model_id}` like the dashboard's own `<option>` values, with
all five `ModelCapability` fields the spec names. It does not re-probe LM
Studio or any CLI provider live on each request — Spec §1's "Live
Capability Probing on Launch" and user story 8's "🔄 רענן מודלים חיים"
button both describe a live re-probe as something a page action triggers,
and no such action exists in the dashboard yet, so wiring `probe_models.
probe_all` through this endpoint would be speculative work nothing today
would exercise.