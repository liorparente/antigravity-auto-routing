# 51 — Local Dashboard Server & Atomic Save API

**What to build:** An embedded lightweight HTTP server in `learning_report.py` (`--serve [PORT]`) providing a `POST /api/config` endpoint that validates and atomically saves updated configurations directly to disk.

**Blocked by:** 50 — Live JSON Drawer & Clipboard Export

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** ready-for-agent

- [ ] Add `--serve [PORT]` flag to `skills/worker-routing/learning_report.py` using standard `http.server`.
- [ ] Implement `POST /api/config` handler receiving updated JSON payloads.
- [ ] Pass payload through `routing_config.parse_routing_config` fail-closed validator.
- [ ] Write to `skills/worker-routing/routing-config.json` via atomic temporary file swap (`os.replace`).
- [ ] Return 200 OK or 400 Bad Request with error details.
