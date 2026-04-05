# OutcomeX AgentSkillOS + 模型路由目标架构

## 总体概览
OutcomeX 后端统一接管所有模型能力调用，确保模型政策、机型能力、成本估算、预览裁剪与结算流程掌握在自己的控制面板里；而 `AgentSkillOS` 仅作为内部 orchestration/wrapper 思路存在，负责“怎么做”的规划与任务串联，所有模型请求都不得绕开 OutcomeX 的路由层。

## 组件边界

### OutcomeX 后端统一模型控制
- 接收 `IntentRequest`、机器能力与报价约束，决定是否可以执行某个目标；
- 通过 `ModelRouter` / `ProviderRouter` 统一驱动模型调用，收集 latency、cost、失败率等数据回写 `RuntimeCostService` 与 solution memory；
- 依据 OutcomeX policy 决定允许哪些模型家族、哪些能力、哪台机器可以参与执行，并负责最终的预览生成、人工确认与链上结算。

### AgentSkillOS Wrapper
- 新增 `AgentSkillOSWrapper`，将 OutcomeX 的 `IntentRequest` 映射成 AgentSkillOS 的任务输入，依靠它提供的 retrieval / orchestration / solution reference 接口做步骤规划；
- 将 `AgentSkillOS` 的输出重构为 OutcomeX 指定的 `ExecutionPlan`/`ExecutionRecipe`/step list/preview 等结构，并提供 artifact 参考；
- 在 wrapper 内部拦截所有模型请求并转发给 OutcomeX 的 `ModelRouter`，避免 AgentSkillOS 直接绑定原生 provider。

### ModelRouter + Provider 层
- `ModelRouter` 是 OutcomeX 面向模型选择的唯一出口，依据 `model family / machine capability / policy whitelist / cost budget` 选出一个 provider；
- 不同调用方（AgentSkillOS、直接执行、手动 recipe）都必须走这层，便于统一打点、cache、降级与追踪；
- 下游的 provider 接口实现（`BailianProvider`、MuleRouter、Alibaba 等）通过配置注册到 provider 层，可以快速加入新的本地模型或私有网关；
- 所有 provider response 要返回标准化的 cost / latency / failure metadata，供 `RuntimeCostService` 与 solution memory 做动态评分。

## 百炼与多模态路由
- 文本 / 多模态理解请求默认由 `百炼`（Bailian）能力平台承接，因其已具备大语言＋多模态融合的语义理解模块；对应的 provider adapter 会在 router 中注册 `BailianText`、`BailianVision`、`BailianVideo` 等能力；
- 图像、视频、Speech 等任务同样通过 provider 层而非 AgentSkillOS 直连，大多数情况落在百炼能力上，也可以通过配置切换为科研模型、内推小模型或外部厂商；
- 任何新增能力都由配置驱动：在 provider registry 中声明名称、能力标签、cost policy；`ModelRouter` 依据路由规则将对应请求转给 `百炼` 或替代 provider；
- 这样一来，OutcomeX 既能使用百炼统一理解多模态输出，也能在必要时对接私有图/影任务或本地推理机，保持战略灵活性。

## 典型调用流程
1. 用户在 chat 发起需求，后端形成 `IntentRequest` 并调用 `PlanService`/`RuntimeCostService`，准备约束与白名单；
2. `AgentSkillOSWrapper.plan(...)` 被触发，传入上述上下文并让 AgentSkillOS 给出多步骤 solution；
3. AgentSkillOS 每次需要模型能力时，由 wrapper 将请求送到 OutcomeX 的 `ModelRouter`，路由层返回一个 Provider adapter（默认落在百炼或可配置 provider）；
4. Provider 执行完模型推理后把结果、cost metadata 回传给 wrapper，`ExecutionPlan` 与 artifact 被组装；
5. OutcomeX 再根据 preview policy 生成推荐结果，按需走 confirm/settle 流程，所有模型调用都被 runtime + cost guard 记录，AgentSkillOS 不直接管理模型源；
6. 任何非 AgentSkillOS 展开的 scenario（直接执行、手动 recipe）也复用 `ModelRouter`，确保路由规范一致。

## 期待的效果
- AgentSkillOS 专注编排与方案设计，OutcomeX 全面掌控模型政策、成本与交付质量；
- 通过 `ModelRouter` + provider registry + `百炼` 多模态能力，文字、图像、视频都拥有可控的一致路由；
- 新 provider 可以通过配置接入，支持私有模型、科研模型与未来的本地推理机，同时保留对运行成本、预览与结算的统一管控；
- 这个架构为未来插件化 provider、RuntimeCostService 智能调度与 settlement 微调奠定基础。
