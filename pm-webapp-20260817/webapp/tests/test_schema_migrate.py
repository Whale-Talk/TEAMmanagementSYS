import os
import sqlite3
import tempfile
import unittest

from schema_migrate import migrate_sqlite


class SchemaMigrationTest(unittest.TestCase):
    def test_empty_database_is_left_for_create_all(self):
        handle = tempfile.NamedTemporaryFile(
            prefix='pm-webapp-empty-', suffix='.db', delete=False
        )
        db_path = handle.name
        handle.close()
        try:
            self.assertEqual(migrate_sqlite(db_path), [])
            with sqlite3.connect(db_path) as conn:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            self.assertEqual(tables, [])
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_additive_migration_upgrades_legacy_database(self):
        handle = tempfile.NamedTemporaryFile(
            prefix='pm-webapp-legacy-', suffix='.db', delete=False
        )
        db_path = handle.name
        handle.close()
        backup_path = None
        try:
            with sqlite3.connect(db_path) as conn:
                conn.executescript("""
                    CREATE TABLE user (id INTEGER PRIMARY KEY);
                    CREATE TABLE project (
                        id INTEGER PRIMARY KEY,
                        lead_id INTEGER
                    );
                    CREATE TABLE goal (
                        id INTEGER PRIMARY KEY,
                        project_id INTEGER NOT NULL,
                        title VARCHAR(200) NOT NULL
                    );
                    CREATE TABLE task (
                        id INTEGER PRIMARY KEY,
                        status VARCHAR(20)
                    );
                    CREATE TABLE progress_log (
                        id INTEGER PRIMARY KEY,
                        created_at DATETIME
                    );
                    INSERT INTO user (id) VALUES (1);
                    INSERT INTO project (id, lead_id) VALUES (1, 1);
                    INSERT INTO goal (id, project_id, title) VALUES (1, 1, '旧目标');
                    INSERT INTO task (id, status) VALUES (1, 'unknown');
                    INSERT INTO progress_log (id, created_at)
                    VALUES (1, '2026-08-21 08:30:00');
                """)

            changes = migrate_sqlite(db_path)
            backup_path = next(
                item.removeprefix('backup:')
                for item in changes
                if item.startswith('backup:')
            )

            with sqlite3.connect(db_path) as conn:
                goal_columns = {
                    row[1] for row in conn.execute('PRAGMA table_info(goal)')
                }
                task_columns = {
                    row[1] for row in conn.execute('PRAGMA table_info(task)')
                }
                log_columns = {
                    row[1] for row in conn.execute('PRAGMA table_info(progress_log)')
                }
                goal = conn.execute(
                    'SELECT owner_id, status FROM goal WHERE id=1'
                ).fetchone()
                task_status = conn.execute(
                    'SELECT status FROM task WHERE id=1'
                ).fetchone()[0]
                log = conn.execute(
                    'SELECT entry_type, checkin_date FROM progress_log WHERE id=1'
                ).fetchone()
                goal_members_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='goal_members'"
                ).fetchone()

            self.assertIn('owner_id', goal_columns)
            self.assertIn('last_checkin_at', task_columns)
            self.assertIn('checkin_date', log_columns)
            self.assertEqual(goal, (1, 'active'))
            self.assertEqual(task_status, 'pending')
            self.assertEqual(log, ('progress', '2026-08-21'))
            self.assertIsNotNone(goal_members_exists)
            self.assertTrue(os.path.exists(backup_path))
        finally:
            for path in (db_path, backup_path):
                if path and os.path.exists(path):
                    os.unlink(path)


if __name__ == '__main__':
    unittest.main()
