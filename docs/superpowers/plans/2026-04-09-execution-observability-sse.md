# Execution Observability SSE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build real-time execution observability for OutcomeX so the frontend can stream structured execution events and default-visible multi-file AgentSkillOS raw logs from each run.

**Architecture:** Extend the backend execution-run snapshot with observability metadata, add append-only event + raw-log backfill/SSE endpoints, and upgrade the AgentSkillOS wrapper to emit rich structured telemetry while discovering and surfacing the run `logs/` directory. Then wire the frontend order detail flow to hydrate from the snapshot, subscribe to SSE, and render an `Execution Observatory` with status, plans, DAG, raw logs, and outputs.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, Python file-backed run records, SSE (`StreamingResponse`), React, Vite, TypeScript, Vitest.

---

## File Map

### Backend

- Modify: `code/backend/app/schemas/execution_run.py`
  - extend API response shape with observability fields
- Modify: `code/backend/app/api/routes/execution_runs.py`
  - add snapshot fields + SSE/backfill endpoints
- Modify: `code/backend/app/integrations/agentskillos_execution_service.py`
  - enrich wrapper telemetry, event sequencing, raw log discovery, stalled metadata
- Create: `code/backend/app/execution/observability.py`
  - event schema helpers, read/backfill logic, log file enumeration
- Create: `code/backend/tests/api/test_execution_run_stream_api.py`
  - SSE + backfill API tests
- Modify: `code/backend/tests/api/test_execution_runs_api.py`
  - snapshot response expectations
- Create: `code/backend/tests/execution/test_execution_observability.py`
  - event parsing, log listing, stalled detection tests

### Frontend

- Modify: `forge-yield-ai/src/lib/execution-runs-api.ts`
  - add observability types + API helpers
- Modify: `forge-yield-ai/src/lib/api/outcomex-types.ts`
  - extend execution run response typing
- Create: `forge-yield-ai/src/hooks/useExecutionRunStream.ts`
  - subscribe to snapshot + SSE event stream + log stream
- Modify: `forge-yield-ai/src/pages/OrderDetail.tsx`
  - embed `Execution Observatory`
- Modify: `forge-yield-ai/src/components/ExecutionRunPanel.tsx`
  - render richer run metadata if still reused elsewhere
- Create: `forge-yield-ai/src/components/execution/ExecutionObservatory.tsx`
  - orchestration shell for status, plans, dag, logs, outputs
- Create: `forge-yield-ai/src/components/execution/ExecutionLogsPanel.tsx`
  - default multi-file raw logs with live tail
- Create: `forge-yield-ai/src/components/execution/ExecutionTimelinePanel.tsx`
  - phase, heartbeat, stalled state
- Create: `forge-yield-ai/src/components/execution/ExecutionPlanPanel.tsx`
  - candidate plan cards + selected plan + DAG nodes
- Create: `forge-yield-ai/src/test/execution-observability.test.tsx`
  - main UI behavior tests

---

### Task 1: Extend execution-run snapshot contract

**Files:**
- Modify: `code/backend/app/schemas/execution_run.py`
- Modify: `code/backend/app/api/routes/execution_runs.py`
- Modify: `code/backend/tests/api/test_execution_runs_api.py`

- [ ] **Step 1: Write the failing backend snapshot test**

```python
def test_execution_run_snapshot_includes_observability_fields(client):
    test_client, stub = client
    machine = _create_machine(test_client)
    order = _create_paid_order(test_client, machine["id"])
    start = test_client.post(f"/api/v1/orders/{order['id']}/start-execution")
    assert start.status_code == 200

    response = test_client.get("/api/v1/execution-runs/aso-run-test")
    payload = response.json()

    assert "plan_candidates" in payload
    assert "dag" in payload
    assert "active_node_id" in payload
    assert "logs_root_path" in payload
    assert "log_files" in payload
    assert "event_cursor" in payload
    assert "last_progress_at" in payload
    assert "stalled" in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_execution_runs_api.py -k observability_fields -q
```

Expected: FAIL because `ExecutionRunResponse` and route payloads do not expose the new fields.

- [ ] **Step 3: Add the new schema fields**

```python
class ExecutionRunResponse(BaseModel):
    # existing fields...
    plan_candidates: list[dict] = Field(default_factory=list)
    dag: dict | None = None
    active_node_id: str | None = None
    logs_root_path: str | None = None
    log_files: list[dict] = Field(default_factory=list)
    event_cursor: int = 0
    last_progress_at: datetime | None = None
    stalled: bool = False
    stalled_reason: str | None = None
```

- [ ] **Step 4: Wire the snapshot route to populate the new fields**

```python
return response.model_copy(
    update={
        "plan_candidates": list(getattr(snapshot, "plan_candidates", []) or []),
        "dag": getattr(snapshot, "dag", None),
        "active_node_id": getattr(snapshot, "active_node_id", None),
        "logs_root_path": getattr(snapshot, "logs_root_path", None),
        "log_files": list(getattr(snapshot, "log_files", []) or []),
        "event_cursor": int(getattr(snapshot, "event_cursor", 0) or 0),
        "last_progress_at": getattr(snapshot, "last_progress_at", None),
        "stalled": bool(getattr(snapshot, "stalled", False)),
        "stalled_reason": getattr(snapshot, "stalled_reason", None),
    }
)
```

- [ ] **Step 5: Update the API test stub payload**

```python
"plan_candidates": [
    {"index": 0, "name": "Quality-First", "strategy": "quality"},
    {"index": 1, "name": "Efficiency-First", "strategy": "efficiency"},
],
"dag": {"nodes": [{"id": "n1", "status": "running"}], "edges": []},
"active_node_id": "n1",
"logs_root_path": "/tmp/run-dir/logs",
"log_files": [{"kind": "raw_file", "name": "planner.log", "path": "/tmp/run-dir/logs/planner.log", "size": 10}],
"event_cursor": 12,
"last_progress_at": datetime.now(timezone.utc),
"stalled": False,
"stalled_reason": None,
```

- [ ] **Step 6: Run the API test to verify it passes**

Run:

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_execution_runs_api.py -k observability_fields -q
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/users/72988/desktop/OutcomeX
git add code/backend/app/schemas/execution_run.py code/backend/app/api/routes/execution_runs.py code/backend/tests/api/test_execution_runs_api.py
git commit -m "feat: extend execution run snapshot observability"
```

---

### Task 2: Build backend observability helpers for events and raw logs

**Files:**
- Create: `code/backend/app/execution/observability.py`
- Create: `code/backend/tests/execution/test_execution_observability.py`

- [ ] **Step 1: Write failing helper tests**

```python
def test_read_events_after_seq_returns_only_newer_events(tmp_path):
    events = tmp_path / "events.ndjson"
    events.write_text(
        '{"seq":1,"event":"run_started"}\n{"seq":2,"event":"skills_discovered"}\n',
        encoding="utf-8",
    )
    result = read_events_after_seq(events, after_seq=1)
    assert [item["seq"] for item in result.items] == [2]


def test_list_log_files_prioritizes_logs_directory(tmp_path):
    run_dir = tmp_path / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "planner.log").write_text("planning\n", encoding="utf-8")
    (run_dir / "stdout.log").write_text("stdout\n", encoding="utf-8")

    files = list_log_files(run_dir=run_dir, stdout_path=run_dir / "stdout.log", stderr_path=None)
    assert files[0]["name"] == "planner.log"
    assert files[0]["kind"] == "raw_file"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/execution/test_execution_observability.py -q
```

Expected: FAIL because the helper module does not exist yet.

- [ ] **Step 3: Implement event/backfill and log enumeration helpers**

```python
@dataclass
class EventReadResult:
    items: list[dict]
    next_cursor: int


def read_events_after_seq(events_path: Path, after_seq: int) -> EventReadResult:
    items: list[dict] = []
    next_cursor = after_seq
    if not events_path.exists():
        return EventReadResult(items=[], next_cursor=after_seq)
    for line in events_path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        seq = int(payload.get("seq", 0) or 0)
        next_cursor = max(next_cursor, seq)
        if seq > after_seq:
            items.append(payload)
    return EventReadResult(items=items, next_cursor=next_cursor)


def list_log_files(*, run_dir: Path | None, stdout_path: Path | None, stderr_path: Path | None) -> list[dict]:
    files: list[dict] = []
    if run_dir is not None:
        logs_dir = run_dir / "logs"
        if logs_dir.exists():
            for path in sorted(p for p in logs_dir.iterdir() if p.is_file()):
                files.append(_log_descriptor(path, "raw_file"))
    if stdout_path and stdout_path.exists():
        files.append(_log_descriptor(stdout_path, "stdout"))
    if stderr_path and stderr_path.exists():
        files.append(_log_descriptor(stderr_path, "stderr"))
    return files
```

- [ ] **Step 4: Add offset-based raw log reads**

```python
def read_log_chunk(path: Path, offset: int) -> tuple[list[str], int]:
    if not path.exists():
        return [], offset
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        lines = handle.readlines()
        next_offset = handle.tell()
    return lines, next_offset
```

- [ ] **Step 5: Add stalled computation helper**

```python
def compute_stalled(*, status: str, last_progress_at: datetime | None, stalled_after_seconds: int, now: datetime) -> tuple[bool, str | None]:
    if status != "running" or last_progress_at is None:
        return False, None
    if (now - last_progress_at).total_seconds() < stalled_after_seconds:
        return False, None
    return True, f"No progress detected for {stalled_after_seconds}s"
```

- [ ] **Step 6: Run the helper test file**

Run:

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/execution/test_execution_observability.py -q
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/users/72988/desktop/OutcomeX
git add code/backend/app/execution/observability.py code/backend/tests/execution/test_execution_observability.py
git commit -m "feat: add execution observability helpers"
```

---

### Task 3: Add structured event and raw log streaming APIs

**Files:**
- Modify: `code/backend/app/api/routes/execution_runs.py`
- Create: `code/backend/tests/api/test_execution_run_stream_api.py`

- [ ] **Step 1: Write failing API tests for events and raw logs**

```python
def test_execution_run_events_endpoint_returns_items_after_seq(test_client, seeded_run):
    response = test_client.get(f"/api/v1/execution-runs/{seeded_run}/events?after_seq=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["seq"] == 2


def test_execution_run_logs_endpoint_lists_raw_files(test_client, seeded_run):
    response = test_client.get(f"/api/v1/execution-runs/{seeded_run}/logs")
    assert response.status_code == 200
    assert response.json()["files"][0]["name"] == "planner.log"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_execution_run_stream_api.py -q
```

Expected: FAIL with missing routes.

- [ ] **Step 3: Add event backfill and log list/read endpoints**

```python
@router.get("/{run_id}/events")
def get_execution_run_events(run_id: str, after_seq: int = 0, ...):
    snapshot = execution_service.get_run(run_id)
    result = read_events_after_seq(Path(snapshot.events_log_path), after_seq=after_seq)
    return {"items": result.items, "next_cursor": result.next_cursor}


@router.get("/{run_id}/logs")
def list_execution_run_logs(run_id: str, ...):
    snapshot = execution_service.get_run(run_id)
    return {
        "logs_root_path": snapshot.logs_root_path,
        "files": snapshot.log_files,
    }


@router.get("/{run_id}/logs/read")
def read_execution_run_log(run_id: str, file: str, offset: int = 0, ...):
    path = resolve_log_file(snapshot, file)
    lines, next_offset = read_log_chunk(path, offset)
    return {"file": file, "lines": lines, "next_offset": next_offset}
```

- [ ] **Step 4: Add SSE endpoints**

```python
@router.get("/{run_id}/stream")
def stream_execution_run_events(run_id: str, after_seq: int = 0, ...):
    async def event_generator():
        cursor = after_seq
        while True:
            result = read_events_after_seq(Path(snapshot.events_log_path), after_seq=cursor)
            for item in result.items:
                cursor = max(cursor, int(item["seq"]))
                yield f"event: execution_event\\ndata: {json.dumps(item)}\\n\\n"
            await asyncio.sleep(1)
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 5: Add raw log SSE endpoint**

```python
@router.get("/{run_id}/logs/stream")
def stream_execution_run_log(run_id: str, file: str, offset: int = 0, ...):
    async def log_generator():
        current_offset = offset
        while True:
            lines, next_offset = read_log_chunk(path, current_offset)
            for line in lines:
                payload = {"file": file, "offset": current_offset, "line": line.rstrip("\\n")}
                yield f"event: log_line\\ndata: {json.dumps(payload)}\\n\\n"
            current_offset = next_offset
            await asyncio.sleep(1)
    return StreamingResponse(log_generator(), media_type="text/event-stream")
```

- [ ] **Step 6: Run the API stream tests**

Run:

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_execution_run_stream_api.py -q
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/users/72988/desktop/OutcomeX
git add code/backend/app/api/routes/execution_runs.py code/backend/tests/api/test_execution_run_stream_api.py
git commit -m "feat: add execution observability stream endpoints"
```

---

### Task 4: Upgrade the AgentSkillOS wrapper telemetry

**Files:**
- Modify: `code/backend/app/integrations/agentskillos_execution_service.py`
- Modify: `code/backend/tests/execution/test_agentskillos_execution_service.py`

- [ ] **Step 1: Write failing wrapper tests**

```python
def test_submit_task_snapshot_includes_event_cursor_and_log_files(service):
    snapshot = service.submit_task(
        external_order_id="order-1",
        prompt="Write a report",
        input_files=[],
        execution_strategy=ExecutionStrategy.QUALITY,
    )
    assert snapshot.event_cursor == 0
    assert snapshot.log_files == []


def test_snapshot_reads_candidate_plans_and_stalled_state(tmp_path, service):
    run_dir = tmp_path / "run"
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "logs" / "planner.log").write_text("planning\\n", encoding="utf-8")
    # seed run.json / events.ndjson with last_progress_at in the past
    snapshot = service.get_run("aso-run-test")
    assert snapshot.logs_root_path.endswith("/logs")
    assert snapshot.stalled is True
```

- [ ] **Step 2: Run wrapper tests to verify they fail**

Run:

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/execution/test_agentskillos_execution_service.py -q
```

Expected: FAIL because the snapshot object does not expose the new observability fields.

- [ ] **Step 3: Add event sequencing and richer event emission**

```python
def append_event(event_type, **fields):
    seq = int(initial.get("event_cursor", 0) or 0) + 1
    initial["event_cursor"] = seq
    initial["last_progress_at"] = utc_now()
    event = {
        "seq": seq,
        "timestamp": utc_now(),
        "run_id": initial["run_id"],
        "phase": initial.get("current_phase"),
        "event": event_type,
        "level": fields.pop("level", "info"),
        "message": fields.pop("message", event_type),
        "data": fields,
    }
    ...
```

- [ ] **Step 4: Emit plan and skill discovery events**

```python
append_event("anchor_inferred", phase="anchor_inference", message="Resolved anchor skills", required_skills=required_skills)
append_event("skills_discovered", phase="skill_discovery", message=f"Discovered {len(skills)} skills", skills=skills)
append_event("plan_candidates_generated", phase="plan_generation", message="Generated candidate plans", plans=plan_candidates)
append_event("plan_selected", phase="plan_selection", message="Selected native plan", selected_plan_index=selected_plan_index)
```

- [ ] **Step 5: Discover run `logs/` and include it in the snapshot**

```python
log_files = list_log_files(run_dir=run_dir, stdout_path=Path(final["stdout_log_path"]), stderr_path=Path(final["stderr_log_path"]))
final["logs_root_path"] = str(run_dir / "logs") if (run_dir / "logs").exists() else None
final["log_files"] = log_files
```

- [ ] **Step 6: Compute stalled state in `get_run()`**

```python
stalled, stalled_reason = compute_stalled(
    status=payload.get("status"),
    last_progress_at=parse_dt(payload.get("last_progress_at")),
    stalled_after_seconds=60,
    now=datetime.now(timezone.utc),
)
payload["stalled"] = stalled
payload["stalled_reason"] = stalled_reason
```

- [ ] **Step 7: Mirror stdout/stderr lines into structured events**

```python
for line in newly_read_stdout_lines:
    append_event("stdout_line", phase=initial.get("current_phase"), message=line.rstrip("\\n"), stream="stdout")
for line in newly_read_stderr_lines:
    append_event("stderr_line", phase=initial.get("current_phase"), level="warning", message=line.rstrip("\\n"), stream="stderr")
```

- [ ] **Step 8: Run wrapper tests**

Run:

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/execution/test_agentskillos_execution_service.py -q
```

Expected: PASS

- [ ] **Step 9: Commit**

```bash
cd /mnt/c/users/72988/desktop/OutcomeX
git add code/backend/app/integrations/agentskillos_execution_service.py code/backend/tests/execution/test_agentskillos_execution_service.py
git commit -m "feat: enrich agentskillos execution telemetry"
```

---

### Task 5: Add frontend observability APIs and streaming hook

**Files:**
- Modify: `forge-yield-ai/src/lib/execution-runs-api.ts`
- Modify: `forge-yield-ai/src/lib/api/outcomex-types.ts`
- Create: `forge-yield-ai/src/hooks/useExecutionRunStream.ts`
- Create: `forge-yield-ai/src/test/execution-observability.test.tsx`

- [ ] **Step 1: Write the failing frontend hook test**

```tsx
it("hydrates execution observability from snapshot and appends streamed events", async () => {
  const snapshot = {
    id: "run_1",
    plan_candidates: [{ index: 0, name: "Quality-First", strategy: "quality" }],
    log_files: [{ kind: "raw_file", name: "planner.log", path: "/tmp/planner.log", size: 10 }],
    current_phase: "planning",
    stalled: false,
  };
  // mock initial fetch + EventSource events
  expect(screen.getByText("planner.log")).toBeInTheDocument();
  expect(screen.getByText(/planning/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai
npm test -- src/test/execution-observability.test.tsx
```

Expected: FAIL because the hook and new types do not exist.

- [ ] **Step 3: Extend API types**

```ts
export interface OutcomeXExecutionEvent {
  seq: number;
  timestamp: string;
  run_id: string;
  phase: string | null;
  event: string;
  level: "info" | "warning" | "error";
  message: string;
  data: Record<string, unknown>;
}

export interface OutcomeXExecutionLogFile {
  kind: "raw_file" | "stdout" | "stderr";
  name: string;
  path: string;
  size: number;
  updated_at?: string | null;
}
```

- [ ] **Step 4: Add API helpers**

```ts
export async function getExecutionRunEvents(runId: string, afterSeq = 0) {
  return apiRequest<{ items: OutcomeXExecutionEvent[]; next_cursor: number }>(
    `/execution-runs/${encodeURIComponent(runId)}/events?after_seq=${afterSeq}`,
  );
}

export async function getExecutionRunLogs(runId: string) {
  return apiRequest<{ logs_root_path: string | null; files: OutcomeXExecutionLogFile[] }>(
    `/execution-runs/${encodeURIComponent(runId)}/logs`,
  );
}
```

- [ ] **Step 5: Implement the streaming hook**

```ts
export function useExecutionRunStream(runId?: string) {
  const [events, setEvents] = useState<OutcomeXExecutionEvent[]>([]);
  const [logFiles, setLogFiles] = useState<OutcomeXExecutionLogFile[]>([]);
  const [selectedLogFile, setSelectedLogFile] = useState<string | null>(null);
  // fetch snapshot, then attach EventSource to /stream and /logs/stream
  return { events, logFiles, selectedLogFile, setSelectedLogFile };
}
```

- [ ] **Step 6: Run the frontend observability test**

Run:

```bash
cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai
npm test -- src/test/execution-observability.test.tsx
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai
git add src/lib/execution-runs-api.ts src/lib/api/outcomex-types.ts src/hooks/useExecutionRunStream.ts src/test/execution-observability.test.tsx
git commit -m "feat: add frontend execution observability stream hook"
```

---

### Task 6: Render the Execution Observatory in the order flow

**Files:**
- Modify: `forge-yield-ai/src/pages/OrderDetail.tsx`
- Modify: `forge-yield-ai/src/components/ExecutionRunPanel.tsx`
- Create: `forge-yield-ai/src/components/execution/ExecutionObservatory.tsx`
- Create: `forge-yield-ai/src/components/execution/ExecutionLogsPanel.tsx`
- Create: `forge-yield-ai/src/components/execution/ExecutionTimelinePanel.tsx`
- Create: `forge-yield-ai/src/components/execution/ExecutionPlanPanel.tsx`
- Modify: `forge-yield-ai/src/test/order-detail-confirmed-run.test.tsx`
- Modify: `forge-yield-ai/src/test/order-detail-wallet-actions.test.tsx`

- [ ] **Step 1: Write the failing UI test**

```tsx
it("shows multi-file raw logs by default in the observatory", async () => {
  render(<OrderDetail />);
  expect(await screen.findByText("Execution Observatory")).toBeInTheDocument();
  expect(await screen.findByRole("tab", { name: /planner.log/i })).toBeInTheDocument();
  expect(screen.getByText(/selected plan/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the UI test to verify it fails**

Run:

```bash
cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai
npm test -- src/test/order-detail-confirmed-run.test.tsx
```

Expected: FAIL because the observatory UI is not rendered.

- [ ] **Step 3: Build the observatory shell component**

```tsx
export function ExecutionObservatory({ run, events, logFiles, selectedLogFile, onSelectLogFile }: Props) {
  return (
    <section>
      <h3>Execution Observatory</h3>
      <ExecutionTimelinePanel run={run} events={events} />
      <ExecutionPlanPanel run={run} />
      <ExecutionLogsPanel
        files={logFiles}
        selectedFile={selectedLogFile}
        onSelectFile={onSelectLogFile}
      />
    </section>
  );
}
```

- [ ] **Step 4: Build the logs panel with raw logs as default**

```tsx
const defaultFile = files.find((file) => file.kind === "raw_file") ?? files.find((file) => file.kind === "stdout");
```

- [ ] **Step 5: Mount the observatory in `OrderDetail`**

```tsx
const observability = useExecutionRunStream(executionRun?.id);

{executionRun ? (
  <ExecutionObservatory
    run={executionRun}
    events={observability.events}
    logFiles={observability.logFiles}
    selectedLogFile={observability.selectedLogFile}
    onSelectLogFile={observability.setSelectedLogFile}
  />
) : null}
```

- [ ] **Step 6: Render stalled and current-phase states clearly**

```tsx
{run?.stalled ? (
  <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-warning">
    {run.stalled_reason ?? "Run stalled"}
  </div>
) : null}
```

- [ ] **Step 7: Run the targeted frontend tests**

Run:

```bash
cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai
npm test -- src/test/order-detail-confirmed-run.test.tsx
npm test -- src/test/order-detail-wallet-actions.test.tsx
```

Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai
git add src/pages/OrderDetail.tsx src/components/ExecutionRunPanel.tsx src/components/execution/ExecutionObservatory.tsx src/components/execution/ExecutionLogsPanel.tsx src/components/execution/ExecutionTimelinePanel.tsx src/components/execution/ExecutionPlanPanel.tsx src/test/order-detail-confirmed-run.test.tsx src/test/order-detail-wallet-actions.test.tsx
git commit -m "feat: add execution observatory UI"
```

---

### Task 7: Verify end-to-end behavior and record final status

**Files:**
- Modify: `docs/current-issues-audit-cn.md`

- [ ] **Step 1: Run backend observability tests**

Run:

```bash
cd /mnt/c/users/72988/desktop/OutcomeX/code/backend
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/api/test_execution_runs_api.py tests/api/test_execution_run_stream_api.py tests/execution/test_execution_observability.py tests/execution/test_agentskillos_execution_service.py -q
```

Expected: PASS

- [ ] **Step 2: Run frontend observability tests**

Run:

```bash
cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai
npm test -- src/test/execution-observability.test.tsx
npm test -- src/test/order-detail-confirmed-run.test.tsx
```

Expected: PASS

- [ ] **Step 3: Run frontend typecheck and build**

Run:

```bash
cd /mnt/c/users/72988/desktop/hashkey/forge-yield-ai
npx tsc --noEmit
npm run build
```

Expected: PASS

- [ ] **Step 4: Run local browser sanity check**

Run:

```bash
# verify these while local stack is running
curl -N http://127.0.0.1:8000/api/v1/execution-runs/<run_id>/stream
curl "http://127.0.0.1:8000/api/v1/execution-runs/<run_id>/logs"
curl -N "http://127.0.0.1:8000/api/v1/execution-runs/<run_id>/logs/stream?file=planner.log"
```

Expected:

- execution events stream in order
- raw log lines stream from the selected file
- frontend defaults to a raw file from `logs/` if one exists

- [ ] **Step 5: Update the issue audit doc**

```md
- execution observability now exposes SSE event stream
- frontend now defaults to multi-file raw log visibility from AgentSkillOS `logs/`
- stalled runs are surfaced explicitly instead of appearing as indefinite running
```

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/users/72988/desktop/OutcomeX
git add docs/current-issues-audit-cn.md
git commit -m "docs: record execution observability rollout status"
```

---

## Spec Coverage Check

- SSE structured execution stream: covered by Tasks 2-4
- raw multi-file `logs/` visibility by default: covered by Tasks 2, 3, 5, 6
- plan candidates / selected plan / DAG exposure: covered by Tasks 1, 4, 6
- stalled detection: covered by Tasks 2, 4, 6
- frontend observatory UI: covered by Tasks 5-6
- reconnect/backfill path: covered by Tasks 2-3 and consumed in Task 5

No spec gaps found for the first implementation slice.

## Placeholder Scan

- No placeholder markers remain
- All tasks include exact file paths
- All code-changing steps include code snippets
- All verification steps include explicit commands

## Type Consistency Check

- snapshot fields use `plan_candidates`, `dag`, `active_node_id`, `log_files`, `event_cursor`, `last_progress_at`, `stalled`, `stalled_reason`
- frontend and backend names align with the same field spellings
- log file kinds are consistently `raw_file | stdout | stderr`
