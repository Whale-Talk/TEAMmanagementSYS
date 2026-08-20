"""修复 task 表 schema：goal_id 改可空，project_id 改必填，删除废弃列。

用法：python fix_task_schema.py
"""
import sqlite3
import shutil
from datetime import datetime

DB = 'database.db'
BACKUP = f'database.db.backup-修schema前-{datetime.now().strftime("%Y%m%d-%H%M%S")}'


def main():
    shutil.copy2(DB, BACKUP)
    print(f'已备份到 {BACKUP}')

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # 关闭外键约束，避免重建表时被引用检查干扰
    cur.execute('PRAGMA foreign_keys=OFF')

    # 新表结构（只保留新模型需要的列）
    cur.execute('''
        CREATE TABLE task_new (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            goal_id INTEGER,
            title VARCHAR(200) NOT NULL,
            description TEXT,
            "order" INTEGER,
            status VARCHAR(20),
            priority VARCHAR(20),
            assignee_id INTEGER,
            submitter_id INTEGER,
            reviewer_id INTEGER,
            start_date DATE,
            due_date DATE,
            solution TEXT,
            created_at DATETIME,
            completed_at DATETIME,
            FOREIGN KEY(project_id) REFERENCES project (id),
            FOREIGN KEY(goal_id) REFERENCES goal (id),
            FOREIGN KEY(assignee_id) REFERENCES user (id),
            FOREIGN KEY(submitter_id) REFERENCES user (id),
            FOREIGN KEY(reviewer_id) REFERENCES user (id)
        )
    ''')

    # 复制数据（project_id 为 NULL 的补一个默认值，避免 NOT NULL 失败）
    # 找出任何 project_id 为 NULL 的任务，先看有多少
    null_proj = cur.execute('SELECT COUNT(*) FROM task WHERE project_id IS NULL').fetchone()[0]
    print(f'project_id 为 NULL 的任务数: {null_proj}')
    if null_proj > 0:
        # 找到第一个项目作为兜底
        first_pid = cur.execute('SELECT id FROM project LIMIT 1').fetchone()
        if first_pid:
            cur.execute('UPDATE task SET project_id = ? WHERE project_id IS NULL', (first_pid[0],))
            print(f'已将 {null_proj} 个任务的 project_id 补为 {first_pid[0]}')

    cur.execute('''
        INSERT INTO task_new (id, project_id, goal_id, title, description, "order",
                              status, priority, assignee_id, submitter_id, reviewer_id,
                              start_date, due_date, solution, created_at, completed_at)
        SELECT id, project_id, goal_id, title, description, "order",
               status, priority, assignee_id, submitter_id, reviewer_id,
               start_date, due_date, solution, created_at, completed_at
        FROM task
    ''')

    # 删除旧表，重命名新表
    cur.execute('DROP TABLE task')
    cur.execute('ALTER TABLE task_new RENAME TO task')

    conn.commit()
    cur.execute('PRAGMA foreign_keys=ON')

    # 校验
    cnt = cur.execute('SELECT COUNT(*) FROM task').fetchone()[0]
    print(f'重建后 task 行数: {cnt}')
    cols = cur.execute('PRAGMA table_info(task)').fetchall()
    print('新表列:', [c[1] for c in cols])

    conn.close()
    print('修复完成。')


if __name__ == '__main__':
    main()
