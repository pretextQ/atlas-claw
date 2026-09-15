# AtlasClaw Hook Script Runtime Design

## Goal

在现有通用 Hook Runtime 之上，补齐两项关键能力：

1. 一个对齐 OpenClaw 使用体验的聚合上下文事件 `run.context_ready`
2. 一个可配置的本地命令型 Script Hook Handler，能够消费事件并通过结构化输出驱动通用动作

本设计保持 core 通用，不内置 `self-improving` 等特定 skill 语义。

## Scope Positioning

这是一份**增量设计**，不是对 Hook Runtime 的重做。

前置条件：
- 通用 Hook Runtime 已经完成
- 通用事件、`HookStateStore`、`MemorySink`、`ContextSink`、通用 Hook API 已经存在

本 spec 只覆盖在现有 Hook Runtime 之上新增的能力：
- `run.context_ready`
- 本地命令型 Script Hook Handler
- 结构化输出动作协议

## Status

- 设计状态：已确认，可进入 implementation plan
- 范围：聚合事件、脚本型 hook handler、结构化动作协议、测试与文档
- 非目标：完整插件自动发现、沙箱执行器、skill 专属 DSL

## Problem Statement

当前 AtlasClaw 的 Hook Runtime 已经具备：

- 通用生命周期事件
- per-user / per-module hook state store
- `MemorySink`
- `ContextSink`
- 最小的通用内建 handler 与通用 API

但距离 OpenClaw 风格的 hooks / plugins 使用体验，还有两个明显缺口：

1. **消费上下文成本高**
   - 目前需要分别订阅 `message.received`、`llm.requested`、`llm.completed`
   - Hook consumer 需要用 `run_id + session_key` 自己拼出完整上下文
   - 对脚本或轻量集成不友好

2. **还不能直接执行本地命令型 hook**
   - 目前只能注册 Python async handler
   - 不能像 OpenClaw hooks 一样，通过配置挂接一个本地命令来消费事件
   - 也就无法让外部 skill 或自动化模块通过脚本快速接入

因此，这一轮不是重做 Hook Runtime，而是在其上补齐：

- 面向 consumer 的聚合上下文事件
- 面向执行器的本地命令型脚本 hook 能力

## Design Principles

1. Core 继续保持通用，不引入 skill 特定事件
2. 保留现有细粒度事件，新增聚合事件而不是替代旧事件
3. Script Hook 只执行本地可执行命令，不做多种执行后端
4. Script Hook 与 Python handler 共享同一事件与状态体系
5. 脚本输出必须是结构化 JSON，由 Runtime 执行有限的通用动作
6. 默认安全：显式启用、有限动作、有限上下文、有限超时

## OpenClaw Alignment

这版能力对齐 OpenClaw 的点在于：

- `Hooks` 可以消费运行时事件
- Hook consumer 不必直接嵌在 core 代码中
- 运行时可以把事件上下文交给外部脚本/模块处理
- 外部处理结果通过通用动作协议回流到 memory / context / pending 流程

AtlasClaw 的映射：

- `HookRuntime`：对齐 OpenClaw hook runtime
- `run.context_ready`：对齐 consumer 友好的聚合上下文事件
- `ScriptHookHandler`：对齐本地命令型 hook consumer
- `MemorySink` / `ContextSink`：对齐 memory / context engine 的最小通用接口

## Approved Direction

本轮已确认：

- 使用“本地可执行命令”作为脚本 hook 执行方式
- Hook Runtime 要接收脚本的结构化输出
- 输出动作只做通用能力，不做 `self-improving` 特例

## High-Level Architecture

在现有 Hook Runtime 上新增四层：

1. **Aggregated Context Event**
- 新增 `run.context_ready`
- 给 consumer 一次性提供完成一轮推理所需的核心上下文

2. **Script Handler Definition**
- 新增一种可注册的脚本 handler 定义
- 由配置驱动，而不是硬编码在某个 skill 里

3. **Script Runner**
- 执行本地命令
- 通过 `stdin` 传入事件
- 解析 `stdout` 结构化 JSON

4. **Action Applier**
- 解析脚本输出动作
- 通过已有 `HookStateStore`、`MemorySink`、`ContextSink` 执行副作用

## Event Model

### Existing Events Remain

保留现有通用事件：

- `run.started`
- `run.completed`
- `run.failed`
- `message.received`
- `message.user_corrected`
- `llm.requested`
- `llm.completed`
- `llm.failed`
- `tool.started`
- `tool.completed`
- `tool.failed`
- `memory.confirmed`
- `memory.rejected`

### New Aggregated Event

新增：

- `run.context_ready`

### Trigger Timing

`run.context_ready` 在以下时机触发：

1. **run 成功完成后**
   - 在最终 assistant 回复确定后发出
   - 用于消费一次完整对话轮次

2. **run 失败时**
   - 以失败态发出同名上下文事件
   - 供脚本仍然获得用户输入、历史上下文和错误信息

### `run.context_ready` Payload

建议 payload 固定包含：

- `user_message`
- `message_history`
- `system_prompt`
- `assistant_message`
- `tool_calls`
- `run_status`
- `error`
- `session_title`

字段说明：

- `message_history`
  - 采用已经归一化后的 AtlasClaw 消息格式
  - 用于脚本理解本轮前的历史上下文
- `assistant_message`
  - 成功时为最终 assistant 回复
  - 失败时可为空
- `tool_calls`
  - 本轮工具调用摘要，不要求完整低层 trace
- `run_status`
  - `completed` 或 `failed`
- `error`
  - 失败时携带错误信息，否则为空

### Why a New Event Instead of Reusing Existing Ones

因为 `llm.requested` 和 `llm.completed` 的消费者体验不同：

- `llm.requested` 更适合监控或 pre-LLM hook
- `llm.completed` 只有回复，不带完整上下文
- `run.context_ready` 更适合脚本型 consumer 和后处理模块

这样既保留现有细粒度事件，也给外部集成一个低门槛入口。

## Script Hook Model

### Registration Source

Phase 1 采用配置式注册。

建议新增配置节：

```json
{
  "hooks_runtime": {
    "script_handlers": [
      {
        "module": "runtime-audit-script",
        "events": ["run.context_ready", "run.failed"],
        "command": ["python", "scripts/hook_consumer.py"],
        "timeout_seconds": 10,
        "enabled": true,
        "cwd": "./"
      }
    ]
  }
}
```

### Registration Rules

- `module`
  - script handler 的模块名
  - 同时决定其 hook state 路径
- `events`
  - 订阅的 `HookEventType`
- `command`
  - 本地可执行命令数组
- `timeout_seconds`
  - 单次执行超时
- `enabled`
  - 是否启用
- `cwd`
  - 执行工作目录，可选

### Execution Contract

脚本执行规则：

- `stdin`
  - 传入完整 `HookEventEnvelope` JSON
- `stdout`
  - 结构化 JSON 响应
- `stderr`
  - 只做日志记录
- 非零退出码
  - 记为 script hook 执行失败

### Environment Variables

运行脚本时，额外提供：

- `ATLASCLAW_HOOK_EVENT`
- `ATLASCLAW_USER_ID`
- `ATLASCLAW_SESSION_KEY`
- `ATLASCLAW_RUN_ID`
- `ATLASCLAW_MODULE`

不额外注入：

- 用户 token
- provider 凭证
- 整个 `deps`
- 数据库连接信息

## Structured Output Protocol

脚本 `stdout` 必须是 JSON，顶层结构为：

```json
{
  "actions": [
    {
      "type": "create_pending",
      "summary": "User correction needs review",
      "body": "Assistant misunderstood the approval scope",
      "metadata": {"source": "script"}
    },
    {
      "type": "write_memory",
      "title": "Confirmed lesson",
      "body": "Always ask for approver scope before finalizing",
      "metadata": {"source": "script"}
    },
    {
      "type": "add_context",
      "summary": "Recent confirmed preference",
      "body": "User prefers concise remediation steps"
    }
  ]
}
```

### Supported Actions in Phase 1

只支持 3 类通用动作：

1. `create_pending`
- 创建待确认项
- 进入 `pending.jsonl`

2. `write_memory`
- 直接写入长期记忆
- 通过 `MemorySink`

3. `add_context`
- 写入可回注上下文的确认项
- 通过 `ContextSink` / hook state

### Why These Three

这 3 个动作足够支撑：

- 审计
- 候选学习项
- 经确认后的长期记忆
- 未来上下文回注

而且仍然保持 core 通用，不引入业务语义。

## Action Application Rules

### `create_pending`

运行时负责：

- 生成 `PendingHookItem`
- 绑定 `module_name`
- 绑定 `user_id`
- 关联 `source_event_ids`
- 将 `body` 等内容写入 `payload`

### `write_memory`

运行时负责：

- 调用 `MemorySink.write_confirmed(...)`
- 写入 `memory_<timestamp>.md`
- 发出 `memory.confirmed`

### `add_context`

运行时负责：

- 将该项写入模块级 confirmed state
- 允许后续通过 `ContextSink` 暴露
- 不直接写入 prompt，由现有上下文整合流程决定是否回注

## Failure Handling

### Script Execution Failure

以下情况视为脚本执行失败：

- 命令不可执行
- 超时
- 返回非 0 退出码
- `stdout` 不是合法 JSON
- JSON 中动作协议不合法

处理原则：

- 不影响主 run 成功与否
- 记录日志
- 可选发出一个内部 hook failure record
- 不允许脚本 hook 失败反向污染用户主响应

### Invalid Action Payload

如果某个 action 无效：

- 丢弃该 action
- 记录错误
- 其它合法 action 继续执行

这样保证脚本输出部分失效不会拖垮整批动作。

## Security Model

### Default Policy

脚本型 hook handler 默认关闭，必须显式配置启用。

### Guardrails

必须满足：

- 超时控制
- 固定工作目录
- 无权限提升
- 不注入敏感凭证
- 不自动访问整个运行时依赖图
- 不允许脚本直接改 AtlasClaw 配置或代码

### Sensitive Data Boundary

允许暴露给脚本的内容：

- 用户消息
- 归一化历史消息
- assistant 回复
- system prompt
- 工具调用摘要
- 事件元数据

不允许默认暴露：

- API keys
- 用户 token
- provider 内部连接对象
- 原始数据库 session

## Storage Model

沿用现有：

- hook state：
  - `workspace/users/<user_id>/hooks/<module_name>/`
- memory：
  - `workspace/users/<user_id>/memory/memory_<timestamp>.md`

script handler 只是新的 consumer，不引入新的特例化目录。

## Compatibility with self-improving

这版能力落地后，`self-improving` 不需要进入 core，也不需要被硬编码：

- 它可以注册一个或多个 script hook
- 订阅：
  - `run.context_ready`
  - `message.user_corrected`
  - `run.failed`
- 再通过结构化动作协议：
  - 创建待确认项
  - 写长期记忆
  - 写上下文候选项

这就是“能力层”和“skill 层”的清晰分工。

## API Impact

Phase 1 不新增新的专属 API 类型，只复用现有通用 hook API：

- `GET /api/hooks/{module}/events`
- `GET /api/hooks/{module}/pending`
- `POST /api/hooks/{module}/pending/{id}/confirm`
- `POST /api/hooks/{module}/pending/{id}/reject`

必要时可新增：

- `GET /api/hooks/{module}/confirmed`

但这不是本轮强制项。

## Implementation Impact

预计需要修改：

- `app/atlasclaw/hooks/runtime_models.py`
  - 增加 `RUN_CONTEXT_READY`
- `app/atlasclaw/agent/runtime_events.py`
  - 增加聚合上下文事件发射
- `app/atlasclaw/agent/runner.py`
  - 在成功/失败结束点发出 `run.context_ready`
- `app/atlasclaw/hooks/runtime.py`
  - 增加 script handler 支持
  - 增加动作解析和应用
- `app/atlasclaw/main.py`
  - 启动时加载配置式 script handlers
- `app/atlasclaw/core/config_schema.py`
  - 新增 `hooks_runtime.script_handlers` 配置模型

新增文件预计包括：

- `app/atlasclaw/hooks/runtime_script.py`
  - script runner 与输出解析

## Testing Strategy

### Unit Tests

- `run.context_ready` payload 完整性
- script handler 配置解析
- 脚本 stdin / stdout 协议
- 非法 JSON 输出处理
- timeout / 非零退出码处理
- `create_pending` / `write_memory` / `add_context` 三类动作应用

### Integration Tests

- run 成功后 script handler 能收到完整上下文
- run 失败后 script handler 能收到失败态上下文
- write_memory 动作成功进入 `memory_<timestamp>.md`
- add_context 动作能被 `ContextSink` 读取

### E2E Tests

- 启动一个测试脚本型 hook handler
- 触发一次对话 run
- 验证：
  - `events` 可见
  - `pending` 可见
  - confirm / reject 可用
  - memory 文件生成
  - context 可读

## Documentation Updates Required

落地时同步更新：

- `docs/ARCHITECTURE.MD`
  - 增加 `run.context_ready` 与 script hook runtime
- `docs/MODULE-DETAILS.MD`
  - 增加 script handler、动作协议
- `docs/DEVELOPMENT-SPEC.MD`
  - 增加脚本 hook 安全边界
- `docs/SKILL-GUIDE.MD`
  - 说明 skill 如何消费 Hook Runtime，而不是进入 core

## Recommended Implementation Direction

按以下顺序实施：

1. 新增聚合事件 `run.context_ready`
2. 新增 script handler 配置模型
3. 实现 script runner 与结构化动作协议
4. 把动作接入 `HookStateStore` / `MemorySink` / `ContextSink`
5. 完整 UT + E2E 回归

这样可以在不破坏现有通用 Hook Runtime 的前提下，补齐 OpenClaw 风格的事件消费与本地命令 hook 能力。
