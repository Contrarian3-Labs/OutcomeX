# OutcomeX 支付路由、合约直连边界与用户交互路径决策

本文档回答下面几个关键问题：

1. `HSP merchant-docs-all-in-one.pdf` 实际对应什么支付模式
2. HashKey 测试链是否适合让用户直接签名提交 `USDT/USDC`
3. 哪些交互应该是“用户直接和合约交互”
4. 哪些交互应该继续由“后端主导 + 写链”
5. 一条完整的正式用户交互路径应该怎么走
6. AI 执行层应如何与 `AgentSkillOS`、模型 router、runtime cost service 配合

这份文档会明确区分：

- 当前代码已经是什么
- 我建议锁定的正式路线是什么

## 1. 基于 HSP 文档的直接结论

我查看了本地 PDF：

- `/mnt/c/users/72988/desktop/Hashkey/merchant-docs-all-in-one.pdf`

从文档内容看，HashKey Merchant / HSP 的核心模式是：

- 商户后端先创建 merchant order
- HSP 返回 `payment_url` / `flow_id`
- 用户在 HSP checkout 完成钱包签名
- 网关广播交易并追踪链上确认
- 最终通过 webhook 回调商户后端

也就是说，HSP 更像：

- 一个 `merchant checkout gateway`
- 不是“你自己做前端让用户直接跟你业务合约交互”的替代品

从文档提炼出来的关键点是：

- 商户后端通过 REST API 创建支付订单
- 用户在 gateway checkout 中进行钱包签名
- gateway 负责提交交易和观察链上结果
- 商户后端通过 webhook 收到最终支付状态

所以，如果走 HSP 这条路，OutcomeX 的正确姿势不是：

- 用户直接给 OutcomeX 业务合约打稳定币

而是：

- OutcomeX 后端创建支付单
- 用户在 HSP checkout 完成支付签名
- HSP 回调 OutcomeX 后端
- OutcomeX 后端再调用自己的业务合约推进订单状态

## 2. HashKey 测试链对 `USDT/USDC` 的现实约束

我核对了官方 HashKey docs / testnet 资料，得到的结论是：

### 2.1 官方测试链资料明确给出的，是 `HSK` 测试币

官方资料里能明确看到的，是：

- HashKey Chain Testnet
- `ChainID = 133`
- 原生 gas token 是 `HSK`
- 官方 faucet 给的是 testnet `HSK`

但我没有在当前查到的官方测试链开发文档中，看到一组明确的、官方维护的 testnet `USDT` / `USDC` 合约地址。

这意味着：

- 不能把“HashKey 测试链上一定有官方 `USDT/USDC` 可直接拿来支付”当成既定前提

### 2.2 主网/生态侧存在 HashKey Chain 上的 `USDT`

我查到 HashKey Global 的公开公告里，确实有：

- HashKey Chain 上支持 `USDT` 充提

但这是：

- 主网/交易所接入层面的事实

不是：

- “官方文档已经为开发者提供稳定、标准、测试环境可直接调用的 testnet `USDT/USDC` 支付基线”

### 2.3 这对 OutcomeX 的设计意味着什么

对于开发和测试阶段，我建议不要把系统设计押注在：

- “HashKey testnet 官方一定有可直接用于支付的 `USDT/USDC`”

更稳妥的做法是：

#### 路线 A：测试链自部署 `MockUSDT / MockUSDC`

优点：

- 最可控
- 开发和测试环境稳定
- 可以完整模拟“用户钱包直接稳定币支付合约”的路径

#### 路线 B：HSP 路径用于真实商户支付接入，测试期只模拟 webhook

优点：

- 更接近真实商户流程
- 与 merchant 文档完全一致

我的建议是：

- 开发 / hackathon / testnet：自部署 `MockUSDT / MockUSDC`
- 生产支付接入：走真实 `HSP`

## 3. 哪些交互应该用户直连合约

你说得对：并不是所有链上动作都应该由后端代为完成。

OutcomeX 应该分成两类链上交互：

### 3.1 资产类交互：用户直接和合约交互

这些动作天然属于用户钱包资产动作，应该用户直接签交易：

#### 1. 购买机器 NFT

例如：

- `MachineAssetMarketplace.buy(...)`
- 或 `MachineAssetNFT.mint / purchase` 的 marketplace 入口

原因：

- 这是用户购买链上资产
- 资产归属必须由用户钱包直接确认

#### 2. 转移机器 NFT

例如：

- `safeTransferFrom`
- 或平台包装过的 `transferWithPolicy` 入口

原因：

- 这是资产所有权动作
- 不能让后端代签

#### 3. claim 机器侧收益 / PWR

例如：

- `RevenueVault.claim(machineId)`

原因：

- 这是用户领取自己链上资产
- 应由用户自己签名

#### 4. 用户直接用 `PWR` 支付订单

例如：

- `PWR.approve(...)`
- `OrderPaymentRouter.payWithPWR(orderId, amount)`

原因：

- 这是用户从自己钱包支付协议资产
- 最自然的模式就是用户直签

所以可以总结为：

- 资产购买
- 资产转移
- 收益 claim
- `PWR pay`

都应该优先设计成用户直接和合约交互。

### 3.2 产品流程类交互：后端主导，再写链

这些动作更适合由后端主导：

#### 1. chat 输入与推荐方案

- 明显是后端产品逻辑

#### 2. `USDT/HSP` 支付入口

- 本身就是 merchant backend + webhook 体系
- 不是纯 dApp 直连合约交互

#### 3. solution orchestration / 执行调度

- 后端和执行 worker 必须参与

#### 4. preview 生成与结果确认前校验

- 必须由后端掌握 artifact、preview、状态判断

#### 5. 后端根据结果确认去调用业务合约

例如：

- `markOrderPaid`
- `markPreviewReady`
- `confirmResult` 的业务写链入口

这些动作更像：

- 平台业务控制面的状态推进

不是纯钱包资产动作。

## 4. 所以最终应该是“混合交互模式”

OutcomeX 最合理的不是：

- 全部用户直接调合约

也不是：

- 全部都让后端代替用户做链上动作

而是：

### 模式 A：产品控制面交互

- 用户 -> 后端
- 后端 -> 合约
- 合约事件 -> indexer -> 后端

适用于：

- 订单
- `USDT/HSP` 支付
- 执行
- preview
- 结果确认
- 结算推进

### 模式 B：资产钱包交互

- 用户钱包 -> 合约
- 合约事件 -> indexer -> 后端

适用于：

- NFT 购买
- NFT 转移
- 收益 claim
- `PWR` 直接支付

这两种模式同时存在，才符合 OutcomeX 的产品本质：

- 它既是 AI delivery product
- 又是 machine-backed onchain asset system

## 5. 当前代码在这件事上处于什么阶段

当前仓库状态更接近：

### 已实现

- 后端主导的订单、mock payment、结果确认、结算逻辑
- 合约主导的 receipt、settlement、claim、transfer guard 逻辑
- indexer 主导的链上事件投影逻辑

### 未真正打通

- HSP 真实 webhook -> 业务合约
- 用户钱包直接购买 NFT 的 marketplace
- 用户钱包直接用 `PWR` 支付订单

所以当前还是：

- 架构骨架基本对
- 但正式支付与钱包交互入口还没补完

## 6. 我建议锁定的正式支付路线

## 6.1 `USDT/HSP` 路线

### 正式路线

1. 用户在 OutcomeX 前端点击支付
2. 后端调用 HSP merchant API 创建 order
3. 返回 `payment_url`
4. 用户跳到 HSP checkout
5. 用户在钱包签名
6. HSP 广播交易
7. HSP webhook 通知 OutcomeX 后端
8. OutcomeX 后端调用业务合约推进：
   - `markOrderPaid`
   - 冻结 settlement classification
9. 后续 preview / confirm / settlement 按业务流程继续

### 为什么这条路线是对的

因为它和 merchant 文档完全一致。

这不是“用户直接给你业务合约打款”的模型，而是：

- HSP 作为支付网关
- OutcomeX 作为 merchant backend

## 6.2 `PWR pay` 路线

### 正式路线

1. 用户创建订单
2. 后端给出该订单当前需要支付的 `PWR` 数量
3. 用户点击 `Use PWR`
4. 前端让用户直接签：
   - `approve`
   - `payWithPWR`
5. 合约记录该订单已使用 `PWR` 支付
6. indexer 回写后端订单状态
7. 后续 preview / confirm / settlement 继续走

### 为什么 `PWR pay` 适合用户直接直连合约

因为这是典型的：

- 用户钱包直接使用协议资产

而不是 merchant checkout。

## 6.3 稳定币直签路线要不要做

可以做，但我不建议它成为主路径。

也就是说，你可以额外支持：

- 用户钱包直接用 `MockUSDT / MockUSDC` 或正式 `USDT/USDC` 调用支付路由合约

但我建议：

- 测试期可做
- 生产期不要把它作为默认入口

原因：

- HSP 更适合商户支付体验
- merchant webhook 模式更适合 OutcomeX 这种产品
- 直签稳定币更像纯 DeFi/dApp，不是 chat-native outcome product 的最佳默认体验

## 7. runtime cost service 必须单独实现

这个结论不变，而且现在更明确。

它不应该只是一个 utility，而应该是一个独立服务模块。

建议新增：

- `code/backend/app/runtime/cost_service.py`

它应该统一输出：

- 订单官方报价
- 模型/技能/运行时成本
- 机器侧成本估算
- `PWR` 参考锚
- 机器收益与 APR 估算

它会被这些模块共同使用：

- plan recommendation
- order quote
- yield estimation
- `PWR` 锚定逻辑
- machine-side reserve accounting

所以它本质上是：

- OutcomeX 经济系统的定价锚服务

## 8. AgentSkillOS 应该怎么真正接进来

我还是维持同一个建议：

- 不要重写一套新的 orchestration system
- 直接把 `AgentSkillOS` 作为内部 orchestration engine
- 用 OutcomeX wrapper 包起来

## 8.1 正确接法

建议新增：

- `code/backend/app/execution/agentskillos_wrapper.py`

职责：

1. 接收 OutcomeX 的 `IntentRequest`
2. 调用 AgentSkillOS 的 retrieval / orchestration / execution 能力
3. 输出 OutcomeX 能识别的：
   - `ExecutionRecipe`
   - `ExecutionPlan`
   - step list
   - artifact refs
   - preview candidates

## 8.2 模型调用怎么接

不要让 AgentSkillOS 直接绑定它原来的模型 provider。

应该改成：

- AgentSkillOS -> OutcomeX ModelRouter -> 实际 provider

建议新增：

- `code/backend/app/integrations/model_router.py`

这样：

- `AgentSkillOS` 负责多步 orchestration
- OutcomeX 负责 provider policy、machine capability、runtime cost、preview、settlement

## 8.3 为什么不是“直接把 AgentSkillOS 服务原样接进来就完事”

因为 OutcomeX 还需要控制：

- 哪些模型可用
- 哪些 skill 可用
- 哪台机器能跑
- 价格怎么算
- preview 怎么裁剪
- 何时允许确认
- 何时允许结算

所以 AgentSkillOS 应该是：

- 内部 orchestration kernel

而不是：

- 整个产品控制面

## 9. 一条完整的正式用户交互路径

下面给一条推荐的完整路径。

## 9.1 路径一：默认 `USDT/HSP`

### Step 1：用户输入需求

用户：

- 在 chat 中输入目标

后端：

- 生成推荐方案
- 调 `RuntimeCostService`
- 调 `AgentSkillOSWrapper.plan(...)`

输出：

- 推荐方案
- 价格
- ETA
- 示例结果

### Step 2：用户创建订单

用户：

- 选方案，下单

后端：

- 创建 backend `Order`
- 调合约 `createOrder`
- 保存链上 order receipt id / tx hash

### Step 3：用户通过 HSP 支付

用户：

- 点击支付
- 进入 HSP checkout
- 在钱包里签名

HSP：

- 广播交易
- 追踪链上确认

后端：

- 通过 webhook 收到支付成功
- 冻结 settlement policy
- 调合约 `markOrderPaid`

### Step 4：执行 orchestration

后端：

- 调 `AgentSkillOSWrapper`
- 多步 orchestration
- 每一步底层模型都走 OutcomeX `ModelRouter`
- 机器资源调度由 runtime 控制

### Step 5：生成 preview

后端：

- 保存 artifact
- 生成 preview
- 更新 order ready 状态
- 调合约 `markPreviewReady`

### Step 6：用户确认结果

用户：

- 点击 confirm

后端：

- 校验 execution / preview / payment
- 更新 backend 状态
- 调合约 `confirmResult`

### Step 7：链上 settlement

合约：

- `SettlementController.settle`
- `RevenueVault.accrueRevenue`
- 如有需要 mint `PWR`

### Step 8：indexer 同步后端

indexer：

- 监听 settlement / revenue / claim 事件

后端：

- 展示收益、claimable balance、transfer blocked state

## 9.2 路径二：用户直接 `PWR pay`

### Step 1：用户创建订单

同上。

### Step 2：后端给出 `PWR quote`

后端：

- 基于 `RuntimeCostService`
- 输出本订单所需 `PWR`

### Step 3：用户钱包直签 `PWR pay`

用户：

- 点击 `Use PWR`
- 钱包签：
  - `approve`
  - `payWithPWR`

合约：

- 记录该订单 `PWR paid`

indexer：

- 同步支付状态到后端

### Step 4：后续执行 / preview / confirm / settlement

与 `USDT/HSP` 路径相同，只是资金路径变成 `PWR`。

## 9.3 路径三：用户购买或转移 NFT

### 购买

用户：

- 直接用钱包和 NFT marketplace / asset contract 交互

后端：

- 只展示 market 信息和 metadata

indexer：

- 回写 owner、listing、ownership 状态

### 转移

用户：

- 直接签 NFT transfer

合约：

- 通过 `transfer guard` 判断是否允许

indexer：

- 回写 ownership 变化

所以这条路径明显应该是：

- 用户直连合约

## 10. 最终建议

为了不再反复摇摆，我建议正式锁定下面这套：

### 10.1 支付

- 默认支付：`USDT/USDC via HSP`
- 第二支付：`PWR pay`
- 测试链开发时：自部署 `MockUSDT / MockUSDC`

### 10.2 产品控制面

- 用户主要与后端交互
- 后端主导订单、执行、preview、confirm、写链

### 10.3 资产交互

- NFT 购买：用户直连合约
- NFT 转移：用户直连合约
- 收益 claim：用户直连合约
- `PWR pay`：用户直连合约

### 10.4 AI 执行

- `AgentSkillOS` 作为内部 orchestration engine
- OutcomeX 通过 wrapper 使用
- 底层模型统一走 OutcomeX 的 model router

### 10.5 定价

- `runtime cost service` 做统一价格锚

### 10.6 状态同步

- 后端写链
- indexer 回写
- 合约是真正的资产与结算真相
- 后端是产品控制面与查询面

如果按这套走，OutcomeX 的结构会比较清楚：

- 对用户来说，仍然是 chat-native AI 结果交付产品
- 对机器资产持有人来说，NFT、收益、转移都是标准链上资产交互
- 对协议来说，支付、执行、结算、收益和 RWA 逻辑能统一到一套闭环里
