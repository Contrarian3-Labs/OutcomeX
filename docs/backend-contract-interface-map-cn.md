# OutcomeX 后端 / 合约 / AgentSkillOS 完整对照文档

本文档对应当前干净集成分支：`feat/agentskillos-thin-interface`。

当前代码基线是：

- `main`
- 合并 `feat/phase1-integration` 的已提交内容
- 再额外收敛为真正的 `OutcomeX backend -> AgentSkillOS` 薄接口边界

当前已经包含的关键支付语义：

- `USDC` / `USDT` 可以走用户直签的链上支付路由
- 后端只负责生成直签意图、同步链上结果、冻结结算资格
- `PWR` 直付仍然显式 gated，等待 anchor 机制落地后再开放

不包含的内容：

- OutcomeX 侧的模型选择、能力路由、skill 检索逻辑，已经明确不再由 OutcomeX 承担

---

## 1. 一句话架构

当前 OutcomeX 的真实职责分层是：

- `OutcomeX 前端/产品层`：收用户意图、展示推荐方案、发起支付、展示执行进度、确认结果、查看收益
- `OutcomeX backend`：做订单控制面、支付状态、执行 run 记录、结算台账、写链边界
- `AgentSkillOS`：做能力理解、skill 检索、编排、模型调用、脚本执行、产物生成
- `合约`：做机器资产语义、订单链上收据、结算语义、收益归集、转移约束

最重要的边界是：

**OutcomeX backend 只向 AgentSkillOS 提交 `intent / files / execution_strategy`，不在自身做 capability routing / model routing / solution orchestration。**

---

## 2. 现在代码里最关键的三个对象

### 2.1 `Order.execution_request`

位置：`code/backend/app/domain/models.py`

这是 OutcomeX 后端准备发给 AgentSkillOS 的薄提交载荷，当前格式是：

```json
{
  "intent": "用户要的结果描述",
  "files": ["输入文件路径列表"],
  "execution_strategy": "quality | efficiency | simplicity"
}
```

它表达的是：

- 用户真正想要什么结果
- 这次执行带了哪些输入文件
- 用户选择了什么执行偏好

它**不表达**：

- 用哪个模型
- 用哪些 skill
- 走几步 DAG
- 中间如何编排

这些都交给 AgentSkillOS 内部决定。

### 2.2 `Order.execution_metadata`

位置：`code/backend/app/domain/models.py`

这是 OutcomeX 自己的控制面元数据，不是交给 AgentSkillOS 的任务内容。当前主要记录：

- `gateway=outcomex_agentskillos_thin.v1`
- `submission_status=draft`
- `execution_strategy`
- `agentskillos_mode`
- `input_file_count`
- run 启动后还会追加：`run_id`、`run_status`

也就是说：

- `execution_request` 是“我要提交什么”
- `execution_metadata` 是“我作为 OutcomeX backend 怎么追踪这次提交”

### 2.3 `ExecutionRun.submission_payload`

位置：`code/backend/app/domain/models.py`

这是某一次实际 run 的提交快照。它和 `Order.execution_request` 很像，但语义更强：

- `Order.execution_request` 是订单级别的默认执行请求
- `ExecutionRun.submission_payload` 是某一次真实 run 被发出时的快照

这样以后即使同一个订单重试多次，也能保留每次 run 的独立提交记录。

---

## 3. 现在的真实执行链路

### 3.1 创建订单

接口：`POST /api/v1/orders`

后端动作：

1. 创建 `Order`
2. 生成 `recommended_plan_summary`
3. 生成薄执行请求 `execution_request`
4. 生成控制面元数据 `execution_metadata`
5. 调用 `OrderWriter.create_order(order)` 形成 deterministic 写链 payload

注意：这一步不会做模型或 skill 选择。

### 3.2 支付

接口分成两条并行 rail：

- HSP rail
  - `POST /api/v1/payments/orders/{order_id}/intent`
  - `POST /api/v1/payments/{payment_id}/mock-confirm`
  - `POST /api/v1/payments/hsp/webhooks`
- 用户直签链上 rail
  - `POST /api/v1/payments/orders/{order_id}/direct-intent`
  - `POST /api/v1/payments/{payment_id}/sync-onchain`

#### 路径 A：HSP rail

后端动作：

1. OutcomeX backend 调 HSP adapter 生成支付意图
2. 落一条 `Payment(provider="hsp")`
3. 用户在 HSP checkout 完成支付
4. 后端通过 webhook / mock confirm 收到支付结果
5. 支付成功后冻结 settlement policy：
   - `settlement_beneficiary_user_id`
   - `settlement_is_self_use`
   - `settlement_is_dividend_eligible`
6. 把机器标记为 `has_unsettled_revenue=true`
7. 通过 `OrderWriter.mark_order_paid(...)` 形成 `OrderBook.markOrderPaid` 写链 payload

#### 路径 B：用户直签链上 stablecoin rail

后端动作：

1. 前端调 `POST /api/v1/payments/orders/{order_id}/direct-intent`
2. 后端要求金额必须等于 `order.quoted_amount_cents`
3. 后端落一条 `Payment(provider="onchain_router")`
4. 后端通过 `OrderWriter.build_direct_payment_intent(...)` 返回用户直签所需的合约调用规格：
   - `contract_name=OrderPaymentRouter`
   - `method_name=payWithUSDCByAuthorization` 或 `payWithUSDT`
   - `chain_id=133`
   - `token_address`
   - `signing_standard=eip3009 | permit2`
5. 用户在钱包里直接签名并把 stablecoin 送进链上 `OrderPaymentRouter`
6. 交易确认后，前端再调 `POST /api/v1/payments/{payment_id}/sync-onchain`
7. 后端只做状态同步：
   - 回填 `callback_event_id`
   - 回填 `callback_state`
   - 回填 `callback_tx_hash`
   - 冻结 settlement policy
   - 标记 `Machine.has_unsettled_revenue=true`
8. 这里**不会再次**调用 `markOrderPaid`，因为真实资金已经在链上支付路由里完成托管；后端此时只是把控制面状态和链上事实对齐

这里的核心原则没变：

- 收益资格在支付成功后冻结，不在结果确认时才决定
- self-use 不进入 dividend-eligible
- 用户直签链上支付时，用户是直接与合约交互，后端只生成意图并同步状态
- `PWR` 直付当前仍然关闭，避免在没有 anchor 的情况下引入错误支付语义

### 3.3 启动执行

接口：`POST /api/v1/orders/{order_id}/start-execution`

后端动作：

1. 校验订单已足额支付
2. 从 `Order.execution_request` 取出：
   - `intent`
   - `files`
   - `execution_strategy`
3. 构造 `IntentRequest`
4. 调用 `ExecutionEngineService.dispatch(...)`
5. `ExecutionEngineService` 只做两件事：
   - 用通用 workload 估计推进本地硬件占用/排队模拟
   - 调 `AgentSkillOSExecutionService.submit_task(...)`
6. 创建 `ExecutionRun`
7. 机器进入 `has_active_tasks=true`

这里最关键的是：

**OutcomeX 不再在 dispatch 前做 model_router / wrapper_plan / provider match。**

### 3.4 AgentSkillOS 执行

边界实现位置：

- `code/backend/app/integrations/agentskillos_bridge.py`
- `code/backend/app/integrations/agentskillos_execution_service.py`

后端真正提交给 AgentSkillOS 的参数现在只有：

- `external_order_id`
- `prompt`（也就是 intent）
- `input_files`
- `execution_strategy`

当前 `execution_strategy` 已经被 OutcomeX 完整透传并记录进：

- `Order.execution_request`
- `ExecutionRun.submission_payload`
- 子进程环境变量 `OUTCOMEX_EXECUTION_STRATEGY`

当前实现中，AgentSkillOS 真正的执行模式仍主要由后端配置里的 `agentskillos_execution_mode` 决定；
也就是说，`execution_strategy` 已经进入正式接口，但是否在 AgentSkillOS 内部进一步消费，后续可以继续加强，而不需要 OutcomeX 再长逻辑。

### 3.5 查询 run 状态

接口：`GET /api/v1/execution-runs/{run_id}`

后端动作：

1. 从 `AgentSkillOSExecutionService.get_run(run_id)` 取 run 记录
2. 回填到 `ExecutionRun`
3. 同步更新订单：
   - run 成功：`execution_state=SUCCEEDED`、`preview_state=READY`、`state=RESULT_PENDING_CONFIRMATION`
   - run 失败：`execution_state=FAILED`
   - run 取消：`execution_state=CANCELLED`
4. 如果 run 结束，释放 `Machine.has_active_tasks`

### 3.6 用户确认结果

接口：`POST /api/v1/orders/{order_id}/confirm-result`

后端动作：

1. 校验足额支付
2. 校验 `execution_state=SUCCEEDED`
3. 校验 `preview_state=READY`
4. 校验 settlement policy 已冻结
5. 更新订单为 `RESULT_CONFIRMED`
6. 调 `OrderWriter.confirm_result(order)` 形成写链 payload

### 3.7 结算与收益分发

接口：

- `POST /api/v1/settlement/orders/{order_id}/preview`
- `POST /api/v1/settlement/orders/{order_id}/start`
- `POST /api/v1/revenue/orders/{order_id}/distribute`
- `GET /api/v1/revenue/machines/{machine_id}`

后端动作：

1. `preview`：只计算 10% / 90%，不落库
2. `start`：创建 `SettlementRecord`，状态锁定为 `LOCKED`
3. `distribute`：生成 `RevenueEntry`，把 settlement 推进到 `DISTRIBUTED`
4. 如果机器没有其他未分发收益，解除 `has_unsettled_revenue`

---

## 4. 用户完整交互路径

下面这条路径是当前代码最真实的产品闭环：

### 路径 A：用户下单到交付（HSP rail）

1. 用户在 chat 中输入目标
2. 前端调 `POST /api/v1/chat/plans`
3. 后端返回推荐方案和 quote
4. 用户选方案并上传文件
5. 前端调 `POST /api/v1/orders`
6. 后端把 `intent / files / execution_strategy` 固化为 `execution_request`
7. 后端生成链上 `createOrder` 写链 payload
8. 用户支付
9. 后端收到支付成功，冻结 settlement policy，并生成 `markOrderPaid` 写链 payload
10. 前端调 `POST /api/v1/orders/{order_id}/start-execution`
11. 后端把薄执行请求提交给 AgentSkillOS
12. AgentSkillOS 产出结果和 artifact
13. 前端轮询 `GET /api/v1/execution-runs/{run_id}`
14. run 成功后，订单进入 `RESULT_PENDING_CONFIRMATION`
15. 用户确认结果
16. 后端调 `POST /api/v1/orders/{order_id}/confirm-result`
17. 后端生成 `confirmResult` 写链 payload
18. 后端启动 settlement 和 revenue distribution

### 路径 A-2：用户下单到交付（用户直签链上 rail）

1. 用户在 chat 中输入目标
2. 前端调 `POST /api/v1/chat/plans`
3. 后端返回推荐方案和 quote
4. 用户选方案并上传文件
5. 前端调 `POST /api/v1/orders`
6. 后端把 `intent / files / execution_strategy` 固化为 `execution_request`
7. 后端生成链上 `createOrder` 写链 payload
8. 前端调 `POST /api/v1/payments/orders/{order_id}/direct-intent`
9. 后端返回 `OrderPaymentRouter` 的方法名、代币地址、签名标准和提交载荷
10. 用户在钱包里直接签 `USDC` / `USDT` 支付交易
11. 链上交易确认后，前端调 `POST /api/v1/payments/{payment_id}/sync-onchain`
12. 后端把 `Payment`、settlement policy、machine revenue guard 同步到控制面
13. 前端调 `POST /api/v1/orders/{order_id}/start-execution`
14. 后端把薄执行请求提交给 AgentSkillOS
15. AgentSkillOS 产出结果和 artifact
16. 前端轮询 `GET /api/v1/execution-runs/{run_id}`
17. run 成功后，订单进入 `RESULT_PENDING_CONFIRMATION`
18. 用户确认结果
19. 后端调 `POST /api/v1/orders/{order_id}/confirm-result`
20. 后端生成 `confirmResult` 写链 payload
21. 后端启动 settlement 和 revenue distribution

### 路径 B：机器资产流转约束

1. HSP rail 成功或链上直签 rail 同步成功后，机器都会被标记为有未结收益
2. 订单执行期间，机器会被标记为有活跃任务
3. 任一条件成立时，都不允许转移机器资产
4. run 结束后释放 active task
5. settlement distribute 完成后，如果没有其他未结收益，则释放 unsettled revenue 锁

这和 deck 里的产品 truth 是一致的：

- revenue distribution only starts after result confirmation
- platform fee is 10%, node side gets 90%
- owner self-use consumes free quota and is not dividend-eligible
- node transfer is blocked when there are active tasks or unsettled revenue

---

## 5. API / 后端对象 / AgentSkillOS / 合约方法对照

| 用户动作 | 后端接口 | 后端核心对象 | AgentSkillOS 边界 | 合约语义 |
| --- | --- | --- | --- | --- |
| chat 要结果 | `POST /api/v1/chat/plans` | `ChatPlan` | 无 | 无 |
| 创建订单 | `POST /api/v1/orders` | `Order.execution_request` / `Order.execution_metadata` | 还未真正执行 | `OrderBook.createOrder` |
| 创建 HSP 支付意图 | `POST /api/v1/payments/orders/{order_id}/intent` | `Payment` | 无 | HSP rail |
| 创建链上直签意图 | `POST /api/v1/payments/orders/{order_id}/direct-intent` | `Payment` | 无 | `OrderPaymentRouter.payWithUSDCByAuthorization` / `payWithUSDT` |
| HSP 支付确认 | `mock-confirm` / `hsp webhooks` | `Payment` / `Order` / `Machine` | 无 | `OrderBook.markOrderPaid` |
| 链上支付同步 | `POST /api/v1/payments/{payment_id}/sync-onchain` | `Payment` / `Order` / `Machine` | 无 | 用户已直接完成链上支付，后端只同步事实 |
| 启动执行 | `POST /api/v1/orders/{order_id}/start-execution` | `Order` / `ExecutionRun` | 提交 `intent/files/execution_strategy` | 当前无直接链上动作 |
| 查询执行 | `GET /api/v1/execution-runs/{run_id}` | `ExecutionRun` | 读取 run/artifacts/skills/models | 未来可映射 preview-ready receipt |
| 确认结果 | `POST /api/v1/orders/{order_id}/confirm-result` | `Order` | 无 | `OrderBook.confirmResult` |
| 开始结算 | `POST /api/v1/settlement/orders/{order_id}/start` | `SettlementRecord` | 无 | `SettlementController.settle` |
| 分发收益 | `POST /api/v1/revenue/orders/{order_id}/distribute` | `RevenueEntry` | 无 | `RevenueVault.accrueRevenue` 的 off-chain 投影 |
| 查询收益 | `GET /api/v1/revenue/machines/{machine_id}` | `RevenueEntry` | 无 | `RevenueVault` 投影视图 |

---

## 6. 关键状态机

### 6.1 Order 主状态

`PLAN_RECOMMENDED`
→ `EXECUTING`
→ `RESULT_PENDING_CONFIRMATION`
→ `RESULT_CONFIRMED`

### 6.2 ExecutionRun 状态

`QUEUED`
→ `RUNNING`
→ `SUCCEEDED | FAILED | CANCELLED`

### 6.3 Settlement 状态

`NOT_READY`
→ `READY`
→ `LOCKED`
→ `DISTRIBUTED`

### 6.4 Machine 约束状态

- `has_active_tasks=true`：执行中，不可转移
- `has_unsettled_revenue=true`：有未分发收益，不可转移

---

## 7. 合约层当前对应关系

当前仓库内已经有一套 Foundry 合约，核心包括：

- `MachineAssetNFT.sol`
- `OrderBook.sol`
- `SettlementController.sol`
- `RevenueVault.sol`
- `PWRToken.sol`

当前后端对接方式不是“用户直接每一步都打合约”，而是：

- 产品流程先走后端控制面
- 后端在关键状态点生成 deterministic 写链 payload
- 这些 payload 对应未来真实 relayer / signer / tx 广播

### 当前 `OrderWriter` 已对齐的方法

| `OrderWriter` 方法 | 对应链上语义 |
| --- | --- |
| `create_order(order)` | `createOrder` |
| `mark_order_paid(order, payment)` | `markOrderPaid` |
| `build_direct_payment_intent(order, payment)` | `OrderPaymentRouter.payWithUSDCByAuthorization` / `payWithUSDT` 直签载荷 |
| `mark_preview_ready(order)` | `markPreviewReady` |
| `confirm_result(order)` | `confirmResult` |
| `settle_order(order, settlement)` | `settleOrder` |

注意：

这些写链动作大部分现在仍是 deterministic payload / fake tx hash，不是真实广播器。
但用户直签链上支付这条路已经允许前端直接拿到真实合约调用规格，并在交易确认后把真实 `tx_hash` 同步回后端。

---

## 8. 当前实现与后续缺口

### 已经完成的

- OutcomeX backend 到 AgentSkillOS 的正式薄接口边界
- `execution_request` / `execution_metadata` / `submission_payload` 三层分离
- run 轮询、artifact 清单、skills 清单、model usage 清单回填
- 支付成功后冻结 settlement policy
- 结果确认后才能开始 settlement
- transfer guard 所需的后端状态位
- 用户直签 `USDC` / `USDT` -> backend sync-onchain 的控制面闭环

### 还没并入这个干净分支的

- `PWR` 链上直付 quote / anchor / conversion 层
- 更完整的用户直连支付前端封装与报价层
- 后端真实广播交易并回写 tx hash
- indexer 完整闭环回写
- AgentSkillOS 内部对 `execution_strategy=quality/efficiency/simplicity` 的进一步原生消费

### 但边界已经定死的

后续无论怎么扩展，OutcomeX backend 这层都不应该再长出：

- 模型家族选择器
- skill 搜索器
- solution DAG 规划器
- 多模态 provider matcher

这些必须继续留在 AgentSkillOS 内部。

---

## 9. 现在最值得记住的一句话

当前 OutcomeX 的正确理解不是“自己做一个 AI 执行引擎”，而是：

**OutcomeX 用后端控制订单、支付、结算和资产语义；用 AgentSkillOS 完成交付；再用合约把机器收益和转移约束变成可验证的资产状态。**
