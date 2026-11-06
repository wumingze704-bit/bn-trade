"""模型产物加载与一致性校验（回测与实盘共用）。

规格：docs/05-模型与训练.md §9；docs/04-特征与标签.md §3（feature_hash）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import torch  # type: ignore[import-untyped]

from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig, exit_policy_hash
from trade.data.universe import UniverseConfig, load_universe_config, rule_hash
from trade.features import enabled_features, feature_hash
from trade.features.registry import FeaturesConfig
from trade.models.config import ModelConfig
from trade.models.train import build_model

_REPO_ROOT = Path(__file__).resolve().parents[3]


class ArtifactError(RuntimeError):
    """模型产物与当前配置不一致时抛出（05 §9）。"""


class Predictor:
    """模型推理接口：`predict(x) -> (p, m)`（05 §1、§9）。

    集成多个种子的 logit 平均后按 `model_meta.temperature` 做温度缩放；
    M0 LightGBM 直接使用概率（再转 logit 校准）。
    """

    def __init__(
        self,
        *,
        meta: dict[str, Any],
        state_dicts: list[dict[str, torch.Tensor]] | None,
        booster: Any | None,
        device: str = "cpu",
    ) -> None:
        self.meta = meta
        self.model_id = str(meta.get("model_id", ""))
        self.threshold = float(meta.get("threshold", 0.5))
        self.temperature = float(meta.get("temperature", 1.0))
        self.device = device
        self.arch = str(meta.get("arch"))
        self.window_L = int(meta.get("window_L", 0))
        self._models: list[torch.nn.Module] = []
        self._booster = booster
        if self.arch in {"tcn", "lstm"}:
            if not state_dicts:
                raise ArtifactError(f"模型 {self.model_id} 缺少 state_dicts（05 §9）")
            params = dict(meta.get("arch_params") or {})
            cfg = ModelConfig(
                **{
                    key: params[key]
                    for key in ("hidden", "blocks", "kernel", "dropout", "window_L")
                    if key in params
                }
            )
            for state_dict in state_dicts:
                model = build_model(self.arch, len(meta.get("features") or []), cfg)
                model.load_state_dict(state_dict)
                model.eval()
                self._models.append(model.to(device))

    def predict(self, x: np.ndarray) -> tuple[float, float]:
        """单样本推理：x 形状 `(L, F)`，返回 (p, m)。"""
        p, m = self.predict_batch(np.asarray(x)[None, ...])
        return float(p[0]), float(m[0])

    def predict_batch(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """批量推理：x 形状 `(B, L, F)`（或单样本 `(L, F)`）。"""
        values = np.asarray(x, dtype="float32")
        if values.ndim == 2:
            values = values[None, ...]
        if self.arch == "lgbm":
            if self._booster is None:
                raise ArtifactError(f"模型 {self.model_id} 缺少 lgbm.txt（05 §9）")
            prob = np.asarray(self._booster.predict(_lgbm_matrix(values)), dtype="float64")
            prob = np.clip(prob, 1e-9, 1.0 - 1e-9)
            logit = np.log(prob / (1.0 - prob))
            p = 1.0 / (1.0 + np.exp(-logit / self.temperature))
            return p, np.zeros(len(p), dtype="float64")
        tensor = torch.from_numpy(values).to(self.device)
        logits: list[np.ndarray] = []
        regs: list[np.ndarray] = []
        with torch.no_grad():
            for model in self._models:
                logit, m = model(tensor)
                logits.append(logit.detach().cpu().numpy())
                regs.append(m.detach().cpu().numpy())
        mean_logit = np.mean(np.stack(logits, axis=0), axis=0)
        p = 1.0 / (1.0 + np.exp(-mean_logit / self.temperature))
        return p, np.mean(np.stack(regs, axis=0), axis=0)


def load(
    model_dir: Path,
    *,
    features_config: FeaturesConfig | None = None,
    exit_config: ExitConfig | None = None,
    universe_config: UniverseConfig | None = None,
    base_config: BaseConfig | None = None,
    device: str = "cpu",
) -> Predictor:
    """加载模型产物并校验哈希（05 §9）。

    依次校验：特征列表与当前注册表一致、`feature_hash` 一致、`exit_policy` 与
    `exits.yaml` 的哈希一致、`label` 关键参数一致、`market` 与 `base.yaml` 一致，
    以及 `universe_rule_hash` 与当前 `universe.yaml` 一致。

    Args:
        model_dir: `artifacts/models/{model_id}`。
        features_config: 当前特征配置；None 时读仓库 `configs/features.yaml`。
        exit_config: 当前出场配置；None 时读 `configs/exits.yaml`。
        universe_config: 当前选币配置；None 时读 `configs/universe.yaml`。
        base_config: 当前基础配置；None 时读 `configs/base.yaml`。
        device: 推理设备（`cpu` / `cuda`）。

    Returns:
        Predictor。

    Raises:
        ArtifactError: 产物缺失或任一哈希/字段不一致，异常信息含文件与期望值。
    """
    meta_path = model_dir / "model_meta.json"
    if not meta_path.is_file():
        raise ArtifactError(f"模型产物缺少 model_meta.json：{meta_path}")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ArtifactError(f"model_meta.json 不是合法 JSON：{meta_path}") from error

    features_cfg = features_config or load_config(
        FeaturesConfig, _REPO_ROOT / "configs" / "features.yaml"
    )
    exits_cfg = exit_config or load_config(ExitConfig, _REPO_ROOT / "configs" / "exits.yaml")
    universe_cfg = universe_config or load_universe_config(_REPO_ROOT / "configs" / "universe.yaml")
    base_cfg = base_config or load_config(BaseConfig, _REPO_ROOT / "configs" / "base.yaml")

    expected_features = [feature.name for feature in enabled_features(features_cfg)]
    actual_features = list(meta.get("features") or [])
    if actual_features != expected_features:
        raise ArtifactError(
            f"特征列表与当前注册表不一致：{meta_path}；"
            f"产物 {len(actual_features)} 个，当前 {len(expected_features)} 个"
        )
    expected_hash = feature_hash(enabled_features(features_cfg))
    if meta.get("feature_hash") != expected_hash:
        raise ArtifactError(
            f"feature_hash 不一致：{meta_path}；产物 {meta.get('feature_hash')}，"
            f"当前 {expected_hash}（features.yaml 已变更，需重训，05 §9）"
        )
    expected_exit = exit_policy_hash(exits_cfg)
    actual_exit = (
        exit_policy_hash(ExitConfig(**meta["exit_policy"])) if meta.get("exit_policy") else None
    )
    if actual_exit != expected_exit:
        raise ArtifactError(
            f"出场策略不一致：{meta_path}；产物 {actual_exit}，当前 {expected_exit}"
            "（exits.yaml 已变更，需重训，05 §9）"
        )
    _validate_label(meta, features_cfg, meta_path)
    if str(meta.get("market")) != base_cfg.market:
        raise ArtifactError(
            f"market 不一致：{meta_path}；产物 {meta.get('market')}，当前 {base_cfg.market}"
        )
    if meta.get("universe_rule_hash") != rule_hash(universe_cfg):
        raise ArtifactError(
            f"universe_rule_hash 不一致：{meta_path}；产物 {meta.get('universe_rule_hash')}，"
            f"当前 {rule_hash(universe_cfg)}（universe.yaml 已变更，需重训）"
        )

    if meta.get("arch") in {"tcn", "lstm"}:
        torch_path = model_dir / "model.pt"
        if not torch_path.is_file():
            raise ArtifactError(f"模型产物缺少 model.pt：{torch_path}")
        payload = torch.load(torch_path, map_location="cpu", weights_only=False)
        state_dicts = list(payload["state_dicts"])
        return Predictor(meta=meta, state_dicts=state_dicts, booster=None, device=device)
    if meta.get("arch") == "lgbm":
        lgbm_path = model_dir / "lgbm.txt"
        if not lgbm_path.is_file():
            raise ArtifactError(f"模型产物缺少 lgbm.txt：{lgbm_path}")
        import lightgbm as lgb  # type: ignore[import-untyped]

        return Predictor(
            meta=meta,
            state_dicts=None,
            booster=lgb.Booster(model_str=lgbm_path.read_text(encoding="utf-8")),
            device=device,
        )
    raise ArtifactError(f"未知模型类型 arch={meta.get('arch')}：{meta_path}")


def _lgbm_matrix(values: np.ndarray) -> np.ndarray:
    """把窗口 `(B, L, F)` 转成 M0 表格化特征 `(B, 4F)`（05 §2）。

    M0 使用最后一根特征及其与 t-1、t-5、t-15 的差分；加载器必须与训练时
    `tabular_features_matrix` 完全一致，不能只取最后一根。
    """
    if values.shape[1] < 16:
        raise ArtifactError("M0 需要至少 16 根窗口（t-15 差分，05 §2）")
    last = values[:, -1, :]
    return np.concatenate(
        [
            last,
            last - values[:, -2, :],
            last - values[:, -6, :],
            last - values[:, -16, :],
        ],
        axis=1,
    )


def _validate_label(meta: dict[str, Any], cfg: FeaturesConfig, path: Path) -> None:
    """校验标签关键参数（y_min_r、k_sl、sl_min、sl_max、mode）与 features.yaml 一致。"""
    label = meta.get("label") or {}
    expected = {
        "mode": cfg.label.mode,
        "y_min_r": cfg.label.y_min_r,
        "k_sl": cfg.label.k_sl,
        "sl_min": cfg.label.sl_min,
        "sl_max": cfg.label.sl_max,
        "entry_latency_ms": cfg.label.entry_latency_ms,
    }
    for key, value in expected.items():
        if label.get(key) != value:
            raise ArtifactError(
                f"标签参数 {key} 不一致：{path}；产物 {label.get(key)}，当前 {value}"
                "（features.yaml 已变更，需重训，05 §9）"
            )
