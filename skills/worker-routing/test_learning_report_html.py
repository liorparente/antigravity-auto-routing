#!/usr/bin/env python3
"""Unit tests for `learning_report_html` (ticket 44).

`_find_forbidden_clock_reads` is imported by name from
`test_learning_scoreboard` — same convention `test_learning_report.py`
already uses: that function is pure (`ast.AST` in, a plain list of tuples
out) and touches neither `learning_journal` nor `learning_scoreboard`.
"""
from __future__ import annotations

import ast
import itertools
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import learning_journal, learning_report_html, learning_scoreboard, routing_config
    from .test_learning_scoreboard import _find_forbidden_clock_reads
else:
    import learning_journal  # type: ignore[no-redef]
    import learning_report_html  # type: ignore[no-redef]
    import learning_scoreboard  # type: ignore[no-redef]
    import routing_config  # type: ignore[no-redef]
    from test_learning_scoreboard import _find_forbidden_clock_reads  # type: ignore[no-redef]

LEARNING_REPORT_HTML_PATH = Path(__file__).with_name("learning_report_html.py")

# A shared, timezone-aware `now` for every test below — never used to derive
# a live clock reading, only as a fixed injected value. Window bounds for the
# default 7-day window: current window is (2026-01-01, 2026-01-08], baseline
# window is (2025-12-25, 2026-01-01].
_NOW = datetime(2026, 1, 8, tzinfo=timezone.utc)


def _worker_execution_record(
    task_id: str,
    *,
    timestamp: str,
    cost: float = 0.0,
    success: bool = True,
    model_family: str = "claude",
    model_id: str = "claude-sonnet-5",
    run_id: str | None = None,
) -> Any:
    return learning_journal.WorkerExecutionRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        duration_ms=100,
        cost_estimate_usd=cost,
        success=success,
        retry_count=0,
        effort="low",
        model_id=model_id,
        model_family=model_family,
        run_id=run_id,
        timestamp=timestamp,
    )


def _outcome_record(
    task_id: str,
    *,
    ground_truth: str,
    verdict: str,
    timestamp: str,
    run_id: str | None = None,
) -> Any:
    return learning_journal.OutcomeRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        ground_truth=ground_truth,  # type: ignore[arg-type]
        verdict=verdict,  # type: ignore[arg-type]
        run_id=run_id,
        timestamp=timestamp,
    )


def _dialogue_record(
    task_id: str,
    *,
    rounds: tuple[Any, ...],
    timestamp: str,
    canaries_planted: int = 0,
    canaries_caught: int = 0,
    degraded: bool = False,
    occasion: str = "ambiguity",
    topology: str = "pair",
) -> Any:
    return learning_journal.DialogueQualityRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        occasion=occasion,  # type: ignore[arg-type]
        topology=topology,  # type: ignore[arg-type]
        rounds=rounds,
        canaries_planted=canaries_planted,
        canaries_caught=canaries_caught,
        degraded=degraded,
        timestamp=timestamp,
    )


def _compliance_record(
    session_id: str,
    *,
    violation_count: int,
    timestamp: str,
    session_last_activity: str | None,
    issue_codes: tuple[str, ...] = (),
    run_id: str | None = None,
) -> Any:
    return learning_journal.ComplianceRecord(
        session_id=session_id,
        total_writes=1,
        code_writes=0,
        routing_declarations=1,
        worker_calls=0,
        violation_count=violation_count,
        declaration_drift_count=0,
        calibration_markers=0,
        code_write_count=0,
        issue_codes=issue_codes,
        run_id=run_id,
        session_last_activity=session_last_activity,
        timestamp=timestamp,
    )


_SCRIPT_OPENER_RE = re.compile(r"<script[^>]*>")
_JSON_BLOCK_RE = re.compile(
    r'<script type="application/json" id="dashboard-config">(.*?)</script>', re.DOTALL
)
_EXECUTABLE_SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.DOTALL)
_EFFORT_SELECT_RE = re.compile(
    r'<select class="effort-select"[^>]*>(.*?)</select>', re.DOTALL
)
_OPTION_VALUE_RE = re.compile(r'<option value="([^"]*)"')


# --- color math, for the badge legibility guard ---
#
# Small, standard, and local to this file: WCAG 2.x relative luminance and
# contrast, plus a CIE76 ΔE over a D65 sRGB→Lab conversion. Written out
# rather than pulled in, because this repo ships zero runtime dependencies
# (`pyproject.toml`'s `dependencies = []`) and one assertion does not earn
# the first one.


def _channels(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _linearize(channel: int) -> float:
    ratio = channel / 255
    return ratio / 12.92 if ratio <= 0.03928 else ((ratio + 0.055) / 1.055) ** 2.4


def _relative_luminance(color: str) -> float:
    red, green, blue = (_linearize(channel) for channel in _channels(color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _tint_over_white(color: str) -> str:
    """The background a badge makes for itself: this color at 10% (the `1a`
    alpha suffix in `_role_controls_html`) composited over the white card.
    """
    alpha = 0x1A / 0xFF
    blended = (round(channel * alpha + 255 * (1 - alpha)) for channel in _channels(color))
    return "#" + "".join(f"{channel:02x}" for channel in blended)


def _contrast_ratio(foreground: str, background: str) -> float:
    first, second = _relative_luminance(foreground), _relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _to_lab(color: str) -> tuple[float, float, float]:
    def pivot(value: float) -> float:
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    red, green, blue = (_linearize(channel) for channel in _channels(color))
    x = pivot((0.4124 * red + 0.3576 * green + 0.1805 * blue) / 0.95047)
    y = pivot(0.2126 * red + 0.7152 * green + 0.0722 * blue)
    z = pivot((0.0193 * red + 0.1192 * green + 0.9505 * blue) / 1.08883)
    return (116 * y - 16, 500 * (x - y), 200 * (y - z))


def _color_distance(first: str, second: str) -> float:
    """CIE76 ΔE. Below ~10 two colors stop being tellable apart at a glance."""
    return math.dist(_to_lab(first), _to_lab(second))


def _script_openers(report: str) -> list[str]:
    return _SCRIPT_OPENER_RE.findall(report)


def _dashboard_config(report: str) -> Any:
    match = _JSON_BLOCK_RE.search(report)
    assert match is not None, "no dashboard-config JSON block in the rendered report"
    return json.loads(match.group(1))


def _capabilities_payload(report: str) -> Any:
    return _dashboard_config(report)["capabilities"]


def _executable_script(report: str) -> str:
    match = _EXECUTABLE_SCRIPT_RE.search(report)
    assert match is not None, "no executable script block in the rendered report"
    return match.group(1)


def _effort_option_values(report: str) -> list[str]:
    match = _EFFORT_SELECT_RE.search(report)
    assert match is not None, "no effort select in the rendered report"
    return _OPTION_VALUE_RE.findall(match.group(1))


# --- running the embedded JavaScript for real (ticket 48) ---

_ROLE_CARD_RE = re.compile(
    r'<div class="role-card" data-role-id="([^"]+)".*?'
    r'<div class="role-card-bindings">',
    re.DOTALL,
)
_MODEL_SELECT_RE = re.compile(r'<select class="model-select"[^>]*>(.*?)</select>', re.DOTALL)
_SELECTED_OPTION_RE = re.compile(r'<option value="([^"]*)" selected>')
_BADGE_RE = re.compile(r'<span class="effort-badge"[^>]*>([^<]*)</span>')

# The DOM surface the embedded script actually touches, and nothing else —
# narrow enough to stub honestly in a few dozen lines, which is why the
# script is written against exactly these calls. A selector or tag the stub
# does not know about raises rather than returning a permissive `null`, so
# widening the script's DOM usage fails these tests loudly instead of
# silently exercising a stub that no longer resembles a browser.
#
# One known way this is laxer than a browser: `makeSelect`'s `value` is a
# plain property, so it accepts a string matching no `<option>`, where
# `HTMLSelectElement.value` would silently fall back to `""`. That is safe
# only while every assignment writes a value belonging to the option set
# written in the same call — which `resolveEffortState` guarantees in all
# three of its statuses, and `test_reselecting_the_rendered_model_
# reproduces_the_rendered_card` checks against the real rendered markup.
# A future handler that sets `.value` independently of the options would
# pass here and fail in a browser, so tighten this stub if that appears.
_DOM_STUB_JS = """
function makeOption() {
  return { value: "", textContent: "", selected: false };
}

function makeSelect(value) {
  return {
    value: value,
    disabled: false,
    children: [],
    listeners: [],
    get firstChild() {
      return this.children.length ? this.children[0] : null;
    },
    removeChild: function (child) {
      this.children = this.children.filter(function (each) {
        return each !== child;
      });
    },
    appendChild: function (child) {
      this.children.push(child);
    },
    addEventListener: function (name, handler) {
      this.listeners.push([name, handler]);
    }
  };
}

function makeCard(spec) {
  var modelSelect = makeSelect(spec.model);
  var effortSelect = makeSelect(spec.effort);
  effortSelect.disabled = spec.disabled;
  var badge = { textContent: spec.badge, style: { background: "", color: "" } };
  return {
    getAttribute: function (name) {
      if (name !== "data-role-id") {
        throw new Error("unsupported attribute " + name);
      }
      return spec.roleId;
    },
    querySelector: function (selector) {
      if (selector === ".model-select") return modelSelect;
      if (selector === ".effort-select") return effortSelect;
      if (selector === ".effort-badge") return badge;
      throw new Error("unsupported selector " + selector);
    }
  };
}

var CARD_NODES = CARD_SPECS.map(makeCard);

// A generic stand-in for the floating action pill and toast elements
// (ticket 49) — none of these are selects, so `makeSelect` does not fit,
// but all of them need `classList`, `children`, and `addEventListener` the
// same narrow way `makeSelect` needs `value` and `disabled`.
function makeElement() {
  var classes = [];
  return {
    className: "",
    textContent: "",
    innerHTML: "",
    style: {},
    children: [],
    listeners: [],
    classList: {
      add: function (name) {
        if (classes.indexOf(name) === -1) {
          classes.push(name);
        }
      },
      remove: function (name) {
        classes = classes.filter(function (each) {
          return each !== name;
        });
      },
      contains: function (name) {
        return classes.indexOf(name) !== -1;
      },
      // Mirrors real `DOMTokenList.toggle`: adds and returns `true` when
      // absent, removes and returns `false` when present.
      toggle: function (name) {
        if (classes.indexOf(name) === -1) {
          classes.push(name);
          return true;
        }
        classes = classes.filter(function (each) {
          return each !== name;
        });
        return false;
      }
    },
    appendChild: function (child) {
      this.children.push(child);
    },
    removeChild: function (child) {
      this.children = this.children.filter(function (each) {
        return each !== child;
      });
    },
    addEventListener: function (name, handler) {
      this.listeners.push([name, handler]);
    }
  };
}

var ACTION_PILL = makeElement();
var ACTION_PILL_LABEL = makeElement();
var ACTION_UNDO_BUTTON = makeElement();
var ACTION_RESET_BUTTON = makeElement();
var ACTION_SAVE_BUTTON = makeElement();
var TOAST_CONTAINER = makeElement();

// The live JSON drawer's markup (ticket 50) — `CONFIG_DRAWER` is the only
// one of these `updateConfigDrawer`/`toggleConfigDrawer` address by
// `classList`/`textContent` rather than `addEventListener`, but it still
// needs `makeElement`'s full shape since `toggleConfigDrawer` reads
// `classList.contains` before deciding which of `add`/`remove` to call.
var CONFIG_DRAWER = makeElement();
var CONFIG_DRAWER_TOGGLE = makeElement();
var CONFIG_DRAWER_COPY = makeElement();
var CONFIG_DRAWER_JSON = makeElement();

var ELEMENTS_BY_ID = {
  "dashboard-config": { textContent: DASHBOARD_CONFIG_JSON },
  "action-pill": ACTION_PILL,
  "action-pill-label": ACTION_PILL_LABEL,
  "action-undo": ACTION_UNDO_BUTTON,
  "action-reset": ACTION_RESET_BUTTON,
  "action-save": ACTION_SAVE_BUTTON,
  "toast-container": TOAST_CONTAINER,
  "config-drawer": CONFIG_DRAWER,
  "config-drawer-toggle": CONFIG_DRAWER_TOGGLE,
  "config-drawer-copy": CONFIG_DRAWER_COPY,
  "config-drawer-json": CONFIG_DRAWER_JSON
};

// Controllable from a test harness the same way `CONFIRM_RESULT` controls
// `confirm()`: `CLIPBOARD_SHOULD_FAIL` flips which callback
// `copyConfigToClipboard`'s `.then(...)` reaches, and `CLIPBOARD_WRITES`
// records every string actually handed to `writeText`, so a test can
// assert on the exact payload copied, not just that copying happened.
// `navigator.clipboard.writeText` genuinely returns a `Promise` in a
// browser, but this stub's `.then` calls its callback synchronously —
// deliberately not a real `Promise` — so a harness snippet can call
// `copyConfigToClipboard()` and immediately assert the resulting toast
// without needing to flush a microtask queue first. Production code only
// ever calls `.then(onFulfilled, onRejected)` once and never chains
// further, so this narrower shape is enough.
var CLIPBOARD_WRITES = [];
var CLIPBOARD_SHOULD_FAIL = false;
var navigator = {
  clipboard: {
    writeText: function (text) {
      CLIPBOARD_WRITES.push(text);
      var failed = CLIPBOARD_SHOULD_FAIL;
      return {
        then: function (onFulfilled, onRejected) {
          if (failed) {
            if (onRejected) onRejected(new Error("denied"));
          } else if (onFulfilled) {
            onFulfilled();
          }
          return this;
        }
      };
    }
  }
};

// Controllable from a test harness by assigning `CONFIRM_RESULT` before
// calling `resetDefaults()`; `CONFIRM_MESSAGES` records every prompt shown,
// so a test can assert the pill actually asked before acting.
var CONFIRM_RESULT = true;
var CONFIRM_MESSAGES = [];
function confirm(message) {
  CONFIRM_MESSAGES.push(message);
  return CONFIRM_RESULT;
}

var document = {
  getElementById: function (id) {
    if (!Object.prototype.hasOwnProperty.call(ELEMENTS_BY_ID, id)) {
      throw new Error("unsupported id " + id);
    }
    return ELEMENTS_BY_ID[id];
  },
  querySelectorAll: function (selector) {
    if (selector !== ".role-card") {
      throw new Error("unsupported selector " + selector);
    }
    return CARD_NODES;
  },
  createElement: function (tag) {
    if (tag === "option") {
      return makeOption();
    }
    if (tag === "div") {
      return makeElement();
    }
    throw new Error("unsupported tag " + tag);
  }
};

function snapshot(roleId) {
  return CARD_NODES.filter(function (card) {
    return card.getAttribute("data-role-id") === roleId;
  }).map(function (card) {
    var effortSelect = card.querySelector(".effort-select");
    var badge = card.querySelector(".effort-badge");
    return {
      model: card.querySelector(".model-select").value,
      effort: effortSelect.value,
      options: effortSelect.children.map(function (option) {
        return option.value;
      }),
      disabled: effortSelect.disabled,
      badgeText: badge.textContent,
      badgeColor: badge.style.color,
      badgeBackground: badge.style.background
    };
  });
}

function fireChange(roleId, which, value) {
  var card = CARD_NODES.filter(function (each) {
    return each.getAttribute("data-role-id") === roleId;
  })[0];
  var select = card.querySelector(which);
  select.value = value;
  select.listeners.forEach(function (entry) {
    entry[1]();
  });
}

function fireClick(elementId) {
  ELEMENTS_BY_ID[elementId].listeners.forEach(function (entry) {
    if (entry[0] === "click") {
      entry[1]();
    }
  });
}

// `updateConfigDrawer` writes highlighted `innerHTML`, not `textContent`
// (ticket 50) — every fixture value under test here is a plain role id,
// model key, or effort word, none of which ever contains `<`/`>`, so
// stripping tags back out of the highlighted markup recovers exactly the
// JSON text `syntaxHighlightJson` started from, with no HTML-entity
// unescaping needed.
function drawerPlainText() {
  return CONFIG_DRAWER_JSON.innerHTML.replace(/<[^>]+>/g, "");
}
"""


def _node_binary() -> str:
    """Locate `node`, failing loudly when it is absent.

    Deliberately not a `skipUnless`: the assertions below are the only ones
    in this file that execute the embedded JavaScript at all, so quietly
    skipping them would leave the report's entire reactive layer covered by
    nothing but substring checks over its source text — a suite that stays
    green while the behavior it names is never run once.
    """
    node = shutil.which("node")
    if node is None:
        raise RuntimeError(
            "node is required to execute the dashboard's embedded JavaScript; "
            "these tests verify behavior, not source text, and must not be skipped"
        )
    return node


def _role_card_specs(report: str) -> list[dict[str, Any]]:
    """The rendered initial state of every role card, as the fixture the DOM
    stub is built from — so the JavaScript runs against what the renderer
    actually emitted, not against a hand-written approximation of it.
    """
    specs = []
    for chunk in report.split('<div class="role-card" data-role-id="')[1:]:
        role_id = chunk.split('"', 1)[0]
        model_block = _MODEL_SELECT_RE.search(chunk)
        effort_block = _EFFORT_SELECT_RE.search(chunk)
        badge = _BADGE_RE.search(chunk)
        assert model_block and effort_block and badge, f"incomplete controls for {role_id!r}"
        model_selected = _SELECTED_OPTION_RE.search(model_block.group(1))
        effort_selected = _SELECTED_OPTION_RE.search(effort_block.group(1))
        specs.append(
            {
                "roleId": role_id,
                "model": model_selected.group(1) if model_selected else "",
                "effort": effort_selected.group(1) if effort_selected else "",
                "disabled": "disabled" in effort_block.group(0).split(">", 1)[0],
                "badge": badge.group(1),
            }
        )
    return specs


def _run_embedded_script(report: str, harness: str) -> Any:
    """Run the report's own `<script>` body under node against a stubbed DOM
    built from that same report's rendered cards, then run `harness` and
    parse whatever it prints as JSON.
    """
    source = "\n".join(
        [
            f"var DASHBOARD_CONFIG_JSON = {json.dumps(json.dumps(_dashboard_config(report)))};",
            f"var CARD_SPECS = {json.dumps(_role_card_specs(report))};",
            _DOM_STUB_JS,
            _executable_script(report),
            harness,
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "harness.js"
        script_path.write_text(source, encoding="utf-8")
        completed = subprocess.run(
            [_node_binary(), str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    assert completed.returncode == 0, f"node failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


def _boards(journal: learning_journal.JournalRead, *, now: datetime, window_days: int = 7) -> Any:
    board = learning_scoreboard.compute_scoreboard(journal, now=now, window_days=window_days)
    baseline_board = learning_scoreboard.compute_scoreboard(
        journal, now=now - timedelta(days=window_days), window_days=window_days
    )
    return board, baseline_board


# --- AST guard: no live clock ---


class NoClockTests(unittest.TestCase):
    def test_the_html_report_module_reads_no_clock(self) -> None:
        tree = ast.parse(LEARNING_REPORT_HTML_PATH.read_text(encoding="utf-8"))

        self.assertEqual(_find_forbidden_clock_reads(tree), [])


# --- Pure contract: render_html_report ---


class RenderHtmlReportSkeletonTests(unittest.TestCase):
    def test_empty_journal_renders_without_crashing_and_carries_expected_sections(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW
        )

        self.assertTrue(report.startswith("<!DOCTYPE html>"))
        self.assertIn('dir="rtl"', report)
        self.assertIn("Light Mode", report)
        self.assertIn("Rubik", report)
        self.assertIn("<html", report)
        self.assertIn("</html>", report)
        for name in (
            "violations_per_session",
            "canary_catch_rate",
            "mean_engagement_count",
            "escalation_rate",
            "dialogue_non_consensus_rate",
            "mean_rework_per_task",
            "cost_per_completed_task_usd",
            "mean_benchmark_score",
            "first_pass_yield",
            "total_cost_usd",
            "cost_savings_usd",
            "token_savings",
        ):
            self.assertIn(name, report)

    def test_refuses_a_naive_now(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        with self.assertRaises(ValueError):
            learning_report_html.render_html_report(
                journal, board, baseline_board, now=datetime(2026, 1, 8)  # noqa: DTZ001
            )

    def test_refuses_a_non_positive_window_days(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        with self.assertRaises(ValueError):
            learning_report_html.render_html_report(
                journal, board, baseline_board, now=_NOW, window_days=0
            )

    def test_refuses_a_board_computed_with_a_different_window_days(self) -> None:
        journal = learning_journal.JournalRead()
        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW, window_days=7)
        mismatched_baseline = learning_scoreboard.compute_scoreboard(
            journal, now=_NOW - timedelta(days=14), window_days=14
        )

        with self.assertRaises(ValueError):
            learning_report_html.render_html_report(
                journal, board, mismatched_baseline, now=_NOW, window_days=7
            )

    def test_refuses_a_baseline_board_not_aligned_to_one_window_before_now(self) -> None:
        journal = learning_journal.JournalRead()
        board = learning_scoreboard.compute_scoreboard(journal, now=_NOW, window_days=7)
        # Baseline computed at the wrong `now` (should be _NOW - 7 days).
        wrong_baseline = learning_scoreboard.compute_scoreboard(
            journal, now=_NOW - timedelta(days=3), window_days=7
        )

        with self.assertRaises(ValueError):
            learning_report_html.render_html_report(
                journal, board, wrong_baseline, now=_NOW, window_days=7
            )


# --- Dynamic metrics: cost & savings ---


class CostAndSavingsTests(unittest.TestCase):
    def test_total_windowed_cost_sums_every_execution_regardless_of_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                _worker_execution_record("task-a", timestamp="2026-01-03T00:00:00Z", cost=2.0),
                _worker_execution_record("task-b", timestamp="2026-01-04T00:00:00Z", cost=3.5),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("5.5", report)

    def test_cost_savings_is_no_data_when_baseline_has_no_completed_task(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("cost_savings_usd", report)
        self.assertIn("no data", report)

    def test_cost_savings_is_positive_when_current_costs_less_per_task_than_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                # Baseline window: one completed task costing 10.0.
                _worker_execution_record(
                    "task-base", timestamp="2025-12-27T00:00:00Z", cost=10.0, run_id="run-base"
                ),
                _outcome_record(
                    "task-base",
                    ground_truth="tests",
                    verdict="pass",
                    timestamp="2025-12-28T00:00:00Z",
                    run_id="run-base",
                ),
                # Current window: one completed task costing 2.0.
                _worker_execution_record(
                    "task-cur", timestamp="2026-01-03T00:00:00Z", cost=2.0, run_id="run-cur"
                ),
                _outcome_record(
                    "task-cur",
                    ground_truth="tests",
                    verdict="pass",
                    timestamp="2026-01-04T00:00:00Z",
                    run_id="run-cur",
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        # Hypothetical (baseline rate 10.0 * 1 completed task) - actual (2.0) = 8.0.
        self.assertIn("cost_savings_usd", report)
        self.assertIn(f'dir="ltr">{learning_report_html._format_value(8.0)} (n=1)<', report)


# --- Dynamic metrics: FPY & rework ---


class FirstPassYieldTests(unittest.TestCase):
    def test_a_task_with_no_rework_is_full_yield(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_execution_record(
                "task-single", timestamp="2026-01-03T00:00:00Z", cost=1.0, run_id="run-1"
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("first_pass_yield", report)
        self.assertIn("1", report)

    def test_a_task_reworked_once_pulls_fpy_below_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                _worker_execution_record(
                    "task-reworked", timestamp="2026-01-02T00:00:00Z", cost=1.0, run_id="run-1"
                ),
                _worker_execution_record(
                    "task-reworked", timestamp="2026-01-03T00:00:00Z", cost=1.0, run_id="run-2"
                ),
                _worker_execution_record(
                    "task-clean", timestamp="2026-01-02T00:00:00Z", cost=1.0, run_id="run-clean"
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("first_pass_yield", report)
        self.assertIn("0.5", report)

    def test_fpy_is_no_data_on_an_empty_journal(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("first_pass_yield", report)


# --- Dynamic metrics: model family breakdown ---


class ModelFamilyBreakdownTests(unittest.TestCase):
    def test_each_family_gets_a_row_with_cost_and_success_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                _worker_execution_record(
                    "task-1",
                    timestamp="2026-01-02T00:00:00Z",
                    cost=1.0,
                    success=True,
                    model_family="claude",
                ),
                _worker_execution_record(
                    "task-2",
                    timestamp="2026-01-03T00:00:00Z",
                    cost=3.0,
                    success=False,
                    model_family="claude",
                ),
                _worker_execution_record(
                    "task-3",
                    timestamp="2026-01-03T00:00:00Z",
                    cost=0.5,
                    success=True,
                    model_family="gemini",
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("claude", report)
        self.assertIn("gemini", report)
        # claude: 2 executions, 1 success -> 50.0%; gemini: 1 execution, 1 success -> 100.0%.
        self.assertIn("50.0%", report)
        self.assertIn("100.0%", report)
        self.assertIn("Rework Rate", report)

    def test_an_execution_outside_the_window_is_excluded_from_the_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _worker_execution_record(
                "task-old", timestamp="2025-01-01T00:00:00Z", cost=9.0, model_family="stale-family"
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertNotIn("stale-family", report)

    def test_no_worker_executions_renders_an_empty_state_not_a_crash(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("No worker executions", report)

    def test_rework_rate_is_reported_per_model_family(self) -> None:
        journal = learning_journal.JournalRead(
            worker_executions=(
                _worker_execution_record(
                    "task-reworked", timestamp="2026-01-02T00:00:00Z", run_id="run-1"
                ),
                _worker_execution_record(
                    "task-reworked", timestamp="2026-01-03T00:00:00Z", run_id="run-2"
                ),
            )
        )
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("100.0%", report)


# --- Dynamic metrics: compliance audits & degradation events ---


class ComplianceAndDegradationTests(unittest.TestCase):
    def test_a_windowed_compliance_record_is_listed_with_its_issue_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _compliance_record(
                "session-audit-1",
                violation_count=2,
                timestamp="2026-01-05T00:00:00Z",
                session_last_activity="2026-01-05T00:00:00Z",
                issue_codes=("DEC-01", "LOG-02"),
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("session-audit-1", report)
        self.assertIn("DEC-01", report)
        self.assertIn("LOG-02", report)

    def test_only_the_last_record_for_a_session_survives_reduction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                _compliance_record(
                    "session-repeat",
                    violation_count=1,
                    timestamp="2026-01-04T00:00:00Z",
                    session_last_activity="2026-01-05T00:00:00Z",
                    run_id="run-1",
                ),
                _compliance_record(
                    "session-repeat",
                    violation_count=9,
                    timestamp="2026-01-05T00:00:00Z",
                    session_last_activity="2026-01-05T00:00:00Z",
                    run_id="run-2",
                ),
            )
            for record in records:
                assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertEqual(report.count("session-repeat"), 1)
        self.assertIn('dir="ltr">9</td>', report)

    def test_no_compliance_records_renders_an_empty_state(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("No compliance audits", report)

    def test_a_degraded_dialogue_in_the_window_is_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-degraded",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T10:00:00Z",
                degraded=True,
                occasion="plan-review",
                topology="pair",
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("task-degraded", report)
        self.assertIn("plan-review", report)

    def test_a_non_degraded_dialogue_never_appears_in_the_degradation_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = _dialogue_record(
                "task-healthy",
                rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=1),),
                timestamp="2026-01-05T10:00:00Z",
                degraded=False,
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            journal = learning_journal.read_journal(root)

        board, baseline_board = _boards(journal, now=_NOW)
        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertNotIn("task-healthy", report)

    def test_no_degradations_renders_an_empty_state(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("No budget degradations", report)

    def test_consensus_and_debate_metrics_are_rendered(self) -> None:
        journal = learning_journal.JournalRead(
            dialogues=(
                _dialogue_record(
                    "task-consensus",
                    rounds=(learning_journal.DialogueRound(verdict="approved", engagement_count=2),),
                    timestamp="2026-01-05T10:00:00Z",
                ),
                _dialogue_record(
                    "task-stalemate",
                    rounds=(learning_journal.DialogueRound(verdict="revise", engagement_count=1),),
                    timestamp="2026-01-06T10:00:00Z",
                ),
            )
        )
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("Consensus &amp; Debate", report)
        self.assertIn("50.0%", report)


# --- Escaping ---


class EscapingTests(unittest.TestCase):
    def test_the_escape_helper_neutralizes_html_metacharacters(self) -> None:
        dangerous = "<script>alert('x')</script>&\""

        escaped = learning_report_html._escape(dangerous)

        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)
        self.assertIn("&amp;", escaped)
        self.assertIn("&quot;", escaped)

    def test_the_escape_helper_stringifies_a_non_string_value_first(self) -> None:
        self.assertEqual(learning_report_html._escape(3.14), "3.14")
        self.assertEqual(learning_report_html._escape(True), "True")

    # The document-wide "no `<`+`script` substring anywhere" assertion that
    # used to live here described a report with no inline JavaScript. Ticket
    # 48 gives it two script blocks by design, so the invariant moved to
    # `ScriptInjectionTests` below, restated as the property that survives
    # that change: the document's script tags are exactly the two this module
    # emits, and no dynamic value can add a third or break out of the JSON
    # payload.


# --- Role matrix (ticket 47) ---


def _capability(
    *,
    provider: str = "anthropic",
    model_id: str = "claude-sonnet-5",
    supported_efforts: tuple[str, ...] = ("low", "medium", "high"),
    default_effort: str | None = "medium",
    tier: str = "high",
    context: int | None = 200000,
    local_only: bool = False,
) -> Any:
    return routing_config.ModelCapability(
        provider=provider,
        model_id=model_id,
        supported_efforts=supported_efforts,
        default_effort=default_effort,
        tier=tier,
        context=context,
        local_only=local_only,
    )


def _binding(
    *,
    provider_id: str = "anthropic-sonnet",
    adapter: str = "anthropic",
    model_id: str = "claude-sonnet-5",
    reasoning_effort: str = "medium",
    capability: Any | None = None,
) -> Any:
    """NOTE: `capability=None` means "give me the default capability", not
    "this binding has none" — see the substitution below. To build a
    capability-less (drift) binding, construct `RoleModelBinding` directly,
    as `test_a_binding_with_no_capability_shows_an_unknown_capability_pill`
    and `_reactive_report` both do.
    """
    return routing_config.RoleModelBinding(
        provider_id=provider_id,
        adapter=adapter,
        model_id=model_id,
        reasoning_effort=reasoning_effort,
        capability=capability if capability is not None else _capability(),
    )


def _role_entry(
    role_id: str,
    *,
    reasoning_tier: str = "high",
    tool_access: str = "full",
    min_context: int = 100000,
    local_only: bool = False,
    bindings: tuple[Any, ...] = (),
) -> Any:
    return routing_config.RoleMatrixEntry(
        role_id=role_id,
        capability_requirements=routing_config.CapabilityRequirements(
            reasoning_tier=reasoning_tier,
            tool_access=tool_access,
            min_context=min_context,
            local_only=local_only,
        ),
        bindings=bindings,
    )


class RoleMatrixSectionTests(unittest.TestCase):
    def test_empty_role_matrix_renders_an_empty_state_in_both_grids(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn('id="role-grid-simple"', report)
        self.assertIn('id="role-grid-all"', report)
        self.assertIn("No roles configured.", report)

    def test_tab_bar_and_role_matrix_heading_are_present(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn('id="tab-metrics"', report)
        self.assertIn('id="tab-roles"', report)
        self.assertIn("מדדי ביצוע ולמידה", report)
        self.assertIn("הגדרת תפקידים ומודלים", report)
        self.assertIn("Role &amp; Model Configuration Matrix", report)

    def test_segmented_toggle_labels_are_present(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)

        report = learning_report_html.render_html_report(journal, board, baseline_board, now=_NOW)

        self.assertIn("תפקידי מפתח (ראשי)", report)
        self.assertIn("פירוט מלא (מתקדם)", report)

    def test_a_primary_role_appears_in_both_the_simple_and_all_grids(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {"planner": _role_entry("planner", bindings=(_binding(),))}

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        simple_grid = report.split('id="role-grid-simple"')[1].split('id="role-grid-all"')[0]
        all_grid = report.split('id="role-grid-all"')[1]
        self.assertIn("planner", simple_grid)
        self.assertIn("planner", all_grid)

    def test_a_non_primary_role_appears_only_in_the_all_grid(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {
            "reviewer_security": _role_entry("reviewer_security", bindings=(_binding(),))
        }

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        simple_grid = report.split('id="role-grid-simple"')[1].split('id="role-grid-all"')[0]
        all_grid = report.split('id="role-grid-all"')[1]
        self.assertNotIn("reviewer_security", simple_grid)
        self.assertIn("reviewer_security", all_grid)

    def test_role_card_shows_capability_requirements_and_binding_details(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {
            "planner": _role_entry(
                "planner",
                reasoning_tier="high",
                tool_access="full",
                min_context=128000,
                bindings=(
                    _binding(
                        provider_id="anthropic-sonnet",
                        model_id="claude-sonnet-5",
                        reasoning_effort="high",
                        capability=_capability(tier="high", supported_efforts=("low", "high")),
                    ),
                ),
            )
        }

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        self.assertIn("Reasoning Tier: high", report)
        self.assertIn("Tool Access: full", report)
        self.assertIn("Min Context: 128000", report)
        self.assertIn("Provider: anthropic-sonnet", report)
        self.assertIn("Model: claude-sonnet-5", report)
        self.assertIn("Effort: high", report)
        self.assertIn("Supported Efforts: low, high", report)

    def test_a_binding_with_no_capability_shows_an_unknown_capability_pill(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {
            "planner": _role_entry(
                "planner",
                bindings=(
                    routing_config.RoleModelBinding(
                        provider_id="anthropic-sonnet",
                        adapter="anthropic",
                        model_id="claude-sonnet-5",
                        reasoning_effort="medium",
                        capability=None,
                    ),
                ),
            )
        }

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        self.assertIn("Capability: unknown (drift)", report)

    def test_local_only_capability_requirement_renders_a_local_only_pill(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {"sensitive_executor": _role_entry("sensitive_executor", local_only=True)}

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        self.assertIn("Local Only: yes", report)

    def test_an_unrecognized_role_id_still_renders_instead_of_being_dropped(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {"future_role": _role_entry("future_role")}

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        all_grid = report.split('id="role-grid-all"')[1]
        self.assertIn("future_role", all_grid)

    def test_role_matrix_values_are_escaped(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        role_matrix = {
            "planner": _role_entry(
                "planner",
                reasoning_tier="<script>alert(1)</script>",
                bindings=(),
            )
        }

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, role_matrix=role_matrix
        )

        self.assertNotIn("<script>alert(1)", report)
        self.assertIn("&lt;script&gt;alert(1)", report)

    def test_write_html_report_wires_in_the_real_routing_config_role_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            path = learning_report_html.write_html_report(root, now=_NOW)

            content = path.read_text(encoding="utf-8")
            self.assertIn('id="role-grid-simple"', content)
            self.assertIn("planner", content)
            self.assertNotIn("No roles configured.", content)


# --- The write door ---


class WriteHtmlReportTests(unittest.TestCase):
    def test_write_creates_parent_dirs_writes_at_html_report_path_and_matches_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            path = learning_report_html.write_html_report(root, now=_NOW)

            self.assertEqual(path, learning_report_html.html_report_path(root, now=_NOW))
            self.assertTrue(path.exists())
            self.assertEqual(path.suffix, ".html")

    def test_write_accepts_an_explicit_output_path_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom = root / "custom" / "dashboard.html"

            path = learning_report_html.write_html_report(root, now=_NOW, output_path=custom)

            self.assertEqual(path, custom)
            self.assertTrue(custom.exists())

    def test_write_accepts_a_literal_journal_path_and_positional_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_path = root / learning_journal.JOURNAL_RELATIVE_PATH
            custom = root / "custom" / "dashboard.html"

            path = learning_report_html.write_html_report(journal_path, custom, now=_NOW)

            self.assertEqual(path, custom)
            self.assertTrue(custom.exists())

    def test_a_second_call_the_same_utc_day_supersedes_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            first_path = learning_report_html.write_html_report(root, now=_NOW)
            record = _worker_execution_record(
                "task-x", timestamp="2026-01-05T00:00:00Z", cost=1.0
            )
            assert learning_journal.append_journal_record(record, root_dir=root) is None
            second_path = learning_report_html.write_html_report(root, now=_NOW)

            self.assertEqual(first_path, second_path)

    def test_a_successful_write_leaves_no_stray_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            path = learning_report_html.write_html_report(root, now=_NOW)

            siblings = list(path.parent.iterdir())
            self.assertIn(path, siblings)
            for sibling in siblings:
                self.assertFalse(sibling.name.startswith("."))

    def test_a_failure_between_temp_creation_and_replace_leaves_the_prior_report_untouched(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path = learning_report_html.write_html_report(root, now=_NOW)
            original_bytes = first_path.read_bytes()

            with (
                mock.patch("learning_report_html.os.replace", side_effect=OSError("boom")),
                self.assertRaises(OSError),
            ):
                learning_report_html.write_html_report(root, now=_NOW)

            self.assertEqual(first_path.read_bytes(), original_bytes)

    def test_write_refuses_a_naive_now_before_any_disk_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                learning_report_html.write_html_report(root, now=datetime(2026, 1, 8))  # noqa: DTZ001

            self.assertFalse((root / ".ralph").exists())

    def test_write_refuses_a_non_positive_window_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ValueError):
                learning_report_html.write_html_report(root, now=_NOW, window_days=-1)


# --- html_report_path ---


class HtmlReportPathTests(unittest.TestCase):
    def test_html_report_path_is_beneath_root_in_ralph_reports_named_by_utc_date(self) -> None:
        root = Path("/fake/root")
        now = datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc)

        path = learning_report_html.html_report_path(root, now=now)

        self.assertEqual(path, root / ".ralph" / "reports" / "weekly-report-2026-01-08.html")

    def test_html_report_path_names_the_file_by_the_utc_date_not_a_local_one(self) -> None:
        root = Path("/fake/root")
        # 2026-01-09T02:00:00+09:00 is 2026-01-08T17:00:00Z.
        now = datetime(2026, 1, 9, 2, 0, 0, tzinfo=timezone(timedelta(hours=9)))

        path = learning_report_html.html_report_path(root, now=now)

        self.assertEqual(path.name, "weekly-report-2026-01-08.html")

    def test_html_report_path_refuses_a_naive_now(self) -> None:
        root = Path("/fake/root")

        with self.assertRaises(ValueError):
            learning_report_html.html_report_path(root, now=datetime(2026, 1, 8))  # noqa: DTZ001


# --- Reactive model/effort binding (ticket 48) ---


class EffortSnapRuleTests(unittest.TestCase):
    """`_resolve_effort_state` is the whole of Spec 0013 §3's auto-snap rule,
    in one pure function, so both the server-side initial render and the
    embedded JavaScript decide identically. These cases pin the rule itself;
    `JsEffortSnapParityTests` separately runs its own case table through
    both implementations and asserts they answer alike.
    """

    def test_a_supported_configured_effort_is_kept(self) -> None:
        capability = _capability(supported_efforts=("low", "medium", "high"), default_effort="high")

        state = learning_report_html._resolve_effort_state(capability, "low")

        self.assertEqual(state, learning_report_html.EffortState("ok", "low", ("low", "medium", "high")))

    def test_an_unsupported_configured_effort_snaps_to_the_models_default(self) -> None:
        capability = _capability(supported_efforts=("low", "medium"), default_effort="medium")

        state = learning_report_html._resolve_effort_state(capability, "ultra")

        self.assertEqual(state.effort, "medium")
        self.assertEqual(state.status, "ok")

    def test_an_unsupported_effort_falls_back_to_the_first_rung_when_the_default_is_also_unsupported(
        self,
    ) -> None:
        # `claude-sonnet-4-6` under `antigravity_cli` is exactly this shape:
        # a real ladder with `default_effort=None`, because `agy models`
        # publishes no per-model default.
        capability = _capability(supported_efforts=("low", "medium", "high"), default_effort=None)

        state = learning_report_html._resolve_effort_state(capability, "ultra")

        self.assertEqual(state.effort, "low")
        self.assertEqual(state.status, "ok")

    def test_a_model_with_no_effort_ladder_reports_none_not_a_snapped_effort(self) -> None:
        # `claude-3-7-sonnet` and every LM Studio entry: `supported_efforts=()`
        # means "this model has no reasoning-effort parameter at all", which
        # is a different answer from "we do not know its ladder".
        capability = _capability(supported_efforts=(), default_effort=None)

        state = learning_report_html._resolve_effort_state(capability, "high")

        self.assertEqual(state, learning_report_html.EffortState("none", None, ()))

    def test_an_unknown_capability_reports_unknown_and_preserves_the_configured_effort(self) -> None:
        # A drift binding (`capability=None`): the configured effort is still
        # what `routing-config.json` says, and this view must not silently
        # rewrite it just because the audited catalog has never seen the model.
        state = learning_report_html._resolve_effort_state(None, "medium")

        self.assertEqual(state, learning_report_html.EffortState("unknown", "medium", ()))


class RoleCardControlsTests(unittest.TestCase):
    def _report(self, role_matrix: Any, capabilities: Any = None) -> str:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        return learning_report_html.render_html_report(
            journal,
            board,
            baseline_board,
            now=_NOW,
            role_matrix=role_matrix,
            model_capabilities=capabilities,
        )

    def test_a_role_card_carries_a_model_select_an_effort_select_and_a_badge(self) -> None:
        report = self._report({"planner": _role_entry("planner", bindings=(_binding(),))})

        self.assertIn('data-role-id="planner"', report)
        self.assertIn('class="model-select"', report)
        self.assertIn('class="effort-select"', report)
        self.assertIn('class="effort-badge"', report)

    def test_the_effort_select_offers_only_the_bound_models_supported_efforts(self) -> None:
        report = self._report(
            {
                "planner": _role_entry(
                    "planner",
                    bindings=(
                        _binding(
                            reasoning_effort="low",
                            capability=_capability(supported_efforts=("low", "high")),
                        ),
                    ),
                )
            }
        )

        options = _effort_option_values(report)
        self.assertEqual(options, ["low", "high"])

    def test_the_initial_render_already_snaps_an_unsupported_configured_effort(self) -> None:
        # The snap is not a JavaScript-only behavior: a document opened with
        # scripting disabled must still never display an effort the bound
        # model cannot accept.
        report = self._report(
            {
                "planner": _role_entry(
                    "planner",
                    bindings=(
                        _binding(
                            reasoning_effort="ultra",
                            capability=_capability(
                                supported_efforts=("low", "medium"), default_effort="medium"
                            ),
                        ),
                    ),
                )
            }
        )

        self.assertEqual(_effort_option_values(report), ["low", "medium"])
        self.assertIn('<option value="medium" selected>medium</option>', report)
        self.assertNotIn(">ultra<", report)

    def test_the_model_select_lists_every_registry_entry(self) -> None:
        capabilities = {
            ("claude_code_cli", "claude-opus-5"): _capability(
                provider="claude_code_cli", model_id="claude-opus-5"
            ),
            ("codex_cli", "gpt-5.6-sol"): _capability(provider="codex_cli", model_id="gpt-5.6-sol"),
        }
        report = self._report(
            {"planner": _role_entry("planner", bindings=(_binding(),))}, capabilities
        )

        self.assertIn('value="claude_code_cli::claude-opus-5"', report)
        self.assertIn('value="codex_cli::gpt-5.6-sol"', report)

    def test_a_drift_binding_still_appears_as_the_selected_model_option(self) -> None:
        # `lm_studio_local::qwen3-coder-30b` is a real, currently-configured
        # binding absent from the audited registry. The select must be able to
        # show what the config actually says, not silently fall back to some
        # other model's option.
        capabilities = {
            ("claude_code_cli", "claude-opus-5"): _capability(
                provider="claude_code_cli", model_id="claude-opus-5"
            )
        }
        report = self._report(
            {
                "adjudicator": _role_entry(
                    "adjudicator",
                    bindings=(
                        _binding(
                            provider_id="lm_studio_local",
                            adapter="lm_studio_local",
                            model_id="qwen3-coder-30b",
                            capability=None,
                        ),
                    ),
                )
            },
            capabilities,
        )

        self.assertIn('value="lm_studio_local::qwen3-coder-30b" selected', report)

    def test_a_model_with_no_effort_ladder_renders_a_disabled_effort_select(self) -> None:
        report = self._report(
            {
                "adjudicator": _role_entry(
                    "adjudicator",
                    bindings=(_binding(capability=_capability(supported_efforts=())),),
                )
            }
        )

        self.assertIn('class="effort-select" data-role-id="adjudicator" disabled', report)

    def test_every_effort_rung_in_the_closed_vocabulary_has_its_own_badge_color(self) -> None:
        # `_EFFORT_RANK` is the closed vocabulary; a rung missing from the
        # badge palette would render neutral grey, reading as "unknown"
        # rather than as the valid, expensive rung it actually is.
        self.assertEqual(
            sorted(learning_report_html._EFFORT_BADGE_COLORS),
            sorted(routing_config._EFFORT_RANK),
        )

    def test_badge_colors_are_legible_and_mutually_distinguishable(self) -> None:
        """User story 7 wants the badge to give "instant visual feedback on
        cognitive resource allocation". Two rungs painted the same shade, or
        a rung whose text cannot be read against its own background, both
        defeat that — and neither is visible in a test that only checks the
        table has an entry per rung.

        A badge paints text in the rung's color over that color at 10%
        (`background:{color}1a` in `_role_controls_html`), so the background
        is derived, not independent: the contrast figure below is against
        the tint each color creates for itself over the white card.
        """
        for effort, color in learning_report_html._EFFORT_BADGE_COLORS.items():
            with self.subTest(effort=effort):
                ratio = _contrast_ratio(color, _tint_over_white(color))
                self.assertGreaterEqual(
                    ratio,
                    4.5,
                    f"{effort} badge ({color}) scores {ratio:.2f} against its own "
                    f"tint; WCAG AA wants 4.5 for text this size",
                )

        rungs = list(learning_report_html._EFFORT_BADGE_COLORS)
        for first, second in itertools.combinations(rungs, 2):
            with self.subTest(pair=(first, second)):
                distance = _color_distance(
                    learning_report_html._EFFORT_BADGE_COLORS[first],
                    learning_report_html._EFFORT_BADGE_COLORS[second],
                )
                # ~10 is where two colors stop being tellable apart at a
                # glance; 15 leaves margin for the tint and for small text.
                self.assertGreaterEqual(
                    distance,
                    15.0,
                    f"{first} and {second} badges are only deltaE {distance:.1f} apart",
                )

    def test_the_badge_is_painted_with_the_selected_efforts_color(self) -> None:
        report = self._report(
            {
                "planner": _role_entry(
                    "planner",
                    bindings=(
                        _binding(
                            reasoning_effort="high",
                            capability=_capability(supported_efforts=("high",)),
                        ),
                    ),
                )
            }
        )

        purple = learning_report_html._EFFORT_BADGE_COLORS["high"]
        self.assertIn(f"color:{purple}", report)


class ModelCapabilitiesPayloadTests(unittest.TestCase):
    def test_the_embedded_payload_is_valid_json_keyed_by_provider_and_model(self) -> None:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        capabilities = {
            ("codex_cli", "gpt-5.6-sol"): _capability(
                provider="codex_cli",
                model_id="gpt-5.6-sol",
                supported_efforts=("low", "ultra"),
                default_effort="low",
                tier="ultra",
                context=272000,
            )
        }

        report = learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, model_capabilities=capabilities
        )

        payload = _capabilities_payload(report)
        # Exactly the two fields the script reads — an equality assertion,
        # so re-adding `tier`/`context`/`localOnly`, none of which the
        # script reads, fails here rather than quietly growing the document.
        self.assertEqual(
            payload["codex_cli::gpt-5.6-sol"],
            {"supportedEfforts": ["low", "ultra"], "defaultEffort": "low"},
        )

    def test_write_html_report_embeds_the_real_audited_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = learning_report_html.write_html_report(Path(tmp), now=_NOW)

            payload = _capabilities_payload(path.read_text(encoding="utf-8"))
            self.assertIn("claude_code_cli::claude-opus-5", payload)
            self.assertEqual(
                payload["claude_code_cli::claude-opus-5"]["supportedEfforts"],
                ["low", "medium", "high", "xhigh", "max"],
            )


class ScriptInjectionTests(unittest.TestCase):
    """The report ships inline JavaScript as of ticket 48, so the old
    "no `<`+`script` substring anywhere" invariant no longer describes it.
    These replace it with the property that actually matters: the document's
    script tags are only the two this module itself emits, and no dynamic
    value can add a third or break out of the JSON payload.
    """

    def _report(self, **kwargs: Any) -> str:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        return learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW, **kwargs
        )

    def test_the_only_script_tags_are_the_two_the_module_emits(self) -> None:
        openers = _script_openers(self._report())

        self.assertEqual(
            openers,
            ['<script type="application/json" id="dashboard-config">', "<script>"],
        )

    def test_a_role_value_carrying_a_script_tag_adds_no_script_tag_to_the_document(self) -> None:
        role_matrix = {
            "planner": _role_entry("planner", reasoning_tier="</script><script>alert(1)</script>")
        }

        report = self._report(role_matrix=role_matrix)

        self.assertEqual(len(_script_openers(report)), 2)
        self.assertNotIn("<script>alert(1)", report)
        self.assertIn("&lt;script&gt;alert(1)", report)

    def test_a_capability_model_id_cannot_break_out_of_the_json_payload(self) -> None:
        capabilities = {
            ("codex_cli", "</script><script>alert(1)</script>"): _capability(
                provider="codex_cli", model_id="</script><script>alert(1)</script>"
            )
        }

        report = self._report(model_capabilities=capabilities)

        self.assertEqual(len(_script_openers(report)), 2)
        self.assertNotIn("<script>alert(1)", report)
        # Still round-trips as data: neutralized, not mangled or dropped.
        payload = _capabilities_payload(report)
        self.assertIn("codex_cli::</script><script>alert(1)</script>", payload)

    def test_the_embedded_script_interpolates_no_dynamic_value(self) -> None:
        # The executable block is a single static literal. Everything dynamic
        # travels through the `application/json` block instead, where it is
        # data the script parses rather than source the browser compiles.
        first = self._report()
        second = self._report(
            role_matrix={"planner": _role_entry("planner", bindings=(_binding(),))},
            model_capabilities={("codex_cli", "gpt-5.6-sol"): _capability()},
        )

        self.assertEqual(_executable_script(first), _executable_script(second))


_LADDERS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "claude-opus-5": (("low", "medium", "high", "xhigh", "max"), "high"),
    "gpt-5.6-sol": (("low", "medium", "high", "xhigh", "max", "ultra"), "low"),
    "gemini-3.6-flash-medium": (("medium",), "medium"),
    "claude-sonnet-4-6": (("low", "medium", "high"), None),
    "claude-3-7-sonnet": ((), None),
}


def _capability_fixture() -> dict[tuple[str, str], Any]:
    return {
        ("provider", model_id): _capability(
            provider="provider",
            model_id=model_id,
            supported_efforts=supported,
            default_effort=default,
        )
        for model_id, (supported, default) in _LADDERS.items()
    }


def _reactive_report(*, effort: str = "high", model_id: str = "claude-opus-5") -> str:
    """A two-grid report whose one role is bound to `model_id` at `effort`.
    `planner` is a primary role, so it renders in both grids — which is what
    makes the "every copy of the card updates" assertions meaningful.
    """
    journal = learning_journal.JournalRead()
    board, baseline_board = _boards(journal, now=_NOW)
    # Built directly rather than through `_binding`, which substitutes a
    # default capability for `None` — that would render a card claiming a
    # capability the embedded registry does not carry, a state
    # `get_role_matrix_view_data` can never produce (it resolves both from
    # the same registry) and one that would make the server and the script
    # legitimately disagree.
    binding = routing_config.RoleModelBinding(
        provider_id="provider",
        adapter="provider",
        model_id=model_id,
        reasoning_effort=effort,
        capability=_capability_fixture().get(("provider", model_id)),
    )
    role_matrix = {"planner": _role_entry("planner", bindings=(binding,))}
    return learning_report_html.render_html_report(
        journal,
        board,
        baseline_board,
        now=_NOW,
        role_matrix=role_matrix,
        model_capabilities=_capability_fixture(),
    )


def _two_role_reactive_report() -> str:
    """Two primary roles (`planner`, `builder_heavy`), each bound to a
    distinct model — the fixture `ClientStateMachineTests` needs to tell
    "this role is dirty" apart from "every role is dirty", and to prove an
    edit to one role's undo history never touches the other's.
    """
    journal = learning_journal.JournalRead()
    board, baseline_board = _boards(journal, now=_NOW)
    capabilities = _capability_fixture()
    planner_binding = routing_config.RoleModelBinding(
        provider_id="provider",
        adapter="provider",
        model_id="claude-opus-5",
        reasoning_effort="high",
        capability=capabilities.get(("provider", "claude-opus-5")),
    )
    builder_binding = routing_config.RoleModelBinding(
        provider_id="provider",
        adapter="provider",
        model_id="claude-sonnet-4-6",
        reasoning_effort="medium",
        capability=capabilities.get(("provider", "claude-sonnet-4-6")),
    )
    role_matrix = {
        "planner": _role_entry("planner", bindings=(planner_binding,)),
        "builder_heavy": _role_entry("builder_heavy", bindings=(builder_binding,)),
    }
    return learning_report_html.render_html_report(
        journal,
        board,
        baseline_board,
        now=_NOW,
        role_matrix=role_matrix,
        model_capabilities=capabilities,
    )


class EmbeddedScriptBehaviorTests(unittest.TestCase):
    """The embedded JavaScript, executed. Every assertion here runs the
    report's own `<script>` body under node against a DOM stub built from
    that same report's rendered cards — nothing asserts over the script's
    source text, which would pass just as happily if the logic inside were
    wrong.
    """

    def test_selecting_a_model_rebuilds_the_effort_options_from_its_own_ladder(self) -> None:
        result = _run_embedded_script(
            _reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertEqual(
            result[0]["options"], ["low", "medium", "high", "xhigh", "max", "ultra"]
        )

    def test_a_still_supported_effort_survives_a_model_change(self) -> None:
        # `high` is on both ladders, so nothing should be snapped away.
        result = _run_embedded_script(
            _reactive_report(effort="high"),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertEqual(result[0]["effort"], "high")

    def test_an_unsupported_effort_snaps_to_the_new_models_default(self) -> None:
        # `ultra` exists only on `gpt-5.6-sol`; moving to `claude-opus-5`,
        # whose ladder stops at `max`, must snap to that model's own default.
        report = _reactive_report(effort="ultra", model_id="gpt-5.6-sol")
        result = _run_embedded_script(
            report,
            'onModelSelect("planner", "provider::claude-opus-5");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertEqual(result[0]["effort"], "high")
        self.assertNotIn("ultra", result[0]["options"])

    def test_an_unsupported_effort_falls_back_to_the_lowest_rung_without_a_default(self) -> None:
        report = _reactive_report(effort="max", model_id="claude-opus-5")
        result = _run_embedded_script(
            report,
            'onModelSelect("planner", "provider::claude-sonnet-4-6");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertEqual(result[0]["effort"], "low")

    def test_selecting_a_model_with_no_ladder_disables_the_effort_select(self) -> None:
        result = _run_embedded_script(
            _reactive_report(),
            'onModelSelect("planner", "provider::claude-3-7-sonnet");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertTrue(result[0]["disabled"])
        self.assertEqual(result[0]["badgeText"], "none")
        self.assertEqual(result[0]["options"], [""])

    def test_selecting_an_unaudited_model_reports_drift_and_keeps_the_configured_effort(
        self,
    ) -> None:
        result = _run_embedded_script(
            _reactive_report(effort="high"),
            'onModelSelect("planner", "provider::not-in-the-registry");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertEqual(result[0]["badgeText"], "unknown")
        self.assertEqual(result[0]["effort"], "high")
        self.assertTrue(result[0]["disabled"])

    def test_the_badge_is_repainted_in_the_new_efforts_color(self) -> None:
        report = _reactive_report(effort="ultra", model_id="gpt-5.6-sol")
        result = _run_embedded_script(
            report,
            'onModelSelect("planner", "provider::gemini-3.6-flash-medium");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertEqual(result[0]["badgeText"], "medium")
        self.assertEqual(
            result[0]["badgeColor"], learning_report_html._EFFORT_BADGE_COLORS["medium"]
        )

    def test_every_grids_copy_of_the_role_updates_not_only_the_one_touched(self) -> None:
        # `planner` renders in both the primary and the advanced grid. If only
        # the touched card updated, flipping the segmented toggle would show
        # the other copy still on the old model.
        report = _reactive_report()
        specs = _role_card_specs(report)
        self.assertEqual(len(specs), 2, "planner should render once per grid")

        result = _run_embedded_script(
            report,
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], result[1])
        self.assertEqual(result[0]["model"], "provider::gpt-5.6-sol")

    def test_a_change_event_on_the_rendered_select_drives_the_same_update(self) -> None:
        # The handler is not merely defined: the renderer's own markup is
        # wired to it, so a user changing the dropdown triggers the snap.
        result = _run_embedded_script(
            _reactive_report(effort="ultra", model_id="gpt-5.6-sol"),
            'fireChange("planner", ".model-select", "provider::claude-opus-5");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertEqual(result[0]["effort"], "high")
        self.assertEqual(result[0]["model"], "provider::claude-opus-5")

    def test_reselecting_the_rendered_model_reproduces_the_rendered_card(self) -> None:
        """Server and client agree on the initial state, not merely on
        transitions. Re-selecting the model a card was *already* rendered
        with must leave its model, effort, disabled flag and badge exactly
        as rendered: the Python renderer and the JavaScript are then
        demonstrably deciding the same way about the same input, which is
        the one thing `JsEffortSnapParityTests` cannot see — that compares
        the two snap functions, not the two bodies of code that paint a
        card from a snap result.
        """
        for effort, model_id in (
            ("high", "claude-opus-5"),
            ("ultra", "gpt-5.6-sol"),
            ("high", "claude-3-7-sonnet"),
            ("high", "not-in-the-registry"),
        ):
            with self.subTest(effort=effort, model=model_id):
                report = _reactive_report(effort=effort, model_id=model_id)
                rendered = _role_card_specs(report)[0]
                key = f"provider::{model_id}"

                after = _run_embedded_script(
                    report,
                    f'onModelSelect("planner", {json.dumps(key)});'
                    "console.log(JSON.stringify(snapshot(\"planner\")[0]));",
                )

                self.assertEqual(after["model"], rendered["model"])
                self.assertEqual(after["effort"], rendered["effort"])
                self.assertEqual(after["disabled"], rendered["disabled"])
                self.assertEqual(after["badgeText"], rendered["badge"])

    def test_moving_from_a_ladderless_model_to_an_unknown_one_keeps_a_labelled_option(
        self,
    ) -> None:
        # Reachable in the browser: the ladderless model empties the select,
        # so the unknown state inherits `""` as the current effort. The
        # option must still be labelled, not a blank row.
        result = _run_embedded_script(
            _reactive_report(),
            'onModelSelect("planner", "provider::claude-3-7-sonnet");'
            'onModelSelect("planner", "provider::not-in-the-registry");'
            "console.log(JSON.stringify(snapshot(\"planner\").concat("
            "  [{labels: CARD_NODES[0].querySelector('.effort-select')"
            "      .children.map(function (o) { return o.textContent; })}]"
            ")));",
        )

        self.assertEqual(result[0]["badgeText"], "unknown")
        self.assertEqual(result[-1]["labels"], ["none"])

    def test_choosing_an_effort_directly_repaints_every_copy_of_the_badge(self) -> None:
        result = _run_embedded_script(
            _reactive_report(effort="high"),
            'fireChange("planner", ".effort-select", "low");'
            "console.log(JSON.stringify(snapshot(\"planner\")));",
        )

        self.assertEqual([card["badgeText"] for card in result], ["low", "low"])
        self.assertEqual(
            result[1]["badgeColor"], learning_report_html._EFFORT_BADGE_COLORS["low"]
        )


class JsEffortSnapParityTests(unittest.TestCase):
    """`_resolve_effort_state` and the script's `resolveEffortState` are the
    same rule written twice, once per language. This runs one table of cases
    through both and asserts they agree — the guard that keeps a change to
    either side from silently teaching the server-rendered badge and the
    reactive one different answers.
    """

    _CASES: tuple[tuple[str, str], ...] = (
        ("claude-opus-5", "low"),
        ("claude-opus-5", "ultra"),
        ("claude-opus-5", "max"),
        ("gpt-5.6-sol", "ultra"),
        ("gpt-5.6-sol", ""),
        ("gemini-3.6-flash-medium", "high"),
        ("claude-sonnet-4-6", "ultra"),
        ("claude-sonnet-4-6", "medium"),
        ("claude-3-7-sonnet", "high"),
        ("not-in-the-registry", "medium"),
    )

    def test_both_implementations_agree_on_every_case(self) -> None:
        capabilities = _capability_fixture()
        expected = []
        for model_id, effort in self._CASES:
            state = learning_report_html._resolve_effort_state(
                capabilities.get(("provider", model_id)), effort
            )
            expected.append([state.status, state.effort, list(state.efforts)])

        cases = json.dumps(
            [[f"provider::{model_id}", effort] for model_id, effort in self._CASES]
        )
        actual = _run_embedded_script(
            _reactive_report(),
            f"var CASES = {cases};"
            "console.log(JSON.stringify(CASES.map(function (case_) {"
            "  var state = resolveEffortState(case_[0], case_[1]);"
            "  return [state.status, state.effort, state.efforts];"
            "})));",
        )

        self.assertEqual(actual, expected)
        # A table where every case landed on the same status would agree
        # trivially; these are the three the rule actually distinguishes.
        self.assertEqual({case[0] for case in expected}, {"ok", "none", "unknown"})


# --- floating action pill markup (ticket 49) ---


class ActionPillMarkupTests(unittest.TestCase):
    """Static presence checks for the pill and toast markup — the spec's own
    Testing Decisions call for "dirty bar, undo button, reset button ...
    elements exist" alongside the behavioral assertions below, and unlike
    those this needs no node subprocess: nothing here is reactive yet.
    """

    def _report(self) -> str:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        return learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW
        )

    def test_the_pill_and_its_three_buttons_are_present(self) -> None:
        report = self._report()

        self.assertIn('<div class="action-pill" id="action-pill">', report)
        self.assertIn('id="action-pill-label"', report)
        self.assertIn('id="action-undo"', report)
        self.assertIn('id="action-reset"', report)
        self.assertIn('id="action-save"', report)

    def test_the_toast_container_is_present(self) -> None:
        report = self._report()

        self.assertIn('<div class="toast-stack" id="toast-container"', report)

    def test_the_pill_and_toast_container_render_inside_the_roles_tab(self) -> None:
        # Nesting inside `#tab-content-roles` is what makes the pill vanish
        # along with the rest of that tab when the metrics tab is active —
        # see the CSS comment in `learning_report_html._CSS`.
        report = self._report()
        roles_tab = report.split('<div id="tab-content-roles">', 1)[1]

        self.assertIn('id="action-pill"', roles_tab)
        self.assertIn('id="toast-container"', roles_tab)

    def test_adding_the_pill_introduces_no_new_script_tag(self) -> None:
        self.assertEqual(
            _script_openers(self._report()),
            ['<script type="application/json" id="dashboard-config">', "<script>"],
        )


# --- client state machine & floating action pill behavior (ticket 49) ---


class ClientStateMachineTests(unittest.TestCase):
    """`currentRoles`/`savedSnapshot`/`undoHistory` and the pill they drive,
    executed under node the same way `EmbeddedScriptBehaviorTests` executes
    `onModelSelect` — against a stubbed DOM built from a real rendered
    report, never asserted over the script's source text.
    """

    def test_the_pill_starts_hidden_with_a_zero_dirty_count(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "console.log(JSON.stringify({"
            "  dirty: dirtyRoleCount(),"
            "  visible: ACTION_PILL.classList.contains('is-visible')"
            "}));",
        )

        self.assertEqual(result, {"dirty": 0, "visible": False})

    def test_reactive_update_keeps_the_zero_dirty_label_wording(self) -> None:
        # `updateActionPill()` runs once on load even though nothing is
        # dirty yet; it must not clobber `_ACTION_PILL_HTML`'s "no changes"
        # copy with a bare "0 ..." count.
        result = _run_embedded_script(
            _two_role_reactive_report(), "console.log(JSON.stringify(ACTION_PILL_LABEL.textContent));"
        )

        self.assertEqual(result, "אין שינויים לא שמורים")

    def test_changing_a_model_marks_its_role_dirty_and_reveals_the_pill(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "console.log(JSON.stringify({"
            "  dirty: dirtyRoleCount(),"
            "  visible: ACTION_PILL.classList.contains('is-visible'),"
            "  label: ACTION_PILL_LABEL.textContent"
            "}));",
        )

        self.assertEqual(result["dirty"], 1)
        self.assertTrue(result["visible"])
        self.assertEqual(result["label"], "שינוי אחד לא שמור")

    def test_editing_a_second_role_increments_the_dirty_count(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            'onModelSelect("builder_heavy", "provider::claude-opus-5");'
            "console.log(JSON.stringify(dirtyRoleCount()));",
        )

        self.assertEqual(result, 2)

    def test_reselecting_the_already_current_model_is_not_a_dirtying_edit(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::claude-opus-5");'
            "console.log(JSON.stringify(dirtyRoleCount()));",
        )

        self.assertEqual(result, 0)

    def test_undo_reverts_only_the_most_recently_edited_role(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            'onModelSelect("builder_heavy", "provider::claude-opus-5");'
            "undoChange();"
            "console.log(JSON.stringify({"
            "  dirty: dirtyRoleCount(),"
            "  planner: snapshot('planner')[0].model,"
            "  builder: snapshot('builder_heavy')[0].model"
            "}));",
        )

        self.assertEqual(result["dirty"], 1)
        self.assertEqual(result["planner"], "provider::gpt-5.6-sol")
        self.assertEqual(result["builder"], "provider::claude-sonnet-4-6")

    def test_undo_with_empty_history_is_a_silent_no_op(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "var undone = undoChange();"
            "console.log(JSON.stringify({ undone: undone, dirty: dirtyRoleCount() }));",
        )

        self.assertEqual(result, {"undone": False, "dirty": 0})

    def test_undoing_every_edit_hides_the_pill_again(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "undoChange();"
            "console.log(JSON.stringify({"
            "  dirty: dirtyRoleCount(),"
            "  visible: ACTION_PILL.classList.contains('is-visible')"
            "}));",
        )

        self.assertEqual(result, {"dirty": 0, "visible": False})

    def test_reset_declined_by_the_confirmation_prompt_changes_nothing(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "CONFIRM_RESULT = false;"
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "var reset = resetDefaults();"
            "console.log(JSON.stringify({"
            "  reset: reset,"
            "  dirty: dirtyRoleCount(),"
            "  prompted: CONFIRM_MESSAGES.length,"
            "  planner: snapshot('planner')[0].model"
            "}));",
        )

        self.assertEqual(result["reset"], False)
        self.assertEqual(result["dirty"], 1)
        self.assertEqual(result["prompted"], 1)
        self.assertEqual(result["planner"], "provider::gpt-5.6-sol")

    def test_reset_accepted_restores_every_dirty_role_to_the_pages_rendered_defaults(
        self,
    ) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "CONFIRM_RESULT = true;"
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            'onModelSelect("builder_heavy", "provider::claude-opus-5");'
            "var reset = resetDefaults();"
            "console.log(JSON.stringify({"
            "  reset: reset,"
            "  dirty: dirtyRoleCount(),"
            "  visible: ACTION_PILL.classList.contains('is-visible'),"
            "  planner: snapshot('planner')[0].model,"
            "  builder: snapshot('builder_heavy')[0].model"
            "}));",
        )

        self.assertEqual(result["reset"], True)
        self.assertEqual(result["dirty"], 0)
        self.assertFalse(result["visible"])
        self.assertEqual(result["planner"], "provider::claude-opus-5")
        self.assertEqual(result["builder"], "provider::claude-sonnet-4-6")

    def test_reset_reaches_the_pages_defaults_even_past_an_intervening_save(self) -> None:
        # Spec 0013 US12: "restore factory routing presets at any time."
        # `savedSnapshot` moves on every `saveChanges`, so a reset that read
        # *that* instead of the page's own immutable rendered values would
        # only reach back as far as the last save — this pins that it does
        # not: the save below is deliberately never undone.
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "saveChanges();"
            'onModelSelect("planner", "provider::gemini-3.6-flash-medium");'
            "CONFIRM_RESULT = true;"
            "var reset = resetDefaults();"
            "console.log(JSON.stringify({"
            "  reset: reset,"
            "  dirty: dirtyRoleCount(),"
            "  planner: snapshot('planner')[0].model"
            "}));",
        )

        self.assertEqual(result["reset"], True)
        self.assertEqual(result["dirty"], 0)
        self.assertEqual(result["planner"], "provider::claude-opus-5")

    def test_reset_is_a_no_op_and_never_prompts_when_already_at_system_defaults(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "var reset = resetDefaults();"
            "console.log(JSON.stringify({ reset: reset, prompted: CONFIRM_MESSAGES.length }));",
        )

        self.assertEqual(result, {"reset": False, "prompted": 0})

    def test_reset_is_still_available_after_a_save_leaves_nothing_dirty(self) -> None:
        # A save alone must not make `resetDefaults` believe there is
        # nothing left to reset — only actually being back at the page's
        # rendered defaults should.
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "saveChanges();"
            "CONFIRM_RESULT = true;"
            "var reset = resetDefaults();"
            "console.log(JSON.stringify({ reset: reset, planner: snapshot('planner')[0].model }));",
        )

        self.assertEqual(result["reset"], True)
        self.assertEqual(result["planner"], "provider::claude-opus-5")

    def test_save_commits_current_state_as_the_new_baseline_and_clears_undo(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "var saved = saveChanges();"
            "var undone = undoChange();"
            "console.log(JSON.stringify({"
            "  saved: saved,"
            "  dirty: dirtyRoleCount(),"
            "  undone: undone,"
            "  planner: snapshot('planner')[0].model"
            "}));",
        )

        self.assertEqual(result["saved"], True)
        self.assertEqual(result["dirty"], 0)
        self.assertFalse(result["undone"])
        self.assertEqual(result["planner"], "provider::gpt-5.6-sol")

    def test_save_is_a_no_op_when_nothing_is_dirty(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(), "console.log(JSON.stringify(saveChanges()));"
        )

        self.assertEqual(result, False)

    def test_undo_reset_and_save_each_show_a_toast(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "undoChange();"
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "CONFIRM_RESULT = true;"
            "resetDefaults();"
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "saveChanges();"
            "console.log(JSON.stringify(TOAST_CONTAINER.children.map(function (t) {"
            "  return t.textContent;"
            "})));",
        )

        self.assertEqual(len(result), 3)
        self.assertTrue(all(result))

    def test_clicking_the_pill_buttons_invokes_the_same_actions_as_calling_them(
        self,
    ) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "fireClick('action-undo');"
            "console.log(JSON.stringify(dirtyRoleCount()));",
        )

        self.assertEqual(result, 0)


# --- live JSON drawer markup (ticket 50) ---


class ConfigDrawerMarkupTests(unittest.TestCase):
    """Static presence checks for the drawer and its controls — mirrors
    `ActionPillMarkupTests`: nothing here is reactive yet, so this needs no
    node subprocess.
    """

    def _report(self) -> str:
        journal = learning_journal.JournalRead()
        board, baseline_board = _boards(journal, now=_NOW)
        return learning_report_html.render_html_report(
            journal, board, baseline_board, now=_NOW
        )

    def test_the_drawer_and_its_controls_are_present(self) -> None:
        report = self._report()

        self.assertIn('<div class="config-drawer" id="config-drawer">', report)
        self.assertIn('id="config-drawer-toggle"', report)
        self.assertIn('id="config-drawer-copy"', report)
        self.assertIn('id="config-drawer-json"', report)

    def test_the_drawer_starts_collapsed(self) -> None:
        # No `is-open` class in the rendered markup — `toggleConfigDrawer`
        # is the only thing that ever adds it, never the initial render.
        report = self._report()

        self.assertIn('<div class="config-drawer" id="config-drawer">', report)
        self.assertNotIn("config-drawer is-open", report)

    def test_the_copy_button_carries_the_tickets_hebrew_label(self) -> None:
        report = self._report()

        self.assertIn(
            '<button type="button" class="pill-btn" id="config-drawer-copy">'
            "📋 העתק קונפיגורציה</button>",
            report,
        )

    def test_the_drawer_renders_inside_the_roles_tab(self) -> None:
        # Same rationale as the action pill (see the CSS comment in
        # `learning_report_html._CSS`): nesting inside `#tab-content-roles`
        # hides the drawer along with the rest of that tab when the metrics
        # tab is active.
        report = self._report()
        roles_tab = report.split('<div id="tab-content-roles">', 1)[1]

        self.assertIn('id="config-drawer"', roles_tab)

    def test_adding_the_drawer_introduces_no_new_script_tag(self) -> None:
        self.assertEqual(
            _script_openers(self._report()),
            ['<script type="application/json" id="dashboard-config">', "<script>"],
        )


# --- live JSON drawer & clipboard export behavior (ticket 50) ---


class ConfigDrawerBehaviorTests(unittest.TestCase):
    """`buildConfigPreview`/`updateConfigDrawer`/`copyConfigToClipboard` and
    the drawer they drive, executed under node the same way
    `ClientStateMachineTests` executes the action pill's own reactive
    functions — against a stubbed DOM built from a real rendered report,
    never asserted over the script's source text.
    """

    def test_the_drawer_starts_closed(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "console.log(JSON.stringify(CONFIG_DRAWER.classList.contains('is-open')));",
        )

        self.assertFalse(result)

    def test_toggling_opens_then_closes_the_drawer(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "toggleConfigDrawer();"
            "var afterFirst = CONFIG_DRAWER.classList.contains('is-open');"
            "toggleConfigDrawer();"
            "var afterSecond = CONFIG_DRAWER.classList.contains('is-open');"
            "console.log(JSON.stringify({ afterFirst: afterFirst, afterSecond: afterSecond }));",
        )

        self.assertEqual(result, {"afterFirst": True, "afterSecond": False})

    def test_the_json_preview_reflects_the_pages_initial_role_state(self) -> None:
        # `updateConfigDrawer()` runs once on load (mirrors
        # `updateActionPill()`'s own initial call), so the drawer already
        # carries the page's rendered defaults before any edit.
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "console.log(JSON.stringify(JSON.parse(drawerPlainText())));",
        )

        self.assertEqual(
            result,
            {
                "roles": {
                    "planner": {"model": "provider::claude-opus-5", "effort": "high"},
                    "builder_heavy": {
                        "model": "provider::claude-sonnet-4-6",
                        "effort": "medium",
                    },
                }
            },
        )

    def test_the_json_preview_is_syntax_highlighted_not_plain_text(self) -> None:
        # Ticket 50's own "What to build" line asks for "a real-time
        # syntax-highlighted preview of `routing-config.json`" — this pins
        # that `updateConfigDrawer` actually wraps tokens in `<span
        # class="json-...">` markup, not just plain escaped text a reader
        # could mistake for highlighting because it merely *looks* like
        # JSON.
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "console.log(JSON.stringify({"
            "  html: CONFIG_DRAWER_JSON.innerHTML,"
            "  plain: drawerPlainText()"
            "}));",
        )

        self.assertIn('<span class="json-key">"planner"', result["html"])
        self.assertIn('<span class="json-string">"provider::claude-opus-5"', result["html"])
        self.assertEqual(json.loads(result["plain"])["roles"]["planner"]["effort"], "high")

    def test_the_syntax_highlighter_neutralizes_markup_characters(self) -> None:
        # `escapeHtmlText` runs before `syntaxHighlightJson` ever wraps a
        # span around anything — a role/model value carrying `<`, `>`, or
        # `&` must not reach the drawer's `innerHTML` as live markup, the
        # same invariant `ScriptInjectionTests` holds the rest of this
        # document to.
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "<img src=x onerror=alert(1)>&sol;");'
            "console.log(JSON.stringify(CONFIG_DRAWER_JSON.innerHTML));",
        )

        self.assertNotIn("<img", result)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;&amp;sol;", result)

    def test_the_json_preview_updates_on_every_model_change(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "console.log(JSON.stringify("
            "  JSON.parse(drawerPlainText()).roles.planner"
            "));",
        )

        self.assertEqual(result, {"model": "provider::gpt-5.6-sol", "effort": "high"})

    def test_the_json_preview_updates_after_undo(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "undoChange();"
            "console.log(JSON.stringify("
            "  JSON.parse(drawerPlainText()).roles.planner"
            "));",
        )

        self.assertEqual(result, {"model": "provider::claude-opus-5", "effort": "high"})

    def test_the_json_preview_updates_after_reset(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "CONFIRM_RESULT = true;"
            "resetDefaults();"
            "console.log(JSON.stringify("
            "  JSON.parse(drawerPlainText()).roles.planner"
            "));",
        )

        self.assertEqual(result, {"model": "provider::claude-opus-5", "effort": "high"})

    def test_the_json_preview_stays_current_after_save(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "saveChanges();"
            "console.log(JSON.stringify("
            "  JSON.parse(drawerPlainText()).roles.planner"
            "));",
        )

        self.assertEqual(result, {"model": "provider::gpt-5.6-sol", "effort": "high"})

    def test_copying_writes_the_exact_preview_json_to_the_clipboard(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            'onModelSelect("planner", "provider::gpt-5.6-sol");'
            "var copied = copyConfigToClipboard();"
            "console.log(JSON.stringify({"
            "  copied: copied,"
            "  writes: CLIPBOARD_WRITES.length,"
            "  matchesDrawer: CLIPBOARD_WRITES[0] === drawerPlainText(),"
            "  payload: JSON.parse(CLIPBOARD_WRITES[0]).roles.planner"
            "}));",
        )

        self.assertEqual(result["copied"], True)
        self.assertEqual(result["writes"], 1)
        self.assertTrue(result["matchesDrawer"])
        self.assertEqual(
            result["payload"], {"model": "provider::gpt-5.6-sol", "effort": "high"}
        )

    def test_a_successful_copy_shows_a_success_toast(self) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "copyConfigToClipboard();"
            "console.log(JSON.stringify(TOAST_CONTAINER.children.map(function (t) {"
            "  return { text: t.textContent, className: t.className };"
            "})));",
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "הקונפיגורציה הועתקה ללוח")
        self.assertEqual(result[0]["className"], "toast toast-success")

    def test_a_rejected_copy_shows_an_error_toast_but_still_records_the_attempt(
        self,
    ) -> None:
        # The attempt is recorded (`CLIPBOARD_WRITES` gains an entry) before
        # the stubbed `.then` decides which callback to invoke — a real
        # `writeText` call is committed the instant it is made; only its
        # outcome is asynchronous.
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "CLIPBOARD_SHOULD_FAIL = true;"
            "var copied = copyConfigToClipboard();"
            "console.log(JSON.stringify({"
            "  copied: copied,"
            "  writes: CLIPBOARD_WRITES.length,"
            "  toasts: TOAST_CONTAINER.children.map(function (t) {"
            "    return { text: t.textContent, className: t.className };"
            "  })"
            "}));",
        )

        self.assertEqual(result["copied"], True)
        self.assertEqual(result["writes"], 1)
        self.assertEqual(len(result["toasts"]), 1)
        self.assertEqual(result["toasts"][0]["text"], "העתקת הקונפיגורציה נכשלה")
        self.assertEqual(result["toasts"][0]["className"], "toast toast-error")

    def test_missing_clipboard_support_fails_gracefully_without_writing_anything(
        self,
    ) -> None:
        # A dashboard opened over `file://` (Implementation Decisions §4's
        # zero-friction default) has no `navigator.clipboard` at all in most
        # browsers — this must degrade to an error toast, not throw out of
        # the click handler.
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "navigator.clipboard = undefined;"
            "var copied = copyConfigToClipboard();"
            "console.log(JSON.stringify({"
            "  copied: copied,"
            "  writes: CLIPBOARD_WRITES.length,"
            "  toasts: TOAST_CONTAINER.children.map(function (t) {"
            "    return { text: t.textContent, className: t.className };"
            "  })"
            "}));",
        )

        self.assertEqual(result["copied"], False)
        self.assertEqual(result["writes"], 0)
        self.assertEqual(len(result["toasts"]), 1)
        self.assertEqual(
            result["toasts"][0]["text"], "ההעתקה ללוח אינה נתמכת בדפדפן זה"
        )
        self.assertEqual(result["toasts"][0]["className"], "toast toast-error")

    def test_clicking_the_toggle_and_copy_buttons_invokes_the_same_actions_as_calling_them(
        self,
    ) -> None:
        result = _run_embedded_script(
            _two_role_reactive_report(),
            "fireClick('config-drawer-toggle');"
            "fireClick('config-drawer-copy');"
            "console.log(JSON.stringify({"
            "  open: CONFIG_DRAWER.classList.contains('is-open'),"
            "  writes: CLIPBOARD_WRITES.length"
            "}));",
        )

        self.assertEqual(result, {"open": True, "writes": 1})


if __name__ == "__main__":
    unittest.main()
