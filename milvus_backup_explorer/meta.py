"""Load milvus-backup metadata and resolve binlog paths."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .schema import FieldInfo


@dataclass(frozen=True)
class CollectionInfo:
    collection_id: int
    db_name: str
    collection_name: str
    fields: dict[int, FieldInfo]
    num_rows_hint: int = 0

    @property
    def qualified_name(self) -> str:
        return f"{self.db_name}.{self.collection_name}"


@dataclass(frozen=True)
class SegmentInfo:
    segment_id: int
    collection_id: int
    partition_id: int
    num_of_rows: int
    binlog_paths: list[str]
    storage_version: int = 2


class BackupCatalog:
    def __init__(self, backup_dir: Path) -> None:
        self.backup_dir = backup_dir.resolve()
        self.meta_dir = self.backup_dir / "meta"
        self.binlogs_dir = self.backup_dir / "binlogs"
        self._collections: dict[int, CollectionInfo] = {}
        self._segments: list[SegmentInfo] = []
        self._backup_meta: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        backup_meta_path = self.meta_dir / "backup_meta.json"
        if backup_meta_path.is_file():
            self._backup_meta = json.loads(backup_meta_path.read_text())

        coll_path = self.meta_dir / "collection_meta.json"
        if coll_path.is_file():
            payload = json.loads(coll_path.read_text())
            for raw in payload.get("infos", []):
                fields = {
                    int(f["fieldID"]): FieldInfo.from_schema_dict(f)
                    for f in raw.get("schema", {}).get("fields", [])
                }
                coll = CollectionInfo(
                    collection_id=int(raw["collection_id"]),
                    db_name=raw.get("db_name", "default"),
                    collection_name=raw["collection_name"],
                    fields=fields,
                    num_rows_hint=_sum_segment_rows(raw),
                )
                self._collections[coll.collection_id] = coll

        seg_path = self.meta_dir / "segment_meta.json"
        if seg_path.is_file():
            payload = json.loads(seg_path.read_text())
            for raw in payload.get("infos", []):
                paths: list[str] = []
                for entry in raw.get("binlogs", []):
                    for blog in entry.get("binlogs", []):
                        p = blog.get("log_path")
                        if p:
                            paths.append(p)
                self._segments.append(
                    SegmentInfo(
                        segment_id=int(raw["segment_id"]),
                        collection_id=int(raw["collection_id"]),
                        partition_id=int(raw["partition_id"]),
                        num_of_rows=int(raw.get("num_of_rows", 0)),
                        binlog_paths=paths,
                        storage_version=int(raw.get("storage_version", 2)),
                    )
                )

    @property
    def milvus_version(self) -> str | None:
        return self._backup_meta.get("milvus_version")

    @property
    def backup_name(self) -> str | None:
        return self._backup_meta.get("name")

    def collections(self) -> list[CollectionInfo]:
        return sorted(self._collections.values(), key=lambda c: c.qualified_name)

    def get_collection(self, collection_id: int) -> CollectionInfo | None:
        return self._collections.get(collection_id)

    def find_collection(self, name: str, db_name: str | None = None) -> CollectionInfo | None:
        name = name.strip()
        for coll in self._collections.values():
            if coll.collection_name != name:
                continue
            if db_name is not None and coll.db_name != db_name:
                continue
            return coll
        return None

    def segments(
        self,
        *,
        collection_id: int | None = None,
        db_name: str | None = None,
        collection_name: str | None = None,
    ) -> list[SegmentInfo]:
        out = self._segments
        if collection_id is not None:
            out = [s for s in out if s.collection_id == collection_id]
        if collection_name is not None:
            coll = self.find_collection(collection_name, db_name=db_name)
            if coll is None:
                return []
            out = [s for s in out if s.collection_id == coll.collection_id]
        return out

    def resolve_binlog_path(self, log_path: str) -> Path:
        """Map metadata log_path to on-disk path under new_backup/binlogs."""
        rel = log_path
        if rel.startswith("files/"):
            rel = rel[len("files/") :]
        parts = rel.split("/")
        if len(parts) == 6 and parts[0] == "insert_log":
            _, collection_id, partition_id, segment_id, group, log_id = parts
            return (
                self.binlogs_dir
                / "insert_log"
                / collection_id
                / partition_id
                / segment_id
                / segment_id
                / group
                / log_id
            )
        return self.binlogs_dir / rel

    def discover_segment_binlogs(self, segment: SegmentInfo) -> list[Path]:
        """Return parquet files for a segment using metadata or filesystem walk."""
        files: list[Path] = []
        for log_path in segment.binlog_paths:
            local = self.resolve_binlog_path(log_path)
            if local.is_file():
                files.append(local)
        if files:
            return sorted(set(files))

        seg_dir = (
            self.binlogs_dir
            / "insert_log"
            / str(segment.collection_id)
            / str(segment.partition_id)
            / str(segment.segment_id)
            / str(segment.segment_id)
        )
        if seg_dir.is_dir():
            files = [p for p in seg_dir.rglob("*") if p.is_file()]
        return sorted(files)


def _sum_segment_rows(collection_raw: dict[str, Any]) -> int:
    total = 0
    for part in collection_raw.get("partition_backups", []):
        for seg in part.get("segment_backups", []):
            total += int(seg.get("num_of_rows", 0))
    return total


def iter_segments_from_disk(binlogs_dir: Path) -> Iterator[tuple[int, int, int, list[Path]]]:
    """Yield (collection_id, partition_id, segment_id, files) without metadata."""
    root = binlogs_dir / "insert_log"
    if not root.is_dir():
        return
    for coll_dir in sorted(root.iterdir()):
        if not coll_dir.is_dir():
            continue
        for part_dir in sorted(coll_dir.iterdir()):
            if not part_dir.is_dir():
                continue
            for seg_dir in sorted(part_dir.iterdir()):
                if not seg_dir.is_dir():
                    continue
                inner = seg_dir / seg_dir.name
                search_root = inner if inner.is_dir() else seg_dir
                files = sorted(p for p in search_root.rglob("*") if p.is_file())
                if not files:
                    continue
                yield int(coll_dir.name), int(part_dir.name), int(seg_dir.name), files
