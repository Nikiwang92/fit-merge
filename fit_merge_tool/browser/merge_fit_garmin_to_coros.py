#!/usr/bin/env python3
"""Merge Garmin FIT files using Garmin-specific message ordering."""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Sequence

from merge_fit_coros_to_garmin import (
    Frame,
    build_fit,
    data_frames,
    first_index,
    frame_map,
    load_fit,
    parse_fit,
    patch_data,
    shift_year,
    value,
)

SKIP_FROM_NEXT = {"file_id", "file_creator", "activity", "session", "lap"}


def _average(first: Frame, second: Frame, name: str, t1: float, t2: float) -> float:
    a, b = frame_map(first), frame_map(second)
    return (float(a[name].value) * t1 + float(b[name].value) * t2) / (t1 + t2)


def _end_time(session: Frame) -> Any:
    start = value(session, "start_time")
    timestamp = value(session, "timestamp")
    if timestamp > start:
        return timestamp
    return start + timedelta(seconds=float(value(session, "total_elapsed_time")))


def combine_garmin_session(first: Frame, second: Frame) -> dict[str, int | float]:
    a, b = frame_map(first), frame_map(second)
    t1 = float(value(first, "total_timer_time"))
    t2 = float(value(second, "total_timer_time"))
    elapsed1 = float(value(first, "total_elapsed_time"))
    elapsed2 = float(value(second, "total_elapsed_time"))
    start2 = value(second, "start_time")
    end1 = _end_time(first)
    end2 = _end_time(second)
    gap = max(0.0, float((start2 - end1).total_seconds()))
    distance = float(value(first, "total_distance")) + float(value(second, "total_distance"))

    result: dict[str, int | float] = {
        "timestamp": end2,
        "total_elapsed_time": elapsed1 + gap + elapsed2,
        "total_timer_time": t1 + t2,
        "total_distance": distance,
        "avg_speed": distance / (t1 + t2),
        "enhanced_avg_speed": distance / (t1 + t2),
        "training_load_peak": float(a["training_load_peak"].value) + float(b["training_load_peak"].value),
        "total_training_effect": math.sqrt(float(a["total_training_effect"].value) ** 2 + float(b["total_training_effect"].value) ** 2),
        "total_anaerobic_training_effect": math.sqrt(float(a["total_anaerobic_training_effect"].value) ** 2 + float(b["total_anaerobic_training_effect"].value) ** 2),
    }

    for name in (
        "total_calories", "total_ascent", "total_descent", "total_work",
        "total_fractional_ascent", "total_fractional_descent",
    ):
        if name in a and name in b and a[name].value is not None and b[name].value is not None:
            result[name] = float(a[name].value) + float(b[name].value)
    for name in (
        "max_heart_rate", "max_speed", "enhanced_max_speed", "max_cadence",
        "max_power", "max_temperature", "max_altitude",
    ):
        if name in a and name in b and a[name].value is not None and b[name].value is not None:
            result[name] = max(float(a[name].value), float(b[name].value))
    for name in ("min_heart_rate", "min_temperature", "min_altitude"):
        if name in a and name in b and a[name].value is not None and b[name].value is not None:
            result[name] = min(float(a[name].value), float(b[name].value))
    for name in ("avg_heart_rate", "avg_temperature", "avg_cadence", "avg_power"):
        if name in a and name in b and a[name].value is not None and b[name].value is not None:
            result[name] = _average(first, second, name, t1, t2)
    return result


def combine_garmin_lap(first: Frame, second: Frame, end_time: Any) -> dict[str, int | float]:
    a, b = frame_map(first), frame_map(second)
    t1 = float(value(first, "total_timer_time"))
    t2 = float(value(second, "total_timer_time"))
    result: dict[str, int | float] = {
        "timestamp": end_time,
        "total_elapsed_time": float(value(first, "total_elapsed_time")) + float(value(second, "total_elapsed_time")),
        "total_timer_time": t1 + t2,
        "total_distance": float(value(first, "total_distance")) + float(value(second, "total_distance")),
        "avg_speed": (float(value(first, "total_distance")) + float(value(second, "total_distance"))) / (t1 + t2),
        "enhanced_avg_speed": (float(value(first, "total_distance")) + float(value(second, "total_distance"))) / (t1 + t2),
    }
    for name in ("total_calories", "total_ascent", "total_descent", "total_work", "total_fractional_ascent", "total_fractional_descent"):
        if name in a and name in b and a[name].value is not None and b[name].value is not None:
            result[name] = float(a[name].value) + float(b[name].value)
    for name in ("max_heart_rate", "max_speed", "enhanced_max_speed", "max_cadence", "max_power", "max_temperature", "max_altitude"):
        if name in a and name in b and a[name].value is not None and b[name].value is not None:
            result[name] = max(float(a[name].value), float(b[name].value))
    for name in ("min_heart_rate", "min_temperature", "min_altitude"):
        if name in a and name in b and a[name].value is not None and b[name].value is not None:
            result[name] = min(float(a[name].value), float(b[name].value))
    for name in ("avg_heart_rate", "avg_temperature", "avg_cadence", "avg_power"):
        if name in a and name in b and a[name].value is not None and b[name].value is not None:
            result[name] = _average(first, second, name, t1, t2)
    for name in ("total_training_effect", "total_anaerobic_training_effect"):
        if name in a and name in b and a[name].value is not None and b[name].value is not None:
            result[name] = math.sqrt(float(a[name].value) ** 2 + float(b[name].value) ** 2)
    return result


def merge_pair(first, second):
    first_frames = list(first.frames)
    second_frames = list(second.frames)

    session1 = data_frames(first_frames, "session")[0]
    session2 = data_frames(second_frames, "session")[0]
    lap1 = data_frames(first_frames, "lap")[0]
    lap2 = data_frames(second_frames, "lap")[0]
    activity1 = data_frames(first_frames, "activity")[0]
    session_values = combine_garmin_session(session1, session2)
    lap_values = combine_garmin_lap(lap1, lap2, session_values["timestamp"])

    total_timer = float(value(session1, "total_timer_time")) + float(value(session2, "total_timer_time"))
    distance_field = frame_map(session1)["total_distance"].field
    distance_scale = getattr(distance_field, "scale", None) or 1
    distance_offset = round(float(value(session1, "total_distance")) * distance_scale)
    first_records = data_frames(first_frames, "record")
    last_power = frame_map(first_records[-1]).get("accumulated_power") if first_records else None
    power_offset = int(last_power.raw_value) if last_power and last_power.raw_value is not None else None

    chunks: list[bytes] = []
    for frame in first_frames:
        raw = frame.raw
        if frame is session1:
            raw = patch_data(frame, session_values, physical=True)
        elif frame is lap1:
            raw = patch_data(frame, lap_values, physical=True)
        elif frame is activity1:
            local_offset = value(activity1, "local_timestamp") - value(activity1, "timestamp")
            raw = patch_data(
                frame,
                {
                    "timestamp": session_values["timestamp"],
                    "local_timestamp": session_values["timestamp"] + local_offset,
                    "total_timer_time": total_timer,
                },
                physical=True,
            )
        chunks.append(raw)

    for frame in second_frames:
        if frame.name in SKIP_FROM_NEXT:
            continue
        raw = frame.raw
        if frame.is_data and frame.name == "record":
            fields = frame_map(frame)
            updates: dict[str, int] = {}
            if fields.get("distance") and fields["distance"].raw_value is not None:
                updates["distance"] = int(fields["distance"].raw_value) + distance_offset
            if power_offset is not None and fields.get("accumulated_power") and fields["accumulated_power"].raw_value is not None:
                updates["accumulated_power"] = int(fields["accumulated_power"].raw_value) + power_offset
            raw = patch_data(frame, updates, physical=False) if updates else raw
        chunks.append(raw)

    metadata = {
        "records": len(first_records) + len(data_frames(second_frames, "record")),
        "laps": 1,
        "distance": float(session_values["total_distance"]),
    }
    return b"".join(chunks), metadata


def merge_files(paths: Sequence[Path], output_path: Path) -> dict[str, float]:
    if len(paths) < 2:
        raise ValueError("At least two Garmin FIT files are required")
    current = load_fit(paths[0])
    metadata: dict[str, float] = {}
    for path in paths[1:]:
        data, metadata = merge_pair(current, load_fit(path))
        current = parse_fit(build_fit(current.header, [data]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(shift_year(current))
    print(f"Generated: {output_path}")
    print(f"Records: {int(metadata['records'])}, laps: {int(metadata['laps'])}, distance: {metadata['distance']:.2f} m")
    return metadata


if __name__ == "__main__":
    raise SystemExit("Use merge.py as the entry point")
