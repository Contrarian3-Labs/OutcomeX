# OutcomeX 下一阶段实现设计（支付路由、Runtime Cost、AgentSkillOS Wrapper）

本文档描述 OutcomeX 下一阶段最关键的四个工程模块：

- `OrderPaymentRouter.sol`
- `RuntimeCostService`
- `AgentSkillOSWrapper`
- `order_writer.py` / 后端统一写链路径

目标不是一次把所有功能做完，而是给后端、合约、索引器三边一套统一的实施设计，避免后面越做越散。

## 1. 设计目标

下一阶段要解决的不是“再加几个接口”，而是四个系统性问题：

1. 三种支付入口进入同一个订单状态机
   - `USDT/USDC via HSP`
   - `USDT/USDC direct contract pay`
   - `PWR pay`
2. 价格、运行成本、PWR quote、APR estimate 共享同一个成本锚
3. AI 执行不再停留在单步 skeleton，而是能接入真实 orchestration kernel
4. 后端所有业务写链动作都通过一个统一 writer 输出，避免每条路由各写各的

## 2. 总体架构

建议落地为四层：

### 2.1 Product API Layer

路径：

- `code/backend/app/api/routes/*.py`

职责：

- 接收前端请求
- 返回 plan / quote / payment params / order timeline
- 不直接拼接 provider 或合约细节

### 2.2 Domain + Control Plane Layer

路径：

- `code/backend/app/domain/*`
- `code/backend/app/runtime/cost_service.py`
- `code/backend/app/execution/agentskillos_wrapper.py`
- `code/backend/app/integrations/model_router.py`
- `code/backend/app/onchain/order_writer.py`

职责：

- 统一报价
- 执行编排
- provider 选择
- 业务写链
- settlement classification freeze

### 2.3 On-Chain Asset / Settlement Layer

路径：

- `code/contracts/src/OrderBook.sol`
- `code/contracts/src/OrderPaymentRouter.sol`
- `code/contracts/src/SettlementController.sol`
- `code/contracts/src/RevenueVault.sol`
- `code/contracts/src/PWRToken.sol`
- `code/contracts/src/MachineAssetNFT.sol`

职责：

- 订单收据
- 支付入账
- settlement split
- revenue accrual / claim
- transfer guard

### 2.4 Projection Layer

路径：

- `code/backend/app/indexer/events.py`
- `code/backend/app/indexer/projections.py`
- `code/backend/app/indexer/replay.py`

职责：

- 监听支付、preview、settlement、claim、transfer
- 把链上事实投影成后端查询模型

## 3. `OrderPaymentRouter.sol` 设计

## 3.1 目标

用一个独立支付路由合约承接三条支付路径，让 `OrderBook` 只关心订单状态，不直接承接多种 token 授权细节。

## 3.2 建议新增文件

- `code/contracts/src/OrderPaymentRouter.sol`
- `code/contracts/src/interfaces/IOrderPaymentRouter.sol`
- `code/contracts/src/interfaces/IPermit2.sol`

如果需要链上稳定币测试常量，可补：

- `code/contracts/src/types/PaymentTypes.sol`

## 3.3 核心依赖

- `OrderBook.sol`
- `PWRToken.sol`
- `SettlementController.sol`
- HashKey testnet `USDC`
- HashKey testnet `USDT`
- Uniswap `Permit2` 接口

## 3.4 支持的支付入口

### A. `payWithUSDCByAuthorization(...)`

用途：

- 让用户用 `USDC` 的 `EIP-3009` 直接完成支付授权

关键语义：

- 校验 `orderId`
- 校验 `amount`
- 校验 `authorization` 未过期
- 拉取 `USDC` 到 payment router 或 treasury vault
- 调 `OrderBook.markOrderPaid(...)`
- 发出 `OrderPaymentReceived` 事件

### B. `payWithUSDT(...)`

用途：

- 让用户通过 `Permit2` 授权 `USDT` 支付

关键语义：

- 先消费 permit
- 再从用户地址转入 `USDT`
- 调 `OrderBook.markOrderPaid(...)`
- 发出 `OrderPaymentReceived` 事件

### C. `payWithPWR(...)`

用途：

- 让用户直接用钱包里的 `PWR` 支付订单

关键语义：

- 校验 order 当前可接受 `PWR`
- 校验 `PWR` amount
- 记录 payment source = `PWR`
- 调 `OrderBook.markOrderPaid(...)`
- 发出 `OrderPaymentReceived` 事件

## 3.5 关键事件

建议新增：

- `event OrderPaymentReceived(uint256 indexed orderId, address indexed payer, address token, uint256 amount, bytes32 paymentSource);`
- `event OrderPaymentVoided(uint256 indexed orderId, address indexed payer, address token, uint256 amount, bytes32 reason);`

这样 indexer 就不需要从多个 token transfer event 里倒推业务语义。

## 3.6 关键安全约束

- 一个订单支付完成后不能重复 paid
- 支付 token 必须白名单化
- `USDC` 和 `USDT` 的授权路径分开实现，不共用一套模糊入口
- `PWR pay` 不应绕过 order quote / payment amount 校验
- 退款 entitlement 仍由 `SettlementController` 管，不在 payment router 里做业务退款

## 4. `RuntimeCostService` 设计

## 4.1 目标

把“价格”从 UI 文案升级成系统级成本锚。

它必须同时服务：

- 推荐方案报价
- 订单官方报价
- `PWR` quote
- 机器 runtime cost
- machine-side margin
- yield estimation / APR

## 4.2 建议新增文件

- `code/backend/app/runtime/cost_service.py`
- `code/backend/app/schemas/quote.py`
- `code/backend/app/domain/costing.py`

如需配置层，可补：

- `code/backend/app/core/cost_config.py`

## 4.3 输入

- machine profile
- model family
- estimated tokens / frames / seconds
- estimated runtime duration
- skill/tool usage
- electricity cost
- hosting cost
- ops cost
- depreciation cost
- target margin
- risk buffer
- payment source

## 4.4 输出

至少统一产出以下对象：

- `runtime_cost_usd`
- `official_quote_usd`
- `pwr_quote`
- `platform_fee_usd`
- `machine_share_usd`
- `machine_margin_usd`
- `estimated_apr`

## 4.5 建议公式

可以先用工程可落地的近似公式：

- `runtime_cost_usd = model_cost + skill_cost + hardware_cost + ops_cost + risk_buffer`
- `official_quote_usd = runtime_cost_usd + target_margin`
- `machine_share_usd = official_quote_usd * 0.9`
- `platform_fee_usd = official_quote_usd * 0.1`
- `pwr_quote = machine_share_usd / pwr_anchor_price`

其中：

- `hardware_cost = electricity + hosting + depreciation`
- `pwr_anchor_price` 由 runtime cost basket 推导，不由硬编码常量决定

## 4.6 为什么它必须是服务，不是 utility

因为它会被以下模块共同调用：

- chat plan quote
- order create
- HSP payment intent
- direct pay quote
- PWR quote
- yield estimation
- machine listing / profitability panel

如果把它分散进多个 route 或 service，后面会出现：

- 同一订单不同页面报价不一致
- PWR quote 和 yield estimate 使用不同成本口径
- settlement 与 profitability 展示无法对齐

## 5. `AgentSkillOSWrapper` 设计

## 5.1 目标

OutcomeX 不自己重写一套多步 orchestration 内核，而是把 `AgentSkillOS` 作为内部执行引擎，通过 wrapper 接进来。

## 5.2 建议新增文件

- `code/backend/app/execution/agentskillos_wrapper.py`
- `code/backend/app/integrations/model_router.py`
- `code/backend/app/integrations/skill_registry.py`

如需结果映射对象，可补：

- `code/backend/app/execution/solution_memory.py`

## 5.3 输入输出边界

### 输入

- `IntentRequest`
- machine capability
- quote constraints
- allowed model families
- allowed skill set
- preview requirement

### 输出

- `ExecutionPlan`
- `ExecutionRecipe`
- step list
- candidate artifacts
- preview candidates
- execution metadata

## 5.4 Wrapper 的职责

- 把 OutcomeX 的 `IntentRequest` 转成 `AgentSkillOS` 可接受的任务输入
- 调用 retrieval / solution reference / orchestration
- 把执行计划转回 OutcomeX 的 `ExecutionPlan`
- 不直接绑定底层 provider，而是把模型请求转发到 `ModelRouter`

## 5.5 `ModelRouter` 的职责

- 根据 `model family / machine capability / cost / policy` 选择 provider
- 统一对接 Alibaba API、MuleRouter、本地模型、后续私有模型网关
- 返回标准化的 provider response
- 回传 cost / latency / failure metadata 给 RuntimeCostService 和 solution memory

## 5.6 为什么不能直接原样暴露 AgentSkillOS

因为 OutcomeX 还需要自己控制：

- 哪些机器可执行
- 哪些模型可用
- 哪些 skill 可用
- preview 如何裁剪
- 什么时候允许 confirm
- 什么时候触发 settlement

因此：

- AgentSkillOS 负责“怎么做”
- OutcomeX 负责“能不能做、多少钱、交付后怎么结算”

## 6. `order_writer.py` 设计

## 6.1 目标

所有后端业务写链动作都走一个 writer，避免：

- route 里直接拼 web3 调用
- callback 写链和手动 confirm 写链逻辑重复
- tx hash / nonce / retries 分散在不同文件

## 6.2 建议新增文件

- `code/backend/app/onchain/order_writer.py`
- `code/backend/app/onchain/contracts_registry.py`
- `code/backend/app/onchain/tx_manager.py`

## 6.3 writer 应封装的动作

- `create_order(...)`
- `mark_order_paid(...)`
- `mark_preview_ready(...)`
- `confirm_result(...)`
- `settle_order(...)`

如果后面补 NFT 市场，也可继续加：

- `list_machine(...)`
- `cancel_listing(...)`

## 6.4 输入

- business order id
- on-chain order id
- machine id
- settlement freeze fields
- preview validity
- settlement path
- chain config

## 6.5 输出

- tx hash
- submitted at
- chain id
- contract name
- method name
- idempotency key

## 6.6 调用方

- `payments.py`
- `hsp_webhooks.py`
- `orders.py`
- `settlement.py`

## 7. 状态同步设计

## 7.1 后端主导写链的路径

适用于：

- `USDT/USDC via HSP`
- preview ready
- confirm result
- settlement

路径：

1. 后端更新本地业务状态
2. 调 `order_writer.py` 提交链上交易
3. indexer 监听链上事件
4. projection 回写 backend read model
5. 前端读取统一 timeline

## 7.2 用户钱包直签的路径

适用于：

- `USDT/USDC direct pay`
- `PWR pay`
- NFT buy / transfer
- claim

路径：

1. 后端返回签名参数或 quote
2. 用户钱包签交易
3. 合约发事件
4. indexer 投影回 backend
5. 后端读模型更新 UI

## 8. 交付顺序建议

建议按下面顺序落地：

### Phase 1：先把支付闭环打通

- `OrderPaymentRouter.sol`
- direct pay indexer
- `hsp_adapter.py` 真实化
- `hsp_webhooks.py`
- `order_writer.py`

### Phase 2：再把价格和执行闭环打通

- `RuntimeCostService`
- `ModelRouter`
- `AgentSkillOSWrapper`
- artifact / preview builder

### Phase 3：最后补资产运营视图

- listing / marketplace read model
- yield estimate panel
- platform claim / refund query

## 9. 最小可演示完成定义

这一版做到下面这些，就足以支撑一套可信的 hackathon demo：

- chat 能给出官方报价与 plan
- 用户可以三选一支付：HSP、stablecoin direct pay、PWR pay
- 支付成功后订单进入 paid，机器转移受 guard 约束
- execution 完成后自动生成 preview
- 用户 confirm 后链上 settlement 触发
- machine-side claimable revenue 更新
- indexer 能把支付、settlement、claim、transfer block 一致投影回后端

做到这里，OutcomeX 的主叙事就从“概念 deck”变成“有真实系统边界的产品原型”。
