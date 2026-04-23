import pandas as pd
from typing import List, Dict, Any


# Map event_origin → Neo4j label và relationship từ Case
ORIGIN_CONFIG = {
    'Application': {
        'label'   : 'ApplicationEvent',
        'rel'     : 'HAS_APP_EVENT',
    },
    'Workflow': {
        'label'   : 'WorkflowEvent',
        'rel'     : 'HAS_WF_EVENT',
    },
    'Offer': {
        'label'   : 'OfferEvent',
        'rel'     : 'HAS_OFFER_EVENT',
    },
}

# Cột thuộc về offer — chỉ có giá trị với OfferEvent
OFFER_COLS = [
    'offer_id', 'offered_amount', 'first_withdrawal_amount',
    'number_of_terms', 'monthly_cost', 'credit_score',
    'accepted', 'selected',
]


def _safe_str(value: Any) -> str:
    """Chuyển giá trị thành string an toàn cho Cypher."""
    if pd.isna(value):
        return ''
    return str(value).replace("'", "\\'").replace('"', '\\"')


def _safe_num(value: Any, default: float = 0.0) -> float:
    """Chuyển giá trị thành số an toàn cho Cypher."""
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_traces(df: pd.DataFrame) -> List[Dict]:
    """
    Chuyển DataFrame đã xử lý thành danh sách trace.
    Mỗi trace là dict chứa thông tin case và danh sách event
    đã được phân loại theo event_origin.
    """
    traces = []
    df = df.sort_values(['case_id', 'seq_index'])

    for case_id, group in df.groupby('case_id'):
        events = group.to_dict('records')

        # Lấy thuộc tính case-level từ dòng đầu tiên
        first = events[0]

        trace = {
            'case_id'          : str(case_id),
            'application_type' : _safe_str(first.get('application_type')),
            'loan_goal'        : _safe_str(first.get('loan_goal')),
            'requested_amount' : _safe_num(first.get('requested_amount')),
            'start_time'       : str(first.get('timestamp')),
            'end_time'         : str(events[-1].get('timestamp')),
            'num_events'       : len(events),
            'events'           : events,
        }
        traces.append(trace)

    print(f"Đã build {len(traces)} trace")
    return traces


def _build_case_query(trace: Dict) -> str:
    """Tạo node Case với đầy đủ thuộc tính."""
    return f"""
        MERGE (c:Case {{case_id: '{trace['case_id']}'}})
        SET c.application_type  = '{trace['application_type']}',
            c.loan_goal         = '{trace['loan_goal']}',
            c.requested_amount  = {trace['requested_amount']},
            c.start_time        = '{trace['start_time']}',
            c.end_time          = '{trace['end_time']}',
            c.num_events        = {trace['num_events']},
            c.status            = 'processed'
    """


def _build_event_query(event: Dict, case_id: str) -> str:
    """
    Tạo node Event đúng loại (ApplicationEvent/WorkflowEvent/OfferEvent)
    và edge HAS_*_EVENT từ Case.
    """
    origin = event.get('event_origin', 'Application')
    config = ORIGIN_CONFIG.get(origin, ORIGIN_CONFIG['Application'])
    label  = config['label']
    rel    = config['rel']

    event_id  = _safe_str(event.get('event_id', ''))
    activity  = _safe_str(event.get('activity', ''))
    timestamp = str(event.get('timestamp', ''))
    resource  = _safe_str(event.get('resource', ''))
    lifecycle = _safe_str(event.get('lifecycle', ''))
    action    = _safe_str(event.get('action', ''))
    seq_index = int(event.get('seq_index', 0))
    is_term   = bool(event.get('is_terminal', False))
    dur_next  = int(_safe_num(event.get('duration_next', 0)))

    # Base properties — tất cả loại event đều có
    base_props = f"""
        event_id:      '{event_id}',
        case_id:       '{case_id}',
        activity_name: '{activity}',
        timestamp:     '{timestamp}',
        resource:      '{resource}',
        lifecycle:     '{lifecycle}',
        action:        '{action}',
        seq_index:     {seq_index},
        is_terminal:   {str(is_term).lower()},
        duration_next: {dur_next}
    """

    # Offer-specific properties
    offer_props = ''
    if label == 'OfferEvent':
        offer_id     = _safe_str(event.get('offer_id', ''))
        offered_amt  = _safe_num(event.get('offered_amount', 0))
        withdrawal   = _safe_num(event.get('first_withdrawal_amount', 0))
        num_terms    = _safe_num(event.get('number_of_terms', 0))
        monthly_cost = _safe_num(event.get('monthly_cost', 0))
        credit_score = _safe_num(event.get('credit_score', 0))

        # Boolean cần xử lý riêng
        accepted_raw = event.get('accepted')
        selected_raw = event.get('selected')
        accepted = 'null' if pd.isna(accepted_raw) \
                   else str(bool(accepted_raw)).lower()
        selected = 'null' if pd.isna(selected_raw) \
                   else str(bool(selected_raw)).lower()

        offer_props = f""",
            offer_id:               '{offer_id}',
            offered_amount:         {offered_amt},
            first_withdrawal_amount:{withdrawal},
            number_of_terms:        {num_terms},
            monthly_cost:           {monthly_cost},
            credit_score:           {credit_score},
            accepted:               {accepted},
            selected:               {selected}
        """

    return f"""
        CREATE (e:{label} {{{base_props}{offer_props}}})
        WITH e
        MATCH (c:Case {{case_id: '{case_id}'}})
        CREATE (c)-[:{rel}]->(e)
    """


def _build_followed_by_queries(events: List[Dict],
                                case_id: str) -> List[str]:
    """
    Tạo edge FOLLOWED_BY giữa các event liên tiếp theo seq_index.
    Edge này nối qua các loại node khác nhau để giữ thứ tự thời gian.
    """
    queries = []
    for i in range(len(events) - 1):
        e1    = events[i]
        e2    = events[i + 1]
        e1_id = _safe_str(e1.get('event_id', ''))
        e2_id = _safe_str(e2.get('event_id', ''))
        dur   = int(_safe_num(e1.get('duration_next', 0)))

        # Xác định label của 2 node để MATCH đúng
        o1 = e1.get('event_origin', 'Application')
        o2 = e2.get('event_origin', 'Application')
        l1 = ORIGIN_CONFIG.get(o1, ORIGIN_CONFIG['Application'])['label']
        l2 = ORIGIN_CONFIG.get(o2, ORIGIN_CONFIG['Application'])['label']

        queries.append(f"""
            MATCH (e1:{l1} {{event_id: '{e1_id}', case_id: '{case_id}'}})
            MATCH (e2:{l2} {{event_id: '{e2_id}', case_id: '{case_id}'}})
            CREATE (e1)-[:FOLLOWED_BY {{
                duration_seconds: {dur},
                from_origin: '{o1}',
                to_origin:   '{o2}'
            }}]->(e2)
        """)
    return queries


def trace_to_cypher_queries(trace: Dict) -> List[str]:
    """
    Sinh toàn bộ Cypher queries để tạo 1 trace trong Neo4j.
    Thứ tự: Case → Events → FOLLOWED_BY edges
    """
    queries  = []
    case_id  = trace['case_id']
    events   = trace['events']

    # 1. Tạo Case node
    queries.append(_build_case_query(trace))

    # 2. Tạo từng Event node (đúng loại) + edge từ Case
    for event in events:
        queries.append(_build_event_query(event, case_id))

    # 3. Tạo FOLLOWED_BY edges
    queries.extend(_build_followed_by_queries(events, case_id))

    return queries