# OutcomeX Execution Observability SSE Design

Date: 2026-04-09
Owner: Codex
Status: Draft for user review

## 1. Goal

Build a real-time execution observability layer for OutcomeX so the frontend can show what AgentSkillOS is doing while a run is in progress, instead of only showing a coarse `running` status.

This design must let the frontend see:

- current phase and current step
- candidate plans and the selected plan
- DAG node progress
- real-time structured execution events
- real-time raw logs from the run `logs/` directory
- stdout/stderr as fallback and debugging channels
- artifact and preview creation events
- stalled / dead / failed state transitions

The design prioritizes observability first. Product flow polish comes later.

## 2. Current Problem

Today the backend exposes an execution-run snapshot via `GET /api/v1/execution-runs/{run_id}` and the frontend mostly polls that snapshot. The AgentSkillOS wrapper writes a small amount of state to `run.json` and `events.ndjson`, but the event stream is too thin to explain what is happening inside planning and execution.

As a result:

- frontend can only say `running`
- frontend cannot show plan candidates or why one plan was selected
- frontend cannot show DAG progress
- frontend cannot show the run's native `logs/` directory
- a run can stall in `planning` while still appearing alive

This is exactly the failure mode seen in the current local demo: the process is alive, but there is no useful live telemetry after `planning`.

## 3. Design Principles

1. Keep the execution kernel in AgentSkillOS; OutcomeX should observe, not re-implement execution.
2. Separate product-facing state from raw debugging data.
3. Use append-only event streams so the frontend can recover after disconnects.
4. Prefer SSE over WebSocket because the traffic is primarily server-to-client and the implementation is simpler.
5. Treat raw multi-file logs under `logs/` as a first-class UX surface, not an afterthought.
6. Detect stalled runs explicitly instead of leaving them as indefinite `running`.

## 4. Recommended Architecture

Use a dual-channel observability model:

- Structured execution event stream
  - for frontend timeline, plan view, DAG state, artifact lifecycle
- Raw log file streaming
  - for direct inspection of AgentSkillOS native logs under each run's `logs/` directory

The frontend should always restore from a snapshot first, then attach live streams:

1. Load run snapshot
2. Load current log file list
3. Open SSE stream for structured execution events
4. Open SSE stream for the currently selected raw log file
5. Reconnect with `after_seq` / `offset` after disconnects

## 5. Structured Event Model

Every execution event must be append-only and ordered by a monotonically increasing `seq`.

Example:

```json
{
  "seq": 12,
  "timestamp": "2026-04-09T14:16:38.565Z",
  "run_id": "aso-run-xxx",
  "phase": "plan_generation",
  "event": "plan_candidates_generated",
  "level": "info",
  "message": "Generated 3 candidate plans",
  "data": {}
}
```

Required fields:

- `seq`
- `timestamp`
- `run_id`
- `phase`
- `event`
- `level`
- `message`
- `data`

Standard phases:

- `starting`
- `anchor_inference`
- `skill_discovery`
- `plan_generation`
- `plan_selection`
- `execution`
- `artifact_collection`
- `preview_ready`
- `finalizing`
- `finished`
- `failed`
- `stalled`

Required event types:

- `run_started`
- `anchor_inferred`
- `skills_discovered`
- `plan_candidates_generated`
- `plan_selected`
- `dag_node_started`
- `dag_node_finished`
- `tool_call_started`
- `tool_call_finished`
- `model_call_started`
- `model_call_finished`
- `artifact_created`
- `preview_created`
- `heartbeat`
- `stdout_line`
- `stderr_line`
- `run_stalled`
- `run_finished`
- `run_failed`

## 6. Raw Log File Model

Each run may expose multiple native log files. The frontend should default to showing these files directly.

The backend should surface each log source as:

```json
{
  "kind": "raw_file",
  "name": "planner.log",
  "path": ".../logs/planner.log",
  "size": 12345,
  "updated_at": "2026-04-09T14:17:01.000Z"
}
```

Kinds:

- `raw_file`
- `stdout`
- `stderr`

Default frontend selection rules:

1. If `logs/` contains files, select the first raw file by default
2. If not, fall back to `stdout.log`
3. Keep `stderr.log` immediately accessible as its own tab

## 7. Backend API Contract

### 7.1 Snapshot

`GET /api/v1/execution-runs/{run_id}`

Extend the existing response with:

- `plan_candidates`
- `selected_plan`
- `dag`
- `active_node_id`
- `logs_root_path`
- `log_files`
- `event_cursor`
- `last_progress_at`
- `stalled`

### 7.2 Structured SSE

`GET /api/v1/execution-runs/{run_id}/stream`

SSE payload should emit:

- `event: execution_event`
- `data: <structured event json>`

Reconnect support:

- `?after_seq=<n>`

### 7.3 Structured Event Backfill

`GET /api/v1/execution-runs/{run_id}/events?after_seq=<n>`

Used for:

- cold start hydration
- reconnect recovery
- environments where SSE temporarily fails

### 7.4 Log File Listing

`GET /api/v1/execution-runs/{run_id}/logs`

Returns:

- `logs_root_path`
- `files[]`

### 7.5 Raw Log Streaming

`GET /api/v1/execution-runs/{run_id}/logs/stream?file=<name>&offset=<n>`

SSE payload should emit:

- `event: log_line`
- `data: { file, offset, line, level? }`

### 7.6 Raw Log Backfill

`GET /api/v1/execution-runs/{run_id}/logs/read?file=<name>&offset=<n>`

Returns:

- text chunk or line array
- `next_offset`

## 8. AgentSkillOS Wrapper Changes

The wrapper in `code/backend/app/integrations/agentskillos_execution_service.py` is the critical integration boundary.

It must be upgraded from a coarse state writer into a telemetry adapter.

Required wrapper responsibilities:

1. Emit structured events before and after:
   - anchor inference
   - skill discovery
   - plan generation
   - plan selection
   - DAG node execution
   - artifact collection

2. Persist candidate plan payloads, not only the final selected plan
3. Capture live stdout/stderr and mirror them into:
   - raw files
   - structured `stdout_line` / `stderr_line` events
4. Detect the run `logs/` directory as soon as it exists
5. Enumerate multi-file raw logs and update snapshot metadata
6. Maintain `last_progress_at`
7. Emit explicit `run_stalled` when there is no meaningful progress for a configured threshold

The wrapper must not block until the full run completes before writing useful state.

## 9. DAG / Plan Representation

The frontend needs more than `selected_plan.name`.

Backend should expose:

- `plan_candidates[]`
  - index
  - name
  - description
  - strategy
- `selected_plan`
- `dag`
  - nodes
  - edges
  - node status: `pending | running | succeeded | failed`
- `active_node_id`

This allows the frontend to show:

- all candidate plans
- which plan was locked
- where execution currently sits inside the DAG

## 10. Frontend UX Contract

Add a dedicated `Execution Observatory` area to the order detail flow.

Primary sections:

### 10.1 Status

- run status
- current phase
- current step
- last heartbeat
- stalled / dead / failed warnings

### 10.2 Plans

- candidate plan cards
- selected plan highlight
- strategy
- plan binding consistency

### 10.3 DAG

- node list or graph
- active node
- node-by-node status transitions

### 10.4 Logs

Default behavior:

- show `logs/` multi-file logs by default
- include `stdout.log` and `stderr.log`
- allow file switching
- auto-follow live tail
- allow pause, search, and error highlighting

### 10.5 Outputs

- preview manifest
- artifact manifest
- direct file open/download actions

## 11. Stalled and Dead Run Detection

This design requires explicit stalled detection.

Suggested rules:

- `stalled` if no new structured event, no log growth, and no artifact progress for `N` seconds while status is `running`
- `dead` if process PID is no longer alive while run status is still non-terminal

Suggested fields on snapshot:

- `last_progress_at`
- `stalled`
- `stalled_reason`
- `pid_alive`

Suggested frontend messages:

- `Stalled in planning`
- `No new progress for 60s`
- `Execution process exited unexpectedly`
- `Stream disconnected; retrying`

## 12. Implementation Order

### Phase 1 - Backend Observability Foundations

- define structured event schema
- add event sequencing
- upgrade wrapper to emit richer events
- detect and expose raw `logs/` directory
- add event backfill API
- add raw log list API
- add raw log read API
- add SSE event stream API
- add SSE log stream API

### Phase 2 - Frontend Observatory UI

- load snapshot
- load log file list
- subscribe to SSE execution stream
- subscribe to active log file stream
- build status panel
- build plan panel
- build logs panel

### Phase 3 - Failure / Reconnect / Stall Hardening

- reconnection with `after_seq`
- reconnection with `offset`
- stalled detection UI
- dead-process UI
- recovery after refresh

## 13. Testing Strategy

Backend tests must cover:

- event ordering
- SSE stream correctness
- log file enumeration
- raw log offset reads
- stalled transition logic
- reconnect from `after_seq`

Frontend tests must cover:

- snapshot + stream hydration
- switching between raw log files
- stalled UI
- artifact updates appearing live
- reconnect behavior

E2E checks should include:

- normal successful run
- run with multi-file raw logs
- planning stall
- failed run
- cancelled run

## 14. Non-Goals for This Slice

This slice does not attempt to:

- redesign the entire order page UX
- replace AgentSkillOS planning logic
- solve recommendation correctness
- fix every current frontend workflow issue

It is intentionally focused on observability first.

## 15. Recommendation

Proceed with:

- SSE for structured execution events
- default multi-file raw log visibility from run `logs/`
- snapshot + backfill APIs for reconnect safety

This is the smallest architecture that makes OutcomeX execution understandable in real time while preserving AgentSkillOS as the execution kernel.
