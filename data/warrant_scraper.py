"""實盤權證清單取得與標準化：CSV 匯入，或對接公開權證網頁 / API。
標準欄位：code,name,underlying,kind,S,K,ratio,days_left,bid,ask,bid_iv,outstanding_ratio"""
import pandas as pd

REQUIRED = ["code", "name", "underlying", "kind", "S", "K", "ratio", "days_left", "bid", "ask", "bid_iv", "outstanding_ratio"]


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str, "underlying": str})
    missing = set(REQUIRED) - set(df.columns)
    if missing:
        raise ValueError(f"CSV 缺少欄位：{missing}")
    return df


def fetch_from_web(date: str | None = None) -> pd.DataFrame:
    """對接公開權證網頁 / API 的接口（各發行商與證交所網頁格式常變，請依來源實作），
    只要回傳含 REQUIRED 欄位的 DataFrame 即可接上 core.warrant_screener.screen()。"""
    raise NotImplementedError("請實作資料來源，或先以 load_csv() 匯入")
