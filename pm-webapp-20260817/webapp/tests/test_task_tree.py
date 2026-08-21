import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path


WEBAPP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_DIR))

_db_file = tempfile.NamedTemporaryFile(prefix='pm-webapp-test-', suffix='.db', delete=False)
_db_file.close()
os.environ['PM_DATABASE_PATH'] = _db_file.name
os.environ['SECRET_KEY'] = 'test-secret-key'

import app as app_module
from models import db, Goal, ProgressLog, Project, Task, User


class TaskTreeFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.app.config.update(TESTING=True)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        try:
            os.unlink(_db_file.name)
        except FileNotFoundError:
            pass

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()

            admin = User(
                username='admin',
                password_hash='test',
                display_name='管理员',
                role='admin',
            )
            owner = User(
                username='owner',
                password_hash='test',
                display_name='目标负责人',
                role='member',
            )
            reviewer = User(
                username='reviewer',
                password_hash='test',
                display_name='目标验收人',
                role='member',
            )
            member = User(
                username='member',
                password_hash='test',
                display_name='参与成员',
                role='member',
            )
            db.session.add_all([admin, owner, reviewer, member])
            db.session.flush()

            project = Project(
                name='25D 飞机',
                description='项目主线',
                lead_id=admin.id,
                status='active',
            )
            db.session.add(project)
            db.session.flush()

            goal = Goal(
                project_id=project.id,
                title='燃料电池系统',
                description='形成可验收的燃料电池方案',
                owner_id=owner.id,
                reviewer_id=reviewer.id,
                status='active',
                start_date=date(2026, 8, 1),
                due_date=date(2026, 8, 31),
                actual_result='待提交的结果草稿',
                result_type='answered',
            )
            goal.members.append(member)
            db.session.add(goal)
            db.session.flush()

            completed_task = Task(
                project_id=project.id,
                goal_id=goal.id,
                title='完成电堆选型',
                assignee_id=owner.id,
                status='completed',
                completed_at=datetime.now(),
            )
            daily_task = Task(
                project_id=project.id,
                title='跟进供应商反馈',
                assignee_id=owner.id,
                status='in_progress',
                due_date=date.today(),
            )
            db.session.add_all([completed_task, daily_task])
            db.session.commit()

            self.ids = {
                'admin': admin.id,
                'owner': owner.id,
                'reviewer': reviewer.id,
                'member': member.id,
                'project': project.id,
                'goal': goal.id,
                'completed_task': completed_task.id,
                'daily_task': daily_task.id,
            }

    def login(self, user_key):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.ids[user_key])
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def post(self, path, data):
        payload = {'csrf_token': 'test-csrf-token'}
        payload.update(data)
        return self.client.post(path, data=payload)

    def test_ready_tree_and_project_render_closure_form(self):
        self.login('owner')

        tree_response = self.client.get('/task-tree?status=ready')
        project_response = self.client.get(f"/project/{self.ids['project']}")

        self.assertEqual(tree_response.status_code, 200)
        self.assertEqual(project_response.status_code, 200)
        tree_html = tree_response.get_data(as_text=True)
        project_html = project_response.get_data(as_text=True)
        for html in (tree_html, project_html):
            self.assertIn('燃料电池系统', html)
            self.assertIn('申请闭环', html)
            self.assertIn('value="achieved"', html)
            self.assertIn('value="transferred"', html)

    def test_primary_authenticated_pages_render(self):
        self.login('admin')
        paths = (
            '/',
            '/my-work',
            '/task-tree',
            f"/project/{self.ids['project']}",
            f"/project/{self.ids['project']}/goals",
            f"/project/{self.ids['project']}/task/new",
            f"/task/{self.ids['completed_task']}",
            '/progress',
            '/blockers',
            '/people',
            '/calendar',
            '/users',
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_goal_creation_persists_owner_reviewer_and_members(self):
        self.login('admin')
        response = self.post(
            f"/project/{self.ids['project']}/goal/new",
            {
                'title': '电池系统',
                'description': '补充能源分支',
                'deliverable': '电池选型结论',
                'owner_id': str(self.ids['owner']),
                'reviewer_id': str(self.ids['reviewer']),
                'start_date': '2026-08-10',
                'due_date': '2026-09-10',
                'members': [str(self.ids['owner']), str(self.ids['member'])],
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            goal = Goal.query.filter_by(title='电池系统').one()
            self.assertEqual(goal.owner_id, self.ids['owner'])
            self.assertEqual(goal.reviewer_id, self.ids['reviewer'])
            self.assertEqual(goal.start_date, date(2026, 8, 10))
            self.assertEqual(goal.due_date, date(2026, 9, 10))
            self.assertEqual(
                [user.id for user in goal.members.order_by(User.id).all()],
                sorted([self.ids['owner'], self.ids['member']]),
            )

    def test_merge_review_and_reopen_lifecycle(self):
        self.login('owner')
        response = self.post(
            f"/goal/{self.ids['goal']}/merge-request",
            {
                'actual_result': '已形成完整选型结论',
                'result_type': 'achieved',
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.status, 'merge_requested')
            self.assertEqual(goal.merge_requested_by_id, self.ids['owner'])

        response = self.post(
            f"/task/{self.ids['completed_task']}/quick-status",
            {'status': 'in_progress'},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            task = db.session.get(Task, self.ids['completed_task'])
            self.assertEqual(task.status, 'completed')

        self.login('reviewer')
        response = self.post(
            f"/goal/{self.ids['goal']}/merge-review",
            {'decision': 'approve', 'merge_note': '验收通过'},
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.status, 'merged')
            self.assertEqual(goal.merge_note, '验收通过')
            self.assertEqual(goal.merged_by_id, self.ids['reviewer'])

        self.login('owner')
        response = self.post(
            f"/goal/{self.ids['goal']}/merge-request",
            {'actual_result': '绕过重开', 'result_type': 'achieved'},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.status, 'merged')

        self.login('reviewer')
        response = self.post(
            f"/goal/{self.ids['goal']}/reopen",
            {'reason': '新增验证范围'},
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.status, 'active')
            self.assertIsNone(goal.merge_requested_at)
            self.assertIsNone(goal.merge_requested_by_id)
            self.assertIsNone(goal.merged_at)
            self.assertIsNone(goal.merged_by_id)

    def test_daily_checkin_clears_today_pending_signal(self):
        self.login('owner')

        before = self.client.get('/my-work').get_data(as_text=True)
        self.assertIn('仍待更新 1', before)

        response = self.post(
            f"/task/{self.ids['daily_task']}/progress",
            {'entry_type': 'no_progress', 'content': ''},
        )
        self.assertEqual(response.status_code, 302)

        after = self.client.get('/my-work').get_data(as_text=True)
        self.assertIn('今日已更新 1', after)
        self.assertIn('仍待更新 0', after)
        with self.app.app_context():
            log = ProgressLog.query.filter_by(task_id=self.ids['daily_task']).one()
            self.assertEqual(log.entry_type, 'no_progress')
            self.assertEqual(log.checkin_date, date.today())

    def test_legacy_goal_edit_preserves_new_lifecycle_fields(self):
        self.login('admin')

        response = self.post(
            f"/goal/{self.ids['goal']}/edit",
            {
                'title': '燃料电池系统改名',
                'description': '更新说明',
                'deliverable': '更新产出',
            },
        )
        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.title, '燃料电池系统改名')
            self.assertEqual(goal.start_date, date(2026, 8, 1))
            self.assertEqual(goal.due_date, date(2026, 8, 31))
            self.assertEqual(goal.actual_result, '待提交的结果草稿')
            self.assertEqual(goal.result_type, 'answered')
            self.assertEqual(
                [user.id for user in goal.members.order_by(User.id).all()],
                [self.ids['member']],
            )


if __name__ == '__main__':
    unittest.main()
