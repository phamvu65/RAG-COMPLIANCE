import pm4py
import pandas as pd
import os

RAW_XES     = 'data/raw/BPI_2017.xes'
RAW_PARQUET = 'data/raw/BPI_2017_raw.parquet'

def ingest_once(xes_path: str = RAW_XES,
                parquet_path: str = RAW_PARQUET) -> pd.DataFrame:
    """
    Đọc XES một lần duy nhất → lưu Parquet.
    Lần sau load thẳng từ Parquet, không đọc XES nữa.
    """
    if os.path.exists(parquet_path):
        print(f"Parquet đã tồn tại — load từ cache: {parquet_path}")
        df = pd.read_parquet(parquet_path)
        print(f"Load xong: {len(df)} event, {len(df.columns)} cột")
        return df

    print(f"Lần đầu — đọc XES (có thể mất vài phút)...")
    log = pm4py.read_xes(xes_path)
    df  = pm4py.convert_to_dataframe(log)

    os.makedirs(os.path.dirname(parquet_path), exist_ok=True)
    df.to_parquet(parquet_path, index=False)
    print(f"Đã lưu Parquet: {parquet_path}")
    print(f"Đọc xong: {len(df)} event, {len(df.columns)} cột")
    return df