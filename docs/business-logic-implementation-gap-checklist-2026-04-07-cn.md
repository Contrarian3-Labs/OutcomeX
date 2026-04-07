# OutcomeX 业务逻辑收敛主清单

更新时间：2026-04-08

依据文档：

- `docs/business-logic-target-decisions-2026-04-07-cn.md`
- `docs/business-logic-alignment-review-2026-04-07-cn.md`

用途：

- 作为 OutcomeX 当前唯一的业务逻辑收敛主文档
- 统一记录合约、后端、AgentSkillOS、前端四端还剩下的真实问题
- 后续按“功能点 / problem slice”推进修改、测试、commit、合并与推送

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

#### 当前状态

- `CONFIRMED` 路径的投影相对完整
- 但以下路径仍不够完整或不够等价：
  - `REJECTED`
  - `REFUNDED`
  - `RefundClaimed`
  - `PlatformRevenueClaimed`
  - `MachineRevenueClaimed`
- 当前 read model 仍不足以完整表达 settlement 真相

#### 涉及模块

- 合约：
  - `code/contracts/src/SettlementController.sol`
  - `code/contracts/src/OrderBook.sol`
  - 如果事件字段不足，再补事件
- 后端：
  - `code/backend/app/indexer/events.py`
  - `code/backend/app/indexer/sql_projection.py`
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

#### commit 主题

- `fix: align settlement projections with onchain truth`

---

### 4.4 Slice D - revenue overview 与 transfer guard 还没完全按 beneficiary / amount 真值收口

优先级：`P1`

#### 当前状态

- 合约真值在 `RevenueVault`：
  - `unsettledRevenueByMachine`
  - `claimableByMachineOwner`
- backend 当前仍有两类近似：
  - revenue overview 仍主要按 current owner 聚合
  - transfer readiness 仍偏布尔近似，不是金额级递减
- 这会导致：
  - 转移后收益口径偏差
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
