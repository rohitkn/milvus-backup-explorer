"""Read Milvus Storage V2 (Parquet) insert binlog files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .schema import START_OF_USER_FIELD_ID, TIMESTAMP_FIELD, ROW_ID_FIELD, FieldInfo


@dataclass
class ColumnChunk:
    field_id: int
    column_name: str
    values: list


def _field_id_from_column(field: pa.Field) -> int | None:
    if field.metadata is None:
        return None
    raw = field.metadata.get(b"PARQUET:field_id")
    if raw is None:
        return None
    return int(raw.decode())


def read_parquet_user_columns(
    path: Path,
    fields_by_id: dict[int, FieldInfo],
    *,
    include_system: bool = False,
) -> dict[int, ColumnChunk]:
    """Read one parquet binlog file; return user-field columns keyed by field id."""
    table = pq.read_table(path)
    out: dict[int, ColumnChunk] = {}
    for name in table.column_names:
        arrow_field = table.schema.field(name)
        field_id = _field_id_from_column(arrow_field)
        if field_id is None:
            continue
        if not include_system and field_id in (ROW_ID_FIELD, TIMESTAMP_FIELD):
            continue
        if not include_system and field_id < START_OF_USER_FIELD_ID:
            continue
        if field_id not in fields_by_id:
            # Unknown field in schema — still export using parquet column name.
            pass
        column = table.column(name)
        values = [column[i].as_py() for i in range(len(column))]
        out[field_id] = ColumnChunk(field_id=field_id, column_name=name, values=values)
    return out


def merge_column_chunks(chunks: dict[int, ColumnChunk]) -> dict[int, list]:
    """Merge chunks from multiple parquet files (one per column group)."""
    merged: dict[int, list] = {}
    for field_id, chunk in chunks.items():
        merged.setdefault(field_id, []).extend(chunk.values)
    return merged
