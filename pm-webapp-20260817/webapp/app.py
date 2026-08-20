"""项目协作平台 - Flask 主程序（简化版：项目 → 任务，目标降级为标签）"""
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.parse import quote
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, abort, session, Response
)
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from models import (db, User, Project, Goal, Task,
                    ActionLog, ProgressLog, UserProjectPriority)

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

DB_PATH = os.path.join(BASE_DIR, 'database.db')

app = Flask(__name__)
app.config['SECRET_KEY'] = 'pm-platform-secret-key-change-in-production'
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
    project = Project.query.get_or_404(project_id)
    projects = Project.query.filter(Project.status != 'archived').order_by(
        Project.sort_order.asc(), Project.created_at.desc()
    ).all()
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


def timeline_logs(entity_type, entity_id, limit=50):
    """返回某实体及其子孙实体的操作日志，时间倒序。"""
    from sqlalchemy import or_, and_
    pairs = [(entity_type, entity_id)]
    if entity_type == 'project':
        for t in Project.query.get(entity_id).tasks:
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
        project = Project.query.get_or_404(project_id)
        if not current_user.can_manage_project(project):
            flash('需要管理员或该项目人员权限', 'error')
            return redirect(url_for('project_view', project_id=project_id))
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
                 Task.due_date.asc().nullslast()).all()]
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
        projects = Project.query.filter_by(status='archived').order_by(
            Project.created_at.desc()).all()
        pinned_ids = set()
        projects = list(projects)
    elif status == 'completed':
        projects = Project.query.filter_by(status='completed').order_by(
            Project.sort_order.asc(), Project.created_at.desc()).all()
        pinned_ids = {p.id for p in current_user.pinned_projects}
        projects = order_projects_for_user(current_user, projects, pinned_ids)
    else:
        projects = Project.query.filter_by(status='active').order_by(
            Project.sort_order.asc(), Project.created_at.desc()).all()
        pinned_ids = {p.id for p in current_user.pinned_projects}
        projects = order_projects_for_user(current_user, projects, pinned_ids)

    today = date.today()
    from datetime import timedelta

    overdue = Task.query.filter(
        Task.due_date < today,
        Task.status != 'completed'
    ).order_by(Task.due_date.asc()).all()

    unassigned = Task.query.filter(
        Task.assignee_id == None,
        Task.status != 'completed'
    ).order_by(Task.created_at.desc()).all()

    soon = today + timedelta(days=3)
    due_tasks = Task.query.filter(
        Task.due_date.isnot(None),
        Task.status != 'completed',
        Task.due_date <= soon
    ).order_by(Task.due_date.asc()).all()

    return render_template('index.html',
        projects=projects,
        pinned_ids=pinned_ids,
        status=status,
        overdue=overdue,
        unassigned=unassigned,
        due_tasks=due_tasks,
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
        start_date_str = request.form.get('start_date', '')
        project = Project(name=name, description=description, deliverable=deliverable,
                          lead_id=lead_id or None,
                          start_date=date.fromisoformat(start_date_str) if start_date_str else None)
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
    project = Project.query.get_or_404(project_id)
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
        start_date_str = request.form.get('start_date', '')
        project.start_date = date.fromisoformat(start_date_str) if start_date_str else None
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
    goals = Goal.query.filter_by(project_id=project_id).order_by(Goal.order).all()
    # 状态排序：进行中(0) > 待进行(1) > 已完成(2)，同状态按截止日期升序
    from sqlalchemy import case
    status_order = case(
        (Task.status == 'in_progress', 0),
        (Task.status == 'pending', 1),
        (Task.status == 'completed', 2),
        else_=3,
    )
    tasks = Task.query.filter_by(project_id=project_id).order_by(
        status_order, Task.due_date.asc().nullslast(), Task.created_at.desc()
    ).all()
    # 按目标标签分组（无标签的归为"未分组"）
    tasks_by_goal = {}
    for t in tasks:
        tasks_by_goal.setdefault(t.goal_id, []).append(t)
    return render_template('project.html', project=project, goals=goals,
                          tasks=tasks, tasks_by_goal=tasks_by_goal,
                          today=date.today(), logs=timeline_logs('project', project_id))


# ============================================================
# 目标标签 CRUD（简化）
# ============================================================

@app.route('/project/<int:project_id>/goal/new', methods=['POST'])
@require_project_lead
def goal_new(project_id):
    title = request.form.get('title', '').strip()
    if not title:
        flash('目标名称不能为空', 'error')
        return redirect(url_for('project_view', project_id=project_id))
    description = request.form.get('description', '').strip()
    deliverable = request.form.get('deliverable', '').strip()
    max_order = db.session.query(db.func.max(Goal.order)).filter(
        Goal.project_id == project_id).scalar() or 0
    goal = Goal(project_id=project_id, title=title, description=description,
                deliverable=deliverable, order=max_order + 1)
    db.session.add(goal)
    db.session.commit()
    flash('目标标签已添加', 'success')
    return redirect(url_for('project_view', project_id=project_id))


@app.route('/project/<int:project_id>/goals', methods=['GET'])
@require_project_lead
def goal_manage(project_id):
    project = Project.query.get_or_404(project_id)
    goals = Goal.query.filter_by(project_id=project_id).order_by(Goal.order).all()
    return render_template('goal_manage.html', project=project, goals=goals)


@app.route('/goal/<int:goal_id>/edit', methods=['POST'])
@login_required
def goal_edit(goal_id):
    goal = Goal.query.get_or_404(goal_id)
    project = goal.project
    if not current_user.can_manage_project(project):
        flash('需要项目负责人或管理员权限', 'error')
        return redirect(url_for('project_view', project_id=goal.project_id))
    title = request.form.get('title', '').strip()
    if not title:
        flash('目标名称不能为空', 'error')
        return redirect(url_for('project_view', project_id=goal.project_id))
    goal.title = title
    goal.description = request.form.get('description', '').strip()
    goal.deliverable = request.form.get('deliverable', '').strip()
    db.session.commit()
    flash('目标标签已更新', 'success')
    return redirect(url_for('project_view', project_id=goal.project_id))


@app.route('/goal/<int:goal_id>/delete', methods=['POST'])
@login_required
def goal_delete(goal_id):
    goal = Goal.query.get_or_404(goal_id)
    project_id = goal.project_id
    project = goal.project
    if not current_user.can_manage_project(project):
        flash('需要项目负责人或管理员权限', 'error')
        return redirect(url_for('project_view', project_id=project_id))
    # 解除任务与该标签的关联
    Task.query.filter_by(goal_id=goal_id).update({Task.goal_id: None})
    db.session.delete(goal)
    db.session.commit()
    flash('目标标签已删除', 'success')
    return redirect(url_for('project_view', project_id=project_id))


# ============================================================
# 任务 CRUD（吸收原问题功能）
# ============================================================

@app.route('/project/<int:project_id>/task/new', methods=['GET', 'POST'])
@login_required
def task_new(project_id):
    project = Project.query.get_or_404(project_id)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('任务标题不能为空', 'error')
            return render_template('task_form.html', project=project, task=None)
        description = request.form.get('description', '').strip()
        deliverable = request.form.get('deliverable', '').strip()
        goal_id = request.form.get('goal_id', type=int) or None
        assignee_id = request.form.get('assignee_id', type=int) or None
        reviewer_id = request.form.get('reviewer_id', type=int) or None
        due_date_str = request.form.get('due_date', '')
        status = request.form.get('status', 'pending')

        task = Task(
            project_id=project_id, goal_id=goal_id,
            title=title, description=description, deliverable=deliverable,
            assignee_id=assignee_id,
            reviewer_id=reviewer_id,
            submitter_id=current_user.id,
            due_date=date.fromisoformat(due_date_str) if due_date_str else None,
            status=status
        )
        db.session.add(task)
        db.session.flush()
        # 成员
        member_ids = request.form.getlist('members', type=int)
        for mid in member_ids:
            member = User.query.get(mid)
            if member:
                task.members.append(member)
        log_action('task', task.id, '创建', f'新建任务「{title}」')
        db.session.commit()
        flash('任务已创建', 'success')
        return redirect(url_for('project_view', project_id=project_id))
    goals = Goal.query.filter_by(project_id=project_id).order_by(Goal.order).all()
    users = User.query.filter(User.is_active == True).all()
    return render_template('task_form.html', project=project, task=None, goals=goals, users=users)


@app.route('/task/<int:task_id>')
@login_required
def task_view(task_id):
    task = Task.query.get_or_404(task_id)
    users = User.query.filter(User.is_active == True).all()
    return render_template('task.html', task=task, users=users,
                          today=date.today(), logs=timeline_logs('task', task_id))


@app.route('/task/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def task_edit(task_id):
    task = Task.query.get_or_404(task_id)
    project = task.project
    if not current_user.can_edit_task(task):
        flash('没有编辑权限', 'error')
        return redirect(url_for('task_view', task_id=task_id))
    if request.method == 'POST':
        task.title = request.form.get('title', '').strip()
        task.description = request.form.get('description', '').strip()
        task.deliverable = request.form.get('deliverable', '').strip()
        task.goal_id = request.form.get('goal_id', type=int) or None
        task.assignee_id = request.form.get('assignee_id', type=int) or None
        task.reviewer_id = request.form.get('reviewer_id', type=int) or None
        due_date_str = request.form.get('due_date', '')
        task.due_date = date.fromisoformat(due_date_str) if due_date_str else None
        task.status = request.form.get('status', 'pending')
        if not task.title:
            flash('任务标题不能为空', 'error')
            return render_template('task_form.html', project=project, task=task)
        # 成员
        member_ids = request.form.getlist('members', type=int)
        task.members = []
        for mid in member_ids:
            member = User.query.get(mid)
            if member:
                task.members.append(member)
        if task.status == 'completed' and not task.completed_at:
            task.completed_at = datetime.utcnow()
        elif task.status != 'completed':
            task.completed_at = None
        db.session.commit()
        flash('任务已更新', 'success')
        return redirect(url_for('task_view', task_id=task.id))
    goals = Goal.query.filter_by(project_id=project.id).order_by(Goal.order).all()
    users = User.query.filter(User.is_active == True).all()
    return render_template('task_form.html', project=project, task=task, goals=goals, users=users,
                          today=date.today(), logs=timeline_logs('task', task_id))


@app.route('/task/<int:task_id>/quick-status', methods=['POST'])
@login_required
def task_quick_status(task_id):
    task = Task.query.get_or_404(task_id)
    if not current_user.can_edit_task(task):
        flash('没有权限', 'error')
        return redirect(request.referrer or url_for('index'))
    task.status = request.form.get('status', 'pending')
    if task.status == 'completed':
        task.completed_at = datetime.utcnow()
    else:
        task.completed_at = None
    db.session.commit()
    return redirect(request.referrer or url_for('index'))


@app.route('/task/<int:task_id>/solution', methods=['POST'])
@login_required
def task_solution(task_id):
    task = Task.query.get_or_404(task_id)
    project = task.project
    if not current_user.can_manage_project(project) and current_user.id != task.assignee_id:
        flash('没有权限', 'error')
        return redirect(url_for('task_view', task_id=task_id))
    task.solution = request.form.get('solution', '').strip()
    db.session.commit()
    flash('方案已保存', 'success')
    return redirect(url_for('task_view', task_id=task_id))


@app.route('/task/<int:task_id>/progress', methods=['POST'])
@login_required
def task_progress(task_id):
    """记录任务进展（显式打卡）"""
    task = Task.query.get_or_404(task_id)
    if not current_user.can_edit_task(task):
        flash('没有权限', 'error')
        return redirect(url_for('task_view', task_id=task_id))
    content = request.form.get('content', '').strip()
    if not content:
        flash('请填写进展内容', 'error')
        return redirect(url_for('task_view', task_id=task_id))
    task.last_progress_at = datetime.utcnow()
    log = ProgressLog(task_id=task_id, user_id=current_user.id, content=content)
    db.session.add(log)
    log_action('task', task_id, '记录进展', content)
    db.session.commit()
    flash('进展已记录', 'success')
    return redirect(url_for('task_view', task_id=task_id))


@app.route('/task/<int:task_id>/delete', methods=['POST'])
@login_required
def task_delete(task_id):
    task = Task.query.get_or_404(task_id)
    project_id = task.project_id
    if not current_user.can_manage_project(task.project) and current_user.id != task.assignee_id and current_user.id != task.submitter_id:
        flash('没有删除权限', 'error')
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
    projects = Project.query.filter_by(status='active').all()
    pinned_ids = {p.id for p in current_user.pinned_projects}
    projects = sorted(projects, key=lambda p: (
        0 if p.id in pinned_ids else 1, p.sort_order, p.created_at or datetime.min
    ))
    progress_data = []
    for p in projects:
        in_progress = [t for t in p.tasks if t.status == 'in_progress']
        pending = [t for t in p.tasks if t.status == 'pending']
        completed = [t for t in p.tasks if t.status == 'completed']
        progress_data.append({
            'project': p,
            'pinned': p.id in pinned_ids,
            'pct': p.progress_pct(),
            'in_progress': in_progress,
            'pending': pending,
            'completed': completed,
            'total': len(p.tasks),
        })
    return render_template('progress.html', progress_data=progress_data, today=date.today())


@app.route('/people')
@login_required
def people():
    """人员视角：每个人正在做什么任务"""
    users = User.query.filter_by(is_active=True).filter(
        User.username != 'test', User.display_name != '测试'
    ).order_by(User.display_name).all()
    today = date.today()
    people_data = []
    for u in users:
        # 该用户负责的进行中任务
        in_progress = Task.query.filter(
            Task.assignee_id == u.id,
            Task.status == 'in_progress'
        ).order_by(Task.due_date.asc().nullslast()).all()
        # 该用户负责的待进行任务
        pending = Task.query.filter(
            Task.assignee_id == u.id,
            Task.status == 'pending'
        ).order_by(Task.due_date.asc().nullslast()).all()
        # 该用户参与的进行中任务（成员）
        member_tasks = Task.query.filter(
            Task.members.any(id=u.id),
            Task.assignee_id != u.id,
            Task.status == 'in_progress'
        ).order_by(Task.due_date.asc().nullslast()).all()
        people_data.append({
            'user': u,
            'in_progress': in_progress,
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

    # 我的任务中3天没推进的（进行中/待进行，且 last_progress_at 为空或超过3天）
    from datetime import datetime as dt
    stale_cutoff = dt.utcnow() - timedelta(days=3)
    my_active_tasks = [t for t in my_assigned + my_member
                       if t.status in ('in_progress', 'pending')]
    stale_tasks = [t for t in my_active_tasks
                   if t.last_progress_at is None or t.last_progress_at < stale_cutoff]

    return render_template(
        'my-work.html',
        my_assigned=my_assigned,
        my_member=my_member,
        my_reviewed=my_reviewed,
        my_projects=my_projects,
        involved_projects=involved_projects,
        stale_task_ids={t.id for t in stale_tasks},
        today=today
    )


# ============================================================
# 卡点面板
# ============================================================

@app.route('/blockers')
@login_required
def blockers():
    today = date.today()
    overdue = Task.query.filter(
        Task.due_date < today,
        Task.status != 'completed'
    ).order_by(Task.due_date.asc()).all()
    unassigned = Task.query.filter(
        Task.assignee_id == None,
        Task.status != 'completed'
    ).order_by(Task.created_at.desc()).all()
    unreviewed = Task.query.filter(
        Task.reviewer_id == None,
        Task.status != 'completed'
    ).order_by(Task.created_at.desc()).all()
    due_tasks = Task.query.filter(
        Task.due_date.isnot(None),
        Task.status != 'completed',
        Task.due_date <= today + timedelta(days=3)
    ).order_by(Task.due_date.asc()).all()
    # 超过3天未推进的任务（进行中/待进行，且 last_progress_at 为空或超过3天）
    from datetime import datetime as dt
    stale_cutoff = dt.utcnow() - timedelta(days=3)
    stale_tasks = Task.query.filter(
        Task.status.in_(['in_progress', 'pending']),
        db.or_(
            Task.last_progress_at.is_(None),
            Task.last_progress_at < stale_cutoff,
        )
    ).order_by(Task.last_progress_at.asc().nullsfirst()).all()
    return render_template('blockers.html',
        overdue=overdue, unassigned=unassigned, unreviewed=unreviewed,
        due_tasks=due_tasks, stale_tasks=stale_tasks, today=today)


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

    users = User.query.filter_by(is_active=True).order_by(User.display_name).all()
    users_by_id = {u.id: u for u in users}

    default_view = user_raw == ''
    if user_raw == 'all':
        sel_user = None
    elif user_raw:
        try:
            sel_user = users_by_id.get(int(user_raw))
        except (TypeError, ValueError):
            sel_user = None
    else:
        sel_user = current_user
    sel_user_id = sel_user.id if sel_user else None

    from sqlalchemy import or_, and_
    tasks_q = Task.query.filter(
        Task.status != 'completed',
        or_(
            and_(Task.start_date.isnot(None), Task.start_date <= month_end),
            and_(Task.due_date.isnot(None), Task.due_date >= month_start),
            and_(Task.start_date.is_(None), Task.due_date.is_(None)),
        )
    )
    if sel_user:
        tasks_q = tasks_q.filter(Task.assignee_id == sel_user_id)
    tasks = tasks_q.all()

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
        db.create_all()


if __name__ == '__main__':
    init_db()
    print('=' * 50)
    print('项目协作平台已启动')
    print('本地访问: http://localhost:5000')
    print('=' * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
