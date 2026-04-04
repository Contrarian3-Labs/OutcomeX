# AgentSkillOS 内部模型调用审计

本文档记录当前参考仓库 `reference-code/AgentSkillOS` 中，哪些代码路径会直接调用外部模型或提供商，从而绕过 OutcomeX 的 `ModelRouter`。

## 1. 总结

当前 AgentSkillOS 的外部模型调用分成三类：

### 1.1 Runtime execution
- 主要通过 `ClaudeSDKClient`；
- 负责 DAG / direct / free-style 的实际执行；
- 这是当前最关键的“未被 OutcomeX 接管”的部分。

### 1.2 Retrieval / capability tree / vector search
- 主要通过 `LiteLLM`；
- 用于能力树构建、树搜索、vector embedding、recipe embedding；
- 可以通过 OpenAI-compatible 接口重定向，但依旧不是 OutcomeX 的 `ModelRouter`。

### 1.3 Skill script 级别的 provider 直连
- 某些 skill 脚本会自己直连 OpenRouter 或 Anthropic SDK；
- 这部分也会绕过 OutcomeX。

## 2. Runtime 层：Claude SDK

### 2.1 主运行时 client
- `reference-code/AgentSkillOS/src/orchestrator/runtime/client.py`
  - `SkillClient.connect()`
  - `SkillClient.execute()`
  - `SkillClient.execute_with_metrics()`
  - `SkillClient.stream_execute()`
- 直接依赖 `ClaudeSDKClient`。

### 2.2 三个执行引擎都会走这条路径
- `reference-code/AgentSkillOS/src/orchestrator/dag/engine.py`
- `reference-code/AgentSkillOS/src/orchestrator/direct/engine.py`
- `reference-code/AgentSkillOS/src/orchestrator/freestyle/engine.py`

这意味着：
- 只要真的跑 execution / delivery；
- 最终运行时就仍然落在 Claude SDK；
- OutcomeX 目前还没有接管这条 runtime 路径。

## 3. Retrieval 层：LiteLLM

### 3.1 Completion
- `reference-code/AgentSkillOS/src/manager/tree/builder.py`
- `reference-code/AgentSkillOS/src/manager/tree/searcher.py`
- `reference-code/AgentSkillOS/src/manager/tree/layer_processor.py`
- `reference-code/AgentSkillOS/src/skill_retriever/tree/builder.py`
- `reference-code/AgentSkillOS/src/skill_retriever/search/searcher.py`

### 3.2 Embedding
- `reference-code/AgentSkillOS/src/manager/vector/indexer.py`
- `reference-code/AgentSkillOS/src/manager/vector/searcher.py`
- `reference-code/AgentSkillOS/src/manager/tree/dormant_indexer.py`
- `reference-code/AgentSkillOS/src/manager/tree/dormant_searcher.py`
- `reference-code/AgentSkillOS/src/web/recipe.py`

这类调用并不一定是问题本身，因为它们可以切到 DashScope/OpenAI-compatible endpoint；
但它们依旧绕过 OutcomeX 的统一 `ModelRouter`，所以只能算“可重定向”，不能算“已被 OutcomeX 真正接管”。

## 4. Skill script 层：直接外呼

### 4.1 OpenRouter 图片脚本
- `reference-code/AgentSkillOS/data/skill_seeds/generate-image/scripts/generate_image.py`
- 直接请求 `https://openrouter.ai/api/v1/chat/completions`
- 默认模型包括：
  - `google/gemini-3-pro-image-preview`
  - `black-forest-labs/flux.2-pro`
  - `black-forest-labs/flux.2-flex`

### 4.2 Anthropic SDK 脚本
- `reference-code/AgentSkillOS/data/skill_seeds/mcp-builder/scripts/evaluation.py`
- 直接使用 `Anthropic()` 与 `client.messages.create(...)`

这类 skill script 是最典型的“绕过 OutcomeX provider 边界”的路径。

## 5. OutcomeX 当前已经做到什么

在 `feat/phase1-integration` 中：
- OutcomeX 已经新增 `AgentSkillOSBridge`；
- 真实调用了本地 AgentSkillOS 的 `discover_skills(...)`；
- discovery 阶段会把 AgentSkillOS 的 LLM 环境变量切到 DashScope / 百炼兼容入口；
- 这意味着：
  - **retrieval / planning 阶段已经开始被 OutcomeX 接管**
  - **runtime delivery 阶段仍未被 OutcomeX 完全接管**

## 6. 一句话结论

如果目标是“让 AgentSkillOS 真正成为 OutcomeX 内部的 orchestration engine，并且全程不绕过 OutcomeX”：

还必须继续做两件事：
- 替换 `ClaudeSDKClient` runtime
- 改造会直连 OpenRouter / Anthropic 的 skill script
