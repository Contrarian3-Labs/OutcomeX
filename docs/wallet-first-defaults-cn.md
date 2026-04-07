# OutcomeX Backend 非支付 Submit Route 已移除

更新时间：2026-04-07

## 本次调整

以下 **非支付类用户经济动作 submit route** 已从 backend 直接移除：

- `POST /api/v1/orders/{order_id}/confirm-result`
- `POST /api/v1/orders/{order_id}/reject-valid-preview`
- `POST /api/v1/orders/{order_id}/refund-failed-or-no-valid-preview`
- `POST /api/v1/settlement/orders/{order_id}/claim-refund`
- `POST /api/v1/revenue/machines/{machine_id}/claim`
- `POST /api/v1/machines/{machine_id}/transfer`

这些动作的推荐且唯一的新前端路径是：

1. 前端直接构造合约调用
2. 用户钱包直接广播交易
3. backend 只监听链上事件、更新 projection / DB、提供读取接口

## 仍然保留的 backend 写接口

以下动作仍然保留在 backend：

- `POST /api/v1/payments/orders/{order_id}/direct-intent`
- `POST /api/v1/payments/{payment_id}/sync-onchain`
- `POST /api/v1/payments/orders/{order_id}/intent`
- `POST /api/v1/payments/hsp/webhooks`
- `POST /api/v1/settlement/platform/claim`

原因：

- `USDC / USDT` 直付仍需要 backend 协助做 intent / finalize / verifier / projection
- `platform claim` 属于 treasury / admin 范畴

## 重要边界修正

推荐边界是：

- `USDC / USDT` direct pay：仍可走 backend-assisted `intent / finalize`
- `PWR` direct pay：应由前端直接调用合约
- `confirm / reject / refund / claim_refund / machine_claim / transfer`：应由前端直接调用合约
- backend 对这些非支付动作的职责应逐步收敛为：监听链上事件、更新 projection、提供读取接口

## 对前端的含义

前端现在可以采用更干净的默认路径：

- `USDC / USDT` 直付：继续走 backend `intent / finalize`
- `PWR` 与非支付类用户动作：优先直接调合约
- 不再依赖 backend 非支付 submit route，因为这些 route 已不存在

## 对 backend 的边界含义

- backend 不再为 `confirm / reject / refund / claim_refund / machine_claim / transfer` 提供提交入口
- 这些动作的业务状态应只来自链上事件 / projection
- backend 仍需继续收敛剩余 demo-only fallback，目标是把非支付状态同步完全收口到 event-driven read model

## 验证范围

本次调整重点验证：

- 创建订单 / 读订单 / 执行轮询路径不受影响
- direct payment 路径继续可用
- revenue overview / machine read 路径继续可用
- backend 代码中不再残留这些已移除 submit route 的注册与测试依赖
