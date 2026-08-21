import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path


WEBAPP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_DIR))

_db_file = tempfile.NamedTemporaryFile(
    prefix='pm-webapp-permissions-', suffix='.db', delete=False
)
_db_file.close()
os.environ.setdefault('PM_DATABASE_PATH', _db_file.name)
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

import app as app_module
from models import db, Goal, ProgressLog, Project, Task, User


class PermissionVisibilityTest(unittest.TestCase):
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

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()

            users = {
                'admin': User(username='admin', password_hash='x', display_name='Admin', role='admin'),
                'lead': User(username='lead', password_hash='x', display_name='Project Lead', role='member'),
                'project_member': User(username='pmember', password_hash='x', display_name='Project Member', role='project_member'),
                'owner': User(username='owner', password_hash='x', display_name='Goal Owner', role='member'),
                'reviewer': User(username='reviewer', password_hash='x', display_name='Goal Reviewer', role='member'),
                'assignee': User(username='assignee', password_hash='x', display_name='Task Assignee', role='member'),
                'task_member': User(username='taskmember', password_hash='x', display_name='Task Member', role='member'),
                'ordinary': User(username='ordinary', password_hash='x', display_name='Ordinary User', role='member'),
                'unrelated': User(username='unrelated', password_hash='x', display_name='Unrelated User', role='member'),
            }
            db.session.add_all(users.values())
            db.session.flush()

            visible = Project(
                name='Visible Project',
                description='visible project body',
                lead_id=users['lead'].id,
                status='active',
            )
            hidden = Project(
                name='Hidden Project Secret',
                description='hidden project body',
                lead_id=users['unrelated'].id,
                status='active',
            )
            db.session.add_all([visible, hidden])
            db.session.flush()
            users['project_member'].member_projects.append(visible)

            goal = Goal(
                project_id=visible.id,
                title='Visible Branch',
                owner_id=users['owner'].id,
                reviewer_id=users['reviewer'].id,
                status='active',
                due_date=date.today() + timedelta(days=7),
                order=1,
            )
            hidden_goal = Goal(
                project_id=hidden.id,
                title='Hidden Branch Secret',
                owner_id=users['unrelated'].id,
                reviewer_id=users['unrelated'].id,
                status='active',
                order=1,
            )
            goal.members.append(users['ordinary'])
            db.session.add_all([goal, hidden_goal])
            db.session.flush()

            assigned = Task(
                project_id=visible.id,
                goal_id=goal.id,
                title='Assigned Visible Task',
                assignee_id=users['assignee'].id,
                status='in_progress',
                due_date=date.today() + timedelta(days=1),
            )
            member_task = Task(
                project_id=visible.id,
                goal_id=goal.id,
                title='Member Visible Task',
                status='pending',
            )
            stale_waiting = Task(
                project_id=visible.id,
                goal_id=goal.id,
                title='Waiting Stale Visible Task',
                assignee_id=users['owner'].id,
                status='waiting',
                due_date=date.today() - timedelta(days=1),
                last_checkin_at=datetime.utcnow() - timedelta(days=5),
                waiting_reason='external dependency',
            )
            hidden_task = Task(
                project_id=hidden.id,
                goal_id=hidden_goal.id,
                title='Hidden Task Secret',
                assignee_id=users['unrelated'].id,
                status='in_progress',
                due_date=date.today() + timedelta(days=1),
            )
            member_task.members.append(users['task_member'])
            db.session.add_all([assigned, member_task, stale_waiting, hidden_task])
            db.session.commit()

            self.ids = {name: user.id for name, user in users.items()}
            self.ids.update({
                'visible_project': visible.id,
                'hidden_project': hidden.id,
                'goal': goal.id,
                'hidden_goal': hidden_goal.id,
                'assigned_task': assigned.id,
                'member_task': member_task.id,
                'stale_waiting_task': stale_waiting.id,
                'hidden_task': hidden_task.id,
            })

    def login(self, user_key):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.ids[user_key])
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def post(self, path, data=None):
        payload = {'csrf_token': 'test-csrf-token'}
        payload.update(data or {})
        return self.client.post(path, data=payload)

    def assert_redirects_to_index(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def create_project_for_lead(self, name='Lead Positive Project'):
        lead = db.session.get(User, self.ids['lead'])
        project = Project(name=name, lead_id=lead.id, status='active')
        db.session.add(project)
        db.session.flush()
        return project

    def create_active_goal(self, project, title='Lead Positive Goal'):
        owner = db.session.get(User, self.ids['owner'])
        reviewer = db.session.get(User, self.ids['reviewer'])
        goal = Goal(
            project_id=project.id,
            title=title,
            owner_id=owner.id,
            reviewer_id=reviewer.id,
            status='active',
        )
        db.session.add(goal)
        db.session.flush()
        return goal

    def create_active_task(self, project, goal=None, title='Lead Positive Task'):
        assignee = db.session.get(User, self.ids['assignee'])
        task = Task(
            project_id=project.id,
            goal_id=goal.id if goal else None,
            title=title,
            assignee_id=assignee.id,
            status='pending',
        )
        db.session.add(task)
        db.session.flush()
        return task

    def test_view_project_permission_does_not_grant_manage_project_permission(self):
        with self.app.app_context():
            user = db.session.get(User, self.ids['owner'])
            project = db.session.get(Project, self.ids['visible_project'])

            self.assertTrue(user.can_view_project(project))
            self.assertFalse(user.can_manage_project(project))

    def test_project_member_can_manage_project_without_admin_role(self):
        with self.app.app_context():
            user = db.session.get(User, self.ids['project_member'])
            project = db.session.get(Project, self.ids['visible_project'])

            self.assertTrue(user.can_manage_project(project))
            self.assertFalse(user.is_admin())

    def test_reviewer_can_review_goal_without_managing_goal(self):
        with self.app.app_context():
            user = db.session.get(User, self.ids['reviewer'])
            goal = db.session.get(Goal, self.ids['goal'])

            self.assertTrue(user.can_review_goal(goal))
            self.assertFalse(user.can_manage_goal(goal))

    def test_task_member_can_log_progress_without_editing_task(self):
        with self.app.app_context():
            user = db.session.get(User, self.ids['task_member'])
            task = db.session.get(Task, self.ids['member_task'])

            self.assertTrue(user.can_log_task_progress(task))
            self.assertFalse(user.can_edit_task(task))

    def test_unrelated_user_cannot_view_hidden_project_goal_or_task(self):
        with self.app.app_context():
            user = db.session.get(User, self.ids['ordinary'])
            hidden_project = db.session.get(Project, self.ids['hidden_project'])
            hidden_goal = db.session.get(Goal, self.ids['hidden_goal'])
            hidden_task = db.session.get(Task, self.ids['hidden_task'])

            self.assertFalse(user.can_view_project(hidden_project))
            self.assertFalse(user.can_view_goal(hidden_goal))
            self.assertFalse(user.can_view_task(hidden_task))

    def test_admin_defaults_to_overview_scope(self):
        with self.app.app_context():
            user = db.session.get(User, self.ids['admin'])

            scope, can_overview = app_module.resolve_view_scope(user)

            self.assertEqual(scope, 'overview')
            self.assertTrue(can_overview)

    def test_ordinary_user_overview_request_is_downgraded_to_mine(self):
        with self.app.app_context():
            user = db.session.get(User, self.ids['ordinary'])

            scope, can_overview = app_module.resolve_view_scope(user, 'overview')

            self.assertEqual(scope, 'mine')
            self.assertFalse(can_overview)

    def test_hidden_project_direct_get_returns_404(self):
        self.login('ordinary')

        response = self.client.get(f"/project/{self.ids['hidden_project']}")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Hidden Project Secret', response.get_data(as_text=True))

    def test_hidden_goal_manage_page_returns_404(self):
        self.login('ordinary')

        response = self.client.get(f"/project/{self.ids['hidden_project']}/goals")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Hidden Branch Secret', response.get_data(as_text=True))

    def test_hidden_task_direct_get_returns_404(self):
        self.login('ordinary')

        response = self.client.get(f"/task/{self.ids['hidden_task']}")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Hidden Task Secret', response.get_data(as_text=True))

    def test_member_read_surfaces_do_not_render_hidden_names(self):
        self.login('ordinary')

        for path in ('/', '/progress', '/blockers', '/people', '/calendar', '/task-tree?scope=overview'):
            with self.subTest(path=path):
                response = self.client.get(path)
                html = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('Hidden Project Secret', html)
                self.assertNotIn('Hidden Branch Secret', html)
                self.assertNotIn('Hidden Task Secret', html)

    def test_my_todos_api_excludes_hidden_tasks(self):
        self.login('ordinary')

        response = self.client.get('/api/my-todos')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        titles = {task['title'] for task in payload['tasks']}
        self.assertNotIn('Hidden Task Secret', titles)

    def test_forbidden_project_move_redirects_to_safe_page_without_mutating_order(self):
        self.login('ordinary')

        response = self.post(f"/project/{self.ids['visible_project']}/move/up")

        self.assert_redirects_to_index(response)

    def test_forbidden_project_priority_move_redirects_to_safe_page_without_mutating_priority(self):
        self.login('ordinary')

        response = self.post(f"/project/{self.ids['visible_project']}/priority/up")

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            project = db.session.get(Project, self.ids['visible_project'])
            self.assertEqual(project.sort_order, 0)

    def test_forbidden_project_toggle_pin_redirects_to_safe_page_without_pinning_hidden_project(self):
        self.login('ordinary')

        response = self.post(f"/project/{self.ids['hidden_project']}/pin")

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            user = db.session.get(User, self.ids['ordinary'])
            self.assertNotIn(self.ids['hidden_project'], [p.id for p in user.pinned_projects])

    def test_forbidden_project_edit_redirects_to_safe_page_without_renaming_project(self):
        self.login('ordinary')

        response = self.post(
            f"/project/{self.ids['visible_project']}/edit",
            {'name': 'Unauthorized Project Name', 'status': 'active'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            project = db.session.get(Project, self.ids['visible_project'])
            self.assertEqual(project.name, 'Visible Project')

    def test_forbidden_goal_new_redirects_to_safe_page_without_creating_goal(self):
        self.login('ordinary')

        response = self.post(
            f"/project/{self.ids['visible_project']}/goal/new",
            {'title': 'Unauthorized New Goal'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            self.assertIsNone(Goal.query.filter_by(title='Unauthorized New Goal').first())

    def test_forbidden_task_new_redirects_to_safe_page_without_creating_task(self):
        self.login('ordinary')

        response = self.post(
            f"/project/{self.ids['visible_project']}/task/new",
            {'title': 'Unauthorized New Task'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            self.assertIsNone(Task.query.filter_by(title='Unauthorized New Task').first())

    def test_forbidden_goal_edit_redirects_to_safe_page_without_renaming_goal(self):
        self.login('ordinary')

        response = self.post(
            f"/goal/{self.ids['goal']}/edit",
            {'title': 'Unauthorized Goal Name'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.title, 'Visible Branch')

    def test_forbidden_goal_delete_redirects_to_safe_page_without_deleting_goal(self):
        self.login('ordinary')

        response = self.post(f"/goal/{self.ids['goal']}/delete")

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Goal, self.ids['goal']))

    def test_forbidden_task_edit_redirects_to_safe_page_without_renaming_task(self):
        self.login('ordinary')

        response = self.post(
            f"/task/{self.ids['assigned_task']}/edit",
            {'title': 'Unauthorized Task Name', 'status': 'completed'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            task = db.session.get(Task, self.ids['assigned_task'])
            self.assertEqual(task.title, 'Assigned Visible Task')
            self.assertEqual(task.status, 'in_progress')

    def test_forbidden_task_quick_status_redirects_to_safe_page_without_status_change(self):
        self.login('ordinary')

        response = self.post(
            f"/task/{self.ids['assigned_task']}/quick-status",
            {'status': 'completed'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            task = db.session.get(Task, self.ids['assigned_task'])
            self.assertEqual(task.status, 'in_progress')

    def test_forbidden_task_delete_redirects_to_safe_page_without_deleting_task(self):
        self.login('ordinary')

        response = self.post(f"/task/{self.ids['assigned_task']}/delete")

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Task, self.ids['assigned_task']))

    def test_forbidden_merge_request_redirects_to_safe_page_without_state_change(self):
        self.login('ordinary')

        response = self.post(
            f"/goal/{self.ids['goal']}/merge-request",
            {'actual_result': 'attempt', 'result_type': 'achieved'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.status, 'active')

    def test_forbidden_merge_review_redirects_to_safe_page_without_state_change(self):
        self.login('ordinary')
        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            goal.status = 'merge_requested'
            goal.merge_requested_by_id = self.ids['owner']
            db.session.commit()

        response = self.post(
            f"/goal/{self.ids['goal']}/merge-review",
            {'decision': 'approve', 'merge_note': 'unauthorized'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.status, 'merge_requested')
            self.assertIsNone(goal.merged_by_id)

    def test_forbidden_goal_reopen_redirects_to_safe_page_without_state_change(self):
        self.login('ordinary')
        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            goal.status = 'merged'
            db.session.commit()

        response = self.post(
            f"/goal/{self.ids['goal']}/reopen",
            {'reason': 'unauthorized'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.status, 'merged')

    def test_forbidden_task_solution_redirects_to_safe_page_without_solution_change(self):
        self.login('ordinary')

        response = self.post(
            f"/task/{self.ids['assigned_task']}/solution",
            {'solution': 'unauthorized solution'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            task = db.session.get(Task, self.ids['assigned_task'])
            self.assertIsNone(task.solution)

    def test_forbidden_task_progress_redirects_to_safe_page_without_progress_log(self):
        self.login('ordinary')

        response = self.post(
            f"/task/{self.ids['assigned_task']}/progress",
            {'entry_type': 'progress', 'content': 'unauthorized progress'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            self.assertEqual(
                ProgressLog.query.filter_by(task_id=self.ids['assigned_task']).count(),
                0,
            )

    def test_hidden_and_missing_mutation_targets_have_identical_responses(self):
        self.login('ordinary')
        missing_id = 999999
        cases = (
            (
                f"/project/{self.ids['hidden_project']}/priority/up",
                f'/project/{missing_id}/priority/up',
            ),
            (
                f"/project/{self.ids['hidden_project']}/pin",
                f'/project/{missing_id}/pin',
            ),
            (
                f"/project/{self.ids['hidden_project']}/edit",
                f'/project/{missing_id}/edit',
            ),
            (
                f"/project/{self.ids['hidden_project']}/goal/new",
                f'/project/{missing_id}/goal/new',
            ),
            (
                f"/project/{self.ids['hidden_project']}/task/new",
                f'/project/{missing_id}/task/new',
            ),
            (
                f"/goal/{self.ids['hidden_goal']}/edit",
                f'/goal/{missing_id}/edit',
            ),
            (
                f"/goal/{self.ids['hidden_goal']}/delete",
                f'/goal/{missing_id}/delete',
            ),
            (
                f"/goal/{self.ids['hidden_goal']}/merge-request",
                f'/goal/{missing_id}/merge-request',
            ),
            (
                f"/goal/{self.ids['hidden_goal']}/merge-review",
                f'/goal/{missing_id}/merge-review',
            ),
            (
                f"/goal/{self.ids['hidden_goal']}/reopen",
                f'/goal/{missing_id}/reopen',
            ),
            (
                f"/task/{self.ids['hidden_task']}/edit",
                f'/task/{missing_id}/edit',
            ),
            (
                f"/task/{self.ids['hidden_task']}/quick-status",
                f'/task/{missing_id}/quick-status',
            ),
            (
                f"/task/{self.ids['hidden_task']}/solution",
                f'/task/{missing_id}/solution',
            ),
            (
                f"/task/{self.ids['hidden_task']}/progress",
                f'/task/{missing_id}/progress',
            ),
            (
                f"/task/{self.ids['hidden_task']}/delete",
                f'/task/{missing_id}/delete',
            ),
        )

        for hidden_path, missing_path in cases:
            with self.subTest(hidden_path=hidden_path):
                hidden_response = self.post(hidden_path)
                missing_response = self.post(missing_path)
                self.assertEqual(hidden_response.status_code, missing_response.status_code)
                self.assertEqual(hidden_response.headers['Location'], missing_response.headers['Location'])
                self.assert_redirects_to_index(hidden_response)

    def test_invalid_goal_date_is_rejected_without_creating_goal(self):
        self.login('lead')

        response = self.post(
            f"/project/{self.ids['visible_project']}/goal/new",
            {'title': 'Invalid Date Goal', 'start_date': 'not-a-date'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            self.assertIsNone(Goal.query.filter_by(title='Invalid Date Goal').first())

    def test_invalid_task_date_is_rejected_without_creating_task(self):
        self.login('lead')

        response = self.post(
            f"/project/{self.ids['visible_project']}/task/new",
            {'title': 'Invalid Date Task', 'due_date': '2026-99-99'},
        )

        self.assert_redirects_to_index(response)
        with self.app.app_context():
            self.assertIsNone(Task.query.filter_by(title='Invalid Date Task').first())

    def test_admin_can_create_project(self):
        self.login('admin')

        response = self.post('/project/new', {'name': 'Admin Created Project'})

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            project = Project.query.filter_by(name='Admin Created Project').one()
            self.assertEqual(project.status, 'active')

    def test_admin_can_edit_project(self):
        self.login('admin')

        response = self.post(
            f"/project/{self.ids['visible_project']}/edit",
            {'name': 'Admin Edited Project', 'status': 'completed'},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            project = db.session.get(Project, self.ids['visible_project'])
            self.assertEqual(project.name, 'Admin Edited Project')
            self.assertEqual(project.status, 'completed')

    def test_admin_can_delete_project(self):
        self.login('admin')
        with self.app.app_context():
            project = Project(name='Admin Delete Project', lead_id=self.ids['admin'])
            db.session.add(project)
            db.session.flush()
            project_id = project.id
            db.session.commit()

        response = self.post(f"/project/{project_id}/delete")

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Project, project_id))

    def test_admin_can_move_project(self):
        self.login('admin')
        with self.app.app_context():
            first = Project(name='Admin Move First', lead_id=self.ids['admin'], sort_order=-2)
            second = Project(name='Admin Move Second', lead_id=self.ids['admin'], sort_order=-1)
            db.session.add_all([first, second])
            db.session.flush()
            first_id = first.id
            second_id = second.id
            db.session.commit()

        response = self.post(f"/project/{second_id}/move/up")

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            first = db.session.get(Project, first_id)
            second = db.session.get(Project, second_id)
            self.assertEqual(second.sort_order, -2)
            self.assertEqual(first.sort_order, -1)

    def test_admin_can_restore_project(self):
        self.login('admin')
        with self.app.app_context():
            project = Project(
                name='Admin Restore Project',
                lead_id=self.ids['admin'],
                status='archived',
            )
            db.session.add(project)
            db.session.flush()
            project_id = project.id
            db.session.commit()

        response = self.post(f"/project/{project_id}/restore")

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            project = db.session.get(Project, project_id)
            self.assertEqual(project.status, 'active')

    def test_project_lead_can_create_goal(self):
        self.login('lead')

        response = self.post(
            f"/project/{self.ids['visible_project']}/goal/new",
            {'title': 'Lead Created Goal'},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            goal = Goal.query.filter_by(title='Lead Created Goal').one()
            self.assertEqual(goal.project_id, self.ids['visible_project'])

    def test_project_lead_can_edit_goal(self):
        self.login('lead')

        response = self.post(
            f"/goal/{self.ids['goal']}/edit",
            {'title': 'Lead Edited Goal', 'description': 'edited'},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            goal = db.session.get(Goal, self.ids['goal'])
            self.assertEqual(goal.title, 'Lead Edited Goal')

    def test_project_lead_can_delete_goal(self):
        self.login('lead')
        with self.app.app_context():
            project = db.session.get(Project, self.ids['visible_project'])
            goal = self.create_active_goal(project, title='Lead Delete Goal')
            goal_id = goal.id
            db.session.commit()

        response = self.post(f"/goal/{goal_id}/delete")

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Goal, goal_id))

    def test_project_lead_can_create_task(self):
        self.login('lead')

        response = self.post(
            f"/project/{self.ids['visible_project']}/task/new",
            {'title': 'Lead Created Task', 'status': 'pending'},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            task = Task.query.filter_by(title='Lead Created Task').one()
            self.assertEqual(task.project_id, self.ids['visible_project'])

    def test_project_lead_can_edit_task(self):
        self.login('lead')

        response = self.post(
            f"/task/{self.ids['assigned_task']}/edit",
            {'title': 'Lead Edited Task', 'status': 'pending'},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            task = db.session.get(Task, self.ids['assigned_task'])
            self.assertEqual(task.title, 'Lead Edited Task')
            self.assertEqual(task.status, 'pending')

    def test_project_lead_can_change_task_status(self):
        self.login('lead')

        response = self.post(
            f"/task/{self.ids['assigned_task']}/quick-status",
            {'status': 'completed'},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            task = db.session.get(Task, self.ids['assigned_task'])
            self.assertEqual(task.status, 'completed')

    def test_project_lead_can_update_task_solution(self):
        self.login('lead')

        response = self.post(
            f"/task/{self.ids['assigned_task']}/solution",
            {'solution': 'lead solution'},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            task = db.session.get(Task, self.ids['assigned_task'])
            self.assertEqual(task.solution, 'lead solution')

    def test_project_lead_can_log_task_progress(self):
        self.login('lead')

        response = self.post(
            f"/task/{self.ids['assigned_task']}/progress",
            {'entry_type': 'progress', 'content': 'lead progress'},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            log = ProgressLog.query.filter_by(task_id=self.ids['assigned_task']).one()
            self.assertEqual(log.content, 'lead progress')

    def test_project_lead_can_delete_task(self):
        self.login('lead')
        with self.app.app_context():
            project = db.session.get(Project, self.ids['visible_project'])
            task = self.create_active_task(project, title='Lead Delete Task')
            task_id = task.id
            db.session.commit()

        response = self.post(f"/task/{task_id}/delete")

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Task, task_id))

    def test_user_management_routes_remain_admin_only(self):
        self.login('ordinary')

        for path in ('/users', '/user/create', f"/user/{self.ids['owner']}/edit", f"/user/{self.ids['owner']}/projects"):
            with self.subTest(path=path):
                response = self.post(path) if path != '/users' else self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers['Location'], '/')


if __name__ == '__main__':
    unittest.main()
