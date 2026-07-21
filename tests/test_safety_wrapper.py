import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_freqtrade_wrapper_rejects_trade_before_binary_lookup() -> None:
    result = subprocess.run(
        [str(ROOT / "scripts" / "freqtrade.sh"), "trade"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 3
    assert "拒绝" in result.stdout
