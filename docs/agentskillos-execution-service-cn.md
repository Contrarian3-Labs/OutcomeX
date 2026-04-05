# OutcomeX × AgentSkillOS 执行服务说明

本文档专门说明当前已经落地的执行服务边界：为什么 OutcomeX 要把 `AgentSkillOS` 当成执行内核，以及现在具体是怎么接的。

## 1. 核心原则

当前技术路线已经明确切换为：

- `AgentSkillOS` 负责真实执行
- OutcomeX 负责业务控制面

更具体地说：

### AgentSkillOS 负责
- skill discovery
- solution matching / orchestration
- 多步任务执行
- 内部模型调用
- 工具调用
- 产物生成

### OutcomeX 负责
- chat 与推荐方案
- 订单
- 支付
- `run_id` 提交与轮询
- preview / result 状态推进
- 结果确认
- 结算
- 机器资产状态与 transfer guard 基础状态

这意味着 OutcomeX 不再试图重写 AgentSkillOS 的内部执行逻辑，而是消费它产出的稳定 run 记录。

## 2. 当前新增了什么

### 2.1 新的执行服务封装
- 文件：`code/backend/app/integrations/agentskillos_execution_service.py`

它提供 3 个核心动作：

- `submit_task(...)`
- `get_run(...)`
- `cancel_run(...)`

其中：

- `submit_task(...)` 会创建 `run.json`
- 然后用子进程启动真实的 `AgentSkillOS workflow.service.run_task(...)`
- `get_run(...)` 负责读取最新 run 记录
- `cancel_run(...)` 负责发终止信号并把状态写成 `cancelled`

### 2.2 新的持久化模型
- 文件：`code/backend/app/domain/models.py`

新增：

- `ExecutionRun`

它用于把 AgentSkillOS 的执行 run 落入 OutcomeX 数据库。

### 2.3 新的 API
- `POST /api/v1/orders/{order_id}/start-execution`
- `GET /api/v1/execution-runs/{run_id}`
- `POST /api/v1/execution-runs/{run_id}/cancel`

## 3. 一次执行请求现在怎么走

### Step 1：用户完成支付
订单必须先 full paid。

### Step 2：OutcomeX 提交执行
后端调用：

- `ExecutionEngineService.dispatch(...)`

内部再调用：

- `AgentSkillOSExecutionService.submit_task(...)`

### Step 3：后端拿到 `run_id`
这一步只返回提交态，不会在创建时就强行轮询成终态。

当前语义是：

- `start-execution` 返回 `queued` / `planning` / `running` 这种提交后状态
- 真正的终态由后续轮询推进

### Step 4：AgentSkillOS 在后台执行
真实执行由 AgentSkillOS 子进程完成，OutcomeX 不直接介入每一步 tool 调用。

### Step 5：轮询 `execution-runs`
后端或前端轮询：

- `GET /api/v1/execution-runs/{run_id}`

这一步会：

- 读取 `run.json`
- 回写 `ExecutionRun`
- 同步 `Order` 的执行状态
- 同步 `Machine` 的 `has_active_tasks`

### Step 6：run 成功后进入确认
如果 run 成功：

- order -> `RESULT_PENDING_CONFIRMATION`
- preview -> `READY`
- machine active task -> 释放

然后用户再走确认结果与结算。

## 4. run 记录里有什么

当前 run 记录已经能稳定提供：

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
- `pid`

## 5. 这些字段对产品有什么用

### 5.1 对 Demo / 执行可视化
- 可以展示状态推进
- 可以展示用了哪些 skill
- 可以展示生成了哪些预览与最终产物

### 5.2 对结算逻辑
- 可以把“执行已经真实发生”与“用户是否确认结果”清晰拆开
- 有助于后续对接 valid preview rejected / rejection fee 等结算分支

### 5.3 对机器资产
- 可以把 active task 与 machine transfer guard 绑定
- 可以把模型消耗、skill 路径与 delivery evidence 绑定到机器侧收益语义

## 6. 当前已经解决的两个关键问题

### 6.1 `start-execution` 不再错误返回终态
之前如果在创建 run 后立刻二次 `get_run()`，测试 stub 会把它直接读成 `succeeded`。

现在已经改为：

- `start-execution` 落库的是提交态
- 终态只由 `GET /execution-runs/{run_id}` 推进

这更符合真实异步执行语义。

### 6.2 修掉了 run 状态被覆盖回 `queued` 的竞态
之前 `submit_task()` 在 launcher 已经更新 `run.json` 后，还会把初始 payload 再写一遍，导致：

- `running` / `succeeded`
  被覆盖回
- `queued`

现在已经改为：

- launcher 返回后重新读取最新 payload
- 只补写 `pid`

因此不会再覆盖已推进的状态。

## 7. 当前还没有做完的部分

### 7.1 benchmark 级稳定性不是 100%
虽然执行边界已经切到 AgentSkillOS，但并不是每个 skill 都已经 benchmark 级稳定。

### 7.2 richer cancellation 仍可继续补
当前 `cancel_run()` 是 MVP 语义：

- 发 `SIGTERM`
- 写 `cancelled`

后续可以继续补更强的子任务取消与回收语义。

### 7.3 成本归因仍可继续细化
当前 `summary_metrics` 已能记录 token 和 estimated cost，但还没有把：

- machine runtime cost
- provider cost
- platform margin

做成统一账本。

## 8. 一句话结论

当前 OutcomeX 已经完成从：

- “OutcomeX 直接自己调模型 / provider”

到：

- “OutcomeX 提交并管理 AgentSkillOS run”

的关键切换。

这让产品结构更清晰：

- AgentSkillOS 做 delivery
- OutcomeX 做 settlement-aware control plane
