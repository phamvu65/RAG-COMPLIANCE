from typing import List, Dict
from datetime import datetime, timezone
from .violation_report import Violation, ViolationType, Severity


def _parse_ts(ts_str: str) -> datetime:
    """Parse timestamp string về datetime có timezone."""
    if not ts_str:
        return None
    try:
        ts = str(ts_str).replace(' ', 'T')
        if ts.endswith('+00:00'):
            return datetime.fromisoformat(ts)
        return datetime.fromisoformat(ts)\
                       .replace(tzinfo=timezone.utc)
    except Exception:
        return None


def check_skip_violations(trace: Dict,
                           regulation: Dict) -> List[Violation]:
    """
    Kiểm tra bước bắt buộc có bị bỏ qua không.
    So sánh required_activities với activity_set của trace.
    """
    violations = []
    activity_set = trace['activity_set']

    for req_act in regulation['required_activities']:
        if req_act not in activity_set:
            # Tìm article_ref của activity này
            act_info = regulation['activities'].get(req_act, {})
            art_ref  = act_info.get('article_ref', 'Unknown')

            violations.append(Violation(
                violation_id   = f"SKIP-{req_act.replace(' ', '_')}",
                violation_type = ViolationType.SKIP,
                severity       = Severity.HIGH,
                article_ref    = art_ref,
                description    = f"Bước bắt buộc '{req_act}' bị bỏ qua",
                expected       = f"Activity '{req_act}' phải xuất hiện "
                                 f"trong quy trình ({art_ref})",
                actual         = f"Không tìm thấy '{req_act}' "
                                 f"trong {len(activity_set)} event của trace",
                evidence       = {
                    'missing_activity': req_act,
                    'present_activities': sorted(list(
                        trace['app_activity_set']
                    )),
                },
                suggestion     = f"Đảm bảo thực hiện '{req_act}' "
                                 f"theo yêu cầu của {art_ref}",
            ))

    return violations


def check_order_violations(trace: Dict,
                            regulation: Dict) -> List[Violation]:
    """
    Kiểm tra thứ tự thực hiện các bước.
    Dùng seq_index để so sánh thứ tự thực tế.
    """
    violations = []

    # Tạo map: activity_name → seq_index đầu tiên xuất hiện
    # Dùng ApplicationEvent và OfferEvent (không dùng Workflow
    # vì Workflow có nhiều lifecycle trung gian)
    first_occurrence = {}
    for event in trace['all_events']:
        act = event['activity']
        if act not in first_occurrence:
            first_occurrence[act] = event['seq_index']

    for from_act, to_act, art_ref, desc in regulation['required_order']:
        # Bỏ qua nếu 1 trong 2 activity không có trong trace
        if from_act not in first_occurrence:
            continue
        if to_act not in first_occurrence:
            continue

        idx_from = first_occurrence[from_act]
        idx_to   = first_occurrence[to_act]

        if idx_from >= idx_to:
            violations.append(Violation(
                violation_id   = (f"ORDER-"
                                  f"{from_act.replace(' ', '_')}-"
                                  f"{to_act.replace(' ', '_')}"),
                violation_type = ViolationType.ORDER,
                severity       = Severity.HIGH,
                article_ref    = art_ref,
                description    = (f"Sai thứ tự: '{to_act}' xảy ra "
                                  f"trước '{from_act}'"),
                expected       = (f"'{from_act}' (seq={idx_from}) phải "
                                  f"xảy ra TRƯỚC '{to_act}' ({art_ref})"),
                actual         = (f"'{from_act}' ở seq={idx_from}, "
                                  f"'{to_act}' ở seq={idx_to} "
                                  f"→ thứ tự bị đảo ngược"),
                evidence       = {
                    'from_activity'    : from_act,
                    'from_seq_index'   : idx_from,
                    'to_activity'      : to_act,
                    'to_seq_index'     : idx_to,
                    'required_order'   : f"{from_act} → {to_act}",
                },
                suggestion     = (f"Đảm bảo '{from_act}' được thực hiện "
                                  f"trước '{to_act}' theo {art_ref}"),
            ))

    return violations


def check_temporal_violations(trace: Dict,
                               regulation: Dict) -> List[Violation]:
    """
    Kiểm tra vi phạm thời hạn.
    Hiện tại kiểm tra: Article 14 — right of withdrawal 14 ngày.
    """
    violations = []
    all_events  = trace['all_events']

    # --- COND-001: Right of withdrawal <= 14 ngày ---
    sent_events     = [e for e in all_events
                       if e['activity'] == 'O_Sent (mail and online)'
                       or e['activity'] == 'O_Sent (online only)']
    returned_events = [e for e in all_events
                       if e['activity'] == 'O_Returned']

    if sent_events and returned_events:
        # Lấy timestamp gửi offer sớm nhất
        ts_sent = _parse_ts(sent_events[0]['timestamp'])
        # Lấy timestamp trả lại muộn nhất
        ts_returned = _parse_ts(returned_events[-1]['timestamp'])

        if ts_sent and ts_returned:
            days_diff = (ts_returned - ts_sent).days

            if days_diff > 14:
                violations.append(Violation(
                    violation_id   = "TEMP-WITHDRAWAL-PERIOD",
                    violation_type = ViolationType.TEMPORAL,
                    severity       = Severity.MEDIUM,
                    article_ref    = "Article 14(1)",
                    description    = (f"Rút lui sau {days_diff} ngày, "
                                      f"vượt quá thời hạn 14 ngày"),
                    expected       = ("O_Returned phải xảy ra trong vòng "
                                      "14 ngày kể từ O_Sent (Article 14(1))"),
                    actual         = (f"O_Sent lúc {sent_events[0]['timestamp']}"
                                      f", O_Returned lúc "
                                      f"{returned_events[-1]['timestamp']}"
                                      f" → {days_diff} ngày"),
                    evidence       = {
                        'o_sent_timestamp'    : str(
                            sent_events[0]['timestamp']),
                        'o_returned_timestamp': str(
                            returned_events[-1]['timestamp']),
                        'days_difference'     : days_diff,
                        'allowed_days'        : 14,
                    },
                    suggestion     = ("Kiểm tra lại thời hạn rút lui. "
                                      "Consumer chỉ có 14 ngày kể từ khi "
                                      "nhận thông tin hợp đồng."),
                ))

    return violations

def check_incomplete_handling_violation(
        trace: Dict,
        regulation: Dict) -> List[Violation]:
    """
    Kiểm tra thời gian xử lý hồ sơ không đầy đủ.
    Ngưỡng: 8 ngày (p90 từ BPI 2017 dataset).
    Cơ sở pháp lý: Article 7(1) — creditor phải xử lý kịp thời.
    """
    violations = []
    all_events  = trace['all_events']
    threshold   = 8  # ngày — p90 của dataset

    incomplete_events = [
        e for e in all_events
        if e['activity'] == 'A_Incomplete'
        and e['lifecycle'] == 'complete'
    ]
    validating_events = [
        e for e in all_events
        if e['activity'] == 'A_Validating'
        and e['lifecycle'] == 'complete'
    ]

    if not incomplete_events or not validating_events:
        return violations

    ts_incomplete = _parse_ts(incomplete_events[0]['timestamp'])
    ts_validating = _parse_ts(validating_events[-1]['timestamp'])

    if not ts_incomplete or not ts_validating:
        return violations

    days_diff = (ts_validating - ts_incomplete).days

    if days_diff > threshold:
        violations.append(Violation(
            violation_id   = "TEMP-INCOMPLETE-HANDLING",
            violation_type = ViolationType.TEMPORAL,
            severity       = Severity.MEDIUM,
            article_ref    = "Article 7(1)",
            description    = (f"Xử lý hồ sơ thiếu quá {days_diff} ngày, "
                              f"vượt ngưỡng {threshold} ngày"),
            expected       = (f"A_Incomplete → A_Validating trong vòng "
                              f"{threshold} ngày (p90 dataset, Article 7(1))"),
            actual         = (f"A_Incomplete lúc {incomplete_events[0]['timestamp']}"
                              f" → A_Validating lúc "
                              f"{validating_events[-1]['timestamp']}"
                              f" = {days_diff} ngày"),
            evidence       = {
                'a_incomplete_timestamp': str(
                    incomplete_events[0]['timestamp']),
                'a_validating_timestamp': str(
                    validating_events[-1]['timestamp']),
                'days_difference'       : days_diff,
                'threshold_days'        : threshold,
                'threshold_basis'       : 'p90 of BPI Challenge 2017 dataset',
            },
            suggestion     = (f"Xử lý hồ sơ thiếu trong vòng "
                              f"{threshold} ngày kể từ khi phát hiện "
                              f"thiếu tài liệu."),
        ))

    return violations


def check_offer_sending_delay(
        trace: Dict,
        regulation: Dict) -> List[Violation]:
    """
    Kiểm tra thời gian từ khi tạo offer đến khi gửi cho khách.
    Article 5(1) yêu cầu thông tin phải được cung cấp kịp thời.
    Ngưỡng: 1 ngày — tạo offer phải gửi ngay trong ngày.
    """
    violations = []
    all_events  = trace['all_events']
    threshold   = 1  # ngày

    created_events = [
        e for e in all_events
        if e['activity'] == 'O_Created'
        and e['lifecycle'] == 'complete'
    ]
    sent_events = [
        e for e in all_events
        if e['activity'] in [
            'O_Sent (mail and online)',
            'O_Sent (online only)'
        ]
        and e['lifecycle'] == 'complete'
    ]

    if not created_events or not sent_events:
        return violations

    ts_created = _parse_ts(created_events[0]['timestamp'])
    ts_sent    = _parse_ts(sent_events[0]['timestamp'])

    if not ts_created or not ts_sent:
        return violations

    days_diff = (ts_sent - ts_created).days

    if days_diff > threshold:
        violations.append(Violation(
            violation_id   = "TEMP-OFFER-SENDING-DELAY",
            violation_type = ViolationType.TEMPORAL,
            severity       = Severity.MEDIUM,
            article_ref    = "Article 5(1)",
            description    = (f"Offer tạo xong nhưng gửi sau "
                              f"{days_diff} ngày, vượt ngưỡng "
                              f"{threshold} ngày"),
            expected       = (f"O_Created → O_Sent trong vòng "
                              f"{threshold} ngày (Article 5(1))"),
            actual         = (f"O_Created lúc {created_events[0]['timestamp']}"
                              f" → O_Sent lúc {sent_events[0]['timestamp']}"
                              f" = {days_diff} ngày"),
            evidence       = {
                'o_created_timestamp': str(
                    created_events[0]['timestamp']),
                'o_sent_timestamp'   : str(
                    sent_events[0]['timestamp']),
                'days_difference'    : days_diff,
                'threshold_days'     : threshold,
            },
            suggestion     = ("Gửi thông tin offer cho khách hàng "
                              "ngay trong ngày tạo offer theo "
                              "Article 5(1)."),
        ))

    return violations