# OutcomeX × AgentSkillOS 真实接入现状（更新版）

本文档说明 `feat/phase1-integration` 当前已经完成的 AgentSkillOS 接入边界、真实验证结果，以及还没有完全收口的部分。

## 1. 当前已经落地的真实接入

### 1.1 真实 discovery / planning bridge
- OutcomeX 通过 `code/backend/app/integrations/agentskillos_bridge.py` 真实调用本地 `AgentSkillOS` checkout；
- 调用目标仍是 `workflow.service.discover_skills(...)`，不是伪造结果；
- 调用时会把 AgentSkillOS 的 discovery LLM 环境切到 OutcomeX 控制的 DashScope / 百炼兼容接口：
  - `LLM_MODEL`
  - `LLM_BASE_URL`
  - `LLM_API_KEY`
  - `OPENAI_BASE_URL`
  - `OPENAI_API_KEY`
- 当前 bridge 默认 discovery timeout 已提升到 `120s`，避免把真实慢搜索误判成失败。

### 1.2 Wrapper 已消费真实 discovery 输出
- `code/backend/app/execution/agentskillos_wrapper.py`
  - 会先调用 `AgentSkillOSBridge.discover_skills(...)`；
  - 再把真实 discovery 输出写入 execution metadata；
  - 关键 metadata：
    - `planning_source`
    - `agentskillos_skill_ids`
    - `agentskillos_error`
    - `agentskillos_repo_root`

### 1.3 AgentSkillOS runtime 已新增 OpenAI-compatible 执行后端
- 参考代码 `reference-code/AgentSkillOS/src/orchestrator/runtime/client.py` 不再只绑定 `ClaudeSDKClient`；
- 现在 `SkillClient` 是一个 facade：
  - 当存在 `LLM_API_KEY / OPENAI_API_KEY` 时，默认走新的 `openai` tool-loop backend；
  - 否则保留 `claude` backend 作为兼容 fallback；
- `src/skill_orchestrator/client.py` 已改成复用同一份 runtime client，避免双份实现继续漂移；
- `src/config.py` 的默认 runtime model 也已优先读取：
  - `AGENTSKILLOS_RUNTIME_MODEL`
  - `LLM_MODEL`
  - `OPENAI_MODEL`
  - 最后才是 `ANTHROPIC_MODEL`

## 2. 这次 runtime 替换具体做了什么

新的 OpenAI-compatible runtime backend 提供了 AgentSkillOS 当前 MVP 需要的最小工具闭环：

- `Bash`
- `Read`
- `Write`
- `Edit`
- `Glob`
- `Grep`
- `Skill`

其中：
- `Skill` 不再依赖 Claude 的原生 Skill tool，而是读取运行目录下 `.claude/skills/<name>/SKILL.md` 与本地文件列表；
- LLM 可以先读 skill 说明，再用 `Bash / Read / Write` 自行完成执行；
- 这让 AgentSkillOS 在 OutcomeX 注入的 DashScope/Qwen 环境下，已经可以跑通一类真实的 tool-using delivery 流程。

## 3. 高风险 skill script 已完成 provider boundary 替换

### 3.1 `generate-image` 已从 OpenRouter 改成 DashScope 边界
- 原文件：
  - `reference-code/AgentSkillOS/data/skill_seeds/generate-image/scripts/generate_image.py`
- 之前问题：
  - 直接打 `https://openrouter.ai/api/v1/chat/completions`
  - 自己读 `OPENROUTER_API_KEY`
  - 直接绕过 OutcomeX provider policy
- 现在：
  - 改成只走 DashScope 兼容密钥与 endpoint；
  - 默认读取：
    - `DASHSCOPE_API_KEY`
    - `OUTCOMEX_DASHSCOPE_API_KEY`
    - `OPENAI_API_KEY`
    - `LLM_API_KEY`
  - 请求走 DashScope 图片生成异步接口；
  - 真实 smoke 已生成图片文件。

### 3.2 `mcp-builder` evaluation 已从 Anthropic 改成 OpenAI-compatible tool loop
- 原文件：
  - `reference-code/AgentSkillOS/data/skill_seeds/mcp-builder/scripts/evaluation.py`
- 之前问题：
  - 直接 `Anthropic()`
  - 直接 `client.messages.create(...)`
- 现在：
  - 改成 `OpenAI(...)` + OpenAI-compatible tool-calling loop；
  - MCP tool schema 会转换成 OpenAI function tool 格式；
  - 继续支持多轮 tool use，但 provider 出口已不再锁死 Anthropic。

## 4. 真实验证结果

### 4.1 AgentSkillOS runtime tool-loop smoke
已用 DashScope 实跑：
- backend: `AGENTSKILLOS_RUNTIME_BACKEND=openai`
- model: `qwen3.6-plus`
- 任务：让模型调用 `Bash` 运行 `printf hello`，再 `Write` 创建 `done.txt`

结果：
- 返回 `OK`
- `done.txt` 成功创建
- 文件内容为 `HELLO_CONFIRMED`
- metrics 正常返回

这说明新的 OpenAI-compatible runtime 已经能完成真实的“模型 -> 工具调用 -> 本地落盘 -> 回答”闭环。

### 4.2 `generate-image` DashScope smoke
已用重写后的脚本真实实跑：
- 输出文件：`/tmp/outcomex-generate-image-smoke.png`
- 文件大小：约 `2.1M`

这说明新的 image skill script 已不再只是“代码改了”，而是真正通过 DashScope provider boundary 生图成功。

### 4.3 OutcomeX provider 聚焦测试
修改 `code/backend/app/integrations/providers/dashscope.py` 后，已跑：
- `tests/execution/test_dashscope_provider_adapter.py`

结果：
- `3 passed`

## 5. 当前仍然没有完全收口的部分

### 5.1 `canvas-design` 仍是 instruction-only skill
- `reference-code/AgentSkillOS/data/skill_seeds/canvas-design/SKILL.md`
- 它本质上仍是一份“设计哲学 + 渲染要求”的长说明；
- 目录里有大量字体资源，但没有稳定的本地 poster composer / PDF renderer；
- 所以如果要把 poster/image delivery 做成稳定产线，仍建议：
  - OutcomeX 自己补一个本地 composer；
  - 或让 wrapper 直接把这类请求标准化成 OutcomeX 自己的 image generation recipe。

### 5.2 `generate-image` 当前只确保生图路径
- 当前改造后的脚本已经验证了 text-to-image；
- 但 image editing 还没有在 DashScope 边界下做完整适配；
- 也就是说，“生成”已经真实可用，“编辑”仍属于下一阶段增强。

### 5.3 Retrieval 还没有完全走 OutcomeX `ModelRouter`
- `discover_skills(...)` 已切到 OutcomeX 注入的 DashScope 环境；
- 但 AgentSkillOS 内部 retrieval 仍主要通过 LiteLLM 直接打 compatible endpoint；
- 所以它已经不再锁死原 provider，但也还不是“每一次内部模型调用都经过 OutcomeX 后端 API”。

## 6. 一句话结论

现在的状态已经从：
- “只有真实 discovery bridge”

推进到：
- “真实 discovery bridge + 可运行的 OpenAI-compatible runtime backend + 已替换的高风险 skill script”

也就是说：
- AgentSkillOS 已经开始具备在 OutcomeX 控制边界里跑通一段真实 `input -> tool use -> delivery` 的能力；
- 但要把所有复杂 skill 都做成稳定产线，仍需要继续补：
  - poster/composer 类本地执行器
  - image editing / video 等更完整的多模态执行适配
  - retrieval 到 OutcomeX `ModelRouter` 的进一步收口
