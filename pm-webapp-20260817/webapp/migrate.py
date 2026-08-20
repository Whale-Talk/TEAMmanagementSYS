"""一次性数据迁移脚本：Ticket 合并进 Task，Goal 降级为标签。

用法：python migrate.py
迁移前会自动备份 database.db。
"""
import sqlite3
import shutil
from datetime import datetime

DB = 'database.db'
BACKUP = f'database.db.backup-迁移前-{datetime.now().strftime("%Y%m%d-%H%M%S")}'


def main():
    # 1. 备份
    shutil.copy2(DB, BACKUP)
    print(f'✅ 已备份到 {BACKUP}')

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    old_task = cur.execute('SELECT COUNT(*) FROM task').fetchone()[0]
    old_ticket = cur.execute('SELECT COUNT(*) FROM ticket').fetchone()[0]
    old_discussion = cur.execute('SELECT COUNT(*) FROM discussion').fetchone()[0]
    print(f'迁移前: task={old_task}, ticket={old_ticket}, discussion={old_discussion}')

    # 2. task 表新增字段
    task_cols = [r[1] for r in cur.execute('PRAGMA table_info(task)').fetchall()]
    new_cols = [
        ('project_id', 'INTEGER'),
        ('priority', "VARCHAR(20) DEFAULT 'medium'"),
        ('submitter_id', 'INTEGER'),
        ('reviewer_id', 'INTEGER'),
        ('solution', 'TEXT'),
        ('resolved_at', 'DATETIME'),
        ('closed_at', 'DATETIME'),
    ]
    for name, ddl in new_cols:
        if name not in task_cols:
            cur.execute(f'ALTER TABLE task ADD COLUMN {name} {ddl}')
            print(f'  task 新增列 {name}')

    # 3. 给现有 task 补 project_id
    cur.execute('''
        UPDATE task SET project_id = (
            SELECT project_id FROM goal WHERE goal.id = task.goal_id
        )
    ''')
    print('✅ 已给现有 task 补 project_id')

    # 4. ticket → task
    ticket_cols = [r[1] for r in cur.execute('PRAGMA table_info(ticket)').fetchall()]
    col_idx = {name: i for i, name in enumerate(ticket_cols)}

    def tget(row, name):
        return row[col_idx[name]] if name in col_idx else None

    status_map = {
        'pending': 'pending',
        'discussing': 'in_progress',
        'in_progress': 'in_progress',
        'resolved': 'completed',
        'closed': 'completed',
    }

    tickets = cur.execute('SELECT * FROM ticket').fetchall()
    migrated = 0
    skipped = 0
    for row in tickets:
        ticket_id = tget(row, 'id')
        task_id = tget(row, 'task_id')
        # 找原 task 的 goal_id 和 project_id
        origin = cur.execute(
            'SELECT goal_id, project_id FROM task WHERE id = ?', (task_id,)
        ).fetchone()
        if origin is None:
            print(f'  ⚠️ ticket {ticket_id} 的原 task {task_id} 不存在，跳过')
            skipped += 1
            continue
        origin_goal_id, project_id = origin

        cur.execute('''
            INSERT INTO task (project_id, goal_id, title, description, status, priority,
                              submitter_id, assignee_id, reviewer_id, due_date, solution,
                              created_at, resolved_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            project_id,
            origin_goal_id,
            tget(row, 'title'),
            tget(row, 'description') or '',
            status_map.get(tget(row, 'status'), 'pending'),
            tget(row, 'priority') or 'medium',
            tget(row, 'submitter_id'),
            tget(row, 'assignee_id'),
            tget(row, 'reviewer_id'),
            tget(row, 'due_date'),
            tget(row, 'solution'),
            tget(row, 'created_at'),
            tget(row, 'resolved_at'),
            tget(row, 'closed_at'),
        ))
        migrated += 1

    print(f'✅ 迁移了 {migrated} 条 ticket → task（跳过 {skipped} 条）')

    # 5. 删除 ticket 和 discussion 表
    for table in ['ticket', 'discussion', 'task_dependencies', 'goal_dependencies']:
        try:
            cur.execute(f'DROP TABLE IF EXISTS {table}')
            print(f'  已删除表 {table}')
        except Exception as e:
            print(f'  ⚠️ 删除 {table} 失败: {e}')

    conn.commit()

    # 6. 校验
    new_task = cur.execute('SELECT COUNT(*) FROM task').fetchone()[0]
    print(f'\n迁移后: task={new_task}')
    expected = old_task + migrated
    if new_task == expected:
        print(f'✅ 对账通过: {new_task} = {old_task}(原task) + {migrated}(原ticket)')
    else:
        print(f'❌ 对账失败: 期望 {expected}，实际 {new_task}')

    conn.close()
    print('\n迁移完成。')


if __name__ == '__main__':
    main()
