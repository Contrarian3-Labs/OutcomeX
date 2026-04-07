# OutcomeX Backend Wallet-First 默认模式更新

更新时间：2026-04-07

## 本次调整

以下 **用户经济动作** 现在在 backend 中默认使用 `user_sign`，前端不再需要显式传 `?mode=user_sign` 才拿到钱包可执行的 action builder：

- `POST /api/v1/orders/{order_id}/confirm-result`
- `POST /api/v1/orders/{order_id}/reject-valid-preview`
- `POST /api/v1/orders/{order_id}/refund-failed-or-no-valid-preview`
- `POST /api/v1/settlement/orders/{order_id}/claim-refund`
- `POST /api/v1/revenue/machines/{machine_id}/claim`

这些接口现在的默认行为是：

1. backend 校验当前业务前置条件
2. backend 返回 `mode= user_sign` 的链上动作描述
3. 响应中包含：
   - `chain_id`
   - `contract_address`
   - `contract_name`
   - `method_name`
   - `submit_payload`
   - `calldata`
4. 前端钱包广播交易
5. 以后端 projection / indexer 状态作为业务成功依据

## 仍然保留的显式例外

以下动作仍然 **不会** 默认切到 `user_sign`：

- `POST /api/v1/settlement/platform/claim`

原因：

- 这是 treasury / admin 范畴动作
- 当前更适合保留 `server_broadcast` 作为默认值
- 如需钱包 builder，可继续显式传 `?mode=user_sign`

## 对前端的含义

前端现在可以采用更干净的默认路径：

- buyer actions 默认直接请求接口，不带 `mode`
- 如果要走 fallback/debug 路径，才显式带 `?mode=server_broadcast`

也就是说：

- `confirm / reject / refund / claim_refund / machine_claim`
  - 默认 = wallet-first
  - fallback = `server_broadcast`

## 对 backend 的边界含义

这次调整只解决了“默认入口”问题，还 **没有** 完成以下更深层的收敛：

- `server_broadcast` 路径仍会直接写部分本地状态
- 事件驱动 projection 还不是所有用户经济动作的唯一真相来源
- `platform claim` 仍默认保留 treasury 广播语义

所以当前状态应准确描述为：

> OutcomeX backend 已把核心用户经济动作的默认入口收敛为 wallet-first，  
> 但 `server_broadcast` fallback 和部分 route-side state mutation 仍然存在，尚未完全收敛到纯事件驱动终态。

## 验证范围

本次调整已通过以下后端测试：

- `tests/api/test_order_actions_api.py`
- `tests/api/test_claims_api.py`

验证重点：

- 默认不带 `mode` 时，返回 `user_sign` action builder
- 显式 `?mode=server_broadcast` 时，旧广播路径仍可用
- `platform claim` 的默认语义保持不变
