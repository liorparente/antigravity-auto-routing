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
import shutil
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

    def test_read_history_raises_a_value_error_on_a_line_that_is_valid_json_but_not_an_object(
        self,
    ) -> None:
        """The outer case of a guard the inner case already had.

        `_mapping_to_entry` checked that each `documents` *item* was a
        `Mapping` from the day it was written, but never checked the line
        itself. Confirmed by experiment before this fix: every one of these
        shapes is valid JSON, so `json.loads` succeeded and
        `_check_required_keys` then died on `mapping.keys()` with an
        `AttributeError` — a third incidental exception type escaping this
        module's one rejection contract, after the `KeyError` and
        `TypeError` already closed above. Seven review rounds passed over
        this reader without noticing, because every corrupted-line test
        written for it had until now started from a JSON *object*.
        """
        for line in ("null", "42", '"a string"', "true", "[1, 2, 3]"):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                history_path = root / "learned-state" / "history.jsonl"
                history_path.parent.mkdir(parents=True)
                history_path.write_text(line + "\n", encoding="utf-8")

                with self.assertRaises(ValueError) as ctx:
                    learned_state.read_history(root)
                self.assertIn("must be an object on the wire", str(ctx.exception))

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

    def test_the_lock_file_is_gitignored(self) -> None:
        """The other half of the same rule, and the half nothing checked.

        `_exclusive_store_lock`'s mutex lives inside the very directory the
        test above requires stay tracked. Only "content must be tracked" was
        asserted, so deleting the `.gitignore` line would have left an empty
        coordination file eligible for commit with the whole suite green.
        """
        probe = learned_state.LEARNED_STATE_RELATIVE_PATH / ".lock"
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(probe)],
            cwd=REPO_ROOT,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            "learned-state/.lock must be gitignored: it is process coordination "
            "holding no content, inside a directory that is otherwise tracked",
        )


class ConcurrentAdoptTests(unittest.TestCase):
    """Two processes adopting different documents at once must both survive.

    Every other test in this file is single-process, which is exactly why
    this gap lasted: the store looked safe because `_append_jsonl_locked`
    holds a lock, but that lock covers only the final append, and the append
    is not what needed protecting. `adopt` *decides* what to write from a
    read of the store — current version, its documents, highest version
    used — so two processes interleaving inside that read-decide-write
    window could both return successfully while one of the two changes was
    simply absent from the version left current.

    Reproduced before the fix with real OS processes: 6 trials out of 6 lost
    a committed write. `_exclusive_store_lock` now spans the whole critical
    section and the same reproduction loses none.

    Subprocesses rather than threads, because the lock is `flock` and the
    bug is between processes; the GIL would hide it.
    """

    _ADOPT_SNIPPET = (
        "import sys; sys.path.insert(0, {skill!r});\n"
        "import learned_state as ls;\n"
        "from datetime import datetime, timezone;\n"
        "from pathlib import Path;\n"
        "ls.adopt([ls.DocumentChange(document={doc!r}, content={content!r})],"
        " root_dir=Path({root!r}),"
        " now=datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc))\n"
    )

    def test_two_processes_adopting_different_documents_lose_neither_change(self) -> None:
        skill_dir = str(Path(__file__).resolve().parent)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt(
                [_change("memory", "seed-m"), _change("briefs", "seed-b")],
                root_dir=root,
                now=_NOW,
            )

            processes = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        self._ADOPT_SNIPPET.format(
                            skill=skill_dir, doc=doc, content=content, root=str(root)
                        ),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for doc, content in (("memory", "M2"), ("briefs", "B2"))
            ]
            outcomes = [(p.wait(timeout=60), p.communicate()[1]) for p in processes]

            for code, stderr in outcomes:
                self.assertEqual(
                    code, 0, f"a concurrent adopt failed: {stderr.decode('utf-8')[-400:]}"
                )
            self.assertEqual(
                learned_state.read_current(root),
                {"memory": "M2", "briefs": "B2"},
                "one process's committed change was lost by the other's stale read",
            )


class FilesystemDamageTests(unittest.TestCase):
    """Every path level, every entry point: an `OSError` is a `ValueError`.

    Six review rounds found this same defect six times, each at whatever
    path level that round happened to look at — a document, a version
    directory, the lock file, the lock file's parent, `versions/`,
    `learned-state/` itself — and each fix closed one level while the next
    round found the one above it. The enumeration was not going to
    terminate: seven paths under `root_dir`, each able to be absent,
    occupied by the wrong kind of node, or unreadable, through four public
    entry points.

    So `_filesystem_damage_is_a_value_error` states the property instead,
    and this class drives the cells that were still bare when it was
    written. Two failures are deliberately *not* damage and are asserted
    elsewhere: a missing `history.jsonl` (an empty store) and a version
    directory that already exists (`_write_snapshot`'s collision, which has
    its own remedy).
    """

    def test_a_version_entry_whose_target_is_unreachable_refuses_rather_than_undercounting(
        self,
    ) -> None:
        """`read_history` cannot shield the version scan from this one.

        Round 15's argument for leaving `entry.is_dir()` alone was that
        `read_history` fails first on the same fault. That holds only when
        the fault covers `learned-state/` as a whole — `read_history` never
        touches `versions/`, so a fault scoped to a single entry under it is
        invisible until the scan reaches it. A `v0009` symlink into a
        mode-000 tree made `is_dir()` answer `False`, so the scan returned a
        highest-version number too low with nothing raised.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            versions = root / "learned-state" / "versions"
            versions.mkdir(parents=True)
            (versions / "v0003").mkdir()
            blocked = root / "blocked"
            blocked.mkdir()
            (blocked / "real").mkdir()
            (versions / "v0009").symlink_to(blocked / "real", target_is_directory=True)
            blocked.chmod(0o000)
            try:
                self.assertEqual(
                    learned_state.read_history(root), (), "history is readable here"
                )
                with self.assertRaises(ValueError) as ctx:
                    learned_state._highest_version_on_disk(root)
                self.assertIn("damaged or unusable", str(ctx.exception))
            finally:
                blocked.chmod(0o755)

    def test_an_unknowable_versions_directory_refuses_rather_than_counting_zero(
        self,
    ) -> None:
        """The container half of the test below, left behind when the
        per-entry check was converted two lines away.

        `versions/` symlinked behind an unreadable ancestor made
        `Path.is_dir()` answer `False`, so the scan returned 0 — "no version
        has ever been written" — when the true answer is "I cannot tell".
        Same mistake as the entry-level one, one level up, which is the
        fifth path level this module has made it at.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "learned-state").mkdir(parents=True)
            blocked = root / "blocked"
            blocked.mkdir()
            (blocked / "versions").mkdir()
            (root / "learned-state" / "versions").symlink_to(
                blocked / "versions", target_is_directory=True
            )
            blocked.chmod(0o000)
            try:
                with self.assertRaises(ValueError) as ctx:
                    learned_state._highest_version_on_disk(root)
                self.assertIn("damaged or unusable", str(ctx.exception))
            finally:
                blocked.chmod(0o755)

    def test_a_store_with_no_versions_directory_yet_counts_zero(self) -> None:
        """The absent case the guard above must not swallow."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(learned_state._highest_version_on_disk(Path(tmp)), 0)

    def test_a_version_entry_symlinked_to_a_reachable_directory_is_counted(self) -> None:
        """The other half, so the fix cannot be "raise on every symlink"."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            versions = root / "learned-state" / "versions"
            versions.mkdir(parents=True)
            target = root / "elsewhere"
            target.mkdir()
            (versions / "v0009").symlink_to(target, target_is_directory=True)

            self.assertEqual(learned_state._highest_version_on_disk(root), 9)

    def test_an_unlistable_version_directory_refuses_rather_than_reading_as_empty(
        self,
    ) -> None:
        """The silent drop a *third* time, and the one no `OSError`
        converter could have caught.

        `Path.is_file()` and `Path.exists()` look like questions about the
        filesystem, but they are questions that cannot fail: both swallow
        `PermissionError` and answer `False`. Probing each vocabulary name
        with them meant a version directory at mode 000 reported every
        document as absent — `read_current` returned `{}` and `adopt` would
        have carried that emptiness forward, with nothing raised anywhere
        for a converter to convert. `os.listdir` asks the same question in a
        form that fails loudly.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt(
                [_change("memory", "m"), _change("briefs", "b")], root_dir=root, now=_NOW
            )
            version_dir = root / "learned-state" / "versions" / "v0001"
            version_dir.chmod(0o000)
            try:
                with self.assertRaises(ValueError):
                    learned_state.read_current(root)
            finally:
                version_dir.chmod(0o755)

    def test_an_append_that_cannot_reach_the_history_file_refuses(self) -> None:
        """The last step of `adopt`/`roll_back`, reached after a brand-new
        version's documents are already durably on disk — the highest-stakes
        call site in the module, and the one that was still outside the
        converter."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "learned-state").mkdir(parents=True)
            (root / "learned-state" / "history.jsonl").mkdir()
            entry = learned_state.VersionEntry(
                kind="adopt",
                version=1,
                replaces=None,
                documents=(),
                change_id=None,
                timestamp="2026-08-15T12:00:00Z",
            )

            with self.assertRaises(ValueError):
                learned_state._append_history(root, entry)

    def _read_paths_refuse(self, root: Path) -> None:
        for name, call in (
            ("read_current", learned_state.read_current),
            ("read_history", learned_state.read_history),
            ("current_version_dir", learned_state.current_version_dir),
        ):
            with self.subTest(entry_point=name), self.assertRaises(ValueError):
                call(root)

    def test_a_store_directory_occupied_by_a_file_refuses_on_every_read_path(self) -> None:
        """`adopt`/`roll_back` were guarded through the lock; the read-only
        entry points never went through it, so the identical fault raised a
        bare `NotADirectoryError` there."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "learned-state").write_text("occupied by a file", encoding="utf-8")
            self._read_paths_refuse(root)

    def test_a_versions_directory_occupied_by_a_file_refuses_on_adopt(self) -> None:
        """`NotADirectoryError` is a *sibling* of `FileExistsError` under
        `OSError`, not a subclass, so `_write_snapshot`'s collision clause
        never saw this one."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "learned-state").mkdir(parents=True)
            (root / "learned-state" / "versions").write_text("occupied", encoding="utf-8")

            with self.assertRaises(ValueError):
                learned_state.adopt([_change("memory", "x")], root_dir=root, now=_NOW)

    def test_an_unreadable_document_file_refuses_rather_than_raising_permission_error(
        self,
    ) -> None:
        """`is_file()` answers what kind of node it is, never whether it can
        be read. A mode-000 regular file passed it and then `read_bytes()`
        raised a bare `PermissionError` — while the docstring above it
        claimed to convert "exists but is not a readable file"."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "x")], root_dir=root, now=_NOW)
            document = root / "learned-state" / "versions" / "v0001" / "memory"
            document.chmod(0o000)
            try:
                with self.assertRaises(ValueError):
                    learned_state.read_current(root)
            finally:
                # Restore, or TemporaryDirectory's cleanup fails on some
                # platforms and masks the result of the test above.
                document.chmod(0o644)


class MissingSnapshotTests(unittest.TestCase):
    """A snapshot `history.jsonl` names, gone from disk.

    The mirror image of `OrphanedSnapshotRecoveryTests`: there, a directory
    exists that history does not name, and the store self-heals. Here,
    history names a directory that does not exist, and the store must stop.

    Before this was guarded, every one of these paths answered silently and
    two of them destroyed data: `read_current` returned `{}` — the identical
    answer it gives for a store nobody has ever used — and the next `adopt`
    read that `{}` as the state to carry forward, so documents the deleted
    version held simply stopped existing in the new one, with no error
    anywhere. `roll_back` restored an empty state just as quietly. That is
    the exact failure ticket 19's "the state after a rollback matches the
    prior version exactly, byte for byte" exists to prevent.
    """

    def _store_with_a_deleted_current_snapshot(self, root: Path) -> None:
        learned_state.adopt(
            [_change("memory", "lesson"), _change("briefs", "brief")],
            root_dir=root,
            now=_NOW,
        )
        learned_state.adopt([_change("routing_table", "table")], root_dir=root, now=_NOW)
        shutil.rmtree(root / "learned-state" / "versions" / "v0002")

    def test_read_current_raises_rather_than_reporting_an_empty_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._store_with_a_deleted_current_snapshot(root)

            with self.assertRaises(ValueError) as ctx:
                learned_state.read_current(root)
            self.assertIn("learned-state is damaged", str(ctx.exception))

    def test_adopt_refuses_rather_than_silently_dropping_the_documents_it_cannot_read(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._store_with_a_deleted_current_snapshot(root)

            with self.assertRaises(ValueError):
                learned_state.adopt([_change("memory", "new")], root_dir=root, now=_NOW)

            self.assertEqual(
                sorted(p.name for p in (root / "learned-state" / "versions").iterdir()),
                ["v0001"],
                "the refusal must write no new version",
            )
            self.assertEqual(
                len(learned_state.read_history(root)),
                2,
                "the refusal must append no history line",
            )

    def test_roll_back_refuses_rather_than_restoring_an_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._store_with_a_deleted_current_snapshot(root)

            with self.assertRaises(ValueError):
                learned_state.roll_back(root_dir=root, now=_NOW)

    def test_a_document_that_exists_but_is_not_a_readable_file_raises(self) -> None:
        """The same silent drop as the missing version directory, one level
        down. Confirmed by experiment before this was guarded: replacing
        `v0001/memory` with a directory — or with a dangling symlink — made
        `read_current` report only `briefs`, and the next `adopt` would have
        written a version with `memory` gone. Absent and unreadable are
        different answers; only absent is legitimate.
        """
        for label, tamper in (
            ("directory", lambda p: (p.unlink(), p.mkdir())),
            ("dangling symlink", lambda p: (p.unlink(), p.symlink_to(p.parent / "nope"))),
        ):
            with self.subTest(kind=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                learned_state.adopt(
                    [_change("memory", "lesson"), _change("briefs", "brief")],
                    root_dir=root,
                    now=_NOW,
                )
                tamper(root / "learned-state" / "versions" / "v0001" / "memory")

                with self.assertRaises(ValueError) as ctx:
                    learned_state.read_current(root)
                self.assertIn("learned-state is damaged", str(ctx.exception))

    def test_a_version_path_occupied_by_a_file_says_so_rather_than_missing(self) -> None:
        """Absent and occupied need different sentences.

        Every other case in this class deletes the directory, so the guard's
        message was only ever read against a genuinely missing path. Replace
        `v0002` with a plain file and the message claimed it "does not
        exist" while `Path.exists()` returned `True` — the same absent-vs-
        unreadable conflation `_read_documents` fixes one layer down,
        recurring one layer up in the sentence rather than in the logic.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "v1")], root_dir=root, now=_NOW)
            learned_state.adopt([_change("memory", "v2")], root_dir=root, now=_NOW)
            version_path = root / "learned-state" / "versions" / "v0002"
            shutil.rmtree(version_path)
            version_path.write_text("not a directory", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                learned_state.read_current(root)
            self.assertIn("is not a directory", str(ctx.exception))
            self.assertNotIn("does not exist", str(ctx.exception))

    def test_a_lock_path_that_cannot_be_opened_is_refused_with_a_value_error(self) -> None:
        """`_exclusive_store_lock`'s `open()` was the one on-disk anomaly
        this module did not rewrite as a `ValueError` — and it was the
        newest code in the file. Occupying `.lock` with a directory raised a
        bare `IsADirectoryError` straight out of `adopt`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "learned-state").mkdir(parents=True)
            (root / "learned-state" / ".lock").mkdir()

            with self.assertRaises(ValueError) as ctx:
                learned_state.adopt([_change("memory", "x")], root_dir=root, now=_NOW)
            self.assertIn("cannot be locked for writing", str(ctx.exception))

    def test_a_store_directory_occupied_by_a_file_is_refused_with_a_value_error(self) -> None:
        """The anomaly one directory higher than the test above.

        The first version of the lock guard wrapped only `open()`, and every
        test for it occupied `.lock` itself — so the shape held constant was
        "the anomaly sits at the lock's own path". Occupying `learned-state/`
        instead makes the *`mkdir`* fail, one line earlier, and a bare
        `FileExistsError` walked straight past a guard written to prevent
        exactly that.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "learned-state").write_text("occupied by a file", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                learned_state.adopt([_change("memory", "x")], root_dir=root, now=_NOW)
            self.assertIn("cannot be locked for writing", str(ctx.exception))

    def test_an_unknowable_version_path_is_not_reported_as_missing(self) -> None:
        """There are three states, not two: present, absent, and unknowable.

        `Path.is_dir()`/`.exists()`/`.is_symlink()` collapse the third into
        the second — each swallows `PermissionError` and answers `False`. So
        removing the search bit from `versions/` produced "does not exist"
        for a version directory that was completely intact, and sent the
        operator to `git checkout` to recover data that was never lost, over
        a permission bit git does not track on directories. `os.stat` raises
        instead, so the unknowable case is described as what it is.

        The assertion is on the message, not the exception type: every one
        of the four states raises `ValueError`, so asserting the type would
        pass identically against the bug.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "hello")], root_dir=root, now=_NOW)
            versions = root / "learned-state" / "versions"
            versions.chmod(0o000)
            try:
                with self.assertRaises(ValueError) as ctx:
                    learned_state.read_current(root)
                self.assertNotIn("does not exist", str(ctx.exception))
                self.assertIn("damaged or unusable", str(ctx.exception))
            finally:
                versions.chmod(0o755)

            # And the data really was intact all along.
            self.assertEqual(learned_state.read_current(root), {"memory": "hello"})

    def test_a_version_path_that_is_a_dangling_symlink_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "hello")], root_dir=root, now=_NOW)
            version_path = root / "learned-state" / "versions" / "v0001"
            shutil.rmtree(version_path)
            version_path.symlink_to(root / "nowhere")

            with self.assertRaises(ValueError) as ctx:
                learned_state.read_current(root)
            self.assertIn("dangling symlink", str(ctx.exception))

    def test_a_version_legitimately_missing_a_document_still_reads(self) -> None:
        """The absent case the guard must not swallow: v0001 holds only
        `memory`, so `briefs` has nothing at its path and is simply not in
        that version yet."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "lesson")], root_dir=root, now=_NOW)
            self.assertEqual(learned_state.read_current(root), {"memory": "lesson"})

    def test_an_empty_store_still_reads_as_empty_rather_than_damaged(self) -> None:
        """The legitimate `{}` the guard must not swallow: no version has
        ever been adopted, so there is no snapshot to be missing."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(learned_state.read_current(Path(tmp)), {})


class DocumentChangeValidationTests(unittest.TestCase):
    def test_rejects_an_unknown_document(self) -> None:
        with self.assertRaises(ValueError):
            _change("protocol", "x")

    def test_rejects_non_string_content(self) -> None:
        with self.assertRaises(ValueError):
            learned_state.DocumentChange(document="memory", content=123)  # type: ignore[arg-type]

    def test_rejects_a_string_that_cannot_be_encoded_as_utf8(self) -> None:
        """`str` is not the same as "writable". An unpaired surrogate is an
        ordinary `str` — `json.loads`, `os.fsdecode` and `surrogateescape`
        decoding all produce them — that `content.encode("utf-8")` cannot
        encode at all.

        Unguarded, such a value survived construction and raised
        `UnicodeEncodeError` out of `_digest` during `adopt`. That was
        *already* a `ValueError` (`UnicodeEncodeError` → `UnicodeError` →
        `ValueError`), so this is not a rejection-contract fix and nothing
        was escaping — it is a fix to where and when the refusal happens,
        and to a message that named a hashing step instead of the bad
        argument.

        Which is why the assertion below is on the message rather than on
        the exception type: `assertRaises(ValueError)` alone passes just as
        happily against the unguarded code, since the codec error is a
        `ValueError` too. Asserting the type here would be a test that
        cannot fail for the reason it names.
        """
        with self.assertRaises(ValueError) as ctx:
            learned_state.DocumentChange(document="memory", content="\ud800")
        self.assertIn("must be encodable as UTF-8", str(ctx.exception))

    def test_a_non_encodable_content_leaves_the_store_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            learned_state.adopt([_change("memory", "good")], root_dir=root, now=_NOW)

            with self.assertRaises(ValueError):
                learned_state.adopt(
                    [learned_state.DocumentChange(document="briefs", content="\ud800")],
                    root_dir=root,
                    now=_NOW,
                )

            self.assertEqual(
                sorted(p.name for p in (root / "learned-state" / "versions").iterdir()),
                ["v0001"],
                "the refusal must write no new version directory",
            )
            self.assertEqual(len(learned_state.read_history(root)), 1)
            self.assertEqual(learned_state.read_current(root), {"memory": "good"})


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


class VersionEntryDuplicateDeltaTests(unittest.TestCase):
    """The read side of the rule `_validate_changes` states for the write side.

    `adopt` and `roll_back` cannot produce two deltas for one document —
    `_diff_snapshots` walks a set union — so this only arrives from a
    hand-corrupted `history.jsonl`, which this module answers loudly by
    design. It used to round-trip such a line without complaint, leaving
    nothing to say which of the two deltas described the version.
    """

    @staticmethod
    # No return annotation: the module is loaded by path, so mypy cannot
    # resolve `learned_state.DocumentDelta` as a type. Same reason `_change`
    # at the top of this file has none.
    def _delta(before: str | None, after: str | None):
        return learned_state.DocumentDelta(
            document="memory", before_digest=before, after_digest=after
        )

    def test_two_deltas_for_one_document_are_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            learned_state.VersionEntry(
                kind="adopt",
                version=1,
                replaces=None,
                documents=(self._delta(None, "a" * 64), self._delta("b" * 64, "c" * 64)),
                change_id=None,
                timestamp="2026-08-15T12:00:00Z",
            )
        self.assertIn("more than once", str(ctx.exception))

    def test_read_history_refuses_a_line_carrying_two_deltas_for_one_document(self) -> None:
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
                        "documents": [
                            {
                                "document": "memory",
                                "before_digest": None,
                                "after_digest": "a" * 64,
                            },
                            {
                                "document": "memory",
                                "before_digest": "b" * 64,
                                "after_digest": "c" * 64,
                            },
                        ],
                        "change_id": None,
                        "timestamp": "2026-08-15T12:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                learned_state.read_history(root)

    def test_deltas_for_different_documents_are_accepted(self) -> None:
        """The legitimate case the guard must not swallow — one adopt
        touching two documents produces exactly this."""
        entry = learned_state.VersionEntry(
            kind="adopt",
            version=1,
            replaces=None,
            documents=(
                learned_state.DocumentDelta(
                    document="memory", before_digest=None, after_digest="a" * 64
                ),
                learned_state.DocumentDelta(
                    document="briefs", before_digest=None, after_digest="b" * 64
                ),
            ),
            change_id=None,
            timestamp="2026-08-15T12:00:00Z",
        )
        self.assertEqual(len(entry.documents), 2)


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
