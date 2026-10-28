import tomllib
from pathlib import Path


def test_resource_monitor_dependency_is_declared_for_package_installs():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(item.startswith("psutil>=") for item in metadata["project"]["dependencies"])
