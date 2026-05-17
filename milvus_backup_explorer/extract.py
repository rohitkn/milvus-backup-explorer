"""Extract collection row data from backup binlogs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from .binlog import read_parquet_user_columns
from .codec import convert_cell, field_type_label
from .meta import BackupCatalog, CollectionInfo, SegmentInfo, iter_segments_from_disk
from .schema import FieldInfo, START_OF_USER_FIELD_ID


@dataclass
class ExtractOptions:
    include_system_fields: bool = False
    geometry_as_wkt: bool = False
    output_format: str = "jsonl"  # jsonl | json | csv | parquet
    limit_rows: int | None = None


def _field_name(fields_by_id: dict[int, FieldInfo], field_id: int, column_name: str) -> str:
    info = fields_by_id.get(field_id)
    return info.name if info else column_name


def _rows_from_columns(
    merged: dict[int, list],
    fields_by_id: dict[int, FieldInfo],
    column_names: dict[int, str],
    options: ExtractOptions,
) -> list[dict[str, Any]]:
    if not merged:
        return []
    lengths = {len(v) for v in merged.values()}
    if len(lengths) != 1:
        raise ValueError(f"column row count mismatch in segment: { {fid: len(v) for fid, v in merged.items()} }")
    row_count = next(iter(lengths))
    if options.limit_rows is not None:
        row_count = min(row_count, options.limit_rows)

    rows: list[dict[str, Any]] = []
    for i in range(row_count):
        row: dict[str, Any] = {}
        for field_id, values in sorted(merged.items()):
            field = fields_by_id.get(field_id)
            if field is None and not options.include_system_fields and field_id < START_OF_USER_FIELD_ID:
                continue
            name = _field_name(fields_by_id, field_id, column_names[field_id])
            row[name] = convert_cell(values[i], field, geometry_as_wkt=options.geometry_as_wkt)
        rows.append(row)
    return rows


def extract_segment(
    catalog: BackupCatalog,
    collection: CollectionInfo,
    segment: SegmentInfo,
    options: ExtractOptions,
) -> list[dict[str, Any]]:
    paths = catalog.discover_segment_binlogs(segment)
    if not paths:
        return []

    merged: dict[int, list] = {}
    column_names: dict[int, str] = {}
    for path in paths:
        if path.read_bytes()[:4] != b"PAR1":
            raise ValueError(f"unsupported binlog format (expected Parquet): {path}")
        file_cols = read_parquet_user_columns(
            path,
            collection.fields,
            include_system=options.include_system_fields,
        )
        for field_id, chunk in file_cols.items():
            column_names[field_id] = chunk.column_name
            merged.setdefault(field_id, []).extend(chunk.values)

    return _rows_from_columns(merged, collection.fields, column_names, options)


def write_rows(
    rows: list[dict[str, Any]],
    output_path: Path,
    fmt: str,
    field_order: list[str] | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = fmt.lower()
    if fmt == "jsonl":
        with output_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False))
                fh.write("\n")
        return

    if fmt == "json":
        output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    if fmt == "csv":
        keys = field_order or _stable_field_names(rows)
        with output_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: _csv_cell(row.get(k)) for k in keys})
        return

    if fmt == "parquet":
        table = _rows_to_arrow(rows, field_order)
        pq.write_table(table, output_path)
        return

    raise ValueError(f"unsupported output format: {fmt}")


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _stable_field_names(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    return seen


def _rows_to_arrow(rows: list[dict[str, Any]], field_order: list[str] | None) -> pa.Table:
    if not rows:
        return pa.table({})
    keys = field_order or _stable_field_names(rows)
    columns: dict[str, list] = {k: [] for k in keys}
    for row in rows:
        for k in keys:
            val = row.get(k)
            if isinstance(val, (dict, list)) and not isinstance(val, (str, bytes)):
                columns[k].append(json.dumps(val, ensure_ascii=False))
            else:
                columns[k].append(val)
    return pa.table(columns)


def extract_backup(
    catalog: BackupCatalog,
    output_dir: Path,
    options: ExtractOptions,
    *,
    db_name: str | None = None,
    collection_name: str | None = None,
) -> dict[str, int]:
    """Extract all matching collections; return stats keyed by qualified collection name."""
    stats: dict[str, int] = {}
    collections = catalog.collections()
    if collection_name:
        coll = catalog.find_collection(collection_name, db_name=db_name)
        collections = [coll] if coll else []

    for collection in collections:
        segments = catalog.segments(collection_id=collection.collection_id)
        if not segments and catalog.binlogs_dir.is_dir():
            segments = _segments_from_disk(catalog, collection.collection_id)

        total_rows = 0
        coll_dir = output_dir / collection.db_name / collection.collection_name
        for segment in segments:
            rows = extract_segment(catalog, collection, segment, options)
            if not rows:
                continue
            ext = _format_extension(options.output_format)
            out_file = coll_dir / f"segment_{segment.segment_id}{ext}"
            field_order = [f.name for f in sorted(collection.fields.values(), key=lambda x: x.field_id)]
            write_rows(rows, out_file, options.output_format, field_order=field_order)
            total_rows += len(rows)
        stats[collection.qualified_name] = total_rows
    return stats


def _segments_from_disk(catalog: BackupCatalog, collection_id: int) -> list[SegmentInfo]:
    segments: list[SegmentInfo] = []
    for cid, pid, sid, files in iter_segments_from_disk(catalog.binlogs_dir):
        if cid != collection_id:
            continue
        segments.append(
            SegmentInfo(
                segment_id=sid,
                collection_id=cid,
                partition_id=pid,
                num_of_rows=0,
                binlog_paths=[],
            )
        )
    return segments


def _format_extension(fmt: str) -> str:
    fmt = fmt.lower()
    return {
        "jsonl": ".jsonl",
        "json": ".json",
        "csv": ".csv",
        "parquet": ".parquet",
    }.get(fmt, ".jsonl")


def iter_collection_rows(
    catalog: BackupCatalog,
    collection: CollectionInfo,
    options: ExtractOptions,
) -> Iterator[tuple[SegmentInfo, list[dict[str, Any]]]]:
    segments = catalog.segments(collection_id=collection.collection_id)
    if not segments:
        segments = _segments_from_disk(catalog, collection.collection_id)
    for segment in segments:
        yield segment, extract_segment(catalog, collection, segment, options)


def format_collection_summary(collection: CollectionInfo) -> str:
    fields = sorted(collection.fields.values(), key=lambda f: f.field_id)
    lines = [f"  fields ({len(fields)}):"]
    for f in fields:
        flags = []
        if f.is_primary_key:
            flags.append("pk")
        if f.is_function_output:
            flags.append("function_output")
        flag_txt = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"    - {f.name} (id={f.field_id}, {field_type_label(f)}){flag_txt}")
    return "\n".join(lines)
