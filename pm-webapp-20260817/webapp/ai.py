"""AI 集成模块 — DeepSeek via New-API 中转（OpenAI 兼容，无 streaming）

配置（webapp/.env，os.getenv 读取）：
  DEEPSEEK_API_URL  默认 http://192.168.77.162:3001/v1/chat/completions
  DEEPSEEK_API_KEY  中转 token
  DEEPSEEK_MODEL    默认 deepseek-v4-flash
未配置 API_KEY 时，所有函数抛 AIUnavailable，调用方优雅降级（页面提示"AI 未配置"）。
"""
import json
import os
import re

import requests

DEEPSEEK_API_URL = os.getenv(
    'DEEPSEEK_API_URL',
    'http://192.168.77.162:3001/v1/chat/completions'
)
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-v4-flash')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')

TIMEOUT = 90  # 秒（deepseek 为推理模型，先思考后作答，单次生成可能 20~60s）


class AIUnavailable(Exception):
    """AI 未配置或调用失败，调用方捕获后给出可读提示。"""


def is_available():
    return bool(DEEPSEEK_API_KEY)


def call_deepseek(system_prompt, user_message, max_tokens=2048, _attempt=0):
    """调用 DeepSeek（OpenAI 兼容格式），返回 assistant 文本。失败抛 AIUnavailable。"""
    if not DEEPSEEK_API_KEY:
        raise AIUnavailable('AI 未配置：请在 webapp/.env 设置 DEEPSEEK_API_KEY')

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            },
            json={
                'model': DEEPSEEK_MODEL,
                'max_tokens': max_tokens,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_message},
                ],
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        # 网络抖动：重试一次
        if _attempt < 1:
            return call_deepseek(system_prompt, user_message, max_tokens, _attempt + 1)
        raise AIUnavailable(f'AI 请求失败：{e}')

    if resp.status_code != 200:
        if _attempt < 1:
            return call_deepseek(system_prompt, user_message, max_tokens, _attempt + 1)
        raise AIUnavailable(
            f'AI 调用失败 ({resp.status_code}): {resp.text[:200]}')

    try:
        data = resp.json()
        content = data['choices'][0]['message']['content']
    except (ValueError, KeyError, IndexError, TypeError):
        raise AIUnavailable(f'AI 返回格式异常: {resp.text[:200]}')

    # 中转偶发返回空内容（抖动），最多重试 2 次；仍空则报错
    if not content or not str(content).strip():
        if _attempt < 2:
            return call_deepseek(system_prompt, user_message, max_tokens, _attempt + 1)
        raise AIUnavailable('AI 返回空内容（中转抖动，请重试）')
    return content


def extract_json(text):
    """从模型输出提取 JSON：优先 markdown 代码块，其次裸 JSON。解析失败返回 None。"""
    if not text:
        return None
    m = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', text)
    if not m:
        m = re.search(r'(\{[\s\S]*\})', text)
    json_str = m.group(1) if m else text
    try:
        return json.loads(json_str.strip())
    except (ValueError, TypeError):
        return None


# ============================================================
# 时间段工作总结
# ============================================================

SUMMARY_SYSTEM_PROMPT = """你是一个研发团队的项目协作助手。根据系统提供的时间段内**真实的工作统计数据**，撰写一份结构化的中文工作总结报告。

## 输出格式（严格的 JSON）
{
  "overall": "80-150字的总体总结：这段时间团队整体推进情况、主要进展、节奏判断",
  "highlights": ["亮点1", "亮点2", "亮点3（最多5条，每条不超过30字）"],
  "projects": [{"name": "项目名", "summary": "该项目这段时间的主要进展"}],
  "members": [{"name": "成员姓名", "summary": "该成员这段时间的工作总结，50-100字"}]
}

## 项目 summary 写法（重要）
每个项目的 summary 要讲一个**完整的故事**，按以下结构写：
1. **先介绍项目目标与框架**：一句话说明这个项目要做什么，尽量点出它的子目标（目标框架），让读者先知道项目全貌；
2. **再讲这段时间具体做了什么**：结合【时间段内完成/关闭的事项】里属于该项目的事项展开，具体到事项名称，把关键进展按推进逻辑串起来；
3. **点出解决的关键问题**：若该项目这段时间解决过问题，说明解决了什么、对项目的意义。

- 重点项目（完成任务+解决问题多，或为当前主线）：120-200 字，展开充分。
- 一般项目：60-100 字，简述但要保留「目标 → 进展」的完整结构。
- 严禁平均分配字数——重要项目必须明显比小项目写得更详细、更长。

## 重要规则
1. **只使用系统提供的数据，严禁编造**任何没有出现在给定数据里的事项、数字、人名。
2. summary 中的统计数字（完成任务数、解决问题数等）必须与给定数据一致，不得夸大。
3. 语气客观、专业、不夸张；亮点要有依据。
4. 若某成员数据很少，summary 如实简短说明即可，不要硬凑字数。
5. **不要输出任何风险、问题、待关注、隐患、逾期等负面提示，也不要输出 risks 字段。**
6. 只输出 JSON，不要输出其他任何文字。"""


def build_summary_prompt(date_from, date_to, aggregated):
    """把服务端聚合的真实数据格式化为 user message。"""
    lines = [
        f'时间段: {date_from} 至 {date_to}',
        f'活跃人数: {aggregated.get("active_count", 0)}',
        '',
        '【按成员统计】',
    ]
    members = aggregated.get('members', [])
    if not members:
        lines.append('（无）')
    for m in members:
        lines.append(
            f'- {m["name"]}: 新任务{m["new_tasks"]}, 完成任务{m["completed_tasks"]}, '
            f'操作{m["actions"]}, 涉及项目: {", ".join(m["projects"]) or "无"}'
        )
    lines.append('')
    lines.append('【按项目统计】')
    projects = aggregated.get('projects', [])
    if not projects:
        lines.append('（无）')
    for p in projects:
        goal_str = '、'.join(g['title'] for g in p.get('goals', []))
        lines.append(
            f'- {p["name"]}: '
            + (f'项目目标「{p["description"]}」；' if p.get('description') else '')
            + (f'目标框架: {goal_str}；' if goal_str else '')
            + f'完成任务{p["completed_tasks"]}'
        )
    lines.append('')
    lines.append('【时间段内完成/关闭的事项】')
    done_items = aggregated.get('done_items', [])
    if not done_items:
        lines.append('（无）')
    for it in done_items:
        lines.append(
            f'- [{it["date"]}] {it["type"]}「{it["title"]}」负责人: {it["person"]} 项目: {it["project"]}'
        )
    lines.append('')
    lines.append('请据此生成总结报告 JSON。')
    return '\n'.join(lines)


def generate_summary(date_from, date_to, aggregated, max_tokens=16000):
    """生成时间段总结报告，返回解析后的 JSON dict。

    max_tokens 需足够大：deepseek 推理模型的思考(reasoning_content)也计入
    max_tokens，过小会导致思考耗尽额度、content 为空。
    """
    text = call_deepseek(
        SUMMARY_SYSTEM_PROMPT,
        build_summary_prompt(date_from, date_to, aggregated),
        max_tokens=max_tokens,
    )
    data = extract_json(text)
    if data is None:
        raise AIUnavailable('AI 返回内容无法解析为 JSON')
    # 兜底：保证结构字段存在；risks 按规则强制清空（不提风险）
    data.setdefault('overall', '')
    data.setdefault('highlights', [])
    data['risks'] = []
    data.setdefault('projects', [])
    data.setdefault('members', [])
    return data


