# Prompt Coach Examples

## missing_output_contract

### Should trigger (no output format)

| Prompt | Why |
|--------|-----|
| "帮我写一个接口文档" | No format: markdown? OpenAPI? table? |
| "Generate release notes" | No format: bullets? prose? markdown? |
| "请帮我生成文档" | 文档 is too generic |
| "Write a summary of this meeting" | No format specified |
| "给我一个 Python 函数" | No docstring/type hints/return spec |

### Should NOT trigger (has contract)

| Prompt | Why |
|--------|-----|
| "把这段话翻译成英文" | Translation → target language = contract |
| "Convert this to JSON" | Explicit format |
| "Give a one-line definition" | Length = implicit contract |
| "Rewrite this as bullet points" | "bullet points" = format |
| "Show me a SQL query for this" | SQL query = output type |

---

## overloaded_request

### Should trigger (multiple phases)

| Prompt | Why |
|--------|-----|
| "分析竞品、制定战略、设计产品、排期、招人、融资" | 6 distinct phases |
| "Write the spec, implement it, add tests, and deploy" | 4 verb phases |
| "Research the topic, write an outline, draft the article, edit it" | 4 phases |

### Should NOT trigger (cohesive single task)

| Prompt | Why |
|--------|-----|
| "Fix the bug and add a regression test" | Natural pairing, same domain |
| "Write a function with proper error handling" | Single task with implied parts |
| "Refactor and update tests" | Strongly coupled pair |

---

## missing_goal

### Should trigger (content without outcome)

| Prompt | Why |
|--------|-----|
| "看看这个" | No goal: review? summarize? rewrite? |
| "帮我处理一下" | "处理" is vague — process how? |
| "请分析" (bare verb, no object) | No target, no outcome |
| "What do I do about this?" (no context) | Unknown issue, unknown goal |

### Should NOT trigger (clear goal implicit)

| Prompt | Why |
|--------|-----|
| "Summarize this article" | "summarize" = clear goal |
| "帮我把这段代码重构一下" | "重构" = clear goal |
| "What's wrong with this function?" | Question = review goal |
| "Review my PR" | "review" = clear goal |

---

## Coaching text examples

### post_answer_tip (light mode, Chinese)

```
下次加一句输出格式说明会让结果更直接可用，比如："输出 JSON""用 markdown 表格""给编号列表"。
```

### post_answer_tip (light mode, English)

```
Next time, add one line specifying the format you want — e.g. 'output as JSON' or 'use a markdown table' — and I'll give you something ready to use.
```

### pre_answer_micro_nudge (standard mode, Chinese)

```
我先继续，但还缺一个关键信息：你希望这份文档以什么格式呈现？如果你不补，我会按 markdown + 编号章节处理。
```

### retrospective_reminder (Chinese)

```
最近几次文档/报告任务里，我经常需要猜你的输出格式。以后固定在请求末尾加一行"输出 X"，通常能让我首轮更准。
```
