# 2026-04-13 Tool-First LLM Fallback Design

## Goal

调整 AtlasClaw 当前的工具路由策略，使其更接近通用助手体验：

- 是否进入工具回合，优先看“是否明显命中已有 skill / tool / provider 能力”。
- 公开知识、推荐、价格、班次、开放状态等问题，不再因为“可能是实时信息”就默认强制走工具。
- 一旦明显命中工具能力，可以先尝试工具。
- 但如果工具返回空结果、超时、报错、或 repeated no-progress，系统必须在同一轮自动回退到 LLM 给出最终回答，而不是直接失败。

## Current Problem

当前 runtime 有两处策略过硬：

1. `tool_gate` 会把公开实时信息倾向性地判成需要 grounded tool verification。
2. 工具回合一旦没有形成“成功工具证据”，post 阶段会直接 failed，而不是自动回退到 LLM。

这会导致：

- `web_search` 这类弱工具把正常问答拖成长时间等待。
- 用户看到 `Starting response analysis.`、`Waiting for tool`、`Stream error`，但实际上模型完全有能力给出一个合理的非核验答案。

## Chosen Behavior

### 1. Capability Match First

tool gate 的第一原则从“实时性/外部性优先”改为“能力匹配优先”：

- 如果请求没有明显命中现有 tool / skill / provider 能力，默认允许 `answer_direct`。
- 如果请求明显命中能力，允许 planner 选择 `use_tools`。
- “新闻 / 价格 / 班次 / 今日状态”这类公共问题不再因为 freshness 被默认抬成强制工具场景。

### 2. Tool First, But Not Tool Locked

当 planner 选择 `use_tools` 时，当前轮仍先走工具。

但以下任一情况出现时，必须释放最终回答权给 LLM：

- 工具空结果
- 工具超时
- 工具报错
- repeated no-progress
- repeated same failure

### 3. Fallback Must Be Safe

同轮 LLM fallback 不是“无条件瞎答”，而是分两类：

- 对公开/泛知识问题：允许给出 best-effort answer，并明确说明“工具未获得有效结果，以下内容未做实时核验”。
- 对 provider/private/enterprise 问题：如果工具没有返回私有数据，LLM 不得编造数据，只能透明说明工具失败原因、缺失参数或建议重试。

### 4. Preserve Tool Value

本轮不是把工具完全降级掉，而是把工具从“阻断器”改成“优先尝试的增益路径”：

- 命中明显能力时先试 tool。
- tool 有效时继续走 tool-backed answer。
- tool 无效时不阻断 final answer。

## Detailed Changes

### A. Tool Gate Prompt / Normalization

更新 classifier prompt 和 normalization 规则：

- 删除“public realtime facts must prefer tool-backed verification”这类刚性语言。
- `needs_live_data` 不再自动推高成 strict tool enforcement。
- provider / skill / private-context intent 仍然保留更高优先级，但默认政策仍优先落在 `prefer_tool`，不是“一律 must use”。

### B. Post-Phase Tool Failure Fallback

在 `runner_execution_flow_post.py` 增加同轮 LLM fallback：

- 当工具回合缺少 usable evidence 时，先判断是否允许 fallback。
- 允许时，构造一个最小 payload，把“用户问题 + 工具证据快照 + 工具失败摘要”交给模型生成最终回答。
- 只有 fallback 也失败时，才走最终 failed。

### C. Stream Failure Reasons Feed Post Fallback

复用现有 stream 阶段已经识别出的状态：

- `repeated_tool_failure`
- `repeated_tool_no_progress`
- `repeated_tool_loop`

这些状态进入 post 阶段后，不应只用于拼失败文案，还应触发 fallback answer 生成。

### D. Runtime UX

用户侧运行态要明确说明进入了 fallback：

- 先给 warning，说明“工具没有产出有效证据，正在回退到模型回答”。
- 再进入最终 answered，而不是 failed。

## Validation

新增或扩展测试覆盖：

1. 公开搜索问题在 repeated no-progress 后会自动产出 LLM fallback answer。
2. 工具报错时，`prefer_tool` 回合会自动产出 fallback answer，而不是 failed。
3. provider/private 请求在工具失败时，fallback 文案不会伪造私有数据。
4. tool gate classifier prompt 不再把公开实时问题描述为默认强制工具场景。
5. 现有 tool-only fallback、tool-only finalize、tool-timeout guard 回归不破坏。

## Out Of Scope

本轮不处理：

- 提升 `web_search` 搜索质量本身
- 查询改写 / multi-hop fetch
- self-improving memory 的完整闭环学习
- grounding provider 替换 HTML fallback provider
