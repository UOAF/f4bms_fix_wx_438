#!/usr/bin/env python3
"""Inspect and safely post-process Falcon BMS FMAP files.

This script is designed to replace older fixed-size FMAP patchers that broke
once F4Wx 2.0.0 switched to FMAP v8. The v8 layout adds one extra float per
cell: fogLayerZ.
"""

from __future__ import annotations

import argparse
import math
import shutil
import struct
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Callable, Sequence


NUM_ALOFT_BREAKPOINTS = 10
HEADER_FORMAT = "<IIIifii4i"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
FLOAT_TOLERANCE = 1e-5

WEATHER_LABELS = {
    1: "Sunny",
    2: "Fair",
    3: "Poor",
    4: "Inclement",
}

DEFAULT_MIN_VISIBILITY = {
    1: 60.0,
    2: 40.0,
    3: 30.0,
    4: 20.0,
}


def _format_counter(values: Counter[int], label_map: dict[int, str] | None = None) -> str:
    parts: list[str] = []
    for key in sorted(values):
        label = label_map.get(key, str(key)) if label_map else str(key)
        parts.append(f"{label}={values[key]}")
    return ", ".join(parts)


def _stats(values: Sequence[float]) -> str:
    return f"min={min(values):.3f} max={max(values):.3f} mean={fmean(values):.3f}"


@dataclass
class FMap:
    version: int
    size_y: int
    size_x: int
    map_wind_heading: int
    map_wind_speed: float
    map_stratus_z_fair: int
    map_stratus_z_inc: int
    map_contrail_layer: list[int]
    basic_condition: list[int]
    pressure: list[float]
    temperature: list[float]
    wind_speed: list[float]
    wind_dir: list[float]
    cumulus_base: list[float]
    cumulus_density: list[int]
    cumulus_size: list[float]
    has_tower_cumulus: list[int]
    has_shower_cumulus: list[int]
    visibility: list[float]
    fog_layer_z: list[float] | None

    @property
    def cell_count(self) -> int:
        return self.size_y * self.size_x

    @property
    def has_fog_layer_z(self) -> bool:
        return self.fog_layer_z is not None

    @classmethod
    def from_bytes(cls, data: bytes) -> "FMap":
        if len(data) < HEADER_SIZE:
            raise ValueError(f"FMAP file is too small: {len(data)} bytes")

        (
            version,
            size_y,
            size_x,
            map_wind_heading,
            map_wind_speed,
            map_stratus_z_fair,
            map_stratus_z_inc,
            *map_contrail_layer,
        ) = struct.unpack_from(HEADER_FORMAT, data, 0)

        cell_count = size_y * size_x
        remaining = len(data) - HEADER_SIZE
        bytes_per_cell_v7 = 29 * 4
        bytes_per_cell_v8 = 30 * 4

        if remaining == cell_count * bytes_per_cell_v8:
            has_fog_layer_z = True
        elif remaining == cell_count * bytes_per_cell_v7:
            has_fog_layer_z = False
        else:
            raise ValueError(
                "Unsupported FMAP layout: "
                f"version={version}, cells={cell_count}, remaining={remaining} bytes"
            )

        offset = HEADER_SIZE

        def read(fmt: str, count: int) -> list[int] | list[float]:
            nonlocal offset
            size = struct.calcsize(fmt) * count
            values = list(struct.unpack_from(f"<{count}{fmt}", data, offset))
            offset += size
            return values

        basic_condition = read("i", cell_count)
        pressure = read("f", cell_count)
        temperature = read("f", cell_count)
        wind_speed = read("f", cell_count * NUM_ALOFT_BREAKPOINTS)
        wind_dir = read("f", cell_count * NUM_ALOFT_BREAKPOINTS)
        cumulus_base = read("f", cell_count)
        cumulus_density = read("i", cell_count)
        cumulus_size = read("f", cell_count)
        has_tower_cumulus = read("i", cell_count)
        has_shower_cumulus = read("i", cell_count)
        visibility = read("f", cell_count)
        fog_layer_z = read("f", cell_count) if has_fog_layer_z else None

        if offset != len(data):
            raise ValueError(f"FMAP parse did not consume the full file: {offset} != {len(data)}")

        return cls(
            version=version,
            size_y=size_y,
            size_x=size_x,
            map_wind_heading=map_wind_heading,
            map_wind_speed=map_wind_speed,
            map_stratus_z_fair=map_stratus_z_fair,
            map_stratus_z_inc=map_stratus_z_inc,
            map_contrail_layer=map_contrail_layer,
            basic_condition=basic_condition,
            pressure=pressure,
            temperature=temperature,
            wind_speed=wind_speed,
            wind_dir=wind_dir,
            cumulus_base=cumulus_base,
            cumulus_density=cumulus_density,
            cumulus_size=cumulus_size,
            has_tower_cumulus=has_tower_cumulus,
            has_shower_cumulus=has_shower_cumulus,
            visibility=visibility,
            fog_layer_z=fog_layer_z,
        )

    @classmethod
    def from_path(cls, path: Path) -> "FMap":
        return cls.from_bytes(path.read_bytes())

    def to_bytes(self) -> bytes:
        header = struct.pack(
            HEADER_FORMAT,
            self.version,
            self.size_y,
            self.size_x,
            self.map_wind_heading,
            self.map_wind_speed,
            self.map_stratus_z_fair,
            self.map_stratus_z_inc,
            *self.map_contrail_layer,
        )
        chunks = [header]

        def pack(fmt: str, values: Sequence[int] | Sequence[float]) -> None:
            chunks.append(struct.pack(f"<{len(values)}{fmt}", *values))

        pack("i", self.basic_condition)
        pack("f", self.pressure)
        pack("f", self.temperature)
        pack("f", self.wind_speed)
        pack("f", self.wind_dir)
        pack("f", self.cumulus_base)
        pack("i", self.cumulus_density)
        pack("f", self.cumulus_size)
        pack("i", self.has_tower_cumulus)
        pack("i", self.has_shower_cumulus)
        pack("f", self.visibility)
        if self.fog_layer_z is not None:
            pack("f", self.fog_layer_z)

        return b"".join(chunks)

    def save(self, path: Path) -> None:
        path.write_bytes(self.to_bytes())

    def sync_fog_layer_z_from_cumulus(self) -> int:
        if self.fog_layer_z is None:
            raise ValueError("This FMAP does not contain a fogLayerZ block.")

        changed = 0
        for idx, base in enumerate(self.cumulus_base):
            if not math.isclose(self.fog_layer_z[idx], base, rel_tol=0.0, abs_tol=FLOAT_TOLERANCE):
                self.fog_layer_z[idx] = base
                changed += 1
        return changed

    def enforce_min_visibility(self, minimums: dict[int, float]) -> dict[int, int]:
        changed_by_type: dict[int, int] = {key: 0 for key in minimums}
        for idx, wx_type in enumerate(self.basic_condition):
            min_vis = minimums.get(wx_type)
            if min_vis is None:
                continue
            if self.visibility[idx] < min_vis:
                self.visibility[idx] = min_vis
                changed_by_type[wx_type] += 1
        return changed_by_type

    def summary_lines(self) -> list[str]:
        lines = [
            f"version: {self.version}",
            f"grid: {self.size_y} x {self.size_x} ({self.cell_count} cells)",
            f"layout: {'v8+' if self.has_fog_layer_z else 'v7'}",
            f"weather counts: {_format_counter(Counter(self.basic_condition), WEATHER_LABELS)}",
            f"visibility: {_stats(self.visibility)} km",
            f"cumulus base: {_stats(self.cumulus_base)} ft",
            f"fogLayerZ present: {'yes' if self.has_fog_layer_z else 'no'}",
        ]

        if self.fog_layer_z is not None:
            mismatches = sum(
                not math.isclose(fog, base, rel_tol=0.0, abs_tol=FLOAT_TOLERANCE)
                for fog, base in zip(self.fog_layer_z, self.cumulus_base)
            )
            lines.append(f"fogLayerZ: {_stats(self.fog_layer_z)} ft")
            lines.append(f"fogLayerZ != cumulusBase cells: {mismatches}")

        return lines


def _default_output_path(path: Path, suffix: str) -> Path:
    return path.with_name(f"{path.stem}{suffix}{path.suffix}")


def _discover_input_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".fmap")
        if not files:
            raise ValueError(f"No .fmap files found in directory: {path}")
        return files
    raise ValueError(f"Input path does not exist: {path}")


def _resolve_output_target(
    src: Path,
    input_path: Path,
    output: Path | None,
    in_place: bool,
) -> Path:
    if in_place:
        return src

    if output is None:
        if input_path.is_dir():
            target_dir = input_path.with_name(f"{input_path.name}.fixed")
            target_dir.mkdir(parents=True, exist_ok=True)
            return target_dir / src.name
        return _default_output_path(src, ".fixed")

    if input_path.is_file():
        if output.exists() and output.is_dir():
            output.mkdir(parents=True, exist_ok=True)
            return output / src.name
        if output.suffix:
            output.parent.mkdir(parents=True, exist_ok=True)
            return output
        output.mkdir(parents=True, exist_ok=True)
        return output / src.name

    output.mkdir(parents=True, exist_ok=True)
    return output / src.name


def _process_files(
    input_path: Path,
    output: Path | None,
    in_place: bool,
    backup: bool,
    dry_run: bool,
    processor: Callable[[Path, FMap], tuple[list[str], int]],
) -> int:
    files = _discover_input_files(input_path)
    total_changed = 0

    for src in files:
        fmap = FMap.from_path(src)
        report_lines, changed = processor(src, fmap)
        total_changed += changed

        print(f"file: {src}")
        if dry_run:
            for line in report_lines:
                print(line)
            continue

        if backup and in_place:
            backup_path = _default_output_path(src, ".bak")
            shutil.copy2(src, backup_path)
            print(f"backup: {backup_path}")

        target = _resolve_output_target(src, input_path, output, in_place)
        map_path = target
        fmap.save(map_path)
        print(f"wrote: {map_path}")
        for line in report_lines:
            print(line)

    if len(files) > 1:
        print(f"processed files: {len(files)}")
        print(f"total cells updated: {total_changed}")

    return 0


def command_inspect(args: argparse.Namespace) -> int:
    files = _discover_input_files(args.path)
    for idx, path in enumerate(files):
        fmap = FMap.from_path(path)
        print(f"file: {path}")
        print(f"size: {path.stat().st_size} bytes")
        for line in fmap.summary_lines():
            print(line)
        if idx + 1 < len(files):
            print()
    return 0


def command_sync_fog_layer(args: argparse.Namespace) -> int:
    def processor(_src: Path, fmap: FMap) -> tuple[list[str], int]:
        changed = fmap.sync_fog_layer_z_from_cumulus()
        return ([f"cells updated: {changed}"], changed)

    return _process_files(
        input_path=args.path,
        output=args.output,
        in_place=args.in_place,
        backup=args.backup,
        dry_run=args.dry_run,
        processor=processor,
    )


def command_enforce_min_visibility(args: argparse.Namespace) -> int:
    minimums = {
        1: args.sunny,
        2: args.fair,
        3: args.poor,
        4: args.inclement,
    }

    def processor(_src: Path, fmap: FMap) -> tuple[list[str], int]:
        changed_by_type = fmap.enforce_min_visibility(minimums)
        total_changed = sum(changed_by_type.values())
        lines = [f"cells updated: {total_changed}"]
        for wx_type in sorted(changed_by_type):
            lines.append(
                f"{WEATHER_LABELS[wx_type]} threshold {minimums[wx_type]:.1f} km: "
                f"{changed_by_type[wx_type]} cells"
            )
        return (lines, total_changed)

    return _process_files(
        input_path=args.path,
        output=args.output,
        in_place=args.in_place,
        backup=args.backup,
        dry_run=args.dry_run,
        processor=processor,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect and safely patch Falcon BMS FMAP files."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Print a summary of the FMAP layout and key weather fields."
    )
    inspect_parser.add_argument("path", type=Path)
    inspect_parser.set_defaults(func=command_inspect)

    sync_parser = subparsers.add_parser(
        "sync-fog-layer-z",
        help="Set fogLayerZ to cumulusBase for each cell without corrupting v8 files.",
    )
    sync_parser.add_argument("path", type=Path)
    sync_parser.add_argument("--output", type=Path, help="Write to a separate file.")
    sync_parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input file instead of writing <name>.fixed.fmap.",
    )
    sync_parser.add_argument(
        "--backup",
        action="store_true",
        help="Create a .bak.fmap backup when used with --in-place.",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many cells would change without writing a file.",
    )
    sync_parser.set_defaults(func=command_sync_fog_layer)

    vis_parser = subparsers.add_parser(
        "enforce-min-visibility",
        help="Clamp cell visibility to per-weather minimums without corrupting v8 files.",
    )
    vis_parser.add_argument("path", type=Path)
    vis_parser.add_argument("--output", type=Path, help="Write to a separate file.")
    vis_parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input file instead of writing <name>.fixed.fmap.",
    )
    vis_parser.add_argument(
        "--backup",
        action="store_true",
        help="Create a .bak.fmap backup when used with --in-place.",
    )
    vis_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many cells would change without writing a file.",
    )
    vis_parser.add_argument(
        "--sunny",
        type=float,
        default=DEFAULT_MIN_VISIBILITY[1],
        help="Minimum visibility for sunny cells in km.",
    )
    vis_parser.add_argument(
        "--fair",
        type=float,
        default=DEFAULT_MIN_VISIBILITY[2],
        help="Minimum visibility for fair cells in km.",
    )
    vis_parser.add_argument(
        "--poor",
        type=float,
        default=DEFAULT_MIN_VISIBILITY[3],
        help="Minimum visibility for poor cells in km.",
    )
    vis_parser.add_argument(
        "--inclement",
        type=float,
        default=DEFAULT_MIN_VISIBILITY[4],
        help="Minimum visibility for inclement cells in km.",
    )
    vis_parser.set_defaults(func=command_enforce_min_visibility)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover - CLI error path
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
