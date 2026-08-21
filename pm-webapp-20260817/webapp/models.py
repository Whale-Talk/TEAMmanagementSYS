"""数据模型定义：项目主线 -> 目标分支 -> 任务节点。"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta

db = SQLAlchemy()


# 用户-项目 置顶关系（成员可把关注的项目置顶，可多个）
project_pins = db.Table('project_pins',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('project_id', db.Integer, db.ForeignKey('project.id'), primary_key=True)
)

# 任务-成员多对多
task_members = db.Table('task_members',
    db.Column('task_id', db.Integer, db.ForeignKey('task.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

# 目标分支-成员多对多
goal_members = db.Table('goal_members',
    db.Column('goal_id', db.Integer, db.ForeignKey('goal.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)

# 用户-项目 项目人员关系（项目人员在这些项目里有全部权限）
project_members = db.Table('project_members',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('project_id', db.Integer, db.ForeignKey('project.id'), primary_key=True)
)


class UserProjectPriority(db.Model):
    """个人项目重要度排序：每个用户维护自己视角下项目的排列。"""
    __tablename__ = 'user_project_priority'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    priority = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint('user_id', 'project_id', name='uq_user_project'),)


class User(UserMixin, db.Model):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), default='member')  # admin / project_member / member
    is_super_admin = db.Column(db.Boolean, default=False)  # 超级管理员，不可被他人修改
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    led_projects = db.relationship('Project', backref='lead', lazy=True)
    pinned_projects = db.relationship(
        'Project', secondary=project_pins,
        backref=db.backref('pinned_by', lazy='dynamic'), lazy='select'
    )
    member_projects = db.relationship(
        'Project', secondary=project_members,
        backref=db.backref('project_members', lazy='dynamic'), lazy='select'
    )
    submitted_tasks = db.relationship(
        'Task', foreign_keys='Task.submitter_id', backref='submitter', lazy=True
    )
    assigned_tasks = db.relationship(
        'Task', foreign_keys='Task.assignee_id', backref='assignee', lazy=True
    )
    reviewed_tasks = db.relationship(
        'Task', foreign_keys='Task.reviewer_id', backref='reviewer', lazy=True
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def is_super(self):
        return self.is_super_admin

    def is_project_member_of(self, project):
        """是否是某项目的项目人员"""
        if self.role == 'admin':
            return True
        if self.role == 'project_member':
            return project in self.member_projects
        return False

    def can_manage_project(self, project):
        """是否有项目管理权（管理员 / 项目负责人 / 该项目项目人员）"""
        if self.role == 'admin':
            return True
        if project and project.lead_id == self.id:
            return True
        if self.role == 'project_member':
            return project in self.member_projects
        return False

    def is_directly_related_to_task(self, task):
        """不把管理员身份当作业务关系，供“我的”高亮和读取授权复用。"""
        if not task:
            return False
        if self.id in (
            task.assignee_id,
            task.submitter_id,
            task.reviewer_id,
        ):
            return True
        return task.members.filter_by(id=self.id).count() > 0

    def is_directly_related_to_goal(self, goal):
        if not goal:
            return False
        if self.id in (goal.owner_id, goal.reviewer_id):
            return True
        if goal.members.filter_by(id=self.id).count() > 0:
            return True
        return any(self.is_directly_related_to_task(task) for task in goal.tasks)

    def is_directly_related_to_project(self, project):
        if not project:
            return False
        if project.lead_id == self.id or project in self.member_projects:
            return True
        if any(self.is_directly_related_to_goal(goal) for goal in project.goals):
            return True
        return any(
            self.is_directly_related_to_task(task)
            for task in project.tasks
            if task.goal_id is None
        )

    def can_view_project(self, project):
        """项目读取权，与编辑/管理权严格分离。"""
        if not project:
            return False
        return self.is_admin() or self.is_directly_related_to_project(project)

    def can_view_goal(self, goal):
        if not goal:
            return False
        if self.can_manage_project(goal.project):
            return True
        return self.is_directly_related_to_goal(goal)

    def can_view_task(self, task):
        if not task:
            return False
        if self.can_manage_project(task.project):
            return True
        if self.is_directly_related_to_task(task):
            return True
        return bool(task.goal and self.is_directly_related_to_goal(task.goal))

    def can_edit_task(self, task):
        """是否有任务编辑权（项目管理者 / 目标负责人 / 任务负责人）"""
        if self.can_manage_project(task.project):
            return True
        if task.goal and task.goal.owner_id == self.id:
            return True
        return task.assignee_id == self.id

    def can_manage_goal(self, goal):
        if not goal:
            return False
        return self.can_manage_project(goal.project) or goal.owner_id == self.id

    def can_review_goal(self, goal):
        if not goal:
            return False
        if self.can_manage_project(goal.project):
            return True
        if goal.reviewer_id == self.id:
            return True
        return False

    def can_log_task_progress(self, task):
        if self.can_manage_project(task.project):
            return True
        if task.assignee_id == self.id:
            return True
        if task.goal and task.goal.owner_id == self.id:
            return True
        return task.members.filter_by(id=self.id).count() > 0


class Project(db.Model):
    __tablename__ = 'project'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='active')  # active / completed / archived
    lead_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    deliverable = db.Column(db.Text)  # 预期产出
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    goals = db.relationship('Goal', backref='project', lazy=True, order_by='Goal.order')
    tasks = db.relationship('Task', backref='project', lazy=True,
                            foreign_keys='Task.project_id')

    def progress_pct(self):
        """计算项目完成百分比"""
        tasks = self.tasks
        if not tasks:
            return 0
        done = sum(1 for t in tasks if t.status == 'completed')
        return int(done / len(tasks) * 100)

    def task_stats(self):
        """统计项目下的任务"""
        total = len(self.tasks)
        open_tasks = [t for t in self.tasks if t.status != 'completed']
        overdue = [t for t in open_tasks if t.is_overdue()]
        unassigned = [t for t in open_tasks if not t.assignee_id]
        return {
            'total': total,
            'open': len(open_tasks),
            'urgent': 0,
            'overdue': len(overdue),
            'unassigned': len(unassigned),
        }

    def work_stats(self):
        """统计项目当前工作负载"""
        tasks_in_progress = sum(1 for t in self.tasks if t.status == 'in_progress')
        tasks_open = sum(1 for t in self.tasks if t.status != 'completed')
        return {
            'tasks_in_progress': tasks_in_progress,
            'tasks_open': tasks_open,
        }


class Goal(db.Model):
    """目标分支：从项目主线分出的可申请闭环的生命周期实体。"""
    __tablename__ = 'goal'

    VALID_STATUSES = ('active', 'merge_requested', 'merged')
    VALID_RESULT_TYPES = ('achieved', 'answered', 'cancelled', 'transferred')

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)  # 为什么做（分组目的）
    deliverable = db.Column(db.Text)  # 预期产出
    order = db.Column(db.Integer, default=0)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    status = db.Column(db.String(20), default='active')  # active / merge_requested / merged
    start_date = db.Column(db.Date, nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    actual_result = db.Column(db.Text, nullable=True)
    result_type = db.Column(db.String(20), nullable=True)
    merge_requested_at = db.Column(db.DateTime, nullable=True)
    merge_requested_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    merged_at = db.Column(db.DateTime, nullable=True)
    merged_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    merge_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner = db.relationship('User', foreign_keys=[owner_id], backref='owned_goals')
    reviewer = db.relationship('User', foreign_keys=[reviewer_id], backref='review_goals')
    merge_requested_by = db.relationship('User', foreign_keys=[merge_requested_by_id])
    merged_by = db.relationship('User', foreign_keys=[merged_by_id])
    members = db.relationship('User', secondary=goal_members, backref='member_goals', lazy='dynamic')
    tasks = db.relationship('Task', backref='goal', lazy=True,
                            foreign_keys='Task.goal_id')

    def status_label(self):
        return {
            'active': '进行中',
            'merge_requested': '待验收',
            'merged': '已闭环',
        }.get(self.status, self.status)

    def result_type_label(self):
        return {
            'achieved': '达成',
            'answered': '已回答',
            'cancelled': '取消',
            'transferred': '转交',
        }.get(self.result_type, self.result_type or '')

    def task_stats(self):
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if t.status == 'completed')
        open_tasks = [t for t in self.tasks if t.is_open()]
        return {
            'total': total,
            'completed': completed,
            'open': len(open_tasks),
            'waiting': sum(1 for t in self.tasks if t.status == 'waiting'),
            'overdue': sum(1 for t in open_tasks if t.is_overdue()),
            'unassigned': sum(1 for t in open_tasks if not t.assignee_id),
        }

    def all_tasks_completed(self):
        return bool(self.tasks) and all(t.status == 'completed' for t in self.tasks)

    def ready_for_merge(self):
        return (
            self.status == 'active'
            and self.all_tasks_completed()
        )

    def can_request_merge(self, user):
        return user.can_manage_goal(self) and self.ready_for_merge()

    def can_review_merge(self, user):
        if self.status != 'merge_requested':
            return False
        if not user.can_review_goal(self):
            return False
        return self.merge_requested_by_id != user.id

    def is_overdue(self):
        return bool(self.due_date and self.status != 'merged' and self.due_date < date.today())


class Task(db.Model):
    """任务 —— 吸收原问题的功能，直接挂在项目下，可附目标标签"""
    __tablename__ = 'task'

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    goal_id = db.Column(db.Integer, db.ForeignKey('goal.id'), nullable=True)  # 目标标签，可空
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    order = db.Column(db.Integer, default=0)
    VALID_STATUSES = ('pending', 'in_progress', 'waiting', 'completed')

    status = db.Column(db.String(20), default='pending')  # pending / in_progress / waiting / completed
    assignee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    submitter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    deliverable = db.Column(db.Text)  # 预期产出
    solution = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    last_progress_at = db.Column(db.DateTime, nullable=True)  # 最后一次记录进展的时间
    last_checkin_at = db.Column(db.DateTime, nullable=True)  # 最后一次打卡/检查时间
    waiting_reason = db.Column(db.Text, nullable=True)
    waiting_until = db.Column(db.Date, nullable=True)

    members = db.relationship('User', secondary=task_members, backref='member_tasks', lazy='dynamic')
    progress_logs = db.relationship('ProgressLog', backref='task', lazy=True,
                                    order_by='ProgressLog.created_at.desc()')

    def is_overdue(self):
        if self.due_date and self.is_open():
            return self.due_date < date.today()
        return False

    def days_overdue(self):
        if not self.is_overdue():
            return 0
        from datetime import date
        return (date.today() - self.due_date).days

    def status_label(self):
        labels = {
            'pending': '待进行',
            'in_progress': '进行中',
            'waiting': '等待中',
            'completed': '已完成',
        }
        return labels.get(self.status, self.status)

    def is_open(self):
        return self.status != 'completed'

    def checkin_at(self):
        return self.last_checkin_at or self.last_progress_at

    def is_stale(self, days=3):
        if not self.is_open():
            return False
        cutoff = datetime.utcnow() - timedelta(days=days)
        last = self.checkin_at()
        return last is None or last < cutoff


class ActionLog(db.Model):
    """操作日志：记录谁在什么时间对什么实体做了什么操作"""
    __tablename__ = 'action_log'

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(20), nullable=False)  # project/goal/task/user
    entity_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(50), nullable=False)
    detail = db.Column(db.String(300), nullable=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    actor = db.relationship('User', foreign_keys=[actor_id])


class ProgressLog(db.Model):
    """任务进展记录：成员显式打卡，记录推进内容"""
    __tablename__ = 'progress_log'

    VALID_ENTRY_TYPES = ('progress', 'no_progress', 'waiting', 'resumed')

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    entry_type = db.Column(db.String(20), default='progress')
    checkin_date = db.Column(db.Date, default=date.today)
    content = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id])


class Report(db.Model):
    """AI 时间段工作总结报告（持久化，可回看历史）"""
    __tablename__ = 'report'

    id = db.Column(db.Integer, primary_key=True)
    date_from = db.Column(db.Date, nullable=False)
    date_to = db.Column(db.Date, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    summary_json = db.Column(db.Text, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('User', foreign_keys=[created_by_id])
