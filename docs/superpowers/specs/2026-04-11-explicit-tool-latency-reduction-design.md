# 2026-04-11 Explicit Tool Latency Reduction Design

## Goal

降低“明确工具场景”的端到端时延，重点优化以下两段：

1. 主模型首轮产出真实 `tool_call` 的耗时。
2. 工具执行完成后，基于工具结果生成最终回答的耗时。

本设计不允许通过业务关键词硬编码做分流，必须继续复用 AtlasClaw 现有的 provider / skill / tool 元数据与 hint 体系，并保持 chat-first 交互语义：
- 普通聊天仍可直接回答。
- 明确工具场景由 LLM 决定是否调用工具。
- 一旦进入工具回合，最终回答必须建立在真实工具执行结果之上。

## Baseline

真实 agent 基准采集时间：`2026-04-11 20:54:54`

| 场景 | 工具 | answer_ready | wall time |
| --- | --- | --- | --- |
| 明天上海天气如何 | `openmeteo_weather` | `18.7s` | `27.053s` |
| 查下CMP 里目前所有待审批 | `smartcmp_list_pending` | `35.4s` | `48.291s` |
| 我要看下TIC20260316000001的详情 | `smartcmp_get_request_detail` | `49.4s` | `58.399s` |
| 还有查下CMP 里目前有的服务目录 | `smartcmp_list_services` | `22.3s` | `30.961s` |

优化后不得低于该基线，即：
- 不允许引入新的 timeout / failed / answered 后回滚重试。
- 上述场景的 `answer_ready` 与 `wall time` 不能劣化。

## Current Bottlenecks

### 1. 首轮主模型仍在吃大 prompt

虽然 `tool_intent_plan_metadata_short_circuit` 已经把前置 planner 缩短到亚秒级，但进入主模型执行轮后，当前 prompt 仍然偏大：
- 工具集合虽然已经收窄，但仍走较重的 execution prompt。
- provider / skill / bootstrap / docs 等上下文在“明确工具场景”里注入过多。

结果是：
- 明确工具请求的第一轮 `tool_call` 仍要花 6-20 秒不等。

### 2. provider tool 的 metadata recall 仍然不够细粒度

当前 recall 有：
- provider-level hint
- skill-level hint
- builtin tool-level hint

但 provider executable tool 还没有对等的 tool-level hint，因此：
- SmartCMP detail/list 等请求，往往只能先收敛到 provider/skill 大包。
- 随后 projection 仍需要在较宽的工具集里做二次收敛。

### 3. 工具后第二轮总结仍走重 prompt

工具结果返回后，当前仍会触发一轮 LLM 总结：
- 工具结果已足够明确，但 summary 轮仍带入过多运行时上下文。
- 导致 explicit tool 场景“工具本身很快，但总结很慢”。

## Chosen Approach

采用“工具级 metadata 收敛 + 轻量 execution/finalize prompt”的统一方案。

### Principle A: 所有 tool 一视同仁进入 metadata recall

不再只给 builtin tool 建立 tool-level hint。
所有 executable tool 都构建 tool-level metadata 文档：
- builtin tool
- provider tool
- standalone skill tool

每个 tool hint 至少包含：
- tool name
- description
- source
- provider_type
- group_ids
- capability_class
- aliases
- keywords
- use_when
- avoid_when
- priority / planner_visibility / result_mode（如适用）

目标：
- 让 intent recall 能优先收敛到具体 tool，而不是先落到 provider/skill 大包。

### Principle B: projection 优先信任精确 tool 命中

当 metadata recall 或 intent plan 已给出明确 `target_tool_names` 时：
- 先保留该精确 tool 子集。
- 只有在 tool 级命中为空或不够时，才退到 provider / skill / capability_class。

这一步避免当前“明确请求仍落到大工具集”的问题。

### Principle C: 小工具集使用轻量 execution prompt

当回合满足以下条件时：
- `IntentAction.USE_TOOLS`
- 非 follow-up
- projected tools 数量小于等于阈值（建议 1-3）

则主模型切换到 minimal execution prompt：
- 保留当前用户问题
- 保留当前时间 / 用户必要上下文
- 保留最小工具列表与 tool-call contract
- 移除 bootstrap/docs/skills index/大段运行时说明

目标：
- 缩短主模型首轮产出 `tool_call` 的时间。

### Principle D: 工具后总结使用轻量 finalize prompt

如果工具已执行完成，且仍需要 LLM 生成最终自然语言回答：
- final summary 轮只保留用户问题 + 工具结果 + 输出要求。
- 不再注入完整 execution prompt。

如果工具本身声明 `result_mode=tool_only_ok` 且当前结果足够直接：
- 允许直接以工具结果完成回答，不再额外触发 summary 模型轮。

这一步是通用 contract，不是按业务关键词硬编码。

### Principle E: 用元数据，不用关键词硬编码

本轮优化不得新增“天气/审批/详情/目录”这类业务关键词判定逻辑。
所有收敛必须来自：
- provider metadata
- skill metadata
- tool metadata
- runtime projection contract

## Detailed Changes

### 1. Tool hint builder 泛化

当前 builtin-only 的 tool hint builder 改为统一的 executable tool hint builder：
- 输入：`available_tools`
- 输出：全部 executable tool 的 normalized hint docs

优先级顺序：
1. tool-level hit
2. skill-level hit
3. provider-level hit

### 2. Metadata recall 输出更细粒度 preference

recall 结果需要明确区分：
- `preferred_tool_names`
- `preferred_skill_names`
- `preferred_provider_types`
- `preferred_capability_classes`

当 tool-level hit 存在时：
- 不再把 provider-level 候选无条件并入。
- provider/skill 只保留与精确 tool 相容的那部分。

### 3. Tool projection 先收敛到精确 tool

projection 顺序：
1. `target_tool_names`
2. `provider_type + skill_name`
3. `provider_type`
4. `capability_class`
5. safety / essential builtins

并保证：
- 收敛是单调的。
- 不会因高优先级 provider 而把不相容工具重新放大。

### 4. Execution prompt profile 轻量化

新增“显式工具场景 prompt profile 选择逻辑”：
- 使用当前 turn 的 projected tool count、intent action、follow-up 状态决定 prompt mode。
- 小工具集默认走 minimal profile。

### 5. Finalize prompt profile 轻量化

工具回合结束后：
- 如果必须再走模型总结，则使用 finalize-minimal payload。
- 仅传：用户问题、工具摘要、格式要求。

## Validation

### Unit / Integration

新增或扩展测试覆盖：
1. provider tool-level hint docs 已生成。
2. recall 在 SmartCMP detail/list/service 场景优先命中具体 tool。
3. projection 在有 `target_tool_names` 时不会扩成 provider 大包。
4. explicit tool 场景会切到 minimal execution prompt。
5. tool finalize 会使用轻量 payload。

### Real Agent E2E

必须跑以下真实 agent 场景：
1. `明天上海天气如何`
2. `查下CMP 里目前所有待审批`
3. `我要看下TIC20260316000001的详情`
4. `还有查下CMP 里目前有的服务目录`

验收标准：
- 有真实 `tool_call`
- 无 timeout / failed
- 无 `Answered -> Retrying`
- answer_ready 不劣于 baseline

## Out of Scope

本轮不处理：
- 通用 `web_search/web_fetch` 搜索质量提升
- grounding provider 抽象升级
- Bing/Google HTML fallback 的召回问题
- 新增业务关键词路由
