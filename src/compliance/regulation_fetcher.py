from neo4j import Driver
from typing import Dict, List


class RegulationFetcher:
    """Lấy regulation graph từ Neo4j."""

    def __init__(self, driver: Driver):
        self.driver = driver
        self._cache = None  # Cache vì regulation không thay đổi

    def get_regulation(self) -> Dict:
        """
        Lấy toàn bộ regulation graph.
        Cache lại sau lần đầu — không query lại mỗi lần check.
        """
        if self._cache is not None:
            return self._cache

        with self.driver.session() as session:

            # Lấy tất cả Activity
            act_result = session.run("""
                MATCH (a:Activity)
                RETURN
                    a.id           AS id,
                    a.name         AS name,
                    a.description  AS description,
                    a.event_origin AS event_origin,
                    a.article_ref  AS article_ref,
                    a.required     AS required
                ORDER BY a.id
            """)
            activities = {r['name']: dict(r) for r in act_result}

            # Lấy thứ tự bắt buộc (MUST_PRECEDE)
            seq_result = session.run("""
                MATCH (a1:Activity)-[r:MUST_PRECEDE]->(a2:Activity)
                RETURN
                    a1.name       AS from_activity,
                    a2.name       AS to_activity,
                    r.article_ref AS article_ref,
                    r.description AS description
                ORDER BY a1.id
            """)
            sequences = [dict(r) for r in seq_result]

            # Lấy Conditions
            cond_result = session.run("""
                MATCH (c:Condition)
                RETURN
                    c.id             AS id,
                    c.description    AS description,
                    c.expression     AS expression,
                    c.article_ref    AS article_ref,
                    c.violation_type AS violation_type
            """)
            conditions = [dict(r) for r in cond_result]

            # Tạo danh sách thứ tự bắt buộc dạng list of tuples
            # để dễ iterate trong checker
            required_order = [
                (s['from_activity'], s['to_activity'],
                 s['article_ref'], s['description'])
                for s in sequences
            ]

            # Tập activity bắt buộc (required=True)
            required_activities = {
                name for name, act in activities.items()
                if act.get('required') is True
            }

        self._cache = {
            'activities'          : activities,
            'sequences'           : sequences,
            'conditions'          : conditions,
            'required_order'      : required_order,
            'required_activities' : required_activities,
        }
        return self._cache