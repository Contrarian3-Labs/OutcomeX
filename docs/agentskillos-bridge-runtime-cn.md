# OutcomeX × AgentSkillOS 真实接入现状

本文档说明 `feat/phase1-integration` 分支中，OutcomeX 与本地 `AgentSkillOS` 参考代码的真实接入边界，以及为什么当前先接入真实 discovery / planning，而不是直接把整个 runtime 原样嵌入。

## 1. 当前已经实现的真实接入

### 1.1 真实 AgentSkillOS discovery bridge
- OutcomeX 新增了 `code/backend/app/integrations/agentskillos_bridge.py`；
- 该 bridge 不再只是“参考 AgentSkillOS 思路”，而是会通过 subprocess 真实调用本地 `AgentSkillOS` checkout；
- 调用的目标是 `workflow.service.discover_skills(...)`；
- 由于真实 `discover_skills(...)` 在 `skill_seeds` 上一次完整搜索实测约 `88s`，bridge 默认超时已提高到 `120s`，避免把“慢搜索”误判为“桥接失败”；
- 调用时会把 AgentSkillOS 的 LLM 环境变量切到 OutcomeX 控制的 DashScope / 百炼兼容接口：
  - `LLM_MODEL`
  - `LLM_BASE_URL`
  - `LLM_API_KEY`
  - `OPENAI_BASE_URL`
  - `OPENAI_API_KEY`

换句话说，AgentSkillOS 的真实 skill retrieval 已经能够纳入 OutcomeX 自己控制的模型入口，而不是继续走它原始默认 provider。

### 1.2 Wrapper 已经消费真实 discovery 结果
- `code/backend/app/execution/agentskillos_wrapper.py`
  - 现在会先调用 `AgentSkillOSBridge.discover_skills(...)`；
  - 再把 skill discovery 结果写进 execution metadata；
  - metadata 中会保留：
    - `planning_source`
    - `agentskillos_skill_ids`
    - `agentskillos_error`
    - `agentskillos_repo_root`

因此，OutcomeX 当前的 `plan()` 已经不只是本地 deterministic normalizer，而是“OutcomeX 自己的 recipe + 真实 AgentSkillOS discovery”的组合。

## 2. 为什么没有直接接整个 `run_task()`

核心原因不是 retrieval，而是 **runtime execution**。

### 2.1 AgentSkillOS 的 runtime 仍然绑定 Claude SDK
在参考代码里：
- `reference-code/AgentSkillOS/src/orchestrator/runtime/client.py`
  - 直接使用 `ClaudeSDKClient`
- `reference-code/AgentSkillOS/src/config.py`
  - runtime model 默认仍依赖 `ANTHROPIC_MODEL`
- `reference-code/AgentSkillOS/config/config.yaml`
  - orchestrator runtime 默认模型仍是 `sonnet`

这意味着：
- 如果直接把 `workflow.service.run_task(...)` 原样接进 OutcomeX；
- 那么 skill retrieval 可以走 OutcomeX / 百炼；
- 但真正执行 DAG / free-style / no-skill 时，runtime 还是会落回 Claude SDK 体系。

这与 OutcomeX 当前的产品原则冲突：
- OutcomeX 必须统一掌控模型政策；
- AgentSkillOS 不应该自己决定最终 provider；
- 任何 delivery 相关调用都不应绕过 OutcomeX 的 ModelRouter。

### 2.2 一些 skill script 仍然会直连外部 provider
例如：
- `reference-code/AgentSkillOS/data/skill_seeds/generate-image/SKILL.md`
- `reference-code/AgentSkillOS/data/skill_seeds/generate-image/scripts/generate_image.py`

这些脚本当前仍默认依赖 OpenRouter / Gemini / FLUX。

所以即使强行接通 `run_task()`：
- runtime 侧可能走 Claude SDK；
- skill script 侧还可能直接打 OpenRouter；
- OutcomeX 就失去统一控制。

## 3. 当前最真实、最安全的集成方式

因此目前采取的是两段式集成：

### 第一段：真实接 AgentSkillOS 的 discovery / planning 能力
- 真实调用本地 AgentSkillOS 的 skill retrieval；
- 把它的树搜索 / skill selection 结果带回 OutcomeX；
- 并且将其模型入口切到 DashScope / 百炼兼容接口。

### 第二段：执行与交付仍由 OutcomeX 控制
- recipe normalization 仍在 OutcomeX；
- provider routing 仍由 OutcomeX `ModelRouter` 决定；
- 文本 / 图像 / 视频仍走 OutcomeX 自己的 DashScope provider adapter；
- preview、runtime pressure、payment、settlement 全在 OutcomeX 控制面里。

这保证：
- AgentSkillOS 提供“怎么找解法 / 怎么组合 skill”；
- OutcomeX 决定“最终调用谁 / 怎么交付 / 怎么结算”。

## 4. 当前状态到底算不算“真实接入”

算，但要精确定义：

### 已经真实的部分
- 本地 AgentSkillOS checkout 被真实调用；
- 真实 `discover_skills(...)` 被 OutcomeX wrapper 使用；
- discovery 阶段的模型调用已切入 OutcomeX / 百炼兼容入口；
- metadata 中已经能看到真实 AgentSkillOS discovery 的输出。

### 还未真实替换完的部分
- `workflow.service.run_task(...)` 的 runtime execution 还没有被 OutcomeX 接管；
- `ClaudeSDKClient` 还没有换成 OutcomeX 自己的 runtime client；
- 会直连 OpenRouter / 旧 provider 的 skill script 还没有统一改造。

## 5. 下一步正确方向

要把 AgentSkillOS 真正推进到 `input -> orchestration -> delivery` 且不绕过 OutcomeX，需要再做两件事：

### 5.1 替换 runtime client
- 目标：不要再让 AgentSkillOS 的执行层直接使用 `ClaudeSDKClient`；
- 应改成 OutcomeX 控制的 runtime client / tool runner；
- 这样 DAG / free-style / no-skill 的执行模型和工具调用才会落在 OutcomeX 控制面里。

### 5.2 改造会直连 provider 的 skill script
- 尤其是 image / video 相关 skill；
- 这些脚本应改成只通过 OutcomeX 的 provider boundary 发请求；
- 不能继续自己读 `OPENROUTER_API_KEY`、直接调用 OpenRouter。

## 6. 一句话结论

当前 OutcomeX 已经实现：
- **真实调用 AgentSkillOS 做 skill discovery / planning**
- **并把 discovery 阶段模型调用切到百炼兼容入口**

但还没有实现：
- **把 AgentSkillOS 的 runtime delivery 全部替换成 OutcomeX 自己控制的执行内核**

所以现在是：
- 真实接入已经开始；
- 但离“完整 input -> delivery 且全程不绕过 OutcomeX”还差 runtime client 和 skill script 两步。
