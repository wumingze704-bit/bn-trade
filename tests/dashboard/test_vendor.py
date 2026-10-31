"""第三方前端库本地化校验测试。

规格：docs/10-可视化看板.md §3、§12
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_VENDOR = Path(__file__).resolve().parents[2] / "src" / "trade" / "dashboard" / "static" / "vendor"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_vendor_versions_sha256_match() -> None:
    versions = _VENDOR / "VERSIONS.txt"
    assert versions.is_file()
    entries = 0
    for line in versions.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        filename, version, expected = line.split()
        assert version, f"VERSIONS.txt 缺少版本：{line}"
        assert _sha256(_VENDOR / filename) == expected, f"{filename} 的 SHA256 不一致"
        entries += 1
    assert entries == 3


def test_vendor_files_exist() -> None:
    for name in (
        "alpinejs.min.js",
        "plotly.min.js",
        "lightweight-charts.standalone.production.js",
    ):
        assert (_VENDOR / name).is_file(), f"缺少本地化资源 {name}"
