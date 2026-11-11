"""回测结果导出脚本测试（06 §8）。"""

from __future__ import annotations

from pathlib import Path

from scripts.export_results import build_markdown, main, parse_run_logs


def test_parse_run_logs_解析θ与失败(tmp_path: Path) -> None:
    (tmp_path / "tune_models_run3.log").write_text(
        "t4_3b_L30_h64_focal: PF=1.261337 net=551.998 n=389 θ=0.34\n"
        "t4_3b_L30_h32_bce: PF=None net=None n=None θ=None 失败=数据集缺文件\n",
        encoding="utf-8",
    )
    info = parse_run_logs(tmp_path)
    assert info["t4_3b_L30_h64_focal"]["theta"] == 0.34
    assert info["t4_3b_L30_h64_focal"]["failed"] is False
    assert info["t4_3b_L30_h32_bce"]["theta"] is None
    assert info["t4_3b_L30_h32_bce"]["failed"] is True


def test_build_markdown_含表格与最优() -> None:
    rows = [
        {
            "run_id": "t4_3b_L30_h32_bce",
            "profit_factor": 0.89,
            "net_return_usdt": -1218.0,
            "n_trades": 1350,
        },
        {
            "run_id": "t4_3b_L30_h64_focal",
            "profit_factor": 1.26,
            "net_return_usdt": 551.9,
            "n_trades": 389,
        },
    ]
    theta = {
        "t4_3b_L30_h32_bce": {"theta": 0.40, "failed": False},
        "t4_3b_L30_h64_focal": {"theta": 0.34, "failed": False},
    }
    md = build_markdown(rows, theta)
    assert "| `t4_3b_L30_h64_focal` |" in md
    assert "当前最优：`t4_3b_L30_h64_focal`" in md
    assert "已登记 2 / 12 组。" in md


def test_main_写出文件(tmp_path: Path) -> None:
    out = tmp_path / "回测结果.md"
    assert main(["--out", str(out)]) == 0
    assert out.is_file()
    assert "回测结果记录" in out.read_text(encoding="utf-8")
