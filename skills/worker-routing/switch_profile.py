"""Validate and atomically activate a checked-in worker-routing profile."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

if __package__:
    from . import routing_config
else:
    import routing_config  # type: ignore[no-redef]


PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
CONFIG_PATH = Path(__file__).resolve().parent / "routing-config.json"
ACTIVE_PROFILE_KEY = "_active_profile"


def available_profiles(profile_dir: Path | None = None) -> list[Path]:
    """Return profile files in their stable, numbered order."""
    return sorted((profile_dir or PROFILE_DIR).glob("*.json"))


def profile_name(profile: Path) -> str:
    """The user-facing profile name, without the ordering prefix or suffix."""
    return profile.stem.split("_", 1)[-1]


def resolve_profile(value: str, profile_dir: Path | None = None) -> Path:
    """Resolve a numbered filename, short name, or numeric prefix."""
    normalized = value.removesuffix(".json")
    matches = [
        profile
        for profile in available_profiles(profile_dir)
        if normalized in {profile.stem, profile_name(profile), profile.stem.split("_", 1)[0]}
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"Unknown routing profile: {value}")
    raise ValueError(f"Ambiguous routing profile: {value}")


def read_profile(profile: Path) -> dict[str, object]:
    with profile.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise TypeError(f"Profile must contain a JSON object: {profile}")
    return data


def atomic_write_config(data: dict[str, object], target: Path = CONFIG_PATH) -> None:
    """Write JSON beside *target* then atomically replace it with ``os.replace``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as stream:
            temp_name = stream.name
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def switch_profile(profile: Path, *, target: Path = CONFIG_PATH, dry_run: bool = False) -> str:
    """Validate a profile before optionally atomically making it active."""
    data = read_profile(profile)
    routing_config.parse_routing_config(data, fallback_on_missing=True)
    active_name = profile_name(profile)
    data[ACTIVE_PROFILE_KEY] = active_name
    if not dry_run:
        atomic_write_config(data, target)
    return active_name


def current_profile(target: Path = CONFIG_PATH) -> str | None:
    """Return the active-profile tag, if this config was activated by this tool."""
    if not target.exists():
        return None
    data = read_profile(target)
    active = data.get(ACTIVE_PROFILE_KEY)
    return active if isinstance(active, str) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="list available profiles")
    action.add_argument("--current", action="store_true", help="show the active profile")
    action.add_argument("--switch", metavar="PROFILE", help="validate and activate a profile")
    parser.add_argument("--dry-run", action="store_true", help="validate without changing routing-config.json")
    return parser


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = stdout if stdout is not None else __import__("sys").stdout
    if args.list:
        active = current_profile()
        for profile in available_profiles():
            marker = " *" if profile_name(profile) == active else ""
            print(f"{profile.stem}: {profile_name(profile)}{marker}", file=output)
        return 0
    if args.current:
        print(current_profile() or "untracked", file=output)
        return 0
    try:
        profile = resolve_profile(args.switch)
        active_name = switch_profile(profile, dry_run=args.dry_run)
    except (OSError, ValueError, json.JSONDecodeError, routing_config.ConfigError) as exc:
        print(f"switch failed: {exc}", file=output)
        return 1
    prefix = "validated" if args.dry_run else "activated"
    print(f"{prefix}: {active_name}", file=output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
