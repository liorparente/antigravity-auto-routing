# 0013 — Role & Model Configuration Matrix Dashboard Specification

* **Date:** 2026-08-24
* **Status:** Approved (`ready-for-agent`)
* **Design System Reference:** Google Stitch — Ethos Analytics (Project `15532077252071408109`, Screen `96f54277e6a048cdb0cc9e3cbbc8c9c0`)
* **Target Backlog Tickets:** Tickets 45–53 (`.scratch/routing-backlog/issues/`)

---

## Problem Statement

Currently, the Auto-Routing system's role assignments, model selections, and reasoning effort calibrations are defined statically in `skills/worker-routing/routing-config.json` and inspected via raw terminal logs or static HTML reports (`learning_report_html.py`). 

Developers and operators cannot visually inspect or adjust which model is assigned to a specific role (e.g. Planner, Heavy Builder, Council Critic, Security Reviewer), nor can they calibrate reasoning effort levels (`low`, `medium`, `high`, `ultra`) or fallback chains from a clean graphical interface. Crucially, **reasoning effort capabilities are heterogeneous across models**: models like `Codex Luna` or `LM Studio Local` support only `low` or `medium` effort, while frontier models like `Claude Opus 5` or `Codex Sol` support `high` and `ultra`. Presenting a generic effort list causes runtime CLI errors, invalid parameter invocations, and configuration drift.

---

## Solution

Build an interactive, executive-grade, RTL-native **Role & Model Configuration Matrix** into the Auto-Routing HTML Dashboard (`learning_report_html.py`), adhering to Google Stitch's **Ethos Analytics** design system with **Dynamic Model Capability Probing & Reactive Effort Binding**.

The solution provides:
1. **Dynamic Model Capability Registry:** A central capability schema mapping every model to its supported reasoning effort levels (`supported_efforts: ["low", "medium", ... ]`) and its factory default effort (`default_effort: "..."`).
2. **Live Capability Probing on Launch:** Automatic, non-blocking 200ms probe to LM Studio (`http://127.0.0.1:1234/v1/models`) and local CLI providers on dashboard launch to discover loaded models and dynamic capabilities.
3. **Reactive Effort Binding & Auto-Snap:** Selecting a model dynamically filters the effort dropdown to show *only* its supported reasoning levels, automatically snapping to the model's default effort if the previous effort is unsupported.
4. **Bento Grid Role Management:** Visual cards for all primary and granular sub-roles displaying capability requirements, context size, and reasoning tier.
5. **Robust State Machine & Floating Action Pill:** Live dirty tracking, single-click **Save Changes**, **Undo**, and **Reset to System Defaults**.
6. **Zero-Friction Hybrid Persistence:** Operates as a portable standalone HTML file with JSON export/copy capabilities, and seamlessly connects to a lightweight local server (`learning_report.py --serve`) for direct disk writes with fail-closed schema validation (`routing_config.py`).
7. **Granular Ticket Breakdown:** Decomposed into 8 bite-sized, atomic tickets suitable for execution by local models (LM Studio / Tier 0) with zero token waste.

---

## User Stories

1. As an engineer configuring the routing protocol, I want to view a top-level tab named "הגדרת תפקידים ומודלים" in the dashboard, so that I can easily navigate between performance metrics and model configurations.
2. As a developer, I want to see a Bento Grid of role cards (Planner, Heavy Builder, Light Builder, Critic, Adjudicator), so that I can understand the current model assigned to each core responsibility.
3. As a developer, I want a toggle between "תפקידי מפתח (ראשי)" and "פירוט מלא (מתקדם)", so that I can focus on primary roles without being overwhelmed by granular sub-reviewers, while retaining the ability to tune all 8+ sub-roles.
4. As an operator, I want to select any supported model from a dynamic dropdown on a role card, so that I can assign newly released frontier or local models as they become available.
5. As an engineer tuning cost and latency, I want the reasoning effort dropdown to show **only** the effort levels genuinely supported by the selected model (e.g. `low` only for Codex Luna; `high`/`ultra` for Opus 5; `low`/`medium`/`high` for Gemini 3.7 Flash).
6. As a user, I want the interface to auto-snap to the model's default reasoning effort if I switch to a model that does not support my previous effort selection, so that invalid configurations are prevented before submission.
7. As an operator, I want the reasoning effort badge on each card to dynamically update its color (Green for Low, Blue for Medium, Purple for High, Amber for Ultra), so that I have instant visual feedback on cognitive resource allocation.
8. As a developer, I want a "🔄 רענן מודלים חיים" button that probes local LM Studio (`http://127.0.0.1:1234/v1/models`) on demand, so that newly loaded local LLMs appear immediately in the interface.
9. As an operator, I want to configure a fallback provider chain for each role, so that tasks degrade gracefully to local or cheap models if a primary cloud provider is unavailable.
10. As a developer, I want a glassmorphic floating action bar to slide into view whenever I modify a role configuration, so that I know exactly how many uncommitted changes exist.
11. As a developer, I want a "בטל פעולה (Undo)" button on the floating action bar, so that I can immediately revert accidental dropdown selections.
12. As an operator, I want an "אפס לברירת מחדל (Reset to Default)" button with a confirmation safeguard, so that I can safely restore factory routing presets at any time.
13. As a user running in standalone mode, I want a collapsible live JSON drawer at the bottom of the screen with a "📋 העתק קונפיגורציה" button, so that I can export the valid `routing-config.json` payload directly to my clipboard or download it.
14. As a developer running the local server (`learning_report.py --serve`), I want the "שמור שינויים" button to send an atomic POST request to update `routing-config.json` on disk, so that changes take effect immediately in the active workspace.
15. As a platform maintainer, I want all saved payloads to pass through `routing_config.parse_routing_config` fail-closed schema validation, so that malformed configurations can never corrupt the project.
16. As an RTL user, I want all typography, layout alignments, cards, and animations to follow natural Hebrew right-to-left flow using the Rubik typeface, so that the user experience is native and seamless.

---

## Implementation Decisions

### 1. Model Capability Registry & Dynamic Probing
* **Registry Schema (`routing_config.py`):**
  Defines `ModelCapability(supported_efforts: tuple[str, ...], default_effort: str, tier: str, context: int, local_only: bool)` for every supported model.
* **Probing Protocol (`GET /api/model-capabilities`):**
  - Probes `127.0.0.1:1234/v1/models` (timeout 200ms) for active LM Studio models.
  - Queries registered CLI providers and returns a unified capability payload to the frontend.

### 2. Architecture & Design System
* **Design Theme:** Google Stitch **Ethos Analytics** (Light Mode, slate-50 canvas, crisp white bento cards, #0f172a primary headers, #2563eb interactive blues, Rubik typography for Hebrew, JetBrains Mono for metrics and JSON).
* **Component Architecture:** Pure vanilla JS and Tailwind CSS embedded in `learning_report_html.py` — zero build step, zero external runtime dependencies.
* **Layout Structure:**
  * Two-tab top navigation: `Tab 1: מדדי ביצוע ולמידה` and `Tab 2: הגדרת תפקידים ומודלים`.
  * Mode segmented switch: `simple` (5 primary roles) vs `all` (all 9 roles including security, architecture, maintainability reviewers, and sensitive executor).
  * Floating action bar: Sticky glassmorphic pill pinned at bottom with dirty state pulse indicator.
  * Collapsible JSON state drawer: Fixed bottom drawer with syntax-highlighted live preview.

### 3. Reactive Effort Binding & Auto-Snap
* When a model selection event fires:
  1. Frontend retrieves `MODEL_CAPABILITIES[newModel]`.
  2. If `currentEffort` is not contained in `supported_efforts`, sets `effort = default_effort`.
  3. Re-renders the effort `<select>` options containing only valid choices.
  4. Updates effort color badge and emits an informative toast notification.

### 4. State Model & Persistence
* **State Container:** Client-side reactive state tracking `currentRoles`, `savedSnapshot`, and an `undoHistory` stack.
* **Serialization Contract:** Maps UI role state directly to the authoritative `routing_config.RoleConfig` and `ProviderConfig` schema shapes.
* **Hybrid Server Protocol:**
  * In standalone mode (`file://`), "שמור שינויים" updates local snapshot, triggers a download/copy action, and displays a success toast.
  * In server mode (`http://localhost:8080`), "שמור שינויים" dispatches `fetch('/api/config', { method: 'POST', body: JSON.stringify(payload) })`.

### 5. Server-Side Validation (`learning_report.py`)
* Implements a lightweight `http.server` handler in `learning_report.py` activated via `--serve [PORT]`.
* Validates incoming JSON via `routing_config.parse_routing_config(..., fallback_on_missing=True)`.
* Writes to `routing-config.json` using atomic temporary file swap (`os.replace`).

---

## Testing Decisions

* **Test Seams:**
  1. `skills/worker-routing/test_learning_report_html.py`:
     - Test HTML generation contains the Role Configuration Matrix markup and tab navigation.
     - Test all dynamic role IDs and model capability definitions are properly rendered and escaped.
     - Test dirty bar, undo button, reset button, and live JSON inspector elements exist.
  2. `skills/worker-routing/test_learning_report.py`:
     - Test `--serve` argument parsing and API endpoint validation (`POST /api/config` and `GET /api/model-capabilities`).
  3. `skills/worker-routing/test_routing_config.py`:
     - Test model capability lookups and roundtrip serialization of modified role structures through `parse_routing_config` and `to_dict()`.

---

## Out of Scope

* Dynamic runtime model downloading (LM Studio model pulling is managed out-of-band).
* Modifying API keys or environment secrets from the web dashboard.
* Cloud multi-tenant authentication (dashboard is strictly local to the developer's workstation).

---

## Further Notes

* Work is partitioned into Tickets 45–53 to enable atomic execution by local LM Studio worker models (T0). Ticket 53 serves as the prerequisite model catalog alignment audit.
