import json
from pathlib import Path

from app.core.config import Settings
from app.execution.contracts import ExecutionStrategy
from app.integrations.agentskillos_execution_service import AgentSkillOSExecutionService


class _BridgeStub:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def resolve_repo_root(self) -> Path:
        return self.repo_root

    def resolve_python_executable(self, _repo_root: Path) -> Path:
        return self.repo_root / ".venv" / "bin" / "python"

    def build_execution_env(self) -> dict[str, str]:
        return {"LLM_MODEL": "openai/qwen3.6-plus"}


def test_execution_service_submit_and_poll_reads_run_record(tmp_path: Path) -> None:
    output_root = tmp_path / "runs"
    repo_root = tmp_path / "agentskillos"
    (repo_root / ".venv" / "bin").mkdir(parents=True)
    (repo_root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")

    def launcher(command, *, cwd: str, env: dict[str, str]) -> int:
        record_path = Path(command[4])
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        payload.update(
            {
                "status": "succeeded",
                "workspace_path": "/tmp/workspace",
                "run_dir": "/tmp/run-dir",
                "preview_manifest": [{"path": "workspace/preview.png", "type": "image", "role": "final"}],
                "artifact_manifest": [{"path": "workspace/final.docx", "type": "document", "role": "final"}],
                "skills_manifest": [{"skill_id": "docx", "skill_path": "/skills/docx", "status": "selected"}],
                "model_usage_manifest": [{"provider": "agentskillos_internal", "model": "openai/qwen3.6-plus"}],
                "summary_metrics": {"total_input_tokens": 100, "total_output_tokens": 50},
            }
        )
        record_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        assert cwd == str(repo_root)
        assert env["LLM_MODEL"] == "openai/qwen3.6-plus"
        return 4242

    service = AgentSkillOSExecutionService(
        settings=Settings(
            agentskillos_execution_output_root=str(output_root),
        ),
        bridge=_BridgeStub(repo_root),
        launcher=launcher,
    )

    submitted = service.submit_task(
        external_order_id="order-1",
        prompt="Create report",
        input_files=("brief.md",),
        execution_strategy=ExecutionStrategy.SIMPLICITY,
    )
    assert submitted.run_id.startswith("aso-run-")
    assert submitted.status.value == "succeeded"
    assert submitted.workspace_path == "/tmp/workspace"
    assert submitted.submission_payload == {
        "intent": "Create report",
        "files": ["brief.md"],
        "execution_strategy": "simplicity",
    }

    snapshot = service.get_run(submitted.run_id)
    assert snapshot.external_order_id == "order-1"
    assert snapshot.status.value == "succeeded"
    assert snapshot.submission_payload == submitted.submission_payload
    assert snapshot.skills_manifest[0]["skill_id"] == "docx"
    assert snapshot.model_usage_manifest[0]["model"] == "openai/qwen3.6-plus"
