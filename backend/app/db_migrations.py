"""Small, explicit database migrations for installations without Alembic.

The project historically relied on ``metadata.create_all``.  That creates new
tables but does not add columns to existing SQLite tables, so migrations are
kept here as idempotent SQL and recorded in ``schema_migrations``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Engine, inspect, text


def _column_names(engine: Engine, table: str) -> set[str]:
    return {str(column["name"]) for column in inspect(engine).get_columns(table)}


def run_schema_migrations(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at DATETIME NOT NULL
                )
                """
            )
        )

        applied = {
            str(row[0])
            for row in connection.execute(text("SELECT version FROM schema_migrations")).fetchall()
        }
        version = "20260727_01_geochem_rule_provenance"
        if version not in applied:
            for table in ("geochem_mining_jobs", "geochem_reconstruct_jobs"):
                columns = _column_names(engine, table)
                if "rule_set_id" not in columns:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN rule_set_id VARCHAR"))
                if "source_variation_job_id" not in columns:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN source_variation_job_id VARCHAR"))
                connection.execute(
                    text(f"CREATE INDEX IF NOT EXISTS ix_{table}_rule_set_id ON {table} (rule_set_id)")
                )
                connection.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_{table}_source_variation_job_id "
                        f"ON {table} (source_variation_job_id)"
                    )
                )
            connection.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (:version, :applied_at)"),
                {"version": version, "applied_at": datetime.utcnow()},
            )

        version = "20260727_02_geochem_rule_completeness"
        if version not in applied:
            rule_set_columns = _column_names(engine, "geochem_rule_sets")
            for name, definition in (
                ("minimum_positive_count", "INTEGER NOT NULL DEFAULT 30"),
                ("merge_gap_m", "FLOAT NOT NULL DEFAULT 0.5"),
                ("minimum_segment_length_m", "FLOAT NOT NULL DEFAULT 0.0"),
                ("source_document", "TEXT"),
                ("confirmed_by", "INTEGER"),
            ):
                if name not in rule_set_columns:
                    connection.execute(text(f"ALTER TABLE geochem_rule_sets ADD COLUMN {name} {definition}"))

            element_columns = _column_names(engine, "geochem_element_rules")
            for name, definition in (
                ("role", "VARCHAR NOT NULL DEFAULT 'other'"),
                ("detection_limit", "FLOAT"),
                ("detection_limit_status", "VARCHAR NOT NULL DEFAULT 'pending'"),
                ("applicable_sample_medium", "VARCHAR NOT NULL DEFAULT 'drill_core_assay'"),
                ("notes", "TEXT"),
            ):
                if name not in element_columns:
                    connection.execute(text(f"ALTER TABLE geochem_element_rules ADD COLUMN {name} {definition}"))
            connection.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (:version, :applied_at)"),
                {"version": version, "applied_at": datetime.utcnow()},
            )

        version = "20260729_01_geochem_workflow_provenance"
        if version not in applied:
            mining_columns = _column_names(engine, "geochem_mining_jobs")
            for name, definition in (
                ("workflow_id", "VARCHAR"),
                ("dataset_hash", "VARCHAR"),
                ("algorithm_version", "VARCHAR"),
            ):
                if name not in mining_columns:
                    connection.execute(text(f"ALTER TABLE geochem_mining_jobs ADD COLUMN {name} {definition}"))
                connection.execute(
                    text(f"CREATE INDEX IF NOT EXISTS ix_geochem_mining_jobs_{name} ON geochem_mining_jobs ({name})")
                )

            reconstruct_columns = _column_names(engine, "geochem_reconstruct_jobs")
            for name, definition in (
                ("source_correlation_job_id", "VARCHAR"),
                ("workflow_id", "VARCHAR"),
                ("selected_clue_id", "VARCHAR"),
                ("dataset_hash", "VARCHAR"),
                ("algorithm_version", "VARCHAR"),
                ("algorithm_profile", "VARCHAR"),
                ("scene_manifest_path", "TEXT"),
                ("validation_status", "VARCHAR"),
            ):
                if name not in reconstruct_columns:
                    connection.execute(
                        text(f"ALTER TABLE geochem_reconstruct_jobs ADD COLUMN {name} {definition}")
                    )
                if definition != "TEXT":
                    connection.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS ix_geochem_reconstruct_jobs_{name} "
                            f"ON geochem_reconstruct_jobs ({name})"
                        )
                    )

            connection.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (:version, :applied_at)"),
                {"version": version, "applied_at": datetime.utcnow()},
            )

        version = "20260730_01_delivery_facing_geochem_labels"
        if version not in applied:
            # Historical seed data used internal project communication labels.
            # They must not leak into the delivery-facing system.
            replacements = (
                ("肖博士", "项目方"),
                ("王老师", "项目方"),
                ("师兄", "原始方案"),
                ("地质专家", "项目审核"),
                ("待专家确认", "待项目确认"),
                ("专家确认", "项目确认"),
            )
            for table, columns in (
                ("geochem_rule_sets", ("name", "notes", "source_document")),
                (
                    "geochem_element_rules",
                    ("boundary_source", "industrial_source", "notes"),
                ),
            ):
                if table not in inspect(engine).get_table_names():
                    continue
                available = _column_names(engine, table)
                for column in columns:
                    if column not in available:
                        continue
                    for old, new in replacements:
                        connection.execute(
                            text(
                                f"UPDATE {table} "
                                f"SET {column} = replace({column}, :old, :new) "
                                f"WHERE {column} LIKE :pattern"
                            ),
                            {"old": old, "new": new, "pattern": f"%{old}%"},
                        )
            connection.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (:version, :applied_at)"),
                {"version": version, "applied_at": datetime.utcnow()},
            )

        version = "20260730_02_delivery_facing_status_notes"
        if version not in applied:
            connection.execute(
                text(
                    "UPDATE geochem_rule_sets "
                    "SET notes = replace(notes, 'confirmed', '已确认版本') "
                    "WHERE notes LIKE '%confirmed%'"
                )
            )
            connection.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (:version, :applied_at)"),
                {"version": version, "applied_at": datetime.utcnow()},
            )

        version = "20260730_03_delivery_facing_historical_jobs"
        if version not in applied:
            replacements = (
                ("待专家确认", "待项目确认"),
                ("专家确认", "项目确认"),
                ("地质专家", "项目审核"),
                ("肖博士", "项目方"),
                ("王老师", "项目方"),
                ("师兄", "原始方案"),
                ("老师", "项目方"),
            )
            for table, columns in (
                ("geochem_mining_jobs", ("params_json", "summary_json", "error")),
                ("geochem_reconstruct_jobs", ("params_json", "summary_json", "error")),
                ("geochem_workflow_runs", ("limitations_json", "error")),
                ("geochem_candidate_clues", ("limitations_json", "professional_evidence_json")),
            ):
                if table not in inspect(engine).get_table_names():
                    continue
                available = _column_names(engine, table)
                for column in columns:
                    if column not in available:
                        continue
                    for old, new in replacements:
                        connection.execute(
                            text(
                                f"UPDATE {table} "
                                f"SET {column} = replace({column}, :old, :new) "
                                f"WHERE {column} LIKE :pattern"
                            ),
                            {"old": old, "new": new, "pattern": f"%{old}%"},
                        )
            connection.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (:version, :applied_at)"),
                {"version": version, "applied_at": datetime.utcnow()},
            )


def recover_interrupted_jobs(engine: Engine, older_than_minutes: int = 5) -> int:
    """Mark abandoned in-process jobs as failed after an application restart."""

    cutoff = datetime.utcnow() - timedelta(minutes=max(1, older_than_minutes))
    recovered = 0
    with engine.begin() as connection:
        for table in (
            "reconstruct_jobs",
            "deep_reconstruct_jobs",
            "geochem_reconstruct_jobs",
            "geochem_mining_jobs",
        ):
            if table not in inspect(engine).get_table_names():
                continue
            result = connection.execute(
                text(
                    f"""
                    UPDATE {table}
                    SET status = 'failed',
                        error = COALESCE(error, 'Application restarted before the in-process job completed'),
                        finished_at = COALESCE(finished_at, :finished_at)
                    WHERE status IN ('queued', 'running')
                      AND created_at < :cutoff
                    """
                ),
                {"cutoff": cutoff, "finished_at": datetime.utcnow()},
            )
            recovered += int(result.rowcount or 0)
    return recovered
