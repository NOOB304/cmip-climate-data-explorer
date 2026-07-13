from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE catalog_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE variable_definitions (
    variable_key TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    table_id TEXT NOT NULL,
    variable_id TEXT NOT NULL,
    frequency TEXT,
    modeling_realm TEXT,
    standard_name TEXT,
    long_name TEXT NOT NULL,
    units TEXT NOT NULL,
    cell_methods TEXT,
    dimensions TEXT,
    comment TEXT,
    chinese_name TEXT,
    chinese_description TEXT,
    source_version TEXT NOT NULL,
    UNIQUE(project, table_id, variable_id)
);
CREATE INDEX ix_variables_id ON variable_definitions(variable_id);
CREATE INDEX ix_variables_standard_name ON variable_definitions(standard_name);
CREATE TABLE variable_aliases (
    variable_key TEXT NOT NULL REFERENCES variable_definitions(variable_key) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'und',
    UNIQUE(variable_key, alias)
);
CREATE INDEX ix_alias ON variable_aliases(alias);
"""


def build_catalog(
    tables_dir: Path,
    overlay_path: Path,
    output: Path,
    source_revision: str,
) -> int:
    overlay = json.loads(overlay_path.read_text(encoding="utf-8")) if overlay_path.exists() else {}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    count = 0
    with closing(sqlite3.connect(temporary)) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO catalog_metadata(key, value) VALUES(?, ?)",
            ("source_revision", source_revision),
        )
        for table_path in sorted(tables_dir.glob("CMIP6_*.json")):
            payload = json.loads(table_path.read_text(encoding="utf-8"))
            table_id = table_path.stem.removeprefix("CMIP6_")
            header = payload.get("Header", {})
            source_version = f"{header.get('data_specs_version', 'unknown')}@{source_revision}"
            for variable_id, entry in payload.get("variable_entry", {}).items():
                variable_key = f"CMIP6:{table_id}:{variable_id}"
                translated = overlay.get(variable_id, {})
                connection.execute(
                    """
                    INSERT OR REPLACE INTO variable_definitions VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        variable_key,
                        "CMIP6",
                        table_id,
                        variable_id,
                        entry.get("frequency"),
                        entry.get("modeling_realm"),
                        entry.get("standard_name"),
                        entry.get("long_name") or variable_id,
                        entry.get("units") or "1",
                        entry.get("cell_methods"),
                        entry.get("dimensions"),
                        entry.get("comment"),
                        translated.get("name"),
                        translated.get("description"),
                        source_version,
                    ),
                )
                for alias in translated.get("aliases", []):
                    connection.execute(
                        "INSERT OR IGNORE INTO variable_aliases VALUES (?, ?, 'zh-CN')",
                        (variable_key, alias),
                    )
                count += 1
        connection.commit()
    temporary.replace(output)
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tables_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--overlay", type=Path, default=Path("resources/variable_zh.json"))
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    count = build_catalog(args.tables_dir, args.overlay, args.output, args.source_revision)
    print(f"built {args.output} with {count} CMIP6 table-variable definitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
