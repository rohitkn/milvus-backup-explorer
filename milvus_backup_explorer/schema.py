"""Milvus schema field types (schemapb.DataType)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# https://github.com/milvus-io/milvus-proto/blob/master/proto/schema.proto
DATA_TYPE_NAMES: dict[int, str] = {
    1: "Bool",
    2: "Int8",
    3: "Int16",
    4: "Int32",
    5: "Int64",
    10: "Float",
    11: "Double",
    21: "VarChar",
    22: "Array",
    23: "JSON",
    24: "Geometry",
    100: "BinaryVector",
    101: "FloatVector",
    102: "Float16Vector",
    103: "BFloat16Vector",
    104: "SparseFloatVector",
    105: "Int8Vector",
}

ROW_ID_FIELD = 0
TIMESTAMP_FIELD = 1
START_OF_USER_FIELD_ID = 100


@dataclass(frozen=True)
class FieldInfo:
    field_id: int
    name: str
    data_type: int
    is_primary_key: bool = False
    type_params: dict[str, str] | None = None
    is_function_output: bool = False

    @property
    def dim(self) -> int | None:
        if not self.type_params:
            return None
        raw = self.type_params.get("dim")
        return int(raw) if raw is not None else None

    @classmethod
    def from_schema_dict(cls, raw: dict[str, Any]) -> FieldInfo:
        params = raw.get("type_params") or []
        type_params = {p["key"]: p["value"] for p in params if "key" in p and "value" in p}
        return cls(
            field_id=int(raw["fieldID"]),
            name=raw["name"],
            data_type=int(raw["data_type"]),
            is_primary_key=bool(raw.get("is_primary_key")),
            type_params=type_params or None,
            is_function_output=bool(raw.get("is_function_output")),
        )


def type_param_map(field: dict[str, Any]) -> dict[str, str]:
    params = field.get("type_params") or []
    return {p["key"]: p["value"] for p in params if "key" in p and "value" in p}
