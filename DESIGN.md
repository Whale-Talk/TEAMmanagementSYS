# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-08-21
- Primary product surfaces: 我的工作、项目空间、任务树、项目详情、任务详情、团队态势、日历、系统管理
- Evidence reviewed: `webapp/app.py`, `webapp/models.py`, `webapp/templates/`, `webapp/static/style.css`, the 2026-08 product discussion transcript, and `/home/mjy/Workspace/Whale-Talk/TEAMmanagementSYS/docs/architecture.md`, `docs/requirements-spec.md`, `api/openapi.yaml`, `db/schema.sql` as the closed-loop hierarchy reference.

## Brand
- Personality: 冷静、可靠、直接、行动导向。
- Trust signals: 明确负责人、截止时间、当前状态、最近进展和下一步动作。
- Avoid: 装饰性 emoji 堆叠、没有业务含义的关系线、同级入口过多、后台表单式任务详情、仅靠颜色传达状态、用前端隐藏代替服务端权限控制。

## Product goals
- Goals: 按时间把人和事推到位；确保任务不遗失；让成员每天快速完成任务更新；让管理者及时发现停滞、逾期和责任缺口。
- Non-goals: 通用流程引擎、完整即时通信、复杂问题工单系统、独立 SPA 重写。
- Success signals: 成员可在一个页面找到当天全部责任事项；一次任务更新不超过两次页面跳转；管理者能按项目、人员、时间和风险定位问题。

## Personas and jobs
- Primary personas: 普通成员、任务负责人、目标负责人/验收人、项目负责人/项目成员、管理员。
- User jobs: 成员查看并更新自己的任务；目标负责人推动分支并申请闭环；验收人处理待合并分支；项目人员监管授权项目；管理员查看全局图谱并维护项目和人员权限。
- Key contexts of use: 每日任务更新、项目例会、截止日期检查、周度进度复盘。

## Information architecture
- Primary navigation: 我的工作、项目空间、任务树、团队态势、日历；“任务树”与日历同级，用于管理者跨项目查看分支闭环；“执行进度 / 风险与卡点 / 人员负载”作为团队态势的明确子项；系统管理仅管理员可见。
- Core routes/screens: `/my-work`, `/`, `/task-tree`, `/project/<id>`, `/task/<id>`, `/progress`, `/blockers`, `/people`, `/calendar`, `/users`.
- Content hierarchy: 可见范围（我的/总体）→ main 主线提交点（项目起点 / 分叉提交 / 合并提交 / HEAD）→ 目标分支 → 任务子分支 → 任务合并回目标 → 目标合并回 main → 方案、进展和操作记录。

## Design principles
- 个人优先: 登录后的第一认知必须是“我今天要做什么”。
- 责任可见: 每个任务始终显示负责人、状态、截止时间和最近进展。
- 渐进披露: 高频动作前置，编辑字段、历史记录和管理操作按需展开。
- 多视角共享同一权限契约: 图谱、项目、人员、日期、风险可使用独立视图，但所有数据范围必须由服务端统一解析，不能各自形成可见性规则。
- 拓扑优先: 任务树必须像 Git 历史一样明确呈现“项目主线 → 目标分支 → 任务子分支”的三级拓扑。任务不能仅作为目标 lane 上的普通圆点；每个任务必须从目标 lane 以曲线开出自己的短分支，已完成任务再以曲线合并回目标 lane，已闭环目标再合并回项目主线。关系线只表达真实父子与闭环关系。
- Main 不得为空: main 必须是一条带提交点的可读历史，而不是纯装饰竖线。至少包含项目起点、每个目标分叉对应的 main 提交点、目标闭环产生的合并提交点，以及当前 `main / HEAD`；目标分支只能从明确的 main 提交点开出。目标合并曲线保留目标分支色以显示来源，但落在 main 上的合并提交节点必须使用 main 样式。
- 父节点同点分叉: 每条子分支必须从父 lane 上一个可见提交点的精确坐标出发。目标分支的起点与 main 分叉提交点重合；任务分支的起点与目标 lane 上的需求提交点重合。父 lane 保持稳定直线，只有分叉和合并转换使用平滑曲线，禁止为了“有曲线”让主线或父分支蛇形摆动。
- Goal 轨道复用: 当前图谱按 Goal 区块沿纵向依次展开，每个区块完成自己的分叉、并行任务、汇合和分支状态后才进入下一个区块；因此后续 Goal 必须复用同一条固定 Goal lane，不能按照 Goal 序号持续向右漂移。横向空间只用于当前 Goal 的并行 Task lane，不能把历史分支数量映射为永久增加的画布宽度。
- 兄弟任务默认并行: 当前模型没有任务依赖关系，同一目标下的 Task 必须视为兄弟并行分支，全部从同一个目标需求提交点扇出，并在下方同一个任务汇合点回收；不得根据列表顺序画成前后串行。`Task.order` 只决定视觉排列，不表达依赖。无目标任务同理，从同一个 main 任务需求点并行扇出。只有未来引入显式 dependency 后，才允许表现串行链路。
- 个人相关性优先: 非管理员默认进入“我的图谱”；总体视图保留完整授权上下文，并通过光环、标签和强调描边突出当前用户负责、参与、验收或提交的节点。
- 权限不泄漏: 不可见实体的直接访问返回 404；禁止写操作使用通用提示并回到保证可见的安全页面，页面内容、计数、筛选项和 DOM 属性都不能泄露隐藏名称。
- Tradeoffs: 优先桌面端高信息密度，同时保证窄屏可完成核心更新；保留 Flask/Jinja 和现有后端契约。

## Visual language
- Color: 中性蓝灰底色，蓝色作为主操作；分支 lane 颜色由分支标识确定性生成，跨刷新不变且不受固定色板数量限制。红/橙/绿分别表达逾期或阻塞、临近或等待、完成，并必须同时配合节点形状、描边样式、徽章或文字。当前用户相关节点使用独立光环，不改变分支本色。
- Typography: 系统中文字体栈；页面标题 28px/850，区块标题 17–18px/800，卡片标题与一级导航 14–16px/700–800，正文 14–15px，说明和元数据 12–13px；相邻层级必须同时通过字号、字重和间距区分，不能只靠颜色。
- Spacing/layout rhythm: 4px 基础单位；页面间距 24px；组件间距以 8/12/16/24px 为主。
- Shape/radius/elevation: 8–12px 圆角，边框优先于阴影，最多两级阴影；所有操作按钮必须有可辨识的 1px 边界，主按钮使用实色填充，次按钮使用表面底色与强调边框。
- Motion: 120–200ms，用于节点选择、详情切换和抽屉；不做持续流动或脉冲动画；尊重 reduced-motion。
- Imagery/iconography: 使用统一、克制的线性符号或文字图标，不用 emoji 作为主要信息载体。

## Components
- Existing components to reuse: Jinja 基础布局、CSRF 隐藏字段、Flash 消息、确认弹窗、任务卡片 partial、主/次级按钮及统一的“返回”按钮。
- New/changed components: 应用侧栏、分区标题、一级导航、移动端分组导航、页面标题栏、指标卡、状态徽章、风险提示、Git 图谱工具栏、范围切换器、图谱滚动视口、SVG 项目主线/目标 lane/任务子 lane/分叉曲线/任务回合并曲线/目标回主线曲线、HTML 节点检查器、无图形回退列表、分段导航、空状态、进展编辑器、统一描边操作按钮。
- Variants and states: default/hover/focus/selected/disabled；任务 pending/in_progress/waiting/completed；分支 active/merge_requested/merged，闭环结果可为 achieved/answered/cancelled/transferred；风险可为 overdue/due-soon/waiting/stale/unassigned/unreviewed/blocked。合并状态必须显示“进行中 / 待验收 / 已闭环”等文字，不能只改变线条颜色。
- Token/component ownership: CSS 变量和通用组件统一放在 `webapp/static/style.css`；页面模板只组合组件，避免新增内联样式。

## Accessibility
- Target standard: WCAG 2.1 AA 的实用基线。
- Keyboard/focus behavior: 所有交互可通过键盘操作；图谱节点进入顺序与视觉提交顺序一致，Enter/Space 选择节点并更新检查器，焦点与选中态清晰区分；弹窗具备焦点管理和 Escape 关闭。
- Contrast/readability: 正文与背景至少 4.5:1；状态不只依赖颜色。
- Screen-reader semantics: 使用语义化导航、标题、表格、按钮和 `aria-current`；SVG 根节点提供 title/desc，交互节点提供可访问名称、状态和 `aria-controls`；同时保留包含同等关键信息的 HTML 回退内容。
- Reduced motion and sensory considerations: 在 `prefers-reduced-motion` 下关闭非必要动画。

## Responsive behavior
- Supported breakpoints/devices: 1440px 桌面、1024px 小屏电脑、768px 平板、390px 手机。
- Layout adaptations: 桌面固定侧栏，侧栏导航始终是单列结构，工作台、项目协作、系统管理必须占满同一内容宽度，禁止因旧导航样式产生横向换列或横向滚动；图谱与检查器并列；中等宽度检查器移到图谱下方；窄屏改为可展开导航。图谱视口只接管横向滚动，鼠标滚轮和触控板纵向手势必须继续滚动整页，不能在图谱区域形成滚动陷阱；main 与 Goal 使用稳定、可复用的固定 lane，当前 Goal 的并行 Task 在右侧多列展开，画布宽度不得随历史 Goal 数量无界增长；同时显示可读的分支摘要回退；多列指标和表单依次折叠为单列；桌面日历始终保持七列等宽，日程内容只能在列内截断，不能改变列宽。
- Touch/hover differences: 触控目标至少 40px；关键操作不能只在 hover 出现。

## Interaction states
- Loading: 按钮内显示进行中状态，AI 或长请求显示明确耗时提示。
- Empty: 解释为什么为空，并给出权限允许的下一步动作。
- Error: Flash 或字段级提示保留用户输入。
- Success: 简短确认，并停留在用户下一步最可能需要的页面。
- Disabled: 说明缺少权限或前置条件。
- Offline/slow network: 表单提交后防止重复点击；普通页面保持服务端渲染降级能力。
- Branch closure: 单个任务完成后，其任务子分支在图谱上明确回合并到所属目标 lane；目标下全部任务完成仅解锁“申请闭环”。目标负责人提交实际结果后，由目标验收人、项目总负责人或项目管理人员明确通过或驳回；只有验收通过的目标分支才回合并到项目主线。
- Graph scope: 管理员默认“总体”；其他角色默认“我的”。只有具备授权总体范围的用户显示总体切换；越权请求由服务端降级到“我的”，而不是先渲染再隐藏。
- Graph selection: 初始选中最需要处理且与当前用户相关的节点；若无个人相关节点则选中最新可见节点。选择节点只更新详情，不改变权限范围。

## Content voice
- Tone: 简短、明确、面向动作。
- Terminology: “目标标签”统一显示为“目标分支”；“我的工作台”简称“我的工作”；“卡点”统一为“风险与卡点”。
- Microcopy rules: 按钮使用动词；跨页面返回入口统一写成“← 返回 + 目标页面”，并使用次级按钮样式，不能表现为无边界文本；风险说明包含原因和下一步；避免“还剩 0 天”等机械表达，改为“今天到期”。

## Implementation constraints
- Framework/styling system: Flask + Jinja + 原生 CSS/JavaScript，不新增前端框架或依赖。
- Design-token constraints: 明暗主题继续使用 CSS 自定义属性；页面不直接写颜色值。
- Performance constraints: 不增加阻塞型外部资源；首屏不依赖 JavaScript 才能展示核心内容。
- Compatibility constraints: 保持现有 URL、表单字段、CSRF 和数据库模型兼容；将“可查看”与“可修改”权限明确分离，UI 重构不得暴露无权限数据或操作。
- Test/screenshot expectations: Flask 测试客户端覆盖管理员、项目负责人、目标负责人、验收人、任务负责人、成员和无关用户；所有关键 GET/POST 均包含允许与拒绝案例，并验证隐藏资源与不存在资源的写操作响应不可区分；检查所有 POST 表单包含 CSRF；图谱测试必须证明 main 包含起点、分叉提交点、合并提交点和 HEAD，项目、目标、任务形成三级坐标层级，同一父节点的兄弟任务 fork 起点完全一致、落在不同并行 lane，并在共享汇合点回收；父 lane 保持稳定直线，任务完成和目标闭环使用贝塞尔曲线回合并；使用 8 分支/35 任务样例保存桌面、窄屏截图与 DOM 快照，并使用超过 8 分支的自动化案例验证 Goal lane 会被复用、分支颜色仍保持唯一稳定、画布宽度不会随 Goal 数量无界增长。

## Open questions
- [ ] 是否将 `/my-work` 最终设为所有角色登录后的默认首页；本轮先让导航和品牌入口优先指向“我的工作”。
- [ ] 报告模板和模型缺少可用路由；本轮不加入主导航。
- [ ] 后续是否需要在超大项目中加入 Goal 区块折叠或时间窗口；本轮通过固定 Goal lane 复用控制横向宽度，仍需通过真实超大数据评估纵向历史何时需要折叠。
