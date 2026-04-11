# AgentSkillOS Monorepo Vendoring Design

**Goal**

将当前外部 `reference-code/AgentSkillOS` 收拢进 OutcomeX monorepo，落在 `code/agentskillos`，并让 OutcomeX backend 默认从这个目录解析和调用 AgentSkillOS，而不是依赖 monorepo 外部路径。

## Scope

本次设计只覆盖：

1. AgentSkillOS 代码与必要资源 vendoring 进入 monorepo
2. backend 的 AgentSkillOS repo root/path 解析改为优先使用 `code/agentskillos`
3. 保留现有执行链路能力：OutcomeX backend -> AgentSkillOS -> artifacts / preview / logs
4. 一并带上本轮已验证的 `dev-browser` seed 修复

本次不覆盖：

- 对 AgentSkillOS 做新的能力改造
- 重写 OutcomeX backend 的 orchestration 逻辑
- 清理 AgentSkillOS 全部历史脏改动，只做可运行的 vendor 收敛

## Recommended Layout

```text
OutcomeX/
  code/
    agentskillos/
      .gitignore
      config/
      data/
      docs/
      scripts/
      src/
      tests/
      pyproject.toml
      requirements.txt
      README.md
```

### Why this layout

- `code/backend`、`code/contracts`、`code/agentskillos` 同层，职责清晰
- backend 读取本仓路径更稳定，不再依赖 monorepo 外部绝对路径
- 后续部署时，服务端只需要拉一个 monorepo 即可包含执行内核

## Copy Policy

### Include

- AgentSkillOS 源码与运行必需文件：`src/`、`data/`、`config/`、`scripts/`、`tests/`
- 项目元信息：`README*`、`pyproject.toml`、`requirements.txt`、`.env.example`
- 本轮修复：
  - `data/skill_seeds/dev-browser/package.json`
  - `tests/test_dev_browser_skill_seed.py`
  - `dev-browser` seed 的 LF 约束

### Exclude

- `.git/`
- `.venv/`、`.cache/`、`node_modules/`
- `runs/`、`artifacts/`、`tests/Output/`
- `__pycache__/`、`.pytest_cache/`
- 其他本地生成物和临时文件

## Backend Integration

backend 需要做两件事：

1. 默认 repo root 指向 monorepo 内的 `code/agentskillos`
2. 仅在该目录不存在时，才 fallback 到外部 reference checkout（如果仍保留兼容）

### Expected behavior

- 本地开发：默认使用 `OutcomeX/code/agentskillos`
- 服务器部署：同样使用 monorepo 内目录，无需额外 checkout 外部仓库
- 现有运行目录、artifact、preview、logs 产出逻辑保持不变

## Git / Ignore Rules

- `code/agentskillos/.gitignore` 保留，确保其自身生成物不被误提交
- monorepo 根 `.gitignore` 如有必要补充一条 `code/agentskillos/node_modules/`
- 为避免 `dev-browser` 在 Windows checkout 回到 CRLF，需要在 vendor 目录内保留 `.gitattributes`

## Verification Plan

完成实现后至少验证：

1. `code/agentskillos` 目录存在且关键文件齐全
2. backend 能解析到 monorepo 内 AgentSkillOS 路径
3. 真实 OutcomeX order -> payment -> execution 能成功跑通至少 1 个 run
4. 产物 / preview / logs 可正常回写
5. `dev-browser` seed 回归测试通过

## Risks

1. AgentSkillOS 当前本身还有其他未收敛改动，vendor 时需要避免把明显无关的临时产物带进 monorepo
2. Windows `core.autocrlf=true` 可能影响 skill seed 运行脚本，所以 `.gitattributes` 需要一起带入
3. backend 代码若仍硬编码外部路径，会导致 vendor 后仍然引用旧 checkout，必须显式改掉

## Implementation Notes

推荐一次完成以下动作：

1. 将 AgentSkillOS 复制到 `code/agentskillos`
2. 清理不应进入 monorepo 的缓存 / 产物目录
3. 调整 backend 的路径解析逻辑
4. 运行最小验证：
   - `pytest tests/test_dev_browser_skill_seed.py`
   - fresh order -> execution smoke
5. 提交为 monorepo commit，并 push 到 `origin/main`
