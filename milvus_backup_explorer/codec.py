"""Convert Milvus parquet column values to JSON-friendly Python objects."""

from __future__ import annotations

import json
import struct
from typing import Any

from .schema import DATA_TYPE_NAMES, FieldInfo

try:
    from shapely import wkb
    from shapely.geometry import mapping as geom_mapping

    _HAS_SHAPELY = True
except ImportError:
    _HAS_SHAPELY = False


def sparse_bytes_to_dict(blob: bytes) -> dict[str, float]:
    """Decode one Milvus sparse float row (8-byte index+value pairs, little-endian)."""
    out: dict[str, float] = {}
    if not blob:
        return out
    if len(blob) % 8 != 0:
        return {"_raw_hex": blob.hex(), "_error": "invalid sparse row length"}
    for i in range(0, len(blob), 8):
        idx, val = struct.unpack_from("<If", blob, i)
        out[str(idx)] = float(val)
    return out


def fixed_binary_to_float_vector(blob: bytes, dim: int) -> list[float]:
    if dim <= 0:
        dim = len(blob) // 4
    count = min(dim, len(blob) // 4)
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


def geometry_bytes_to_json(blob: bytes, as_wkt: bool) -> Any:
    if not blob:
        return None
    if _HAS_SHAPELY:
        try:
            geom = wkb.loads(blob)
            if as_wkt:
                return geom.wkt
            return geom_mapping(geom)
        except Exception as exc:
            return {"_wkb_hex": blob.hex(), "_error": str(exc)}
    return {"_wkb_hex": blob.hex()}


def convert_cell(value: Any, field: FieldInfo | None, *, geometry_as_wkt: bool) -> Any:
    if value is None:
        return None

    data_type = field.data_type if field else None

    if isinstance(value, bytes):
        if data_type == 104:  # SparseFloatVector
            return sparse_bytes_to_dict(value)
        if data_type == 101:  # FloatVector
            dim = field.dim if field else len(value) // 4
            return fixed_binary_to_float_vector(value, dim or 0)
        if data_type == 24:  # Geometry
            return geometry_bytes_to_json(value, geometry_as_wkt)
        if data_type in (100, 102, 103, 105):
            return value.hex()
        return value.hex()

    if data_type == 23 and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    if hasattr(value, "as_py"):
        return convert_cell(value.as_py(), field, geometry_as_wkt=geometry_as_wkt)

    if isinstance(value, (list, tuple)) and value and isinstance(value[0], bytes):
        if data_type == 104:
            return [sparse_bytes_to_dict(v) for v in value]
        if data_type == 101:
            dim = field.dim if field else 0
            return [fixed_binary_to_float_vector(v, dim or 0) for v in value]

    return value


def field_type_label(field: FieldInfo) -> str:
    return DATA_TYPE_NAMES.get(field.data_type, f"Unknown({field.data_type})")
