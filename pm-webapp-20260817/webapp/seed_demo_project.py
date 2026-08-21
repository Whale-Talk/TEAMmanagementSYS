"""Create a deterministic large demo project for local product walkthroughs."""
import os
from datetime import date, datetime, timedelta

from app import app, init_db
from models import ActionLog, Goal, ProgressLog, Project, Task, User, db


PROJECT_NAME = '【演示】25D大型无人机研制与交付'


USER_SPECS = (
    ('demo_admin', '演示项目总负责人', 'admin'),
    ('demo_system', '系统总体负责人', 'project_member'),
    ('demo_airframe', '机体结构负责人', 'project_member'),
    ('demo_power', '能源动力负责人', 'project_member'),
    ('demo_control', '飞控软件负责人', 'project_member'),
    ('demo_test', '集成试验负责人', 'project_member'),
    ('demo_quality', '质量与适航负责人', 'project_member'),
    ('demo_supply', '供应链负责人', 'project_member'),
    ('demo_software', '地面站软件工程师', 'member'),
    ('demo_operator', '试飞与运维工程师', 'member'),
)


GOAL_SPECS = (
    {
        'title': '01 总体方案与需求基线',
        'description': '冻结任务场景、性能指标、系统边界和跨专业接口，形成项目共同基线。',
        'deliverable': '总体技术方案、需求基线、接口控制文件和风险清单。',
        'owner': 'demo_system',
        'reviewer': 'demo_admin',
        'due': 18,
        'tasks': (
            ('完成任务场景与载荷谱确认', 'completed', 'demo_system', -10),
            ('冻结整机重量与能量预算', 'in_progress', 'demo_admin', 2),
            ('发布跨专业接口控制文件 ICD V1.0', 'in_progress', 'demo_system', 6),
            ('组织总体方案技术评审', 'pending', 'demo_admin', 12),
        ),
    },
    {
        'title': '02 机体结构与气动设计',
        'description': '完成机体布局、主承力结构设计、气动分析和样件制造准备。',
        'deliverable': '结构数模、强度报告、气动数据库和首件制造图纸。',
        'owner': 'demo_airframe',
        'reviewer': 'demo_system',
        'due': 35,
        'tasks': (
            ('完成机翼布局参数扫描', 'completed', 'demo_airframe', -6),
            ('主承力框有限元强度复核', 'in_progress', 'demo_airframe', 4),
            ('复材铺层工艺评审', 'waiting', 'demo_quality', 8),
            ('释放首件机身制造图纸', 'pending', 'demo_airframe', 20),
        ),
    },
    {
        'title': '03 燃料电池能源系统选型',
        'description': '完成燃料电池、电池缓冲系统、氢路和热管理方案选型。',
        'deliverable': '能源系统选型结论和供应商技术协议。',
        'owner': 'demo_power',
        'reviewer': 'demo_admin',
        'due': -1,
        'actual_result': '已确认 30kW 电堆与高倍率缓冲电池组合，完成台架数据复核并形成采购技术条件。',
        'result_type': 'achieved',
        'tasks': (
            ('完成任务剖面能量仿真', 'completed', 'demo_power', -18),
            ('完成电堆供应商技术澄清', 'completed', 'demo_supply', -12),
            ('完成缓冲电池倍率与寿命校核', 'completed', 'demo_power', -8),
            ('冻结氢路与热管理接口', 'completed', 'demo_system', -3),
        ),
    },
    {
        'title': '04 飞控与航电系统开发',
        'description': '完成飞控律、航电网络、导航与任务管理软件的集成验证。',
        'deliverable': '飞控软件基线、SIL/HIL 报告和航电接口说明。',
        'owner': 'demo_control',
        'reviewer': 'demo_system',
        'due': -4,
        'status': 'merge_requested',
        'actual_result': '飞控 V0.9 基线已通过 SIL 与 HIL 回归，关键故障注入场景全部满足安全策略。',
        'result_type': 'achieved',
        'tasks': (
            ('完成纵向控制律参数整定', 'completed', 'demo_control', -16),
            ('完成航电 CAN 网络矩阵冻结', 'completed', 'demo_control', -13),
            ('完成 HIL 故障注入回归', 'completed', 'demo_test', -7),
            ('发布飞控软件 V0.9 基线', 'completed', 'demo_control', -5),
        ),
    },
    {
        'title': '05 地面站与数据链联调',
        'description': '打通地面站、机载数传、遥测解析和任务规划全链路。',
        'deliverable': '地面站演示版本、数据链联调报告和操作手册。',
        'owner': 'demo_software',
        'reviewer': 'demo_control',
        'due': -15,
        'status': 'merged',
        'actual_result': '地面站演示版完成交付，数据链在 80km 等效链路条件下稳定运行。',
        'result_type': 'achieved',
        'merge_note': '演示验收通过，后续缺陷转入集成试验分支跟踪。',
        'tasks': (
            ('完成遥测协议解析模块', 'completed', 'demo_software', -30),
            ('完成任务航线编辑功能', 'completed', 'demo_software', -26),
            ('完成弱网重连与断点续传验证', 'completed', 'demo_control', -21),
            ('完成用户操作手册初版', 'completed', 'demo_operator', -17),
        ),
    },
    {
        'title': '06 全机集成与地面试验',
        'description': '推进总装、供电、通信、动力和安全联锁的全机级验证。',
        'deliverable': '全机集成状态、地面试验报告和遗留问题清单。',
        'owner': 'demo_test',
        'reviewer': 'demo_admin',
        'due': 14,
        'tasks': (
            ('完成全机线束导通检查', 'completed', 'demo_test', -2),
            ('开展能源系统满功率地面试验', 'in_progress', 'demo_power', -1),
            ('完成舵面全行程与故障保护测试', 'in_progress', 'demo_test', 3),
            ('关闭地面联调遗留问题', 'pending', 'demo_admin', 10),
        ),
    },
    {
        'title': '07 质量、适航与交付资料',
        'description': '建立构型、质量问题和验证证据链，准备客户交付资料包。',
        'deliverable': '构型清单、质量关闭报告、符合性证据和交付资料。',
        'owner': 'demo_quality',
        'reviewer': 'demo_admin',
        'due': 50,
        'tasks': (
            ('建立软硬件构型基线台账', 'completed', 'demo_quality', -1),
            ('整理关键件可追溯性记录', 'in_progress', 'demo_quality', 7),
            ('编制系统安全性评估 SSA', 'in_progress', 'demo_system', 18),
            ('形成客户交付资料目录', 'pending', 'demo_quality', 30),
        ),
    },
    {
        'title': '08 供应链与首架交付保障',
        'description': '保障长周期器件到货、替代料验证、生产排期和现场支持资源。',
        'deliverable': '采购到货计划、缺料清单、替代方案和交付保障计划。',
        'owner': 'demo_supply',
        'reviewer': 'demo_admin',
        'due': 24,
        'tasks': (
            ('锁定电堆与储氢系统交期', 'waiting', 'demo_supply', 1),
            ('完成长周期航电器件齐套检查', 'in_progress', 'demo_supply', 5),
            ('评审关键器件国产替代方案', 'pending', 'demo_quality', 11),
            ('编制首架交付现场保障计划', 'pending', 'demo_operator', 19),
        ),
    },
)


def create_demo_project():
    password = os.environ.get('DEMO_PASSWORD')
    if not password:
        raise RuntimeError('请通过 DEMO_PASSWORD 环境变量提供演示账号密码')

    existing = Project.query.filter_by(name=PROJECT_NAME).first()
    if existing:
        print(f'演示项目已存在，project_id={existing.id}')
        return existing

    password_owner = User(
        username='_demo_password_template',
        display_name='密码模板',
    )
    password_owner.set_password(password)
    shared_password_hash = password_owner.password_hash

    users = {}
    for username, display_name, role in USER_SPECS:
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User(
                username=username,
                display_name=display_name,
                role=role,
                is_active=True,
                password_hash=shared_password_hash,
            )
            db.session.add(user)
        else:
            user.display_name = display_name
            user.role = role
            user.is_active = True
            user.password_hash = shared_password_hash
        users[username] = user
    db.session.flush()

    today = date.today()
    now = datetime.now()
    admin = users['demo_admin']
    project = Project(
        name=PROJECT_NAME,
        description=(
            '以 25D 大型无人机为主线，覆盖总体、结构、能源、飞控、地面站、'
            '集成试验、质量适航和供应链交付的完整研制闭环。'
        ),
        deliverable='完成首架样机集成、地面验证、试飞准备和客户交付资料包。',
        status='active',
        lead_id=admin.id,
        start_date=today - timedelta(days=45),
        sort_order=-100,
    )
    db.session.add(project)
    db.session.flush()

    for user in users.values():
        if user.role == 'project_member' and project not in user.member_projects:
            user.member_projects.append(project)

    db.session.add(ActionLog(
        entity_type='project',
        entity_id=project.id,
        action='创建演示项目',
        detail='生成大型项目任务树与闭环状态样例',
        actor_id=admin.id,
        created_at=now - timedelta(days=45),
    ))

    task_number = 0
    for goal_order, spec in enumerate(GOAL_SPECS, start=1):
        status = spec.get('status', 'active')
        owner = users[spec['owner']]
        reviewer = users[spec['reviewer']]
        goal = Goal(
            project_id=project.id,
            title=spec['title'],
            description=spec['description'],
            deliverable=spec['deliverable'],
            order=goal_order,
            owner_id=owner.id,
            reviewer_id=reviewer.id,
            status=status,
            start_date=today - timedelta(days=max(5, 42 - goal_order * 3)),
            due_date=today + timedelta(days=spec['due']),
            actual_result=spec.get('actual_result'),
            result_type=spec.get('result_type'),
            merge_note=spec.get('merge_note'),
        )
        if status in ('merge_requested', 'merged'):
            goal.merge_requested_at = now - timedelta(days=3)
            goal.merge_requested_by_id = owner.id
        if status == 'merged':
            goal.merged_at = now - timedelta(days=2)
            goal.merged_by_id = reviewer.id
        db.session.add(goal)
        db.session.flush()

        for member_name in {spec['owner'], spec['reviewer'], 'demo_admin'}:
            goal.members.append(users[member_name])

        db.session.add(ActionLog(
            entity_type='goal',
            entity_id=goal.id,
            action='创建目标分支',
            detail=f'建立「{goal.title}」责任与验收边界',
            actor_id=admin.id,
            created_at=now - timedelta(days=40 - goal_order),
        ))

        for local_order, (title, task_status, assignee_name, due_offset) in enumerate(
            spec['tasks'], start=1
        ):
            task_number += 1
            assignee = users[assignee_name]
            task = Task(
                project_id=project.id,
                goal_id=goal.id,
                title=title,
                description=f'围绕“{goal.title}”推进：{title}。',
                deliverable=f'形成可复核的「{title}」结果记录。',
                solution=f'按需求确认、实施、复核、记录四步推进「{title}」。',
                order=local_order,
                status=task_status,
                assignee_id=assignee.id,
                reviewer_id=reviewer.id,
                submitter_id=admin.id,
                start_date=today - timedelta(days=max(2, 16 - local_order * 2)),
                due_date=today + timedelta(days=due_offset),
            )
            if task_status == 'completed':
                task.completed_at = now - timedelta(days=max(1, -due_offset // 2))
            elif task_status == 'waiting':
                task.waiting_reason = '等待供应商数据、工艺确认或外部评审结论'
                task.waiting_until = today + timedelta(days=3 + local_order)
            db.session.add(task)
            db.session.flush()

            task.members.append(owner)
            if reviewer.id != owner.id:
                task.members.append(reviewer)

            age_days = 0 if task_number % 4 == 0 else (1 if task_number % 4 == 1 else 5)
            checkin_at = now - timedelta(days=age_days, hours=task_number % 6)
            if task_status == 'completed':
                entry_type = 'progress'
                content = f'已完成：{title}，结果已提交验收。'
                task.last_progress_at = task.completed_at
                task.last_checkin_at = task.completed_at
                checkin_at = task.completed_at
            elif task_status == 'waiting':
                entry_type = 'waiting'
                content = f'等待外部输入：{task.waiting_reason}。'
                task.last_checkin_at = checkin_at
            elif task_number % 5 == 0:
                entry_type = 'no_progress'
                content = '今日无实质推进，下一步继续跟进依赖项。'
                task.last_checkin_at = checkin_at
            else:
                entry_type = 'progress'
                content = f'已推进「{title}」，完成当前阶段检查并明确下一步。'
                task.last_progress_at = checkin_at
                task.last_checkin_at = checkin_at

            db.session.add(ProgressLog(
                task_id=task.id,
                user_id=assignee.id,
                entry_type=entry_type,
                checkin_date=checkin_at.date(),
                content=content,
                created_at=checkin_at,
            ))
            db.session.add(ActionLog(
                entity_type='task',
                entity_id=task.id,
                action='演示进展',
                detail=content[:280],
                actor_id=assignee.id,
                created_at=checkin_at,
            ))

        if status == 'merge_requested':
            db.session.add(ActionLog(
                entity_type='goal',
                entity_id=goal.id,
                action='申请闭环',
                detail=f'{owner.display_name} 已提交实际结果，等待 {reviewer.display_name} 验收',
                actor_id=owner.id,
                created_at=goal.merge_requested_at,
            ))
        elif status == 'merged':
            db.session.add(ActionLog(
                entity_type='goal',
                entity_id=goal.id,
                action='闭环通过',
                detail=goal.merge_note,
                actor_id=reviewer.id,
                created_at=goal.merged_at,
            ))

    for order, (title, assignee_name, due_offset) in enumerate((
        ('确认下周项目例会输入材料', 'demo_admin', 2),
        ('维护跨专业风险台账', 'demo_admin', 5),
        ('准备客户阶段汇报演示环境', 'demo_operator', 9),
    ), start=1):
        task = Task(
            project_id=project.id,
            title=title,
            description='尚未归入明确目标分支的项目级协调事项。',
            deliverable='形成明确记录并决定后续归属分支。',
            order=order,
            status='in_progress' if order < 3 else 'pending',
            assignee_id=users[assignee_name].id,
            reviewer_id=admin.id,
            submitter_id=admin.id,
            start_date=today - timedelta(days=2),
            due_date=today + timedelta(days=due_offset),
        )
        db.session.add(task)

    db.session.commit()
    print(
        f'已创建演示项目 project_id={project.id}，'
        f'{len(GOAL_SPECS)} 个目标分支，{task_number + 3} 个任务节点，'
        f'{len(users)} 个演示账号'
    )
    return project


if __name__ == '__main__':
    init_db()
    with app.app_context():
        create_demo_project()
