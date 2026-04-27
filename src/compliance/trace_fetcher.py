from neo4j import Driver
from typing import Dict, List


class TraceFetcher:
    """Lấy trace từ Neo4j về Python để alignment engine xử lý."""

    def __init__(self, driver: Driver):
        self.driver = driver

    def get_trace(self, case_id: str) -> Dict:
        """
        Lấy toàn bộ thông tin 1 case:
        - Case metadata
        - ApplicationEvent theo thứ tự
        - WorkflowEvent theo thứ tự
        - OfferEvent theo thứ tự
        - Toàn bộ event hợp nhất theo thứ tự thời gian
        """
        with self.driver.session() as session:
            # Lấy Case metadata
            case_result = session.run("""
                MATCH (c:Case {case_id: $case_id})
                RETURN c
            """, case_id=case_id)
            case_record = case_result.single()
            if not case_record:
                raise ValueError(f"Không tìm thấy case: {case_id}")
            case_data = dict(case_record['c'])

            # Lấy toàn bộ event theo seq_index
            events_result = session.run("""
                MATCH (c:Case {case_id: $case_id})
                -[:HAS_APP_EVENT|HAS_WF_EVENT|HAS_OFFER_EVENT]->(e)
                RETURN
                    labels(e)[0]   AS node_type,
                    e.event_id     AS event_id,
                    e.activity_name AS activity,
                    e.lifecycle    AS lifecycle,
                    e.resource     AS resource,
                    e.timestamp    AS timestamp,
                    e.seq_index    AS seq_index,
                    e.duration_next AS duration_next,
                    e.is_terminal  AS is_terminal,
                    e.offer_id     AS offer_id,
                    e.offered_amount AS offered_amount,
                    e.credit_score AS credit_score,
                    e.accepted     AS accepted
                ORDER BY e.seq_index
            """, case_id=case_id)

            events = [dict(r) for r in events_result]

            # Tách riêng theo origin để checker dễ dùng
            app_events = [
                e for e in events
                if e['node_type'] == 'ApplicationEvent'
            ]
            wf_events = [
                e for e in events
                if e['node_type'] == 'WorkflowEvent'
            ]
            offer_events = [
                e for e in events
                if e['node_type'] == 'OfferEvent'
            ]

        return {
            'case_id'          : case_id,
            'application_type' : case_data.get('application_type', ''),
            'loan_goal'        : case_data.get('loan_goal', ''),
            'requested_amount' : case_data.get('requested_amount', 0),
            'num_events'       : len(events),
            'all_events'       : events,
            'app_events'       : app_events,
            'wf_events'        : wf_events,
            'offer_events'     : offer_events,
            # Set tên activity để lookup nhanh O(1)
            'activity_set'     : {e['activity'] for e in events},
            'app_activity_set' : {e['activity'] for e in app_events},
        }

    def get_all_case_ids(self, limit: int = None) -> List[str]:
        """Lấy danh sách tất cả case_id."""
        query = "MATCH (c:Case) RETURN c.case_id AS case_id"
        if limit:
            query += f" LIMIT {limit}"
        with self.driver.session() as session:
            result = session.run(query)
            return [r['case_id'] for r in result]