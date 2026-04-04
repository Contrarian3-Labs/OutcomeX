# OutcomeX 后端与合约状态说明

本文档说明当前 `OutcomeX` MVP 在以下几个维度上的实现情况：

- 后端模块与职责
- 合约模块与职责
- 后端与合约的边界划分
- AI 执行层如何复用现有代码
- 订单、支付、执行、预览、确认、结算、分润、Claim、Transfer Guard 的状态变化
- 当前 MVP 已实现能力与未完成边界

这份文档面向后续继续开发后端、合约、索引器、执行层的工程师。

## 1. 总体架构

当前仓库分为两层主系统：

- `code/backend`
  - 面向产品的后端服务
  - 负责订单、支付、执行调度、结果确认、结算流程、机器管理、收益分发、索引查询
- `code/contracts`
  - 面向链上资产和结算的合约系统
  - 负责机器资产 NFT、订单链上收据、结算拆分、收益归集、PWR claim 与 transfer guard

当前 MVP 的核心原则是：

- 后端决定产品策略与结算分类
- 合约执行已经冻结好的结算语义
- `self-use` 不由合约实时推断，而由后端在支付完成时冻结分类
- indexer 将链上真实事件投影成后端友好的查询模型

也就是说：

- 后端更像 `product control plane`
- 合约更像 `settlement and asset state machine`
- indexer 更像 `chain truth -> query model adapter`

## 2. 目录结构

### 2.1 后端

主要目录：

- `code/backend/app/api`
- `code/backend/app/domain`
- `code/backend/app/execution`
- `code/backend/app/runtime`
- `code/backend/app/integrations`
- `code/backend/app/indexer`
- `code/backend/app/onchain`
- `code/backend/tests`

### 2.2 合约

主要目录：

- `code/contracts/src`
- `code/contracts/src/interfaces`
- `code/contracts/src/types`
- `code/contracts/src/common`
- `code/contracts/test`

## 3. 后端模块说明

### 3.1 API 层

主路由：

- `code/backend/app/api/router.py`

当前主要接口分组：

- `code/backend/app/api/routes/chat_plans.py`
  - chat-native 推荐计划接口
- `code/backend/app/api/routes/orders.py`
  - 创建订单
  - 查询订单
  - mock 结果 ready
  - 确认结果
- `code/backend/app/api/routes/payments.py`
  - 创建 mock HSP 支付意图
  - mock 支付成功/失败确认
  - 支付成功后冻结 settlement policy
- `code/backend/app/api/routes/settlement.py`
  - 预览结算
  - 锁定结算
- `code/backend/app/api/routes/revenue.py`
  - 分发收益
  - 查询某机器收益记录
- `code/backend/app/api/routes/machines.py`
  - 创建机器
  - 查询机器
  - 机器转移
- `code/backend/app/api/routes/health.py`
  - 健康检查

### 3.2 Domain 层

关键文件：

- `code/backend/app/domain/enums.py`
- `code/backend/app/domain/models.py`
- `code/backend/app/domain/rules.py`
- `code/backend/app/domain/planning.py`

主要对象：

#### `Machine`

表示后端侧的 hosted machine 记录，当前关注：

- `owner_user_id`
- `has_active_tasks`
- `has_unsettled_revenue`

它是产品层机器对象，不等同于链上的 `MachineAssetNFT`，但未来应与链上资产一一映射。

#### `Order`

表示产品层订单，当前保存：

- 用户与机器关系
- prompt 与推荐计划摘要
- 报价金额
- `order state`
- `execution state`
- `preview state`
- `settlement state`
- 冻结后的 `settlement beneficiary`
- 是否 `self-use`
- 是否 `dividend-eligible`
- `result confirmed` 时间

#### `Payment`

表示支付记录，当前是 mock HSP 边界：

- `provider`
- `provider reference`
- `amount`
- `currency`
- `payment state`

#### `SettlementRecord`

表示后端侧锁定后的结算记录：

- `gross amount`
- `platform fee`
- `machine share`
- `settlement state`
- `distributed time`

#### `RevenueEntry`

表示后端侧收益分发记录：

- `beneficiary`
- `gross / platform / machine share`
- `is_self_use`
- `is_dividend_eligible`

#### `ChatPlan`

表示 `chat-native` 推荐计划快照。

### 3.3 Execution 层

关键文件：

- `code/backend/app/execution/contracts.py`
- `code/backend/app/execution/normalizer.py`
- `code/backend/app/execution/matcher.py`
- `code/backend/app/execution/service.py`
- `code/backend/app/runtime/hardware_simulator.py`
- `code/backend/app/runtime/preview_policy.py`
- `code/backend/app/integrations/providers/alibaba_mulerouter.py`

Execution 层负责：

- 把用户意图转成 `execution recipe`
- 根据 `recipe` 做 `provider / model / runtime` 匹配
- 进行硬件运行时模拟
- 决定 `preview` 形式
- 为后端暴露统一的 `execution service interface`

当前 MVP 规则：

- 只支持 `single-step execution`
- `multi-output` 请求不会被静默截断
- `multi-output` 会被显式标记为 `unsupported`

### 3.4 Integrations 层

关键文件：

- `code/backend/app/integrations/hsp_adapter.py`
- `code/backend/app/integrations/execution_gateway.py`
- `code/backend/app/integrations/onchain_indexer.py`
- `code/backend/app/integrations/providers/base.py`
- `code/backend/app/integrations/providers/registry.py`
- `code/backend/app/integrations/providers/alibaba_mulerouter.py`

职责：

- 支付边界抽象
- 执行边界抽象
- 链上索引器边界抽象
- 外部模型/生成 provider 边界抽象

### 3.5 Indexer 层

关键文件：

- `code/backend/app/onchain/adapter.py`
- `code/backend/app/indexer/events.py`
- `code/backend/app/indexer/replay.py`
- `code/backend/app/indexer/projections.py`
- `code/backend/app/indexer/cursor.py`

职责：

- 接收 `decoded chain events`
- 归一化真实合约事件
- 按 `chain/log` 顺序 replay
- 安全跳过不支持事件
- 构建 `projection stores`

当前 indexer 已对齐当前真实合约事件，而不是使用最早那套假想事件名。

## 4. 合约模块说明

### 4.1 `MachineAssetNFT`

文件：

- `code/contracts/src/MachineAssetNFT.sol`

职责：

- 机器资产 NFT
- mint hosted machine asset
- 通过 `ITransferGuard` 阻止不允许的转移

关键事件：

- `MachineMinted`
- `TransferGuardSet`

### 4.2 `OrderBook`

文件：

- `code/contracts/src/OrderBook.sol`

职责：

- 链上订单收据与状态机
- 保存 `buyer、machine、gross amount、status、timestamps`
- 在订单创建时 snapshot `settlement beneficiary`
- 在支付时冻结 `dividend eligibility` 与 `refund authorization`
- 在 `active task / unsettled revenue` 条件下阻止机器转移

关键事件：

- `OrderCreated`
- `OrderClassified`
- `OrderPaid`
- `PreviewReady`
- `OrderSettled`

重要语义：

- `Paid` 状态下不能任意 `full refund`
- `refund` 需要 `payment/backend` 事先授权对应路径
- `self-use` 不由合约根据当前 owner 动态推断
- 合约只消费已经冻结好的 `settlement classification`

### 4.3 `SettlementController`

文件：

- `code/contracts/src/SettlementController.sol`

职责：

- 执行订单结算拆分
- 记录 buyer 可退款余额
- 记录平台累计 USDT
- 驱动 `RevenueVault` 记录 machine-side 收益

关键事件：

- `Settled`
- `RefundClaimed`
- `PlatformRevenueClaimed`

当前结算规则：

#### Confirmed

- 平台 `10%`
- 机器侧 `90%`

#### RejectedValidPreview

- 用户退款 `70%`
- 剩余 `30%` 作为 `rejection fee`
- `rejection fee` 再按 `10/90` 分给平台 / 机器侧

#### FailedOrNoValidPreview

- 用户退款 `100%`
- 不产生机器侧可分润收益

### 4.4 `RevenueVault`

文件：

- `code/contracts/src/RevenueVault.sol`

职责：

- 记录机器侧可 claim 收益
- 记录 `unsettled revenue`
- 在 `dividend-eligible` 情况下铸造并托管 `PWR`
- 暴露 `hasUnsettledRevenue(machineId)` 供 `transfer guard` 使用

关键事件：

- `RevenueAccrued`
- `RevenueClaimed`

当前语义：

- `dividend-eligible` 收益会进入可 claim `PWR`
- `non-dividend-eligible` 收益单独记录，不进入 `claimable PWR`
- `claim` 权利属于订单快照下来的 `settlement beneficiary`，而不是未来转手后的 owner

### 4.5 `PWRToken`

文件：

- `code/contracts/src/PWRToken.sol`

职责：

- 机器侧结算 token
- 由 `RevenueVault` 在合格收益发生时铸造

当前 MVP 未实现：

- PWR 二级市场
- 定价机制
- 用户直接用 PWR 支付订单

## 5. AI 执行层如何复用现有代码

这是这次实现里非常重要的一部分。

我们没有从零重写一个“通用 Agent 编排系统”，而是采用“尽量复用、在 OutcomeX 产品边界上封装”的策略。

### 5.1 复用 `AgentSkillOS` 的部分

参考来源：

- `/mnt/c/users/72988/desktop/Hashkey/reference-code/AgentSkillOS`

主要借鉴的是它的架构思路，而不是整套 `UI/Web workflow` 原样搬进来。

具体复用点：

#### 1. `Intent -> Recipe` 的结构化思路

OutcomeX 中的：

- `code/backend/app/execution/contracts.py`
- `code/backend/app/execution/normalizer.py`

采用了类似 `AgentSkillOS` 的做法：

- 先把用户目标抽象成结构化 `intent`
- 再把 `intent` 转成稳定的 `execution recipe / execution step`
- 不直接把自然语言 `prompt` 散落在各个 provider 调用里

这样做的好处是：

- 执行层边界清楚
- 后续可以替换 `matcher、provider、runtime`，而不改 API 语义
- 更符合 OutcomeX 的 `solution layer` 叙事

#### 2. `Orchestration boundary` 的思想

OutcomeX 当前没有完整复刻 `AgentSkillOS` 的 `DAG orchestration`，但保留了它的核心边界：

- `ExecutionService`
- `ExecutionPlan`
- `ExecutionRecipe`
- `ExecutionStep`

也就是说：

- 当前 MVP 只是 `single-step`
- 但模型、provider、runtime、preview policy 已经被抽成可扩展边界
- 后续如果要升级成多步 `solution orchestration`，可以沿着现有结构继续长，不需要推翻重来

#### 3. `Runtime / execution separation` 的思想

`AgentSkillOS` 将“搜索 / 编排 / 运行时”分层，OutcomeX 也采取了类似拆法：

- `normalizer.py` 负责意图归一化
- `matcher.py` 负责匹配
- `service.py` 负责执行入口
- `hardware_simulator.py` 负责 runtime 约束
- `preview_policy.py` 负责结果预览策略

所以 OutcomeX 的执行层不是 ad-hoc 调接口，而是已经形成了一个明确的 `execution control plane` 雏形。

### 5.2 复用 `mulerouter-skills` 的部分

参考来源：

- `/mnt/c/users/72988/desktop/Hashkey/reference-code/mulerouter-skills`

这里复用的重点不是“技能市场”概念，而是它已经存在的多模态 `provider / model adapter` 组织方式。

具体复用点：

#### 1. Provider boundary

OutcomeX 中的：

- `code/backend/app/integrations/providers/base.py`
- `code/backend/app/integrations/providers/registry.py`
- `code/backend/app/integrations/providers/alibaba_mulerouter.py`

延续了 `mulerouter-skills` 的思路：

- provider 有统一请求/响应协议
- provider 下面可以挂不同 `model family`
- provider registry 可以把模型家族和 endpoint 信息注册起来

#### 2. Alibaba / MuleRouter 模型适配壳

当前 `alibaba_mulerouter.py` 是一个 `adapter shell`：

- 还没有接入真实网络侧轮询/生产调用闭环
- 但 `endpoint、request、response、task status` 这层结构已经搭好了
- 未来直接替换成真实 client，而不用改上层 `execution service`

这符合你的要求：

- 能拿现有东西就拿现有东西
- 先封装，不重写
- OutcomeX 只补产品层缺失的那一层：订单、预览、结算、资产、收益

### 5.3 OutcomeX 自己补的那层是什么

虽然 AI 部分大量借鉴了现有代码，但 OutcomeX 自己新增的核心并不是模型调用本身，而是下面这些产品层对象：

- `outcome request`
- `recommended plan`
- `machine-backed runtime selection`
- `preview gating`
- `confirm-result`
- `settlement start`
- `revenue distribution`
- `machine asset transfer guard`
- `machine-side claimable yield / PWR`

也就是说，OutcomeX 的 AI 执行层不是单纯“封装调用模型”，而是把：

- 用户意图
- 执行交付
- 预览确认
- 收益归属
- 资产状态

串成一个完整闭环。

### 5.4 当前 AI 层的实际边界

当前已经做到：

- `intent -> recipe`
- `recipe -> provider match`
- `runtime capacity simulation`
- `preview policy`
- `Alibaba/MuleRouter provider shell`
- `multi-output` 明确 reject，而不是静默降级

当前还没做到：

- 真实 `skill registry` 对接
- 多步 DAG 编排
- 真实 artifact 存储/回传
- 真实 provider polling / callback
- `solution memory` 回写

所以可以把当前版本理解为：

- 结构已经按可扩展 execution plane 组织好了
- 但只实现了 MVP 必需的 `single-step` 执行能力

## 6. 后端与合约职责映射

### 6.1 创建订单

后端：

- 创建 off-chain `Order`
- 保存 `prompt、quote、machine id`

合约：

- `OrderBook.createOrder(machineId, grossAmount)`
- 创建链上 `order receipt`
- snapshot `settlement beneficiary`

### 6.2 支付成功

后端：

- mock 支付成功
- 检查累计 payment 是否足够
- 冻结：
  - `settlement beneficiary`
  - `self-use`
  - `dividend eligibility`
- 设置 `machine transfer block`

合约：

- `OrderBook.markOrderPaid(orderId, dividendEligible, refundFailedOrNoValidPreviewAuthorized)`
- 冻结 `settlement classification`
- 增加 `active task count`

对齐后的关键规则：

- backend 和 contract 都在 payment 时冻结 `settlement policy`
- 不再等到 confirm 时才冻结 beneficiary

### 6.3 结果 ready / 预览 ready

后端：

- 当前通过 `POST /api/v1/orders/{order_id}/mock-result-ready`
- 把订单推进到可确认状态
- 设置：
  - `execution_state = succeeded`
  - `preview_state = ready`
  - `order.state = result_pending_confirmation`

合约：

- `OrderBook.markPreviewReady(orderId, validPreview)`

### 6.4 用户确认结果

后端：

- `POST /api/v1/orders/{order_id}/confirm-result`
- 要求：
  - full payment
  - execution succeeded
  - preview ready
  - settlement policy 已冻结

合约：

- `OrderBook.confirmResult(orderId)`
- 要求 `preview ready` 且 `preview valid`

### 6.5 锁定结算

后端：

- `POST /api/v1/settlement/orders/{order_id}/start`
- 创建 `SettlementRecord`
- `state -> locked`

合约：

- `OrderBook` 调 `SettlementController.settle(...)`

### 6.6 收益分发 / Claim

后端：

- `POST /api/v1/revenue/orders/{order_id}/distribute`
- 创建 `RevenueEntry`
- 重新计算该机器是否还有其他未完成的 `unsettled revenue`

合约：

- `RevenueVault.accrueRevenue(...)`
- `RevenueVault.claim(machineId)`

## 7. 后端状态变化

### 7.1 Order State

定义：

- `code/backend/app/domain/enums.py`

当前状态：

- `draft`
- `plan_recommended`
- `user_confirmed`
- `executing`
- `result_pending_confirmation`
- `result_confirmed`
- `cancelled`

当前实际路径：

1. 创建订单 -> `plan_recommended`
2. mock result ready -> `result_pending_confirmation`
3. confirm result -> `result_confirmed`

### 7.2 Execution State

状态：

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`

当前实际路径：

- 默认 `queued`
- mock result ready -> `succeeded`

### 7.3 Preview State

状态：

- `draft`
- `generating`
- `ready`
- `expired`

当前实际规则：

- confirm 必须要求 `preview_state == ready`

### 7.4 Settlement State

状态：

- `not_ready`
- `ready`
- `locked`
- `distributed`

当前路径：

1. 初始 -> `not_ready`
2. confirm result -> `ready`
3. start settlement -> `locked`
4. distribute revenue -> `distributed`

## 8. 合约状态变化

### 8.1 `OrderStatus`

定义位置：

- `code/contracts/src/types/OutcomeXTypes.sol`

当前状态：

- `Created`
- `Paid`
- `PreviewReady`
- `Confirmed`
- `Rejected`
- `Refunded`

路径：

1. `createOrder` -> `Created`
2. `markOrderPaid` -> `Paid`
3. `markPreviewReady(validPreview)` -> `PreviewReady`
4. 之后三选一：
   - `confirmResult` -> `Confirmed`
   - `rejectValidPreview` -> `Rejected`
   - `refundFailedOrNoValidPreview` -> `Refunded`

### 8.2 Transfer Guard

机器 NFT 不可转移条件：

- machine 还有 active tasks
- `RevenueVault.hasUnsettledRevenue(machineId)` 为真

逻辑位置：

- `code/contracts/src/OrderBook.sol`
- `code/contracts/src/MachineAssetNFT.sol`

## 9. 关键业务流

### 9.1 Happy Path

后端：

1. create order
2. create payment intent
3. mock payment success
4. 冻结 `settlement beneficiary / self-use / dividend eligibility`
5. mock result ready
6. confirm result
7. preview settlement
8. start settlement
9. distribute revenue

合约：

1. `createOrder`
2. `markOrderPaid`
3. `markPreviewReady(validPreview=true)`
4. `confirmResult`
5. `SettlementController.settle(...Confirmed...)`
6. `RevenueVault.accrueRevenue(...)`
7. beneficiary claim `PWR`

### 9.2 Valid Preview Rejected

合约路径：

1. `markPreviewReady(validPreview=true)`
2. `rejectValidPreview`
3. 计算：
   - buyer refund `70%`
   - rejection fee `30%`
   - fee split `10/90`

### 9.3 Failed / No Valid Preview

合约路径：

1. backend/payment 侧需已授权该 refund 路径
2. `refundFailedOrNoValidPreview`
3. buyer refund `100%`

## 10. 当前 MVP 已实现与未实现边界

### 10.1 已实现

- mock HSP 边界
- 后端订单/支付/结算/收益/机器 API
- execution runtime 与 provider shell
- multi-output 显式 `unsupported`
- contract settlement suite
- machine asset transfer guard
- indexer 对齐当前真实合约事件
- 中文工程说明文档

### 10.2 未完全实现

- 真实 HSP merchant integration
- backend 到 contract 的真实写链流程
- 持久化 indexer cursor / projection store
- production-grade reorg rollback
- 真实 artifact storage 与 `preview/final unlock`
- 多步 DAG orchestration
- 直接 `PWR` 支付路径

## 11. 当前代码中已经保持的产品真相

当前实现已经尽量保证：

- 用户买的是 outcome，不是工具编排
- settlement 只在 result confirmation 之后开始
- 平台 `10%`，机器侧 `90%`
- `self-use` 由 backend 分类，不由 contract live infer
- transfer 在 `active task / unsettled revenue` 下被阻止
- machine-side claim 归属于 `snapshot beneficiary`，而不是转手后的新 owner

## 12. 下一步建议

1. 打通 `backend -> contract` 的真实写链调用
2. 用链上 receipt / event 驱动 backend order state，而不是并行双写
3. 为 execution 增加 artifact object 与 preview unlock 机制
4. 把 indexer 的 cursor / projection store 落到 Postgres
5. 引入真实 `skill registry / solution memory / multi-step orchestration`
