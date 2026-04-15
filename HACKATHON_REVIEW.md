# OutcomeX Monorepo Review for Hackathon

## Executive assessment

OutcomeX is already more than an AI demo with wallet dressing. The repo contains a credible financial product shape: user intent becomes a priced order, payment is anchored through onchain or HSP-backed flows, delivery is executed by a separate agent runtime, and confirmation drives settlement plus machine-side revenue attribution.

The strongest submission posture is: OutcomeX is a confirmed AI delivery network built on HashKey Chain, with machine-backed productive assets, event-driven settlement, and a thin execution boundary into AgentSkillOS. That story is real in the codebase, but it needs precise wording. A few product claims that appear in older docs are ahead of the current default implementation and should not be overstated in the new READMEs.

## Findings

### High

1. `/chat/plans` should not be described as fully native AgentSkillOS planning today.
   - The public API currently imports and uses `build_fast_recommended_plans`, not the deeper bridge-backed `build_recommended_plans`: `code/backend/app/api/routes/chat_plans.py:7`, `code/backend/app/api/routes/chat_plans.py:30`, `code/backend/app/domain/planning.py:245`.
   - A real AgentSkillOS planning bridge does exist and is already wired in the domain layer: `code/backend/app/domain/planning.py:203`, `code/backend/app/domain/planning.py:207`, `code/backend/app/integrations/agentskillos_bridge.py:1`.
   - Submission implication: describe plan cards as product-facing execution strategies that reference AgentSkillOS semantics and architecture, while being honest that the fast path remains the default API path today.

2. Stablecoin UX should be documented as `USDC/USDT via HSP`, while direct stablecoin code paths should be treated as compatibility or test coverage rather than the official main path.
   - The product-truth docs explicitly say formal rails are `PWR` direct and `USDC/USDT` only via HSP: `docs/business-logic-target-decisions-2026-04-07-cn.md:56`, `docs/target-user-flow-cn.md:61`.
   - The backend still exposes direct payment intent support for `USDC`, `USDT`, and `PWR`: `code/backend/app/api/routes/payments.py:690`, `code/backend/app/api/routes/payments.py:712`.
   - The contracts also still implement direct stablecoin payment routes: `code/contracts/src/OrderPaymentRouter.sol:68`, `code/contracts/src/OrderPaymentRouter.sol:88`, `code/contracts/src/OrderPaymentRouter.sol:117`, `code/contracts/src/OrderPaymentRouter.sol:138`.
   - Submission implication: keep the README story aligned with the intended product rail, not every compatibility path still present in code.

### Medium

3. Wallet-first is directionally true, but the default settlement action path is still not purely user-sign-first.
   - Confirm, reject, and failed-preview refund endpoints still default to `server_broadcast`: `code/backend/app/api/routes/orders.py:711`, `code/backend/app/api/routes/orders.py:763`, `code/backend/app/api/routes/orders.py:815`.
   - On those broadcast paths, the backend immediately projects terminal order truth after the broadcast response: `code/backend/app/api/routes/orders.py:736`, `code/backend/app/api/routes/orders.py:788`, `code/backend/app/api/routes/orders.py:841`.
   - Submission implication: say the system is moving toward wallet-first and event-driven truth, but avoid claiming that every user action is already only finalized by post-indexed chain events.

4. HSP is a real payment rail in the repo, but fully live readiness still depends on merchant configuration and environment setup.
   - The backend includes webhook ingestion, polling, tx-hash checking, and receipt verification: `code/backend/app/api/routes/payments.py:101`, `code/backend/app/api/routes/payments.py:135`, `code/backend/app/api/routes/payments.py:179`.
   - The adapter explicitly distinguishes mockable configuration from fully live configuration: `code/backend/app/integrations/hsp_adapter.py:154`, `code/backend/app/integrations/hsp_adapter.py:180`, `code/backend/app/integrations/hsp_adapter.py:184`.
   - Internal validation docs also note that real merchant rollout still depends on env completion and webhook deployment: `docs/business-logic-implementation-gap-checklist-2026-04-07-cn.md:301`, `docs/business-logic-implementation-gap-checklist-2026-04-07-cn.md:314`.
   - Submission implication: present HSP as integrated and demoable, but not as already production-configured in every environment.

5. The RWA thesis is strong, but it should be framed as machine-backed productive digital assets and revenue rights, not as a legal claim about offchain title.
   - The code clearly supports machine ownership, transfer guards, and revenue accrual: `code/contracts/src/MachineAssetNFT.sol`, `code/contracts/src/OrderBook.sol`, `code/contracts/src/RevenueVault.sol`.
   - What the repo proves today is productive assetization of hosted compute and delivery cash flow, not a legal wrapper around traditional real-world property.
   - Submission implication: say OutcomeX turns hosted machine capacity into transferable, yield-bearing onchain assets tied to verified delivery outcomes.

## Strengths worth emphasizing

- The system boundary is unusually clear for a hackathon project.
  - `code/backend` owns product control, payment intents, execution dispatch, read models, and state convergence.
  - `code/contracts` owns payment truth, settlement rules, ownership, transfer guards, and claims.
  - `code/agentskillos` is kept as an execution kernel instead of being mixed into financial logic.

- The settlement model is financially legible.
  - `SettlementController` defines platform share, refund logic, and payout handling: `code/contracts/src/SettlementController.sol`.
  - `RevenueVault` tracks machine-side claimable revenue and transfer-blocking unsettled value: `code/contracts/src/RevenueVault.sol`.

- The machine asset story is not cosmetic.
  - Transfer restrictions are tied to active orders and unsettled revenue, which makes the NFT behave like an asset with operating constraints instead of a collectible shell: `code/contracts/src/MachineAssetNFT.sol`, `code/contracts/src/OrderBook.sol`.

- The repo includes meaningful integration evidence.
  - Contract tests cover lifecycle, router behavior, and marketplace flows: `code/contracts/test/OutcomeXLifecycle.t.sol`, `code/contracts/test/OrderPaymentRouter.t.sol`, `code/contracts/test/MachineMarketplace.t.sol`.
  - Backend API and smoke coverage extends through HSP, direct pay, execution, claims, and marketplace paths: `code/backend/tests/api`, `code/backend/tests/smoke`.
  - The E2E validation doc shows the team has already exercised the multi-step product loop, not just isolated unit tests: `docs/e2e-validation-2026-04-09-cn.md`.

- The self-use split is a good product decision.
  - Owner-only self-use avoids polluting buyer settlement and revenue accounting with internal machine usage: `code/backend/app/api/routes/self_use.py`.

## DeFi applicability

OutcomeX fits the DeFi track because it is not merely using tokens for checkout. The protocol logic actually uses familiar DeFi primitives:

- programmable payment rails (`PWR`, `USDC`, `USDT`, HSP-confirmed stablecoins)
- escrowed settlement and deterministic fee splitting
- claimable balances for buyers, treasury, and machine-side beneficiaries
- onchain ownership plus transfer guards
- event-driven state projection for product UX

The most compelling DeFi angle for judges is: OutcomeX financializes AI delivery. Work completion and buyer confirmation drive onchain settlement, and that settlement creates machine-side claimable value.

## RWA applicability

OutcomeX is strongest when framed as productive digital RWA rather than classic offchain legal RWA.

Good framing:

- hosted machine capacity becomes a transferable onchain machine asset
- confirmed delivery outcomes generate revenue that accrues to the machine side
- unsettled revenue affects transferability, so ownership and cash flow are linked
- the asset has operating state, payout rights, and usage-linked yield

Risky framing to avoid:

- claiming legal ownership over offchain hardware from the code alone
- implying institutional custody or compliance wrapping that is not implemented in this repo
- equating current machine assets with regulated tokenized stocks, MMFs, or real estate

## Submission guidance

Use these claims aggressively:

- OutcomeX closes the loop between AI demand, onchain payment, delivery confirmation, and machine-side yield.
- AgentSkillOS is the execution kernel, but OutcomeX adds the missing commercial layer: order control, payment truth, settlement, claims, and asset constraints.
- The machine NFT is productive because it is tied to confirmed delivery cash flow, not just marketplace speculation.
- HashKey Chain matters because the project combines wallet-native settlement with an HSP stablecoin rail that reduces user friction.

Use these claims carefully:

- say "references and vendors AgentSkillOS, with OutcomeX-specific financial control-plane changes"
- say "built around a wallet-first target architecture" rather than "every action is already purely wallet-direct"
- say "HSP-integrated" rather than "fully production-configured merchant rollout"
- say "machine-backed productive asset" rather than broad legal claims about traditional real-world assets

## Bottom line

OutcomeX is submission-ready if the documentation tells the truth crisply:

- present the repo as an AI delivery settlement network, not as a generic agent wrapper
- highlight the onchain economics, not just the model orchestration
- acknowledge the remaining convergence gaps where the live default path is still catching up with the target architecture
- make the RWA claim through productive machine cash flow, not through legal overreach
