# OutcomeX 当前统一后端状态说明

本文档描述 `feat/bailian-migration` 分支在合并百炼执行链路与支付控制面之后的当前后端状态，便于后续继续实现合约直连、HSP 正式接入与真实 AgentSkillOS orchestration。

## 1. 当前已经合并的两条主线

### 1.1 百炼执行主线
- OutcomeX 后端已经不再依赖原先的 `builtin/text-fast` 或旧的 `alibaba-mulerouter` 占位调用；
- 文本、图像、视频的默认模型能力由 OutcomeX 后端统一路由到 DashScope / 百炼 provider；
- `AgentSkillOSWrapper` 仅保留 orchestration / planning 边界，不直接绑定任何外部模型源。

### 1.2 支付控制面主线
- 后端已经具备 chat-plan 报价、payment intent、HSP webhook、settlement start、on-chain write 边界等核心控制流；
- `RuntimeCostService` 负责统一报价输出；
- `OrderWriter` 负责把订单创建、支付确认、preview ready、confirm result、settle order 等状态变更映射为统一链上写入动作。

## 2. 当前后端的核心模块

### 2.1 API 层
- `code/backend/app/api/routes/chat_plans.py`
  - 负责 chat-native plan recommendation 与 quote 输出；
- `code/backend/app/api/routes/orders.py`
  - 负责创建订单、写入 execution metadata、进入 on-chain `createOrder` 写入边界；
- `code/backend/app/api/routes/payments.py`
  - 负责创建 payment intent、mock confirm，以及支付完成后的订单状态冻结；
- `code/backend/app/api/routes/hsp_webhooks.py`
  - 负责接收 HSP 回调、校验签名、保证 callback 幂等；
- `code/backend/app/api/routes/settlement.py`
  - 负责确认订单后启动 settlement 流程。

### 2.2 执行与模型路由层
- `code/backend/app/execution/agentskillos_wrapper.py`
  - 把用户意图转成 OutcomeX 的 execution recipe；
  - 只保留 wrapper / orchestration 边界；
- `code/backend/app/execution/normalizer.py`
  - 把 text / image / video 请求归一化到默认模型配置；
- `code/backend/app/execution/service.py`
  - 执行入口，统一连接 wrapper、preview policy、hardware simulator、model router、provider adapter；
- `code/backend/app/integrations/model_router.py`
  - 模型选择的唯一出口；
- `code/backend/app/integrations/providers/dashscope.py`
  - 当前默认 provider adapter；
  - 文本走 DashScope OpenAI-compatible `/chat/completions`；
  - 图像 / 视频走 DashScope 异步任务接口。

### 2.3 支付与链上写入层
- `code/backend/app/runtime/cost_service.py`
  - 负责 deterministic quote；
- `code/backend/app/integrations/hsp_adapter.py`
  - 当前的 HSP merchant-order / webhook 形态边界；
- `code/backend/app/onchain/order_writer.py`
  - 负责构造统一链上写入 payload；
- `code/backend/app/onchain/contracts_registry.py`
  - 负责链上目标合约信息配置；
- `code/backend/app/domain/models.py`
  - 已包含支付回调追踪、execution metadata、settlement policy 等字段。

## 3. 当前完整请求路径

### 3.1 Chat 规划与报价
1. 用户发起 chat 请求；
2. `chat_plans` 路由调用 plan summary + `RuntimeCostService`；
3. 返回推荐计划、报价与基础交付结构；
4. 这一阶段仍由后端掌握推荐逻辑，而不是把 workflow internals 直接暴露给用户。

### 3.2 创建订单
1. 用户确认某个推荐方案；
2. `orders` 路由创建 `Order`；
3. 后端调用 `ExecutionEngineService.plan(...)` 生成 execution metadata；
4. execution metadata 被保存到订单中，包括：
   - planner
   - primary output
   - match status
   - selected provider
   - selected model
5. 同时调用 `OrderWriter.create_order(order)`，将订单进入统一链上写入边界。

### 3.3 创建支付意图
1. 用户对订单发起支付；
2. `payments` 路由调用 `HSPAdapter.create_payment_intent(...)`；
3. 支付 intent 中会包含 merchant-order / flow 等 provider 侧追踪标识；
4. 订单的 settlement beneficiary / self-use / dividend eligibility 会在支付完成时冻结。

### 3.4 支付成功与回调
1. HSP webhook 到达 `hsp_webhooks`；
2. 后端校验签名；
3. 后端根据 callback id 做幂等检查；
4. 若支付成功，则更新 payment/order 状态；
5. 同时调用 `OrderWriter.mark_order_paid(order, payment)` 进入链上写入边界。

### 3.5 执行与预览
1. OutcomeX 通过 `ExecutionEngineService` 处理执行；
2. `AgentSkillOSWrapper` 只负责 recipe / orchestration 边界；
3. `ModelRouter` 根据输出类型、preferred model、family whitelist 等决定实际 provider；
4. 当前默认 provider 为 DashScope：
   - text -> `qwen3.6-plus`
   - image -> `wan2.6-t2i`
   - video -> `wan2.2-t2v-plus`
5. preview policy 根据 runtime pressure 决定 preview 形态；
6. 当 preview ready 时，通过 `OrderWriter.mark_preview_ready(order)` 写入链上边界。

### 3.6 用户确认结果与结算
1. 用户确认结果；
2. `orders/{id}/confirm-result` 会检查：
   - payment 是否足额成功
   - result 是否 ready
   - settlement policy 是否已冻结
3. 满足条件后，订单进入 `RESULT_CONFIRMED` / `READY`；
4. 同时调用 `OrderWriter.confirm_result(order)`；
5. `settlement` 路由再根据 10% / 90% 规则启动结算，并通过 `OrderWriter.settle_order(order, settlement)` 进入链上写入边界。

## 4. 当前产品真相在后端中的体现

### 4.1 Users buy outcomes, not tools
- 用户面对的是 chat、plan recommendation、quote、order、result confirmation；
- 用户不会直接看到 workflow 编排细节；
- execution metadata 存在后端与内部状态中，不作为前台复杂配置暴露。

### 4.2 Machine side earns only after confirmed work
- 只有 payment 成功且 result confirmed 后，settlement 才会进入可启动状态；
- 这保证 revenue 分配绑定在 confirmed work 上，而不是 idle hardware narrative 上。

### 4.3 Settlement policy freezes at payment time
- beneficiary / self-use / dividend eligibility 不是确认结果时才临时判断；
- 而是在支付成功时冻结，避免后续订单生命周期中被篡改。

### 4.4 Transfer / revenue guard 的后端基础已具备
- 机器侧 `has_active_tasks` / `has_unsettled_revenue` 已存在于数据模型；
- 当前后端已具备为 NFT transfer guard 提供状态依据的数据库结构；
- 后续只需把这些状态正式联动到合约或 indexer guard。

## 5. 当前实现仍然是“真实边界 + 部分模拟”

### 已经真实化的部分
- 后端 API 与状态流；
- execution metadata 写入；
- DashScope provider adapter 结构；
- quote / payment intent / webhook / order writer；
- deterministic on-chain write payload；
- settlement 启动与收益切分基本逻辑。

### 仍是 mock / placeholder 的部分
- HSP 尚未完全接到真实链上支付流程；
- `OrderWriter` 目前输出的是 deterministic write payload / tx metadata，而非真实交易广播；
- DashScope provider adapter 已是真实 HTTP 边界，但是否直接打生产模型仍取决于环境变量与 key；
- `AgentSkillOSWrapper` 当前仍是 OutcomeX 侧 orchestration facade，还没有把真实 `AgentSkillOS workflow.service.run_task(...)` 完整接入。

## 6. 当前分支的意义

当前 `feat/bailian-migration` 已经形成一个统一后端基线：
- 支付控制面存在；
- 执行控制面存在；
- 模型路由统一到 OutcomeX 自己掌控；
- 链上写入边界存在；
- 全部 backend tests 通过。

这意味着接下来的工作可以集中在真正的“最后几段”：
- 接正式 HSP；
- 接真实合约广播或 relayer；
- 让 AgentSkillOS 成为真实 orchestration engine；
- 把 PWR / runtime cost / settlement 更深度联动起来。
