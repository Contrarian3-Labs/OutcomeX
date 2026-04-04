# OutcomeX 实施映射与 Implementation Checklist

本文档把产品路径、后端模块、合约模块、索引器状态同步和下一步实施任务放到同一张图里，方便直接按模块推进开发。

目标不是重复产品叙事，而是回答下面四个工程问题：

- 一个用户动作最终落到哪些后端文件、哪些合约、哪些索引器投影
- 当前仓库已经覆盖到哪一步，还缺什么
- 哪些能力应该由后端主导，哪些应该由用户钱包直连合约
- 下一阶段应该按什么顺序补齐，才能最快形成可演示的完整闭环

## 1. 总体实施原则

OutcomeX 最终应采用混合交互模型：

- 产品控制面：用户 -> 后端 -> 合约 -> indexer -> 后端
- 资产/钱包动作：用户钱包 -> 合约 -> indexer -> 后端

这意味着工程拆分要遵循三条原则：

1. 后端负责产品语义
   - chat
   - 推荐方案
   - execution orchestration
   - preview policy
   - confirmation gating
   - settlement classification freeze
2. 合约负责资产与结算真相
   - 订单收据
   - 支付入账语义
   - settlement split
   - machine-side accrual
   - claim
   - transfer guard
3. indexer 负责把链上真相投影回产品查询模型
   - order payment state
   - confirmation state
   - claimable revenue
   - machine transfer block state
   - ownership / listing / transfer state

## 2. 产品路径到代码模块映射

## 2.1 Chat 输入 -> 推荐方案 -> 官方报价

### 当前用户动作

- 用户在 chat 输入一个 deliverable goal

### 当前后端模块

- `code/backend/app/api/routes/chat_plans.py`
- `code/backend/app/domain/planning.py`
- `code/backend/app/execution/normalizer.py`
- `code/backend/app/execution/matcher.py`
- `code/backend/app/execution/service.py`
- `code/backend/app/runtime/hardware_simulator.py`
- `code/backend/app/runtime/preview_policy.py`

### 当前缺失模块

- `code/backend/app/runtime/cost_service.py`
- `code/backend/app/execution/agentskillos_wrapper.py`
- `code/backend/app/integrations/model_router.py`

### 当前状态

已实现：

- intent -> recipe
- recipe -> provider match
- 基础 runtime capacity simulation
- preview policy

未实现：

- RuntimeCostService 统一报价
- AgentSkillOS 多步 orchestration wrapper
- model family / provider 的统一路由与成本回传
- solution memory 回写

### 下一步动作

- 用 `RuntimeCostService` 统一产出 quote、runtime cost、PWR anchor quote
- 用 `AgentSkillOSWrapper.plan(...)` 替换纯本地 single-step 规划入口
- 用 `ModelRouter` 统一对接 Alibaba API / MuleRouter / 本地模型族

## 2.2 选择方案 -> 创建订单

### 当前用户动作

- 用户选择一个推荐方案并创建订单

### 当前后端模块

- `code/backend/app/api/routes/orders.py`
- `code/backend/app/domain/models.py`
- `code/backend/app/schemas/order.py`

### 当前合约模块

- `code/contracts/src/OrderBook.sol`

### 当前链边界

- `code/backend/app/onchain/adapter.py`
- `code/backend/app/indexer/events.py`
- `code/backend/app/indexer/projections.py`

### 当前状态

已实现：

- backend order object
- contract order receipt state machine

未实现：

- 后端统一写链入口
- backend order id 与 on-chain order id 的稳定映射写入器

### 下一步动作

建议新增：

- `code/backend/app/onchain/order_writer.py`

职责：

- 封装 `createOrder`
- 封装 `markOrderPaid`
- 封装 `markPreviewReady`
- 封装 `confirmResult`
- 封装 settlement 写链动作
- 统一记录 tx hash、nonce、回执、重试语义

## 2.3 支付路径 A：`USDT/USDC via HSP`

### 当前用户动作

- 用户在前端点击官方支付入口
- 跳转 HSP checkout

### 当前后端模块

- `code/backend/app/api/routes/payments.py`
- `code/backend/app/integrations/hsp_adapter.py`
- `code/backend/app/domain/rules.py`

### 当前状态

已实现：

- mock payment intent
- mock payment confirm
- full paid 后冻结 settlement policy

未实现：

- HSP merchant API 真实下单
- webhook 验签
- callback 去重
- callback 成功后统一写链
- HSP payment reference 与 order receipt 的稳定关联

### 下一步动作

修改：

- `code/backend/app/integrations/hsp_adapter.py`
- `code/backend/app/api/routes/payments.py`

新增建议：

- `code/backend/app/api/routes/hsp_webhooks.py`
- `code/backend/app/onchain/order_writer.py`

Checklist：

- [ ] `create_payment_intent` 改为真实 merchant order 创建
- [ ] 记录 `payment_url / flow_id / merchant_order_id`
- [ ] 新增 webhook 验签与重放保护
- [ ] callback 成功后冻结 `settlement_beneficiary_user_id`
- [ ] callback 成功后调用 `OrderBook.markOrderPaid(...)`
- [ ] indexer 回写支付成功状态

## 2.4 支付路径 B：`USDT/USDC direct contract pay`

### 当前用户动作

- 用户钱包直接签稳定币支付交易

### 当前合约模块

当前仓库里还没有专门的支付路由合约，需要新增。

建议新增：

- `code/contracts/src/OrderPaymentRouter.sol`

### 当前后端模块

当前还没有 direct pay 的 API 协助层，建议补：

- `code/backend/app/api/routes/payments.py` 扩展 direct pay quote 接口
- `code/backend/app/onchain/order_writer.py` 只负责读取/校验，不代签 direct pay

### 当前链边界

- `code/backend/app/indexer/events.py`
- `code/backend/app/indexer/projections.py`
- `code/backend/app/integrations/onchain_indexer.py`

### 当前状态

未实现：

- `USDC` 的 `EIP-3009` 支付入口
- `USDT` 的 `Permit2` 支付入口
- 支付事件 -> order paid projection

### 下一步动作

Checklist：

- [ ] `OrderPaymentRouter.sol` 支持 `payWithUSDCByAuthorization(...)`
- [ ] `OrderPaymentRouter.sol` 支持 `payWithUSDT(...)`
- [ ] 支付成功后调用 `OrderBook.markOrderPaid(...)`
- [ ] 发出 `OrderPaymentReceived` 事件
- [ ] indexer 增加 direct pay 事件解析与投影
- [ ] backend 新增 direct pay 参数下发接口

## 2.5 支付路径 C：`PWR pay`

### 当前用户动作

- 用户在订单页点击 `Use PWR`
- 钱包直签 `approve + payWithPWR`

### 当前合约模块

已存在：

- `code/contracts/src/PWRToken.sol`
- `code/contracts/src/RevenueVault.sol`

待新增：

- `code/contracts/src/OrderPaymentRouter.sol`

### 当前状态

已实现：

- PWR mint / claim 语义

未实现：

- 用户拿 PWR 支付订单
- PWR 扣款与订单 paid 状态连接

### 下一步动作

Checklist：

- [ ] `OrderPaymentRouter.sol` 支持 `payWithPWR(orderId, amount)`
- [ ] 合约记录 PWR 支付金额与订单 paid 状态
- [ ] indexer 回写 `pwr_paid` payment source
- [ ] backend 在 quote 阶段返回 `PWR quote`

## 2.6 执行调度 -> Preview -> Confirm

### 当前后端模块

- `code/backend/app/api/routes/orders.py`
- `code/backend/app/execution/service.py`
- `code/backend/app/runtime/preview_policy.py`
- `code/backend/app/runtime/hardware_simulator.py`

### 当前合约模块

- `code/contracts/src/OrderBook.sol`

### 当前状态

已实现：

- mock result ready
- preview ready
- confirm result

未实现：

- 真正的 execution job lifecycle
- artifact 存储
- preview mask / text truncation / watermark
- execution completion -> markPreviewReady 的自动写链

### 下一步动作

建议新增：

- `code/backend/app/artifacts/storage.py`
- `code/backend/app/artifacts/preview_builder.py`
- `code/backend/app/execution/agentskillos_wrapper.py`

Checklist：

- [ ] execution dispatch 结果落库
- [ ] artifact metadata 与 preview metadata 落库
- [ ] preview policy 输出真正可展示的 preview artifact
- [ ] execution completed 后调用 `markPreviewReady(validPreview)`
- [ ] confirm-result 路由改为读取真实 execution / preview 状态

## 2.7 Settlement -> Revenue Accrual -> Claim

### 当前后端模块

- `code/backend/app/api/routes/settlement.py`
- `code/backend/app/api/routes/revenue.py`
- `code/backend/app/domain/models.py`

### 当前合约模块

- `code/contracts/src/SettlementController.sol`
- `code/contracts/src/RevenueVault.sol`

### 当前状态

已实现：

- backend settlement record
- backend revenue entry
- contract settlement split
- contract revenue accrual
- claim

未实现：

- 真实 stablecoin / PWR 资金入账后的一致性对账
- platform accrued stablecoin 提现路径
- refund entitlement 的前后端统一展示

### 下一步动作

Checklist：

- [ ] settlement route 改为以后端发起链上 settlement 为准
- [ ] indexer 回写 `OrderSettled / Settled / RevenueAccrued`
- [ ] revenue query 页读取链上投影后的 claimable balance
- [ ] refund claim 与 platform claim 增加只读查询接口

## 2.8 NFT 购买 / 转移 / 收益 claim

### 当前合约模块

- `code/contracts/src/MachineAssetNFT.sol`
- `code/contracts/src/OrderBook.sol`
- `code/contracts/src/RevenueVault.sol`

### 当前状态

已实现：

- transfer guard
- active task / unsettled revenue 阻止转移
- revenue claim

未实现：

- marketplace 购买入口
- ownership / listing 的产品层查询模型

### 下一步动作

Checklist：

- [ ] 补 machine marketplace 合约或 listing 读模型
- [ ] indexer 投影 owner / transfer / claimable state
- [ ] machines route 返回 transfer blocked 原因

## 3. 核心文件职责清单

## 3.1 已存在且应继续沿用

后端：

- `code/backend/app/api/routes/chat_plans.py`
- `code/backend/app/api/routes/orders.py`
- `code/backend/app/api/routes/payments.py`
- `code/backend/app/api/routes/settlement.py`
- `code/backend/app/api/routes/revenue.py`
- `code/backend/app/execution/service.py`
- `code/backend/app/execution/normalizer.py`
- `code/backend/app/execution/matcher.py`
- `code/backend/app/runtime/hardware_simulator.py`
- `code/backend/app/runtime/preview_policy.py`
- `code/backend/app/integrations/hsp_adapter.py`
- `code/backend/app/onchain/adapter.py`
- `code/backend/app/indexer/events.py`
- `code/backend/app/indexer/projections.py`

合约：

- `code/contracts/src/OrderBook.sol`
- `code/contracts/src/SettlementController.sol`
- `code/contracts/src/RevenueVault.sol`
- `code/contracts/src/MachineAssetNFT.sol`
- `code/contracts/src/PWRToken.sol`

## 3.2 建议新增

后端：

- `code/backend/app/runtime/cost_service.py`
- `code/backend/app/execution/agentskillos_wrapper.py`
- `code/backend/app/integrations/model_router.py`
- `code/backend/app/onchain/order_writer.py`
- `code/backend/app/api/routes/hsp_webhooks.py`
- `code/backend/app/artifacts/storage.py`
- `code/backend/app/artifacts/preview_builder.py`

合约：

- `code/contracts/src/OrderPaymentRouter.sol`
- `code/contracts/src/interfaces/IOrderPaymentRouter.sol`
- `code/contracts/src/interfaces/IPermit2.sol`

## 4. 实施优先级

## P0：必须先补，才能形成真实闭环

- [ ] `RuntimeCostService`
- [ ] `OrderPaymentRouter.sol`
- [ ] HSP 真回调与后端写链
- [ ] direct stablecoin pay 事件与 indexer 投影
- [ ] `PWR pay` 路径
- [ ] `order_writer.py`

## P1：补齐执行闭环

- [ ] `AgentSkillOSWrapper`
- [ ] `ModelRouter`
- [ ] artifact storage
- [ ] preview builder
- [ ] execution complete -> preview ready 自动写链

## P2：补齐资产与运营视图

- [ ] marketplace / listing 视图
- [ ] refund entitlement 查询
- [ ] platform claim 查询
- [ ] machine APR / profitability 面板

## 5. 推荐实施顺序

如果目标是最快做出一个可信的 hackathon demo，推荐顺序是：

1. 先补 `RuntimeCostService`
   - 因为报价、PWR quote、yield estimate 都要依赖它
2. 再补 `OrderPaymentRouter.sol`
   - 一次性打通 `USDC / USDT / PWR` 三个入口
3. 再补 `order_writer.py`
   - 让 HSP callback、confirm-result、preview-ready 都走统一写链器
4. 再补 direct pay indexer
   - 让链上支付能稳定投影回 backend
5. 再补 `AgentSkillOSWrapper + ModelRouter`
   - 把 AI 执行从 skeleton 升级成真实 orchestration kernel
6. 最后补 artifact / preview / ops 面板
   - 完成演示层与运营层

## 6. 验收标准

完成上述 P0 + P1 后，系统至少应该满足下面这些验收条件：

- 用户可以通过 `USDT/USDC via HSP` 下单并触发真实 paid 状态
- 用户可以直接用 `USDC` 或 `USDT` 直签支付订单
- 用户可以直接用 `PWR` 支付订单
- 支付成功后，机器 NFT 在 active task / unsettled revenue 情况下不可转移
- execution 完成后能自动生成 preview 并进入 confirm
- confirm 后链上 settlement 触发，机器侧 claimable revenue 更新
- indexer 能把 paid / preview ready / settled / claimed 状态稳定回写后端
- 后端能查询到统一的 order timeline、machine transfer block state、claimable balance
