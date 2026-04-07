# OutcomeX Phase 1 Payment + AI Integration Design

> 该设计文档作为本轮实现的正式 spec，基于已经确认的中文设计文档整理而成，供并行 agent 和集成主会话共同遵循。

## Scope

本轮实现只覆盖三条主线：

1. 合约支付路由
   - `USDC` direct pay via `EIP-3009`
   - `USDT` direct pay via `Permit2`
   - `PWR pay`
   - 统一订单支付事件
2. 后端支付控制面
   - 真实 `HSP` merchant adapter
   - webhook 验签 / 幂等 / 回调写链
   - `RuntimeCostService`
   - `order_writer.py`
3. AI 执行包装层
   - `AgentSkillOSWrapper`
   - `ModelRouter`
   - 与现有 `ExecutionService` 的兼容接入

## Non-Goals

本轮不做：

- machine marketplace 完整交易系统
- PWR 二级市场 / 做市
- 完整生产级 artifact 存储基础设施
- 完整前端钱包交互页

## Architectural Boundaries

### Contracts

- `OrderBook.sol` 继续维护订单状态机
- 新增 `OrderPaymentRouter.sol` 承接多支付入口
- `SettlementController.sol` 与 `RevenueVault.sol` 继续维护结算和收益

### Backend Control Plane

- API 路由只接收请求与返回读模型
- `RuntimeCostService` 提供统一价格与收益锚
- `order_writer.py` 是后端唯一业务写链入口
- HSP callback 成功后由后端推进链上状态

### Execution Layer

- `AgentSkillOS` 作为内部 orchestration engine
- OutcomeX wrapper 负责输入/输出映射
- `ModelRouter` 负责 provider / model family 选择

## Required Outcomes

完成后至少应满足：

- 三种支付路径进入统一 order paid 状态机
- HSP 回调可推进真实链上 paid 状态
- runtime cost 可输出 quote / PWR quote / platform fee / machine share
- execution 层可通过 wrapper 产生结构化执行计划与 provider 选择结果
- 现有测试不回归，并新增对应单元/集成测试

## Source Design Docs

- `docs/payment-contract-routing-decision-cn.md`
- `docs/payment-execution-contract-interaction-cn.md`
- `docs/implementation-checklist-cn.md`
- `docs/next-phase-implementation-design-cn.md`
