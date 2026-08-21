import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from flask_login import login_user


WEBAPP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_DIR))

_db_file = tempfile.NamedTemporaryFile(
    prefix='pm-webapp-graph-', suffix='.db', delete=False
)
_db_file.close()
os.environ.setdefault('PM_DATABASE_PATH', _db_file.name)
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

import app as app_module
from models import db, Goal, Project, Task, User


class TaskTreeGraphTest(unittest.TestCase):
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

            admin = User(username='admin', password_hash='x', display_name='Admin', role='admin')
            owner = User(username='owner', password_hash='x', display_name='Owner', role='member')
            reviewer = User(username='reviewer', password_hash='x', display_name='Reviewer', role='member')
            outsider = User(username='outsider', password_hash='x', display_name='Outsider', role='member')
            db.session.add_all([admin, owner, reviewer, outsider])
            db.session.flush()

            project = Project(
                name='Graph Project',
                description='graph project body',
                lead_id=admin.id,
                status='active',
            )
            hidden_project = Project(
                name='Graph Hidden Secret',
                lead_id=outsider.id,
                status='active',
            )
            db.session.add_all([project, hidden_project])
            db.session.flush()

            active_goal = Goal(
                project_id=project.id,
                title='Active Lane',
                owner_id=owner.id,
                reviewer_id=reviewer.id,
                status='active',
                order=1,
            )
            review_goal = Goal(
                project_id=project.id,
                title='Review Lane',
                owner_id=owner.id,
                reviewer_id=reviewer.id,
                status='merge_requested',
                merge_requested_by_id=owner.id,
                order=2,
            )
            merged_goal = Goal(
                project_id=project.id,
                title='Merged Lane',
                owner_id=owner.id,
                reviewer_id=reviewer.id,
                status='merged',
                order=3,
            )
            hidden_goal = Goal(
                project_id=hidden_project.id,
                title='Graph Hidden Branch Secret',
                owner_id=outsider.id,
                status='active',
                order=1,
            )
            db.session.add_all([active_goal, review_goal, merged_goal, hidden_goal])
            db.session.flush()

            waiting_task = Task(
                project_id=project.id,
                goal_id=active_goal.id,
                title='Waiting Graph Task',
                assignee_id=owner.id,
                status='waiting',
                due_date=date.today() - timedelta(days=2),
                last_checkin_at=datetime.utcnow() - timedelta(days=5),
                waiting_reason='blocked on input',
            )
            unassigned_task = Task(
                project_id=project.id,
                goal_id=active_goal.id,
                title='Unassigned Graph Task',
                status='pending',
            )
            review_task = Task(
                project_id=project.id,
                goal_id=review_goal.id,
                title='Review Graph Task',
                assignee_id=owner.id,
                status='completed',
                completed_at=datetime.utcnow(),
            )
            merged_task = Task(
                project_id=project.id,
                goal_id=merged_goal.id,
                title='Merged Graph Task',
                assignee_id=owner.id,
                status='completed',
                completed_at=datetime.utcnow(),
            )
            hidden_task = Task(
                project_id=hidden_project.id,
                goal_id=hidden_goal.id,
                title='Graph Hidden Task Secret',
                assignee_id=outsider.id,
                status='in_progress',
            )
            db.session.add_all([waiting_task, unassigned_task, review_task, merged_task, hidden_task])
            db.session.commit()

            self.ids = {
                'admin': admin.id,
                'owner': owner.id,
                'reviewer': reviewer.id,
                'outsider': outsider.id,
                'project': project.id,
                'hidden_project': hidden_project.id,
                'active_goal': active_goal.id,
                'review_goal': review_goal.id,
                'merged_goal': merged_goal.id,
                'waiting_task': waiting_task.id,
                'unassigned_task': unassigned_task.id,
            }

    def login(self, user_key):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.ids[user_key])
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def test_task_risk_reasons_combines_overdue_waiting_stale_and_unassigned(self):
        with self.app.app_context():
            task = Task(
                project_id=self.ids['project'],
                title='synthetic risk',
                status='waiting',
                due_date=date.today() - timedelta(days=1),
                last_checkin_at=datetime.utcnow() - timedelta(days=4),
            )

            reasons = app_module.task_risk_reasons(task)

            self.assertEqual(set(reasons), {'overdue', 'unassigned', 'waiting', 'stale'})

    def test_task_branch_locked_returns_true_for_non_active_goal(self):
        with self.app.app_context():
            task = db.session.get(Task, self.ids['waiting_task'])
            task.goal.status = 'merge_requested'

            self.assertTrue(app_module.task_branch_locked(task))

    def test_graph_view_model_keeps_branch_lane_color_stable(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['admin'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )

            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            lane_by_label = {
                node['label']: (node['lane_color'], node['lane_color_dark'])
                for node in graph['nodes']
                if node['kind'] == 'goal'
            }

            self.assertEqual(
                lane_by_label['Active Lane'],
                app_module.branch_lane_colors(self.ids['active_goal']),
            )

    def test_graph_view_model_reuses_goal_lane_beyond_eight_branches(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['admin'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            for order in range(4, 13):
                db.session.add(Goal(
                    project_id=project.id,
                    title=f'Extra Lane {order}',
                    owner_id=self.ids['owner'],
                    reviewer_id=self.ids['reviewer'],
                    status='active',
                    order=order,
                ))
            db.session.commit()

            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )
            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            goal_nodes = [node for node in graph['nodes'] if node['kind'] == 'goal']

            self.assertEqual(len(goal_nodes), 12)
            self.assertEqual(len({node['x'] for node in goal_nodes}), 1)
            self.assertEqual({node['x'] for node in goal_nodes}, {174})
            self.assertEqual(len({node['lane_color'] for node in goal_nodes}), 12)
            self.assertEqual(graph['width'], 980)

    def test_tasks_render_as_child_branches_of_their_goal(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['admin'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )

            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            goal_node = next(
                node for node in graph['nodes']
                if node['id'] == f"goal-{self.ids['active_goal']}"
            )
            task_node = next(
                node for node in graph['nodes']
                if node['id'] == f"task-{self.ids['waiting_task']}"
            )
            task_forks = [
                edge for edge in graph['edges']
                if edge['kind'] == 'task-fork'
                and edge['source_node_id'] == f"goal-task-origin-{self.ids['active_goal']}"
            ]
            active_task_nodes = [
                node for node in graph['nodes']
                if node.get('goal') and node['kind'] == 'task'
                and node['goal'].id == self.ids['active_goal']
            ]

            self.assertEqual(goal_node['topology_level'], 1)
            self.assertEqual(task_node['topology_level'], 2)
            self.assertGreater(task_node['x'], goal_node['x'])
            self.assertEqual(task_node['parent_label'], 'Active Lane')
            self.assertEqual(len(task_forks), 2)
            self.assertEqual(len({edge['source_node_id'] for edge in task_forks}), 1)
            self.assertEqual(len({node['x'] for node in active_task_nodes}), 2)
            self.assertEqual(len({node['y'] for node in active_task_nodes}), 1)
            self.assertTrue(all(' C ' in edge['path'] for edge in task_forks))

    def test_completed_task_curves_back_to_its_goal_lane(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['admin'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )

            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            completed_task = next(
                task for task in project.tasks if task.title == 'Merged Graph Task'
            )
            goal_node = next(
                node for node in graph['nodes']
                if node['id'] == f"goal-{self.ids['merged_goal']}"
            )
            merge_node = next(
                node for node in graph['nodes']
                if node['id'] == f"goal-task-join-{self.ids['merged_goal']}"
            )
            merge_edge = next(
                edge for edge in graph['edges']
                if edge['kind'] == 'task-merge'
                and edge['source_node_id'] == f'task-{completed_task.id}'
            )

            self.assertEqual(merge_node['merge_scope'], 'task_group')
            self.assertEqual(merge_node['x'], goal_node['x'])
            self.assertEqual(merge_node['status'], 'completed')
            self.assertEqual(merge_edge['target_node_id'], merge_node['id'])
            self.assertEqual(merge_edge.get('lane_style'), goal_node['lane_style'])
            self.assertIn(' C ', merge_edge['path'])

    def test_sibling_tasks_fan_out_in_parallel_and_share_one_join(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['admin'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            goal = db.session.get(Goal, self.ids['active_goal'])
            for index in range(3):
                db.session.add(Task(
                    project_id=project.id,
                    goal_id=goal.id,
                    title=f'Parallel Task {index + 1}',
                    assignee_id=self.ids['owner'],
                    status='completed' if index < 2 else 'in_progress',
                    order=10 + index,
                ))
            db.session.commit()

            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )
            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            origin_id = f"goal-task-origin-{goal.id}"
            join_id = f"goal-task-join-{goal.id}"
            task_forks = [
                edge for edge in graph['edges']
                if edge['kind'] == 'task-fork' and edge['source_node_id'] == origin_id
            ]
            completed_merges = [
                edge for edge in graph['edges']
                if edge['kind'] == 'task-merge' and edge['target_node_id'] == join_id
            ]
            sibling_nodes = [
                node for node in graph['nodes']
                if node['kind'] == 'task'
                and node.get('goal') and node['goal'].id == goal.id
            ]
            join_node = next(node for node in graph['nodes'] if node['id'] == join_id)

            self.assertEqual(len(task_forks), 5)
            self.assertEqual({edge['source_node_id'] for edge in task_forks}, {origin_id})
            self.assertGreaterEqual(len({node['x'] for node in sibling_nodes}), 2)
            self.assertGreaterEqual(len({node['y'] for node in sibling_nodes}), 3)
            self.assertEqual(len(completed_merges), 2)
            self.assertEqual({edge['target_node_id'] for edge in completed_merges}, {join_id})
            self.assertEqual(join_node['status'], 'in_progress')
            self.assertIn('2/5 完成', join_node['label'])

    def test_merged_goal_curves_back_to_project_trunk(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['admin'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )

            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            project_node = next(node for node in graph['nodes'] if node['kind'] == 'project')
            endpoint = next(
                node for node in graph['nodes']
                if node['id'] == f"endpoint-{self.ids['merged_goal']}"
            )
            merge_edge = next(
                edge for edge in graph['edges']
                if edge['kind'] == 'merge' and 'graph-edge-merged' in edge['lane_class']
            )

            self.assertEqual(endpoint['merge_scope'], 'goal')
            self.assertEqual(endpoint['x'], project_node['x'])
            self.assertEqual(endpoint['label'], '已合并回主线')
            self.assertEqual(endpoint['lane_class'], 'graph-lane-main')
            self.assertIn(' C ', merge_edge['path'])

    def test_main_lane_contains_fork_commits_merge_commit_and_head(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['admin'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )

            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            project_node = next(node for node in graph['nodes'] if node['kind'] == 'project')
            main_commits = [
                node for node in graph['nodes'] if node['kind'] == 'main_commit'
            ]
            main_head = next(node for node in graph['nodes'] if node['kind'] == 'main_head')
            merge_commit = next(
                node for node in graph['nodes']
                if node['id'] == f"endpoint-{self.ids['merged_goal']}"
            )

            self.assertEqual(len(main_commits), 3)
            self.assertTrue(all(node['x'] == project_node['x'] for node in main_commits))
            self.assertTrue(all(node['label'].startswith('main · 创建分支') for node in main_commits))
            self.assertEqual(merge_commit['x'], project_node['x'])
            self.assertIn('main 合并提交', merge_commit['state_label'])
            self.assertEqual(main_head['x'], project_node['x'])
            self.assertEqual(main_head['label'], 'main / HEAD')
            self.assertGreater(main_head['y'], max(node['y'] for node in main_commits))

    def test_graph_viewport_only_owns_horizontal_scrolling(self):
        css = (WEBAPP_DIR / 'static' / 'style.css').read_text(encoding='utf-8')
        viewport_rule = css.split('.git-graph-viewport {', 1)[1].split('}', 1)[0]

        self.assertIn('overflow-x: auto', viewport_rule)
        self.assertIn('overflow-y: hidden', viewport_rule)
        self.assertIn('overscroll-behavior-y: auto', viewport_rule)
        self.assertNotIn('overscroll-behavior: contain', viewport_rule)

    def test_sidebar_navigation_stays_in_one_column_without_horizontal_scroll(self):
        css = (WEBAPP_DIR / 'static' / 'style.css').read_text(encoding='utf-8')
        sidebar_rule = css.split(
            '.sidebar-nav.nav,\n.sidebar-nav {', 1
        )[1].split('}', 1)[0]
        section_rule = css.split('.nav-section {', 1)[1].split('}', 1)[0]

        self.assertIn('flex-direction: column', sidebar_rule)
        self.assertIn('flex-wrap: nowrap', sidebar_rule)
        self.assertIn('overflow-x: hidden', sidebar_rule)
        self.assertIn('width: 100%', section_rule)
        self.assertIn('flex: 0 0 auto', section_rule)

    def test_fork_and_merge_transitions_are_curved_while_parent_lanes_stay_straight(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['admin'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )

            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            transition_edges = [
                edge for edge in graph['edges']
                if edge['kind'] in ('fork', 'task-fork', 'task-merge', 'merge')
            ]
            lane_edges = [edge for edge in graph['edges'] if edge['kind'] == 'lane']

            self.assertTrue(transition_edges)
            self.assertTrue(all(' C ' in edge['path'] for edge in transition_edges))
            self.assertTrue(lane_edges)
            self.assertTrue(all(' L ' in edge['path'] and ' C ' not in edge['path'] for edge in lane_edges))

    def test_each_fork_starts_at_the_exact_parent_commit_coordinate(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['admin'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )

            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            nodes_by_id = {node['id']: node for node in graph['nodes']}
            fork_edges = [
                edge for edge in graph['edges']
                if edge['kind'] in ('fork', 'task-fork')
            ]

            self.assertTrue(fork_edges)
            for edge in fork_edges:
                with self.subTest(source=edge['source_node_id']):
                    source = nodes_by_id[edge['source_node_id']]
                    path_parts = edge['path'].split()
                    self.assertEqual(float(path_parts[1]), source['x'])
                    self.assertEqual(float(path_parts[2]), source['y'])
                    self.assertIn(source['kind'], ('main_commit', 'branch_commit'))

    def test_graph_view_model_marks_personal_relevance_on_owned_branch(self):
        with self.app.test_request_context('/'):
            viewer = db.session.get(User, self.ids['owner'])
            login_user(viewer)
            project = db.session.get(Project, self.ids['project'])
            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )

            graph = app_module.build_graph_view_model(tree_projects, viewer)[0]['graph']
            active_node = next(
                node for node in graph['nodes']
                if node['kind'] == 'goal' and node['label'] == 'Active Lane'
            )

            self.assertTrue(active_node['is_mine'])
            self.assertIn('分支负责人', active_node['relevance'])

    def test_build_tree_context_uses_viewer_without_request_user(self):
        with self.app.app_context():
            viewer = db.session.get(User, self.ids['admin'])
            project = db.session.get(Project, self.ids['project'])

            tree_projects, _summary = app_module.build_tree_context(
                [project], {'status': 'all'}, viewer=viewer
            )

            self.assertEqual(len(tree_projects), 1)

    def test_task_tree_renders_svg_accessibility_semantics(self):
        self.login('admin')

        response = self.client.get('/task-tree?status=all')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('<svg class="git-graph"', html)
        self.assertIn('role="img"', html)
        self.assertIn('aria-labelledby="graph-title-', html)
        self.assertIn('role="button"', html)
        self.assertIn('aria-controls="graph-inspector-', html)

    def test_task_tree_renders_non_color_status_cues(self):
        self.login('admin')

        response = self.client.get('/task-tree?status=all')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        for text in ('待验收', '已闭环', '逾期', '等待', '停滞', '未分配'):
            self.assertIn(text, html)

    def test_task_tree_overview_request_by_member_uses_mine_scope(self):
        self.login('owner')

        response = self.client.get('/task-tree?scope=overview&status=all')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('value="mine"', html)
        self.assertNotIn('总体图谱</a>', html)
        self.assertNotIn('Graph Hidden Secret', html)

    def test_task_tree_status_filter_renders_only_merged_branches(self):
        self.login('admin')

        response = self.client.get('/task-tree?status=merged')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Merged Lane', html)
        self.assertNotIn('Active Lane', html)
        self.assertNotIn('Review Lane', html)

    def test_task_tree_hidden_project_filter_returns_404_without_name_leak(self):
        self.login('owner')

        response = self.client.get(f"/task-tree?project_id={self.ids['hidden_project']}")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Graph Hidden Secret', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
