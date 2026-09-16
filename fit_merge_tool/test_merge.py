#!/usr/bin/env python3
"""Regression checks for FIT merging."""

from __future__ import annotations

import tempfile
import unittest
import warnings
from datetime import timedelta
from pathlib import Path

warnings.filterwarnings("ignore", message="invalid field size 1.*")

import fitdecode

from merge_fit_coros_to_garmin import merge_files
from merge_fit_garmin_to_coros import merge_files as merge_garmin_files

ROOT = Path(__file__).resolve().parent.parent
FIT = sorted(path for path in (ROOT / "data" / "fit").glob("*.fit") if path.stem.isdigit())
GARMIN = sorted((ROOT / "data" / "fit").glob("*_ACTIVITY.fit"))


def records(path: Path) -> list[dict]:
    result = []
    with fitdecode.FitReader(str(path), check_crc=fitdecode.CrcCheck.RAISE) as fit:
        for frame in fit:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "record":
                result.append({field.name: field.value for field in frame.fields})
    return result


def count(path: Path, name: str) -> int:
    result = 0
    with fitdecode.FitReader(str(path), check_crc=fitdecode.CrcCheck.RAISE) as fit:
        for frame in fit:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == name:
                result += 1
    return result


class FitMergeTests(unittest.TestCase):
    def test_pair_and_triple(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "merged.fit"
            merge_files(FIT[:2], output)
            expected_records = sum(count(path, "record") for path in FIT[:2])
            expected_laps = sum(count(path, "lap") for path in FIT[:2])
            self.assertEqual(count(output, "record"), expected_records)
            self.assertEqual(count(output, "lap"), expected_laps)

            first, second, merged = records(FIT[0]), records(FIT[1]), records(output)
            fields = (
                "position_lat", "position_long", "heart_rate", "altitude",
                "speed", "cadence", "power", "step_length",
            )
            for source, result in zip(first, merged[:len(first)]):
                self.assertEqual(result["timestamp"] - source["timestamp"], timedelta(days=365))
                for name in fields:
                    self.assertEqual(result.get(name), source.get(name))
            for source, result in zip(second, merged[len(first):]):
                self.assertEqual(result["timestamp"] - source["timestamp"], timedelta(days=365))
                for name in fields:
                    self.assertEqual(result.get(name), source.get(name))

            triple = Path(directory) / "triple.fit"
            merge_files([*FIT[:2], FIT[1]], triple)
            self.assertEqual(
                count(triple, "record"),
                expected_records + len(second),
            )
            self.assertEqual(
                count(triple, "lap"),
                expected_laps + count(FIT[1], "lap"),
            )

    def test_garmin_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "garmin.fit"
            merge_garmin_files(GARMIN[:2], output)
            expected_records = sum(count(path, "record") for path in GARMIN[:2])
            self.assertEqual(count(output, "record"), expected_records)
            self.assertEqual(count(output, "lap"), 1)

            with fitdecode.FitReader(str(output), check_crc=fitdecode.CrcCheck.RAISE) as fit:
                session = next(
                    {field.name: field.value for field in frame.fields}
                    for frame in fit
                    if isinstance(frame, fitdecode.FitDataMessage)
                    and frame.name == "session"
                )
            self.assertAlmostEqual(session["total_distance"], 12714.18, places=2)
            self.assertAlmostEqual(session["total_timer_time"], 2438.363, places=3)
            self.assertEqual(session["total_calories"], 219)


if __name__ == "__main__":
    unittest.main(verbosity=2)
