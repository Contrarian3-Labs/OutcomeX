# AgentSkillOS in OutcomeX

`code/agentskillos` is the vendored execution kernel used by OutcomeX.

AgentSkillOS is the subsystem that discovers skills, chooses execution structure, runs the task, and produces artifacts. OutcomeX vendors it into the monorepo so planning and execution stay local to the product instead of depending on an external checkout.

## What AgentSkillOS is responsible for

- skill retrieval and search
- orchestration strategy and DAG planning
- runtime execution
- logs, previews, and artifact generation
- web and batch execution modes inside its own project boundary

## What OutcomeX adds around it

OutcomeX does not use AgentSkillOS as the whole product. It wraps the runtime with a much stricter financial boundary.

OutcomeX owns:

- user-facing plans, orders, and payment flows
- HSP integration
- onchain settlement, refunds, and claims
- machine asset ownership and transfer rules
- SQL projections and product APIs

AgentSkillOS remains the execution kernel, not the ledger.

## OutcomeX-specific integration stance

This vendored copy exists because OutcomeX needs a stable local runtime boundary.

The backend integrates with AgentSkillOS through subprocess-based bridges so it can:

- call planning and skill discovery without importing the whole runtime into the backend process
- submit execution tasks and capture logs, artifacts, and previews
- keep financial logic outside the execution runtime

That is why the OutcomeX repo references AgentSkillOS architecture but still materially changes the surrounding system behavior.

## Architecture notes

The upstream project structure is still visible here:

- `src/manager` - skill discovery and indexing
- `src/orchestrator` - execution engines and DAG orchestration
- `src/runtime` - execution runtime helpers
- `src/web` - web UI for standalone AgentSkillOS usage
- `src/workflow` - batch and workflow services

For a deeper technical map, read `ARCHITECTURE.md`.

## Important practical caveat

OutcomeX currently uses AgentSkillOS more deeply for execution than for the public chat-plan API. The repo already contains bridge code for native planning, but the product-facing fast plan path is still the default route used by the backend today.

That means the right README language is:

- AgentSkillOS is the execution kernel already used by OutcomeX
- OutcomeX references AgentSkillOS planning semantics and architecture
- deeper convergence of public plan generation into native AgentSkillOS planning is already scaffolded in the repo

## Local standalone usage

If you want to work with AgentSkillOS directly:

```bash
cd code/agentskillos
pip install -e .
python run.py --port 8765
```

Configuration is driven by `config/config.yaml` plus environment variables in `.env`.

## OutcomeX integration references

- `../backend/README.md`
- `../../docs/superpowers/specs/2026-04-11-agentskillos-monorepo-vendoring-design.md`
- `ARCHITECTURE.md`
