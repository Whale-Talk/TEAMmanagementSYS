# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-08-20
- Primary product surfaces: 我的工作、项目空间、项目详情、任务详情、团队态势、日历、系统管理
- Evidence reviewed: `webapp/app.py`, `webapp/models.py`, `webapp/templates/`, `webapp/static/style.css`, and the 2026-08 product discussion transcript supplied by the user.

## Brand
- Personality: 冷静、可靠、直接、行动导向。
- Trust signals: 明确负责人、截止时间、当前状态、最近进展和下一步动作。
- Avoid: 装饰性 emoji 堆叠、复杂拓扑连线、同级入口过多、后台表单式任务详情、仅靠颜色传达状态。

## Product goals
- Goals: 按时间把人和事推到位；确保任务不遗失；让成员每天快速完成任务更新；让管理者及时发现停滞、逾期和责任缺口。
- Non-goals: 通用流程引擎、完整即时通信、复杂问题工单系统、独立 SPA 重写。
- Success signals: 成员可在一个页面找到当天全部责任事项；一次任务更新不超过两次页面跳转；管理者能按项目、人员、时间和风险定位问题。

## Personas and jobs
- Primary personas: 普通成员、项目人员、管理员。
- User jobs: 成员查看并更新自己的任务；项目人员监管所属项目；管理员维护全局项目和人员权限。
- Key contexts of use: 每日任务更新、项目例会、截止日期检查、周度进度复盘。

## Information architecture
- Primary navigation: 我的工作、项目空间、团队态势、日历；“执行进度 / 风险与卡点 / 人员负载”作为团队态势的明确子项；系统管理仅管理员可见。
- Core routes/screens: `/my-work`, `/`, `/project/<id>`, `/task/<id>`, `/progress`, `/blockers`, `/people`, `/calendar`, `/users`.
- Content hierarchy: 项目主线 → 目标分支 → 任务节点 → 方案/进展/操作记录。

## Design principles
- 个人优先: 登录后的第一认知必须是“我今天要做什么”。
- 责任可见: 每个任务始终显示负责人、状态、截止时间和最近进展。
- 渐进披露: 高频动作前置，编辑字段、历史记录和管理操作按需展开。
- 多视角而非一张大图: 项目、人员、日期、风险使用独立视图，但归入清晰的导航分组。
- 结构有层级、视觉不绕线: 用缩进、节点、竖线和分组表达树形关系，不绘制复杂回环拓扑。
- Tradeoffs: 优先桌面端高信息密度，同时保证窄屏可完成核心更新；保留 Flask/Jinja 和现有后端契约。

## Visual language
- Color: 中性蓝灰底色，蓝色作为主操作；红/橙/绿分别表达风险、临近和完成，并配合文字或图标。
- Typography: 系统中文字体栈；页面标题 28px/850，区块标题 17–18px/800，卡片标题与一级导航 14–16px/700–800，正文 14–15px，说明和元数据 12–13px；相邻层级必须同时通过字号、字重和间距区分，不能只靠颜色。
- Spacing/layout rhythm: 4px 基础单位；页面间距 24px；组件间距以 8/12/16/24px 为主。
- Shape/radius/elevation: 8–12px 圆角，边框优先于阴影，最多两级阴影；所有操作按钮必须有可辨识的 1px 边界，主按钮使用实色填充，次按钮使用表面底色与强调边框。
- Motion: 120–200ms，用于展开、悬停和抽屉；尊重 reduced-motion。
- Imagery/iconography: 使用统一、克制的线性符号或文字图标，不用 emoji 作为主要信息载体。

## Components
- Existing components to reuse: Jinja 基础布局、CSRF 隐藏字段、Flash 消息、确认弹窗、任务卡片 partial、主/次级按钮及统一的“返回”按钮。
- New/changed components: 应用侧栏、分区标题、一级导航、带引导线的二级导航、移动端分组导航、页面标题栏、指标卡、状态徽章、风险提示、任务行、分支树、分段导航、空状态、详情侧栏、进展编辑器、等宽日历轨道与可截断日程条、统一描边操作按钮。
- Variants and states: default/hover/focus/active/disabled；pending/in_progress/completed；overdue/due-soon/stale/unassigned/unreviewed。
- Token/component ownership: CSS 变量和通用组件统一放在 `webapp/static/style.css`；页面模板只组合组件，避免新增内联样式。

## Accessibility
- Target standard: WCAG 2.1 AA 的实用基线。
- Keyboard/focus behavior: 所有交互可通过键盘操作；弹窗具备焦点管理和 Escape 关闭。
- Contrast/readability: 正文与背景至少 4.5:1；状态不只依赖颜色。
- Screen-reader semantics: 使用语义化导航、标题、表格、按钮和 `aria-current`。
- Reduced motion and sensory considerations: 在 `prefers-reduced-motion` 下关闭非必要动画。

## Responsive behavior
- Supported breakpoints/devices: 1440px 桌面、1024px 小屏电脑、768px 平板、390px 手机。
- Layout adaptations: 桌面固定侧栏；窄屏改为可展开导航；多列指标和表单依次折叠为单列；桌面日历始终保持七列等宽，日程内容只能在列内截断，不能改变列宽。
- Touch/hover differences: 触控目标至少 40px；关键操作不能只在 hover 出现。

## Interaction states
- Loading: 按钮内显示进行中状态，AI 或长请求显示明确耗时提示。
- Empty: 解释为什么为空，并给出权限允许的下一步动作。
- Error: Flash 或字段级提示保留用户输入。
- Success: 简短确认，并停留在用户下一步最可能需要的页面。
- Disabled: 说明缺少权限或前置条件。
- Offline/slow network: 表单提交后防止重复点击；普通页面保持服务端渲染降级能力。

## Content voice
- Tone: 简短、明确、面向动作。
- Terminology: “目标标签”统一显示为“目标分支”；“我的工作台”简称“我的工作”；“卡点”统一为“风险与卡点”。
- Microcopy rules: 按钮使用动词；跨页面返回入口统一写成“← 返回 + 目标页面”，并使用次级按钮样式，不能表现为无边界文本；风险说明包含原因和下一步；避免“还剩 0 天”等机械表达，改为“今天到期”。

## Implementation constraints
- Framework/styling system: Flask + Jinja + 原生 CSS/JavaScript，不新增前端框架或依赖。
- Design-token constraints: 明暗主题继续使用 CSS 自定义属性；页面不直接写颜色值。
- Performance constraints: 不增加阻塞型外部资源；首屏不依赖 JavaScript 才能展示核心内容。
- Compatibility constraints: 保持现有 URL、表单字段、CSRF、权限判断和数据库模型兼容；UI 重构不得暴露无权限操作。
- Test/screenshot expectations: Flask 测试客户端覆盖主要 GET 页面；检查所有 POST 表单包含 CSRF；桌面和窄屏进行视觉烟测。

## Open questions
- [ ] 是否将 `/my-work` 最终设为所有角色登录后的默认首页；本轮先让导航和品牌入口优先指向“我的工作”。
- [ ] “验收人”未来是否扩展为提交验收/通过/驳回流程；本轮仅优化展示，不新增状态。
- [ ] 报告模板和模型缺少可用路由；本轮不加入主导航。
- [ ] 项目负责人目前不自动获得项目管理权限；本轮按现有权限契约显示操作。
