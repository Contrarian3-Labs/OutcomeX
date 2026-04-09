# OutcomeX 本地浏览器闭环与当前非 Fully-Live 清单

更新时间：2026-04-10

这份文档只回答两件事：

1. 现在本地到底能不能拉起一套可手测的闭环
2. 现在还有哪些点不能对外说成 fully-live

## 1. 当前已经真实验证过的闭环

基于本地 Anvil + 当前合约 + 当前 backend + 当前 frontend，已经 fresh 跑过：

- 合约测试：`30 passed, 0 failed`
- backend 全量测试：`226 passed, 1 warning`
- 前端全量检查：`tsc + build + vitest` 通过
- 主业务 smoke E2E：
  - HSP 路径下 claim 前 transfer 被阻止
  - claim 后 machine 可以 transfer
  - PWR 路径下 reject / refund / claim 可跑
  - platform `USDC` 与 `PWR` claim 可跑
- marketplace smoke E2E：
  - listing 生效
  - buyer 成为 canonical owner
  - owner projection 回写
  - listing 清除

因此，当前 repo 已经具备一个真实的本地链上演示闭环，不再只是接口 mock。

## 2. 当前仍然不应宣称为 fully-live 的点

下面这些点依然需要明确控制口径：

### 2.1 HSP 商户实环还没有在本地闭环配置完成

当前代码已经有：

- HSP adapter
- webhook 验签入口
- HSP -> paid -> chain projection 的主逻辑

但本地默认 `.env` 仍然没有填：

- `OUTCOMEX_HSP_APP_KEY`
- `OUTCOMEX_HSP_APP_SECRET`
- `OUTCOMEX_HSP_WEBHOOK_URL`
- `OUTCOMEX_HSP_PAY_TO_ADDRESS`

所以现在能说的是：

- `HSP code path exists`
- `HSP local smoke semantics are covered`

还不能说的是：

- `merchant QA / testnet webhook loop is configured and running in this local browser demo`

### 2.2 本地浏览器一键脚本默认走的是 PWR 本地链演示，不是 HSP 对外网实收款

新增的本地脚本会：

- 拉起 fresh Anvil
- 部署本地合约
- 给 `buyer-1` 预充 `PWR`
- 准备一台可交易的 machine
- 启动 backend + frontend

它的目标是让你本地直接手测：

- wallet connect
- PWR 支付
- order detail
- claim / transfer / marketplace

它不是 HSP 公网联调脚本。

### 2.3 AgentSkillOS 的 live AI 生成依赖本地 DashScope key

当前执行链路已经接入：

- OutcomeX backend -> AgentSkillOS thin boundary
- execution run observability / SSE / logs

但如果本地没有配置：

- `OUTCOMEX_DASHSCOPE_API_KEY`

那么你能看到控制面和执行状态，但不能保证真实在线模型持续产出结果。

所以当前对外更准确的说法是：

- `AgentSkillOS execution path is integrated`
- `live provider execution requires local DashScope credentials`

### 2.4 前端仍保留少量 env fallback，便于本地演示

当前前端已经是 wallet-first，合约动作默认走连接钱包。

但为了本地 demo 稳定性，仍保留：

- `VITE_OUTCOMEX_USER_ID`
- `VITE_OUTCOMEX_MACHINE_VIEWER_ID`
- `VITE_OUTCOMEX_WALLET_USER_MAP_JSON`

这不影响钱包主路径，但意味着：

- 某些页面在未连钱包时仍可用 fallback identity 渲染
- 本地 demo 更稳定
- 不是严格的“无 fallback 纯钱包-only”模式

## 3. 新增的一键本地浏览器脚本

新增脚本：

- `scripts/start_local_browser_demo.sh`
- `scripts/stop_local_browser_demo.sh`
- `code/backend/scripts/prepare_local_browser_demo.py`
- `code/backend/.env.local-demo.example`

### 3.1 启动前要求

你本机需要已有：

- `anvil`
- `forge`
- `python3`
- backend venv：`code/backend/.venv`
- frontend 依赖：`forge-yield-ai/node_modules`

### 3.2 backend 本地 env

如果你本地没有 `code/backend/.env`，先复制：

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
cp .env.local-demo.example .env
```

如果要跑真实 AgentSkillOS 在线模型，再补：

- `OUTCOMEX_DASHSCOPE_API_KEY`

### 3.3 一键启动

```bash
cd /mnt/c/users/72988/desktop/OutcomeX
./scripts/start_local_browser_demo.sh
```

脚本会自动完成：

- 启 fresh `Anvil`（`127.0.0.1:8545`）
- 重新部署本地合约
- 删除旧的本地 backend SQLite
- seed 一台 machine 到 `owner-1`
- 给 `buyer-1` 预充 `PWR`
- 启动 backend（`127.0.0.1:8000`）
- 启动 frontend（`127.0.0.1:8080`）

### 3.4 只准备链和 seed，不启动服务

```bash
cd /mnt/c/users/72988/desktop/OutcomeX
./scripts/start_local_browser_demo.sh --prepare-only
```

### 3.5 停止

```bash
cd /mnt/c/users/72988/desktop/OutcomeX
./scripts/stop_local_browser_demo.sh
```

## 4. 本地手测建议路径

### 4.1 钱包

建议在钱包里连接 Anvil `chainId=133`，并导入这些地址对应私钥：

- `buyer-1`: `0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC`
- `owner-1`: `0x70997970C51812dc3A010C7d01b50e0d17dc79C8`
- `treasury-1`: `0x90F79bf6EB2c4f870365E785982E1f101E93b906`

### 4.2 推荐手测顺序

1. 用 `buyer-1` 打开 frontend
2. 走 chat / plans / create order
3. 选择 `PWR` 支付
4. 查看 order detail / execution observability
5. 切到 `owner-1` 查看 machine / claim
6. 完成 claim 后测试 transfer / marketplace

## 5. 当前最适合对外的口径

最准确的表达是：

- `OutcomeX now has a real local-chain product loop for browser demo`
- `wallet-first onchain actions are real in local E2E`
- `HSP merchant production-style loop still needs real merchant config + public webhook`
- `live AgentSkillOS generation still depends on local model credentials`

不要说成：

- `everything is already fully-live`
- `HSP merchant loop is already configured in this local demo`
- `frontend has no local fallback semantics at all`
