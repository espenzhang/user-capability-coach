# User Capability Coach

> 本地运行、默认关闭、隐私优先的提问能力辅导 skill —— 帮助用户把 prompt 写得更清楚，但永远不打断任务。

[English](README.md)

`user-capability-coach` 是一个两 skill 组合（`prompt-coach` + `growth-coach`），在用户**显式开启后**以极克制、可忽略、绝不评判的方式，帮用户提升提问能力。

完全本地运行，不调任何外部网络，不存任何原始 prompt。用户没执行 `coach enable` 之前，0 输出、0 写入。

## 核心能力

- **单轮即时辅导**：当前这条 prompt 有明显可修复的缺口时（缺输出格式 / 多阶段捆绑 / 目标未明说 / ……），在答案**之后**附一句简短提示——永远不阻塞任务。
- **会话内短期模式**：识别单会话内的重复模式（例如"最近 5 轮里 3 次都没说目标"），发出一条会话级提醒，每个会话每个 issue 最多一次。
- **跨会话长期模式**：在证据充足时积累跨会话模式（≥4 次观察 / ≥3 个独立会话 / ≥2 次造成成本），开启长期记忆后有 14 天观察期，同一模式触发后有 14 天冷却期。
- **完整用户控制**：`coach dismiss` 7 天软静默、`coach disable` 硬关停、`coach forget-pattern` / `coach forget-all` 删数据、`coach why-reminded` 查完整证据链。

## 设计原则

1. **默认 `off`**。安装后无可见输出、无磁盘写入，直到用户显式 `coach enable`。
2. **先完成任务，再顺手教学**。总是先给答案，之后（可选）附一句可忽略的建议。绝不为了澄清阻塞任务。
3. **每回合最多 1 条可见辅导**。policy 层硬不变量。
4. **agent 优先判断**。带完整对话上下文的 agent 对每条 prompt 做分类，把判断传给 coach；规则层只是 fallback。
5. **隐私在设计层保障**。磁盘上永远不写原始 prompt——只存系统生成的摘要。敏感域 prompt 只留 content-free 记录（`shadow_only=1`）。长期记忆默认关闭。
6. **一切可审计**。每条长期复盘提醒都带完整 `explanation_chain`（证据数 / 独立会话数 / 成本次数 / 冷却状态 / 允许原因）。如果证据链无法构造，提醒就不发。

## 架构一览

```
┌─────────────────────────────────────────────────────────┐
│  Agent (Claude Code / Codex) — 带完整对话上下文          │
│  对每条 prompt 做分类、传给 coach                        │
│  ├─ prompt-coach    — 单轮处理                            │
│  └─ growth-coach    — 会话 + 长期模式                    │
└────────────────┬────────────────────────────────────────┘
                 │ stdin JSON (text, session_id, agent_classification)
                 ▼
┌─────────────────────────────────────────────────────────┐
│  Coach CLI (Python 本地 subprocess, 单次调用 <60ms)       │
│  ├─ detectors.py    — 规则 fallback（agent 没传分类时）  │
│  ├─ policy.py       — 模式 / 预算 / 冷却 / 静默窗口      │
│  └─ templates.py    — 中英双语辅导文案                    │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│  本地 SQLite                                             │
│  ├─ observations      — 跨会话证据                        │
│  ├─ patterns          — 长期聚合                          │
│  ├─ session_turns     — 会话内短期                        │
│  ├─ intervention_events — 每次提醒的完整审计链            │
│  └─ preferences / session_nudge_log / schema_meta        │
└─────────────────────────────────────────────────────────┘
```

## 8 类 prompt 问题

所有 8 类都是用户可见的（`coach enable` 之后）。7 类有规则 fallback，`missing_constraints` 纯 agent-driven。

| 类型 | 捕获什么 | 规则 |
|---|---|---|
| `missing_output_contract` | 未指定输出格式/结构 | ✅ 强 |
| `overloaded_request` | 3+ 阶段捆绑在一句话里 | ✅ 强 |
| `missing_goal` | 无明确意图动词或目标 | ✅ 强 |
| `unbound_reference` | 代词开头 + 问题词（"It's broken."）| ✅ 强 |
| `missing_context` | 引用了"这段代码"但未附内容 | ✅ 强 |
| `missing_success_criteria` | 改进动词但无可验证指标 | ⚠️ 弱 |
| `conflicting_instructions` | 显式冲突词（"更短但要详细"）| ⚠️ 弱 |
| `missing_constraints` | 有目标但没边界（长度/受众/质量）| 纯 agent |

## 安装

**Claude Code（用户级）：**
```bash
cd packages/user-capability-coach
bash adapters/claude-code/install.sh
```

会做：
1. 把 `prompt-coach/` 和 `growth-coach/` 复制到 `~/.claude/skills/`
2. 在 `~/.claude/user-capability-coach/` 创建本地数据目录
3. 配置 `coach` CLI 包装脚本
4. 在 `~/.claude/CLAUDE.md` 末尾追加简短片段（带 `<!-- user-capability-coach:start/end -->` 标记，卸载时精确移除）

**Codex / AGENTS.md 项目：**
```bash
export COACH_PROJECT_ROOT=/path/to/your/project
bash adapters/codex/install.sh
```

**安装后辅导是关闭的。** 开启：
```
/coach on          # Claude Code 中
# 或
coach enable       # 任何 shell
```

## 快速上手

```bash
# 开启辅导（默认 light 模式）
coach enable

# 同时开启长期记忆（启动 14 天观察期）
coach set-memory on

# 升级到 standard 模式（允许对高严重度 prompt 做 pre-answer nudge）
coach set-mode standard

# 查看状态
coach status

# 查看累积的长期模式
coach show-patterns

# 临时静默 7 天（不完全关闭）
coach dismiss

# 完全关闭
coach disable

# 问"刚才为什么提醒我"
coach why-reminded

# 删除某条具体模式
coach forget-pattern missing_output_contract [coding]

# 删除全部数据
coach forget-all

# 数据导出 / 导入（跨机器迁移）
coach memory export > backup.jsonl
coach memory import < backup.jsonl
```

## Agent 调用协议

每个用户回合，agent：

1. **带完整上下文对 prompt 做分类**，然后调 `coach select-action`，在 JSON 里带上分类：
   ```json
   {
     "text": "<当前用户消息>",
     "session_id": "<稳定的会话 ID>",
     "agent_classification": {
       "issue_type": "missing_output_contract",
       "confidence": 0.85,
       "severity": 0.75,
       "fixability": 0.9,
       "cost_signal": "output_format_mismatch",
       "domain": "coding",
       "is_sensitive": false,
       "is_urgent": false,
       "evidence_summary": "系统生成的描述，不是用户原话"
     }
   }
   ```
2. **根据返回的 `action` 渲染辅导**：
   - `none` / `silent_rewrite` → 正常回答
   - `post_answer_tip` → 先回答，末尾附 `💡 <coaching_text>`
   - `pre_answer_micro_nudge` → 开头附 `⚡ <coaching_text>`，再回答
   - `retrospective_reminder` / `session_pattern_nudge` → 交给 growth-coach，末尾附 `📊 <coaching_text>`
3. **记录本轮**：调 `coach record-observation`，传入同样的分类。

没传 `agent_classification` 的 agent 会降级到规则识别 —— 仍然可用，但看不到上下文。

## 隐私

硬规则（代码 + 测试双重守护）：

1. **用户没执行 `coach enable` 前，磁盘上什么都不写。**
2. **原始 prompt 文本永远不落盘。** 只存系统生成的 `evidence_summary`、结构化字段（issue_type / domain / 时间戳 / 计数）和模式得分。
3. **长期记忆默认关闭。** 开启后启动 14 天观察期，期间只记录不提醒 —— 系统只在学习。
4. **敏感域观察默认剥离内容。** `domain=sensitive` 命中时无论调用方传了什么，都强制写入 `issue_type=null, evidence_summary="", shadow_only=1`。显式 `coach set-sensitive-logging on` 才能 opt in。
5. **删除立即生效。** `forget-pattern` 清 patterns + observations + 相关 intervention_events。`forget-all` 清空三张表。
6. **数据只在本地。** `~/.claude/user-capability-coach/coach.db` (Claude Code) 或 `~/.local/share/user-capability-coach/coach.db` (Codex) 或 `~/Library/Application Support/...` (macOS Codex)。无网络调用，无云同步。

## 提醒安全护栏

长期复盘提醒**只在**下面全部为真时才触发：

- `mode != off` 且 `memory_enabled = true`
- 14 天观察期已结束
- 模式有 ≥4 次观察，覆盖 ≥3 个独立会话
- 其中至少 2 次造成了可观察的成本
- 上次就同一模式提醒已过 ≥14 天
- 本周复盘预算未耗尽（每周 1 次）
- 当前上下文既非敏感也非紧急
- 用户没有在 7 天静默窗口内
- `build_explanation_chain()` 能返回完整证据链

任一不满足 → 静默抑制，永不放宽。

## 开发

```bash
cd packages/user-capability-coach

# 跑所有测试
python3 -m pytest tests/ -v

# 跑基于 fixture 的评测
python3 tests/eval_fixtures.py

# 详细模式（查看失败分析）
python3 tests/eval_fixtures.py --verbose
```

当前状态：
- **247 个单测 + 集成测试** 全部通过
- **v1 检测器**：在 57 条 weak fixture 上 100% 命中
- **好 prompt 误报率**：在 25 条 good fixture 上 0%
- **敏感域抑制率**：在 18 条 sensitive fixture 上 100%

## 多平台支持

核心层（taxonomy / detectors / policy / memory / templates / CLI）是平台无关的 Python。每个平台有一个薄 adapter：

- `adapters/claude-code/` — 把 skill 复制到 `~/.claude/skills/`，在 `~/.claude/CLAUDE.md` 追加片段
- `adapters/codex/` — 把 skill 复制到 `<project>/.agents/skills/`，在 `AGENTS.md` 追加片段

新增宿主（Cursor / Windsurf / Continue / ……）只需加一个 adapter 目录。核心层不动。

## 文档

- [`docs/user-capability-coach.md`](packages/user-capability-coach/docs/user-capability-coach.md) —— 开发者指南、架构、阈值、如何新增 issue 类型
- [`docs/user-capability-memory-controls.md`](packages/user-capability-coach/docs/user-capability-memory-controls.md) —— 面向用户的记忆控制和删数据说明
- [`docs/multi-platform-install.md`](packages/user-capability-coach/docs/multi-platform-install.md) —— 各平台的安装 / 卸载说明

## License

MIT
