# OutcomeX Codebase

This monorepo contains the three systems that make OutcomeX work as a financial product:

- `backend` turns product intent into orders, payments, execution dispatch, and read models
- `contracts` hold the economic truth for payment, settlement, ownership, and claims
- `agentskillos` provides the execution kernel that actually delivers user outputs

## Monorepo layout

```text
code/
├── backend/        FastAPI control plane and indexer-backed product APIs
├── contracts/      Foundry contracts for order lifecycle, settlement, and machine assets
├── agentskillos/   Vendored agent runtime used as the execution kernel
├── infra/          Infra placeholders and support files
├── scripts/        Cross-repo utilities
└── shared-specs/   Shared notes and schema fragments
```

## System boundary

OutcomeX is intentionally split into three layers.

### `code/backend`

Owns the application control plane:

- chat plans and quote presentation
- order creation and payment intents
- HSP webhook and polling integration
- execution dispatch into AgentSkillOS
- onchain indexing and SQL projections
- buyer, treasury, and machine-owner read APIs

### `code/contracts`

Owns financial truth and asset semantics:

- machine NFT ownership
- marketplace listing and purchase flows
- paid-order lifecycle
- settlement splits, refunds, and claims
- machine-side revenue accrual
- transfer guards based on active tasks and unsettled revenue

### `code/agentskillos`

Owns execution behavior:

- skill retrieval and search
- orchestration strategy
- plan and DAG generation
- task execution
- artifact and preview production

OutcomeX deliberately keeps this runtime downstream from the financial system. AgentSkillOS does not own settlement, payment truth, or RWA logic.

## Dependency flow

```text
frontend -> backend -> contracts
                  \
                   -> AgentSkillOS
contracts -> backend projections -> frontend
```

Important consequences:

- contracts are the source of truth for economic state
- backend turns chain events into application-friendly read models
- backend uses a thin contract when calling AgentSkillOS
- AgentSkillOS is an execution dependency, not the business ledger

## Product flow in code terms

1. `chat/plans` and order APIs define the commercial shape of a job.
2. payment APIs create either direct-pay payloads or HSP checkout intents.
3. contracts anchor paid orders, confirmations, refunds, and claims.
4. backend projections update order, machine, revenue, and claim views.
5. execution APIs dispatch the paid task into AgentSkillOS and expose artifacts back to the product.

## DeFi and RWA relevance inside the code

The monorepo is interesting because the financial parts are not bolted on after the fact.

- `backend` and `contracts` together implement escrow, settlement, refunds, revenue attribution, and ownership constraints.
- `contracts` make machine assets transferable but operationally constrained by unresolved economic state.
- `backend` keeps a projection layer so the financial model can still drive a usable product UI.
- `agentskillos` gives the system real delivery capacity, which is what makes the machine asset economically meaningful.

## Where to start

- Start with `backend/README.md` if you want the product and API boundary.
- Start with `contracts/README.md` if you want the onchain model.
- Start with `agentskillos/README.md` if you want the execution-kernel integration.
- Read `../HACKATHON_REVIEW.md` if you want the most important architectural caveats before presenting the project externally.
