# OutcomeX 当前统一后端状态说明

本文档描述 `feat/phase1-integration` 分支在 2026-04-04 的当前后端状态，重点更新两条主线：

- OutcomeX 不再继续扩张“自研 delivery wrapper”，而是把 `AgentSkillOS` 作为真实执行内核
- OutcomeX 自身保留业务控制面：订单、支付、执行 run、预览、确认、结算、资产与链上状态

## 1. 当前已经合并的三条主线

### 1.1 AgentSkillOS 执行主线
- `code/backend/app/integrations/agentskillos_execution_service.py` 已新增真实执行服务边界；
- 后端会提交任务给本地 `AgentSkillOS`，返回稳定的 `run_id`；
- `AgentSkillOS` 负责：
  - skill discovery
  - solution orchestration
  - 多步执行
  - 模型调用
  - 工具调用
  - 产物生成
- OutcomeX 只消费执行结果，不需要理解内部 orchestration 细节。

### 1.2 支付与结算控制面主线
- 后端已经具备 chat-plan 报价、payment intent、支付确认、结果确认、settlement start 的主控制流；
- `RuntimeCostService` 负责统一输出：
  - official quote
  - runtime cost
  - platform fee
  - machine share
  - `PWR` quote
- `OrderWriter` 继续作为统一链上写入边界，负责把业务状态映射成链上 payload。

### 1.3 机器资产与链上状态主线
- 机器侧 `has_active_tasks` / `has_unsettled_revenue` 已进入数据库模型；
- `ExecutionRun` 已落库，用于承接 AgentSkillOS run 状态与产物清单；
- 后端已能为：
  - transfer guard
  - claimable revenue
  - preview / confirmation / settlement
  提供统一状态基础。

## 2. 当前后端的核心模块

### 2.1 API 层
- `code/backend/app/api/routes/chat_plans.py`
  - 负责 chat-native 的推荐方案与报价输出；
- `code/backend/app/api/routes/orders.py`
  - 负责创建订单、写入 execution metadata、启动执行、确认结果；
- `code/backend/app/api/routes/execution_runs.py`
  - 负责查询 / 取消 AgentSkillOS 执行 run；
- `code/backend/app/api/routes/payments.py`
  - 负责创建 payment intent、mock confirm，以及支付成功后的 settlement policy 冻结；
- `code/backend/app/api/routes/hsp_webhooks.py`
  - 负责接收 HSP webhook、校验签名、处理幂等；
- `code/backend/app/api/routes/settlement.py`
  - 负责确认结果后启动结算。

### 2.2 执行层
- `code/backend/app/execution/agentskillos_wrapper.py`
  - 只负责把用户意图转换为 OutcomeX 需要的 planning metadata；
  - 不再承担真正的 delivery 执行；
- `code/backend/app/execution/service.py`
  - 负责计划、硬件 admission、向 AgentSkillOS 提交 run；
  - `dispatch()` 返回的是 `run_id` 和 `run_status`，不是 provider task；
- `code/backend/app/integrations/agentskillos_bridge.py`
  - 负责定位 AgentSkillOS repo、Python 环境、注入执行环境变量；
- `code/backend/app/integrations/agentskillos_execution_service.py`
  - 负责提交任务、维护 `run.json`、轮询 run 状态、收集 preview / artifacts / skills / model usage。

### 2.3 计价、支付与链上写入层
- `code/backend/app/runtime/cost_service.py`
  - 当前统一计价锚；
- `code/backend/app/integrations/hsp_adapter.py`
  - 当前是 merchant-order / webhook 边界的 deterministic 适配器；
- `code/backend/app/onchain/order_writer.py`
  - 当前输出 deterministic write payload / tx metadata；
- `code/backend/app/domain/models.py`
  - 已包含 `Order`、`Payment`、`SettlementRecord`、`RevenueEntry`、`ExecutionRun`。

## 3. 当前完整请求路径

### 3.1 Chat 规划与报价
1. 用户发起 chat 请求；
2. `chat_plans` 路由调用推荐逻辑和 `RuntimeCostService`；
3. 返回推荐方案、报价和预期交付结构；
4. 这一层仍坚持产品真相：用户看到的是 outcome，不是 workflow internals。

### 3.2 创建订单
1. 用户确认推荐方案；
2. `orders` 路由创建 `Order`；
3. 后端调用 `ExecutionEngineService.plan(...)` 写入 execution metadata；
4. 当前 execution metadata 主要保存：
   - planner
   - primary output
   - match status
   - selected provider / selected model（这是 planning 元数据，不代表实际执行中的全部模型）
5. 同时调用 `OrderWriter.create_order(order)` 进入统一链上写入边界。

### 3.3 创建支付意图与冻结结算策略
1. 用户对订单发起支付；
2. `payments` 路由创建 payment intent；
3. payment 成功后冻结：
   - `settlement_beneficiary_user_id`
   - `settlement_is_self_use`
   - `settlement_is_dividend_eligible`
4. 机器同时被标记为 `has_unsettled_revenue = True`。

### 3.4 启动执行
1. 用户完成支付后，后端调用 `POST /api/v1/orders/{order_id}/start-execution`；
2. `ExecutionEngineService.dispatch(...)` 向 `AgentSkillOSExecutionService.submit_task(...)` 提交任务；
3. 后端立即拿到：
   - `run_id`
   - `run_status`（通常是 `queued`）
4. 后端创建 `ExecutionRun` 记录，并把 `run_id / run_status` 写回 `order.execution_metadata`；
5. 机器被标记为 `has_active_tasks = True`。

### 3.5 查询执行结果
1. 前端或后端轮询 `GET /api/v1/execution-runs/{run_id}`；
2. 路由从执行服务读取最新 `run.json`；
3. 回写数据库中的 `ExecutionRun`；
4. 当 run 成功时：
   - `order.execution_state = SUCCEEDED`
   - `order.preview_state = READY`
   - `order.state = RESULT_PENDING_CONFIRMATION`
   - `machine.has_active_tasks = False`
5. 当 run 失败或取消时：
   - `order.execution_state = FAILED / CANCELLED`
   - `machine.has_active_tasks = False`

### 3.6 用户确认结果与结算
1. 用户确认结果；
2. `orders/{id}/confirm-result` 检查：
   - payment 是否足额
   - preview/result 是否 ready
   - settlement policy 是否已冻结
3. 满足条件后订单进入 `RESULT_CONFIRMED`；
4. `settlement` 路由再按 `10% / 90%` 启动结算；
5. 同时通过 `OrderWriter.confirm_result(...)` 与 `OrderWriter.settle_order(...)` 进入链上写入边界。

## 4. AgentSkillOS run 记录现在长什么样

`ExecutionRun` / `run.json` 当前已经稳定承载以下字段：

- `run_id`
- `external_order_id`
- `status`
- `workspace_path`
- `run_dir`
- `preview_manifest`
- `artifact_manifest`
- `skills_manifest`
- `model_usage_manifest`
- `summary_metrics`
- `error`
- `started_at`
- `finished_at`

这意味着 OutcomeX 现在已经可以拿到：

- 这次任务用了哪些 skill
- 这次任务内部调用了哪些模型
- token 消耗与估算成本
- 预览产物和最终产物位置
- 当前 run 的生命周期状态

## 5. 当前产品真相在后端中的体现

### 5.1 Users buy outcomes, not tools
- 用户看到的是 chat、推荐方案、价格、执行进度、结果确认；
- `skills_manifest`、`model_usage_manifest`、`run_dir` 这些信息主要属于后台控制面与调试面。

### 5.2 Machine side earns only after confirmed work
- 支付成功只会冻结结算策略，不会立刻产生可分配收益；
- 只有结果确认后，settlement 才会进入可启动状态。

### 5.3 Self-use 仍然主要是后端策略
- 当前 self-use / dividend eligibility 的冻结发生在支付成功时；
- 这部分本质上是业务规则，不要求用户直接和合约交互。

### 5.4 Transfer guard 的后端状态基础已经就位
- `has_active_tasks` 会在执行开始时打开，在成功 / 失败 / 取消时释放；
- `has_unsettled_revenue` 会在支付成功与结算期间保持；
- 这与链上 NFT transfer guard 语义是对齐的。

## 6. 当前实现里真实与未完成的边界

### 已经真实化的部分
- AgentSkillOS execution-service 边界；
- `run_id` / run record / artifact manifest / skill manifest / model usage manifest；
- 后端订单、支付、执行、确认、结算状态流；
- 合约生命周期测试；
- transfer guard 与收益状态机的基础语义。

### 仍未完全真实化的部分
- HSP 仍是 mock merchant adapter，不是正式 merchant API；
- `OrderWriter` 仍是 deterministic 写链边界，不是真实广播交易；
- 用户直签的 `USDC` / `USDT` / `PWR` 支付入口还没有和后端控制面正式打通；
- AgentSkillOS 内部并非每个 skill 都已经 benchmark 级稳定，但执行边界已经切换成可观测 run。

## 7. 当前分支的意义

当前 `feat/phase1-integration` 已经从“OutcomeX 自己拼 provider 调用”推进到：

- OutcomeX = 业务 / 支付 / 结算 / 机器资产控制面
- AgentSkillOS = discovery / orchestration / execution 内核

这一步很关键，因为它让接下来的工作更聚焦在：

- 接正式 HSP 或链上直付
- 接真实合约广播 / relayer / indexer
- 补 `PWR pay`
- 扩充 AgentSkillOS benchmark 覆盖与运行稳定性
