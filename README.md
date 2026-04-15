# OutcomeX

OutcomeX is a confirmed AI delivery network built on HashKey Chain.

Users buy outcomes, not prompts or workflow graphs. Hosted machines do the work through a vendored AgentSkillOS execution kernel. Payments, confirmation, settlement, and machine-side revenue are anchored into an onchain financial loop.

## Why this project fits the hackathon

OutcomeX is a DeFi submission first, not a generic AI app:

- it turns AI delivery into a priced financial flow
- it supports wallet-native settlement plus an HSP stablecoin rail
- it keeps payment truth, ownership, claims, and transfer constraints onchain
- it makes machine assets productive by linking confirmed work to claimable revenue

That makes the project a strong fit for HashKey Chain's DeFi track and a credible bridge into productive digital RWA.

## Product thesis

The core thesis is simple:

1. Users want a deliverable, not tool orchestration.
2. OutcomeX prices the deliverable and anchors the commercial flow.
3. AgentSkillOS executes the work.
4. Buyer confirmation finalizes settlement.
5. Machine-side beneficiaries earn from confirmed delivery.

This closes a loop that most agent demos never reach: demand, payment, execution, acceptance, settlement, and yield all live in one system.

## Why this is DeFi

OutcomeX uses onchain financial infrastructure as the product backbone:

- payment routing for `PWR` and stablecoins
- escrowed settlement and deterministic revenue splits
- buyer refunds and treasury claims
- machine-side claimable revenue
- transfer guards based on active work and unsettled value
- event-driven state projection into application UX

The result is not just "AI + wallet". It is a delivery market with onchain payment truth and programmable settlement.

## Why this is RWA-relevant

OutcomeX does not claim to tokenize legal title to offchain real estate or equities. Its stronger and more honest RWA angle is productive machine-backed assets:

- each hosted machine is represented as an onchain asset
- confirmed delivery work creates attributable economic output
- machine-side revenue becomes claimable value
- unsettled revenue affects transferability, so ownership and yield are linked

In other words, the machine NFT is not a decorative shell. It is a productive asset tied to verified delivery cash flow.

## How the product works

1. A user submits an outcome request.
2. OutcomeX returns plan options, pricing, and payment rails.
3. The buyer pays through `PWR` direct or `USDC/USDT via HSP`.
4. Once payment is anchored, OutcomeX dispatches execution to AgentSkillOS.
5. The buyer reviews the output and confirms or rejects the result.
6. Settlement logic updates refunds, platform revenue, and machine-side revenue.
7. Machine owners can claim revenue, and transfer guards prevent unsafe asset transfers while value is still unsettled.

## Architecture

```text
                        +----------------------+
                        |      Frontend        |
                        | plans, order, claim  |
                        +----------+-----------+
                                   |
                                   v
+--------------------+   +----------------------+   +----------------------+
|  code/agentskillos |<--|   code/backend       |-->|   code/contracts     |
| execution kernel   |   | control plane        |   | payment + settlement |
| skill retrieval    |   | APIs, HSP, indexer   |   | ownership + claims   |
| orchestration      |   | dispatch, projection |   | transfer guards      |
+--------------------+   +----------------------+   +----------------------+
                                   |
                                   v
                        +----------------------+
                        |   SQL read models    |
                        | orders, payments,    |
                        | revenue, machines    |
                        +----------------------+
```

## AgentSkillOS relationship

OutcomeX vendors `code/agentskillos` and uses it as the execution kernel, but it does not delegate financial control to that runtime.

AgentSkillOS is responsible for:

- skill retrieval
- orchestration strategy
- task execution
- artifact generation

OutcomeX adds the financial and product layer around it:

- plans, quotes, and order control
- payment rail handling
- HSP integration
- onchain settlement and claims
- machine asset ownership and transfer policy
- event indexing and application-facing projection

So the repo references AgentSkillOS architecture, but it materially changes the surrounding system boundary to make AI delivery financeable.

## Repository guide

- `code/backend` - FastAPI control plane, HSP integration, order/payment APIs, execution dispatch, indexer-backed projections
- `code/contracts` - Foundry contracts for machine assets, marketplace, order lifecycle, payment routing, settlement, and claims
- `code/agentskillos` - vendored execution runtime used by OutcomeX for planning and delivery
- `code/README.md` - engineering map of the monorepo

## Current implementation posture

What is already strong in the repo:

- real payment and settlement contracts
- machine-side claim logic and transfer guards
- HSP webhook and polling integration
- execution dispatch into a separate runtime boundary
- contract, API, and smoke coverage for the main business loop

What should be stated carefully:

- the repo has both target-state docs and compatibility paths; not every legacy path is the intended main UX
- HSP is integrated, but fully live merchant rollout still depends on environment and webhook setup
- the backend already contains a deeper AgentSkillOS planning bridge, but the fast product-facing plan path is still the default API path today

## Start reading here

- Product and control-plane overview: [code/backend/README.md](code/backend/README.md)
- Protocol and settlement model: [code/contracts/README.md](code/contracts/README.md)
- Execution-kernel integration: [code/agentskillos/README.md](code/agentskillos/README.md)
- Monorepo engineering guide: [code/README.md](code/README.md)

## Markdown index

Current top-level markdown navigation for the documentation set created in this repo:

- [README.md](README.md) - product overview, DeFi/RWA positioning, and architecture
- [code/README.md](code/README.md) - monorepo structure and subsystem boundaries
- [code/backend/README.md](code/backend/README.md) - backend control-plane responsibilities and runtime model
- [code/contracts/README.md](code/contracts/README.md) - onchain protocol, settlement, and machine-asset logic
- [code/agentskillos/README.md](code/agentskillos/README.md) - vendored AgentSkillOS role and OutcomeX integration boundary
