import pm4py
import pandas as pd
import os


LIFECYCLE_KEEP = ['complete', 'ate_abort', 'withdraw']


def load_raw(filepath: str) -> pd.DataFrame:
    """Đọc XES, trả về DataFrame thô."""
    log = pm4py.read_xes(filepath)
    df  = pm4py.convert_to_dataframe(log)
    print(f"Đọc xong: {len(df)} event, {len(df.columns)} cột")
    return df


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Chuẩn hoá tên cột, giữ toàn bộ dữ liệu."""
    rename_map = {
        'case:concept:name'     : 'case_id',
        'concept:name'          : 'activity',
        'time:timestamp'        : 'timestamp',
        'org:resource'          : 'resource',
        'lifecycle:transition'  : 'lifecycle',
        'EventID'               : 'event_id',
        'EventOrigin'           : 'event_origin',
        'Action'                : 'action',
        'case:LoanGoal'         : 'loan_goal',
        'case:ApplicationType'  : 'application_type',
        'case:RequestedAmount'  : 'requested_amount',
        'OfferID'               : 'offer_id',
        'OfferedAmount'         : 'offered_amount',
        'FirstWithdrawalAmount' : 'first_withdrawal_amount',
        'NumberOfTerms'         : 'number_of_terms',
        'MonthlyCost'           : 'monthly_cost',
        'CreditScore'           : 'credit_score',
        'Accepted'              : 'accepted',
        'Selected'              : 'selected',
    }
    actual_rename = {k: v for k, v in rename_map.items()
                     if k in df.columns}
    df = df.rename(columns=actual_rename)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    return df


def filter_lifecycle(df: pd.DataFrame,
                     keep: list = None) -> pd.DataFrame:
    """
    Với BPI 2017, giữ toàn bộ 7 lifecycle vì mỗi trạng thái
    mang thông tin compliance riêng biệt:

      Application origin:
        complete  → trạng thái hồ sơ thay đổi (A_Submitted, A_Accepted...)

      Workflow origin:
        schedule  → task được hệ thống giao cho nhân viên
        start     → nhân viên bắt đầu thực hiện
        suspend   → tạm dừng (chờ tài liệu, chờ khách...)
        resume    → tiếp tục sau suspend
        ate_abort → kết thúc task (hành vi bình thường của BPMS)

      Kết thúc đặc biệt:
        withdraw  → hồ sơ/offer bị rút lui chủ động

    Chỉ lọc nếu người dùng truyền danh sách keep cụ thể.
    """
    if keep is None:
        # Giữ toàn bộ — không lọc gì
        print(f"Giữ toàn bộ lifecycle — không lọc")
        print(df['lifecycle'].value_counts().to_string())
        return df

    before = len(df)
    df = df[df['lifecycle'].str.lower().isin(keep)].copy()
    after  = len(df)
    print(f"Lọc lifecycle {keep}:")
    print(f"  Giữ {after}/{before} event ({after/before*100:.1f}%)")
    print(df['lifecycle'].value_counts().to_string())
    return df


def analyze_trace_length(df: pd.DataFrame,
                          percentile_low:  float = 5,
                          percentile_high: float = 95) -> tuple:
    """
    Phân tích phân phối độ dài trace và đề xuất ngưỡng lọc.
    Dùng percentile thay vì min/max cứng để xử lý outlier.
    """
    case_lengths = df.groupby('case_id').size()

    p_low  = int(case_lengths.quantile(percentile_low  / 100))
    p_high = int(case_lengths.quantile(percentile_high / 100))

    print(f"Phân phối độ dài trace ({len(case_lengths)} case):")
    print(f"  min    = {case_lengths.min()}")
    print(f"  p5     = {p_low}   ← đề xuất min_events")
    print(f"  p25    = {int(case_lengths.quantile(0.25))}")
    print(f"  median = {int(case_lengths.median())}")
    print(f"  mean   = {case_lengths.mean():.1f}")
    print(f"  p75    = {int(case_lengths.quantile(0.75))}")
    print(f"  p95    = {p_high}  ← đề xuất max_events")
    print(f"  max    = {case_lengths.max()}")
    print(f"\nĐề xuất: filter_complete_cases("
          f"min_events={p_low}, max_events={p_high})")
    return p_low, p_high


def filter_complete_cases(df: pd.DataFrame,
                           min_events: int = None,
                           max_events: int = None) -> pd.DataFrame:
    """
    Lọc case theo độ dài trace.
    Nếu không truyền min/max, tự tính từ p5/p95.
    """
    case_lengths = df.groupby('case_id').size()

    if min_events is None:
        min_events = int(case_lengths.quantile(0.05))
    if max_events is None:
        max_events = int(case_lengths.quantile(0.95))

    valid_cases = case_lengths[
        (case_lengths >= min_events) &
        (case_lengths <= max_events)
    ].index

    filtered = df[df['case_id'].isin(valid_cases)].copy()
    removed  = df['case_id'].nunique() - filtered['case_id'].nunique()

    print(f"Lọc trace [{min_events}, {max_events}] event:")
    print(f"  Giữ: {filtered['case_id'].nunique()} case")
    print(f"  Loại: {removed} case outlier")
    return filtered


def remove_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Loại event trùng hoàn toàn."""
    before = len(df)
    df = df.drop_duplicates(
        subset=['case_id', 'activity', 'timestamp', 'lifecycle']
    ).copy()
    removed = before - len(df)
    print(f"Loại duplicate: {removed} event bị loại")
    return df


def add_trace_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Thêm các feature cần thiết cho graph construction:
      seq_index     : thứ tự event trong trace (0-based)
      duration_next : thời gian đến event tiếp theo (giây)
      is_terminal   : event có phải bước cuối của trace không
    """
    df = df.sort_values(['case_id', 'timestamp']).copy()

    # Sequence index trong từng case
    df['seq_index'] = df.groupby('case_id').cumcount()

    # Duration đến event tiếp theo
    df['timestamp_next'] = df.groupby('case_id')['timestamp'].shift(-1)
    df['duration_next']  = (
        df['timestamp_next'] - df['timestamp']
    ).dt.total_seconds().fillna(0).astype(int)
    df = df.drop(columns=['timestamp_next'])

    # Đánh dấu event cuối của trace
    case_max_idx = df.groupby('case_id')['seq_index'].transform('max')
    df['is_terminal'] = (df['seq_index'] == case_max_idx)

    print(f"Đã thêm seq_index, duration_next, is_terminal")
    return df


def validate_traces(df: pd.DataFrame) -> pd.DataFrame:
    """
    Kiểm tra và báo cáo chất lượng trace.
    Không loại bỏ gì thêm — chỉ in thống kê để người dùng quyết định.
    """
    case_stats = df.groupby('case_id').agg(
        num_events    =('activity', 'count'),
        num_origins   =('event_origin', 'nunique'),
        has_complete  =('lifecycle', lambda x: 'complete' in x.values),
        has_abort     =('lifecycle', lambda x: 'ate_abort' in x.values),
        has_withdraw  =('lifecycle', lambda x: 'withdraw' in x.values),
    )

    print(f"Thống kê chất lượng trace:")
    print(f"  Tổng case: {len(case_stats)}")
    print(f"  Độ dài trace: min={case_stats.num_events.min()}, "
          f"max={case_stats.num_events.max()}, "
          f"mean={case_stats.num_events.mean():.1f}")
    print(f"  Case có event complete:  "
          f"{case_stats.has_complete.sum()} "
          f"({case_stats.has_complete.mean()*100:.1f}%)")
    print(f"  Case có ate_abort:       "
          f"{case_stats.has_abort.sum()} "
          f"({case_stats.has_abort.mean()*100:.1f}%)")
    print(f"  Case có withdraw:        "
          f"{case_stats.has_withdraw.sum()} "
          f"({case_stats.has_withdraw.mean()*100:.1f}%)")
    print(f"  Case có đủ 3 origin:     "
          f"{(case_stats.num_origins == 3).sum()} "
          f"({(case_stats.num_origins == 3).mean()*100:.1f}%)")
    return df


def save_processed(df: pd.DataFrame, output_path: str) -> None:
    """Lưu dataset đã xử lý ra cả CSV và Parquet."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Lưu Parquet (đọc nhanh, giữ kiểu dữ liệu)
    parquet_path = output_path.replace('.csv', '.parquet')
    df.to_parquet(parquet_path, index=False)

    # Lưu CSV (dễ xem, dễ chia sẻ)
    df.to_csv(output_path, index=False)

    print(f"Đã lưu:")
    print(f"  {parquet_path}")
    print(f"  {output_path}")
    print(f"  ({len(df)} event, {df['case_id'].nunique()} case, "
          f"{len(df.columns)} cột)")