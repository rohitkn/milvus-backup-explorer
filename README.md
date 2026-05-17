# milvus-backup-explorer

A Python CLI that reads a [milvus-backup](https://github.com/zilliztech/milvus-backup) snapshot and exports **insert row data** from on-disk binlogs. It does **not** read or export indexes (those are separate from `binlogs/insert_log`).

## Purpose

After running `milvus-backup`, you get a directory (for example `new_backup/`) containing:

- **`meta/`** — JSON describing the backup, collections, partitions, segments, and where each binlog file lives.
- **`binlogs/`** — Binary insert logs (Milvus Storage V2: Parquet files under `binlogs/insert_log/`).

This tool helps you **inspect** what was backed up (`list`) and **decode rows** into JSONL, JSON, CSV, or Parquet (`extract`) without running Milvus or restoring the backup.

## Requirements

```bash
pip install -r requirements.txt
```

Requires **Python 3.10+** and **PyArrow**. Optional: **shapely** (for `--geometry-wkt` on geometry fields).

## Backup layout

```
new_backup/
├── meta/
│   ├── backup_meta.json       # backup id, name, size, Milvus version
│   ├── collection_meta.json   # schemas, field types, index *metadata* (not index files)
│   ├── partition_meta.json
│   ├── segment_meta.json      # per-segment binlog paths and row counts
│   └── full_meta.json         # optional merged view
└── binlogs/
    └── insert_log/
        └── {collection_id}/{partition_id}/{segment_id}/{segment_id}/{group}/{log_id}
```

Binlog files are Parquet (`PAR1`). Columns are tagged with Milvus field IDs in Parquet metadata (`PARQUET:field_id`). User-defined fields use IDs **≥ 100**; system fields `RowID` (0) and `Timestamp` (1) are skipped unless you pass `--include-system`.

---

## How `list` works (metadata only)

The `list` command **only reads `meta/`**. It does not open `binlogs/`.

1. Loads `backup_meta.json` for backup name and Milvus version.
2. Loads `collection_meta.json` and builds each collection’s schema (field names, types, primary key, function outputs).
3. Loads `segment_meta.json` to count segments per collection.
4. Prints a summary: `database.collection`, collection ID, segment count, and field list.

Use `list` to see what is in the backup before running a large `extract`.

```bash
python milvus-backup-explorer.py list --backup new_backup
```

---

## How `extract` works (metadata + binlogs)

The `extract` command uses **both** `meta/` and `binlogs/`.

1. **Metadata (`meta/`)**  
   - `collection_meta.json` — maps field IDs to names and types (for decoding vectors, sparse floats, geometry, etc.).  
   - `segment_meta.json` — for each segment: `num_of_rows` and `log_path` entries pointing at insert binlog files.

2. **Path resolution**  
   Metadata paths look like:
   `files/insert_log/{collection}/{partition}/{segment}/{group}/{log_id}`  
   On disk after backup they are stored as:
   `binlogs/insert_log/{collection}/{partition}/{segment}/{segment}/{group}/{log_id}`  
   (segment ID appears twice in the local path).

3. **Reading binlogs (`binlogs/`)**  
   For each segment, every referenced Parquet file is read. Column groups may bundle several fields in one file; the tool merges columns by field ID and assembles one row dict per index.

4. **Decoding**  
   - VarChar, integers, floats — native values  
   - Float vectors — list of `float` (from fixed-size binary)  
   - Sparse float vectors — `{ "index": value, ... }`  
   - Geometry — WKB hex, or WKT with `--geometry-wkt` if shapely is installed  

5. **Writing output**  
   Files are written under `{output}/{db}/{collection}/segment_{segment_id}.{ext}`.

Indexes are **not** extracted; index build artifacts are not stored under `binlogs/insert_log`.

```bash
# All collections → JSONL under extracted/
python milvus-backup-explorer.py extract --backup new_backup -o extracted

# One collection
python milvus-backup-explorer.py extract --backup new_backup -c prod_ann5 -o extracted

# Cap rows per segment (useful for testing)
python milvus-backup-explorer.py extract --backup new_backup -c doyying2 --db write_test1 --limit 100
```

### Output formats

| Format   | Flag           | Notes                                      |
|----------|----------------|--------------------------------------------|
| JSONL    | `-f jsonl`     | Default; one JSON object per line          |
| JSON     | `-f json`      | Single array per segment file              |
| CSV      | `-f csv`       | Nested values JSON-encoded in cells        |
| Parquet  | `-f parquet`   | Re-encoded Parquet per segment               |

---

## Command-line help

### Main command

```
usage: milvus-backup-explorer [-h] {list,extract} ...

Extract row data from milvus-backup insert binlogs (not indexes).

positional arguments:
  {list,extract}
    list          List collections and fields from backup metadata
    extract       Extract insert data to JSONL/JSON/CSV/Parquet

options:
  -h, --help      show this help message and exit
```

### `list`

```
usage: milvus-backup-explorer list [-h] [--backup BACKUP]

options:
  -h, --help       show this help message and exit
  --backup BACKUP  Path to milvus-backup output directory (default:
                   new_backup)
```

### `extract`

```
usage: milvus-backup-explorer extract [-h] [--backup BACKUP] [--output OUTPUT]
                                      [--db DB] [--collection COLLECTION]
                                      [--format {jsonl,json,csv,parquet}]
                                      [--include-system] [--geometry-wkt]
                                      [--limit LIMIT]

options:
  -h, --help            show this help message and exit
  --backup BACKUP       Path to milvus-backup output directory (default:
                        new_backup)
  --output OUTPUT, -o OUTPUT
                        Output directory (default: extracted)
  --db DB               Only extract this database name
  --collection COLLECTION, -c COLLECTION
                        Only extract this collection name
  --format {jsonl,json,csv,parquet}, -f {jsonl,json,csv,parquet}
                        Output format (default: jsonl)
  --include-system      Include Milvus system columns RowID (0) and Timestamp
                        (1)
  --geometry-wkt        Emit geometry as WKT (requires shapely; otherwise WKB
                        hex)
  --limit LIMIT         Max rows per segment
```

---

## Project layout

```
milvus-backup-explorer.py      # CLI entry point
milvus_backup_explorer/
  meta.py                      # Load meta/*.json, resolve binlog paths
  binlog.py                    # Read Parquet column groups
  codec.py                     # Type decoding (vectors, sparse, geometry)
  extract.py                   # Segment merge and export
  schema.py                    # Milvus field type constants
requirements.txt
```

## Related projects

- [Milvus](https://github.com/milvus-io/milvus) — vector database  
- [milvus-backup](https://github.com/zilliztech/milvus-backup) — backup and restore tool that produced the snapshot this explorer reads
