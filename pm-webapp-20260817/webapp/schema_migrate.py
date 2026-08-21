"""Idempotent SQLite schema maintenance for app startup.

This intentionally covers additive, backward-compatible changes only.
"""
import os
import shutil
import sqlite3
from datetime import datetime, timezone


GOAL_COLUMNS = {
    'owner_id': 'INTEGER REFERENCES user(id)',
    'reviewer_id': 'INTEGER REFERENCES user(id)',
    'status': "VARCHAR(20) DEFAULT 'active'",
    'start_date': 'DATE',
    'due_date': 'DATE',
    'actual_result': 'TEXT',
    'result_type': 'VARCHAR(20)',
    'merge_requested_at': 'DATETIME',
    'merge_requested_by_id': 'INTEGER REFERENCES user(id)',
    'merged_at': 'DATETIME',
    'merged_by_id': 'INTEGER REFERENCES user(id)',
    'merge_note': 'TEXT',
}

TASK_COLUMNS = {
    'last_checkin_at': 'DATETIME',
    'waiting_reason': 'TEXT',
    'waiting_until': 'DATE',
}

PROGRESS_LOG_COLUMNS = {
    'entry_type': "VARCHAR(20) DEFAULT 'progress'",
    'checkin_date': 'DATE',
}


def _table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _columns(conn, table):
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def _missing_columns(conn):
    missing = []
    for table, wanted in (
        ('goal', GOAL_COLUMNS),
        ('task', TASK_COLUMNS),
        ('progress_log', PROGRESS_LOG_COLUMNS),
    ):
        existing = _columns(conn, table)
        for name, ddl in wanted.items():
            if existing and name not in existing:
                missing.append((table, name, ddl))
    return missing


def _goal_members_missing(conn):
    return (
        _table_exists(conn, 'goal')
        and _table_exists(conn, 'user')
        and not _table_exists(conn, 'goal_members')
    )


def needs_migration(db_path):
    if not db_path or db_path == ':memory:' or not os.path.exists(db_path):
        return False
    with sqlite3.connect(db_path) as conn:
        return bool(_missing_columns(conn) or _goal_members_missing(conn))


def migrate_sqlite(db_path):
    """Apply additive schema updates to an existing SQLite DB.

    Returns a list of applied change labels. New empty DBs already created by
    SQLAlchemy usually need no work because models define the final schema.
    """
    if not db_path or db_path == ':memory:' or not os.path.exists(db_path):
        return []

    with sqlite3.connect(db_path) as conn:
        missing = _missing_columns(conn)
        create_goal_members = _goal_members_missing(conn)
        if not missing and not create_goal_members:
            return []

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    backup_path = f'{db_path}.bak-{timestamp}'
    shutil.copy2(db_path, backup_path)

    changes = [f'backup:{backup_path}']
    with sqlite3.connect(db_path) as conn:
        conn.execute('PRAGMA foreign_keys=off')
        for table, name, ddl in missing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
            changes.append(f'{table}.{name}')

        if create_goal_members:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS goal_members (
                    goal_id INTEGER NOT NULL REFERENCES goal(id),
                    user_id INTEGER NOT NULL REFERENCES user(id),
                    PRIMARY KEY (goal_id, user_id)
                )
            """)
            changes.append('goal_members')

        if _table_exists(conn, 'goal'):
            goal_cols = _columns(conn, 'goal')
            if 'status' in goal_cols:
                conn.execute("UPDATE goal SET status='active' WHERE status IS NULL OR status=''")
            if 'owner_id' in goal_cols and 'project' in {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }:
                conn.execute("""
                    UPDATE goal
                    SET owner_id = (
                        SELECT lead_id FROM project WHERE project.id = goal.project_id
                    )
                    WHERE owner_id IS NULL
                """)
            if 'result_type' in goal_cols:
                conn.execute("""
                    UPDATE goal
                    SET result_type = NULL
                    WHERE result_type IS NOT NULL
                      AND result_type NOT IN ('achieved', 'answered', 'cancelled', 'transferred')
                """)

        if _table_exists(conn, 'task'):
            task_cols = _columns(conn, 'task')
            if 'status' in task_cols:
                conn.execute("""
                    UPDATE task
                    SET status='pending'
                    WHERE status IS NULL
                       OR status NOT IN ('pending', 'in_progress', 'waiting', 'completed')
                """)

        if _table_exists(conn, 'progress_log'):
            log_cols = _columns(conn, 'progress_log')
            if 'entry_type' in log_cols:
                conn.execute("""
                    UPDATE progress_log
                    SET entry_type='progress'
                    WHERE entry_type IS NULL
                       OR entry_type NOT IN ('progress', 'no_progress', 'waiting', 'resumed')
                """)
            if 'checkin_date' in log_cols and 'created_at' in log_cols:
                conn.execute("""
                    UPDATE progress_log
                    SET checkin_date = date(created_at)
                    WHERE checkin_date IS NULL
                """)

        conn.commit()
    return changes
