# 2026-04-13 Built-in Tool Exclusion and Script Execution Scope Design

## Goal

为 AtlasClaw 增加一组平台级内建工具控制配置，满足以下要求：

1. 通过反向排除配置控制内建 FS / runtime 工具是否在启动时注册。
2. 默认不排除任何工具。
3. `allow_script_execution` 仅对本地高风险文件与命令工具生效。
4. 工具一旦被关闭，必须从运行时注册表、`/api/skills`、LLM 可见工具集、最小工具集过滤与执行链路中一并消失。
5. 不引入运行时硬编码分支，不依赖额外 deny 补丁。
6. 不为 markdown skill / provider skill 增加任何新开关；它们默认保持开启。

## Scope

### In scope

1. 配置 schema 增加 `skills.tools_exclusive`。
2. 将 `allow_script_execution` 的作用域收敛到本地 FS / runtime 高风险内建工具。
3. 内建工具注册链路支持启动时排除工具。
4. `/api/skills` 与运行时工具快照对齐新的注册结果。
5. 补齐 `delete` 内建工具注册与 FS 分组映射。
6. 测试与示例配置更新。

### Out of scope

1. provider / markdown skills 的功能开关设计。
2. 运行时 RBAC 或 policy pipeline 的新权限模型。
3. 非内建工具的动态卸载。

## Current State

当前代码存在三个问题：

1. `allow_script_execution` 现在被用在比“本地 FS / exec 安全开关”更宽的范围上，语义不够清晰。
2. 内建工具注册目前通过 `ToolCatalog -> register_builtin_tools()` 统一装载，但缺少一个启动时排除某些内建工具的配置层。
3. `delete_tool.py` 已存在，但尚未进入 `_TOOL_REGISTRY` 和 `group:fs`，因此配置目标与实际可注册工具不完全一致。

## Chosen Configuration Model

配置放在 `skills` 下：

```json
{
  "skills": {
    "tools_exclusive": [],
    "allow_script_execution": true
  }
}
```

### `tools_exclusive`

- 类型：`list[str]`
- 默认值：`[]`
- 含义：启动时排除指定内建工具或工具组
- 支持值：
  - 具体工具名：`read`、`write`、`edit`、`delete`、`exec`、`process` 等
  - 工具组：`group:fs`、`group:runtime`、`group:web`、`group:ui` 等

语义规则：

1. 这是“反向排除”列表，而不是 allowlist。
2. 只影响 **内建工具注册**，不直接影响 provider skills / markdown skills。
3. `tools_exclusive` 中命中的工具，在启动时不注册。
4. 未注册的工具在整个运行时中不可见、不可选、不可执行。

### `allow_script_execution`

- 类型：`bool`
- 默认值：`true`
- 含义：管理员总开关，仅作用于本地高风险内建工具

受影响工具集合：

- `read`
- `write`
- `edit`
- `delete`
- `exec`

说明：

1. 这里把 `edit` 视为文件 CRUD 的一部分，因为它具有文件修改能力。
2. `process` 不纳入该总开关，除非后续单独提出要求。
3. `allow_script_execution=false` 时，上述工具即使未被 `tools_exclusive` 排除，也必须在注册前被统一剔除。
4. markdown skill / provider skill 不新增任何开关，本次需求不改变它们默认开启的行为。

## Architecture Changes

### 1. Config Schema

在 `SkillsConfig` 增加：

- `tools_exclusive: list[str] = []`

并更新 `allow_script_execution` 描述，使其明确表示：

- 仅控制本地高风险内建 FS / runtime 工具的注册
- 默认启用
- 管理员可关闭

### 2. Built-in Tool Catalog Alignment

补齐以下注册与分组对齐：

1. `_TOOL_REGISTRY` 增加 `delete`
2. `GROUP_FS` 扩展为：
   - `read`
   - `write`
   - `edit`
   - `delete`
3. `GROUP_ATLASCLAW` 自动包含 `delete`

这样 `tools_exclusive=["group:fs"]` 才能完整覆盖文件工具集合。

### 3. Registration-Time Exclusion

`register_builtin_tools()` 在 profile 过滤后、实际注册前增加两层排除：

1. `skills.tools_exclusive`
2. `allow_script_execution=false` 时的固定高风险工具剔除

执行顺序：

```text
profile tools
-> allow/deny filter (existing)
-> tools_exclusive filter
-> allow_script_execution safety filter
-> register remaining built-in tools
```

原则：

- 这是启动注册阶段的单调收敛，不在运行时再补一层特殊 deny。
- `tools_exclusive` 与 `allow_script_execution` 都不能把工具重新放回集合。

### 4. Runtime Consistency

因为被关闭的工具根本不会注册，所以以下输出必须天然一致：

- `SkillRegistry.tools_snapshot()`
- `/api/skills?include_metadata=true`
- LLM prompt 中的 available tools
- 最小工具集投影结果
- 工具执行循环

不允许出现：

- API 还能看到工具，但运行时不能执行
- LLM 还能看到工具，但注册表中没有 handler
- `tools_exclusive` 只在某个 UI/API 层生效，主运行时无感

## Behavior Matrix

| 配置 | 结果 |
| --- | --- |
| `tools_exclusive=[]`, `allow_script_execution=true` | 所有内建工具按 profile 正常注册 |
| `tools_exclusive=["read"]` | `read` 不注册，其他不受影响 |
| `tools_exclusive=["group:fs"]` | `read/write/edit/delete` 全部不注册 |
| `allow_script_execution=false` | `read/write/edit/delete/exec` 不注册 |
| `tools_exclusive=["group:runtime"]`, `allow_script_execution=false` | `exec/process` 先被组排除，`read/write/edit/delete` 再被安全开关排除 |

## Files Expected to Change

### Backend

- `app/atlasclaw/core/config_schema.py`
- `app/atlasclaw/tools/catalog.py`
- `app/atlasclaw/tools/registration.py`
- `app/atlasclaw/main.py`

### Config / Docs

- `atlasclaw.json.example`
- `docs/module-details.md`
- `docs/development-spec.md`（如需补充配置行为说明）

### Tests

- `tests/atlasclaw/test_tool_catalog.py`
- `tests/atlasclaw/test_skills_api_metadata.py`
- 新增或扩展注册层测试文件（如 `tests/atlasclaw/test_builtin_tool_registration.py`）

## Validation Requirements

至少覆盖以下验证：

1. 默认配置下，`read/write/edit/delete/exec` 正常注册。
2. `tools_exclusive=["read"]` 时，`read` 不出现在注册表与 `/api/skills`。
3. `tools_exclusive=["group:fs"]` 时，FS 四个工具全部消失。
4. `allow_script_execution=false` 时，`read/write/edit/delete/exec` 全部不注册。
5. `process` 在 `allow_script_execution=false` 下仍保留。
6. `delete` 被纳入 `group:fs`。
7. 运行时工具快照与 API 返回一致。
8. markdown skill / provider skill 默认仍可用，不新增任何配置项。

## Risks and Guardrails

### Risk 1: 误伤 markdown skill / provider skill

如果把 `allow_script_execution` 当成“全平台脚本执行总开关”，会把本次需求错误扩大到非内建工具域。

**Guardrail:**
- `allow_script_execution` 只在 built-in registration 阶段生效。
- markdown/provider skill 不新增任何配置读取逻辑。

### Risk 2: group 排除与显式工具排除不一致

如果 `delete` 没补进 `group:fs`，会导致 `tools_exclusive=["group:fs"]` 语义不完整。

**Guardrail:**
- 先补齐 catalog，再接入配置过滤。

### Risk 3: API 与运行时不一致

如果只在某个接口层过滤，而不是注册层过滤，会再次出现“API 可见 / runtime 不可执行”的错位。

**Guardrail:**
- 所有开关都前置到注册阶段处理。

## Acceptance Criteria

1. `skills.tools_exclusive` 生效，默认空列表。
2. `skills.allow_script_execution` 仅控制本地高风险内建 FS / runtime 工具。
3. `delete` 被完整纳入内建工具注册与 `group:fs`。
4. 被排除工具不会出现在注册表、API、prompt、最小工具集、执行循环中。
5. markdown skill / provider skill 默认保持开启，本次不新增它们的开关。
6. 测试覆盖默认、单工具排除、组排除、安全总开关三类行为。

## Delivery Constraint

本需求必须端到端完成：

- 配置 schema
- 启动注册逻辑
- 工具分组对齐
- API 可见性对齐
- 测试
- 示例配置

不接受只在某一层补丁式生效的最小实现。
