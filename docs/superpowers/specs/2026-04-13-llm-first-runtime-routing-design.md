# 2026-04-13 LLM-First Runtime Routing Design

## Goal

将 AtlasClaw 当前“metadata / planner 先拍板，再让模型执行”的路由方式，替换为已经落地的“先准备上下文和能力边界，再让主模型做本轮决策”的 runtime-first 主路径。

目标不是简单降低工具调用率，而是把决策顺序改对：

- 主模型先判断这轮是 `direct_answer`、`ask_clarification`、`use_tools` 还是 `create_artifact`
- skills / tools / providers 先作为“可用能力索引”提供给模型
- metadata recall 只保留为候选压缩和提示，不再短路主路由
- follow-up 默认承接上文，不因为 `use_tools` 而丢失上下文
- 工具执行完成后，必须检查“用户目标是否真的完成”，而不是“返回了结构化数据就算成功”
- Core routing logic must not rely on keyword-hardcoded artifact/provider/tool-family classifiers; new capability types must flow through explicit capability metadata, not phrase lists in core code

---

## Why The Current Design Fails

这轮暴露出的失败不是单点 bug，而是当前架构的系统性问题：

1. 前置 metadata recall 和 short-circuit 太早介入
   - `tool_intent_plan_metadata_short_circuit` 会在主模型真正理解用户意图前，就先把请求送进某个 provider / tool 桶
   - 中文 follow-up 如“将这些申请写入一个新的PPT”会因为“申请”命中 SmartCMP，而不是因为“写入新的 PPT”命中产物生成意图

2. `use_tools` 回合会过早裁掉历史
   - follow-up 请求没有被稳妥地视为“基于上文继续处理”
   - 当前链路更像“重新猜用户要哪个工具”，而不是“在已有结果上继续工作”

3. 技能命中依赖 metadata / token overlap
   - 全英文 skill metadata 对中文命中很弱
   - 模型本来能理解“PPT / 幻灯片 / 导出演示文稿”，但前置路由层先失败了

4. 完成判定过粗
   - 只要工具返回结构化结果，就可能被判成 finalize
   - 但用户真实目标可能是“生成 PPT / 文件 / 报告”，并不是“查到了些列表”

---

## OpenClaw Reference

从 `C:\Projects\githubs\openclaw-cn` 的实现看，OpenClaw 的关键优点不是规则更多，而是决策顺序更合理：

- 会话路由、权限过滤、上下文准备在前
- skills 以索引形式进入 prompt，不是前置 metadata router
- 主模型直接进入 agent loop
- tools 的真正选择发生在模型回合里
- loop detection、before-tool-call guard、tool choice constraint 放在 runtime 守卫层

AtlasClaw 的目标不是逐文件照搬 OpenClaw，而是收敛到同一个核心原则：

`前置层负责边界和上下文，主模型负责本轮意图决策，runtime 守卫负责纠偏与止损。`

---

## Final Decision

本方案已经不再采用“双栈灰度 + planner 保留回退”的路径。

AtlasClaw 的主请求链路已经切换为：

- 不再在热路径上运行独立 `tool-intent planner`
- 不再让 metadata recall 拥有路由拍板权
- 不再保留“旧 planner 路径”作为正式请求的主回退方案

允许保留的只有：

- 运行时守卫
- 指标与日志
- 兼容旧配置的软处理

不允许保留的只有：

- 任何独立于主模型之外的前置意图规划模型调用
- 任何 metadata short-circuit 对主 action 的覆盖
- 任何“因为可能要用工具，所以先清空上下文”的策略

---

## Proposed Architecture

### 1. Replace The Main Routing Path

主路径改成：

1. 解析 session、thread、user、recent context
2. 构建当前可见的 capability index
3. 允许 metadata recall 只做候选压缩
4. 让主模型直接给出本轮 action
5. runtime 根据 action 展开最小能力集合并继续执行
6. post phase 校验用户目标是否真的完成

也就是说，AtlasClaw 之后的主路径不再是：

`metadata recall -> planner tool projection -> planner model call -> main model`

而是：

`context + capability index -> main model decision -> runtime continuation`

### 2. Reframe Skills And Tools As An Index Layer

主模型不再直接收到“所有 skill 正文”，而是先收到一层轻量索引：

- skill name
- short description
- location or stable id
- whether it is executable or markdown-only
- compatible tool names / artifact types

tools 也先以索引形式给出：

- tool name
- short capability summary
- provider scope
- input expectations
- output type

模型先基于索引判断要不要使用某项能力；只有真正选中时，runtime 再补充更详细的 skill/tool 上下文。

### 2.1 Capability Index Contract

这层不是抽象概念，而是一份明确的数据契约。建议新增统一索引结构：

```json
{
  "capability_id": "skill:powerpoint-pptx-1.0.1",
  "kind": "skill",
  "name": "powerpoint-pptx-1.0.1",
  "summary": "Create or update editable PPTX slide decks",
  "provider_type": "",
  "artifact_types": ["pptx"],
  "declared_tool_names": [],
  "input_hints": ["title", "outline", "table data"],
  "file_path": "C:/Projects/cmps/atlasclaw-providers/skills/powerpoint-pptx-1.0.1/SKILL.md"
}
```

```json
{
  "capability_id": "tool:smartcmp_list_pending_requests",
  "kind": "tool",
  "name": "smartcmp_list_pending_requests",
  "summary": "List pending approval requests from SmartCMP",
  "provider_type": "smartcmp",
  "artifact_types": [],
  "declared_tool_names": ["smartcmp_list_pending_requests"],
  "input_hints": ["cmp instance"],
  "file_path": ""
}
```

设计要求：

- `capability_id` 必须稳定，可被模型选回
- `kind` 只允许 `skill` / `tool`
- `summary` 必须短，控制在 1 句
- `artifact_types` 用于让模型识别“生成 PPT / 文件 / markdown”这类目标
- `declared_tool_names` 用于 skill 和实际可执行工具的关联
- `file_path` 只对 markdown skill 有意义，用于二段加载 `SKILL.md`

### 2.2 Where The Index Comes From

当前代码里数据其实已经有了，不是从零做：

- executable skill metadata 来自 `SkillRegistry.snapshot()` / `snapshot_builtins()`
- markdown skill metadata 来自 `SkillRegistry.md_snapshot()` / `MdSkillEntry`
- tool metadata 来自 `SkillRegistry.tools_snapshot()` 和 `runner_prompt_context.collect_tools_snapshot()`

所以实现重点不是“新增一整套注册系统”，而是新增一个统一 adapter，把现有三份 snapshot 变成一份 `capability_index`：

- skill rows
- md skill rows
- tool rows

然后做去重：

- 如果某个 md skill 已经声明了具体 `declared_tool_names`
- prompt 里不要再把 skill 和它的每个衍生 tool 当成两套完全无关能力反复灌给模型

### 2.2.1 Eligibility Filtering Happens Before Index Construction

这里还要明确一条 OpenClaw 风格的边界：

模型看到的 capability index，必须已经是“当前真的可用”的能力集合，而不是把仓库里所有 skills/tools 都列出来再让模型自己踩权限坑。

因此 index 构建前必须先完成：

- tool policy pipeline 过滤
- provider / agent / group 级别 allowlist-denylist 过滤
- skill invocation policy 过滤
  - 包括 `disableModelInvocation`
  - 包括环境变量或运行条件不满足的 skill
- plugin-only / provider-only 能力展开后的可见性过滤

这一步的原则是：

- 模型只在“当前可执行、当前可读取、当前允许”的能力里做选择
- 不能把理论存在但当前不可用的 capability 暴露给模型
- metadata recall 可以压缩候选，但不能绕过 eligibility filtering

### 2.2.2 Prompt Budget And Truncation Must Be Explicit

OpenClaw 不只是“有 skills 索引”，还对索引预算做了明确限制。AtlasClaw 也要照这个思路补全：

- capability index 需要有明确数量上限
- capability index 需要有明确字符预算
- 超预算时必须可预测地截断，并留下诊断信息

要求如下：

- 先按 eligibility 和 policy 得到完整候选集
- 再按稳定顺序和候选压缩规则截断
- 截断后要在 trace / debug 日志里写清：
  - 原始候选数
  - 实际进入 prompt 的数量
  - 截断原因：`count` 或 `chars`

不允许：

- 每次随机截断不同的 skills/tools
- 因 prompt 过长 silently drop 掉高优先级 capability
- 因为 capability index 超长而重新引入独立 planner

### 2.3 Two-Stage Runtime

这层必须按“两段式”执行，而不是把所有细节一次性喂给模型。

#### Stage A: Routing Prompt

主模型只看到：

- 用户当前请求
- 最近相关上下文
- `capability_index`
- 明确的行动约束：只能返回 `direct_answer` / `ask_clarification` / `use_tools` / `create_artifact`

这一步不加载完整 `SKILL.md`，也不默认暴露全部工具细节。

#### Stage B: Capability Expansion

当模型选中某个 `capability_id` 后，runtime 再做定向展开：

- 选中 `tool:*`
  - 只暴露被选中的 tool，以及必要的 coordination tools
- 选中 `skill:*`
  - 如果 skill 绑定了 `declared_tool_names`，只展开这些 tool
  - 如果是 markdown-only skill，则定向加载该 skill 的 `SKILL.md`
  - 必要时再给出最小可执行工具集

这样可以避免：

- routing 阶段 prompt 过重
- skill 太多时模型无从判断
- 还没决定方向就把全量工具带回 prompt

### 2.4 Prompt Rendering Shape

建议不再分别输出一大段 `## Tools` 和另一大段 `## Skills`，而是统一成：

```text
## Available Capabilities
- [skill] powerpoint-pptx-1.0.1 | create editable PPTX decks | artifact:pptx | detail: read SKILL.md if selected
- [tool] smartcmp_list_pending_requests | list pending SmartCMP approvals | provider:smartcmp | returns: structured list
- [tool] write | write a new local file | artifact:file
```

同时增加一段明确约束：

```text
## Routing Contract
Return exactly one action:
- direct_answer
- ask_clarification
- use_tools
- create_artifact

If you choose use_tools or create_artifact, return selected capability ids.
Do not emit pseudo tool calls before capability selection is complete.
```

### 2.5 Example: `将这些申请写入一个新的PPT`

这句在新模式里的正确处理应该是：

1. recent context 中已经有“这些申请”的数据来源
2. routing prompt 里可见：
   - `skill:powerpoint-pptx-1.0.1`
   - 相关 SmartCMP 查询工具
   - 可能的 `write` / file artifact tool
3. 主模型输出：
   - `action=create_artifact`
   - `selected_capability_ids=["skill:powerpoint-pptx-1.0.1"]`
   - 如果当前上下文里没有申请数据，再补充 `tool:*` 作为数据来源
4. runtime 再二段展开 PPT skill，而不是被 SmartCMP metadata 先 short-circuit 成纯 provider 查询

也就是说，这类请求里：

- SmartCMP 是“数据来源能力候选”
- PPT skill 是“目标产物能力候选”
- 最终谁是主路径，由模型根据用户目标决定

### 3. Make The Main Model Decide Turn Intent

本轮高层意图由主模型给出，允许的结果只有四类：

- `direct_answer`
- `ask_clarification`
- `use_tools`
- `create_artifact`

其中：

- `direct_answer`：允许直接回答，不暴露真实工具执行路径
- `ask_clarification`：要求补必要信息，但必须带明确缺失项
- `use_tools`：进入工具回合
- `create_artifact`：优先进入文件 / PPT / 文档 / 报告等产物生成路径；需要数据时再显式引用上文或工具

### 3.3 Explicit Runtime Constraints Still Override Free Routing

去掉 planner 不等于去掉所有硬约束。像 OpenClaw 的 `tool_choice=none|required|specific tool` 一样，AtlasClaw 仍然需要保留明确的强约束入口。

这些约束优先级高于模型自由选择：

- 用户或 API 显式指定只能不用 tool
- 用户或 API 显式指定必须使用 tool
- 用户或 API 显式指定某个 capability / tool
- provider / policy 明确禁止某类 capability

因此主模型的“自由决策”范围应该是：

- 在显式约束收窄后的 capability 集合里做决策
- 而不是无视上层约束重新开放全部能力

### 3.1 Latency Rule: No Dedicated Planner Call On The Hot Path

这条是硬约束：

- 不再保留当前这种独立的 `tool-intent planner model call`
- 默认只允许一个主模型请求进入热路径
- routing decision 必须由主模型在这一次请求里直接做出

因此要区分“两个阶段”和“两个远程 LLM 调用”：

- `Stage A routing`
  - 是同一次主模型请求里的决策输出
  - 不是单独的 planner API
- `Stage B expansion`
  - 只有当模型选中了需要更多细节的能力时才发生
  - 比如 markdown-only skill 需要 `read SKILL.md`
  - 或工具结果回来了，需要模型继续整合结果

也就是说：

- `direct_answer`：通常只有一次主模型调用
- `ask_clarification`：通常只有一次主模型调用
- `use_tools`：一次主模型调用 + 正常 tool loop continuation
- `create_artifact`：一次主模型调用；只有在选中 markdown-only skill 或必须补充 detail 时，才有第二段 continuation

新设计要明确去掉独立 planner 这一跳，而不是再增加一个 routing LLM。

### 3.2 Continuation Loop Is Evidence-Driven

新主路径必须覆盖这种正常 agent loop：

1. 模型先看用户问题
2. 决定调用某个 tool
3. runtime 执行该 tool
4. 把 tool 结果回给模型
5. 模型基于新证据再判断：
   - 现在是否已经足够回答
   - 是否还需要另一个 tool
   - 是否应该直接生成最终答案或产物

它的本质是：

`LLM -> tool -> LLM -> tool -> LLM`

这里的关键要求是：

- continuation 必须由最新证据驱动
- 不是预先把候选工具全部跑完
- 每次是否继续调用下一个 tool，都由主模型在上一跳结果基础上重新决定
- runtime 只负责执行和守卫，不替模型预排一串工具队列

换句话说，正常的多工具任务应该表现为“逐步逼近目标”，而不是“把可能相关的工具都试一遍”。

### 4. Preserve Follow-Up Context By Default

`这些`、`上面`、`刚才`、`那个列表`、`这些申请` 这类 follow-up 不再主要靠规则猜测，而是默认保留最近上下文给模型判断。

只有在上下文过长或确定无关时，才做裁剪。

禁止当前这种行为：

- 本轮一旦判成 `use_tools`
- 就把 runtime history 清空
- 然后模型被迫重新猜“这些申请”到底是什么

### 5. Demote Metadata Recall To Candidate Compression

metadata recall 仍然保留，但角色调整为：

- 压缩候选 skill / tool 集合
- 提供 provider 偏好提示
- 帮助 prompt 控制长度

metadata recall 不再拥有：

- 主路由拍板权
- provider short-circuit 权
- final action override 权

### 6. Add Artifact-Aware Completion Checks

当用户目标属于产物生成时，finalize 必须校验目标是否满足：

- 用户要 PPT，必须进入 PPT / slides / write-to-file 路径
- 用户要文件，必须至少产出文件内容或明确进入写文件能力
- 用户要报告，必须产出结构化报告文本或文件

仅有 `session_status`、`smartcmp_list_services`、`smartcmp_list_components` 这类结构化查询结果，不能再被判定为“任务完成”。

### 7. Keep Runtime Guards, Not Planner Locks

保留并加强这些守卫：

- repeated tool loop detection
- no-progress detection
- timeout fallback
- before-tool-call validation
- tool result truncation / normalization
- final answer recovery

但这些守卫只负责止损，不负责抢主路由。

这里要补充两个 OpenClaw 里已经验证过的重要点：

1. `before_tool_call` 不只是校验，还可能：
   - block 某个 tool call
   - 调整 tool params
   - 记录 loop outcome

2. loop / no-progress 检测不能只看“同一个 tool 调了多少次”，还要结合：
   - 参数签名
   - 结果签名
   - ping-pong 模式

也就是说，AtlasClaw 的 runtime guards 不能退化成“简单计数器”，否则会比 OpenClaw 弱很多。

### 8. Stream Every LLM Re-Entry Explicitly

新主路径不只要正确，还要可观察。每次进入模型回合都必须有明确的流式状态输出，避免用户看到长时间静默。

要求如下：

- 初始进入模型时，必须有明确状态，如：
  - `Preparing model request context.`
  - `Starting model session.`
- 每次 tool 结果返回并重新进入模型 continuation 时，必须再次发出明确状态，如：
  - `Tool results received. Continuing reasoning with tool evidence.`
  - `Starting model continuation.`
- 如果是 recovery / invalid markup / artifact completion recovery，也必须有单独状态，不允许复用模糊的通用文案。

每次 LLM loop 建议至少带这些可观测字段：

- `loop_index`
- `loop_reason`
  - `initial_request`
  - `tool_result_continuation`
  - `invalid_markup_recovery`
  - `artifact_completion_recovery`
- `selected_capability_ids`

这层要求的目标不是加更多日志，而是保证：

- 用户能看到 agent 当前为什么又进入模型
- 调试时能区分“初始回合”和“工具后的 continuation”
- frontend 不会在长时间等待时只停留在模糊的 `Thinking`

### 9. Hooks And Session Semantics Must Survive Planner Removal

OpenClaw 的稳定性不只来自路由，还来自它没有在新链路里绕开原有 hooks 和 session 语义。AtlasClaw 也必须保留这层兼容。

要求如下：

- `llm_input` / `llm_output` / runtime trace hooks 继续在每次 LLM loop 上触发
- `before_prompt_build` 或同类 prompt 注入点不能因为 planner 删除而失效
- continuation 期间的 session history / transcript / pending tool results 不能乱序
- finalize 前要确保 pending tool results 已经完成落账，避免“工具其实还没收尾，但主流程先结束”

这里的原则是：

- planner removal 只改变“谁做意图决策”
- 不应该破坏现有 hooks、日志、session consistency、tool result pairing 这些底层语义

---

## Runtime Flow In The New Main Path

```text
1. Resolve session, thread, user, and recent conversation context
2. Build visible capability index
3. Optionally compress candidates with metadata recall
4. Prompt the main model with:
   - user request
   - recent conversation context
   - available capability index
   - runtime guardrails
5. Main model emits one of:
   - direct_answer
   - ask_clarification
   - use_tools
   - create_artifact
6. Runtime expands only the selected capabilities
7. Post phase verifies:
   - was the requested goal actually completed?
   - if not, can we recover in the same turn?
8. Stream final answer or artifact result
```

### Runtime Latency Expectations

- 大多数普通问答：1 次主模型调用
- 明显工具回合：1 次主模型调用 + tool continuation
- markdown-only skill / artifact detail expansion：最多再加 1 次定向 continuation

不允许的形态：

- planner model call + main model call 作为每轮默认前置
- 为了“先判断意图”而额外调用一个小模型
- 在没有选中 capability 前就去加载大量 skill 正文

---

## Required Code Changes

### A. Configuration

删除 planner-centric 主路径配置假设，只保留与 capability index 大小相关的可选控制项：

- `agent.max_indexed_skills`
- `agent.max_indexed_tools`
- `agent.max_capability_index_chars`

涉及文件：

- `app/atlasclaw/core/config_schema.py`
- `atlasclaw.json`
- `tests/atlasclaw.test.json`

### B. Routing Entry And Prepare Phase

把当前 prepare 阶段从“先决定工具政策”改成“先准备上下文和候选能力索引”。

涉及文件：

- `app/atlasclaw/agent/runner_tool/runner_execution_prepare.py`
- `app/atlasclaw/agent/runner_tool/runner_tool_gate_routing.py`
- 新增建议：`app/atlasclaw/agent/runner_tool/runner_llm_routing.py`

并显式删除热路径上的独立 planner model 调用：

- `tool_intent_plan_model_start`
- `tool_intent_plan_model_timeout`
- 以及与之绑定的强制前置 warning 文案

### C. Prompt Context And Capability Index

新增轻量 capability index 拼装逻辑，替代现在的 metadata-first prompt hint 角色。

涉及文件：

- `app/atlasclaw/agent/runner_prompt_context.py`
- `app/atlasclaw/agent/prompt_sections.py`
- `app/atlasclaw/skills/registry.py`

同时要显式保留：

- skill invocation policy filtering
- provider / group / plugin 可见性过滤
- capability index 截断诊断

### D. Main Model Decision Contract

定义主模型在新主路径下的决策输出契约，并让 runtime 消费它。

涉及文件：

- `app/atlasclaw/agent/runner_tool/runner_execution_runtime.py`
- `app/atlasclaw/agent/runner_tool/runner_execution_flow_stream.py`
- `app/atlasclaw/agent/runner_tool/runner_execution_flow_post.py`

其中必须覆盖正常 continuation loop：

- tool 执行后把结果回灌给主模型
- 主模型基于新证据决定是否继续调用工具
- 不预排固定工具序列
- 每次 continuation 都有独立 loop metadata 和流式状态
- 显式 runtime constraints 先于主模型决策生效

### E. Metadata Recall Demotion

保留 metadata recall，但只允许其参与候选压缩和提示，不允许其直接决定：

- action
- provider short-circuit
- artifact routing

涉及文件：

- `app/atlasclaw/agent/runner_tool/runner_tool_gate_routing.py`
- `app/atlasclaw/agent/runner_tool/runner_tool_gate_model.py`
- `app/atlasclaw/agent/runner_tool/runner_tool_gate_policy.py`

但 metadata recall 不能绕过：

- tool policy pipeline
- skill invocation eligibility
- explicit tool/capability constraints

### F. Artifact-Aware Completion

新增后置完成校验：

- 查询结果 != 产物完成
- 只有真实进入 artifact 路径，或产出 artifact 内容，才允许 finalize

涉及文件：

- `app/atlasclaw/agent/runner_tool/runner_execution_flow_post.py`
- `app/atlasclaw/agent/runner_tool/runner_execution_payload.py`

同时要确保 finalize 与 session/tool result pairing 一致，不允许在 pending tool results 尚未稳定时提前结束。

### G. Tests And E2E Corpus

新增中文多轮 E2E 和 contract tests：

- “查一个 cmp 所有待审批的申请” -> “将这些申请写入一个新的PPT”
- “我想查下上海周边的骑行公园”
- “把上面的结果保存成 markdown”
- “导出这些结果到一个新文件”

涉及文件：

- `tests/atlasclaw/test_runner_tool_gate_behavior.py`
- `tests/atlasclaw/test_runner_tool_execution_contract.py`
- `tests/atlasclaw/test_runner_prompt_context.py`
- 新增建议：`tests/atlasclaw/e2e/test_runtime_routing.py`

---

## Replacement Strategy

### Phase 1: Remove Planner Authority

- 去掉正式请求链路里的独立 planner 调用
- 去掉 metadata short-circuit 对 action 的覆盖
- 让主模型成为唯一的 turn-intent 决策入口

### Phase 2: Replace Prompt Capability Surface

- 把原来的工具/技能散装提示替换成统一 capability index
- 让模型先选 capability，再按需展开 detail
- 保证 follow-up 默认保留上下文

### Phase 3: Enforce Goal Completion

- 增加 artifact-aware completion gate
- 解决“查到了数据但没产出 PPT 却被判成功”

### Phase 4: Delete Planner-Centric Assumptions

- 删除 planner 相关日志、warning、超时处理主逻辑
- 更新 canonical docs，明确 AtlasClaw 不再依赖独立 planner

---

## Acceptance Criteria

1. 中文 follow-up “这些 / 上面 / 刚才” 默认承接上文，不因 `use_tools` 丢上下文。
2. “将这些申请写入一个新的PPT” 不会再被 SmartCMP metadata 直接抢走主路由。
3. 如果请求明显是 artifact 生成，finalize 前必须确认 artifact 路径真的被执行。
4. `powerpoint-pptx-1.0.1` 这类 skill 即使 metadata 是英文，也能以索引形式进入模型可见候选。
5. `web_search`、provider tools、session tools 的 loop / timeout / no-progress 守卫仍然有效。
6. 公开知识和推荐类问题允许主模型直接答，不再默认被 metadata/tool gate 锁死。
7. 正式请求链路里不再出现独立 planner model call。
8. 多工具任务以 `LLM -> tool -> LLM` 的 continuation 方式推进，而不是预跑候选工具列表。
9. 每次重新进入 LLM 时，前端都能看到明确的流式状态，且能区分初始回合与 continuation 回合。
10. capability index 只包含当前真正可用的 skills/tools，并带稳定的截断规则与诊断。
11. planner removal 后，hooks、session consistency、tool result pairing 语义保持完整。

---

## Non-Goals

本轮不包含：

- 提升 `web_search` 自身搜索质量
- 训练式 self-improve 记忆闭环
- 全量重写所有 skill metadata
