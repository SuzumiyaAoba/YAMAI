#!/usr/bin/env python3
"""Scoring CLI for the current profile; calculations never read expected data.

The draft.5 command and --print interface are preserved. Draft.6 delegates to
scoring_reference, replacing the old fixture-ID-dependent interpretation and
settlement logic. This is the CLI for the same reference, not a second engine.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from scoring_reference import ScoringError, calculate_fixture as compute_fixture
import validate_artifacts as v

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "test-vectors" / "yrc-0005" / v.PROFILE_REVISION / "scoring.json"


def run(path: Path, print_json: bool = False) -> int:
    data = v.strict_load(path)
    schemas = v.SchemaSet()
    schemas.validate(data, {"$ref": f"urn:yamai:schema:yrc-0005:{v.PROFILE_REVISION}:scoring-vectors"})
    computed = {}
    for fixture in data["fixtures"]:
        actual = compute_fixture(fixture, data["rules"])
        if not v._json_equal(actual, fixture["expected"]):
            raise ValueError(f"{fixture['id']}: recalculated scoring differs")
        computed[fixture["id"]] = actual
    for fixture in data["negative_fixtures"]:
        try:
            compute_fixture(fixture, data["rules"])
        except ScoringError as error:
            if error.code != fixture["expected_error"]:
                raise ValueError(f"{fixture['id']}: expected {fixture['expected_error']}, got {error.code}") from error
        else:
            raise ValueError(f"{fixture['id']}: invalid scoring input was accepted")
    if print_json:
        print(json.dumps(computed, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"score oracle: {len(computed)} positive and {len(data['negative_fixtures'])} negative fixtures verified")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--print", dest="print_json", action="store_true")
    args = parser.parse_args()
    try:
        return run(args.path, args.print_json)
    except (v.ArtifactError, ScoringError, ValueError, KeyError) as error:
        print(f"score oracle: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
