from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEV_BROWSER_DIR = PROJECT_ROOT / 'data' / 'skill_seeds' / 'dev-browser'


def test_dev_browser_seed_has_package_json() -> None:
    package_json = DEV_BROWSER_DIR / 'package.json'
    assert package_json.exists(), 'dev-browser seed must include package.json for npm/tsx runtime'

    payload = json.loads(package_json.read_text(encoding='utf-8'))
    assert payload['name'] == 'dev-browser'
    assert payload['type'] == 'module'
    assert payload['dependencies']['playwright']
    assert payload['devDependencies']['tsx']


def test_dev_browser_seed_uses_lf_line_endings_for_runtime_scripts() -> None:
    for relative_path in ('server.sh', 'scripts/start-server.ts'):
        content = (DEV_BROWSER_DIR / relative_path).read_bytes()
        assert b'\r\n' not in content, f'{relative_path} must use LF line endings'
