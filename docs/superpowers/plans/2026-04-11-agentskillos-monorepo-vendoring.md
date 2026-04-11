# AgentSkillOS Monorepo Vendoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor AgentSkillOS into `code/agentskillos`, point OutcomeX backend at the vendored copy by default, and verify a fresh OutcomeX execution still succeeds.

**Architecture:** Keep OutcomeX backend as a thin wrapper, but change its default AgentSkillOS repo-root resolution to monorepo-local `code/agentskillos`. Vendor only the source and runtime-critical assets from the current external checkout, excluding caches and generated outputs, so deployment becomes single-repo. Preserve existing execution outputs, logs, and artifact collection semantics.

**Tech Stack:** Git, rsync/cp, Python/FastAPI backend, AgentSkillOS Python project, pytest, existing local OutcomeX backend + Anvil services.

---

### Task 1: Vendor AgentSkillOS Into Monorepo

**Files:**
- Create: `code/agentskillos/**`
- Test: `code/agentskillos/tests/test_dev_browser_skill_seed.py`

- [ ] **Step 1: Copy the external checkout into the monorepo without git metadata or generated caches**

Run:

```bash
mkdir -p /mnt/c/Users/72988/Desktop/OutcomeX/code/agentskillos
rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.cache' \
  --exclude 'node_modules' \
  --exclude 'runs' \
  --exclude 'artifacts' \
  --exclude 'tests/Output' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  /mnt/c/Users/72988/Desktop/Hashkey/reference-code/AgentSkillOS/ \
  /mnt/c/Users/72988/Desktop/OutcomeX/code/agentskillos/
```

- [ ] **Step 2: Verify the vendored tree contains the runtime-critical files**

Run:

```bash
cd /mnt/c/Users/72988/Desktop/OutcomeX
python3 - <<'PY'
from pathlib import Path
root = Path('code/agentskillos')
required = [
    root / 'src',
    root / 'data/skill_seeds/dev-browser/package.json',
    root / '.gitattributes',
    root / '.gitignore',
    root / 'tests/test_dev_browser_skill_seed.py',
]
missing = [str(path) for path in required if not path.exists()]
assert not missing, missing
print('vendored tree OK')
PY
```

Expected: `vendored tree OK`

- [ ] **Step 3: Run the vendored dev-browser regression test**

Run:

```bash
cd /mnt/c/Users/72988/Desktop/OutcomeX/code/agentskillos
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp pytest -q tests/test_dev_browser_skill_seed.py
```

Expected: `2 passed`

### Task 2: Repoint Backend To Vendored AgentSkillOS By Default

**Files:**
- Modify: `code/backend/app/integrations/agentskillos_bridge.py`
- Modify: `code/backend/app/core/config.py` (only if path configuration needs a default override)
- Test: `code/backend/tests/execution/test_agentskillos_bridge.py` (or nearest existing bridge test file)

- [ ] **Step 1: Write a failing backend test for vendored path preference**

Add a test that creates a fake monorepo layout under a temp directory and asserts the bridge resolves `code/agentskillos` before external reference paths.

- [ ] **Step 2: Run the targeted test to verify it fails first**

Run:

```bash
cd /mnt/c/Users/72988/Desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp pytest -q tests/execution/test_agentskillos_bridge.py -k vendored
```

Expected: FAIL because current bridge still prefers the old external checkout path.

- [ ] **Step 3: Implement the minimal bridge resolution change**

Update the bridge so repo-root resolution prefers:

1. explicit env/config override
2. monorepo-local `code/agentskillos`
3. legacy external checkout fallback

- [ ] **Step 4: Re-run the targeted bridge test**

Run:

```bash
cd /mnt/c/Users/72988/Desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp pytest -q tests/execution/test_agentskillos_bridge.py -k vendored
```

Expected: PASS

### Task 3: Verify End-to-End OutcomeX Execution Against Vendored Copy

**Files:**
- Verify: `code/backend/data/agentskillos-execution/**`
- Verify: `/tmp/outcomex-vendored-agentskillos-smoke.json`

- [ ] **Step 1: Run the backend execution smoke that creates a fresh order and starts a real execution**

Run a fresh script against the local backend on `8787` using the existing HSP mock webhook pattern, and save the report to:

```text
/tmp/outcomex-vendored-agentskillos-smoke.json
```

- [ ] **Step 2: Verify the final run reaches a terminal success state**

Check that the final run payload includes:

```json
{
  "status": "succeeded"
}
```

and that `artifact_manifest` / `preview_manifest` are non-empty.

- [ ] **Step 3: Inspect the final logs and artifact paths**

Confirm the final run has:

- `events.ndjson`
- `logs_root_path`
- at least one generated artifact under the run workspace

### Task 4: Commit and Push Monorepo Changes

**Files:**
- Create/Modify: `code/agentskillos/**`
- Modify: `code/backend/**` (only changed bridge/test files)

- [ ] **Step 1: Review the final git diff is scoped to vendoring + backend pathing**

Run:

```bash
cd /mnt/c/Users/72988/Desktop/OutcomeX
git status --short
```

- [ ] **Step 2: Commit the monorepo vendoring work**

Run:

```bash
git add code/agentskillos code/backend docs/superpowers/specs/2026-04-11-agentskillos-monorepo-vendoring-design.md docs/superpowers/plans/2026-04-11-agentskillos-monorepo-vendoring.md
git commit -m "feat: vendor agentskillos into monorepo"
```

- [ ] **Step 3: Push to OutcomeX main**

Run:

```bash
git push origin main
```

Expected: push succeeds and remote main advances to the new commit.
