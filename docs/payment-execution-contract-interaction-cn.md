# OutcomeX 支付、执行、合约交互与技术路线说明

本文档专门回答以下问题：

- 现在支付是怎么支付的，是否直接付给合约
- 现在是怎么分润的
- 现在是否会因为订单存在而阻止 NFT 转移
- `PWR` 直接支付订单应该如何实现
- `runtime cost` 是否需要独立服务，以及应该怎么做
- AI 执行层是否可以直接沿用 `AgentSkillOS` 做 `solution orchestration` 和多步任务
- 当前技术路线到底是“后端同步到合约”，还是“用户直接调用合约，后端再同步状态”
- 如果采用当前推荐路线，一条完整的用户交互路径应该是什么

这份文档会明确区分：

- 当前仓库里已经实现的东西
- 当前还没有实现、但应该怎么做
- 我建议采用的正式技术路线

## 1. 先回答结论

先给结论，避免后面信息过载。

### 1.1 现在支付不是直接付给合约

当前代码里：

- 支付是在后端侧通过 mock HSP adapter 创建支付意图
- 支付成功也是后端 mock 确认
- 钱没有真正打进链上合约
- 合约里的 `SettlementController` 和 `RevenueVault` 现在是“结算语义与资产状态机”，不是实际收款入口

所以当前实现是：

- 后端先管理订单与支付状态
- 合约先管理链上 receipt / settlement / transfer guard 语义
- 两边还没有打成同一个真实资金通路

### 1.2 现在分润也不是“真实稳定币已打到合约后自动分润”

当前已经实现的是：

- 后端侧：
  - `SettlementRecord`
  - `RevenueEntry`
  - `10% / 90%` 规则
- 合约侧：
  - `SettlementController` 按规则拆分
  - `RevenueVault` 记录机器侧收益
  - `PWRToken` 由 `RevenueVault` mint

但当前还不是：

- 用户付款直接进链上合约
- 合约直接从真实 stablecoin 余额中结算

所以当前分润更准确的说法是：

- 规则已经实现
- 状态机已经实现
- 真实支付资产入金路径还没有接进去

### 1.3 现在“有 order 会不会阻止 NFT 转移”要分当前状态看

当前合约逻辑里，机器 NFT 转移会被两类条件阻止：

- 有 active task
- 有 unsettled revenue

具体逻辑在：

- `code/contracts/src/OrderBook.sol`
- `code/contracts/src/MachineAssetNFT.sol`
- `code/contracts/src/RevenueVault.sol`

更具体地说：

- `createOrder` 本身不会立刻阻止转移
- `markOrderPaid` 后会增加 `activeTaskCountByMachine`
- 订单结算后，如果机器侧还有未 claim 收益，`RevenueVault.hasUnsettledRevenue(machineId)` 会继续阻止转移

所以正确表述是：

- 不是“只要有 order 就阻止转移”
- 而是“进入支付/执行/未结收益状态后阻止转移”

### 1.4 `PWR` 直接支付订单目前还没实现，但必须进入下一阶段

当前代码里：

- `PWRToken` 存在
- 机器侧收益的 `PWR` mint / claim 逻辑存在
- 但“用户直接拿 PWR 支付订单”的支付入口还没有实现

这部分应该补，而且我建议作为独立支付路径补入，而不是和 `USDT/HSP` 混在一条接口里糊过去。

### 1.5 `runtime cost` 应该做成独立服务

这个必须做。

因为它不仅是展示价格，而是系统里很多核心逻辑的统一锚点：

- 官方报价
- `PWR` 参考结算锚
- 机器侧成本估算
- 方案推荐中的价格预估
- 机器收益 / APR 估算
- 风控与最低接单阈值

所以它不应该只是一段函数，而应该是一个明确的后端服务模块。

### 1.6 AI 执行层应该尽量直接复用 `AgentSkillOS`

结论是：

- 可以，而且我认为应该这么做
- 但不是把 `AgentSkillOS` 整个前端和原生交互直接塞进 OutcomeX
- 而是把它包装成 `wrapper service` / orchestration worker

推荐方式是：

- OutcomeX 保留自己的产品 API、订单状态机、资产逻辑
- `AgentSkillOS` 作为内部的 `solution orchestration engine`
- 通过 wrapper 调它的检索、编排、执行能力
- 再把模型调用改到我们的 model router / provider router 上

### 1.7 当前最合理的技术路线仍然是“后端主导，再写链”

我不建议把当前版本做成：

- 用户每一步都自己先调合约
- 然后后端再被动同步

我更建议：

- 用户主要和后端交互
- 后端负责：
  - chat
  - 推荐方案
  - 支付创建
  - 执行调度
  - 结果确认
  - 结算分类
  - 写链
- 合约负责：
  - receipt
  - settlement
  - revenue accrual
  - transfer guard
  - claim
- indexer 把链上状态同步回后端

也就是：

- 用户 -> 后端
- 后端 -> 合约
- 合约事件 -> indexer -> 后端

而不是：

- 用户 -> 合约
- 合约 -> 后端被动适配

这条路线更符合 OutcomeX 当前的产品形态，因为它本质上不是一个“纯链上手工交互产品”，而是一个 chat-native AI delivery product。

---

## 2. 当前实现到底是什么样

## 2.1 当前支付路径

当前支付路径在后端里是：

- `code/backend/app/api/routes/payments.py`

现在的流程是：

1. 后端创建订单
2. 后端创建 mock HSP payment intent
3. 后端通过 mock-confirm 把 payment 状态改成 `SUCCEEDED` 或 `FAILED`
4. 如果累计支付金额足够：
   - 冻结 `settlement_beneficiary_user_id`
   - 冻结 `settlement_is_self_use`
   - 冻结 `settlement_is_dividend_eligible`
   - 把机器标记为 `has_unsettled_revenue = True`

也就是说当前的“付款成功”主要是：

- 冻结 settlement policy
- 锁定后续结算语义
- 触发 transfer block

不是：

- 实际稳定币已经进合约

## 2.2 当前后端结算路径

后端结算路径在：

- `code/backend/app/api/routes/settlement.py`
- `code/backend/app/api/routes/revenue.py`

后端目前会做：

1. 检查订单是否已经：
   - full paid
   - result confirmed
   - settlement policy frozen
2. 创建 `SettlementRecord`
3. 计算：
   - `platform_fee_cents`
   - `machine_share_cents`
4. 分发成 `RevenueEntry`

这个是产品后端侧的结算记录，不是链上真实资金收支。

## 2.3 当前合约结算路径

合约结算路径在：

- `code/contracts/src/OrderBook.sol`
- `code/contracts/src/SettlementController.sol`
- `code/contracts/src/RevenueVault.sol`

链上语义目前已经有：

1. `OrderBook.createOrder`
2. `OrderBook.markOrderPaid`
3. `OrderBook.markPreviewReady`
4. 用户：
   - `confirmResult`
   - 或 `rejectValidPreview`
   - 或 `refundFailedOrNoValidPreview`
5. `SettlementController.settle(...)`
6. `RevenueVault.accrueRevenue(...)`
7. `RevenueVault.claim(...)`

所以当前链上这套更像：

- 正式结算语义的最小资产状态机

而当前后端更像：

- 产品控制面和 off-chain receipt / ledger

## 2.4 当前 Transfer Guard 逻辑

当前合约里机器 NFT 转移限制已经实现。

在以下情况下不可转移：

### 条件 A：有 active tasks

在 `OrderBook.markOrderPaid(...)` 后：

- `activeTaskCountByMachine[machineId] += 1`

在订单最终 settle 后：

- `activeTaskCountByMachine[machineId] -= 1`

所以支付后到最终结算前，这部分会阻止转移。

### 条件 B：有 unsettled revenue

在 `RevenueVault.accrueRevenue(...)` 后：

- `unsettledRevenueByMachine[machineId] += amount`

在 `claim(...)` 后：

- `unsettledRevenueByMachine[machineId] -= amount`

因此：

- 有未 claim 的机器侧 dividend-eligible 收益，也会阻止机器 NFT 转移

---

## 3. 当前实现还缺什么

## 3.1 真实支付入金路径还没接到合约

当前没有真实：

- `USDT` 付款到链上结算入口
- `HSP callback -> onchain settlement receipt`

所以现在仍然是：

- 后端状态先走通
- 合约语义单独存在

这意味着当前仓库适合：

- demo
- hackathon
- MVP 骨架

但还不是完整 production payment path。

## 3.2 用户直接用 `PWR` 支付订单还没做

当前缺少：

- `PWR pay intent`
- `PWR balance check`
- `PWR consume / transfer / burn / settlement routing`
- `USDT path` 与 `PWR path` 的统一订单状态机

## 3.3 `runtime cost` 只是概念锚，还不是独立服务

目前代码里已经有：

- `hardware simulator`
- execution/runtime structures

但还没有一个正式的：

- `runtime cost service`

来给出：

- 机器运行成本
- 模型成本
- skill 成本
- orchestration 成本
- 风险缓冲
- 官方报价
- PWR 参考锚

## 3.4 `AgentSkillOS` 还没有真正被当作 orchestration engine 接进来

当前代码主要是“借鉴结构”：

- `intent -> recipe`
- `recipe -> match`
- `execution service`
- `runtime simulator`

但还没有做到：

- 真正把 `AgentSkillOS` 当内核执行多步 solution orchestration

这部分正是下一步应该升级的地方。

---

## 4. 我建议的正式技术路线

## 4.1 支付路线：双支付入口

我建议 OutcomeX 保留两条正式支付路径：

### 路径 A：`USDT via HSP`

用途：

- 默认官方支付入口
- 面向所有订单

特点：

- 用户感知最简单
- 适合作为主交易路径
- 便于后续接真实 checkout

### 路径 B：`existing PWR pay`

用途：

- 用户直接使用钱包里的 `PWR` 支付订单

特点：

- 不引入新的 stablecoin 入金
- 只在协议内发生结算和再分配
- 是对 `PWR` 使用价值的关键支撑

所以最终应该是：

- `USDT/HSP` 是默认主入口
- `PWR pay` 是第二支付入口
- 两条路径进入同一个订单状态机

## 4.2 真实支付不建议“用户直接给业务合约打钱”

我不建议把体验做成：

- 用户自己直接往 `SettlementController` 打 stablecoin

更好的路线是：

### 对 `USDT/HSP`

- 用户在前端点击支付
- 后端创建 payment intent
- 用户在支付 rail 完成支付
- 后端收到回调
- 后端调用合约的 `markOrderPaid / createReceipt / freezeClassification`

即：

- 用户不直接操作业务合约
- 后端是产品控制面
- 合约是资产与结算状态机

### 对 `PWR pay`

这里分两种做法：

#### 做法 1：用户钱包直接签链上支付

- 用户点击“Use PWR”
- 前端让用户签一个链上交易
- 调 `PWR.approve(...)` + `OrderPaymentRouter.payWithPWR(orderId, amount)`
- 合约记录订单已支付
- indexer / backend 更新状态

#### 做法 2：permit 风格

- 用户签名
- 后端代提交链上交易

如果从产品体验来说，我更推荐：

- `USDT/HSP`：后端主导
- `PWR pay`：用户钱包直接签链上交易

这是最合理的组合。

## 4.3 分润路线：链上为准，后端做镜像与展示

最终建议如下：

### 对 `USDT/HSP` 路径

1. 用户通过 HSP 支付 `USDT`
2. 回调后，后端确认支付完成
3. 后端调用链上订单支付确认接口
4. 用户确认结果后，后端再调用合约 settlement
5. 合约按规则记录：
   - buyer refund entitlement
   - platform accrued USDT
   - machine-side revenue accrual
   - `PWR` mint / claimable balance
6. indexer 同步回后端
7. 后端只做展示、报表、查询，不作为最终收益真相

### 对 `PWR pay` 路径

1. 用户直接支付 `PWR`
2. 合约记录支付成功
3. 确认结果后，合约直接完成 `PWR` 再分配
4. 后端通过 indexer 展示结果

因此目标状态应该是：

- 分润真相在链上
- 后端只是 query + workflow coordinator

## 4.4 `runtime cost` 应独立成服务

这个服务建议命名为：

- `RuntimeCostService`

建议放在：

- `code/backend/app/runtime/cost_service.py`

建议职责：

### 输入

- machine profile
- GPU/CPU/memory usage estimate
- model family
- skill/tool usage
- orchestration depth
- duration estimate
- hosting/electricity/ops/depreciation config
- risk buffer config

### 输出

- `runtime_cost_quote`
- `official_price_quote`
- `pwr_anchor_quote`
- `machine_margin_estimate`
- `platform_fee_estimate`

### 它服务哪些模块

- `orders` 报价
- `plans` 推荐卡片价格展示
- `PWR` 参考锚
- `yield estimation`
- `machine APR / profitability`
- `risk guardrail`

换句话说：

- runtime cost 不应该只是 execution 里的辅助函数
- 它应该是整个经济系统的统一价格锚服务

## 4.5 AgentSkillOS 的正确接法：wrapper，不是硬抄一套新系统

你的直觉是对的。

我的建议是：

- 不要自己再重写一套完整 orchestration system
- 直接把 `AgentSkillOS` 当成 OutcomeX 内部的 orchestration engine
- OutcomeX 通过 wrapper service 使用它

推荐接法如下：

### OutcomeX 保留的部分

- chat UI / product API
- order lifecycle
- payment lifecycle
- result confirmation
- settlement lifecycle
- machine asset logic
- transfer guard logic
- indexer / onchain projection

### AgentSkillOS 接管的部分

- skill retrieval
- solution retrieval
- multi-step orchestration
- task decomposition
- execution DAG / pipeline generation
- runtime execution planning

### 中间加一层 wrapper

建议新增：

- `code/backend/app/execution/agentskillos_wrapper.py`

这个 wrapper 的职责是：

1. 把 OutcomeX 的 `IntentRequest` 转成 AgentSkillOS 的任务输入
2. 调用 AgentSkillOS 的 orchestration service
3. 把 AgentSkillOS 的输出转成：
   - `ExecutionRecipe`
   - `ExecutionPlan`
   - `PreviewPolicyInput`
   - `ExecutionArtifacts`
4. 把底层模型调用改到 OutcomeX 的 provider/model router 上

所以不是：

- OutcomeX 直接嵌一个 AgentSkillOS UI

而是：

- OutcomeX 把 AgentSkillOS 变成内部执行引擎

## 4.6 是否可以直接用 AgentSkillOS 的服务来做多步任务

答案是：可以，而且我建议这样做。

但要加几个边界：

### 可以直接复用的

- retrieval
- orchestration
- recipe / DAG generation
- multi-step skill execution

### 必须由 OutcomeX 自己决定的

- 哪些模型可用
- 哪些 skills 可用
- 哪台机器可执行
- runtime cost 怎么算
- preview 怎么生成
- 什么时候允许确认
- 什么时候可以 settlement

也就是说：

- AgentSkillOS 负责“怎么做”
- OutcomeX 负责“能不能做、花多少钱、交付之后怎么结算”

## 4.7 模型 router 怎么接

推荐方式：

- 不让 AgentSkillOS 直接调用它原始默认模型源
- 改成通过 OutcomeX 的 `ProviderRouter / ModelRouter`

可新增模块：

- `code/backend/app/integrations/model_router.py`

它负责：

- 根据 `model family / cost / policy / machine capability` 选择实际 provider endpoint
- 把 AgentSkillOS 的模型请求转成 OutcomeX 支持的 provider 请求
- 统一接：
  - Alibaba
  - MuleRouter
  - 后续本地模型
  - 自有模型网关

这样后面不论是：

- `AgentSkillOS`
- 直接执行
- 手工 recipe

都走同一个模型路由层。

---

## 5. 推荐的一条完整用户交互路径

下面给一条“正式形态”的完整路径。

## 5.1 路线选择

我建议采用：

- 主路径：用户和后端交互
- 后端写链
- indexer 同步链上状态
- 只有 `PWR pay` 这类钱包支付行为由用户直接签交易

## 5.2 `USDT/HSP` 路径完整交互

### Step 1：用户输入需求

用户动作：

- 在 chat 中输入结果目标

后端动作：

- `POST /chat`
- 调 `PlanService`
- 调 `RuntimeCostService`
- 调 `AgentSkillOSWrapper.plan(...)`

返回给前端：

- recommended plan
- example output
- ETA
- official quote

### Step 2：用户选择方案并创建订单

用户动作：

- 选择 plan
- 点击下单

后端动作：

- 创建 `Order`
- 生成内部 `order_id`
- 同时调用合约 `OrderBook.createOrder(...)`
- 保存 `onchain_order_id / tx_hash / receipt anchor`

状态变化：

- backend: `plan_recommended`
- contract: `Created`

### Step 3：用户支付 `USDT`

用户动作：

- 点击支付
- 跳到 HSP checkout

后端动作：

- 创建 payment intent
- 记录 `Payment(PENDING)`

### Step 4：HSP 回调成功

后端动作：

- 校验 HSP callback
- 更新 `Payment(SUCCEEDED)`
- 若累计付款足额：
  - 冻结 settlement beneficiary
  - 冻结 self-use / dividend eligibility
  - 调合约 `markOrderPaid(...)`

状态变化：

- backend: payment confirmed, machine transfer blocked
- contract: `Paid`, active task count +1

### Step 5：AgentSkillOS 做 solution orchestration

后端动作：

- 调 `AgentSkillOSWrapper`
- 生成多步 orchestration plan
- 通过 OutcomeX `ModelRouter` 和 `RuntimeScheduler` 执行
- 机器容量、成本、技能调用都由 OutcomeX runtime 侧控制

### Step 6：执行完成并生成 preview

后端动作：

- 保存 artifact
- 生成 preview
- 更新 backend：
  - `execution_state = succeeded`
  - `preview_state = ready`
  - `order.state = result_pending_confirmation`
- 调合约：
  - `markPreviewReady(validPreview)`

状态变化：

- backend: ready for confirm
- contract: `PreviewReady`

### Step 7：用户确认结果

用户动作：

- 点击 confirm

后端动作：

- 校验 order 已 full paid、preview ready、execution done
- 更新 backend `result_confirmed`
- 调合约 `confirmResult(...)`

状态变化：

- backend: settlement ready
- contract: `Confirmed`

### Step 8：链上 settlement

合约动作：

- `SettlementController.settle(...)`
- 记录：
  - 平台应得
  - buyer refund entitlement（若有）
  - machine-side accrual
- `RevenueVault.accrueRevenue(...)`
- 若 dividend-eligible，则 mint `PWR`

### Step 9：indexer 回写状态

indexer 动作：

- 监听：
  - `OrderSettled`
  - `Settled`
  - `RevenueAccrued`
  - `RevenueClaimed`
  - `Transfer` 等
- 更新 projection

后端动作：

- 展示订单完成、收益、claimable balance、transfer blocked state

### Step 10：机器持有人 claim

用户动作：

- 机器资产持有人点击 claim

合约动作：

- `RevenueVault.claim(machineId)`

状态变化：

- `unsettledRevenueByMachine` 减少
- 如果没有其他未 claim 收益，则机器可再次转移

## 5.3 `PWR pay` 路径完整交互

### Step 1：用户创建订单

同上。

### Step 2：系统报价

后端给出：

- `USDT official quote`
- 对应订单当前所需 `PWR quote`

### Step 3：用户选择 `Use PWR`

用户动作：

- 钱包签交易

前端 / 合约动作：

- `PWR.approve(...)`
- `OrderPaymentRouter.payWithPWR(orderId, amount)`

后端动作：

- indexer 监听支付事件
- 更新订单支付状态
- 冻结 settlement classification

### Step 4 ~ Step 10

之后执行、preview、confirm、settlement、claim 流程与上面类似，只是资金路径是 `PWR` 而不是 `USDT/HSP`。

---

## 6. 我建议接下来补的代码模块

为了把上面的目标真正落地，建议按下面顺序补：

### 6.1 支付与写链

- `code/backend/app/integrations/hsp_adapter.py`
  - 从 mock 升级成真实 HSP adapter
- `code/backend/app/onchain/order_writer.py`
  - 后端统一写链入口
- `code/contracts/src/OrderPaymentRouter.sol`
  - 新增 `PWR pay` 路由合约

### 6.2 runtime cost 服务

- `code/backend/app/runtime/cost_service.py`

### 6.3 AgentSkillOS wrapper

- `code/backend/app/execution/agentskillos_wrapper.py`

### 6.4 模型路由

- `code/backend/app/integrations/model_router.py`

### 6.5 artifact 与 preview unlock

- `code/backend/app/artifacts/*`

---

## 7. 最终推荐决策

为了避免未来继续来回改，我建议把正式方向锁成下面这套：

### 支付

- 默认支付：`USDT via HSP`
- 第二支付：`existing PWR`

### 控制面

- 用户主要与后端交互
- 后端负责订单、支付、执行、确认、写链

### 合约

- 合约负责 receipt、settlement、claim、transfer guard

### AI 执行

- AgentSkillOS 做内部 orchestration engine
- OutcomeX 用 wrapper 包起来
- 底层模型统一走 OutcomeX 的 model/provider router

### 定价

- `runtime cost service` 做统一官方价格锚与 `PWR anchor` 服务

### 状态同步

- 后端写链
- indexer 回写
- 后端作为产品读模型
- 合约作为资产与结算真相

如果按这套做，OutcomeX 的系统会清晰很多：

- 用户体验仍然是 chat-native 的产品
- 执行层可以真正多步编排
- 结算层可以真正上链
- RWA / yield / transfer guard 逻辑和支付语义也能统一起来
