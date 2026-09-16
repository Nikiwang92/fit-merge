#!/usr/bin/env python3
"""Detect FIT manufacturer and dispatch to Coros or Garmin merger."""

from __future__ import annotations

import argparse
from pathlib import Path

from merge_fit_coros_to_garmin import data_frames, frame_map, load_fit
from merge_fit_coros_to_garmin import merge_files as merge_coros_fit
from merge_fit_garmin_to_coros import merge_files as merge_garmin_fit

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "fit"
MERGE_FUNCTIONS = {
    "coros": merge_coros_fit,
    "garmin": merge_garmin_fit,
}


def manufacturer(path: Path) -> str:
    file_id = data_frames(load_fit(path).frames, "file_id")[0]
    value = frame_map(file_id).get("manufacturer")
    if value is None or value.value is None:
        raise ValueError(f"FIT manufacturer is missing: {path}")
    return str(value.value).lower()


def merge_group(brand: str, paths: list[Path], output: Path | None = None) -> None:
    if len(paths) < 2:
        raise ValueError(f"At least two {brand} FIT files are required")
    filename = (
        "fit_coros_to_garmin.fit" if brand == "coros"
        else "fit_garmin_to_coros.fit"
    )
    destination = output or ROOT / "merged" / filename
    MERGE_FUNCTIONS[brand](paths, destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge Coros or Garmin FIT files")
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.files:
        brands = {manufacturer(path) for path in args.files}
        if len(brands) != 1:
            raise ValueError("FIT files must have the same manufacturer")
        merge_group(brands.pop(), args.files, args.output)
        return

    groups = {
        "coros": sorted(path for path in DATA_DIR.glob("*.fit") if path.stem.isdigit()),
        "garmin": sorted(DATA_DIR.glob("*_ACTIVITY.fit")),
    }
    merged = False
    for brand, paths in groups.items():
        if len(paths) >= 2:
            merge_group(brand, paths)
            merged = True
    if not merged:
        raise FileNotFoundError("No mergeable FIT groups found under data/fit")


if __name__ == "__main__":
    main()
