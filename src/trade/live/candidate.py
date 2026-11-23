"""候选冻结文件、总账与上线前置条件。

规格：docs/07-实盘与风控.md §3；docs/13-研究框架.md §8

候选文件 `docs/research/candidates/<候选ID>.md` 的第一行为 `结论：...`；
第二行起可含一个 ```yaml 代码块，字段如下（全部必填，缺一即抛错，红线 12）：

    family: xs_momentum_ls
    exposure: neutral
    rebalance_hours: 24
    risk_per_trade: 0.0075
    framework_dir: src/trade/research
    params: {lookback_days: 14, top_k: 5}
    frozen:
      - path: configs/strategies/round_R1.yaml
        sha256: <64 位十六进制>

引擎只调用 `framework_dir` 指向的同一份研究框架、策略与组合层（红线 4）。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_EXPOSURES = frozenset({"neutral", "directional"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)


class CandidateError(Exception):
    """候选文件、总账或上线前置条件不满足（不应静默回退）。"""


@dataclass(frozen=True)
class FrozenFile:
    """一个冻结文件的相对路径与 SHA256。"""

    path: str
    sha256: str


@dataclass(frozen=True)
class CandidateSpec:
    """候选的冻结参数与运行配置（docs/07 §3）。"""

    candidate_id: str
    family: str
    exposure: str
    rebalance_hours: int
    risk_per_trade: float
    framework_dir: str
    params: dict[str, Any]
    frozen: tuple[FrozenFile, ...]
    document_path: Path


def read_conclusion(path: Path) -> str:
    """读取结果文件第一行 `结论：...` 的结论值。

    Args:
        path: 结果文件（final / shadow / engine）路径。

    Returns:
        第一行冒号后的结论（如 `PASS`、`LIVE_READY`）；文件不存在返回空字符串。

    Raises:
        CandidateError: 文件存在但第一行不是 `结论：...` 格式。
    """
    if not path.is_file():
        return ""
    first = path.read_text(encoding="utf-8").splitlines()[0].strip()
    if not first.startswith("结论："):
        raise CandidateError(f"结果文件第一行不是“结论：……”：{path}：{first[:40]}")
    return first[len("结论：") :].strip()


def read_ledger(path: Path) -> dict[str, Any]:
    """读取 `docs/research/ledger.yaml`（只读）。

    Raises:
        CandidateError: 文件不存在或不是 YAML 映射。
    """
    if not path.is_file():
        raise CandidateError(f"总账不存在：{path}（先运行研究循环）")
    try:
        values = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise CandidateError(f"总账不是有效 YAML：{path}：{error}") from error
    if not isinstance(values, dict):
        raise CandidateError(f"总账必须是 YAML 映射：{path}")
    return values


def ledger_candidate(ledger: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    """从总账取出某候选条目。

    Raises:
        CandidateError: 总账没有该候选。
    """
    items = ledger.get("candidates", [])
    if not isinstance(items, list):
        raise CandidateError("总账 candidates 必须是列表")
    for item in items:
        if isinstance(item, dict) and item.get("id") == candidate_id:
            return item
    raise CandidateError(f"总账没有候选 {candidate_id}")


def parse_candidate_document(path: Path, candidate_id: str) -> CandidateSpec:
    """解析候选 Markdown 中的冻结参数。

    Args:
        path: `docs/research/candidates/<候选ID>.md`。
        candidate_id: 候选 ID（与总账一致）。

    Returns:
        CandidateSpec。

    Raises:
        CandidateError: 文件不存在、缺少 yaml 代码块或字段缺失 / 非法。
    """
    if not path.is_file():
        raise CandidateError(f"候选文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    match = _FENCE_RE.search(text)
    if match is None:
        raise CandidateError(f"候选文件缺少 ```yaml 代码块：{path}")
    try:
        values = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise CandidateError(f"候选文件的 yaml 代码块无法解析：{path}：{error}") from error
    if not isinstance(values, dict):
        raise CandidateError(f"候选文件的 yaml 代码块必须是映射：{path}")
    required = (
        "family",
        "exposure",
        "rebalance_hours",
        "risk_per_trade",
        "framework_dir",
        "params",
    )
    missing = [key for key in required if key not in values]
    if missing:
        raise CandidateError(f"候选 {candidate_id} 缺少字段 {missing}（{path}）")
    exposure = str(values["exposure"])
    if exposure not in _EXPOSURES:
        raise CandidateError(f"候选 {candidate_id} 的 exposure 非法：{exposure}")
    params = values["params"]
    if not isinstance(params, dict):
        raise CandidateError(f"候选 {candidate_id} 的 params 必须是映射")
    rebalance_hours = int(values["rebalance_hours"])
    if rebalance_hours < 1 or 24 % rebalance_hours != 0:
        raise CandidateError(
            f"候选 {candidate_id} 的 rebalance_hours 必须是 24 的因数：{rebalance_hours}"
        )
    risk_per_trade = float(values["risk_per_trade"])
    if not 0.0 < risk_per_trade <= 0.05:
        raise CandidateError(f"候选 {candidate_id} 的 risk_per_trade 非法：{risk_per_trade}")
    frozen_raw = values.get("frozen", [])
    if not isinstance(frozen_raw, list) or not frozen_raw:
        raise CandidateError(f"候选 {candidate_id} 缺少冻结文件清单（frozen）")
    frozen: list[FrozenFile] = []
    for item in frozen_raw:
        if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
            raise CandidateError(f"候选 {candidate_id} 的 frozen 条目非法：{item}")
        digest = str(item["sha256"]).lower()
        if _SHA256_RE.match(digest) is None:
            raise CandidateError(f"候选 {candidate_id} 的 SHA256 非法：{digest}")
        frozen.append(FrozenFile(path=str(item["path"]), sha256=digest))
    return CandidateSpec(
        candidate_id=candidate_id,
        family=str(values["family"]),
        exposure=exposure,
        rebalance_hours=rebalance_hours,
        risk_per_trade=risk_per_trade,
        framework_dir=str(values["framework_dir"]),
        params=dict(params),
        frozen=tuple(frozen),
        document_path=path,
    )


def load_candidate(
    *, root: Path, candidates_dir: Path, ledger_path: Path, candidate_id: str
) -> tuple[CandidateSpec, dict[str, Any]]:
    """读取总账条目与候选冻结文件。

    Args:
        root: 仓库根目录（解析冻结文件的相对路径）。
        candidates_dir: `docs/research/candidates`。
        ledger_path: `docs/research/ledger.yaml`。
        candidate_id: 候选 ID。

    Returns:
        (CandidateSpec, 总账中的候选条目)。

    Raises:
        CandidateError: 候选不存在或文件非法。
    """
    ledger = read_ledger(ledger_path)
    entry = ledger_candidate(ledger, candidate_id)
    spec = parse_candidate_document(candidates_dir / f"{candidate_id}.md", candidate_id)
    return spec, entry


def sha256_file(path: Path) -> str:
    """计算文件 SHA256（十六进制小写）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen(spec: CandidateSpec, root: Path) -> None:
    """逐个核对冻结文件 SHA256；任一不一致立即抛错（红线 2、任务底线 2）。

    Args:
        spec: 候选冻结参数。
        root: 仓库根目录。

    Raises:
        CandidateError: 文件缺失或 SHA256 不一致。
    """
    mismatched: list[str] = []
    for item in spec.frozen:
        path = root / item.path
        if not path.is_file():
            raise CandidateError(f"候选 {spec.candidate_id} 的冻结文件缺失：{path}")
        actual = sha256_file(path)
        if actual != item.sha256:
            mismatched.append(f"{item.path}（记录 {item.sha256[:12]}…，实际 {actual[:12]}…）")
    if mismatched:
        raise CandidateError(f"候选 {spec.candidate_id} 的冻结文件 SHA256 不一致：{mismatched}")


def live_precondition_errors(
    *,
    spec: CandidateSpec,
    entry: dict[str, Any],
    final_dir: Path,
    shadow_dir: Path,
    root: Path,
) -> list[str]:
    """检查 live 模式的全部前置条件（docs/07 §5），返回错误列表。

    live 启动要求同时满足：总账 final=pass 且 ops 为 live_ready / live；
    `final/<候选ID>_<窗口ID>.md` 第一行为 `结论：PASS`；
    `shadow/<候选ID>.md` 第一行为 `结论：LIVE_READY`；冻结文件 SHA256 一致。

    Args:
        spec: 候选冻结参数。
        entry: 总账中的候选条目。
        final_dir: `docs/research/final`。
        shadow_dir: `docs/research/shadow`。
        root: 仓库根目录。

    Returns:
        错误文案列表；空列表表示通过。
    """
    errors: list[str] = []
    if entry.get("final") != "pass":
        errors.append(f"总账 final={entry.get('final')}，不是 pass")
    if entry.get("ops") not in {"live_ready", "live"}:
        errors.append(f"总账 ops={entry.get('ops')}，不是 live_ready / live")
    window = entry.get("final_window")
    if not window:
        errors.append("总账缺少 final_window，无法核对最终检验结果")
    else:
        final_path = final_dir / f"{spec.candidate_id}_{window}.md"
        conclusion = read_conclusion(final_path)
        if conclusion != "PASS":
            errors.append(f"{final_path.name} 结论为 {conclusion or '缺失'}，不是 PASS")
    shadow_conclusion = read_conclusion(shadow_dir / f"{spec.candidate_id}.md")
    if shadow_conclusion != "LIVE_READY":
        errors.append(
            f"shadow/{spec.candidate_id}.md 结论为 {shadow_conclusion or '缺失'}，不是 LIVE_READY"
        )
    try:
        verify_frozen(spec, root)
    except CandidateError as error:
        errors.append(str(error))
    return errors


def run_mode_errors(
    *,
    mode: str,
    i_understand_the_risk: bool,
    spec: CandidateSpec,
    entry: dict[str, Any],
    final_dir: Path,
    shadow_dir: Path,
    root: Path,
    env: Mapping[str, str],
) -> list[str]:
    """启动前的模式校验（docs/07 §5.1、§5.4），返回错误列表。

    - live：`--i-understand-the-risk`、正式网密钥、最终检验 PASS、影子盘 LIVE_READY、
      冻结 SHA256 一致（缺一即退出）；
    - paper：需要 demo 密钥（API Key + Secret）；
    - shadow：只需要公开行情，不要求密钥。

    Args:
        mode: `shadow` / `paper` / `live`。
        i_understand_the_risk: 命令行是否带确认参数。
        spec: 候选冻结参数。
        entry: 总账中的候选条目。
        final_dir: `docs/research/final`。
        shadow_dir: `docs/research/shadow`。
        root: 仓库根目录。
        env: 环境变量映射（测试注入；生产传 `os.environ`）。

    Returns:
        错误文案列表；空表示可以启动。
    """
    errors: list[str] = []
    if mode not in {"shadow", "paper", "live"}:
        return [f"未知运行模式：{mode}"]
    if mode == "live":
        if not i_understand_the_risk:
            errors.append("live 模式必须带 --i-understand-the-risk")
        if not env.get("BINANCE_API_KEY"):
            errors.append("缺少环境变量 BINANCE_API_KEY")
        if not env.get("BINANCE_API_SECRET") and not env.get("BINANCE_PRIVATE_KEY_PATH"):
            errors.append("缺少 BINANCE_API_SECRET 或 BINANCE_PRIVATE_KEY_PATH")
        errors.extend(
            live_precondition_errors(
                spec=spec, entry=entry, final_dir=final_dir, shadow_dir=shadow_dir, root=root
            )
        )
    elif mode == "paper":
        if not env.get("BINANCE_DEMO_API_KEY") or not env.get("BINANCE_DEMO_API_SECRET"):
            errors.append("paper 模式缺少 BINANCE_DEMO_API_KEY / BINANCE_DEMO_API_SECRET")
    return errors
