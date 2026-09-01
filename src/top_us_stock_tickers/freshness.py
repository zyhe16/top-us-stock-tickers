"""UTC freshness checks used by the scheduled update fallback."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def snapshot_is_current_utc_day(manifest_path, *, now=None):
    """Return whether a manifest was generated on the current UTC day."""
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        generated_at = manifest["generatedAt"]
        if not isinstance(generated_at, str):
            return False
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False

    if generated.tzinfo is None:
        return False

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    return generated.astimezone(UTC).date() == current.astimezone(UTC).date()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report whether a dataset manifest is current in UTC."
    )
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    print("true" if snapshot_is_current_utc_day(args.manifest) else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
