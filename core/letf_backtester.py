"""大盤正二（00631L）擇時策略與跨市場濾網回測引擎。（待實作，見 specs/spec_core_strategies.md）"""
import pandas as pd


def backtest_00631l(price: pd.DataFrame, us: pd.DataFrame | None = None) -> pd.DataFrame:
    """回傳 00631L 擇時策略績效表。"""
    raise NotImplementedError
