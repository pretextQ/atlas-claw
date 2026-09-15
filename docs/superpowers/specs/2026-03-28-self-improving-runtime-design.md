# AtlasClaw Hook Runtime Design

## Goal

为 AtlasClaw 建立一层对齐 OpenClaw 的轻量 Hook Runtime 能力层。该能力层提供通用事件模型、hook 注册与调度、用户隔离的 hook state 持久化，以及通用的 memory sink / context sink 接口，供后续 skill、provider 或自动化模块使用。

## Status

- 设计状态：已确认，可进入 implementation plan
- 范围：AtlasClaw hook runtime + memory/session/context integration
- 非目标：不实现完整 plugin marketplace，不内置某个具体 skill 语义

## Problem Statement

当前 AtlasClaw 已经有：

- `HookSystem`
- `runner.py`
- `runtime_events.py`
- `memory` / `session` / `context compaction`

但这些能力还没有形成一套类似 OpenClaw 的通用 Hook Runtime：

1. 缺少统一的 runtime event taxonomy
2. 缺少通用的 hook state store
3. 缺少对 memory / context 的标准 sink 抽象
4. 缺少清晰的 hook registration boundary
5. 当前 skill 即使能提供规则说明，也无法稳定接入自动事件、持久化与上下文回注

因此，这一轮的目标不是把某个 skill 特例化，而是先建设 AtlasClaw 自己的通用 Hook 能力层。

## Design Principles

1. Core 只提供通用能力，不内置具体 skill 语义
2. 事件、存储、记忆、上下文回注必须彼此解耦
3. 所有运行时数据必须按用户隔离存储
4. 不打破现有 session / memory / prompt compaction 架构
5. 先做可复用的最小能力层，再让具体 skill 使用它
6. Phase 1 允许内建注册，但注册对象是通用 hook handler，不是特定 skill 语义
7. 接口命名和职责尽量对齐 OpenClaw 的 hooks/plugins/memory-context 分层

## OpenClaw Alignment

### OpenClaw Capability Model

根据 OpenClaw 官方文档：

- `Skills` 更偏向能力说明和行为包
- `Hooks` 负责监听 command / session / agent / message 等事件并执行自动化
- `Plugins` 负责扩展新的能力边界，并通过 slot 机制接入 memory / context engine 等独占能力
- memory/context 的自动捕获与回注并不依赖单个 `SKILL.md`，而是依赖 hooks + memory/context engine 的运行时集成

参考：

- OpenClaw Hooks: <https://docs.openclaw.ai/automation/hooks>
- OpenClaw Plugins: <https://docs.openclaw.ai/tools/plugin>
- OpenClaw Skills: <https://docs.openclaw.ai/tools/skills>

### AtlasClaw Mapping

本设计在 AtlasClaw 中采用以下映射：

- `SKILL.md`：规则说明层，对应 OpenClaw 的 skill 文档层
- `HookSystem` + `runtime_events.py` + `runner.py`：Hook Runtime 事件捕获与调度层
- `HookStateStore`：通用 hook 状态持久化层
- `MemorySink`：通用长期记忆写入层
- `ContextSink`：通用上下文回注层

### Explicit Non-Goals for Phase 1

- 不实现完整 plugin marketplace
- 不实现文件系统自动发现 hook modules
- 不实现完整 slot registry
- 不把 `self-improving` 或其它 skill 语义硬编码到 core event model 里

但 Phase 1 的接口必须可扩展到：

- `memory` slot
- `contextEngine` slot
- file-system discovery
- provider-supplied hook handlers
- skill-driven automation modules

## User-Approved Direction

本轮确认的方向如下：

- 需要对齐 OpenClaw 的 hooks/plugins/memory-context 分层能力
- 不要把 `self-improving` 做成 core 内置语义
- core 只做 event、hook、sink、state 等通用能力
- 最终 `self-improving` 应该能够利用这些新增能力完成自身 skill 行为

## High-Level Architecture

新增一个轻量的 Hook Runtime 层，由五部分组成：

1. Event Model
- 定义统一 `HookEventType` 与 envelope
- 供所有 hook handlers 订阅

2. Hook Registry
- 注册 hook handlers
- 提供 phase / event_type / priority / mode 等元信息

3. Hook Runtime Dispatcher
- 在 `runner.py`、`runtime_events.py`、message/session 路径中发出统一事件
- 调度 hook handlers

4. Hook State Store
- 为每个用户、每个 handler/module 提供独立的 state 路径
- 不假设该状态一定代表 memory 或 skill 数据

5. Sinks
- `MemorySink`: 将某些 hook 决策写入长期记忆
- `ContextSink`: 将某些确认后的内容纳入上下文回注流程

## Event Taxonomy

### Hook Runtime Events

建议定义统一 `HookEventType`，第一阶段至少包含：

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

说明：

- `run.*` 描述一次 agent 运行的生命周期
- `message.*` 描述消息进入与显式纠正
- `llm.*` 描述模型调用阶段
- `tool.*` 描述工具调用阶段
- `memory.*` 描述候选知识的最终记忆决策结果

### Hook Event Envelope

所有 Hook Runtime Events 采用统一 envelope：

- `id`
- `event_type`
- `user_id`
- `session_key`
- `run_id`
- `channel`
- `agent_id`
- `created_at`
- `payload`

其中：

- `payload` 保留事件具体字段
- core 不解释某个 skill 的具体语义
- 不在 envelope 里引入 `self-improving`、`reflection_candidate` 之类特例字段

## Registration Model

### Phase 1: Built-In Hook Registration

本轮先做内建注册，而不是文件系统自动发现：

- 注册的是通用 hook handlers
- handler 可以是内建模块、实验能力或后续 bridge
- 注册入口留在 core runtime
- 但 runtime 不绑定具体 skill 语义

### Future Direction

后续可演进为：

- 文件系统自动发现 hook handlers
- provider-supplied hook modules
- plugin slot registry
- 可配置启停和优先级

## Hook Handler Contract

建议定义通用 handler 接口：

- 输入：`HookEventEnvelope`, `HookRuntimeContext`
- 输出：`HookActionResult`

### HookRuntimeContext

建议包含：
- `user_info`
- `session_manager`
- `session_manager_router`
- `memory_manager`
- `deps`
- `workspace_path`
- `hook_state_store`
- `memory_sink`
- `context_sink`

### HookActionResult

建议允许：
- `no_op`
- `append_state_record`
- `create_pending_item`
- `emit_followup_event`
- `write_memory`
- `request_context_injection`

这样 hook handler 可以声明自己的副作用，而不是直接散落写文件。

## Storage Model

### Primary Storage

所有 hook 运行时状态都存放在：

`workspace/users/<user_id>/hooks/<handler_or_module_name>/`

建议目录结构：

```text
workspace/users/<user_id>/
├── hooks/
│   └── <handler_or_module_name>/
│       ├── events.jsonl
│       ├── pending.jsonl
│       ├── decisions.jsonl
│       ├── snapshots/
│       └── summaries/
└── memory/
    ├── memory_20260328_101530_123456.md
    └── memory_20260328_142001_654321.md
```

### File Responsibilities

- `events.jsonl`
  - 原始 hook 事件或该 handler 派生出的内部状态记录
  - 只追加，不回写

- `pending.jsonl`
  - 待用户确认的候选项
  - 不限定具体业务语义

- `decisions.jsonl`
  - 用户确认/拒绝决定
  - 可用于审计和统计

- `snapshots/`
  - 可选，用于导出聚合状态

- `summaries/`
  - 可选，用于定期汇总

### Confirmed Memory

只有在某个 handler 显式通过 `MemorySink` 请求写入时，候选内容才会进入：

`workspace/users/<user_id>/memory/memory_<timestamp>.md`

这样可以与现有 `HistoryMemoryCoordinator` 的 recall / compaction 流程保持一致。

## Sinks

### MemorySink

`MemorySink` 是一个通用写入接口，不属于某个具体 skill。

职责：
- 验证写入是否合法
- 将确认后的候选项写入 `memory_*.md`
- 生成 `memory.confirmed` / `memory.rejected` 事件

### ContextSink

`ContextSink` 是一个通用回注接口。

职责：
- 只处理已确认内容
- 与现有 memory recall 机制兼容
- 控制上下文大小和注入顺序

### What Does Not Get Injected

不直接注入：
- hook 原始 `events.jsonl`
- 未确认 `pending.jsonl`
- 纯审计 `decisions.jsonl`

## Runtime Integration Points

### HookSystem

现有 `hooks/system.py` 需要扩展为 Hook Runtime：

- 增加统一事件类型支持
- 提供 handler registry
- 支持内建注册
- 支持后续扩展 discovery

### AgentRunner

在 `runner.py` 中增加统一事件发射：

- run start -> `run.started`
- run success -> `run.completed`
- run failure -> `run.failed`

### RuntimeEventDispatcher

在 `runtime_events.py` 中补充：

- LLM 事件映射
- tool 事件映射
- runtime event forwarding

### Message / Session Path

在消息接收与纠正路径中增加：

- `message.received`
- `message.user_corrected`

### Session / User Routing

必须复用当前：

- `SessionManagerRouter`
- `build_scoped_deps(...)`
- `user_info.user_id`

确保所有 hook state 按真实用户隔离，不落到共享 default bucket。

## API Surface

Phase 1 不直接暴露某个 skill 专属 API，而是先暴露 hook runtime 的最小管理接口。

### Proposed Endpoints

- `GET /api/hooks/{module}/pending`
  - 返回当前用户某个 hook handler/module 的待确认项

- `POST /api/hooks/{module}/pending/{id}/confirm`
  - 确认某条待确认项
  - 触发 `MemorySink` 写入或其他 handler-specific action

- `POST /api/hooks/{module}/pending/{id}/reject`
  - 拒绝某条待确认项
  - 写入决策并更新状态

- `GET /api/hooks/{module}/events`
  - 查看最近 hook 事件或 handler state

这样 API 是通用 hook runtime API，而不是先做 `self-improving` 专属路由。

## Data Model

### HookRuntimeEvent

建议字段：
- `id`
- `event_type`
- `user_id`
- `session_key`
- `run_id`
- `channel`
- `agent_id`
- `created_at`
- `payload`

### PendingHookItem

建议字段：
- `id`
- `module_name`
- `user_id`
- `source_event_ids`
- `summary`
- `payload`
- `status` (`pending|confirmed|rejected`)
- `created_at`
- `updated_at`

### HookDecisionRecord

建议字段：
- `id`
- `module_name`
- `pending_id`
- `decision` (`confirm|reject`)
- `decided_by`
- `decided_at`
- `note`

## Safety Rules

必须遵守以下约束：

- 不存储凭证、token、密码、密钥
- 不存储健康、财务、第三方敏感信息
- 不从沉默中学习偏好
- core 不内置某个 skill 的私有语义
- 不自动修改代码、配置、prompt、skill 文件
- 不在未确认前把候选内容注入长期记忆
- 不让原始失败细节无限进入 prompt

## Compatibility with self-improving Skill

当前 `atlasclaw-providers/skills/self-improving/SKILL.md` 继续保留，其角色是：

- 行为规则文档
- 人类可读规范
- future consumer of Hook Runtime

它不是 Hook Runtime 的一部分，但未来可以利用以下能力完成自身 skill 行为：

- 订阅 `message.user_corrected`
- 订阅 `run.failed` / `llm.failed` / `tool.failed`
- 订阅 `run.completed`
- 使用 hook state store 记录候选状态
- 使用 `MemorySink` 与 `ContextSink` 接入长期记忆与上下文回注

## Documentation Updates Required

本设计落地时需要同步更新：

- `docs/architecture.md`
  - 增加 Hook Runtime / memory sink / context sink 描述
- `docs/module-details.md`
  - 增加 Hook Runtime、HookStateStore、MemorySink、ContextSink、通用 API
- `docs/development-spec.md`
  - 增加 hook handler 与安全边界说明
- `docs/SKILL_GUIDE.md`
  - 说明 skill 规则层与 hook runtime 能力层的区别

## Testing Strategy

### Unit Tests

- event taxonomy validation
- hook registry registration / dispatch
- per-user hook state isolation
- pending item lifecycle
- memory sink writes `memory_*.md`
- context sink exposes confirmed items only

### Integration Tests

- runner success path emits `run.completed`
- runner failure path emits `run.failed`
- tool / llm failure emits corresponding events
- confirmed memory enters recall chain

### E2E Tests

- list pending hook items
- confirm item and verify memory file created
- reject item and verify no memory promotion
- verify hook state isolation across users

## Phasing

### Phase 1

- Hook Runtime
- built-in hook handler registration
- HookStateStore
- MemorySink / ContextSink
- generic pending/confirm/reject API
- No UI entry yet

### Phase 2

- file-system discovery
- provider-supplied hook handlers
- lightweight frontend management entry

### Phase 3

- plugin slot expansion
- richer context engine policies
- cross-module hook orchestration

## Recommended Implementation Direction

采用“轻量 Hook Runtime + Memory/Context sinks”方案：

- 不做完整 plugin engine
- 不做文件系统自动发现注册
- 先建设最小可复用 Hook Runtime
- 不把 `self-improving` 或其它 skill 语义硬编码进 core
- 让具体 skill 后续利用这套能力完成自己的自动化行为

这样既满足当前需求，又真正对齐 OpenClaw 的 hooks/plugins/memory-context 分层。
