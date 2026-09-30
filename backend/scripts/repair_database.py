"""Audit and repair the known SQLite integrity issue.

Dry-run is the default.  ``--apply`` first creates a timestamped database copy,
then quarantines orphan borehole import copies that share the same hole identity
with one valid borehole and have no dependent sections.  The full original row
is retained as JSON before deletion.  Indexes are rebuilt and both SQLite
integrity checks must pass before commit.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


BOREHOLE_FIELDS = (
    "Borehole", "原点", "Northing", "Easting", "Elevation", "Holedepth",
    "范围下限", "范围上限",
)
IDENTITY_FIELDS = ("Borehole", "Northing", "Easting", "Elevation", "Holedepth")


def audit(connection: sqlite3.Connection) -> list[tuple[int, int, dict]]:
    connection.row_factory = sqlite3.Row
    fields = ", ".join(f"b2.{field} AS orphan_{index}" for index, field in enumerate(BOREHOLE_FIELDS))
    candidates = connection.execute(
        f"""
        SELECT b2.id AS orphan_id, b2.model_id AS orphan_model_id, {fields}
        FROM boreholes AS b2 NOT INDEXED
        LEFT JOIN geological_models AS model ON model.id = b2.model_id
        WHERE model.id IS NULL
        ORDER BY b2.id
        """
    ).fetchall()
    recoverable: list[tuple[int, int, dict]] = []
    for orphan in candidates:
        dependent_count = connection.execute(
            "SELECT count(*) FROM borehole_sections WHERE borehole_id = ?",
            (orphan["orphan_id"],),
        ).fetchone()[0]
        if dependent_count:
            continue
        field_indexes = {field: index for index, field in enumerate(BOREHOLE_FIELDS)}
        comparisons = " AND ".join(f"b1.{field} IS ?" for field in IDENTITY_FIELDS)
        values = [orphan[f"orphan_{field_indexes[field]}"] for field in IDENTITY_FIELDS]
        matches = connection.execute(
            f"""
            SELECT b1.id
            FROM boreholes AS b1 NOT INDEXED
            JOIN geological_models AS model ON model.id = b1.model_id
            WHERE {comparisons}
            """,
            values,
        ).fetchall()
        if len(matches) == 1:
            row = connection.execute(
                "SELECT * FROM boreholes NOT INDEXED WHERE id = ?",
                (orphan["orphan_id"],),
            ).fetchone()
            recoverable.append(
                (int(orphan["orphan_id"]), int(matches[0][0]), dict(row))
            )
    return recoverable


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "geology_norm.db")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db_path = args.db.resolve()
    connection = sqlite3.connect(db_path)
    try:
        matches = audit(connection)
        print("recoverable orphan import copies:", [(a, b) for a, b, _ in matches])
        print("foreign key issues before:", [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")])
        print("integrity before:", [tuple(row) for row in connection.execute("PRAGMA integrity_check")])
        if not args.apply:
            print("dry-run only; pass --apply to repair")
            return
        backup_dir = db_path.parent / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-database-repair")
        backup_dir.mkdir(parents=True, exist_ok=False)
        backup_path = backup_dir / db_path.name
        connection.close()
        shutil.copy2(db_path, backup_path)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        # Rebuild the damaged indexes before DELETE so SQLite can maintain them safely.
        connection.execute("REINDEX ix_boreholes_Borehole")
        connection.execute("REINDEX ix_boreholes_model_id")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS borehole_quarantine (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_row_id INTEGER NOT NULL,
                source_model_id INTEGER NOT NULL,
                matched_borehole_id INTEGER,
                reason TEXT NOT NULL,
                row_json TEXT NOT NULL,
                quarantined_at DATETIME NOT NULL
            )
            """
        )
        for orphan_id, valid_id, row in matches:
            connection.execute(
                """
                INSERT INTO borehole_quarantine(
                    source_row_id, source_model_id, matched_borehole_id,
                    reason, row_json, quarantined_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    orphan_id,
                    row["model_id"],
                    valid_id,
                    "orphan import copy with matching hole identity and no dependent sections",
                    json.dumps(row, ensure_ascii=False, default=str),
                    datetime.now().isoformat(),
                ),
            )
            connection.execute("DELETE FROM boreholes WHERE id = ?", (orphan_id,))
        foreign_key_issues = list(connection.execute("PRAGMA foreign_key_check"))
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if foreign_key_issues or integrity != ["ok"]:
            connection.rollback()
            raise RuntimeError(
                f"post-repair verification failed: foreign_keys={foreign_key_issues}, integrity={integrity}"
            )
        connection.commit()
        print(f"backup: {backup_path}")
        print(f"quarantined and removed from active boreholes: {[item[0] for item in matches]}")
        print("post-repair checks: ok")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
