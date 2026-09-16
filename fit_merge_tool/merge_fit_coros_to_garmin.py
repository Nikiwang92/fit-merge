#!/usr/bin/env python3
"""Merge the two COROS splits into one Coros-compatible FIT file."""

from __future__ import annotations

import math
import struct
import warnings
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import fitdecode

warnings.filterwarnings("ignore", message="invalid field size 1.*")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "fit"
OUTPUT_PATH = ROOT / "merged" / "merged.fit"
SHIFT_SECONDS = 0

CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)


@dataclass(frozen=True)
class Frame:
    kind: str
    name: str | None
    local_id: int | None
    raw: bytes
    frame: Any

    @property
    def is_definition(self) -> bool:
        return self.kind == "definition"

    @property
    def is_data(self) -> bool:
        return self.kind == "data"


@dataclass(frozen=True)
class FitDocument:
    header: bytes
    frames: tuple[Frame, ...]


def parse_fit(data: bytes) -> FitDocument:
    header_size = data[0]
    data_size = int.from_bytes(data[4:8], "little")
    if header_size < 12 or len(data) != header_size + data_size + 2:
        raise ValueError("Invalid FIT container")

    frames = []
    with fitdecode.FitReader(
        data,
        check_crc=fitdecode.CrcCheck.RAISE,
        keep_raw_chunks=True,
    ) as reader:
        for frame in reader:
            if isinstance(frame, (fitdecode.FitHeader, fitdecode.FitCRC)):
                continue
            if not isinstance(
                frame,
                (fitdecode.FitDefinitionMessage, fitdecode.FitDataMessage),
            ):
                continue
            frames.append(
                Frame(
                    kind=(
                        "definition"
                        if isinstance(frame, fitdecode.FitDefinitionMessage)
                        else "data"
                    ),
                    name=getattr(frame, "name", None),
                    local_id=frame.local_mesg_num,
                    raw=bytes(frame.chunk.bytes),
                    frame=frame,
                )
            )
    return FitDocument(data[:header_size], tuple(frames))


def load_fit(path: Path) -> FitDocument:
    return parse_fit(path.read_bytes())


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        tmp = CRC_TABLE[crc & 0x0F]
        crc = (crc >> 4) & 0x0FFF
        crc ^= tmp ^ CRC_TABLE[byte & 0x0F]
        tmp = CRC_TABLE[crc & 0x0F]
        crc = (crc >> 4) & 0x0FFF
        crc ^= tmp ^ CRC_TABLE[(byte >> 4) & 0x0F]
    return crc & 0xFFFF


def build_fit(header: bytes, chunks: Sequence[bytes]) -> bytes:
    body = b"".join(chunks)
    result_header = bytearray(header[:14])
    result_header[4:8] = len(body).to_bytes(4, "little")
    result_header[12:14] = crc16(bytes(result_header[:12])).to_bytes(2, "little")
    container = bytes(result_header) + body
    return container + crc16(container).to_bytes(2, "little")


def field_definitions(frame: Any) -> tuple[Any, ...]:
    if isinstance(frame, fitdecode.FitDefinitionMessage):
        return tuple(frame.all_field_defs)
    return tuple(frame.def_mesg.all_field_defs)


def encode_raw(base_type: str, value: int | float, endian: str) -> bytes:
    formats = {
        "enum": "B", "uint8": "B", "uint16": "H", "uint32": "I",
        "sint8": "b", "sint16": "h", "sint32": "i",
    }
    byteorder = "little" if endian == "<" else "big"
    if base_type in formats:
        return int(value).to_bytes(
            struct.calcsize(formats[base_type]),
            byteorder,
            signed=base_type.startswith("sint"),
        )
    if base_type == "float32":
        return struct.pack(f"{endian}f", float(value))
    raise ValueError(f"Unsupported FIT type: {base_type}")


def encode_physical(field_data: Any, value: int | float | datetime, endian: str) -> bytes:
    if isinstance(value, datetime):
        raw: int | float = int(value.timestamp()) - 631_065_600
    else:
        field = field_data.field
        scale = getattr(field, "scale", None) or 1
        offset = getattr(field, "offset", None) or 0
        raw_value = (float(value) - float(offset)) * float(scale)
        raw = raw_value if field_data.base_type.name.startswith("float") else round(raw_value)
    return encode_raw(field_data.base_type.name, raw, endian)


def patch_data(
    frame: Frame,
    updates: Mapping[str | int, int | float],
    *,
    physical: bool,
) -> bytes:
    raw = bytearray(frame.raw)
    definitions = field_definitions(frame.frame)
    data_fields = tuple(frame.frame.fields)
    offset = 1
    endian = frame.frame.def_mesg.endian

    for definition in definitions:
        matches = [
            field for field in data_fields
            if field.def_num == definition.def_num
            and field.field_def.is_dev == definition.is_dev
            and not field.is_expanded
        ]
        if len(matches) != 1:
            raise ValueError(f"Could not map field {definition.name}")
        field_data = matches[0]
        key = next(
            (name for name in (field_data.name, definition.name) if name in updates),
            None,
        )
        if key is not None:
            encoded = (
                encode_physical(field_data, updates[key], endian)
                if physical
                else encode_raw(field_data.base_type.name, updates[key], endian)
            )
            raw[offset:offset + definition.size] = encoded
        offset += definition.size
    return bytes(raw)


def frame_map(frame: Frame) -> dict[str, Any]:
    return {field.name: field for field in frame.frame.fields}


def value(frame: Frame, name: str) -> Any:
    result = frame_map(frame)[name].value
    if result is None:
        raise ValueError(f"Missing {frame.name}.{name}")
    return result


def data_frames(frames: Sequence[Frame], name: str) -> list[Frame]:
    return [frame for frame in frames if frame.is_data and frame.name == name]


def first_index(frames: Sequence[Frame], predicate: Callable[[Frame], bool]) -> int:
    return next(index for index, frame in enumerate(frames) if predicate(frame))


def last_index(frames: Sequence[Frame], predicate: Callable[[Frame], bool]) -> int:
    return max(index for index, frame in enumerate(frames) if predicate(frame))


def deduplicate_descriptions(
    primary: Sequence[Frame],
    secondary: Sequence[Frame],
) -> list[Frame]:
    def signature(frame: Frame) -> tuple[tuple[int, Any], ...]:
        return tuple((field.def_num, field.raw_value) for field in frame.frame.fields)

    known = {
        signature(frame)
        for frame in primary
        if frame.is_data and frame.name == "field_description"
    }
    result: list[Frame] = []
    pending: Frame | None = None
    for frame in secondary:
        if frame.is_definition and frame.name == "field_description":
            pending = frame
        elif frame.is_data and frame.name == "field_description":
            if signature(frame) in known:
                pending = None
            else:
                if pending is not None:
                    result.append(pending)
                result.append(frame)
                pending = None
        else:
            if pending is not None:
                result.append(pending)
                pending = None
            result.append(frame)
    return result


def combine_session(first: Frame, second: Frame) -> dict[str, int | float]:
    a = frame_map(first)
    b = frame_map(second)
    t1 = float(value(first, "total_timer_time"))
    t2 = float(value(second, "total_timer_time"))
    distance = float(value(first, "total_distance")) + float(value(second, "total_distance"))

    def combined(name: str, operation: Callable[[float, float], float]) -> float:
        return operation(float(a[name].value), float(b[name].value))

    def weighted(name: str) -> float:
        return (float(a[name].value) * t1 + float(b[name].value) * t2) / (t1 + t2)

    end = value(second, "timestamp")
    start = value(first, "timestamp")
    gap = float(value(second, "start_time").timestamp() - start.timestamp())
    return {
        "timestamp": end,
        "total_elapsed_time": float(value(first, "total_elapsed_time")) + gap + float(value(second, "total_elapsed_time")),
        "total_timer_time": t1 + t2,
        "total_distance": distance,
        "total_calories": combined("total_calories", lambda x, y: x + y),
        "max_heart_rate": combined("max_heart_rate", max),
        "min_heart_rate": combined("min_heart_rate", min),
        "avg_heart_rate": weighted("avg_heart_rate"),
        "avg_temperature": weighted("avg_temperature"),
        "total_ascent": combined("total_ascent", lambda x, y: x + y),
        "total_descent": combined("total_descent", lambda x, y: x + y),
        "total_strides": combined("total_strides", lambda x, y: x + y),
        "max_running_cadence": combined("max_running_cadence", max),
        "avg_running_cadence": weighted("avg_running_cadence"),
        "avg_step_length": weighted("avg_step_length"),
        "max_speed": combined("max_speed", max),
        "avg_speed": distance / (t1 + t2),
        "avg_power": weighted("avg_power"),
        "avg_stance_time": weighted("avg_stance_time"),
        "avg_stance_time_balance": weighted("avg_stance_time_balance"),
        "avg_vertical_oscillation": weighted("avg_vertical_oscillation"),
        "avg_vertical_ratio": weighted("avg_vertical_ratio"),
        "Effort Pace": weighted("Effort Pace"),
    }



def add_standard_fields(frame: Frame, additions: tuple[tuple[int, int, int, bytes], ...], raw_override: bytes | None = None) -> bytes:
    raw = bytearray(raw_override or frame.raw)
    definitions = field_definitions(frame.frame)
    standard = [item for item in definitions if not item.is_dev]
    if frame.is_definition:
        count = raw[5]
        if count != len(standard):
            raise ValueError("Unexpected FIT definition field count")
        offset = 6 + count * 3
        raw[5] = count + len(additions)
        raw[offset:offset] = b"".join(
            bytes((number, size, base_type))
            for number, size, base_type, _ in additions
        )
    else:
        offset = 1 + sum(item.size for item in standard)
        raw[offset:offset] = b"".join(value for _, _, _, value in additions)
    return bytes(raw)

def merge_source_data(first: FitDocument, second: FitDocument) -> tuple[bytes, dict[str, float]]:
    first_frames = list(first.frames)
    second_frames = deduplicate_descriptions(first_frames, second.frames)

    first_lap = first_index(first_frames, lambda frame: frame.name == "lap")
    second_lap = first_index(second_frames, lambda frame: frame.name == "lap")
    second_record = first_index(
        second_frames,
        lambda frame: frame.is_data and frame.name == "record",
    )
    second_event = max(
        index for index, frame in enumerate(second_frames[:second_record])
        if frame.is_definition and frame.name == "event"
    )

    first_prefix = first_frames[:first_lap]
    second_prefix = second_frames[second_event:second_lap]
    first_laps = first_frames[first_lap:last_index(first_frames, lambda f: f.is_data and f.name == "lap") + 1]
    second_laps = second_frames[second_lap:last_index(second_frames, lambda f: f.is_data and f.name == "lap") + 1]

    sessions1 = data_frames(first_frames, "session")
    sessions2 = data_frames(second_frames, "session")
    activities = data_frames(first_frames, "activity")
    if len(sessions1) != 1 or len(sessions2) != 1 or len(activities) != 1:
        raise ValueError("Expected one session and activity per segment")

    session1, session2, activity = sessions1[0], sessions2[0], activities[0]
    session_def = next(
        frame for frame in first_frames
        if frame.is_definition and frame.name == "session"
    )
    total_timer = float(value(session1, "total_timer_time")) + float(value(session2, "total_timer_time"))
    session_values = combine_session(session1, session2)

    distance_field = frame_map(session1)["total_distance"].field
    distance_scale = getattr(distance_field, "scale", None) or 1
    distance_offset = round(float(value(session1, "total_distance")) * distance_scale)
    last_power = frame_map(data_frames(first_frames, "record")[-1]).get("accumulated_power")
    power_offset = int(last_power.raw_value) if last_power and last_power.raw_value is not None else None

    chunks: list[bytes] = []
    for frame in first_prefix:
        raw = frame.raw
        if frame.is_data and frame.name == "activity":
            raw = patch_data(frame, {"total_timer_time": total_timer}, physical=True)
        chunks.append(raw)

    for frame in second_prefix:
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

    chunks.extend(frame.raw for frame in first_laps)
    lap_base = len(data_frames(first_frames, "lap"))
    appended = 0
    for frame in second_laps:
        raw = frame.raw
        if frame.is_data and frame.name == "lap":
            raw = patch_data(frame, {"message_index": lap_base + appended}, physical=False)
            appended += 1
        chunks.append(raw)

    chunks.append(session_def.raw)
    chunks.append(patch_data(session1, session_values, physical=True))

    metadata = {
        "records": float(len(data_frames(first_frames, "record")) + len(data_frames(second_frames, "record"))),
        "laps": float(lap_base + len(data_frames(second_frames, "lap"))),
        "distance": float(value(session1, "total_distance")) + float(value(session2, "total_distance")),
    }
    return b"".join(chunks), metadata



def standardize_for_garmin(document: FitDocument) -> bytes:
    frames = list(document.frames)
    activity_def = next(
        frame for frame in frames
        if frame.is_definition and frame.name == "activity"
    )
    activity_data = data_frames(frames, "activity")[0]
    session_def = next(
        frame for frame in frames
        if frame.is_definition and frame.name == "session"
    )
    session_data = data_frames(frames, "session")[0]
    lap_total = len(data_frames(frames, "lap"))
    session_end = value(session_data, "timestamp")
    local_offset = value(activity_data, "local_timestamp") - value(activity_data, "timestamp")
    additions = (
        (254, 2, 0x84, struct.pack("<H", 0)),
        (25, 2, 0x84, struct.pack("<H", 0)),
        (26, 2, 0x84, struct.pack("<H", lap_total)),
    )

    chunks: list[bytes] = []
    for frame in frames:
        if frame is activity_def or frame is activity_data:
            continue
        raw = frame.raw
        if frame.is_data and frame.name == "file_id":
            raw = patch_data(frame, {"manufacturer": 1, "product": 4315}, physical=False)
        elif frame.is_data and frame.name == "device_info":
            raw = patch_data(frame, {"manufacturer": 1}, physical=False)
        elif frame is session_def:
            raw = add_standard_fields(frame, additions)
        elif frame is session_data:
            raw = add_standard_fields(frame, additions, raw_override=raw)
        chunks.append(raw)

    activity_raw = patch_data(
        activity_data,
        {
            "timestamp": session_end,
            "local_timestamp": session_end + local_offset,
            "total_timer_time": value(session_data, "total_timer_time"),
        },
        physical=True,
    )
    activity_frame = replace(activity_data, raw=activity_raw)
    chunks.append(activity_def.raw)
    chunks.append(patch_data(activity_frame, {"type": 0}, physical=False))
    return build_fit(document.header, chunks)

def shift_year(document: FitDocument) -> bytes:
    names = {"timestamp", "start_time", "local_timestamp", "time_created"}
    chunks = []
    for frame in document.frames:
        if not frame.is_data:
            chunks.append(frame.raw)
            continue
        updates = {
            field.name: int(field.raw_value) + SHIFT_SECONDS
            for field in frame.frame.fields
            if field.name in names and field.raw_value is not None
        }
        chunks.append(patch_data(frame, updates, physical=False) if updates else frame.raw)
    return build_fit(document.header, chunks)


def merge_files(
    paths: Sequence[Path],
    output_path: Path = OUTPUT_PATH,
) -> dict[str, float]:
    if len(paths) < 2:
        raise ValueError("At least two FIT files are required")

    current = load_fit(paths[0])
    metadata: dict[str, float] = {}
    for path in paths[1:]:
        merged_data, metadata = merge_source_data(current, load_fit(path))
        current = parse_fit(build_fit(current.header, [merged_data]))

    output_path.parent.mkdir(exist_ok=True)
    output_path.write_bytes(standardize_for_garmin(current))
    check = load_fit(output_path)
    counts = {
        name: len(data_frames(check.frames, name))
        for name in ("file_id", "activity", "session", "record", "lap")
    }
    if counts != {
        "file_id": 1,
        "activity": 1,
        "session": 1,
        "record": int(metadata["records"]),
        "lap": int(metadata["laps"]),
    }:
        raise RuntimeError(f"Validation failed: {counts}")

    print(f"Generated: {output_path}")
    print(f"Records: {int(metadata['records'])}, laps: {int(metadata['laps'])}, distance: {metadata['distance']:.2f} m")
    return metadata


def main() -> None:
    merge_files(sorted(DATA_DIR.glob("*.fit")))


if __name__ == "__main__":
    main()
