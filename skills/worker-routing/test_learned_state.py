#!/usr/bin/env python3
"""Unit tests for `learned_state` (spec 0004 ticket 19).

Modules are loaded by path with `importlib.util.spec_from_file_location`,
the pattern every other test file in this skill directory uses: these files
are not a package.

Every test that mutates `root_dir` performs its disk-reading assertions
*inside* the `tempfile.TemporaryDirectory()` block that owns it — reading
after the block exits would read a directory that no longer exists.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("learned_state.py")
REPO_ROOT = Path(__file__).resolve().parents[2]

learned_state_spec = importlib.util.spec_from_file_location("learned_state", MODULE_PATH)
assert learned_state_spec is not None and learned_state_spec.loader is not None
learned_state = importlib.util.module_from_spec(learned_state_spec)
sys.modules["learned_state"] = learned_state
learned_state_spec.loader.exec_module(learned_state)

# Three fixed, timezone-aware instants — never used to derive a live clock
# reading, only as injected values. Spread out so successive calls in one
# test have a strictly increasing `now`, matching how a real caller would
# use this module.
_NOW = datetime(2026, 1, 8, tzinfo=timezone.utc)
_LATER = datetime(2026, 1, 9, tzinfo=timezone.utc)
_LATEST = datetime(2026, 1, 10, tzinfo=timezone.utc)


def _change(document: str, content: str):
    return learned_state.DocumentChange(document=document, content=content)  # type: ignore[arg-type]


class AdoptTests(unittest.TestCase):
    def test_adopt_on_an_empty_root_creates_v0001_and_read_current_returns_exactly_what_was_adopted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = learned_state.adopt(
                [_change("memory", "memory v1"), _change("briefs", "briefs v1")],
                root_dir=root,
                now=_NOW,
            )

            self.assertEqual(entry.kind, "adopt")
            self.assertEqual(entry.version, 1)
            self.assertIsNone(entry.replaces)
            self.assertEqual(entry.timestamp, "2026-01-08T00:00:00Z")
            self.assertEqual(
                {delta.document: (delta.before_digest, delta.after_digest) for delta in entry.documents},
                {
                    "memory": (None, hashlib.sha256(b"memory v1").hexdigest()),
                    "briefs": (None, hashlib.sha256(b"briefs v1").hexdigest()),
                },
            )

            self.assertEqual(
                learned_state.read_current(root),
                {"memory": "memory v1", "briefs": "briefs v1"},
            )
            self.assertEqual(learned_state.read_history(root), (entry,))
            self.assertEqual(
                (root / "learned-state" / "versions" / "v0001" / "memory").read_bytes(),
                b"memory v1",
            )
            self.assertEqual(
                (root / "learned-state" / "versions" / "v0001" / "briefs").read_bytes(),
                b"briefs v1",
            )

    def test_a_second_adopt_carries_forward_untouched_documents_byte_for_byte_and_records_a_delta_only_for_the_changed_one(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt(
                [_change("memory", "memory v1"), _change("briefs", "briefs v1")],
                root_dir=root,
                now=_NOW,
            )
            second = learned_state.adopt(
                [_change("memory", "memory v2")], root_dir=root, now=_LATER
            )

            self.assertEqual(second.version, 2)
            self.assertEqual(second.replaces, 1)
            self.assertEqual(len(second.documents), 1, "briefs was untouched and gets no delta")
            self.assertEqual(second.documents[0].document, "memory")
            self.assertEqual(
                second.documents[0].before_digest, hashlib.sha256(b"memory v1").hexdigest()
            )
            self.assertEqual(
                second.documents[0].after_digest, hashlib.sha256(b"memory v2").hexdigest()
            )

            self.assertEqual(
                learned_state.read_current(root),
                {"memory": "memory v2", "briefs": "briefs v1"},
            )
            briefs_v1 = (root / "learned-state" / "versions" / "v0001" / "briefs").read_bytes()
            briefs_v2 = (root / "learned-state" / "versions" / "v0002" / "briefs").read_bytes()
            self.assertEqual(briefs_v1, briefs_v2)
            self.assertEqual(briefs_v1, b"briefs v1")


class RollBackTests(unittest.TestCase):
    def test_roll_back_after_adopt_adopt_restores_v0001_byte_for_byte_and_creates_no_new_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
            learned_state.adopt([_change("memory", "v2")], root_dir=root, now=_LATER)
            v1_bytes_before = (
                root / "learned-state" / "versions" / "v0001" / "memory"
            ).read_bytes()

            entry = learned_state.roll_back(root_dir=root, now=_LATEST)

            self.assertEqual(entry.kind, "rollback")
            self.assertEqual(entry.version, 1)
            self.assertEqual(entry.replaces, 2)

            versions_dir = root / "learned-state" / "versions"
            self.assertEqual(
                sorted(p.name for p in versions_dir.iterdir()),
                ["v0001", "v0002"],
                "rollback must not create a third snapshot directory",
            )
            v1_bytes_after = (
                root / "learned-state" / "versions" / "v0001" / "memory"
            ).read_bytes()
            self.assertEqual(v1_bytes_after, v1_bytes_before)
            self.assertEqual(v1_bytes_after, b"v1")

            self.assertEqual(
                learned_state.current_version_dir(root), root / "learned-state" / "versions" / "v0001"
            )
            self.assertEqual(learned_state.read_current(root), {"memory": "v1"})

    def test_roll_back_twice_after_three_adopts_lands_on_v0001(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
            learned_state.adopt(
                [_change("memory", "v2")], root_dir=root, now=_NOW + timedelta(hours=1)
            )
            learned_state.adopt(
                [_change("memory", "v3")], root_dir=root, now=_NOW + timedelta(hours=2)
            )

            first_rollback = learned_state.roll_back(
                root_dir=root, now=_NOW + timedelta(hours=3)
            )
            second_rollback = learned_state.roll_back(
                root_dir=root, now=_NOW + timedelta(hours=4)
            )

            self.assertEqual(first_rollback.version, 2)
            self.assertEqual(second_rollback.version, 1)
            self.assertEqual(learned_state.read_current(root), {"memory": "v1"})
            self.assertEqual(
                learned_state.current_version_dir(root), root / "learned-state" / "versions" / "v0001"
            )

    def test_roll_back_with_only_one_adoption_raises_naming_why(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)

            with self.assertRaises(ValueError) as ctx:
                learned_state.roll_back(root_dir=root, now=_LATER)

            self.assertIn("first adoption", str(ctx.exception))
            self.assertEqual(len(learned_state.read_history(root)), 1, "the refusal writes nothing")

    def test_roll_back_delta_shows_a_document_absent_from_the_target_version_as_removed(
        self,
    ) -> None:
        """`routing_table` did not exist in v0001 — rolling back v0002 must
        report it as *removed* (`after_digest=None`), not merely changed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
            learned_state.adopt([_change("routing_table", "rt-v1")], root_dir=root, now=_LATER)

            entry = learned_state.roll_back(root_dir=root, now=_LATEST)

            self.assertEqual(len(entry.documents), 1)
            delta = entry.documents[0]
            self.assertEqual(delta.document, "routing_table")
            self.assertIsNotNone(delta.before_digest)
            self.assertIsNone(delta.after_digest)
            current = learned_state.read_current(root)
            self.assertEqual(current, {"memory": "v1"})
            self.assertNotIn("routing_table", current)


class NonUtcNowTests(unittest.TestCase):
    def test_a_non_utc_now_is_stamped_in_utc_on_the_wire(self) -> None:
        """Every other test injects a `now` that is already UTC, which
        leaves `_wire_timestamp`'s `.astimezone(timezone.utc)` conversion a
        no-op — dropping it would still pass every other test. `+02:00` at
        10:00 is `08:00Z`; if the raw local wall-clock value leaked onto the
        wire instead, this would read `10:00:00Z`."""
        non_utc_now = datetime(2026, 1, 8, 10, 0, tzinfo=timezone(timedelta(hours=2)))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = learned_state.adopt(
                [_change("memory", "v1")], root_dir=root, now=non_utc_now
            )

            self.assertEqual(entry.timestamp, "2026-01-08T08:00:00Z")
            self.assertEqual(
                learned_state.read_history(root)[-1].timestamp, "2026-01-08T08:00:00Z"
            )


class RollBackHistoryCorruptionTests(unittest.TestCase):
    """The two refusals that depend on `history.jsonl`'s exact shape rather
    than on how many `adopt`/`roll_back` calls were made."""

    def test_rolling_back_an_empty_root_raises_a_message_distinct_from_the_first_adoption_case(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError) as ctx:
                learned_state.roll_back(root_dir=root, now=_NOW)

        self.assertIn("no version has ever been adopted", str(ctx.exception))

    def test_rolling_back_a_hand_corrupted_history_where_every_adoption_is_already_undone_raises(
        self,
    ) -> None:
        """Not reachable through `adopt`/`roll_back` themselves — a
        successful rollback can never consume the first adoption, so at
        least one live adoption always remains through this module's own two
        writers. Reachable only by hand-editing `history.jsonl`, which is
        exactly what this test does: it appends a second, illegitimate
        `"rollback"` line directly, bypassing `roll_back` (which would
        refuse to produce it)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)

            history_path = root / "learned-state" / "history.jsonl"
            with open(history_path, "a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "kind": "rollback",
                            "version": 1,
                            "replaces": 1,
                            "documents": [],
                            "change_id": None,
                            "timestamp": "2026-01-09T00:00:00Z",
                        }
                    )
                    + "\n"
                )

            with self.assertRaises(ValueError) as ctx:
                learned_state.roll_back(root_dir=root, now=_LATER)

        self.assertIn("every adoption has already been undone", str(ctx.exception))


class VersionNumberingTests(unittest.TestCase):
    def test_adopt_after_a_rollback_allocates_a_new_version_number_rather_than_reusing_the_undone_one(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
            learned_state.adopt(
                [_change("memory", "v2")], root_dir=root, now=_NOW + timedelta(hours=1)
            )
            learned_state.roll_back(root_dir=root, now=_NOW + timedelta(hours=2))

            third = learned_state.adopt(
                [_change("memory", "v3")], root_dir=root, now=_NOW + timedelta(hours=3)
            )

            self.assertEqual(
                third.version,
                3,
                "must not reuse v0002's number even though it is no longer current",
            )
            self.assertEqual(
                (root / "learned-state" / "versions" / "v0002" / "memory").read_bytes(),
                b"v2",
                "v0002 is undone, not deleted or rewritten (Decision 2)",
            )
            self.assertEqual(
                (root / "learned-state" / "versions" / "v0003" / "memory").read_bytes(), b"v3"
            )

    def test_write_snapshot_raises_a_value_error_rather_than_overwriting_an_existing_version_directory(
        self,
    ) -> None:
        """`_write_snapshot`'s own `mkdir(..., exist_ok=False)` integrity
        check, exercised directly against the private function for the one
        case that reaches it this way: a version-numbering bug in this
        module.

        Tampering with a `vNNNN` directory that *does* match
        `_VERSION_DIRNAME_RE` is deliberately **not** named here, though an
        earlier draft of this docstring did: `_highest_version_on_disk`
        counts any matching directory, so `adopt` allocates past a
        fabricated one instead of colliding with it (see the module
        docstring's Decision 2, which rules this out explicitly).

        That is also why this particular directory-already-a-directory
        collision is not reachable through the public `adopt()`: it
        allocates one past the highest version number used by *either*
        `history.jsonl` *or* a `vNNNN` directory actually present under
        `versions/` (see `OrphanedSnapshotRecoveryTests` below), so handing
        `adopt` a stray directory at the number it would otherwise have
        picked no longer collides — it gets skipped instead. So this test
        calls `_write_snapshot` directly, the same way a numbering bug
        would reach it.

        `test_a_file_named_like_a_version_directory_makes_adopt_raise_a_value_error`
        below drives this same underlying check through the *public* path —
        a stray file, not directory, which `_highest_version_on_disk` never
        sees. Either way the collision now raises `ValueError`, not a bare
        `FileExistsError` naming an absolute path with no guidance."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            colliding_dir = root / "learned-state" / "versions" / "v0001"
            colliding_dir.mkdir(parents=True)

            with self.assertRaises(ValueError) as ctx:
                learned_state._write_snapshot(root, 1, {"memory": "v1"})

            self.assertIn("already exists", str(ctx.exception))
            self.assertIn(str(colliding_dir), str(ctx.exception))

    def test_a_file_named_like_a_version_directory_makes_adopt_raise_a_value_error(
        self,
    ) -> None:
        """Confirmed by running the code, not merely by reading it:
        `_highest_version_on_disk` skips any `versions/` entry that is not a
        directory (`if not entry.is_dir(): continue`), so a plain **file**
        named `v0001` is invisible to it. On an empty root `adopt` then
        allocates version 1, exactly as it would with no stray entry at all,
        and `_write_snapshot`'s `mkdir` collides with the file — reachable
        through the public `adopt()`, unlike the directory-collision case
        above. Before this fix a bare `FileExistsError` escaped here with no
        guidance, and every later `adopt` call recomputed the identical
        colliding version number forever (the file is never a match for
        `_VERSION_DIRNAME_RE`'s directory scan, so nothing about the
        allocation ever changes). This test asserts the module's actual
        contract now: a `ValueError` naming the offending path, and no
        partial write."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            versions_dir = root / "learned-state" / "versions"
            versions_dir.mkdir(parents=True)
            stray_file = versions_dir / "v0001"
            stray_file.write_bytes(b"not a directory")

            with self.assertRaises(ValueError) as ctx:
                learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)

            self.assertIn("already exists", str(ctx.exception))
            self.assertIn(str(stray_file), str(ctx.exception))
            self.assertEqual(
                learned_state.read_history(root),
                (),
                "the refusal must write no history line",
            )
            self.assertEqual(
                stray_file.read_bytes(),
                b"not a directory",
                "the stray file must be left untouched",
            )

    def test_a_symlink_to_a_file_collides_but_a_symlink_to_a_directory_does_not(
        self,
    ) -> None:
        """The discriminator is `entry.is_dir()`, not "file versus
        directory" — and `is_dir()` follows symlinks.

        The module docstring's Decision 2 states the property that defeats
        self-healing (an entry the scan cannot count, because `is_dir()` is
        false *or* the name misses the case-sensitive regex) rather than
        listing instances, because three earlier drafts listed instances and
        every one of them undercounted — symlinks were the case they all
        missed. This test is what stops that claim from being prose nobody
        checked: both halves of the symlink case are driven here, and they
        land on opposite sides.

        A symlink pointing at a file fails `is_dir()`, so the scan skips it
        and `adopt` collides exactly as it does with a plain stray file. A
        symlink pointing at a *directory* passes `is_dir()`, is counted like
        any real version directory, and `adopt` allocates past it instead.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            versions_dir = root / "learned-state" / "versions"
            versions_dir.mkdir(parents=True)
            target_file = root / "target-file"
            target_file.write_bytes(b"not a directory")
            (versions_dir / "v0001").symlink_to(target_file)

            with self.assertRaises(ValueError) as ctx:
                learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
            self.assertIn("already exists", str(ctx.exception))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            versions_dir = root / "learned-state" / "versions"
            versions_dir.mkdir(parents=True)
            target_dir = root / "target-dir"
            target_dir.mkdir()
            (versions_dir / "v0001").symlink_to(target_dir, target_is_directory=True)

            entry = learned_state.adopt(
                [_change("memory", "v1")], root_dir=root, now=_NOW
            )
            self.assertEqual(
                entry.version,
                2,
                "a symlinked directory passes is_dir(), so the scan counts it "
                "as version 1 and adopt must allocate past it",
            )


class OrphanedSnapshotRecoveryTests(unittest.TestCase):
    """Simulates a crash between `_write_snapshot` and `_append_history`:
    `_write_snapshot` finishes writing a version's directory but the process
    dies before `adopt` appends that version's `history.jsonl` line, leaving
    an orphaned snapshot `history.jsonl` has no record of at all.

    Confirmed by experiment before this fix: allocating the next version
    number from `history.jsonl` alone recomputes the exact same, already-
    occupied number on every subsequent call, so every `adopt` afterward —
    not just the first — died with `FileExistsError` forever. The fix reads
    the highest version number from *both* `history.jsonl` and whatever
    `vNNNN` directories are actually present under `versions/`, so the
    orphan is skipped rather than collided with, and the store self-heals.
    """

    def _seed_with_orphan(self, root: Path) -> None:
        learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
        # Simulate the crash: `_write_snapshot` finished v0002's directory,
        # but `adopt` never reached `_append_history` for it.
        orphan_dir = root / "learned-state" / "versions" / "v0002"
        orphan_dir.mkdir(parents=True)
        (orphan_dir / "memory").write_bytes(b"orphaned, never recorded")

    def test_adopt_after_an_orphaned_snapshot_allocates_past_it_and_succeeds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_with_orphan(root)

            entry = learned_state.adopt(
                [_change("memory", "v2-recovered")], root_dir=root, now=_LATER
            )

            self.assertEqual(entry.version, 3, "must allocate past the orphaned v0002")
            self.assertEqual(entry.replaces, 1)
            self.assertEqual(learned_state.read_current(root), {"memory": "v2-recovered"})
            self.assertEqual(len(learned_state.read_history(root)), 2)
            self.assertEqual(
                (root / "learned-state" / "versions" / "v0002" / "memory").read_bytes(),
                b"orphaned, never recorded",
                "the orphan is left in place for forensics, not deleted",
            )

    def test_a_second_adopt_after_orphan_recovery_still_succeeds(self) -> None:
        """The property whose absence made the bug permanent: not just the
        one `adopt` immediately after an orphan, but every one after that."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_with_orphan(root)
            learned_state.adopt(
                [_change("memory", "v2-recovered")], root_dir=root, now=_LATER
            )

            third = learned_state.adopt([_change("memory", "v3")], root_dir=root, now=_LATEST)

            self.assertEqual(third.version, 4)
            self.assertEqual(third.replaces, 3)
            self.assertEqual(learned_state.read_current(root), {"memory": "v3"})
            self.assertEqual(len(learned_state.read_history(root)), 3)


class NoOpAdoptTests(unittest.TestCase):
    def test_adopting_content_identical_to_the_current_version_is_refused_and_writes_nothing(
        self,
    ) -> None:
        """Confirmed by experiment before this fix: adopting content byte-
        for-byte identical to what the current version already holds
        produced `version=2`, `documents=()`, a second `history.jsonl` line,
        and a second on-disk directory — the same "nothing" `_validate_changes`
        already refuses for an empty `changes` list, merely expressed a
        different way, and it went completely unrefused."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "same")], root_dir=root, now=_NOW)

            with self.assertRaises(ValueError) as ctx:
                learned_state.adopt([_change("memory", "same")], root_dir=root, now=_LATER)

            self.assertIn("no actual difference", str(ctx.exception))
            self.assertEqual(
                len(learned_state.read_history(root)), 1, "the refusal writes nothing"
            )
            versions_dir = root / "learned-state" / "versions"
            self.assertEqual(
                sorted(p.name for p in versions_dir.iterdir()),
                ["v0001"],
                "the refusal must not create a second snapshot directory",
            )

    def test_a_no_op_adopt_that_also_carries_forward_other_documents_is_refused(
        self,
    ) -> None:
        """A no-op is a property of the whole new document map versus the
        whole previous one, not of the single `DocumentChange` a caller
        passed — carrying forward an untouched second document must not
        mask that the named change itself was a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt(
                [_change("memory", "m1"), _change("briefs", "b1")], root_dir=root, now=_NOW
            )

            with self.assertRaises(ValueError):
                learned_state.adopt([_change("memory", "m1")], root_dir=root, now=_LATER)

            self.assertEqual(len(learned_state.read_history(root)), 1)


class AdoptValidationTests(unittest.TestCase):
    """Every guard `adopt` runs before touching disk. Each test seeds one
    real adoption first and confirms the store is unchanged after the
    refused call — an empty root would let "unchanged" mean "still empty",
    which proves nothing about whether the refusal ran before or after a
    write."""

    def _seeded_root(self, tmp: str) -> Path:
        root = Path(tmp)
        learned_state.adopt([_change("memory", "seed")], root_dir=root, now=_NOW)
        return root

    def _assert_store_still_only_has_v0001(self, root: Path) -> None:
        self.assertEqual(len(learned_state.read_history(root)), 1)
        versions_dir = root / "learned-state" / "versions"
        self.assertEqual(sorted(p.name for p in versions_dir.iterdir()), ["v0001"])

    def test_an_unknown_document_name_is_refused_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seeded_root(tmp)
            with self.assertRaises(ValueError):
                learned_state.adopt(
                    [_change("protocol", "x")], root_dir=root, now=_LATER
                )
            self._assert_store_still_only_has_v0001(root)

    def test_non_string_content_is_refused_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seeded_root(tmp)
            with self.assertRaises(ValueError):
                learned_state.adopt(
                    [_change("memory", 123)], root_dir=root, now=_LATER  # type: ignore[arg-type]
                )
            self._assert_store_still_only_has_v0001(root)

    def test_empty_changes_is_refused_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seeded_root(tmp)
            with self.assertRaises(ValueError):
                learned_state.adopt([], root_dir=root, now=_LATER)
            self._assert_store_still_only_has_v0001(root)

    def test_a_naive_now_is_refused_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seeded_root(tmp)
            with self.assertRaises(ValueError):
                learned_state.adopt(
                    [_change("memory", "v2")],
                    root_dir=root,
                    now=datetime(2026, 1, 9),  # noqa: DTZ001 - the value under test
                )
            self._assert_store_still_only_has_v0001(root)

    def test_a_malformed_change_id_is_refused_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seeded_root(tmp)
            with self.assertRaises(ValueError):
                learned_state.adopt(
                    [_change("memory", "v2")],
                    root_dir=root,
                    now=_LATER,
                    change_id="bad id with spaces",
                )
            self._assert_store_still_only_has_v0001(root)

    def test_two_changes_naming_the_same_document_in_one_call_is_refused_and_writes_nothing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seeded_root(tmp)
            with self.assertRaises(ValueError):
                learned_state.adopt(
                    [_change("memory", "a"), _change("memory", "b")],
                    root_dir=root,
                    now=_LATER,
                )
            self._assert_store_still_only_has_v0001(root)

    def test_a_bare_string_as_changes_is_refused_rather_than_iterated_as_characters(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seeded_root(tmp)
            with self.assertRaises(ValueError):
                learned_state.adopt("memory", root_dir=root, now=_LATER)  # type: ignore[arg-type]
            self._assert_store_still_only_has_v0001(root)

    def test_a_non_iterable_changes_is_refused_with_a_clean_value_error(self) -> None:
        """Distinct from the bare-string case above, which — even with the
        list/tuple type check deleted — still gets rejected one layer down,
        by the per-item `DocumentChange` check, since iterating a string
        yields characters that are never `DocumentChange` instances. An
        `int` has no such fallback: without the type check, `len(42)` itself
        raises `TypeError`, which is not a `ValueError` and so is exactly
        what `assertRaises(ValueError)` here catches only when the type
        check is doing its job."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seeded_root(tmp)
            with self.assertRaises(ValueError):
                learned_state.adopt(42, root_dir=root, now=_LATER)  # type: ignore[arg-type]
            self._assert_store_still_only_has_v0001(root)

    def test_a_list_item_that_is_not_a_document_change_is_refused(self) -> None:
        """Distinct from the unknown-document-name test above: this bad
        value never reaches `DocumentChange.__post_init__` at all, so it
        exercises `_validate_changes`'s own type check on each item."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seeded_root(tmp)
            with self.assertRaises(ValueError):
                learned_state.adopt(
                    [{"document": "memory", "content": "x"}],  # type: ignore[list-item]
                    root_dir=root,
                    now=_LATER,
                )
            self._assert_store_still_only_has_v0001(root)


class RollBackValidationTests(unittest.TestCase):
    def _two_versions(self, tmp: str) -> Path:
        root = Path(tmp)
        learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
        learned_state.adopt([_change("memory", "v2")], root_dir=root, now=_LATER)
        return root

    def test_a_naive_now_is_refused_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._two_versions(tmp)
            with self.assertRaises(ValueError):
                learned_state.roll_back(
                    root_dir=root, now=datetime(2026, 1, 10)  # noqa: DTZ001 - value under test
                )
            self.assertEqual(len(learned_state.read_history(root)), 2)

    def test_a_malformed_change_id_is_refused_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._two_versions(tmp)
            with self.assertRaises(ValueError):
                learned_state.roll_back(
                    root_dir=root, now=_LATEST, change_id="bad id with spaces"
                )
            self.assertEqual(len(learned_state.read_history(root)), 2)


class ChangeIdWiringTests(unittest.TestCase):
    """Neither test above passes a non-default `change_id`. Without these,
    `change_id` could be dropped from either function's signature (or from
    `_entry_to_mapping`'s serialization) with the whole suite staying
    green."""

    def test_adopt_records_the_caller_supplied_change_id_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = learned_state.adopt(
                [_change("memory", "v1")], root_dir=root, now=_NOW, change_id="change-001"
            )

            self.assertEqual(entry.change_id, "change-001")
            self.assertEqual(learned_state.read_history(root)[-1].change_id, "change-001")

    def test_roll_back_records_the_caller_supplied_change_id_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
            learned_state.adopt([_change("memory", "v2")], root_dir=root, now=_LATER)

            entry = learned_state.roll_back(
                root_dir=root, now=_LATEST, change_id="rollback-001"
            )

            self.assertEqual(entry.change_id, "rollback-001")
            self.assertEqual(learned_state.read_history(root)[-1].change_id, "rollback-001")


class ReadCurrentTests(unittest.TestCase):
    def test_read_current_on_an_empty_root_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(learned_state.read_current(Path(tmp)), {})

    def test_read_current_ignores_a_stray_file_in_the_version_directory(self) -> None:
        """`_read_documents` iterates the closed `LEARNED_DOCUMENTS`
        vocabulary rather than the directory's own listing. A directory
        listing would either surface the stray key or crash decoding its
        non-UTF-8 bytes as document content."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
            stray = root / "learned-state" / "versions" / "v0001" / "stray-file.txt"
            stray.write_bytes(b"\xff\xfe not valid utf-8")

            self.assertEqual(learned_state.read_current(root), {"memory": "v1"})


class ReadHistoryTests(unittest.TestCase):
    def test_read_history_on_an_empty_root_returns_an_empty_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(learned_state.read_history(Path(tmp)), ())

    def test_read_history_raises_on_a_malformed_line_rather_than_skipping_it(self) -> None:
        """Unlike `learning_journal.read_journal`, this reader has exactly
        one writer (this module itself) and so is not required to tolerate
        damage — see the module docstring."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "learned-state" / "history.jsonl"
            history_path.parent.mkdir(parents=True)
            history_path.write_text("not json at all\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                learned_state.read_history(root)

    def test_read_history_raises_on_a_documents_field_that_is_not_a_list_on_the_wire(
        self,
    ) -> None:
        """`documents` is a JSON array on every real wire record
        (`_entry_to_mapping` always emits a list). A line whose `documents`
        is some other JSON type is damage, and must be refused with a clean
        `ValueError` rather than whatever incidental exception iterating the
        wrong type happens to raise (a `TypeError` from subscripting a
        string or a `dict`, for instance) — `assertRaises(ValueError)` below
        only passes if the guard produces that clean rejection."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "learned-state" / "history.jsonl"
            history_path.parent.mkdir(parents=True)
            history_path.write_text(
                json.dumps(
                    {
                        "kind": "adopt",
                        "version": 1,
                        "replaces": None,
                        "documents": "not-a-list",
                        "change_id": None,
                        "timestamp": "2026-01-08T00:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                learned_state.read_history(root)

    def test_read_history_raises_a_value_error_on_a_line_missing_the_kind_key(self) -> None:
        """Confirmed by experiment before this fix: `_mapping_to_entry` read
        `mapping["kind"]` directly, so a line missing that key raised the
        incidental `KeyError: 'kind'` instead of this module's one rejection
        contract (`ValueError`, the same contract every other validator in
        this file uses)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "learned-state" / "history.jsonl"
            history_path.parent.mkdir(parents=True)
            history_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "replaces": None,
                        "documents": [],
                        "change_id": None,
                        "timestamp": "2026-01-08T00:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                learned_state.read_history(root)

    def test_read_history_raises_a_value_error_on_a_documents_item_that_is_a_bare_string(
        self,
    ) -> None:
        """Confirmed by experiment before this fix: `_mapping_to_entry`
        subscripted each `documents` item as `item["document"]` with no
        shape check first, so a string item (a plausible corruption — a
        document *name* landing in the list instead of a delta object)
        raised the incidental `TypeError: string indices must be integers`
        instead of `ValueError`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "learned-state" / "history.jsonl"
            history_path.parent.mkdir(parents=True)
            history_path.write_text(
                json.dumps(
                    {
                        "kind": "adopt",
                        "version": 1,
                        "replaces": None,
                        "documents": ["memory"],
                        "change_id": None,
                        "timestamp": "2026-01-08T00:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                learned_state.read_history(root)

    def test_read_history_raises_a_value_error_on_a_documents_item_missing_a_required_key(
        self,
    ) -> None:
        """Distinct from the bare-string case above: this item is an object,
        just an incomplete one, so it exercises `_check_required_keys`
        against a `documents` item rather than the type check ahead of it.

        `before_digest` is entirely absent here, not merely `null` — chosen
        so the fix is the only thing that can catch it. A prior version of
        this test used a missing `after_digest` alongside a `null`
        `before_digest`, which happened to leave both digests `None` and so
        was already refused by `DocumentDelta`'s own "not an actual change"
        check — a coincidence that would have let this test pass with the
        required-key fix reverted. `before_digest=None, after_digest=<a real
        digest>` is a legitimate combination (a document adopted for the
        first time), so nothing else in this module rejects it; only a
        required-key check on the missing wire key does."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "learned-state" / "history.jsonl"
            history_path.parent.mkdir(parents=True)
            history_path.write_text(
                json.dumps(
                    {
                        "kind": "adopt",
                        "version": 1,
                        "replaces": None,
                        "documents": [{"document": "memory", "after_digest": "a" * 64}],
                        "change_id": None,
                        "timestamp": "2026-01-08T00:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                learned_state.read_history(root)


class CurrentVersionDirTests(unittest.TestCase):
    def test_current_version_dir_is_none_on_an_empty_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(learned_state.current_version_dir(Path(tmp)))

    def test_current_version_dir_resolves_to_the_latest_history_entrys_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
            second = learned_state.adopt([_change("memory", "v2")], root_dir=root, now=_LATER)

            self.assertEqual(second.version, 2)
            self.assertEqual(
                learned_state.current_version_dir(root),
                root / "learned-state" / "versions" / "v0002",
            )


class RootIsolationTests(unittest.TestCase):
    def test_all_writes_land_under_the_injected_root_and_nowhere_else(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_str,
            tempfile.TemporaryDirectory() as outside_str,
            tempfile.TemporaryDirectory() as cwd_str,
        ):
            root = Path(root_str)
            outside = Path(outside_str)
            cwd = Path(cwd_str)
            original_cwd = Path.cwd()
            os.chdir(cwd)
            try:
                learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
                learned_state.adopt([_change("memory", "v2")], root_dir=root, now=_LATER)
                learned_state.roll_back(root_dir=root, now=_LATEST)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(list(outside.iterdir()), [], "nothing was written outside root_dir")
            self.assertEqual(
                list(cwd.iterdir()), [], "nothing was written relative to the caller's cwd"
            )
            # The root itself did get written to, so the isolation above is
            # not simply "nothing was written anywhere".
            self.assertTrue((root / "learned-state").is_dir())


class GitTrackingTests(unittest.TestCase):
    def test_learned_state_directory_is_not_gitignored(self) -> None:
        """Decision 1: "git-tracked" depends on `learned-state/` never being
        matched by the real repository's `.gitignore`. Checked against the
        actual `.gitignore`, not merely asserted in prose, so a future
        ignore rule that accidentally shadows this path fails a test instead
        of silently breaking the git-tracking guarantee."""
        probe = learned_state.LEARNED_STATE_RELATIVE_PATH / "versions" / "v0001" / "memory"
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(probe)],
            cwd=REPO_ROOT,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            1,
            "learned-state/ must not be gitignored (git check-ignore exits 1 for a "
            "path that is not ignored, 0 for one that is)",
        )


class DocumentChangeValidationTests(unittest.TestCase):
    def test_rejects_an_unknown_document(self) -> None:
        with self.assertRaises(ValueError):
            _change("protocol", "x")

    def test_rejects_non_string_content(self) -> None:
        with self.assertRaises(ValueError):
            learned_state.DocumentChange(document="memory", content=123)  # type: ignore[arg-type]


class DocumentDeltaValidationTests(unittest.TestCase):
    def test_rejects_an_unknown_document(self) -> None:
        with self.assertRaises(ValueError):
            learned_state.DocumentDelta(
                document="protocol", before_digest=None, after_digest="a" * 64  # type: ignore[arg-type]
            )

    def test_rejects_a_malformed_digest(self) -> None:
        with self.assertRaises(ValueError):
            learned_state.DocumentDelta(
                document="memory", before_digest="not-a-hex-digest", after_digest=None
            )

    def test_rejects_equal_before_and_after_digests(self) -> None:
        digest = "a" * 64
        with self.assertRaises(ValueError):
            learned_state.DocumentDelta(
                document="memory", before_digest=digest, after_digest=digest
            )

    def test_rejects_two_none_digests(self) -> None:
        with self.assertRaises(ValueError):
            learned_state.DocumentDelta(document="memory", before_digest=None, after_digest=None)


class VersionEntryValidationTests(unittest.TestCase):
    def _valid_kwargs(self) -> dict[str, object]:
        return {
            "kind": "adopt",
            "version": 1,
            "replaces": None,
            "documents": (),
            "change_id": None,
            "timestamp": "2026-01-08T00:00:00Z",
        }

    def test_rejects_an_unknown_kind(self) -> None:
        kwargs = self._valid_kwargs()
        kwargs["kind"] = "amend"
        with self.assertRaises(ValueError):
            learned_state.VersionEntry(**kwargs)  # type: ignore[arg-type]

    def test_rejects_a_non_positive_or_boolean_version(self) -> None:
        for bad in (0, -1, True):
            with self.subTest(version=bad):
                kwargs = self._valid_kwargs()
                kwargs["version"] = bad
                with self.assertRaises(ValueError):
                    learned_state.VersionEntry(**kwargs)  # type: ignore[arg-type]

    def test_rejects_a_non_positive_or_boolean_replaces(self) -> None:
        for bad in (0, -1, True):
            with self.subTest(replaces=bad):
                kwargs = self._valid_kwargs()
                kwargs["replaces"] = bad
                with self.assertRaises(ValueError):
                    learned_state.VersionEntry(**kwargs)  # type: ignore[arg-type]

    def test_rejects_documents_that_is_not_a_tuple(self) -> None:
        kwargs = self._valid_kwargs()
        kwargs["documents"] = []
        with self.assertRaises(ValueError):
            learned_state.VersionEntry(**kwargs)  # type: ignore[arg-type]

    def test_rejects_a_tuple_item_that_is_not_a_document_delta(self) -> None:
        kwargs = self._valid_kwargs()
        kwargs["documents"] = (1, 2)
        with self.assertRaises(ValueError):
            learned_state.VersionEntry(**kwargs)  # type: ignore[arg-type]

    def test_rejects_a_malformed_change_id(self) -> None:
        kwargs = self._valid_kwargs()
        kwargs["change_id"] = "bad id with spaces"
        with self.assertRaises(ValueError):
            learned_state.VersionEntry(**kwargs)  # type: ignore[arg-type]

    def test_rejects_a_timestamp_with_the_wrong_shape(self) -> None:
        """`datetime.strptime` also rejects `"not-a-timestamp"` on its own,
        so asserting only `ValueError` here would still pass with the shape
        check deleted — the calendar check's `except` clause would catch
        strptime's own failure and re-raise a *different*, misleading
        message ("names no real instant") for input that in fact never
        matched the wire shape at all. Asserting on the message is what
        tells the two apart."""
        kwargs = self._valid_kwargs()
        kwargs["timestamp"] = "not-a-timestamp"
        with self.assertRaises(ValueError) as ctx:
            learned_state.VersionEntry(**kwargs)  # type: ignore[arg-type]
        self.assertIn("must match", str(ctx.exception))

    def test_rejects_a_timestamp_that_matches_the_shape_but_names_no_real_instant(
        self,
    ) -> None:
        kwargs = self._valid_kwargs()
        kwargs["timestamp"] = "2026-13-45T99:99:99Z"
        with self.assertRaises(ValueError) as ctx:
            learned_state.VersionEntry(**kwargs)  # type: ignore[arg-type]
        self.assertIn("names no real instant", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
