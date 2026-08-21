"""项目协作平台 - Flask 主程序"""
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, abort, session
)
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from models import (db, User, Project, Goal, Task,
                    ActionLog, ProgressLog, UserProjectPriority)
from schema_migrate import migrate_sqlite

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 加载 webapp/.env
_ENV_PATH = os.path.join(BASE_DIR, '.env')
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from ai import AIUnavailable

DB_PATH = os.environ.get('PM_DATABASE_PATH', os.path.join(BASE_DIR, 'database.db'))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get(
    'SECRET_KEY', 'pm-platform-secret-key-change-in-production'
)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录'


# ============================================================
# CSRF 防护
# ============================================================

@app.before_request
def csrf_protect():
    if request.method == 'POST':
        token = session.get('_csrf_token')
        submitted = (request.form.get('csrf_token')
                     or request.headers.get('X-CSRF-Token'))
        if not token or not submitted or not secrets.compare_digest(token, submitted):
            flash('页面已过期，请刷新后重试', 'error')
            return redirect(request.referrer or url_for('index'))


@app.context_processor
def inject_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return {'csrf_token': session['_csrf_token']}


@app.context_processor
def inject_theme_preference():
    return {'theme_pref': session.get('theme', 'light')}


# ============================================================
# 个人项目重要度排序
# ============================================================

def _priority_row(user, project_id):
    return UserProjectPriority.query.filter_by(
        user_id=user.id, project_id=project_id
    ).first()


def _priority_map(user, project_ids):
    rows = UserProjectPriority.query.filter(
        UserProjectPriority.user_id == user.id,
        UserProjectPriority.project_id.in_(project_ids)
    ).all()
    return {r.project_id: r.priority for r in rows}


def order_projects_for_user(user, projects, pinned_ids):
    if not projects:
        return projects
    pids = [p.id for p in projects]
    p_map = _priority_map(user, pids)
    eff = {}
    for p in projects:
        eff[p.id] = p_map[p.id] if p.id in p_map else p.sort_order
    pinned = [p for p in projects if p.id in pinned_ids]
    others = [p for p in projects if p.id not in pinned_ids]
    pinned.sort(key=lambda p: (eff.get(p.id, 0), p.created_at or datetime.min))
    others.sort(key=lambda p: (eff.get(p.id, 0), p.created_at or datetime.min))
    return pinned + others


@app.route('/project/<int:project_id>/priority/<direction>', methods=['POST'])
@login_required
def project_priority_move(project_id, direction):
    project = db.session.get(Project, project_id)
    if project is None or not current_user.can_manage_project(project):
        return deny_mutation()
    projects = [
        item for item in visible_projects_for(current_user)
        if item.status != 'archived' and current_user.can_manage_project(item)
    ]
    pinned_ids = {p.id for p in current_user.pinned_projects}
    ordered = order_projects_for_user(current_user, projects, pinned_ids)

    idx = next((i for i, p in enumerate(ordered) if p.id == project_id), -1)
    if idx == -1:
        return redirect(url_for('index'))
    if direction == 'up' and idx > 0:
        ordered[idx], ordered[idx - 1] = ordered[idx - 1], ordered[idx]
    elif direction == 'down' and idx < len(ordered) - 1:
        ordered[idx], ordered[idx + 1] = ordered[idx + 1], ordered[idx]
    else:
        return redirect(url_for('index'))

    for rank, p in enumerate(ordered):
        row = _priority_row(current_user, p.id)
        if row is None:
            row = UserProjectPriority(user_id=current_user.id, project_id=p.id)
            db.session.add(row)
        row.priority = rank
    log_action('project', project_id, '调整重要度',
               f'{current_user.display_name} 调整「{project.name}」排序')
    db.session.commit()
    flash(f'已调整「{project.name}」的重要度', 'success')
    return redirect(url_for('index'))


# ============================================================
# 时间显示与时区辅助
# ============================================================

LOCAL_TZ = timezone(timedelta(hours=8))


@app.template_filter('localdt')
def localdt(value, fmt='%Y-%m-%d %H:%M'):
    if not value:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TZ).strftime(fmt)


@app.template_filter('localdt_short')
def localdt_short(value, fmt='%m-%d %H:%M'):
    return localdt(value, fmt)


# ============================================================
# 操作日志
# ============================================================

def log_action(entity_type, entity_id, action, detail='', actor_id=None):
    db.session.add(ActionLog(
        entity_type=entity_type, entity_id=entity_id,
        action=action, detail=detail,
        actor_id=actor_id if actor_id is not None else current_user.id
    ))


def timeline_logs(entity_type, entity_id, limit=50, viewer=None):
    """返回某实体及其子孙实体的操作日志，时间倒序。"""
    from sqlalchemy import or_, and_
    viewer = viewer or current_user
    pairs = [(entity_type, entity_id)]
    if entity_type == 'project':
        project = Project.query.get(entity_id)
        for g in project.goals if project else []:
            if not viewer.can_view_goal(g):
                continue
            pairs.append(('goal', g.id))
        for t in project.tasks if project else []:
            if not viewer.can_view_task(t):
                continue
            pairs.append(('task', t.id))
    elif entity_type == 'goal':
        goal = Goal.query.get(entity_id)
        for t in goal.tasks if goal else []:
            if not viewer.can_view_task(t):
                continue
            pairs.append(('task', t.id))
    elif entity_type == 'task':
        pass  # 任务只有自身
    conditions = [and_(ActionLog.entity_type == et, ActionLog.entity_id == eid)
                  for et, eid in pairs]
    if not conditions:
        return []
    return ActionLog.query.filter(or_(*conditions)).order_by(
        ActionLog.created_at.desc()
    ).limit(limit).all()


ENTITY_LABELS = {'project': '项目', 'goal': '目标', 'task': '任务', 'user': '用户'}


class InvalidDateField(ValueError):
    def __init__(self, field_name):
        super().__init__(field_name)
        self.field_name = field_name


def parse_date_field(name):
    raw = request.form.get(name, '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise InvalidDateField(name) from exc


def validate_task_status(raw):
    status = raw or 'pending'
    if status not in Task.VALID_STATUSES:
        status = 'pending'
    return status


def validate_result_type(raw):
    result_type = (raw or '').strip() or None
    if result_type and result_type not in Goal.VALID_RESULT_TYPES:
        return None
    return result_type


def update_user_members(obj, attr, member_ids):
    setattr(obj, attr, [])
    collection = getattr(obj, attr)
    for mid in member_ids:
        member = User.query.get(mid)
        if member:
            collection.append(member)


def apply_task_status(task, new_status):
    old_status = task.status
    task.status = validate_task_status(new_status)
    if task.status == 'completed':
        if not task.completed_at:
            task.completed_at = datetime.utcnow()
    else:
        task.completed_at = None
    if task.status != 'waiting':
        task.waiting_reason = None
        task.waiting_until = None
    return old_status != task.status


def safe_redirect(default_endpoint='task_tree', **default_values):
    referrer = request.referrer
    if referrer:
        referrer_url = urlparse(referrer)
        host_url = urlparse(request.host_url)
        if referrer_url.scheme in ('http', 'https') and referrer_url.netloc == host_url.netloc:
            return redirect(referrer)
    return redirect(url_for(default_endpoint, **default_values))


@app.errorhandler(InvalidDateField)
def handle_invalid_date_field(error):
    db.session.rollback()
    field_labels = {
        'start_date': '开始日期',
        'due_date': '截止日期',
        'waiting_until': '等待截止日期',
    }
    label = field_labels.get(error.field_name, '日期')
    flash(f'{label}格式无效，请使用 YYYY-MM-DD', 'error')
    return safe_redirect('index')


def deny_mutation():
    flash('没有权限执行此操作', 'error')
    return redirect(url_for('index'))


def task_status_sort_key(task):
    order = {'in_progress': 0, 'waiting': 1, 'pending': 2, 'completed': 3}
    return (order.get(task.status, 9), task.due_date or date.max, task.created_at or datetime.min)


def task_risk_reasons(task, today=None):
    today = today or date.today()
    reasons = []
    if task.is_open() and task.due_date and task.due_date < today:
        reasons.append('overdue')
    if task.is_open() and not task.assignee_id:
        reasons.append('unassigned')
    if task.is_open() and task.status == 'waiting':
        reasons.append('waiting')
    if task.is_stale(days=3):
        reasons.append('stale')
    return reasons


def task_branch_locked(task):
    return bool(task.goal and task.goal.status != 'active')


def visible_projects_for(user, status=None):
    query = Project.query
    if status:
        query = query.filter_by(status=status)
    projects = query.order_by(
        Project.sort_order.asc(), Project.created_at.desc()
    ).all()
    return [project for project in projects if user.can_view_project(project)]


def visible_goals_for(user, project):
    return [
        goal for goal in sorted(
            project.goals,
            key=lambda item: (item.order or 0, item.created_at or datetime.min),
        )
        if user.can_view_goal(goal)
    ]


def visible_tasks_for(user, project=None):
    tasks = project.tasks if project is not None else Task.query.all()
    return [task for task in tasks if user.can_view_task(task)]


def resolve_view_scope(user, requested_scope=None):
    default_scope = 'overview' if user.is_admin() else 'mine'
    requested_scope = requested_scope if requested_scope in ('mine', 'overview') else default_scope
    active_projects = visible_projects_for(user, 'active')
    can_overview = user.is_admin() or any(
        user.can_manage_project(project) for project in active_projects
    )
    if requested_scope == 'overview' and not can_overview:
        requested_scope = 'mine'
    return requested_scope, can_overview


def task_stats_for(tasks):
    tasks = list(tasks)
    open_tasks = [task for task in tasks if task.status != 'completed']
    completed = len(tasks) - len(open_tasks)
    return {
        'total': len(tasks),
        'open': len(open_tasks),
        'completed': completed,
        'overdue': sum(1 for task in open_tasks if task.is_overdue()),
        'unassigned': sum(1 for task in open_tasks if not task.assignee_id),
        'progress_pct': int(completed / len(tasks) * 100) if tasks else 0,
    }


def goal_relevance(user, goal):
    labels = []
    if goal.project.lead_id == user.id:
        labels.append('项目负责人')
    if goal.project in user.member_projects:
        labels.append('项目人员')
    if goal.owner_id == user.id:
        labels.append('分支负责人')
    if goal.reviewer_id == user.id:
        labels.append('验收人')
    if goal.members.filter_by(id=user.id).count() > 0:
        labels.append('分支成员')
    return labels


def task_relevance(user, task):
    labels = []
    if task.assignee_id == user.id:
        labels.append('任务负责人')
    if task.submitter_id == user.id:
        labels.append('提交人')
    if task.reviewer_id == user.id:
        labels.append('任务验收人')
    if task.members.filter_by(id=user.id).count() > 0:
        labels.append('任务成员')
    if task.goal:
        for label in goal_relevance(user, task.goal):
            if label not in labels:
                labels.append(label)
    elif task.project.lead_id == user.id:
        labels.append('项目负责人')
    return labels


def build_tree_context(projects, filters=None, viewer=None):
    filters = filters or {}
    viewer = viewer or current_user
    today = date.today()
    q = (filters.get('q') or '').strip().lower()
    tree_projects = []
    summary = {
        'projects': 0,
        'goals': 0,
        'tasks': 0,
        'risk_count': 0,
        'merge_requested': 0,
        'merged': 0,
    }

    for project in projects:
        branches = []
        project_tasks = sorted(
            (task for task in project.tasks if viewer.can_view_task(task)),
            key=task_status_sort_key,
        )
        filtered_project_tasks = []
        for task in project_tasks:
            haystack = ' '.join([
                task.title or '',
                task.description or '',
                task.assignee.display_name if task.assignee else '',
                task.goal.title if task.goal else '',
            ]).lower()
            if q and q not in haystack:
                continue
            filtered_project_tasks.append(task)

        tasks_by_goal = {}
        for task in filtered_project_tasks:
            tasks_by_goal.setdefault(task.goal_id, []).append(task)

        for goal in visible_goals_for(viewer, project):
            if q:
                goal_text = ' '.join([goal.title or '', goal.description or '', goal.deliverable or '']).lower()
                if q not in goal_text and goal.id not in tasks_by_goal:
                    continue
            branch_tasks = sorted(tasks_by_goal.get(goal.id, []), key=task_status_sort_key)
            risk_count = sum(1 for task in branch_tasks if task_risk_reasons(task, today))
            if goal.is_overdue():
                risk_count += 1
            relevance = goal_relevance(viewer, goal)
            branches.append({
                'goal': goal,
                'tasks': branch_tasks,
                'risk_count': risk_count,
                'relevance': relevance,
                'is_mine': bool(relevance),
                'can_request_merge': goal.can_request_merge(viewer),
                'can_review': goal.can_review_merge(viewer),
                'can_reopen': (
                    goal.status == 'merged'
                    and (
                        viewer.can_manage_project(goal.project)
                        or goal.reviewer_id == viewer.id
                    )
                ),
            })
            summary['goals'] += 1
            summary['tasks'] += len(branch_tasks)
            summary['risk_count'] += risk_count
            if goal.status == 'merge_requested':
                summary['merge_requested'] += 1
            if goal.status == 'merged':
                summary['merged'] += 1

        ungrouped_tasks = sorted(tasks_by_goal.get(None, []), key=task_status_sort_key)
        summary['tasks'] += len(ungrouped_tasks)
        summary['risk_count'] += sum(1 for task in ungrouped_tasks if task_risk_reasons(task, today))

        if branches or ungrouped_tasks or not q:
            tree_projects.append({
                'project': project,
                'branches': branches,
                'ungrouped_tasks': ungrouped_tasks,
                'relevance': (
                    ['项目负责人'] if project.lead_id == viewer.id else []
                ) + (
                    ['项目人员'] if project in viewer.member_projects else []
                ),
            })

    summary['projects'] = len(tree_projects)
    return tree_projects, summary


def branch_lane_colors(goal_id):
    """Return deterministic light/dark lane colors without a fixed palette cap."""
    hue = (goal_id * 137 + 17) % 360
    return (
        f'hsl({hue} 68% 40%)',
        f'hsl({hue} 78% 68%)',
    )


def build_graph_view_model(tree_projects, viewer):
    """把可见项目转换为项目→目标→任务的 Git 式闭环拓扑。"""
    graph_projects = []
    for item in tree_projects:
        project = item['project']
        trunk_x = 58
        goal_lane_x = 174
        y = 44
        max_x = trunk_x
        nodes = [{
            'id': f'project-{project.id}',
            'inspector_id': f'project-{project.id}',
            'kind': 'project',
            'x': trunk_x,
            'y': y,
            'label': project.name,
            'status': project.status,
            'state_label': '项目主线',
            'is_mine': bool(item.get('relevance')),
            'relevance': item.get('relevance', []),
            'risk_reasons': [],
            'lane_class': 'graph-lane-main',
            'project': project,
            'topology_level': 0,
        }]
        edges = []

        ungrouped_tasks = item['ungrouped_tasks']
        if ungrouped_tasks:
            task_origin_y = y + 56
            task_join_id = f'main-task-join-{project.id}'
            group_relevance = item.get('relevance', [])
            nodes.append({
                'id': f'main-task-origin-{project.id}',
                'inspector_id': f'project-{project.id}',
                'kind': 'main_commit',
                'x': trunk_x,
                'y': task_origin_y,
                'label': 'main · 并行任务需求点',
                'status': 'active',
                'state_label': 'main 并行任务分叉提交',
                'risk_reasons': [],
                'is_mine': bool(group_relevance),
                'relevance': group_relevance,
                'project': project,
                'lane_class': 'graph-lane-main',
                'topology_level': 0,
                'compact': True,
            })
            task_positions = []
            for task_index, task in enumerate(ungrouped_tasks):
                column = task_index % 2
                row = task_index // 2
                task_x = trunk_x + 148 + column * 260
                task_y = task_origin_y + 62 + row * 92
                task_positions.append((task, task_x, task_y))
                risk_reasons = task_risk_reasons(task)
                relevance = task_relevance(viewer, task)
                edges.append({
                    'kind': 'task-fork',
                    'lane_class': 'graph-lane-main graph-edge-subbranch',
                    'source_node_id': f'main-task-origin-{project.id}',
                    'target_node_id': f'task-{task.id}',
                    'path': (
                        f'M {trunk_x} {task_origin_y} '
                        f'C {trunk_x + 52} {task_origin_y}, '
                        f'{task_x - 64} {task_y - 24}, {task_x} {task_y}'
                    ),
                })
                nodes.append({
                    'id': f'task-{task.id}',
                    'inspector_id': f'task-{task.id}',
                    'kind': 'task',
                    'x': task_x,
                    'y': task_y,
                    'label': task.title,
                    'status': task.status,
                    'state_label': f'主线并行任务 · {task.status_label()}',
                    'risk_reasons': risk_reasons,
                    'is_mine': bool(relevance),
                    'relevance': relevance,
                    'task': task,
                    'lane_class': 'graph-lane-main',
                    'topology_level': 1,
                    'parent_label': project.name,
                })
                max_x = max(max_x, task_x)

            max_task_y = max(position[2] for position in task_positions)
            task_join_y = max_task_y + 58
            completed_tasks = [
                position for position in task_positions
                if position[0].status == 'completed'
            ]
            for task, task_x, task_y in completed_tasks:
                edges.append({
                    'kind': 'task-merge',
                    'lane_class': 'graph-lane-main graph-edge-subbranch graph-edge-merged',
                    'source_node_id': f'task-{task.id}',
                    'target_node_id': task_join_id,
                    'path': (
                        f'M {task_x} {task_y} '
                        f'C {task_x + 48} {task_y + 18}, '
                        f'{trunk_x + 62} {task_join_y}, {trunk_x} {task_join_y}'
                    ),
                })
            nodes.append({
                'id': task_join_id,
                'inspector_id': f'project-{project.id}',
                'kind': 'task_join',
                'merge_scope': 'task_group',
                'x': trunk_x,
                'y': task_join_y,
                'label': f'主线任务汇合 · {len(completed_tasks)}/{len(ungrouped_tasks)} 完成',
                'status': (
                    'completed'
                    if len(completed_tasks) == len(ungrouped_tasks)
                    else 'in_progress'
                ),
                'state_label': '并行任务共享汇合点',
                'risk_reasons': [],
                'is_mine': bool(group_relevance),
                'relevance': group_relevance,
                'project': project,
                'lane_class': 'graph-lane-main',
                'topology_level': 0,
            })
            y = task_join_y + 28

        for branch in item['branches']:
            goal = branch['goal']
            lane_class = 'graph-lane-branch'
            lane_color, lane_color_dark = branch_lane_colors(goal.id)
            lane_style = (
                f'--graph-lane-color: {lane_color}; '
                f'--graph-lane-color-dark: {lane_color_dark}'
            )
            lane_x = goal_lane_x
            y += 82
            fork_y = y
            main_fork_y = fork_y - 30
            branch_relevance = branch.get('relevance', [])
            nodes.append({
                'id': f'main-fork-{goal.id}',
                'inspector_id': f'goal-{goal.id}',
                'kind': 'main_commit',
                'x': trunk_x,
                'y': main_fork_y,
                'label': f'main · 创建分支 {goal.title}',
                'status': 'active',
                'state_label': 'main 分叉提交',
                'risk_reasons': [],
                'is_mine': bool(branch_relevance),
                'relevance': branch_relevance,
                'goal': goal,
                'branch': branch,
                'lane_class': 'graph-lane-main',
                'topology_level': 0,
            })
            edges.append({
                'kind': 'fork',
                'lane_class': lane_class,
                'lane_style': lane_style,
                'source_node_id': f'main-fork-{goal.id}',
                'target_node_id': f'goal-{goal.id}',
                'path': (
                    f'M {trunk_x} {fork_y - 30} '
                    f'C {trunk_x + 46} {fork_y - 30}, {lane_x - 56} {fork_y - 18}, '
                    f'{lane_x} {fork_y}'
                ),
            })
            nodes.append({
                'id': f'goal-{goal.id}',
                'inspector_id': f'goal-{goal.id}',
                'kind': 'goal',
                'x': lane_x,
                'y': fork_y,
                'label': goal.title,
                'status': goal.status,
                'state_label': goal.status_label(),
                'risk_reasons': ['overdue'] if goal.is_overdue() else [],
                'is_mine': bool(branch_relevance),
                'relevance': branch_relevance,
                'goal': goal,
                'branch': branch,
                'lane_class': lane_class,
                'lane_style': lane_style,
                'lane_color': lane_color,
                'lane_color_dark': lane_color_dark,
                'topology_level': 1,
                'parent_label': project.name,
            })
            max_x = max(max_x, lane_x)

            branch_tasks = branch['tasks']
            previous_lane_y = fork_y
            if branch_tasks:
                task_origin_y = fork_y + 56
                task_origin_id = f'goal-task-origin-{goal.id}'
                task_join_id = f'goal-task-join-{goal.id}'
                edges.append({
                    'kind': 'lane',
                    'lane_class': lane_class,
                    'lane_style': lane_style,
                    'path': f'M {lane_x} {fork_y} L {lane_x} {task_origin_y}',
                })
                nodes.append({
                    'id': task_origin_id,
                    'inspector_id': f'goal-{goal.id}',
                    'kind': 'branch_commit',
                    'x': lane_x,
                    'y': task_origin_y,
                    'label': f'并行任务需求 · {len(branch_tasks)} 条',
                    'status': goal.status,
                    'state_label': '同一目标需求的并行分叉点',
                    'risk_reasons': [],
                    'is_mine': bool(branch_relevance),
                    'relevance': branch_relevance,
                    'goal': goal,
                    'lane_class': lane_class,
                    'lane_style': lane_style,
                    'topology_level': 1,
                })
                task_positions = []
                for task_index, task in enumerate(branch_tasks):
                    column = task_index % 2
                    row = task_index // 2
                    task_x = lane_x + 160 + column * 260
                    task_y = task_origin_y + 70 + row * 94
                    task_positions.append((task, task_x, task_y))
                    risk_reasons = task_risk_reasons(task)
                    relevance = task_relevance(viewer, task)
                    edges.append({
                        'kind': 'task-fork',
                        'lane_class': f'{lane_class} graph-edge-subbranch',
                        'lane_style': lane_style,
                        'source_node_id': task_origin_id,
                        'target_node_id': f'task-{task.id}',
                        'path': (
                            f'M {lane_x} {task_origin_y} '
                            f'C {lane_x + 58} {task_origin_y}, '
                            f'{task_x - 72} {task_y - 26}, {task_x} {task_y}'
                        ),
                    })
                    nodes.append({
                        'id': f'task-{task.id}',
                        'inspector_id': f'task-{task.id}',
                        'kind': 'task',
                        'x': task_x,
                        'y': task_y,
                        'label': task.title,
                        'status': task.status,
                        'state_label': f'并行任务子分支 · {task.status_label()}',
                        'risk_reasons': risk_reasons,
                        'is_mine': bool(relevance),
                        'relevance': relevance,
                        'task': task,
                        'goal': goal,
                        'lane_class': lane_class,
                        'lane_style': lane_style,
                        'topology_level': 2,
                        'parent_label': goal.title,
                    })
                    max_x = max(max_x, task_x)

                max_task_y = max(position[2] for position in task_positions)
                task_join_y = max_task_y + 62
                completed_tasks = [
                    position for position in task_positions
                    if position[0].status == 'completed'
                ]
                for task, task_x, task_y in completed_tasks:
                    edges.append({
                        'kind': 'task-merge',
                        'lane_class': f'{lane_class} graph-edge-subbranch graph-edge-merged',
                        'lane_style': lane_style,
                        'source_node_id': f'task-{task.id}',
                        'target_node_id': task_join_id,
                        'path': (
                            f'M {task_x} {task_y} '
                            f'C {task_x + 52} {task_y + 18}, '
                            f'{lane_x + 68} {task_join_y}, {lane_x} {task_join_y}'
                        ),
                    })
                edges.append({
                    'kind': 'lane',
                    'lane_class': lane_class,
                    'lane_style': lane_style,
                    'path': f'M {lane_x} {task_origin_y} L {lane_x} {task_join_y}',
                })
                nodes.append({
                    'id': task_join_id,
                    'inspector_id': f'goal-{goal.id}',
                    'kind': 'task_join',
                    'merge_scope': 'task_group',
                    'x': lane_x,
                    'y': task_join_y,
                    'label': f'并行任务汇合 · {len(completed_tasks)}/{len(branch_tasks)} 完成',
                    'status': (
                        'completed'
                        if len(completed_tasks) == len(branch_tasks)
                        else 'in_progress'
                    ),
                    'state_label': '目标任务共享汇合点',
                    'risk_reasons': [],
                    'is_mine': bool(branch_relevance),
                    'relevance': branch_relevance,
                    'goal': goal,
                    'branch': branch,
                    'lane_class': lane_class,
                    'lane_style': lane_style,
                    'topology_level': 1,
                })
                previous_lane_y = task_join_y

            y = previous_lane_y + 58
            edges.append({
                'kind': 'lane',
                'lane_class': lane_class,
                'lane_style': lane_style,
                'path': f'M {lane_x} {previous_lane_y} L {lane_x} {y}',
            })
            if goal.status == 'merged':
                nodes.append({
                    'id': f'goal-close-{goal.id}',
                    'inspector_id': f'goal-{goal.id}',
                    'kind': 'branch_commit',
                    'x': lane_x,
                    'y': y,
                    'label': f'闭环提交 · {goal.title}',
                    'status': goal.status,
                    'state_label': '目标闭环提交点',
                    'risk_reasons': [],
                    'is_mine': bool(branch_relevance),
                    'relevance': branch_relevance,
                    'goal': goal,
                    'branch': branch,
                    'lane_class': lane_class,
                    'lane_style': lane_style,
                    'topology_level': 1,
                    'compact': True,
                })
                edges.append({
                    'kind': 'merge',
                    'lane_class': f'{lane_class} graph-edge-merged',
                    'lane_style': lane_style,
                    'source_node_id': f'goal-close-{goal.id}',
                    'target_node_id': f'endpoint-{goal.id}',
                    'path': (
                        f'M {lane_x} {y} '
                        f'C {lane_x + 54} {y + 8}, {trunk_x + 64} {y + 34}, '
                        f'{trunk_x} {y + 34}'
                    ),
                })
                endpoint_x = trunk_x
                endpoint_y = y + 34
            else:
                endpoint_x = lane_x
                endpoint_y = y
            nodes.append({
                'id': f'endpoint-{goal.id}',
                'inspector_id': f'goal-{goal.id}',
                'kind': 'merge',
                'x': endpoint_x,
                'y': endpoint_y,
                'label': (
                    '已合并回主线' if goal.status == 'merged'
                    else '待验收' if goal.status == 'merge_requested'
                    else '分支推进中'
                ),
                'status': goal.status,
                'state_label': (
                    'main 合并提交 · 已闭环'
                    if goal.status == 'merged'
                    else goal.status_label()
                ),
                'risk_reasons': [],
                'is_mine': bool(branch_relevance),
                'relevance': branch_relevance,
                'goal': goal,
                'branch': branch,
                'lane_class': (
                    'graph-lane-main'
                    if goal.status == 'merged'
                    else lane_class
                ),
                'lane_style': '' if goal.status == 'merged' else lane_style,
                'lane_color': lane_color,
                'lane_color_dark': lane_color_dark,
                'merge_scope': 'goal',
                'topology_level': 1,
            })
            y = max(y, endpoint_y)

        head_y = y + 54
        nodes.append({
            'id': f'main-head-{project.id}',
            'inspector_id': f'project-{project.id}',
            'kind': 'main_head',
            'x': trunk_x,
            'y': head_y,
            'label': 'main / HEAD',
            'status': project.status,
            'state_label': '项目主线当前点',
            'is_mine': bool(item.get('relevance')),
            'relevance': item.get('relevance', []),
            'risk_reasons': [],
            'lane_class': 'graph-lane-main',
            'project': project,
            'topology_level': 0,
        })
        y = head_y
        graph_height = max(240, y + 72)
        edges.insert(0, {
            'kind': 'trunk',
            'lane_class': 'graph-lane-main',
            'path': f'M {trunk_x} 24 L {trunk_x} {graph_height - 28}',
        })
        selectable = [node for node in nodes if node['kind'] != 'merge']
        selected = next((
            node for node in selectable
            if node.get('is_mine') and node.get('risk_reasons')
        ), None)
        selected = selected or next((node for node in selectable if node.get('is_mine')), None)
        selected = selected or (selectable[0] if selectable else None)
        graph_projects.append({
            **item,
            'graph': {
                'width': max(980, max_x + 330),
                'height': graph_height,
                'nodes': nodes,
                'edges': edges,
                'selected_id': selected['inspector_id'] if selected else None,
            },
        })
    return graph_projects


def filter_tree_by_status(tree_projects, status_filter):
    if status_filter == 'all':
        return tree_projects
    filtered = []
    for item in tree_projects:
        branches = []
        for branch in item['branches']:
            goal = branch['goal']
            if status_filter == 'risk':
                if branch['risk_count'] > 0:
                    branches.append(branch)
            elif status_filter == 'ready':
                if goal.ready_for_merge():
                    branches.append(branch)
            elif goal.status == status_filter:
                branches.append(branch)
        ungrouped_tasks = item['ungrouped_tasks']
        if status_filter == 'risk':
            ungrouped_tasks = [
                t for t in ungrouped_tasks
                if task_risk_reasons(t)
            ]
        elif status_filter != 'active':
            ungrouped_tasks = []
        if branches or ungrouped_tasks:
            filtered.append({
                **item,
                'branches': branches,
                'ungrouped_tasks': ungrouped_tasks,
            })
    return filtered


def summarize_tree(tree_projects):
    summary = {
        'project_count': len(tree_projects),
        'branch_count': 0,
        'task_count': 0,
        'ready_count': 0,
        'review_count': 0,
        'merged_count': 0,
        'risk_count': 0,
        'waiting_task_count': 0,
    }
    for item in tree_projects:
        for branch in item['branches']:
            goal = branch['goal']
            summary['branch_count'] += 1
            summary['task_count'] += len(branch['tasks'])
            summary['risk_count'] += branch['risk_count']
            summary['waiting_task_count'] += sum(
                1 for task in branch['tasks'] if task.status == 'waiting'
            )
            if goal.ready_for_merge():
                summary['ready_count'] += 1
            if goal.status == 'merge_requested':
                summary['review_count'] += 1
            if goal.status == 'merged':
                summary['merged_count'] += 1
        summary['task_count'] += len(item['ungrouped_tasks'])
        summary['waiting_task_count'] += sum(
            1 for task in item['ungrouped_tasks'] if task.status == 'waiting'
        )
        summary['risk_count'] += sum(1 for t in item['ungrouped_tasks'] if task_risk_reasons(t))
    return summary


# ============================================================
# 权限装饰器
# ============================================================

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def require_admin(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin():
            flash('需要管理员权限', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def require_project_lead(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        project_id = kwargs.get('project_id')
        project = db.session.get(Project, project_id)
        if project is None or not current_user.can_manage_project(project):
            if request.method == 'POST':
                return deny_mutation()
            abort(404)
        return f(*args, **kwargs)
    return decorated


# ============================================================
# 认证路由
# ============================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            flash(f'欢迎回来, {user.display_name}', 'success')
            return redirect(next_page or url_for('index'))
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        display_name = request.form.get('display_name', '').strip()
        if not username or not password or not display_name:
            flash('请填写所有字段', 'error')
            return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('用户名已存在', 'error')
            return render_template('register.html')
        user = User(username=username, display_name=display_name)
        user.set_password(password)
        if User.query.count() == 0:
            user.role = 'admin'
        db.session.add(user)
        db.session.commit()
        flash('注册成功，请登录', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/theme', methods=['POST'])
def theme_toggle():
    current = session.get('theme', 'light')
    session['theme'] = 'dark' if current == 'light' else 'light'
    return redirect(request.referrer or url_for('index'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        display_name = request.form.get('display_name', '').strip()
        old_pw = request.form.get('old_password', '')
        new_pw = request.form.get('new_password', '')

        if not username or not display_name:
            flash('用户名和显示名不能为空', 'error')
            return render_template('profile.html')

        # 检查用户名是否被占用
        existing = User.query.filter_by(username=username).first()
        if existing and existing.id != current_user.id:
            flash('用户名已被占用', 'error')
            return render_template('profile.html')

        # 超级管理员只能改自己的用户名和密码（已经是自己）
        current_user.username = username
        current_user.display_name = display_name

        # 如果填了新密码，需要验证旧密码
        if new_pw:
            if not current_user.check_password(old_pw):
                flash('原密码错误', 'error')
                return render_template('profile.html')
            if len(new_pw) < 2:
                flash('新密码太短', 'error')
                return render_template('profile.html')
            current_user.set_password(new_pw)

        db.session.commit()
        flash('个人资料已更新', 'success')
        return redirect(url_for('profile'))

    return render_template('profile.html')


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    return redirect(url_for('profile'))


@app.route('/api/my-todos')
@login_required
def api_my_todos():
    today = date.today()
    tasks = [{'title': t.title, 'date': str(t.due_date) if t.due_date else None,
              'type': 'task', 'id': t.id}
             for t in Task.query.filter(
                 (Task.assignee_id == current_user.id) |
                 (Task.members.any(id=current_user.id))
             ).filter(Task.status != 'completed').order_by(
                 Task.due_date.asc().nullslast()).all()
             if current_user.can_view_task(t)]
    return {'tasks': tasks, 'today': str(today)}


# ============================================================
# 首页 / 项目列表
# ============================================================

@app.route('/')
@login_required
def index():
    status = request.args.get('status', 'active')
    if status not in ('active', 'completed', 'archived'):
        status = 'active'
    if status == 'archived':
        projects = visible_projects_for(current_user, 'archived')
        pinned_ids = set()
    elif status == 'completed':
        projects = visible_projects_for(current_user, 'completed')
        pinned_ids = {p.id for p in current_user.pinned_projects}
        projects = order_projects_for_user(current_user, projects, pinned_ids)
    else:
        projects = visible_projects_for(current_user, 'active')
        pinned_ids = {p.id for p in current_user.pinned_projects}
        projects = order_projects_for_user(current_user, projects, pinned_ids)

    today = date.today()
    from datetime import timedelta

    visible_tasks = [
        task for project in projects
        for task in visible_tasks_for(current_user, project)
    ]
    overdue = sorted(
        (task for task in visible_tasks if task.is_overdue()),
        key=lambda task: task.due_date,
    )
    unassigned = sorted(
        (task for task in visible_tasks if task.is_open() and not task.assignee_id),
        key=lambda task: task.created_at or datetime.min,
        reverse=True,
    )

    soon = today + timedelta(days=3)
    due_tasks = sorted(
        (
            task for task in visible_tasks
            if task.is_open() and task.due_date and task.due_date <= soon
        ),
        key=lambda task: task.due_date,
    )
    project_stats = {
        project.id: task_stats_for(visible_tasks_for(current_user, project))
        for project in projects
    }

    return render_template('index.html',
        projects=projects,
        pinned_ids=pinned_ids,
        status=status,
        overdue=overdue,
        unassigned=unassigned,
        due_tasks=due_tasks,
        project_stats=project_stats,
        today=today
    )


# ============================================================
# 项目 CRUD
# ============================================================

@app.route('/project/new', methods=['GET', 'POST'])
@require_admin
def project_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        deliverable = request.form.get('deliverable', '').strip()
        lead_id = request.form.get('lead_id', type=int)
        if not name:
            flash('项目名称不能为空', 'error')
            return render_template('project_form.html', project=None)
        project = Project(name=name, description=description, deliverable=deliverable,
                          lead_id=lead_id or None,
                          start_date=parse_date_field('start_date'))
        db.session.add(project)
        db.session.flush()
        log_action('project', project.id, '创建', f'新建项目「{name}」')
        db.session.commit()
        flash('项目创建成功', 'success')
        return redirect(url_for('project_view', project_id=project.id))
    users = User.query.filter(User.is_active == True).all()
    return render_template('project_form.html', project=None, users=users)


@app.route('/project/<int:project_id>/delete', methods=['POST'])
@require_admin
def project_delete(project_id):
    project = Project.query.get_or_404(project_id)
    for task in project.tasks:
        ActionLog.query.filter_by(entity_type='task', entity_id=task.id).delete()
        ProgressLog.query.filter_by(task_id=task.id).delete()
    Task.query.filter_by(project_id=project_id).delete()
    Goal.query.filter_by(project_id=project_id).delete()
    log_action('project', project_id, '删除', f'删除项目「{project.name}」')
    db.session.delete(project)
    db.session.commit()
    flash('项目已删除', 'success')
    return redirect(url_for('index'))


@app.route('/project/<int:project_id>/move/<direction>', methods=['POST'])
@require_admin
def project_move(project_id, direction):
    project = Project.query.get_or_404(project_id)
    projects = Project.query.filter(Project.status != 'archived').order_by(
        Project.sort_order.asc(), Project.created_at.desc()
    ).all()
    idx = next((i for i, p in enumerate(projects) if p.id == project_id), -1)
    if idx == -1:
        return redirect(url_for('index'))
    if direction == 'up' and idx > 0:
        project.sort_order, projects[idx - 1].sort_order = projects[idx - 1].sort_order, project.sort_order
    elif direction == 'down' and idx < len(projects) - 1:
        project.sort_order, projects[idx + 1].sort_order = projects[idx + 1].sort_order, project.sort_order
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/project/<int:project_id>/pin', methods=['POST'])
@login_required
def project_toggle_pin(project_id):
    project = db.session.get(Project, project_id)
    if project is None or not current_user.can_view_project(project):
        return deny_mutation()
    if project in current_user.pinned_projects:
        current_user.pinned_projects.remove(project)
    else:
        current_user.pinned_projects.append(project)
    db.session.commit()
    return redirect(request.referrer or url_for('index'))


@app.route('/project/<int:project_id>/restore', methods=['POST'])
@require_admin
def project_restore(project_id):
    project = Project.query.get_or_404(project_id)
    project.status = 'active'
    db.session.commit()
    flash('项目已恢复', 'success')
    return redirect(url_for('index'))


@app.route('/project/<int:project_id>/edit', methods=['GET', 'POST'])
@require_project_lead
def project_edit(project_id):
    project = Project.query.get_or_404(project_id)
    if request.method == 'POST':
        project.name = request.form.get('name', '').strip()
        project.description = request.form.get('description', '').strip()
        project.deliverable = request.form.get('deliverable', '').strip()
        project.lead_id = request.form.get('lead_id', type=int) or None
        project.start_date = parse_date_field('start_date')
        project.status = request.form.get('status', 'active')
        if not project.name:
            flash('项目名称不能为空', 'error')
            return render_template('project_form.html', project=project)
        db.session.commit()
        flash('项目已更新', 'success')
        return redirect(url_for('project_view', project_id=project.id))
    users = User.query.filter(User.is_active == True).all()
    return render_template('project_form.html', project=project, users=users)


@app.route('/project/<int:project_id>')
@login_required
def project_view(project_id):
    project = Project.query.get_or_404(project_id)
    if not current_user.can_view_project(project):
        abort(404)
    goals = visible_goals_for(current_user, project)
    # 状态排序：进行中(0) > 待进行(1) > 已完成(2)，同状态按截止日期升序
    from sqlalchemy import case
    status_order = case(
        (Task.status == 'in_progress', 0),
        (Task.status == 'waiting', 1),
        (Task.status == 'pending', 2),
        (Task.status == 'completed', 3),
        else_=4,
    )
    tasks = [
        task for task in Task.query.filter_by(project_id=project_id).order_by(
            status_order, Task.due_date.asc().nullslast(), Task.created_at.desc()
        ).all()
        if current_user.can_view_task(task)
    ]
    # 按目标标签分组（无标签的归为"未分组"）
    tasks_by_goal = {}
    for t in tasks:
        tasks_by_goal.setdefault(t.goal_id, []).append(t)
    tree_projects, tree_summary = build_tree_context([project], {
        'project_id': project_id,
        'status': 'all',
        'q': '',
    })
    tree_item = tree_projects[0] if tree_projects else {
        'branches': [],
        'ungrouped_tasks': [],
    }
    stale_task_ids = {t.id for t in tasks if t.is_stale(days=3)}
    project_stats = task_stats_for(tasks)
    return render_template('project.html', project=project, goals=goals,
                          tasks=tasks, tasks_by_goal=tasks_by_goal,
                          tree_projects=tree_projects, tree_summary=tree_summary,
                          branches=tree_item['branches'],
                          ungrouped_tasks=tree_item['ungrouped_tasks'],
                          stale_task_ids=stale_task_ids,
                          project_stats=project_stats,
                          today=date.today(),
                          logs=timeline_logs('project', project_id, viewer=current_user))


# ============================================================
# 目标标签 CRUD（简化）
# ============================================================

@app.route('/project/<int:project_id>/goal/new', methods=['POST'])
@require_project_lead
def goal_new(project_id):
    project = Project.query.get_or_404(project_id)
    title = request.form.get('title', '').strip()
    if not title:
        flash('目标名称不能为空', 'error')
        return redirect(url_for('project_view', project_id=project_id))
    description = request.form.get('description', '').strip()
    deliverable = request.form.get('deliverable', '').strip()
    owner_id = request.form.get('owner_id', type=int) or project.lead_id or current_user.id
    reviewer_id = request.form.get('reviewer_id', type=int) or project.lead_id
    max_order = db.session.query(db.func.max(Goal.order)).filter(
        Goal.project_id == project_id).scalar() or 0
    goal = Goal(project_id=project_id, title=title, description=description,
                deliverable=deliverable, order=max_order + 1,
                owner_id=owner_id, reviewer_id=reviewer_id,
                start_date=parse_date_field('start_date'),
                due_date=parse_date_field('due_date'),
                actual_result=request.form.get('actual_result', '').strip(),
                result_type=validate_result_type(request.form.get('result_type')))
    db.session.add(goal)
    db.session.flush()
    update_user_members(goal, 'members', request.form.getlist('members', type=int))
    log_action('goal', goal.id, '创建', f'新建目标分支「{title}」')
    db.session.commit()
    flash('目标分支已添加', 'success')
    return redirect(url_for('project_view', project_id=project_id))


@app.route('/project/<int:project_id>/goals', methods=['GET'])
@login_required
def goal_manage(project_id):
    project = Project.query.get_or_404(project_id)
    if not current_user.can_view_project(project):
        abort(404)
    goals = visible_goals_for(current_user, project)
    can_manage_project = current_user.can_manage_project(project)
    users_query = User.query.filter(User.is_active == True)
    if can_manage_project:
        users = users_query.order_by(User.display_name).all()
    else:
        visible_user_ids = {current_user.id}
        for goal in goals:
            visible_user_ids.update(
                user_id for user_id in (goal.owner_id, goal.reviewer_id) if user_id
            )
            visible_user_ids.update(user.id for user in goal.members.all())
        users = users_query.filter(User.id.in_(visible_user_ids)).order_by(
            User.display_name
        ).all()
    goal_permissions = {
        goal.id: current_user.can_manage_goal(goal) for goal in goals
    }
    return render_template(
        'goal_manage.html', project=project, goals=goals, users=users,
        can_manage_project=can_manage_project,
        goal_permissions=goal_permissions,
        can_manage_any_goal=any(goal_permissions.values()),
    )


@app.route('/goal/<int:goal_id>/edit', methods=['POST'])
@login_required
def goal_edit(goal_id):
    goal = db.session.get(Goal, goal_id)
    if goal is None or not current_user.can_manage_goal(goal):
        return deny_mutation()
    project = goal.project
    if goal.status != 'active':
        flash('待验收或已闭环分支不能直接编辑', 'error')
        return redirect(url_for('project_view', project_id=goal.project_id))
    title = request.form.get('title', '').strip()
    if not title:
        flash('目标名称不能为空', 'error')
        return redirect(url_for('project_view', project_id=goal.project_id))
    goal.title = title
    goal.description = request.form.get('description', '').strip()
    goal.deliverable = request.form.get('deliverable', '').strip()
    if current_user.can_manage_project(project):
        goal.owner_id = request.form.get('owner_id', type=int) or goal.owner_id
        goal.reviewer_id = request.form.get('reviewer_id', type=int) or goal.reviewer_id
    if 'start_date' in request.form:
        goal.start_date = parse_date_field('start_date')
    if 'due_date' in request.form:
        goal.due_date = parse_date_field('due_date')
    if 'actual_result' in request.form:
        goal.actual_result = request.form.get('actual_result', '').strip()
    if 'result_type' in request.form:
        goal.result_type = validate_result_type(request.form.get('result_type'))
    if current_user.can_manage_project(project) and 'members_present' in request.form:
        update_user_members(goal, 'members', request.form.getlist('members', type=int))
    detail = f'更新目标分支「{goal.title}」'
    log_action('goal', goal.id, '编辑', detail)
    db.session.commit()
    flash('目标分支已更新', 'success')
    return redirect(url_for('project_view', project_id=goal.project_id))


@app.route('/goal/<int:goal_id>/delete', methods=['POST'])
@login_required
def goal_delete(goal_id):
    goal = db.session.get(Goal, goal_id)
    if goal is None or not current_user.can_manage_project(goal.project):
        return deny_mutation()
    project_id = goal.project_id
    if goal.status in ('merge_requested', 'merged'):
        flash('已申请或已闭环的目标分支不能删除', 'error')
        return redirect(url_for('project_view', project_id=project_id))
    # 解除任务与该标签的关联
    Task.query.filter_by(goal_id=goal_id).update({Task.goal_id: None})
    log_action('goal', goal.id, '删除', f'删除目标分支「{goal.title}」')
    db.session.delete(goal)
    db.session.commit()
    flash('目标分支已删除', 'success')
    return redirect(url_for('project_view', project_id=project_id))


@app.route('/goal/<int:goal_id>/merge-request', methods=['POST'])
@login_required
def goal_merge_request(goal_id):
    goal = db.session.get(Goal, goal_id)
    if goal is None or not current_user.can_manage_goal(goal):
        return deny_mutation()
    if not goal.can_request_merge(current_user):
        flash('当前分支不能申请闭环，请确认权限、状态和任务完成情况', 'error')
        return safe_redirect('project_view', project_id=goal.project_id)
    goal.actual_result = request.form.get('actual_result', goal.actual_result or '').strip()
    goal.result_type = validate_result_type(request.form.get('result_type', goal.result_type))
    if not goal.actual_result or goal.result_type not in Goal.VALID_RESULT_TYPES:
        flash('请填写实际结果并选择有效结果类型', 'error')
        return safe_redirect('project_view', project_id=goal.project_id)
    goal.status = 'merge_requested'
    goal.merge_requested_at = datetime.utcnow()
    goal.merge_requested_by_id = current_user.id
    goal.merge_note = request.form.get('merge_note', goal.merge_note or '').strip()
    log_action('goal', goal.id, '申请闭环', f'{current_user.display_name} 申请验收「{goal.title}」')
    db.session.commit()
    flash('已提交闭环申请', 'success')
    return safe_redirect('task_tree')


@app.route('/goal/<int:goal_id>/merge-review', methods=['POST'])
@login_required
def goal_merge_review(goal_id):
    goal = db.session.get(Goal, goal_id)
    if goal is None or not current_user.can_review_goal(goal):
        return deny_mutation()
    if not goal.can_review_merge(current_user):
        flash('没有验收权限，或不能验收自己提交的申请', 'error')
        return safe_redirect('task_tree')
    decision = request.form.get('decision', '').strip()
    merge_note = request.form.get('merge_note', '').strip()
    if decision not in ('approve', 'reject'):
        flash('无效的验收决定', 'error')
        return safe_redirect('task_tree')
    if decision == 'approve':
        goal.status = 'merged'
        goal.merged_at = datetime.utcnow()
        goal.merged_by_id = current_user.id
        goal.merge_note = merge_note
        log_action('goal', goal.id, '闭环通过', merge_note or f'目标分支「{goal.title}」已闭环')
        flash('目标分支已闭环', 'success')
    else:
        goal.status = 'active'
        goal.merge_requested_at = None
        goal.merge_requested_by_id = None
        goal.merge_note = merge_note
        log_action('goal', goal.id, '闭环驳回', merge_note or f'目标分支「{goal.title}」退回继续推进')
        flash('已驳回闭环申请', 'success')
    db.session.commit()
    return safe_redirect('task_tree')


@app.route('/goal/<int:goal_id>/reopen', methods=['POST'])
@login_required
def goal_reopen(goal_id):
    goal = db.session.get(Goal, goal_id)
    if goal is None or not (
        current_user.can_manage_project(goal.project)
        or goal.reviewer_id == current_user.id
    ):
        return deny_mutation()
    if goal.status != 'merged':
        flash('只有已闭环分支可以重新打开', 'error')
        return safe_redirect('project_view', project_id=goal.project_id)
    reason = request.form.get('reason', '').strip() or request.form.get('merge_note', '').strip()
    if not reason:
        flash('请填写重新打开原因', 'error')
        return safe_redirect('project_view', project_id=goal.project_id)
    old_status = goal.status
    goal.status = 'active'
    goal.merge_requested_at = None
    goal.merge_requested_by_id = None
    goal.merged_at = None
    goal.merged_by_id = None
    goal.merge_note = reason
    log_action('goal', goal.id, '重新打开', f'{old_status} -> active：{reason}')
    db.session.commit()
    flash('目标分支已重新打开', 'success')
    return safe_redirect('project_view', project_id=goal.project_id)


# ============================================================
# 任务 CRUD（吸收原问题功能）
# ============================================================

@app.route('/project/<int:project_id>/task/new', methods=['GET', 'POST'])
@login_required
def task_new(project_id):
    project = db.session.get(Project, project_id)
    if project is None or not current_user.can_manage_project(project):
        if request.method == 'POST':
            return deny_mutation()
        abort(404)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('任务标题不能为空', 'error')
            return render_template('task_form.html', project=project, task=None)
        description = request.form.get('description', '').strip()
        deliverable = request.form.get('deliverable', '').strip()
        goal_id = request.form.get('goal_id', type=int) or None
        if goal_id:
            goal = Goal.query.get(goal_id)
            if not goal or goal.project_id != project_id:
                goal_id = None
            elif goal.status != 'active':
                flash('待验收或已闭环分支不能新增任务', 'error')
                return render_template('task_form.html', project=project, task=None)
        assignee_id = request.form.get('assignee_id', type=int) or None
        reviewer_id = request.form.get('reviewer_id', type=int) or None
        status = validate_task_status(request.form.get('status', 'pending'))

        task = Task(
            project_id=project_id, goal_id=goal_id,
            title=title, description=description, deliverable=deliverable,
            assignee_id=assignee_id,
            reviewer_id=reviewer_id,
            submitter_id=current_user.id,
            start_date=parse_date_field('start_date'),
            due_date=parse_date_field('due_date'),
            status=status,
            waiting_reason=request.form.get('waiting_reason', '').strip(),
            waiting_until=parse_date_field('waiting_until'),
        )
        if task.status == 'completed':
            task.completed_at = datetime.utcnow()
        if task.status != 'waiting':
            task.waiting_reason = None
            task.waiting_until = None
        db.session.add(task)
        db.session.flush()
        # 成员
        update_user_members(task, 'members', request.form.getlist('members', type=int))
        log_action('task', task.id, '创建', f'新建任务「{title}」')
        db.session.commit()
        flash('任务已创建', 'success')
        return redirect(url_for('project_view', project_id=project_id))
    goals = Goal.query.filter_by(project_id=project_id, status='active').order_by(Goal.order).all()
    users = User.query.filter(User.is_active == True).all()
    return render_template('task_form.html', project=project, task=None, goals=goals, users=users)


@app.route('/task/<int:task_id>')
@login_required
def task_view(task_id):
    task = Task.query.get_or_404(task_id)
    if not current_user.can_view_task(task):
        abort(404)
    return render_template('task.html', task=task,
                          today=date.today(), logs=timeline_logs('task', task_id))


@app.route('/task/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def task_edit(task_id):
    task = db.session.get(Task, task_id)
    if task is None or not current_user.can_edit_task(task):
        if request.method == 'POST':
            return deny_mutation()
        abort(404)
    project = task.project
    if task_branch_locked(task):
        flash('待验收或已闭环分支不能编辑任务', 'error')
        return redirect(url_for('task_view', task_id=task_id))
    if request.method == 'POST':
        old_status = task.status
        task.title = request.form.get('title', '').strip()
        task.description = request.form.get('description', '').strip()
        task.deliverable = request.form.get('deliverable', '').strip()
        if current_user.can_manage_project(project):
            goal_id = request.form.get('goal_id', type=int) or None
            if goal_id:
                goal = Goal.query.get(goal_id)
                if not goal or goal.project_id != project.id:
                    goal_id = None
                elif goal.status != 'active':
                    flash('不能把任务移入待验收或已闭环分支', 'error')
                    return render_template('task_form.html', project=project, task=task)
            task.goal_id = goal_id
            task.assignee_id = request.form.get('assignee_id', type=int) or None
            task.reviewer_id = request.form.get('reviewer_id', type=int) or None
        task.start_date = parse_date_field('start_date')
        task.due_date = parse_date_field('due_date')
        task.waiting_reason = request.form.get('waiting_reason', task.waiting_reason or '').strip()
        task.waiting_until = parse_date_field('waiting_until')
        status_changed = apply_task_status(task, request.form.get('status', 'pending'))
        if not task.title:
            flash('任务标题不能为空', 'error')
            return render_template('task_form.html', project=project, task=task)
        # 成员
        if current_user.can_manage_project(project):
            update_user_members(task, 'members', request.form.getlist('members', type=int))
        if status_changed:
            log_action('task', task.id, '状态变更', f'{old_status} -> {task.status}')
        log_action('task', task.id, '编辑', f'更新任务「{task.title}」')
        db.session.commit()
        flash('任务已更新', 'success')
        return redirect(url_for('task_view', task_id=task.id))
    goals = [
        goal for goal in visible_goals_for(current_user, project)
        if goal.status == 'active'
    ]
    if current_user.can_manage_project(project):
        users = User.query.filter(User.is_active == True).all()
    else:
        visible_user_ids = {
            user_id for user_id in (
                current_user.id, task.assignee_id, task.reviewer_id, task.submitter_id
            ) if user_id
        }
        visible_user_ids.update(user.id for user in task.members.all())
        users = User.query.filter(
            User.is_active == True, User.id.in_(visible_user_ids)
        ).all()
    return render_template('task_form.html', project=project, task=task, goals=goals, users=users,
                          today=date.today(), logs=timeline_logs('task', task_id))


@app.route('/task/<int:task_id>/quick-status', methods=['POST'])
@login_required
def task_quick_status(task_id):
    task = db.session.get(Task, task_id)
    if task is None or not current_user.can_log_task_progress(task):
        return deny_mutation()
    if task_branch_locked(task):
        flash('待验收或已闭环分支不能更新任务状态', 'error')
        return redirect(request.referrer or url_for('index'))
    old_status = task.status
    if apply_task_status(task, request.form.get('status', 'pending')):
        log_action('task', task.id, '状态变更', f'{old_status} -> {task.status}')
    db.session.commit()
    return redirect(request.referrer or url_for('index'))


@app.route('/task/<int:task_id>/solution', methods=['POST'])
@login_required
def task_solution(task_id):
    task = db.session.get(Task, task_id)
    if task is None or not (
        current_user.can_manage_project(task.project)
        or current_user.id == task.assignee_id
    ):
        return deny_mutation()
    if task_branch_locked(task):
        flash('待验收或已闭环分支不能更新任务方案', 'error')
        return redirect(url_for('task_view', task_id=task_id))
    task.solution = request.form.get('solution', '').strip()
    db.session.commit()
    flash('方案已保存', 'success')
    return redirect(url_for('task_view', task_id=task_id))


@app.route('/task/<int:task_id>/progress', methods=['POST'])
@login_required
def task_progress(task_id):
    """记录任务进展（显式打卡）"""
    task = db.session.get(Task, task_id)
    if task is None or not current_user.can_log_task_progress(task):
        return deny_mutation()
    if task_branch_locked(task):
        flash('待验收或已闭环分支不能记录任务更新', 'error')
        return redirect(url_for('task_view', task_id=task_id))
    entry_type = request.form.get('entry_type', 'progress').strip() or 'progress'
    if entry_type not in ProgressLog.VALID_ENTRY_TYPES:
        entry_type = 'progress'
    content = request.form.get('content', '').strip()
    waiting_reason = request.form.get('waiting_reason', '').strip() or content
    if entry_type == 'waiting' and not waiting_reason:
        flash('请填写等待原因', 'error')
        return redirect(url_for('task_view', task_id=task_id))
    if not content:
        if entry_type == 'no_progress':
            content = '今日暂无实质推进'
        elif entry_type == 'resumed':
            content = '已恢复推进'
        elif entry_type == 'waiting':
            content = waiting_reason
        else:
            flash('请填写进展内容', 'error')
            return redirect(url_for('task_view', task_id=task_id))

    now = datetime.utcnow()
    task.last_checkin_at = now
    action = '记录进展'
    if entry_type == 'progress':
        task.last_progress_at = now
    elif entry_type == 'no_progress':
        action = '记录无进展'
    elif entry_type == 'waiting':
        old_status = task.status
        task.status = 'waiting'
        task.waiting_reason = waiting_reason
        task.waiting_until = parse_date_field('waiting_until')
        task.completed_at = None
        action = '标记等待'
        if old_status != task.status:
            log_action('task', task_id, '状态变更', f'{old_status} -> waiting')
    elif entry_type == 'resumed':
        old_status = task.status
        task.status = 'in_progress'
        task.waiting_reason = None
        task.waiting_until = None
        task.completed_at = None
        task.last_progress_at = now
        action = '恢复推进'
        if old_status != task.status:
            log_action('task', task_id, '状态变更', f'{old_status} -> in_progress')
    log = ProgressLog(
        task_id=task_id,
        user_id=current_user.id,
        entry_type=entry_type,
        checkin_date=date.today(),
        content=content,
    )
    db.session.add(log)
    log_action('task', task_id, action, content)
    db.session.commit()
    flash('任务更新已记录', 'success')
    return redirect(url_for('task_view', task_id=task_id))


@app.route('/task/<int:task_id>/delete', methods=['POST'])
@login_required
def task_delete(task_id):
    task = db.session.get(Task, task_id)
    if task is None or not current_user.can_manage_project(task.project):
        return deny_mutation()
    project_id = task.project_id
    if task_branch_locked(task):
        flash('待验收或已闭环分支不能删除任务', 'error')
        return redirect(url_for('task_view', task_id=task_id))
    ActionLog.query.filter_by(entity_type='task', entity_id=task_id).delete()
    ProgressLog.query.filter_by(task_id=task_id).delete()
    db.session.delete(task)
    db.session.commit()
    flash('任务已删除', 'success')
    return redirect(url_for('project_view', project_id=project_id))


# ============================================================
# 我的工作台
# ============================================================

@app.route('/progress')
@login_required
def progress():
    """当前状态展板：每个项目展示谁在做什么，置顶项目排前"""
    projects = visible_projects_for(current_user, 'active')
    pinned_ids = {p.id for p in current_user.pinned_projects}
    projects = sorted(projects, key=lambda p: (
        0 if p.id in pinned_ids else 1, p.sort_order, p.created_at or datetime.min
    ))
    progress_data = []
    for p in projects:
        visible_tasks = visible_tasks_for(current_user, p)
        stats = task_stats_for(visible_tasks)
        in_progress = [t for t in visible_tasks if t.status == 'in_progress']
        waiting = [t for t in visible_tasks if t.status == 'waiting']
        pending = [t for t in visible_tasks if t.status == 'pending']
        completed = [t for t in visible_tasks if t.status == 'completed']
        progress_data.append({
            'project': p,
            'pinned': p.id in pinned_ids,
            'pct': stats['progress_pct'],
            'in_progress': in_progress,
            'waiting': waiting,
            'pending': pending,
            'completed': completed,
            'total': stats['total'],
        })
    return render_template('progress.html', progress_data=progress_data, today=date.today())


@app.route('/people')
@login_required
def people():
    """人员视角：每个人正在做什么任务"""
    visible_tasks = visible_tasks_for(current_user)
    visible_user_ids = {current_user.id}
    for task in visible_tasks:
        visible_user_ids.update(
            user_id for user_id in (
                task.assignee_id, task.reviewer_id, task.submitter_id
            ) if user_id
        )
        visible_user_ids.update(user.id for user in task.members.all())
    users_query = User.query.filter_by(is_active=True).filter(
        User.username != 'test', User.display_name != '测试'
    )
    if not current_user.is_admin():
        users_query = users_query.filter(User.id.in_(visible_user_ids))
    users = users_query.order_by(User.display_name).all()
    today = date.today()
    people_data = []
    for u in users:
        # 该用户负责的进行中任务
        in_progress = sorted(
            (task for task in visible_tasks if task.assignee_id == u.id and task.status == 'in_progress'),
            key=lambda task: task.due_date or date.max,
        )
        # 该用户负责的待进行任务
        pending = sorted(
            (task for task in visible_tasks if task.assignee_id == u.id and task.status == 'pending'),
            key=lambda task: task.due_date or date.max,
        )
        waiting = sorted(
            (task for task in visible_tasks if task.assignee_id == u.id and task.status == 'waiting'),
            key=lambda task: task.due_date or date.max,
        )
        # 该用户参与的进行中任务（成员）
        member_tasks = sorted(
            (
                task for task in visible_tasks
                if task.assignee_id != u.id
                and task.status in ('in_progress', 'waiting')
                and task.members.filter_by(id=u.id).count() > 0
            ),
            key=lambda task: task.due_date or date.max,
        )
        people_data.append({
            'user': u,
            'in_progress': in_progress,
            'waiting': waiting,
            'pending': pending,
            'member_tasks': member_tasks,
        })
    return render_template('people.html', people_data=people_data, today=today)


@app.route('/my-work')
@login_required
def my_work():
    today = date.today()

    # 我负责的任务（负责人）
    my_assigned = Task.query.filter(
        Task.assignee_id == current_user.id
    ).order_by(Task.due_date.asc().nullslast()).all()

    # 我参与的任务（成员，排除我负责的）
    my_member = Task.query.filter(
        Task.members.any(id=current_user.id),
        Task.assignee_id != current_user.id
    ).order_by(Task.due_date.asc().nullslast()).all()

    # 我验收的任务
    my_reviewed = Task.query.filter(
        Task.reviewer_id == current_user.id
    ).order_by(Task.due_date.asc().nullslast()).all()

    # 我负责的项目（项目 lead 或项目人员）
    led_project_ids = {p.id for p in Project.query.filter_by(lead_id=current_user.id).all()}
    member_project_ids = {p.id for p in current_user.member_projects}
    managed_project_ids = led_project_ids | member_project_ids
    my_projects = Project.query.filter(
        Project.id.in_(managed_project_ids) if managed_project_ids else False
    ).filter(Project.status == 'active').all()

    # 我参与的项目（我在其中负责任务或参与任务，但不是我负责的项目）
    involved_task_project_ids = set()
    for t in my_assigned + my_member:
        involved_task_project_ids.add(t.project_id)
    # 排除已负责的项目，避免重复
    involved_project_ids = involved_task_project_ids - managed_project_ids
    involved_projects = Project.query.filter(
        Project.id.in_(involved_project_ids) if involved_project_ids else False
    ).filter(Project.status == 'active').all()

    my_active_tasks = [t for t in my_assigned + my_member
                       if t.status in ('in_progress', 'pending', 'waiting')]
    stale_tasks = [t for t in my_active_tasks if t.is_stale(days=3)]
    today_logs = ProgressLog.query.filter(
        ProgressLog.user_id == current_user.id,
        ProgressLog.checkin_date == today,
    ).all()
    today_checked_task_ids = {log.task_id for log in today_logs}
    daily_pending_tasks = [
        t for t in my_active_tasks
        if t.id not in today_checked_task_ids
    ]

    return render_template(
        'my-work.html',
        my_assigned=my_assigned,
        my_member=my_member,
        my_reviewed=my_reviewed,
        my_projects=my_projects,
        involved_projects=involved_projects,
        stale_task_ids={t.id for t in stale_tasks},
        today_checked_task_ids=today_checked_task_ids,
        daily_pending_tasks=daily_pending_tasks,
        today=today
    )


# ============================================================
# 卡点面板
# ============================================================

@app.route('/blockers')
@login_required
def blockers():
    today = date.today()
    visible_tasks = visible_tasks_for(current_user)
    overdue = sorted(
        (task for task in visible_tasks if task.is_overdue()),
        key=lambda task: task.due_date,
    )
    unassigned = sorted(
        (task for task in visible_tasks if task.is_open() and not task.assignee_id),
        key=lambda task: task.created_at or datetime.min,
        reverse=True,
    )
    unreviewed = sorted(
        (task for task in visible_tasks if task.is_open() and not task.reviewer_id),
        key=lambda task: task.created_at or datetime.min,
        reverse=True,
    )
    due_tasks = sorted(
        (
            task for task in visible_tasks
            if task.is_open() and task.due_date and task.due_date <= today + timedelta(days=3)
        ),
        key=lambda task: task.due_date,
    )
    stale_tasks = [
        task for task in visible_tasks
        if task.status in ('in_progress', 'pending', 'waiting') and task.is_stale(days=3)
    ]
    stale_tasks.sort(key=lambda t: t.checkin_at() or datetime.min)
    waiting_tasks = sorted(
        (task for task in visible_tasks if task.status == 'waiting'),
        key=lambda task: (task.waiting_until or date.max, task.created_at or datetime.min),
    )
    return render_template('blockers.html',
        overdue=overdue, unassigned=unassigned, unreviewed=unreviewed,
        due_tasks=due_tasks, stale_tasks=stale_tasks,
        waiting_tasks=waiting_tasks, today=today)


# ============================================================
# 日历
# ============================================================

def local_date(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).date()


@app.route('/calendar')
@login_required
def calendar():
    import calendar as cal_mod
    today = date.today()

    month_str = request.args.get('month', '')
    try:
        year, mon = (int(x) for x in month_str.split('-')[:2])
        year, mon = max(2000, year), max(1, min(12, mon))
    except (ValueError, AttributeError):
        year, mon = today.year, today.month

    user_raw = request.args.get('user_id', '')
    day_str = request.args.get('day', '')

    month_start = date(year, mon, 1)
    if mon == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, mon + 1, 1)
    prev_month = date(year - 1, 12, 1) if mon == 1 else date(year, mon - 1, 1)
    month_end = next_month - timedelta(days=1)

    visible_tasks = visible_tasks_for(current_user)
    visible_user_ids = {
        task.assignee_id for task in visible_tasks if task.assignee_id
    }
    visible_user_ids.add(current_user.id)
    users_query = User.query.filter_by(is_active=True)
    if not current_user.is_admin():
        users_query = users_query.filter(User.id.in_(visible_user_ids))
    users = users_query.order_by(User.display_name).all()
    users_by_id = {u.id: u for u in users}

    default_view = user_raw == ''
    if user_raw == 'all':
        sel_user = None
    elif user_raw:
        try:
            sel_user = users_by_id.get(int(user_raw)) or current_user
        except (TypeError, ValueError):
            sel_user = None
    else:
        sel_user = current_user
    sel_user_id = sel_user.id if sel_user else None

    tasks = [
        task for task in visible_tasks
        if task.status != 'completed'
        and (
            (task.start_date and task.start_date <= month_end)
            or (task.due_date and task.due_date >= month_start)
            or (not task.start_date and not task.due_date)
        )
        and (not sel_user or task.assignee_id == sel_user_id)
    ]

    day_items = {}

    def add_item(d, item):
        if d is None or d < month_start or d > month_end:
            return
        day_items.setdefault(d, []).append(item)

    for t in tasks:
        person = t.assignee.display_name if t.assignee else None
        title = t.title
        url = url_for('task_view', task_id=t.id)
        if t.status == 'completed' and t.completed_at:
            d = local_date(t.completed_at)
            add_item(d, {'type': 'done', 'title': title, 'url': url,
                         'person': person, 'badge': 'resolved', 'label': '完成'})
        else:
            if t.status == 'waiting':
                badge = 'waiting'
            else:
                badge = 'in_progress' if t.status == 'in_progress' else 'pending'
            # 开始日期
            if t.start_date and month_start <= t.start_date <= month_end:
                add_item(t.start_date, {'type': 'start', 'title': title, 'url': url,
                                        'person': person, 'badge': badge, 'label': '开始'})
            # 截止日期
            if t.due_date and month_start <= t.due_date <= month_end:
                overdue = t.due_date < today
                add_item(t.due_date, {'type': 'due', 'title': title, 'url': url,
                                      'person': person,
                                      'badge': 'overdue' if overdue else 'due',
                                      'label': '截止'})
            # 无日期任务：显示在今天
            if not t.start_date and not t.due_date:
                if month_start <= today <= month_end:
                    add_item(today, {'type': 'start', 'title': title, 'url': url,
                                     'person': person, 'badge': badge, 'label': '进行'})
            # 跨月任务：本月显示一条
            if t.start_date and t.due_date:
                has_in_month = (month_start <= t.start_date <= month_end) or \
                               (month_start <= t.due_date <= month_end)
                if not has_in_month and t.start_date < month_start and t.due_date > month_end:
                    add_item(month_start, {'type': 'start', 'title': title, 'url': url,
                                           'person': person, 'badge': badge, 'label': '进行中'})

    first_weekday = month_start.weekday()
    grid = []
    cursor = month_start - timedelta(days=first_weekday)
    while cursor <= month_end:
        week = []
        for _ in range(7):
            week.append({
                'date': cursor,
                'in_month': month_start <= cursor <= month_end,
                'items': day_items.get(cursor, []),
                'is_today': cursor == today,
            })
            cursor += timedelta(days=1)
        grid.append(week)

    if day_str:
        try:
            sel_day = date.fromisoformat(day_str)
        except ValueError:
            sel_day = None
    else:
        sel_day = today if month_start <= today <= month_end else month_start
    if sel_day is None:
        sel_day = month_start
    sel_items = day_items.get(sel_day, [])
    order = {'due': 0, 'start': 1, 'done': 2}
    sel_items.sort(key=lambda it: order.get(it['type'], 9))

    return render_template(
        'calendar.html',
        grid=grid,
        month_year=f'{year}-{mon:02d}',
        year=year, mon=mon,
        prev_month=prev_month.strftime('%Y-%m'),
        next_month=next_month.strftime('%Y-%m'),
        users=users, sel_user=sel_user, default_view=default_view,
        user_id=user_raw,
        sel_day=sel_day, sel_items=sel_items,
        today=today,
        weekday_names=['周一', '周二', '周三', '周四', '周五', '周六', '周日'],
    )


@app.route('/task-tree')
@login_required
def task_tree():
    """Git 风格的项目主线、目标分支、任务提交与合并图谱。"""
    project_id = request.args.get('project_id', type=int)
    scope, can_overview = resolve_view_scope(current_user, request.args.get('scope'))
    status_filter = request.args.get('status', 'active')
    if status_filter not in ('all', 'active', 'ready', 'merge_requested', 'merged', 'risk'):
        status_filter = 'active'
    q = request.args.get('q', '').strip()

    project_options = visible_projects_for(current_user, 'active')
    if project_id:
        project = Project.query.get_or_404(project_id)
        if project.status != 'active' or not current_user.can_view_project(project):
            abort(404)
        projects = [project]
    else:
        projects = list(project_options)
        pinned_ids = {p.id for p in current_user.pinned_projects}
        projects = order_projects_for_user(current_user, projects, pinned_ids)

    current_filters = {
        'project_id': project_id,
        'status': status_filter,
        'q': q,
        'scope': scope,
    }
    tree_projects, _ = build_tree_context(
        projects, current_filters, viewer=current_user
    )
    tree_projects = filter_tree_by_status(tree_projects, status_filter)
    summary = summarize_tree(tree_projects)
    graph_projects = build_graph_view_model(tree_projects, current_user)
    context = {
        'tree_projects': tree_projects,
        'graph_projects': graph_projects,
        'project_options': project_options,
        'summary': summary,
        'filters': current_filters,
        'scope': scope,
        'can_overview': can_overview,
        'task_risk_reasons': task_risk_reasons,
        'task_relevance': task_relevance,
        'today': date.today(),
    }

    return render_template('task_tree.html', **context)


# ============================================================
# 用户管理 (仅管理员)
# ============================================================

@app.route('/users')
@require_admin
def user_list():
    users = User.query.order_by(User.role, User.created_at).all()
    projects = Project.query.filter_by(status='active').order_by(Project.sort_order).all()
    # 每个项目人员已有的项目 id 映射
    user_projects_map = {}
    for u in users:
        if u.role == 'project_member':
            user_projects_map[u.id] = [p.id for p in u.member_projects]
    return render_template('users.html', users=users, projects=projects,
                          user_projects_map=user_projects_map)


@app.route('/user/<int:user_id>/projects', methods=['POST'])
@require_admin
def user_projects(user_id):
    """设置用户的项目人员关系"""
    user = User.query.get_or_404(user_id)
    # 超级管理员的项目关系也不能被他人改（超管本来就是全权限，无需分配）
    if user.is_super() and current_user.id != user.id:
        flash('不能修改超级管理员', 'error')
        return redirect(url_for('user_list'))
    project_ids = request.form.getlist('projects', type=int)
    user.member_projects = []
    for pid in project_ids:
        p = Project.query.get(pid)
        if p:
            user.member_projects.append(p)
    db.session.commit()
    flash(f'{user.display_name} 的项目权限已更新', 'success')
    return redirect(url_for('user_list'))


@app.route('/user/create', methods=['POST'])
@require_admin
def user_create():
    username = request.form.get('username', '').strip()
    display_name = request.form.get('display_name', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'member')
    if not username or not display_name or not password:
        flash('请填写所有字段', 'error')
        return redirect(url_for('user_list'))
    if User.query.filter_by(username=username).first():
        flash('用户名已存在', 'error')
        return redirect(url_for('user_list'))
    user = User(username=username, display_name=display_name, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'用户 {display_name} 创建成功', 'success')
    return redirect(url_for('user_list'))


@app.route('/user/<int:user_id>/edit', methods=['POST'])
@require_admin
def user_edit(user_id):
    user = User.query.get_or_404(user_id)
    # 超级管理员不能被其他人编辑
    if user.is_super() and current_user.id != user.id:
        flash('不能修改超级管理员', 'error')
        return redirect(url_for('user_list'))
    username = request.form.get('username', '').strip()
    display_name = request.form.get('display_name', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'member')
    if not username or not display_name:
        flash('用户名和显示名不能为空', 'error')
        return redirect(url_for('user_list'))
    existing = User.query.filter_by(username=username).first()
    if existing and existing.id != user_id:
        flash('用户名已被占用', 'error')
        return redirect(url_for('user_list'))
    user.username = username
    user.display_name = display_name
    user.role = role
    if password:
        user.set_password(password)
    db.session.commit()
    flash(f'用户 {display_name} 已更新', 'success')
    return redirect(url_for('user_list'))


@app.route('/user/<int:user_id>/role', methods=['POST'])
@require_admin
def user_role(user_id):
    user = User.query.get_or_404(user_id)
    # 超级管理员不能被其他人修改（自己可以改自己的角色，但超级管理员应始终保留）
    if user.is_super() and current_user.id != user.id:
        flash('不能修改超级管理员的角色', 'error')
        return redirect(url_for('user_list'))
    new_role = request.form.get('role', 'member')
    if user.role == 'admin' and new_role != 'admin':
        admin_count = User.query.filter_by(role='admin', is_active=True).count()
        if admin_count <= 1:
            flash('不能移除最后一个管理员', 'error')
            return redirect(url_for('user_list'))
    # 超级管理员不能被降级
    if user.is_super() and new_role != 'admin':
        flash('超级管理员不能被降级', 'error')
        return redirect(url_for('user_list'))
    user.role = new_role
    db.session.commit()
    flash(f'{user.display_name} 的角色已更新为 {new_role}', 'success')
    return redirect(url_for('user_list'))


@app.route('/user/<int:user_id>/toggle', methods=['POST'])
@require_admin
def user_toggle(user_id):
    user = User.query.get_or_404(user_id)
    # 超级管理员不能被其他人禁用
    if user.is_super() and current_user.id != user.id:
        flash('不能禁用超级管理员', 'error')
        return redirect(url_for('user_list'))
    if user.role == 'admin' and user.is_active:
        admin_count = User.query.filter_by(role='admin', is_active=True).count()
        if admin_count <= 1:
            flash('不能禁用最后一个管理员', 'error')
            return redirect(url_for('user_list'))
    user.is_active = not user.is_active
    db.session.commit()
    flash(f'{user.display_name} 已{"启用" if user.is_active else "禁用"}', 'success')
    return redirect(url_for('user_list'))


# ============================================================
# 启动
# ============================================================

def init_db():
    with app.app_context():
        if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite:///'):
            migrate_sqlite(DB_PATH)
        db.create_all()


if __name__ == '__main__':
    init_db()
    print('=' * 50)
    print('项目协作平台已启动')
    print('本地访问: http://localhost:5000')
    print('=' * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
