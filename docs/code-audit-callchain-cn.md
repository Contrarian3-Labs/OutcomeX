# OutcomeX 代码审计与调用链总览（2026-04-05）

本文档基于分支 `feat/agentskillos-thin-interface` 当前状态审计，目标是回答三件事：

- 用户从前台点击到结果确认，完整调用链怎么走
- 后端、AgentSkillOS、合约之间分别负责什么
- 当前实现有哪些高优先级风险和已知缺口

---

## 1. 当前架构边界

### 1.1 角色分层

- `前端 / 产品层`
  - 收集用户意图
  - 展示 plan / quote
  - 发起 HSP 支付或钱包直签支付
  - 轮询执行进度
  - 触发结果确认、收益查看、机器转移
- `OutcomeX backend`
  - 订单控制面
  - 支付状态记录
  - settlement / revenue 台账
  - 机器活跃任务 / 未结收益状态
  - 向 AgentSkillOS 提交薄执行请求
  - 对链上交互生成 deterministic write payload 或 direct-intent
- `AgentSkillOS`
  - skill 检索
  - 多步编排
  - 模型 / 工具 / 脚本调用
  - 产物生成与 run 元数据
- `合约`
  - 机器资产 NFT
  - 订单链上生命周期
  - direct pay 资金托管
  - settlement split
  - 平台 claim / 用户 refund / 机器侧 PWR accrual
  - transfer guard

### 1.2 最关键的边界

当前 backend 和 AgentSkillOS 的边界是薄接口：

- backend 只提交 `intent`
- backend 只提交 `files`
- backend 只提交 `execution_strategy`

对应实现：

- `code/backend/app/execution/service.py`
- `code/backend/app/integrations/agentskillos_execution_service.py`
- `code/backend/app/api/routes/orders.py:63`
- `code/backend/app/api/routes/orders.py:187`

---

## 2. 用户交互完整调用链

## 2.1 路径 A：Chat -> Plan -> 下单

### Step 1：用户输入目标

前端调用：

- `POST /api/v1/chat/plans`

后端：

- `code/backend/app/api/routes/chat_plans.py`
- 生成 `ChatPlan`
- 用 `RuntimeCostService.quote_for_prompt(...)` 产出 quote

返回给前端：

- 推荐 plan 文案
- official quote
- runtime cost
- platform fee / machine share
- PWR quote
- PWR anchor metadata

### Step 2：用户确认 plan，创建订单

前端调用：

- `POST /api/v1/orders`

后端动作：

- `code/backend/app/api/routes/orders.py:42`
- 创建 `Order`
- 调 `_build_execution_plan(...)`
- 生成：
  - `execution_request`
  - `execution_metadata`
- 调 `OrderWriter.create_order(order)` 生成链上写入意图

注意：

- 当前 `create_order(...)` 是 deterministic payload，不是真实广播
- 对应代码：`code/backend/app/onchain/order_writer.py:30`

---

## 2.2 路径 B1：HSP 支付

### Step 3：前端请求 HSP intent

前端调用：

- `POST /api/v1/payments/orders/{order_id}/intent`

后端动作：

- `code/backend/app/api/routes/payments.py:81`
- 调 HSP adapter 创建 merchant order
- 落库 `Payment(provider="hsp")`

### Step 4：HSP 回调 / mock confirm

入口：

- `POST /api/v1/payments/hsp/webhooks`
- `POST /api/v1/payments/{payment_id}/mock-confirm`

后端动作：

- `_apply_payment_state(...)`
- `_freeze_settlement_policy_if_fully_paid(...)`
- 若足额支付：
  - 冻结 beneficiary / self-use / dividend-eligible
  - 标记 `machine.has_unsettled_revenue = True`
  - 调 `order_writer.mark_order_paid(...)`

对应代码：

- `code/backend/app/api/routes/payments.py:38`
- `code/backend/app/api/routes/payments.py:61`
- `code/backend/app/onchain/order_writer.py:39`

---

## 2.3 路径 B2：用户钱包直签 direct pay（USDC / USDT / PWR）

### Step 3：前端请求 direct intent

前端调用：

- `POST /api/v1/payments/orders/{order_id}/direct-intent`

后端动作：

- `code/backend/app/api/routes/payments.py:126`
- 落库 `Payment(provider="onchain_router")`
- 用 `RuntimeCostService` 生成 quote
- 调 `OrderWriter.build_direct_payment_intent(...)`
- 返回：
  - `contract_name`
  - `contract_address`
  - `method_name`
  - `signing_standard`
  - `submit_payload`

如果币种是 `PWR`：

- 还会返回
  - `pwr_amount`
  - `pricing_version`
  - `pwr_anchor_price_cents`

对应代码：

- `code/backend/app/api/routes/payments.py:131`
- `code/backend/app/api/routes/payments.py:161`
- `code/backend/app/onchain/order_writer.py:54`

### Step 4：用户钱包直接和合约交互

合约入口：

- `OrderPaymentRouter.payWithUSDCByAuthorization(...)`
- `OrderPaymentRouter.payWithUSDT(...)`
- `OrderPaymentRouter.payWithPWR(...)`

对应代码：

- `code/contracts/src/OrderPaymentRouter.sol:72`
- `code/contracts/src/OrderPaymentRouter.sol:91`
- `code/contracts/src/OrderPaymentRouter.sol:109`

合约内部动作：

- 校验 order 状态必须是 `Created`
- 校验 buyer 必须是 `msg.sender`
- 校验 amount 必须等于 `order.grossAmount`
- 把 token 转进 `SettlementController`
- 调 `orderBook.markOrderPaid(...)`

对应代码：

- `code/contracts/src/OrderPaymentRouter.sol:118`
- `code/contracts/src/OrderBook.sol:114`

### Step 5：前端把链上支付结果 sync 回 backend

前端调用：

- `POST /api/v1/payments/{payment_id}/sync-onchain`

后端动作：

- `code/backend/app/api/routes/payments.py:195`
- 回填：
  - `callback_event_id`
  - `callback_state`
  - `callback_received_at`
  - `callback_tx_hash`
- 调 `_apply_payment_state(..., write_chain=False)`
- 冻结 settlement policy
- 标记 `machine.has_unsettled_revenue = True`
- 不重复 `markOrderPaid`

这一步的设计意图是：

- 链上 direct pay 已经完成真实资金托管
- backend 只同步控制面状态，不再二次写链

---

## 2.4 路径 C：执行阶段（OutcomeX -> AgentSkillOS）

### Step 6：启动执行

前端调用：

- `POST /api/v1/orders/{order_id}/start-execution`

后端动作：

- `code/backend/app/api/routes/orders.py:166`
- 校验订单足额支付
- 用 `ExecutionEngineService.dispatch(...)` 发起执行
- 提交给 AgentSkillOS 的只有：
  - `intent`
  - `files`
  - `execution_strategy`
- 创建 `ExecutionRun`
- 标记 `machine.has_active_tasks = True`

### Step 7：AgentSkillOS 执行与状态同步

查询入口：

- `GET /api/v1/execution-runs/{run_id}`

后端动作：

- `code/backend/app/api/routes/execution_runs.py:28`
- 从 AgentSkillOS wrapper 读取 run snapshot
- 回填：
  - `artifact_manifest`
  - `skills_manifest`
  - `model_usage_manifest`
  - `summary_metrics`
- run 成功时：
  - `order.execution_state = SUCCEEDED`
  - `order.preview_state = READY`
  - `order.state = RESULT_PENDING_CONFIRMATION`
  - `machine.has_active_tasks = False`
- run 失败 / 取消时：
  - 释放 `machine.has_active_tasks`

---

## 2.5 路径 D：结果确认、结算、收益

### Step 8：用户确认结果

前端调用：

- `POST /api/v1/orders/{order_id}/confirm-result`

后端动作：

- `code/backend/app/api/routes/orders.py:85`
- 校验：
  - 足额支付
  - execution 成功
  - preview ready
  - settlement policy 已冻结
- 设置：
  - `order.state = RESULT_CONFIRMED`
  - `order.result_confirmed_at`
  - `order.settlement_state = READY`
- 调 `order_writer.confirm_result(order)`

### Step 9：开始 settlement

前端调用：

- `POST /api/v1/settlement/orders/{order_id}/start`

后端动作：

- `code/backend/app/api/routes/settlement.py:64`
- 生成 `SettlementRecord(state=LOCKED)`
- 设置 `order.settlement_state = LOCKED`
- 维持 `machine.has_unsettled_revenue = True`
- 调 `order_writer.settle_order(order, settlement)`

### Step 10：收益分发

前端调用：

- `POST /api/v1/revenue/orders/{order_id}/distribute`

后端动作：

- `code/backend/app/api/routes/revenue.py:37`
- 创建 `RevenueEntry`
- 设置：
  - `settlement.state = DISTRIBUTED`
  - `order.settlement_state = DISTRIBUTED`
- 如果没有其他未结收益：
  - `machine.has_unsettled_revenue = False`

注意：

- backend 这里是 off-chain 投影台账
- 合约侧真实 split 在 `SettlementController` + `RevenueVault`

---

## 2.6 路径 E：机器转移

前端调用：

- `POST /api/v1/machines/{machine_id}/transfer`

后端动作：

- `code/backend/app/api/routes/machines.py:35`
- 如果 `has_active_tasks` 或 `has_unsettled_revenue` 为真，则拒绝
- 否则直接修改 `Machine.owner_user_id`

链上对应语义：

- `MachineAssetNFT` 的 `_beforeTokenTransfer(...)` 会通过 `OrderBook.canTransfer(...)` 检查 active task 和 unsettled revenue

对应代码：

- `code/contracts/src/MachineAssetNFT.sol`
- `code/contracts/src/OrderBook.sol`

---

## 3. 合约内部调用链

## 3.1 用户 direct pay -> OrderBook

- `OrderPaymentRouter.payWithUSDCByAuthorization(...)`
- `OrderPaymentRouter.payWithUSDT(...)`
- `OrderPaymentRouter.payWithPWR(...)`
  - 资金转入 `settlementEscrow`
  - 调 `orderBook.markOrderPaid(...)`

## 3.2 用户确认 / 拒绝 / 退款 -> SettlementController

- buyer 调 `OrderBook.confirmResult(...)`
- buyer 调 `OrderBook.rejectValidPreview(...)`
- buyer 或 settlementBeneficiary 调 `OrderBook.refundFailedOrNoValidPreview(...)`
- `OrderBook._settleOrder(...)` 统一进入 `SettlementController.settle(...)`

## 3.3 SettlementController -> RevenueVault

`SettlementController.settle(...)` 会：

- 计算 refund / platform share / machine share
- 为 buyer 记 refund ledger
- 为 treasury 记 platform ledger
- 调 `RevenueVault.accrueRevenue(...)`

## 3.4 RevenueVault -> PWRToken

`RevenueVault.accrueRevenue(...)` 会：

- dividend-eligible：
  - 增加 `unsettledRevenueByMachine`
  - 增加 `claimableByMachineOwner`
  - mint `PWR` 到 vault
- non-dividend-eligible：
  - 进入 `nonDividendRevenueByMachine`

---

## 4. 审计结论

### 总体结论

当前分支已经形成了一个可以讲清楚产品闭环的 hackathon 主线：

- 用户意图 -> plan -> order
- HSP 或 direct onchain 支付
- backend 冻结 settlement policy
- AgentSkillOS 执行
- 用户确认结果
- settlement / revenue / transfer guard

但它仍然不是“完全 production-ready 的链上闭环”，主要原因是：

- direct-intent 还不是可直接喂 ABI 的最终 calldata 层
- backend 的 onchain sync 还没有真实验链
- 机器 ownership 在 backend 和 chain 之间还可能漂移
- 硬件 admission control 还没有跨请求持久化

---

## 5. 主要发现（按严重度排序）

### Finding 1 — High
`/direct-intent` 返回的是控制面 payload，不是和合约函数签名严格对齐的真实链上调用参数，direct pay 仍缺少“backend order <-> onchain orderId”真实绑定。

证据：

- `code/backend/app/api/routes/payments.py:179`
- `code/backend/app/onchain/order_writer.py:67`
- `code/contracts/src/OrderPaymentRouter.sol:72`
- `code/contracts/src/OrderPaymentRouter.sol:118`
- `code/contracts/src/OrderBook.sol:88`

原因：

- backend control plane 用的是 UUID 型 `order.id`
- 合约 `OrderBook` 用的是 `uint256 orderId`
- backend `submit_payload` 里仍然是 `order_id` / `payment_id` / `amount_cents` 这类控制面字段
- 合约真正需要的是和链上订单严格对应的 ABI 参数

影响：

- 目前 direct-intent 更像“钱包调用说明”而不是最终可直接签名广播的真实 calldata
- 这意味着 direct onchain path 在产品层是半真实、半控制面模拟

建议：

- 增加 backend `onchain_order_id` 字段
- 创建真实 `createOrder` 广播 / 索引回写闭环
- direct-intent 返回 ABI 参数对象或 calldata，而不是仅返回抽象 payload

状态更新（2026-04-05）：

- 已补 `Order.onchain_order_id`，direct-intent 和 mark/order writer 已统一绑定链侧 order 标识
- 真实 createOrder 广播 / 回写仍未完成，因此该 finding 目前是部分修复

### Finding 2 — High
`/sync-onchain` 当前完全信任调用方上报的 `state` 和 `tx_hash`，没有做链上 receipt / log 验证。

证据：

- `code/backend/app/api/routes/payments.py:195`
- `code/backend/app/api/routes/payments.py:210`
- `code/backend/app/api/routes/payments.py:216`

原因：

- 当前 sync 路径只是把前端上报内容写回 `Payment`
- 没有校验：
  - tx 是否真实存在
  - from 是否等于 buyer
  - token / amount / order 是否匹配
  - 是否确实触发了 `OrderPaymentReceived`

影响：

- 如果暴露在不受信任环境中，攻击者可能伪造支付成功状态，提前冻结 settlement policy 并推进订单

建议：

- 至少接入 indexer 或链上 receipt 查询
- 以 event/log 为准，而不是以前端上报 `state` 为准
- `wallet_address` 当前未参与校验，应纳入验证逻辑

状态更新（2026-04-05）：

- 已增加 `OnchainPaymentVerifier` 边界，`/sync-onchain` 现在使用 verifier 返回的 `state/event_id/tx_hash`
- 前端上报的 `state` 不再是最终真值；mismatch 会被拒绝
- 默认 verifier 仍是本地占位实现，后续仍应接入真实 indexer / receipt provider

### Finding 3 — Medium
后端 `machines/transfer` 是纯 off-chain owner 切换，没有和链上 `MachineAssetNFT.ownerOf(...)` 做一致性绑定，可能导致 ownership 双写漂移。

证据：

- `code/backend/app/api/routes/machines.py:35`
- `code/backend/app/api/routes/machines.py:51`
- `code/contracts/src/MachineAssetNFT.sol`

原因：

- backend 直接写 `Machine.owner_user_id`
- 但链上真实 owner 在 `MachineAssetNFT`
- 当前没有强制要求“先链上转移，再由 indexer 回写 backend”

影响：

- backend owner 与 chain owner 可能不一致
- settlement beneficiary snapshot、机器市场展示、后续 transfer 逻辑可能出现分叉

建议：

- 把 backend transfer 改成“发起链上转移 intent”而不是直接改 owner
- 以 indexer 投影更新 `owner_user_id`
- 若保留 demo route，必须显式标记为 mock/off-chain only

状态更新（2026-04-05）：

- 已将 transfer route 改为记录 pending intent，不再直接写 canonical owner
- 已引入 `MachineOwnershipProjectionIntegrator`，把链上 owner 投影为后端真值

### Finding 4 — Medium
硬件 admission control 目前是请求级新实例，`HardwareSimulator` 状态不会跨请求持久化，无法真正表达共享机器容量。

证据：

- `code/backend/app/api/routes/orders.py:63`
- `code/backend/app/api/routes/orders.py:187`
- `code/backend/app/execution/service.py:47`

原因：

- `ExecutionEngineService()` 在 plan 和 dispatch 时都临时 new
- 每次 new 都会创建新的 `HardwareSimulator(...)`
- simulator 队列 / 占用状态不会跨请求保留

影响：

- 当前 admission control 更像单次估算器，不是全局容量调度器
- 当并发多订单时，无法真实限制资源

建议：

- 把 `HardwareSimulator` 提升为单例依赖或持久化服务
- 或者直接用数据库 / redis 记录 machine runtime occupancy

状态更新（2026-04-05）：

- 已把 simulator 提升为共享依赖，并通过 container reset hook 清理测试期状态
- admission occupancy 现在可以跨 service/request 实例延续

---

## 6. 适合合入 main 吗？

我的判断：

- 作为 hackathon 主线分支：可以合入 `main`
- 作为 production-ready onchain payment system：还不可以

原因：

- 主产品故事线已经完整
- 测试覆盖了当前 MVP 范围
- 其中 Finding 2/3/4 已在 `feat/post-hackathon-hardening` 修复，Finding 1 已完成 `onchain_order_id` 部分修复
- 仍需继续推进真实广播 / indexer 回写，才能达到 production-ready onchain payment system

---

## 7. 建议的 main 后续优先级

1. 打通真实 `createOrder` 广播 + `onchain_order_id` 回写（仍未完成）
2. 将 `OnchainPaymentVerifier` 接入真实 indexer / receipt provider（当前为占位 verifier 边界）
3. 把 machine owner 地址到用户身份的 resolver 接到真实映射源
4. 如果要走多实例部署，把共享 runtime occupancy 从进程内单例升级到 Redis / DB

