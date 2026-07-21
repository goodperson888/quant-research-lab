import json
from pathlib import Path

from quant_lab.runs import create_run


def test_run_manifest_contains_reproducibility_fields(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = create_run(
        tmp_path,
        tmp_path / "factor_library" / "registry.sqlite3",
        "backtest",
        random_seed=42,
    )

    manifest = json.loads(
        (Path(result["run_dir"]) / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["code_version"].startswith("tree-sha256:")
    assert manifest["random_seed"] == 42
    assert "data_manifest" in manifest
    assert "parameters" in manifest
    assert "cost_model" in manifest
    assert "time_splits" in manifest
    assert "outputs" in manifest
