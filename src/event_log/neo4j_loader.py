import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from typing import List, Dict
from src.event_log.trace_builder import build_traces, trace_to_cypher_queries

load_dotenv()


class Neo4jLoader:

    def __init__(self, env_path: str = None):
        if env_path:
            load_dotenv(env_path)

        uri      = os.getenv("NEO4J_URI")
        username = os.getenv("NEO4J_USERNAME")
        password = os.getenv("NEO4J_PASSWORD")

        self.driver = GraphDatabase.driver(
            uri, auth=(username, password)
        )
        print(f"Kết nối Neo4j: {uri}")

    def create_indexes(self):
        """Tạo index trước khi load data — tăng tốc MERGE và MATCH."""
        indexes = [
            "CREATE INDEX case_id IF NOT EXISTS "
            "FOR (c:Case) ON (c.case_id)",

            "CREATE INDEX app_event_id IF NOT EXISTS "
            "FOR (e:ApplicationEvent) ON (e.event_id)",

            "CREATE INDEX wf_event_id IF NOT EXISTS "
            "FOR (e:WorkflowEvent) ON (e.event_id)",

            "CREATE INDEX offer_event_id IF NOT EXISTS "
            "FOR (e:OfferEvent) ON (e.event_id)",

            "CREATE INDEX app_event_case IF NOT EXISTS "
            "FOR (e:ApplicationEvent) ON (e.case_id)",

            "CREATE INDEX wf_event_case IF NOT EXISTS "
            "FOR (e:WorkflowEvent) ON (e.case_id)",

            "CREATE INDEX offer_event_case IF NOT EXISTS "
            "FOR (e:OfferEvent) ON (e.case_id)",
        ]
        with self.driver.session() as session:
            for idx in indexes:
                session.run(idx)
        print(f"Đã tạo {len(indexes)} index")

    def clear_trace_data(self):
        """Xoá toàn bộ trace data cũ — giữ lại Regulation graph."""
        with self.driver.session() as session:
            session.run("""
                MATCH (n)
                WHERE n:Case OR n:ApplicationEvent
                   OR n:WorkflowEvent OR n:OfferEvent
                   OR n:Resource
                DETACH DELETE n
            """)
        print("Đã xoá trace data cũ")

    def load_traces(self,
                    traces: List[Dict],
                    batch_size: int = 10,
                    limit: int = None) -> Dict:
        """
        Load traces vào Neo4j.

        Args:
            traces:     danh sách trace từ build_traces()
            batch_size: in log mỗi N trace
            limit:      giới hạn số trace load (None = tất cả)

        Returns:
            dict thống kê kết quả
        """
        if limit:
            traces = traces[:limit]

        total   = len(traces)
        success = 0
        failed  = 0
        errors  = []

        print(f"Bắt đầu load {total} trace...")

        for i, trace in enumerate(traces):
            try:
                queries = trace_to_cypher_queries(trace)
                with self.driver.session() as session:
                    for query in queries:
                        session.run(query)
                success += 1

            except Exception as e:
                failed += 1
                errors.append({
                    'case_id': trace['case_id'],
                    'error'  : str(e)
                })
                if failed <= 5:  # chỉ in 5 lỗi đầu
                    print(f"  Lỗi [{trace['case_id']}]: {str(e)[:100]}")

            if (i + 1) % batch_size == 0 or (i + 1) == total:
                print(f"  {i+1}/{total} trace "
                      f"(OK={success}, lỗi={failed})")

        stats = {
            'total'  : total,
            'success': success,
            'failed' : failed,
            'errors' : errors,
        }
        print(f"\nHoàn thành: {success}/{total} trace loaded")
        if failed > 0:
            print(f"Có {failed} trace lỗi — xem stats['errors'] để debug")
        return stats

    def verify(self) -> Dict:
        """Kiểm tra số lượng node và edge trong Neo4j."""
        with self.driver.session() as session:
            counts = {}
            for label in ['Case', 'ApplicationEvent',
                          'WorkflowEvent', 'OfferEvent']:
                r = session.run(
                    f"MATCH (n:{label}) RETURN count(n) AS cnt"
                )
                counts[label] = r.single()['cnt']

            r = session.run(
                "MATCH ()-[r:FOLLOWED_BY]->() RETURN count(r) AS cnt"
            )
            counts['FOLLOWED_BY'] = r.single()['cnt']

            r = session.run(
                "MATCH ()-[r:HAS_APP_EVENT]->() RETURN count(r) AS cnt"
            )
            counts['HAS_APP_EVENT'] = r.single()['cnt']

        print("Thống kê Neo4j:")
        for k, v in counts.items():
            print(f"  {k:<20}: {v:>8,}")
        return counts

    def close(self):
        self.driver.close()
        print("Đã đóng kết nối Neo4j")