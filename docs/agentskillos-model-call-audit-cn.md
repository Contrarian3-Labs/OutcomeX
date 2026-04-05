# AgentSkillOS 内部模型调用审计（更新版）

本文档记录 `reference-code/AgentSkillOS` 当前仍然存在的模型/提供商调用边界，以及哪些部分已经被本轮改造收口。

## 1. 已收口的部分

### 1.1 Runtime execution 默认不再锁死 Claude SDK
- 文件：
  - `reference-code/AgentSkillOS/src/orchestrator/runtime/client.py`
  - `reference-code/AgentSkillOS/src/skill_orchestrator/client.py`
- 变化：
  - `SkillClient` 已从“只会 `ClaudeSDKClient`”变成 facade；
  - 当存在 `LLM_API_KEY / OPENAI_API_KEY` 时，默认走新的 OpenAI-compatible tool loop；
  - 只有在没有这些环境变量时，才退回 Claude backend。

这意味着：
- `dag / direct / free-style / unified_service` 这些执行路径，不再天然把 OutcomeX 排除在外；
- OutcomeX 可以通过注入 DashScope/Qwen 兼容环境，接管 runtime 模型出口。

### 1.2 直接外呼 OpenRouter / Anthropic 的两个高风险脚本已替换

#### 已替换 1：图片脚本
- 文件：
  - `reference-code/AgentSkillOS/data/skill_seeds/generate-image/scripts/generate_image.py`
- 旧状态：
  - 直连 OpenRouter
  - 读 `OPENROUTER_API_KEY`
- 新状态：
  - 走 DashScope image-generation boundary
  - 读 DashScope/OutcomeX 兼容环境变量

#### 已替换 2：MCP evaluation 脚本
- 文件：
  - `reference-code/AgentSkillOS/data/skill_seeds/mcp-builder/scripts/evaluation.py`
- 旧状态：
  - `Anthropic()`
  - `client.messages.create(...)`
- 新状态：
  - `OpenAI(...)`
  - OpenAI-compatible function tool loop

## 2. 仍未完全收口的部分

### 2.1 Retrieval / search / embedding 仍主要是 LiteLLM 直连 compatible endpoint
- 代表文件：
  - `reference-code/AgentSkillOS/src/manager/tree/builder.py`
  - `reference-code/AgentSkillOS/src/manager/tree/searcher.py`
  - `reference-code/AgentSkillOS/src/manager/tree/layer_processor.py`
  - `reference-code/AgentSkillOS/src/manager/vector/indexer.py`
  - `reference-code/AgentSkillOS/src/manager/vector/searcher.py`
  - `reference-code/AgentSkillOS/src/web/recipe.py`

这部分现在可以通过环境变量切到 DashScope / 百炼 compatible endpoint，但仍然不是 OutcomeX 后端 API 层面的 `ModelRouter` 收口。

### 2.2 指令型 skill 还依赖模型自己理解 `SKILL.md`
- 代表文件：
  - `reference-code/AgentSkillOS/data/skill_seeds/canvas-design/SKILL.md`
- 问题：
  - 这类 skill 没有稳定脚本；
  - 即使 runtime 已有 OpenAI-compatible tool loop，也仍依赖模型读说明、自己调用工具完成执行；
  - 对 poster / composer / PDF 这种强可控输出，还缺本地确定性执行器。

### 2.3 多模态能力还没有全部做成 OutcomeX 级 provider registry
- 目前真实跑通的是：
  - text runtime tool loop
  - image generation script
- 还没完全做完的是：
  - image editing
  - video generation
  - richer skill-specific execution wrappers

## 3. 对 OutcomeX 的实际意义

### 已经成立的结论
- AgentSkillOS 不再只能活在 Claude SDK 世界里；
- OutcomeX 已经可以把它拉进 DashScope / 百炼兼容运行时；
- 至少一条真实 delivery 路径已经被实跑验证：
  - runtime tool loop 可用
  - `generate-image` 可真实生图

### 还不能过度宣称的地方
- 不能说 AgentSkillOS 所有内部模型调用都已经经过 OutcomeX `ModelRouter`；
- 不能说所有 skill 都已经是稳定的、确定性的 delivery pipeline；
- 不能说 poster / video / editing 已全部完成 OutcomeX 级收口。

## 4. 一句话结论

这轮改造之后：
- **最大的三个绕过点里，已经解决了两个半**

具体说：
- runtime 默认 Claude 绑定：已基本解除
- OpenRouter 图片脚本：已替换
- Anthropic evaluation 脚本：已替换
- retrieval / embedding：仍是“可重定向，但未完全后端收口”
