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
- `Slice C / E / F` 的主链路已经在当前工作区补齐并通过本地全量验证
- 本轮新增重点已完成：真实 HSP 双步链路、direct verifier fail-closed、execution run GET 只读化、PWR anchor-sized 支付、真实本地链路 E2E
- 仍未做的只剩部署相关收尾：HSP 商户环境变量、HTTPS webhook URL、线上联调 smoke

---

## 0. 2026-04-08 本轮新增收口结果

这轮新增收口的，不再只是“代码看起来合理”，而是已经有本地真实链路与全量测试支撑：

### 0.1 已完成的关键补丁

- `HSP` 正式收成双步链路：
  - backend 先 `createOrderByAdapter`
  - webhook 成功后再 `payOrderByAdapter`
  - 合约已禁用旧的 `createPaidOrderByAdapter` 一步到位入口，避免“无实款直接 paid”
- direct onchain verifier 已改成 `fail-closed`：
  - 没有真实 receipt 不再判成功
  - 必须解出 `PaymentFinalized`
  - 若是首笔 create+pay，还必须同时解出 `OrderCreated`
- `GET /execution-runs/{run_id}` 已经只读：
  - 不再把 `RESULT_CONFIRMED` 打回 `RESULT_PENDING_CONFIRMATION`
  - 不再靠前端轮询副作用释放机器 active task
- 支付终态已单向化：
  - `SUCCEEDED / FAILED / REFUNDED` 进入终态后不能再相互覆盖
  - HSP webhook 失败晚到时，不会再把已成功支付改回失败
- HSP 支付约束已收紧：
  - 金额必须与订单报价一致
  - 成功 webhook 必须带真实 `0x...` tx hash
  - 同一条成功 tx hash 不能复用于另一笔 payment
- `PWR` 直付已允许 anchor-sized amount：
  - 不再强制等于订单 gross cents
  - 仍保留 stablecoin/HSP 路径的 gross amount 严格匹配
- machine mint 已在 backend 创建时回填 `owner_chain_address`（如果 user→wallet resolver 已知）
- live indexer 投影已避免 `OrderClassified` 把 `PAID` authoritative truth 再降级回 `CLASSIFIED`

### 0.2 本轮新增验证结果

- backend 全量测试：
  - `cd code/backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q`
  - 结果：`201 passed, 1 warning`
- contracts 全量测试：
  - `cd code/contracts && forge test -vv`
  - 结果：`25 passed, 0 failed`
- 真实本地业务逻辑 E2E：
  - `cd code/backend && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/smoke/run_real_business_logic_e2e.py`
  - 结果：
    - `hsp_transfer_blocked_before_claim = true`
    - `machine_transferred_after_hsp_claim = true`
    - `pwr_refund_claim_available = true`
    - `platform_usdc_claimed = true`
    - `platform_pwr_claimed = true`
  - 落盘报告：`/tmp/outcomex-business-e2e-report.json`

### 0.3 这轮 E2E 真正覆盖了什么

#### HSP 场景

- backend 真正 mint 机器并等待 indexer 回写 owner projection
- `/chat/plans` 生成 plan，`/orders` 建单
- `/payments/orders/{order_id}/intent` 先创建链上 order anchor
- HSP webhook 成功后，backend 真实调用 `payOrderByAdapter`
- machine owner 标记 preview ready
- buyer 确认结果
- machine owner claim machine revenue
- treasury claim platform `USDC`
- revenue 未 claim 前 NFT transfer 被阻止；claim 后可以真实转移

#### PWR 场景

- buyer 创建 order
- admin 向 buyer 转 `PWR`
- buyer 对 router 做 `approve`
- buyer 直接 `payWithPWR`
- backend `sync-onchain` 只在真实 receipt 存在且事件匹配时才回写成功
- preview ready 后 buyer 走 reject 路径
- buyer 领取 refund
- machine owner 领取 machine revenue
- treasury 领取 platform `PWR`

### 0.4 仍然不是 fully-live 的部分

- HSP 商户线上环境仍未实配，因此这轮 HSP 验证是：
  - 正式业务逻辑
  - 本地链上真实合约
  - mock merchant webhook
- 要变成真正线上 HSP 联调，还需要：
  - `OUTCOMEX_HSP_APP_KEY`
  - `OUTCOMEX_HSP_APP_SECRET`
  - `OUTCOMEX_HSP_MERCHANT_PRIVATE_KEY_PEM`
  - `OUTCOMEX_HSP_PAY_TO_ADDRESS`
  - `OUTCOMEX_HSP_REDIRECT_URL`
  - `OUTCOMEX_HSP_WEBHOOK_URL`
- webhook 最终地址应为：
  - `https://<backend-domain>/api/v1/payments/hsp/webhooks`

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

### 1.5 本轮新增已收口的高优先级问题

- `HSP/adapter` 不再允许一步 create+paid 直接把订单标记成已支付
- direct payment verifier 不再在 receipt 缺失时 fail-open
- execution run 查询接口不再带状态回写副作用
- paid authoritative truth 不再被后续 `OrderClassified` 事件降级
- mint machine 时 owner 链地址可在已知 wallet mapping 下直接回填

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

状态：`进行中（本轮已补 canonical event / indexer 对齐）`

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
- backend `available-actions` 已不再直接把账户级 `refundableAmount` 回显给单个订单：
  - 现在会按 `SettlementRecord` 的订单级 refund due 与 `SettlementClaimRecord(refund)` 做 FIFO 分摊
  - 同一用户 / 同一币种有多个退款订单时，按钮与金额会落到具体 order，而不是把账户总余额误显示到每个订单
  - 前端继续消费 `refund_claim_currency / refund_claim_amount_cents`，但其语义已改为“该订单剩余可领退款”
- backend 已新增统一 claim ledger 投影与查询接口：
  - `SettlementClaimRecord` 统一记录 `refund / platform_revenue / machine_revenue`
  - `GET /api/v1/revenue/accounts/{user_id}/claims` 可返回按时间倒序的 claim history
  - `SpendWallet` 已开始消费该接口，`Refund Records / PWR Settlement Ledger` 不再完全是 placeholder
- backend 已新增 platform-side overview read API：
  - `GET /api/v1/revenue/platform/overview?currency=USDC|USDT|PWR`
  - 该接口会把 `SettlementRecord.platform_fee_cents` 与 `SettlementClaimRecord(platform_revenue)` 汇总成：
    - `projected_cents`
    - `claimed_cents`
    - `claimable_cents`
    - `claim_history`
- 合约事件 schema 已进一步收口成更富业务语义的 canonical events：
  - `OrderPaymentRouter.PaymentFinalized`
  - `SettlementController.RefundClaimedDetailed`
  - `SettlementController.PlatformRevenueClaimedDetailed`
  - `RevenueVault.MachineRevenueClaimedDetailed`
- backend live indexer/runtime 已对齐到上述 canonical events：
  - 已把 `OrderPaymentRouter` 纳入 live subscription
  - paid 真值优先来自 `PaymentFinalized`
  - refund / platform / machine claim 的 remaining-after 真值可直接进入 projection
- backend SQL projection 已按新事件字段收口：
  - direct payment 即使只拿到 `PaymentFinalized` 也能回写 `onchain_order_id / onchain_machine_id`
  - machine revenue claim 不再用“投影值减历史 claim”反推，而直接使用链上 claim amount
  - `machine.has_unsettled_revenue` 不再在 claim 后一律置 `false`，而是跟随 `remainingUnsettledRevenueByMachineAfter`
- backend `pyproject.toml` 已补 `web3` 依赖，以匹配 live indexer / smoke test 的真实运行需要
- 验证目标：
  - `code/backend`：`pytest -q tests/indexer/test_sql_projection_store.py tests/api/test_revenue_claims_api.py tests/api/test_settlement_convergence_api.py tests/api/test_order_available_actions_api.py tests/indexer/test_event_normalization.py`
  - `code/contracts`：`forge test -vv`

#### 当前状态

- `CONFIRMED / REJECTED / REFUNDED` 三条 settlement 路径已经可以在 read model 里解释
- richer claim / remaining-after 真值已进 backend；当前剩余主要是前端尚未完整消费这些字段
- 当前已经有统一 claim ledger / projection 表来记录 claim 历史与 token 维度
- refund claim 虽然链上事件没有 order id，但 backend 已通过 FIFO projection 补齐单个 order 的剩余退款读模型
- platform claim 已补正式 overview read model；当前剩余主要是前端尚未消费该 platform-side API

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

- platform claim 的 backend overview 已具备，但前端还没有专门 platform-side dashboard

本轮新增验证：

- `code/backend`：`pytest -q tests/api/test_order_available_actions_api.py tests/api/test_hsp_webhooks.py tests/integrations/test_hsp_adapter.py tests/api/test_direct_payments_api.py tests/indexer/test_sql_projection_store.py` → `31 passed`
- `code/backend`：`pytest -q tests/api/test_revenue_overview_api.py tests/api/test_revenue_claims_api.py tests/api/test_machines_api.py tests/indexer/test_sql_projection_store.py` → `21 passed`
- `code/contracts`：`forge test -vv` → `24 passed`
- `code/backend`：`source code/backend/.venv/bin/activate && PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp pytest -p no:cacheprovider -q` → `195 passed`

#### commit 主题

- `fix: align settlement projections with onchain truth`

---

### 4.4 Slice D - revenue overview 与 transfer guard 还没完全按 beneficiary / amount 真值收口

优先级：`P1`

状态：`已完成（已合并到 main）`

本轮已完成：

- `GET /api/v1/revenue/accounts/{owner_user_id}/overview` 已不再按 current owner 的 machine 集合聚合
- 现在改成：
  - `projected_cents` 按 `RevenueEntry.beneficiary_user_id`
  - `claimed_cents / withdraw_history` 按统一 claim ledger 中的 `machine_revenue` + `claimant_user_id`
- 因此“机器转手后，旧 beneficiary 的历史收益 / 已领取记录消失”这个问题已被修正
- `GET /api/v1/machines` 现已补充机器级锁定金额真值：
  - `locked_unsettled_revenue_cents`
  - `locked_unsettled_revenue_pwr`
  - `locked_beneficiary_user_ids`
- `MyMachines` / `NodeDetail` 已开始直接消费这组字段，明确区分：
  - 谁拥有这笔收益（beneficiary）
  - 是多少未领取收益仍在锁住机器转移（asset-level locked amount）
- `GET /api/v1/revenue/machines/{machine_id}` 现已补 entry/order 级 machine claim FIFO projection：
  - 每条 `RevenueEntry` 会显式返回 `claimed_cents / claimable_cents`
  - 多笔 `machine_revenue` claim 不再只体现在账户 overview 聚合值里，也能下钻到具体 order entry
- 验证：
  - `code/backend`：`pytest -q tests/api/test_revenue_overview_api.py tests/api/test_revenue_claims_api.py tests/indexer/test_sql_projection_store.py` → `13 passed`
  - `code/backend`：`pytest -q tests/api/test_machines_api.py tests/api/test_revenue_overview_api.py tests/api/test_revenue_claims_api.py` → `10 passed`
  - `code/backend`：`pytest -q tests/api/test_revenue_overview_api.py tests/api/test_revenue_claims_api.py tests/api/test_machines_api.py tests/indexer/test_sql_projection_store.py` → `21 passed`

#### 当前状态

- 合约真值在 `RevenueVault`：
  - `unsettledRevenueByMachine`
  - `claimableByMachineOwner`
- backend 现在已经同时具备：
  - beneficiary 口径的 owner overview
  - machine 口径的 locked amount 真值
  - entry/order 口径的 machine claim FIFO projection
- 因此 `Slice D` 当前不再是主 blocker

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
- machine 页面显式展示锁定金额与 beneficiary 提示，不再只给布尔 blocked / unblocked
- revenue entry 列表可下钻到 order/entry 级 claimed / claimable 真值

#### commit 主题

- `fix: make revenue overview beneficiary based and amount accurate`

---

### 4.5 Slice E - `/chat/plans` 的产品输入契约还没完整透传到 AgentSkillOS

优先级：`P1`

状态：`进行中（本轮已完成主链路透传）`

本轮已完成：

- `/api/v1/chat/plans` 现在已经正式接收并回显：
  - `user_message`
  - `mode`
  - `input_files`
- backend 已把 `input_files` 真实传入 `AgentSkillOSBridge.generate_plans(files=...)`
- backend 已把 `mode` 作为 planning preference，用于把对应 strategy 的 plan 提升到返回列表首位
- 前端 `Home -> ChatWorkspace` 已新增明确的 `quality / efficiency / simplicity` 选择，不再只是展示标签
- 前端创建 order 时也会沿用同一组 `input_files`，保持 planning 与 execution 输入一致
- 验证：
  - `code/backend`：`pytest -q tests/api/test_chat_plans_api.py tests/test_orders_execution_metadata.py tests/domain/test_planning_inputs.py` → `6 passed`
  - `forge-yield-ai`：`npm test -- src/test/plans-order-flow.test.tsx src/test/chat-workspace-api-hooks.test.tsx` → `5 passed`
  - `forge-yield-ai`：`npm run build` → `BUILD_EXIT=0`

#### 当前状态

- `code/backend/app/domain/planning.py` 已经会调用真实 `AgentSkillOSBridge.generate_plans()`
- 说明 `/chat/plans` 已不再是纯静态推荐
- 当前剩余问题已经缩小为：
  - attachment 仍是“文件名 / 引用”级输入，不是已上传文件对象
  - 更完整的 plan metadata 还没有在更多页面完全消费
  - `selected_plan_id -> execution binding` 的更强约束仍归 `Slice F`

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

状态：`进行中（本轮已完成 contract hardening + 前端 traceability 展示）`

本轮已完成：

- backend 已把 selected plan contract 明确收成 order 的执行合同：
  - `selected_plan_id`
  - `selected_plan_strategy`
  - `selected_native_plan_index`
  - `input_files`
- `start-execution` 现在会先校验 order 上的执行合同是否自洽；若 `execution_request` 与 `execution_metadata` 被篡改或不一致，会直接 `409`
- execution run 接口现在会显式返回更完整的 `selected_plan_binding`：
  - order 侧锁定的 plan id / strategy / native plan index / input files
  - submission payload 实际携带的 strategy / files / selected plan index
  - selected plan 运行结果
  - `is_consistent`
- `ExecutionRunPanel` 与 `OrderDetail` 已开始把这组 contract truth 展示给前端，不再只显示一个 plan 名称
- 前端 `Orders` 列表现在也会直接展示锁定的：
  - selected plan name
  - execution strategy
  - native plan index
- 前端 `OrderDetail` 已新增 run contract status 摘要：
  - 当前 run 是否与锁定 plan 一致
  - run 实际提交的 selected plan id / strategy / native plan index
- 前端 `ExecutionRunPanel` 的 binding 文案已收成更明确的合同语义：
  - `Order locked plan`
  - `Run submitted plan`
  - `Run submitted strategy`
  - `Execution returned plan`
  - `Contract verdict`
- 验证：
  - `code/backend`：`pytest -q tests/api/test_execution_runs_api.py` → `11 passed`
  - `forge-yield-ai`：`npm test -- src/test/orders-list.test.tsx src/test/execution-run-panel.test.tsx src/test/order-detail-wallet-actions.test.tsx` → `18 passed`
  - `forge-yield-ai`：`npm run build` → `BUILD_EXIT=0`

#### 当前状态

- backend 已经会把：
  - `execution_strategy`
  - `selected_native_plan_index`

往 AgentSkillOS 执行入口传递
- `ExecutionEngineService` 也会按 strategy 影响 workload admission
- 当前剩余问题已经缩小为：
  - history / list / detail 的前端 traceability 已补齐，本 slice 剩余主要是最终合并与状态同步

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

---

## 2026-04-08 补充进展：前端 richer onchain truth + live indexer 联调

### A. 前端页面已接 richer onchain truth

本轮新增/收口的前端重点，不再只消费“是否成功”的平面字段，而是直接消费 backend 已经暴露出的更丰富链上投影：

- `AssetYield`
  - 接入 `GET /api/v1/revenue/machines/{machine_id}`
  - 展示 machine-level 锁定收益、beneficiary、transfer guard、recent machine revenue entries
- `SpendWallet`
  - 接入统一 claim history 投影
  - machine-side claim 与 refund claim 都展示真实 tx hash / timestamp / amount
- `OrderDetail`
  - `Live Order State` 直接展示：
    - `create_order_tx_hash`
    - `onchain_order_id`
    - `onchain_machine_id`
    - `latest_success_payment_currency`
    - `settlement_beneficiary_user_id`
    - `settlement_is_dividend_eligible`
    - `settlement_is_self_use`
  - `Available Actions` 直接展示 refund claim currency / amount truth

对应前端改动文件：

- `forge-yield-ai/src/pages/AssetYield.tsx`
- `forge-yield-ai/src/pages/SpendWallet.tsx`
- `forge-yield-ai/src/pages/OrderDetail.tsx`
- `forge-yield-ai/src/hooks/use-outcomex-api.ts`
- `forge-yield-ai/src/lib/api/outcomex-client.ts`
- `forge-yield-ai/src/lib/api/outcomex-types.ts`
- `forge-yield-ai/src/lib/api/query-keys.ts`
- `forge-yield-ai/src/lib/machines-api.ts`
- `forge-yield-ai/src/lib/projection-success.ts`

新增/更新验证：

- `forge-yield-ai`：
  - `npm test -- src/test/asset-yield-claim.test.tsx src/test/spend-wallet-ledger.test.tsx src/test/order-detail-wallet-actions.test.tsx` → `13 passed`
  - `npm test -- src/test/asset-yield-claim.test.tsx src/test/spend-wallet-ledger.test.tsx src/test/order-detail-wallet-actions.test.tsx src/test/order-detail-hsp-payment.test.tsx src/test/product-closure.test.tsx` → `25 passed`
  - `npm run build` → `built in 1m 24s`

### B. live indexer 本地联调已真实跑通

本轮不是只跑 pytest / Foundry，而是起了本地 Anvil（chain id `133`）、真实部署合约、打开 backend live indexer，然后走了一条真实链路：

1. `create_machine`
   - backend 发真实 mint tx
   - live indexer 回写 `owner_projection_last_event_id / owner_chain_address`
2. HSP mock-merchant webhook
   - backend 走真实 `createPaidOrderByAdapter`
   - 本地链上产生真实 `OrderCreated / PaymentFinalized`
3. `mock-result-ready`
   - machine owner 发真实 `markPreviewReady`
4. buyer 发真实 `confirmResult`
   - live indexer 投影出 confirmed settlement / revenue entry
5. machine owner 发真实 `claimMachineRevenue`
   - live indexer 回写 machine claim history / machine unlock state

本轮联调的真实结果已经落盘到：

- `/tmp/outcomex-live-indexer-report.json`

关键结果：

- machine ownership projection：
  - `owner_chain_address = 0x70997970c51812dc3a010c7d01b50e0d17dc79c8`
  - `owner_projection_last_event_id = 133:10:...`
- payment projection：
  - `onchain_order_id = 2`
  - `create_order_tx_hash = 0xf346...7a92`
  - `latest_success_payment_currency = USDC`
- preview / confirm projection：
  - `onchain_preview_ready_tx_hash = 0x1763...e3bf`
  - `confirm_tx_hash = 0x708e...7d99`
  - order 终态：`result_confirmed + distributed`
- revenue projection：
  - claim 前 machine revenue entry：`claimable_cents = 900`
  - claim 后 machine revenue entry：`claimed_cents = 900`, `claimable_cents = 0`
  - owner overview：`currency = PWR`, `projected_cents = 900`, `claimed_cents = 900`, `claimable_cents = 0`
  - owner claim history：machine revenue claim 现在也会规范化成 `currency = PWR`，不再返回 `null`
  - machine claim 后：
    - `transfer_ready = true`
    - `locked_unsettled_revenue_cents = 0`

这条联调说明：

- frontend 这轮接的 richer truth 已经有真实 backend / chain 数据支撑，不只是 mock payload
- `owner_projection_last_event_id`、machine revenue entry、claim history 这些字段确实来自 live indexer 投影，而不是前端或 route 直接写死
- claim/payment/yield 三类页面现在都可以围绕同一套 authoritative projection 展示
