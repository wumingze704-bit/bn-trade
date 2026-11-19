"""山寨币截面动量轮动 + 大盘状态过滤策略（回测与实盘共用打分与调仓代码）。

规格：docs/11-轮动策略.md
"""

from trade.rotation.config import RotationConfig, load_rotation_config

__all__ = ["RotationConfig", "load_rotation_config"]
