# OutcomeX 后端 / 合约接口对照表

本文档把当前 `feat/phase1-integration` 分支里的：

- 后端 API
- 后端内部服务
- `OrderWriter` 写链边界
- 合约方法
- 关键事件 / 状态

放到一张更细的对照表里，方便你后面继续把：

- HSP 正式接入
- `USDC / USDT / PWR` 直付
- indexer 回写
- 前端 demo

统一到一套状态机里。

## 1. 总体分层

### 1.1 当前职责分层

| 层级 | 当前职责 | 代表文件 |
| --- | --- | --- |
| 前端 / 用户交互层 | chat、选方案、支付、看执行、确认结果、查看收益 | 前端 demo（未全部并入本分支） |
| 后端 API 层 | 对外暴露产品接口 | `code/backend/app/api/routes/*.py` |
| 后端控制面 | 订单、支付、执行 run、预览、确认、结算、收益台账 | `code/backend/app/domain/*` |
| 执行内核边界 | 向 AgentSkillOS 提交任务并读取 run 记录 | `code/backend/app/integrations/agentskillos_execution_service.py` |
| 写链边界 | 把业务状态映射成 deterministic 链上调用 payload | `code/backend/app/onchain/order_writer.py` |
| 合约层 | receipt、结算、claim、transfer guard | `code/contracts/src/*.sol` |

### 1.2 当前最重要的分界

- `AgentSkillOS` 负责执行
- OutcomeX 后端负责控制面
- 合约负责资产语义与结算语义

也就是说：

- 后端不直接替代 AgentSkillOS 做多步 delivery
- 合约也不直接承接所有产品流程交互

## 2. 用户动作 → 后端接口 → 链上语义总表

| 用户动作 | 后端接口 | 后端核心动作 | `OrderWriter` / 写链边界 | 当前对应合约语义 |
| --- | --- | --- | --- | --- |
| 发起 chat 需求 | `POST /api/v1/chat/plans` | 生成推荐方案 + quote | 无 | 暂无直接链上动作 |
| 确认方案创建订单 | `POST /api/v1/orders` | 创建 `Order`，写入 execution metadata | `createOrder` | `OrderBook.createOrder(...)` |
| 创建支付意图 | `POST /api/v1/payments/orders/{order_id}/intent` | 创建 `Payment`，返回 HSP-like checkout 信息 | 无 | 未来可映射到 payment router / merchant receipt |
| 支付确认 | `POST /api/v1/payments/{payment_id}/mock-confirm` 或 `POST /api/v1/payments/hsp/webhooks` | 更新 `Payment`，冻结 settlement policy，标记 machine 有未结收益 | `markOrderPaid` | `OrderBook.markOrderPaid(...)` |
| 启动执行 | `POST /api/v1/orders/{order_id}/start-execution` | 提交 AgentSkillOS run，写入 `ExecutionRun`，机器进入 active task | 当前无直接写链 | 未来可映射为 paid 后开始执行 |
| 查询执行结果 | `GET /api/v1/execution-runs/{run_id}` | 同步 run 状态、preview、artifacts、skills、model usage | 当前无直接写链 | 未来与 preview-ready 语义衔接 |
| 标记 preview ready | 当前由 run 成功自动推进到订单待确认 | 设置 `preview_state=READY` | 当前仅 mock 路径里可触发 `markPreviewReady` | `OrderBook.markPreviewReady(...)` |
| 用户确认结果 | `POST /api/v1/orders/{order_id}/confirm-result` | 检查 payment/execution/preview/settlement policy，更新订单 | `confirmResult` | `OrderBook.confirmResult(...)` |
| 发起结算 | `POST /api/v1/settlement/orders/{order_id}/start` | 创建 `SettlementRecord`，锁定 10% / 90% | `settleOrder` | `SettlementController.settle(...)` |
| 分发收益 | `POST /api/v1/revenue/orders/{order_id}/distribute` | 写 `RevenueEntry`，更新 machine 未结收益状态 | 当前无直接写链 | `RevenueVault.accrueRevenue(...)` / `claim(...)` 的 off-chain 投影 |
| 查询机器收益 | `GET /api/v1/revenue/machines/{machine_id}` | 读取 off-chain revenue ledger | 无 | 对应链上 `claimableByMachineOwner(...)` 的投影视图 |
| 机器转移 | `POST /api/v1/machines/{machine_id}/transfer` | 依据 `has_active_tasks` / `has_unsettled_revenue` 做后端保护 | 无 | `MachineAssetNFT` + `OrderBook.canTransfer(...)` |

## 3. 后端 API 详细对照

## 3.1 Chat / Quote

| 接口 | 作用 | 读写的核心对象 | 备注 |
| --- | --- | --- | --- |
| `POST /api/v1/chat/plans` | 把 chat 请求转成推荐方案和报价 | `ChatPlan`，`RuntimeCostService` | 用户看到的是方案和价格，不是 workflow internals |

## 3.2 Orders

| 接口 | 作用 | 读写对象 | 关键状态变化 |
| --- | --- | --- | --- |
| `POST /api/v1/orders` | 创建订单 | `Order` | `state=PLAN_RECOMMENDED` |
| `GET /api/v1/orders/{order_id}` | 查询订单 | `Order` | 读取 `execution_metadata` / settlement 冻结信息 |
| `POST /api/v1/orders/{order_id}/start-execution` | 提交 AgentSkillOS run | `Order`、`ExecutionRun`、`Machine` | `Order.state=EXECUTING`，`Machine.has_active_tasks=true` |
| `POST /api/v1/orders/{order_id}/mock-result-ready` | mock 结果 ready | `Order` | `execution_state=SUCCEEDED`，`preview_state=READY` |
| `POST /api/v1/orders/{order_id}/confirm-result` | 用户确认结果 | `Order` | `state=RESULT_CONFIRMED`，`settlement_state=READY` |

## 3.3 Execution Runs

| 接口 | 作用 | 读写对象 | 关键字段 |
| --- | --- | --- | --- |
| `GET /api/v1/execution-runs/{run_id}` | 轮询 AgentSkillOS run | `ExecutionRun`、`Order`、`Machine` | `status`、`preview_manifest`、`artifact_manifest`、`skills_manifest`、`model_usage_manifest` |
| `POST /api/v1/execution-runs/{run_id}/cancel` | 取消 run | `ExecutionRun`、`Order`、`Machine` | `status=CANCELLED`，释放 active task |

## 3.4 Payments

| 接口 | 作用 | 读写对象 | 关键状态变化 |
| --- | --- | --- | --- |
| `POST /api/v1/payments/orders/{order_id}/intent` | 创建支付意图 | `Payment` | `state=PENDING` |
| `POST /api/v1/payments/{payment_id}/mock-confirm` | mock 支付回调 | `Payment`、`Order`、`Machine` | `Payment.state=SUCCEEDED/FAILED`；足额支付时冻结 settlement policy |
| `POST /api/v1/payments/hsp/webhooks` | HSP webhook 入口 | `Payment`、`Order`、`Machine` | 幂等更新 callback 字段和 payment state |

## 3.5 Settlement / Revenue

| 接口 | 作用 | 读写对象 | 关键状态变化 |
| --- | --- | --- | --- |
| `POST /api/v1/settlement/orders/{order_id}/preview` | 查看预结算 | `Order` | 返回 10% / 90% 拆分，不落库 |
| `POST /api/v1/settlement/orders/{order_id}/start` | 锁定 settlement | `SettlementRecord`、`Order`、`Machine` | `SettlementRecord.state=LOCKED`，`Order.settlement_state=LOCKED` |
| `POST /api/v1/revenue/orders/{order_id}/distribute` | 落 off-chain revenue ledger | `RevenueEntry`、`SettlementRecord`、`Order`、`Machine` | `SettlementRecord.state=DISTRIBUTED` |
| `GET /api/v1/revenue/machines/{machine_id}` | 读取机器收益记录 | `RevenueEntry` | 只读接口 |

## 3.6 Machines

| 接口 | 作用 | 读写对象 | 关键状态变化 |
| --- | --- | --- | --- |
| `POST /api/v1/machines` | 创建机器对象 | `Machine` | 初始化 owner、transfer guard 基础状态 |
| `GET /api/v1/machines` | 查询机器列表 | `Machine` | 只读 |
| `POST /api/v1/machines/{machine_id}/transfer` | 后端模拟机器转移 | `Machine` | 如果 `has_active_tasks` 或 `has_unsettled_revenue` 为真则阻止 |

## 4. 后端对象 → 合约对象对照

| 后端对象 | 当前含义 | 最接近的链上对象 | 差异说明 |
| --- | --- | --- | --- |
| `Machine` | 产品层机器实例 | `MachineAssetNFT` | 当前后端 `Machine.id` 还不是链上的 `tokenId` |
| `Order` | 产品层订单控制对象 | `OrderBook` 中的 `OrderRecord` | 后端字段更丰富，包含 execution / preview / settlement 元数据 |
| `Payment` | 支付意图与回调记录 | 未来的 payment router receipt | 当前合约里未承接真实稳定币入金 |
| `ExecutionRun` | AgentSkillOS 执行 run 投影 | 无直接对应 | 更像执行内核的 off-chain receipt |
| `SettlementRecord` | 结算锁定与分账记录 | `SettlementController` 内部 settlement 结果 | 后端是台账，链上是状态机 |
| `RevenueEntry` | 机器侧收益分发记录 | `RevenueVault` 中的 claimable / accrued 状态 | 后端是明细账，链上是可 claim 状态 |

## 5. `OrderWriter` 方法 → 目标合约方法对照

| `OrderWriter` 方法 | 当前 payload 含义 | 目标合约方法 | 当前状态 |
| --- | --- | --- | --- |
| `create_order(order)` | 创建订单链上收据 | `OrderBook.createOrder(machineId, grossAmount)` | 当前为 deterministic payload |
| `mark_order_paid(order, payment)` | 订单已足额支付且 settlement policy 已冻结 | `OrderBook.markOrderPaid(orderId, dividendEligible, refundAuthorized)` | 当前为 deterministic payload |
| `mark_preview_ready(order)` | preview 已可确认 | `OrderBook.markPreviewReady(orderId, validPreview)` | 当前仅 mock 路径触发 |
| `confirm_result(order)` | 用户已确认结果 | `OrderBook.confirmResult(orderId)` | 当前为 deterministic payload |
| `settle_order(order, settlement)` | 触发 10% / 90% settlement | `SettlementController.settle(...)` | 当前为 deterministic payload |

## 6. 合约方法 / 事件 → 后端应如何消费

## 6.1 OrderBook

| 合约方法 / 事件 | 语义 | 后端应该同步什么 |
| --- | --- | --- |
| `OrderCreated` | 订单已在链上登记 | 建立 order ↔ chain order id 映射 |
| `OrderClassified` | dividend eligibility / refund 授权已冻结 | 回写 settlement policy 快照 |
| `OrderPaid` | 已完成 paid 状态 | 回写 payment completed / active task opened |
| `PreviewReady` | preview 已 ready | 回写 preview_state |
| `OrderSettled` | 订单终局已确定 | 回写 settlement terminal state、refund/platform/machine breakdown |

## 6.2 SettlementController

| 合约方法 / 事件 | 语义 | 后端应该同步什么 |
| --- | --- | --- |
| `Settled` | 具体 settlement breakdown 已确定 | 生成 settlement timeline、machine revenue projection |
| `RefundClaimed` | 用户已 claim refund | 回写 refundable balance / claimed history |
| `PlatformRevenueClaimed` | 平台已 claim 平台侧收入 | 回写 platform treasury ledger |

## 6.3 RevenueVault

| 合约方法 / 事件 | 语义 | 后端应该同步什么 |
| --- | --- | --- |
| `RevenueAccrued` | 机器侧收益已归集 | 回写 machine claimable、unsettled revenue |
| `RevenueClaimed` | 机器持有人已 claim | 回写 claimable 余额下降，必要时解除 transfer block |

## 6.4 MachineAssetNFT / Transfer Guard

| 合约方法 / 事件 | 语义 | 后端应该同步什么 |
| --- | --- | --- |
| `Transfer` | 机器资产发生 owner 变更 | 回写 owner 投影 |
| `canTransfer(...)` / revert `TransferGuardBlocked` | 当前不可转移 | 回写 blocked reason：`ACTIVE_TASK` 或 `UNSETTLED_REVENUE` |

## 7. 当前最重要的状态字段

## 7.1 后端状态字段

| 对象 | 字段 | 含义 |
| --- | --- | --- |
| `Order` | `state` | 订单主状态 |
| `Order` | `execution_state` | 执行态：queued/running/succeeded/failed/cancelled |
| `Order` | `preview_state` | preview 态 |
| `Order` | `settlement_state` | 结算态 |
| `Order` | `execution_metadata.run_id` | 对应 AgentSkillOS run |
| `Order` | `execution_metadata.run_status` | 当前 run 状态投影 |
| `Machine` | `has_active_tasks` | 是否阻止转移 |
| `Machine` | `has_unsettled_revenue` | 是否阻止转移 |
| `ExecutionRun` | `status` | AgentSkillOS run 生命周期 |

## 7.2 链上关键状态字段

| 合约 | 字段 | 含义 |
| --- | --- | --- |
| `OrderBook` | `activeTaskCountByMachine` | 活跃任务计数 |
| `OrderBook` | `settlementBeneficiaryByOrder` | 订单收益快照持有人 |
| `OrderBook` | `dividendEligibleByOrder` | 是否进入 dividend-eligible 路径 |
| `SettlementController` | `refundableUSDT` | 买家可退余额 |
| `SettlementController` | `platformAccruedUSDT` | 平台累计应收 |
| `RevenueVault` | `unsettledRevenueByMachine` | 机器未 claim 收益 |
| `RevenueVault` | `claimableByMachineOwner` | 快照 beneficiary 的可 claim PWR |

## 8. 现在还没完全打通的地方

| 能力 | 当前状态 | 后续建议 |
| --- | --- | --- |
| HSP 正式收款 | 只有 mock / deterministic adapter | 接正式 merchant API + webhook |
| `USDC direct pay` | 设计已明确，未正式落地到后端主流 | 接 payment router + indexer |
| `USDT direct pay` | 同上 | 接 Permit2 路径 |
| `PWR pay` | 设计已明确，未正式落地 | 接 `payWithPWR(...)` + quote / anchor |
| `OrderWriter` 真实广播 | 目前只是 deterministic payload | 接 relayer / signer / tx hash 回写 |
| 链上事件回写 | 已有 indexer 雏形，但未完整闭环 | 把 paid / preview / settlement / claim / transfer 全部投影回后端 |

## 9. 一句话结论

当前最合理的理解方式是：

- 后端 API 管产品流程
- AgentSkillOS 管执行
- 合约管资产语义与结算语义

而这份对照表的作用，就是把三层之间的映射锁清楚，避免后面在：

- 支付入口
- 结果确认
- settlement
- claim
- transfer guard

这些地方重复造状态机。
