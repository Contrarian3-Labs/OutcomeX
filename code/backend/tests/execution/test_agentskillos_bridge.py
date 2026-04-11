from __future__ import annotations

import sys
from pathlib import Path

from app.core.config import Settings
from app.integrations import agentskillos_bridge as bridge_module
from app.integrations.agentskillos_bridge import AgentSkillOSBridge


def _make_repo_root(path: Path) -> None:
    (path / 'src' / 'workflow').mkdir(parents=True, exist_ok=True)
    (path / 'run.py').write_text('print("run")\n', encoding='utf-8')
    (path / 'src' / 'workflow' / 'service.py').write_text('def discover_skills(*args, **kwargs):\n    return []\n', encoding='utf-8')


def test_resolve_repo_root_prefers_vendored_monorepo_copy(tmp_path, monkeypatch) -> None:
    repo_root = tmp_path / 'OutcomeX'
    vendored = repo_root / 'code' / 'agentskillos'
    legacy = tmp_path / 'Hashkey' / 'reference-code' / 'AgentSkillOS'
    backend_file = repo_root / 'code' / 'backend' / 'app' / 'integrations' / 'agentskillos_bridge.py'
    backend_file.parent.mkdir(parents=True, exist_ok=True)
    backend_file.write_text('# test\n', encoding='utf-8')
    _make_repo_root(vendored)
    _make_repo_root(legacy)

    monkeypatch.setattr(bridge_module, '__file__', str(backend_file))
    bridge = AgentSkillOSBridge(settings=Settings(dashscope_api_key='test-key', agentskillos_root=''))

    assert bridge.resolve_repo_root() == vendored


def test_resolve_python_executable_falls_back_to_current_interpreter(tmp_path) -> None:
    repo_root = tmp_path / 'agentskillos'
    _make_repo_root(repo_root)

    bridge = AgentSkillOSBridge(settings=Settings(dashscope_api_key='test-key', agentskillos_python_executable=''))
    python_path = bridge.resolve_python_executable(repo_root)

    assert python_path == Path(sys.executable)


def test_resolve_python_executable_prefers_configured_override(tmp_path) -> None:
    repo_root = tmp_path / 'agentskillos'
    configured_python = tmp_path / 'custom-python'
    _make_repo_root(repo_root)
    configured_python.write_text('', encoding='utf-8')

    bridge = AgentSkillOSBridge(
        settings=Settings(
            dashscope_api_key='test-key',
            agentskillos_python_executable=str(configured_python),
        )
    )

    python_path = bridge.resolve_python_executable(repo_root)

    assert python_path == configured_python
