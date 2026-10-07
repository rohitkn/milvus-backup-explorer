#!/usr/bin/env python3
"""
Extract insert data from a milvus-backup directory (Parquet / Storage V2 binlogs).

Indexes are not stored under binlogs/insert_log; this tool only reads insert binlogs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from milvus_backup_explorer.extract import ExtractOptions, extract_backup, format_collection_summary
from milvus_backup_explorer.meta import BackupCatalog


def _cmd_list(args: argparse.Namespace) -> int:
    catalog = BackupCatalog(Path(args.backup))
    print(f"Backup: {catalog.backup_name or args.backup}")
    if catalog.milvus_version:
        print(f"Milvus version: {catalog.milvus_version}")
    print()
    for coll in catalog.collections():
        segs = catalog.segments(collection_id=coll.collection_id)
        print(f"{coll.qualified_name}  (id={coll.collection_id}, segments={len(segs)})")
        print(format_collection_summary(coll, catalog))
        print()
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    catalog = BackupCatalog(Path(args.backup))
    options = ExtractOptions(
        include_system_fields=args.include_system,
        geometry_as_wkt=args.geometry_wkt,
        output_format=args.format,
        limit_rows=args.limit,
    )
    stats = extract_backup(
        catalog,
        Path(args.output),
        options,
        db_name=args.db,
        collection_name=args.collection,
    )
    if not stats:
        print("No data extracted. Check --backup path and optional --db/--collection filters.", file=sys.stderr)
        return 1
    print(f"Wrote extracted data to: {Path(args.output).resolve()}")
    for name, count in sorted(stats.items()):
        print(f"  {name}: {count} rows")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="milvus-backup-explorer",
        description="Extract row data from milvus-backup insert binlogs (not indexes).",
    )
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--backup",
        default="new_backup",
        help="Path to milvus-backup output directory (default: new_backup)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser(
        "list",
        parents=[parent],
        help="List collections and fields from backup metadata",
    )
    list_p.set_defaults(func=_cmd_list)

    ext_p = sub.add_parser(
        "extract",
        parents=[parent],
        help="Extract insert data to JSONL/JSON/CSV/Parquet",
    )
    ext_p.add_argument(
        "--output",
        "-o",
        default="extracted",
        help="Output directory (default: extracted)",
    )
    ext_p.add_argument("--db", help="Only extract this database name")
    ext_p.add_argument("--collection", "-c", help="Only extract this collection name")
    ext_p.add_argument(
        "--format",
        "-f",
        choices=("jsonl", "json", "csv", "parquet"),
        default="jsonl",
        help="Output format (default: jsonl)",
    )
    ext_p.add_argument(
        "--include-system",
        action="store_true",
        help="Include Milvus system columns RowID (0) and Timestamp (1)",
    )
    ext_p.add_argument(
        "--geometry-wkt",
        action="store_true",
        help="Emit geometry as WKT (requires shapely; otherwise WKB hex)",
    )
    ext_p.add_argument("--limit", type=int, default=None, help="Max rows per segment")
    ext_p.set_defaults(func=_cmd_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
