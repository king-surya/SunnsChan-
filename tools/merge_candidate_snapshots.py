"""Merge checkpoint-safe GitHub discovery snapshots without losing source queries."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args()

    candidates: dict[str, dict] = {}
    source_files: list[str] = []
    failures: dict[str, str] = {}
    for path in args.inputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_files.append(str(path))
        failures.update(payload.get("failures", {}))
        for item in payload["unique_candidates"]:
            merged = candidates.setdefault(item["full_name"], item | {"queries": []})
            merged["queries"] = sorted(set(merged["queries"]) | set(item.get("queries", [])))
            merged["stars"] = max(merged["stars"], item["stars"])
            merged["updated_at"] = max(merged["updated_at"], item["updated_at"])

    result = {
        "merged_at": datetime.now(UTC).isoformat(),
        "source_snapshots": source_files,
        "failures": failures,
        "unique_candidates": sorted(candidates.values(), key=lambda item: (-item["stars"], item["full_name"])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Merged {len(args.inputs)} snapshots into {len(candidates)} unique candidates: {args.output}")


if __name__ == "__main__":
    main()
