# OutcomeX Wallet-First 边界参考

更新时间：2026-04-07

关联文档：
- `docs/business-logic-target-decisions-2026-04-07-cn.md`
- `docs/api-contract-for-frontend-cn.md`
- `docs/target-user-flow-cn.md`

---

## 1. 默认原则

OutcomeX 的默认原则是：

- 默认 wallet-first
- 用户经济动作默认由用户钱包直接调用合约
- 后端默认只负责 projection、执行编排、读取接口

---

## 2. backend 发链例外

以下动作允许由 backend / platform 发链：

- `createOrder`
- `mint NFT`
- `HSP adapter markPaid`

除这三项外，其余用户经济动作一律 wallet-direct。

---

## 3. 动作矩阵

### backend-sent

- `createOrder`
  - 用户已选 plan 与支付方式
  - backend 先上链创建 order
- `mint NFT`
  - 一级市场新机器发售时，由平台 mint 到用户地址
- `HSP adapter markPaid`
  - 仅限 `USDC/USDT via HSP`
  - 必须在真实入金确认后发链

### wallet-direct

- `pay`（仅 `PWR`）
- `confirmResult`
- `rejectResult`
- `claimRefund`
- `claimMachineRevenue`
- `claimPlatformRevenue`
- `transfer NFT`
- 二级市场 `sell / buy NFT`

### backend execution only

- `self-use`
  - owner / controller 命中时
  - 不创建链上 order
  - 不走支付
  - 后端直接执行

---

## 4. 支付轨默认值

正式支付轨只有：

- `PWR direct`
- `USDC/USDT via HSP`

以下旧设计已废弃为正式主路径：

- `USDC direct`
- `USDT direct`
- `USDC/USDT direct-intent`

---

## 5. 前端默认接入方式

### `PWR`

- backend `createOrder`
- 前端钱包直调合约 `pay`
- 回看 order projection

### `USDC/USDT`

- backend `createOrder`
- 前端发起 HSP checkout
- 等待 adapter 上链 `markPaid`
- 回看 order projection

### 非支付动作

- 一律钱包直连合约
- 前端只把 tx 过程当过程态
- 最终成功以 projection 为准

---

## 6. 前端状态默认值

支付相关最小状态集：

- `pending_payment`
- `paid`

解释：

- 钱包已提交 / 已打包 / sync 中，都还是 `pending_payment`
- 只有 projection 确认 `paid` 后才是真正业务成功

---

## 7. authoritative gating 默认值

以下是否可执行，不能由前端自己推导决定：

- `Start Execution`
- claim
- transfer
- secondary sale related action

这些 authoritative gating 必须以后端 / 合约真值为准。

---

## 8. 需要避免的旧口径

不要再用以下说法作为当前正式边界：

- `USDC/USDT direct pay 仍是默认正式路径`
- `sync-onchain 成功就等于 payment success`
- backend 仍负责普通用户经济动作广播
