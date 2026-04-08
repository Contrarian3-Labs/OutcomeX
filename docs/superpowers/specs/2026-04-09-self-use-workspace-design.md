# Self-use Workspace Design (2026-04-09)

## Goal

Add a dedicated `self-use` product path that lets a machine owner run AI delivery on their own hosted machine without creating an order, paying, writing onchain, or entering settlement / revenue flows.

## Product Boundaries

- Entry lives on `NodeDetail` via `Run on My Machine`
- Entry opens a dedicated `Self-use Workspace` route, not a buyer-order screen
- `self-use` is only available to the current machine owner / controller
- `self-use` must not show payment, settlement, revenue, refund, or claim semantics
- `self-use` uses real AgentSkillOS planning and execution
- `self-use` stores backend execution records only

## UX Structure

### 1. Entry

- `NodeDetail` shows `Run on My Machine` only for owner / controller
- Clicking the CTA navigates to `/nodes/:nodeId/self-use`

### 2. Self-use Workspace

The page has three sections:

- `Machine Context`: machine identity, runtime state, capability summary
- `Intent + Plans`: user inputs prompt/files, requests plans, selects one plan and an execution strategy (`quality` / `efficiency` / `simplicity`)
- `Execution Run`: after start, display run state, preview, artifacts, skill path, and model usage

### 3. Semantic Guardrails

The page must never show:

- order/payment/refund wording
- settlement / claim / revenue wording
- wallet transaction prompts for execution start

## Backend Design

### 1. New API surface

Add a dedicated backend route module for self-use, with minimal endpoints:

- `POST /api/v1/self-use/plans`
  - Validates viewer is machine owner / controller
  - Reuses the real planning path
  - Returns multiple plans plus selected/default strategy metadata
- `POST /api/v1/self-use/runs`
  - Validates viewer is machine owner / controller
  - Reuses the real execution submission path
  - Creates a backend execution run only
  - Must not create order/payment/settlement state
- `GET /api/v1/self-use/runs/{run_id}`
  - Returns run status, preview, artifacts, skills, model usage
  - Should make `run_kind=self_use` explicit in the response if practical

### 2. Ownership check

Owner gating should rely on the same viewer identity / machine owner projection already used by the machine pages. Non-owners get `403`.

### 3. Execution semantics

Self-use run creation must:

- persist machine id
- persist viewer user id
- persist selected plan and execution strategy
- submit to AgentSkillOS through the existing execution service thin boundary

Self-use must not:

- create `Order`
- create `Payment`
- write onchain
- enter settlement / revenue distribution

## Frontend Design

### 1. Route

Add a dedicated route/page for `/nodes/:nodeId/self-use`.

### 2. Page flow

- load machine context
- enter prompt / files
- request plans from `POST /api/v1/self-use/plans`
- choose plan + strategy
- start run with `POST /api/v1/self-use/runs`
- poll / fetch `GET /api/v1/self-use/runs/{run_id}`
- display preview and artifacts

### 3. NodeDetail integration

Replace the current informational preflight with navigation into the new workspace.

## Testing Strategy

### Backend

- owner can request self-use plans
- non-owner is rejected
- owner can create self-use run
- self-use run does not create order/payment/settlement data
- self-use run read endpoint returns execution state/artifacts

### Frontend

- owner sees `Run on My Machine`
- CTA navigates to self-use workspace
- workspace requests plans
- user can select plan/strategy and start run
- workspace renders run output
- page copy does not include buyer-order / payment / settlement semantics

### Verification

- focused backend pytest for new self-use routes/services
- focused frontend vitest for `NodeDetail` + `SelfUseWorkspace`
- frontend build

## Scope Exclusions

This slice does not implement:

- self-use quota accounting
- self-use billing or pseudo-payment
- new onchain contract behavior
- marketplace/listing/sell changes

## Success Criteria

- Owners have a real dedicated self-use workspace
- Planning and execution are real AgentSkillOS-backed flows
- No order/payment/settlement side effects are introduced
- Frontend language and backend behavior stay semantically aligned
