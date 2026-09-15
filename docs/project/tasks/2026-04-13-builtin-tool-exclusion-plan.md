# Built-in Tool Exclusion Implementation Plan

## Scope
- 增加 `skills.tools_exclusive` 反向排除配置。
- 收敛 `allow_script_execution` 到本地高风险内建 FS / runtime 工具。
- 保持 markdown skill / provider skill 默认开启，不为它们增加新开关。
- 确保注册表、API、prompt、最小工具集和执行链路一致反映关闭结果。

## Steps
1. [x] 更新 config schema 与示例配置
2. [x] 补齐 `delete` 注册与 `group:fs` 对齐
3. [x] 在 built-in registration 阶段实现 `tools_exclusive` + `allow_script_execution` 过滤
4. [x] 对齐 `/api/skills` 与运行时工具快照
5. [x] 补齐测试并完成回归验证

## Verification
- command: `pytest tests/atlasclaw/test_tool_catalog.py -q`
- command: `pytest tests/atlasclaw/test_skills_api_metadata.py -q`
- command: `pytest tests/atlasclaw -q`
- expected: 被排除工具不会注册、不会出现在 API 和 prompt 可见工具集中；markdown/provider skill 默认不受影响
- actual:
  - `pytest tests/atlasclaw/test_builtin_tool_registration.py tests/atlasclaw/test_tool_catalog.py tests/atlasclaw/test_md_skills.py tests/atlasclaw/test_provider_tool_groups.py tests/atlasclaw/test_skills_api_metadata.py -q`
  - result: `70 passed`
  - `pytest tests/atlasclaw -q`
  - result: `1096 passed, 9 skipped`

## Handoff Notes
- State: `docs/project/state/current.md`
- Spec: `docs/project/specs/2026-04-13-builtin-tool-exclusion-design.md`
- Completed: state/task/spec 对齐、实现、双 review、后端全量回归
