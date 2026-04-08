# OutcomeX 业务逻辑收敛主清单

更新时间：2026-04-08

依据文档：

- `docs/business-logic-target-decisions-2026-04-07-cn.md`
- `docs/business-logic-alignment-review-2026-04-07-cn.md`

用途：

- 作为 OutcomeX 当前唯一的业务逻辑收敛主文档
- 统一记录合约、后端、AgentSkillOS、前端四端还剩下的真实问题
- 后续按“功能点 / problem slice”推进修改、测试、commit、合并与推送

当前进度：

- `Slice A` 已完成并合并到 `main`
- `Slice B` 已完成并合并到 `main`
- 下一步进入 `Slice C`：`settlement / refund / claim projection` 与链上真值对齐

---

## 1. 当前已经基本收口的内容

以下方向按当前代码状态，已经不再是主 blocker：

### 1.1 wallet-first 默认边界

- 默认采用 wallet-first
- 普通用户经济动作不再默认走 backend 代发
- 目前保留的 backend 发链例外仍是：
  - `createOrder`
  - `mint NFT`
  - `HSP adapter markPaid`

### 1.2 非支付用户动作的大方向

- `confirm result`
- `reject valid preview`
- `claim refund`
- `claim machine revenue`
- `transfer NFT`

以上大方向已经基本收成前端钱包直调合约，backend 主要负责读接口、投影、状态同步。

### 1.3 订单支付成功不再由前端本地臆断

- `sync-onchain success`
- `wallet mined`

不再应被视为业务成功本身；前端已经基本改成等待 authoritative paid projection。

### 1.4 AgentSkillOS 已经是执行内核薄边界

- backend 已把 AgentSkillOS 当作 execution kernel
- 不是自己重写 orchestration 内核
- 当前问题主要不在“是否接了 AgentSkillOS”，而在“产品输入契约与执行约束是否完全透传”

---

## 2. 第一轮收口范围

本轮只处理六个“闭环真值”问题，不包含以下内容：

- 机器二级市场 / live machine acquisition
- `NodeExecutionProfile` 的正式持久化
- 大范围页面去 mock 化

这些内容留到第二轮。

---

## 3. 收口原则

### 3.1 一律按 problem slice 推进

不再按“先把某一层全部修完”推进，而是按功能点 / 问题切片推进：

- 同一个问题同时修改：
  - 合约
  - 后端
  - AgentSkillOS 接口层
  - 前端
  - 本主文档

### 3.2 每个 slice 必须独立提交

每个 problem slice 都要：

- 修改代码
- 补测试
- 本地验证
- 更新本主文档
- 单独 commit
- 合并到 `main`
- push 远端

### 3.3 authoritative truth 的优先级

统一遵循：

- 合约事件与状态是真值源
- backend projection / read model 是前端唯一业务真相来源
- 前端不再自行推导关键业务状态来决定按钮开放与否

---

## 4. 剩余问题总表

### 4.1 Slice A - 未支付订单失效机制还没做实

优先级：`P0`

状态：`已完成（已合并到 main）`

验证：

- `code/contracts`：`forge test --match-contract "OutcomeXLifecycleTest|OrderPaymentRouterTest" -vv` → `24 passed`
- `code/backend`：`pytest -p no:cacheprovider tests/api/test_execution_runs_api.py tests/indexer/test_event_normalization.py tests/indexer/test_evm_runtime.py tests/indexer/test_sql_projection_store.py tests/test_order_models.py tests/test_alembic_migrations.py -q` → `26 passed`
- `forge-yield-ai`：`npm test -- src/lib/order-presentation.test.ts src/test/order-detail-wallet-actions.test.tsx src/test/execution-run-panel.test.tsx src/test/order-detail-direct-payment-copy.test.ts` → `18 passed`

收口结果：

- 合约已补齐 unpaid TTL / buyer cancel / expire cleanup 真值，并持久化区分“主动取消”与“超时失效”
- backend 已把 `OrderCancelled` 归一化并投影到 authoritative order truth，`start-execution` 也已按 authoritative paid / expired / cancelled / unavailable 真值 gating
- 前端已统一改为消费 authoritative projected truth，不再混用 `payment_state`、`onchain_order_id` 与本地推导

残余风险：

- Alembic migration 已补文件与轻量测试，但当前本地环境缺少 Alembic runtime，尚未实际执行一次真实 `alembic upgrade`

#### 当前状态

- `code/contracts/src/OrderBook.sol` 目前没有：
  - `deadline`
  - `expire`
  - `cancel cleanup`
  - `OrderCancelled` 一类未支付失效真值
- 订单创建后，如果不支付，链上没有自然失效语义
- backend / frontend 也缺少严格围绕该真值的展示与 gating

#### 涉及模块

- 合约：
  - `code/contracts/src/OrderBook.sol`
  - 相关 `Order*` 测试
- 后端：
  - `code/backend/app/indexer/events.py`
  - `code/backend/app/indexer/sql_projection.py`
  - `code/backend/app/api/routes/orders.py`
  - 定时 cleanup / order services
- 前端：
  - `forge-yield-ai/src/pages/OrderDetail.tsx`
  - 相关 order presentation / tests

#### 目标状态

- 订单创建后拥有明确的 unpaid expiry truth
- 超过 10 分钟未支付的订单在业务上失效
- 前端只消费 read model 中的 `pending / expired / cancelled`
- `Start Execution` 不可能在未支付或已失效订单上开放

#### commit 主题

- `fix: enforce unpaid order expiry across contract backend frontend`

---

### 4.2 Slice B - HSP 还没有真正成为稳定币正式主路径

优先级：`P0/P1`

状态：`已完成（已合并到 main）`

验证：

- `code/backend`：`pytest -q code/backend/tests/api/test_hsp_webhooks.py code/backend/tests/api/test_direct_payments_api.py code/backend/tests/indexer/test_sql_projection_store.py code/backend/tests/api/test_execution_runs_api.py code/backend/tests/runtime/test_cost_service.py` → `37 passed`
- `forge-yield-ai`：`npm test -- src/test/order-detail-hsp-payment.test.tsx src/test/order-detail-wallet-actions.test.tsx src/test/order-detail-direct-payment-copy.test.ts src/test/product-closure.test.tsx` → `23 passed`

收口结果：

- backend 已把稳定币默认支付意图收敛成 HSP 主路径，`/payments/orders/{order_id}/intent` 默认走 `USDC` 且仅接受 `USDC/USDT`
- backend 已把 `direct-intent` 收敛成兼容性 PWR 入口，稳定币不再继续从该接口扩散
- HSP webhook 与 SQL projection 已统一按 authoritative paid truth 回写，前端不再把 checkout 创建成功误当成支付成功
- 前端 `OrderDetail` 已把 `USDC/USDT via HSP` 提升为正式主路径，并补齐 checkout 创建、projection pending、projection synced、失败重试与订单切换防串单保护

残余风险：

- 当前这一 slice 只收口了“稳定币主支付轨”，没有改变 HSP 适配器是否具备真实资金入账校验这一更底层安全问题；该问题仍在更高优先级审计项中单独跟踪

#### HSP 当前接入状态记录（2026-04-08）

- backend 已按 `merchant-docs-all-in-one.pdf` 补齐真实接入骨架：
  - Merchant API HMAC 请求签名
  - `ES256K` 的 `merchant_authorization` JWT
  - 文档格式的 webhook 验签：`X-Signature: t=...,v1=...`
  - `.env.example` 模板
- 当前代码仍未切到真实 HSP 线上联调，原因是部署环境变量尚未正式填写
- 目前默认仍可在 `dev/test` 下走 mock-compatible checkout；只有当 HSP live 配置填完整后，才会真正调用 HashKey Merchant API
- 当前尚未填写 / 尚未在服务器侧确认的关键项：
  - `OUTCOMEX_HSP_APP_KEY`
  - `OUTCOMEX_HSP_APP_SECRET`
  - `OUTCOMEX_HSP_MERCHANT_PRIVATE_KEY_PEM`
  - `OUTCOMEX_HSP_PAY_TO_ADDRESS`
  - `OUTCOMEX_HSP_REDIRECT_URL`
  - `OUTCOMEX_HSP_WEBHOOK_URL`
- webhook 最终应配置为：
  - 生产：`https://<backend-domain>/api/v1/payments/hsp/webhooks`
  - 本地联调：`https://<public-tunnel-domain>/api/v1/payments/hsp/webhooks`
- 注意：HashKey Merchant 文档要求 `webhook_url` 必须是 `HTTPS`，因此部署前不能用裸 `localhost`
- 结论：HSP 代码层已到“待填环境变量 + 待服务器部署联调”的状态；最后收尾时只需要填好 `.env`、配置 merchant console 中的 webhook URL、再做一次真实 create-order / webhook smoke

#### 当前状态

- 合约侧 `HSP adapter -> escrow -> mark paid` 路径已经比早期真实很多
- `code/backend/app/api/routes/hsp_webhooks.py` 已能 ingest HSP webhook
- 但产品主路径仍未完整围绕 HSP 建立：
  - checkout
  - pending
  - confirmed
  - projection synced
- 前台用户心智上，稳定币主支付轨仍不够清晰

#### 涉及模块

- 合约：
  - `code/contracts/src/OrderPaymentRouter.sol`
  - 必要时补 HSP 相关测试
- 后端：
  - `code/backend/app/api/routes/payments.py`
  - `code/backend/app/api/routes/hsp_webhooks.py`
  - `code/backend/app/integrations/hsp_adapter.py`
  - `code/backend/app/indexer/sql_projection.py`
- 前端：
  - `forge-yield-ai/src/pages/OrderDetail.tsx`
  - `forge-yield-ai/src/lib/api/outcomex-client.ts`
  - `forge-yield-ai/src/hooks/use-outcomex-api.ts`

#### 目标状态

- 稳定币正式支付只呈现 `USDC/USDT via HSP`
- 前端有完整的 HSP 状态流转
- paid 仍以 authoritative projection 为准
- 不再把直连稳定币路径作为正式产品主路径继续扩散

#### commit 主题

- `feat: ship hsp as the primary stablecoin payment flow`

---

### 4.3 Slice C - settlement / refund / claim projection 还不等价于链上真相

优先级：`P1`

状态：`进行中`

本轮已完成：

- backend projection 已补齐 `REJECTED` 路径的 read model 落地：
  - `70% refund / 30% rejection fee`
  - `10% platform / 90% machine` 的 rejection fee split
  - `SettlementRecord / RevenueEntry / machine.has_unsettled_revenue` 现在可被完整解释
- backend projection 已补齐 `REFUNDED` 路径的 read model 落地：
  - `SettlementRecord / RevenueEntry` 会落成 `0 platform / 0 machine`
  - direct payment 会同步标记为 `PaymentState.REFUNDED`
  - order 会进入 `CANCELLED + DISTRIBUTED`，与退款完成后的按钮 gating 更一致
- 合约 `RefundClaimed / PlatformRevenueClaimed` 事件已补 token 维度，便于后端对链上 claim 轨做 token-aware normalization
- backend `available-actions` 已在链上 runtime 打开时改为读取 authoritative `refundableAmount`，前端也开始消费 `refund_claim_currency / refund_claim_amount_cents`
- backend 已新增统一 claim ledger 投影与查询接口：
  - `SettlementClaimRecord` 统一记录 `refund / platform_revenue / machine_revenue`
  - `GET /api/v1/revenue/accounts/{user_id}/claims` 可返回按时间倒序的 claim history
  - `SpendWallet` 已开始消费该接口，`Refund Records / PWR Settlement Ledger` 不再完全是 placeholder
- 验证目标：
  - `code/backend`：`pytest -q tests/indexer/test_sql_projection_store.py tests/api/test_revenue_claims_api.py tests/api/test_settlement_convergence_api.py tests/api/test_order_available_actions_api.py tests/indexer/test_event_normalization.py`
  - `code/contracts`：`forge test -vv`

#### 当前状态

- `CONFIRMED / REJECTED / REFUNDED` 三条 settlement 路径已经可以在 read model 里解释
- 但以下 claim read model 仍不够完整或不够等价：
  - `RefundClaimed`
  - `PlatformRevenueClaimed`
  - `MachineRevenueClaimed`
- 当前已经有统一 claim ledger / projection 表来记录 claim 历史与 token 维度
- 但它仍缺少 order 维度，因此 refund claim 暂时只能做到“账户级 / 币种级”已领取状态，而不是精确回写到单个 order

#### 涉及模块

- 合约：
  - `code/contracts/src/SettlementController.sol`
  - `code/contracts/src/OrderBook.sol`
- 后端：
  - `code/backend/app/indexer/events.py`
  - `code/backend/app/indexer/evm_runtime.py`
  - `code/backend/app/indexer/sql_projection.py`
  - `code/backend/app/api/routes/orders.py`
  - `code/backend/app/api/routes/settlement.py`
  - `code/backend/app/api/routes/revenue.py`
- 前端：
  - `forge-yield-ai/src/pages/OrderDetail.tsx`
  - `forge-yield-ai/src/pages/AssetYield.tsx`
  - 相关 wallet action / product closure tests

#### 目标状态

- settlement、reject、refund、claim 在 read model 层都可被完整解释
- 前端所有相关状态与按钮开放逻辑都以后端 projection 为准
- 不再靠页面本地公式或静态判断替代真实投影

未完成项：

- refund claim 仍未精确绑定到单个 order，因为现有链上事件没有 order 维度
- platform claim 虽已进入统一 claim ledger，但前端还没有专门 platform-side dashboard
- `AssetYield` 侧 machine claim history / overview 仍是 beneficiary 聚合问题，后续会与 `Slice D` 一起收口

#### commit 主题

- `fix: align settlement projections with onchain truth`

---

### 4.4 Slice D - revenue overview 与 transfer guard 还没完全按 beneficiary / amount 真值收口

优先级：`P1`

状态：`进行中`

本轮已完成：

- `GET /api/v1/revenue/accounts/{owner_user_id}/overview` 已不再按 current owner 的 machine 集合聚合
- 现在改成：
  - `projected_cents` 按 `RevenueEntry.beneficiary_user_id`
  - `claimed_cents / withdraw_history` 按统一 claim ledger 中的 `machine_revenue` + `claimant_user_id`
- 因此“机器转手后，旧 beneficiary 的历史收益 / 已领取记录消失”这个问题已被修正
- 验证：
  - `code/backend`：`pytest -q tests/api/test_revenue_overview_api.py tests/api/test_revenue_claims_api.py tests/indexer/test_sql_projection_store.py` → `13 passed`

#### 当前状态

- 合约真值在 `RevenueVault`：
  - `unsettledRevenueByMachine`
  - `claimableByMachineOwner`
- backend 当前仍有两类近似：
  - machine-level summary 仍主要按 machine aggregate 聚合
  - transfer readiness 仍偏布尔近似，不是金额级递减
- 这会导致：
  - 某些 machine 页面展示仍未完全区分“asset-level locked amount”与“beneficiary-level revenue ownership”
  - claim 后 transfer guard 与合约 guard 可能不完全一致

#### 涉及模块

- 合约：
  - `code/contracts/src/RevenueVault.sol`
  - `code/contracts/src/OrderBook.sol`
- 后端：
  - `code/backend/app/domain/settlement_projection.py`
  - `code/backend/app/indexer/sql_projection.py`
  - `code/backend/app/api/routes/revenue.py`
  - `code/backend/app/api/routes/machines.py`
- 前端：
  - `forge-yield-ai/src/pages/AssetYield.tsx`
  - `forge-yield-ai/src/pages/MyMachines.tsx`
  - `forge-yield-ai/src/lib/machines-api.ts`

#### 目标状态

- revenue overview 按 beneficiary 口径聚合
- unsettled revenue / claim 后余额按 amount 真值变化
- transfer readiness 只由权威 read model 决定

#### commit 主题

- `fix: make revenue overview beneficiary based and amount accurate`

---

### 4.5 Slice E - `/chat/plans` 的产品输入契约还没完整透传到 AgentSkillOS

优先级：`P1`

#### 当前状态

- `code/backend/app/domain/planning.py` 已经会调用真实 `AgentSkillOSBridge.generate_plans()`
- 说明 `/chat/plans` 已不再是纯静态推荐
- 但产品输入契约仍不完整：
  - 还没有正式透传 `attachments / input_files`
  - `mode` 仍未成为清晰产品输入
  - 前端 `ChatWorkspace` 仍以 `user_message` 为主

#### 涉及模块

- AgentSkillOS 接口层：
  - `code/backend/app/integrations/agentskillos_bridge.py`
- 后端：
  - `code/backend/app/schemas/chat_plan.py`
  - `code/backend/app/api/routes/chat_plans.py`
  - 相关 planning tests
- 前端：
  - `forge-yield-ai/src/pages/ChatWorkspace.tsx`
  - `forge-yield-ai/src/lib/api/outcomex-types.ts`
  - `forge-yield-ai/src/lib/plans-order-api.ts`

#### 目标状态

- `/chat/plans` 接收真实产品输入：
  - `user_message`
  - `mode`
  - `attachments / input_files`
- `quality / efficiency / simplicity` 成为清晰 planning 输入
- plan 卡片与 selected plan 元信息来自真实 AgentSkillOS planning contract

#### commit 主题

- `feat: pass real planning inputs into agentskillos chat plans`

---

### 4.6 Slice F - execution strategy 还没有完全成为强执行契约

优先级：`P1`

#### 当前状态

- backend 已经会把：
  - `execution_strategy`
  - `selected_native_plan_index`

往 AgentSkillOS 执行入口传递
- `ExecutionEngineService` 也会按 strategy 影响 workload admission
- 但 end-to-end 产品契约还没完全收死：
  - 下单选中的 plan 与执行采用的 plan 需要更明确锁定
  - 前端对“已锁定计划”的表达还不够强

#### 涉及模块

- AgentSkillOS 接口层：
  - `code/backend/app/integrations/agentskillos_execution_service.py`
- 后端：
  - `code/backend/app/execution/service.py`
  - `code/backend/app/api/routes/orders.py`
  - order / execution schemas
- 前端：
  - `forge-yield-ai/src/pages/ChatWorkspace.tsx`
  - `forge-yield-ai/src/pages/OrderDetail.tsx`
  - plans/order flow tests

#### 目标状态

- 用户在 plans 阶段选中的 plan 被真实锁定进 order
- execution submit payload 与 selected native plan 对齐
- 执行结果可回溯到用户选中的 strategy / native plan

#### commit 主题

- `fix: harden selected plan as execution contract`

---

## 5. 本轮明确不处理的内容

以下问题存在，但不进入本轮：

- 机器二级市场 / `sell / buy NFT` 的正式站内机制
- `NodePurchase` 从 preview 升级为 live acquisition
- `NodeExecutionProfile` 从 local draft 升级为正式 policy persistence
- 大范围页面去 mock 化
- self-use 正式控制面

这些内容在第二轮再收。

---

## 6. 执行顺序

本轮执行顺序固定为：

1. Slice A - 未支付订单失效
2. Slice B - HSP 主路径
3. Slice C - settlement / refund / claim projection
4. Slice D - beneficiary revenue / transfer guard
5. Slice E - `/chat/plans` 输入契约
6. Slice F - execution strategy 强约束

---

## 7. 完成标准

当本轮完成时，应满足：

- 合约、后端、前端围绕这六个问题的行为一致
- AgentSkillOS 在 planning 与 execution 两侧都收到完整产品输入
- 每个问题都有独立 commit，可单独 review / 回滚
- 修改已合并到 `main` 并推送远端
- 本主文档会在每个 slice 完成后同步更新状态

---

## 8. 状态维护方式

后续只维护本文件，不再要求同步维护多份平行状态文档。

建议在每个 slice 完成后追加一行状态：

- `状态：未开始 / 进行中 / 已完成`
- `commit: <hash>`
- `验证：<tests / local e2e / manual flow>`

## 建议执行顺序

1. P0-1 / P0-2 / P0-3 / P0-4
2. P1-1 / P1-2
3. P1-3 / P1-4
4. P1-5 / P1-6 / P1-7
5. P1.5 分阶段收敛
6. 最后统一文档与前端文案
