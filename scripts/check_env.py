"""检查本地 Python、PyTorch、CUDA 与数据盘环境。

规格：docs/02-系统架构与部署.md §5.5；docs/08-开发计划与验收.md §2 P0
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from pydantic import ValidationError

from trade.common.config import BaseConfig, ConfigError, load_config


def _existing_parent(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _torch_status() -> dict[str, object]:
    try:
        import torch
    except ImportError:
        return {"installed": False, "cuda_available": False, "gpu": None, "gpu_memory_gib": None}

    cuda_available = torch.cuda.is_available()
    gpu_name: str | None = None
    gpu_memory_gib: float | None = None
    if cuda_available:
        properties = torch.cuda.get_device_properties(0)
        gpu_name = properties.name
        gpu_memory_gib = round(properties.total_memory / (1024**3), 2)
    return {
        "installed": True,
        "version": torch.__version__,
        "cuda_available": cuda_available,
        "gpu": gpu_name,
        "gpu_memory_gib": gpu_memory_gib,
    }


def main() -> int:
    """读取配置并输出环境检查结果。"""
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="检查 trade 开发环境")
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root / "configs" / "base.yaml",
        help="基础配置文件路径",
    )
    arguments = parser.parse_args()
    config_path = arguments.config
    if not config_path.is_absolute():
        config_path = project_root / config_path

    try:
        config = load_config(BaseConfig, config_path)
    except (ConfigError, ValidationError) as error:
        print(f"配置检查失败：{error}", file=sys.stderr)
        return 1

    data_path = config.paths.data_root
    if not data_path.is_absolute():
        data_path = project_root / data_path
    disk = shutil.disk_usage(_existing_parent(data_path))
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    result = {
        "python": python_version,
        "python_supported": (3, 11) <= sys.version_info[:2] < (3, 14),
        "pytorch": _torch_status(),
        "disk_free_gib": round(disk.free / (1024**3), 2),
        "data_path": str(data_path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["python_supported"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
