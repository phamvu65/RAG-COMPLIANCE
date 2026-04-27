import os
from typing import List, Dict
from neo4j import Driver
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()


class GraphRAGRetriever:
    """
    Hybrid retriever kết hợp 2 bước:
    1. Vector search  — tìm Activity node gần nhất
                        theo ngữ nghĩa với violation query
    2. Graph traversal — duyệt k-hop từ entry node
                         để lấy context đầy đủ
    """

    EMBEDDING_MODEL = (
        'sentence-transformers/'
        'paraphrase-multilingual-MiniLM-L12-v2'
    )

    def __init__(self, driver: Driver, k_hop: int = 2):
        self.driver = driver
        self.k_hop  = k_hop
        print("Loading embedding model...")
        self.embedder = SentenceTransformer(self.EMBEDDING_MODEL)
        print("Embedding model loaded")

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def retrieve_for_violation(self,
                                violation: Dict) -> Dict:
        """
        Truy xuất context đầy đủ cho 1 vi phạm.

        Input:  violation dict từ ViolationReport.to_dict()
        Output: {
            query, entry_nodes, subgraphs, regulation_text
        }
        """
        query       = self._build_query(violation)
        entry_nodes = self._vector_search(query, top_k=3)
        subgraphs   = self._collect_subgraphs(
            entry_nodes, violation
        )
        reg_text    = self._build_regulation_text(
            violation['article_ref'], subgraphs
        )

        return {
            'query'           : query,
            'entry_nodes'     : entry_nodes,
            'subgraphs'       : subgraphs,
            'regulation_text' : reg_text,
        }

    # ----------------------------------------------------------
    # Query building
    # ----------------------------------------------------------

    def _build_query(self, violation: Dict) -> str:
        """
        Tạo query từ thông tin vi phạm.
        Dùng tên activity trực tiếp làm query chính
        thay vì mô tả chung chung — tăng độ chính xác
        của exact match và vector search.
        """
        vtype    = violation['type']
        article  = violation['article_ref']
        evidence = violation.get('evidence', {})

        if vtype == 'skip':
            missing = evidence.get('missing_activity', '')
            return missing  # ví dụ: "A_Complete"

        if vtype == 'order':
            from_act = evidence.get('from_activity', '')
            to_act   = evidence.get('to_activity', '')
            return f"{from_act} {to_act}"

        if vtype == 'temporal':
            return f"{article} time limit deadline"

        return f"{article}"

    # ----------------------------------------------------------
    # Vector search
    # ----------------------------------------------------------

    def _embed(self, text: str) -> List[float]:
        return self.embedder.encode(text).tolist()

    def _vector_search(self, query: str,
                       top_k: int = 3) -> List[Dict]:
        """
        Tìm Activity node gần nhất với query.
        Thử vector index trước, fallback về text search.
        """
        try:
            nodes = self._vector_index_search(query, top_k)
            if nodes:
                return nodes
        except Exception:
            pass

        return self._text_search(query, top_k)

    def _vector_index_search(self, query: str,
                              top_k: int) -> List[Dict]:
        """
        Tìm exact name match trước,
        sau đó mới dùng vector similarity.
        """
        with self.driver.session() as session:
            # Exact match theo tên activity
            exact = session.run("""
                MATCH (a:Activity)
                WHERE a.name = $query
                   OR toLower(a.name) = toLower($query)
                RETURN
                    a.id          AS id,
                    a.name        AS name,
                    a.description AS description,
                    a.article_ref AS article_ref,
                    1.0           AS similarity
                LIMIT 1
            """, query=query)
            exact_nodes = [dict(r) for r in exact]
            if exact_nodes:
                return exact_nodes

            # Vector similarity fallback
            query_vec = self._embed(query)
            result = session.run("""
                CALL db.index.vector.queryNodes(
                    'activity_embedding',
                    $top_k,
                    $query_vec
                ) YIELD node, score
                RETURN
                    node.id          AS id,
                    node.name        AS name,
                    node.description AS description,
                    node.article_ref AS article_ref,
                    score            AS similarity
                ORDER BY score DESC
            """, top_k=top_k, query_vec=query_vec)
            return [dict(r) for r in result]

    def _text_search(self, query: str,
                     top_k: int) -> List[Dict]:
        """Text search fallback — tìm theo tên và article."""
        keywords = query.lower().split()
        with self.driver.session() as session:
            result = session.run("""
                MATCH (a:Activity)
                WHERE ANY(kw IN $keywords WHERE
                    toLower(a.name)        CONTAINS kw OR
                    toLower(a.description) CONTAINS kw OR
                    toLower(a.article_ref) CONTAINS kw
                )
                RETURN
                    a.id          AS id,
                    a.name        AS name,
                    a.description AS description,
                    a.article_ref AS article_ref,
                    1.0           AS similarity
                LIMIT $top_k
            """, keywords=keywords, top_k=top_k)
            return [dict(r) for r in result]

    # ----------------------------------------------------------
    # Graph traversal
    # ----------------------------------------------------------

    def _collect_subgraphs(self,
                            entry_nodes : List[Dict],
                            violation   : Dict) -> List[Dict]:
        """
        Thu thập subgraph từ entry nodes.
        Bổ sung thêm node từ evidence nếu vector search
        không tìm được.
        """
        subgraphs = []
        seen      = set()

        for node in entry_nodes:
            name = node.get('name', '')
            if name and name not in seen:
                seen.add(name)
                sg = self._traverse(name)
                if sg:
                    subgraphs.append(sg)

        if not subgraphs:
            evidence = violation.get('evidence', {})
            for key in ['missing_activity',
                        'from_activity', 'to_activity']:
                name = evidence.get(key, '')
                if name and name not in seen:
                    seen.add(name)
                    sg = self._traverse(name)
                    if sg:
                        subgraphs.append(sg)

        return subgraphs

    def _traverse(self, activity_name: str) -> Dict:
        """
        K-hop traversal từ 1 Activity node.
        Lấy: predecessors, successors, conditions,
              roles, document.
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (a:Activity {name: $name})

                OPTIONAL MATCH (pre:Activity)
                    -[:MUST_PRECEDE]->(a)

                OPTIONAL MATCH (a)
                    -[:MUST_PRECEDE]->(suc:Activity)

                OPTIONAL MATCH (a)-[:REQUIRES]->(c:Condition)

                OPTIONAL MATCH (a)-[:PERFORMED_BY]->(role:Role)

                OPTIONAL MATCH (a)-[:DEFINED_IN]->(doc:Document)

                RETURN
                    a.name         AS activity,
                    a.description  AS description,
                    a.article_ref  AS article_ref,
                    a.required     AS required,
                    collect(DISTINCT pre.name)
                                   AS predecessors,
                    collect(DISTINCT suc.name)
                                   AS successors,
                    collect(DISTINCT {
                        expression:     c.expression,
                        article_ref:    c.article_ref,
                        violation_type: c.violation_type
                    })             AS conditions,
                    collect(DISTINCT role.name)
                                   AS roles,
                    doc.title      AS document_title,
                    doc.id         AS document_id
            """, name=activity_name)

            record = result.single()
            if not record:
                return {}
            return dict(record)

    # ----------------------------------------------------------
    # Context building
    # ----------------------------------------------------------

    def _build_regulation_text(self,
                                article_ref : str,
                                subgraphs   : List[Dict]) -> str:
        """
        Tổng hợp subgraph thành text có cấu trúc
        để đưa vào LLM prompt.
        """
        lines = [
            f"REGULATORY CONTEXT ({article_ref}):",
            "=" * 50
        ]

        for sg in subgraphs:
            if not sg:
                continue

            lines.append(
                f"\nActivity    : {sg.get('activity', '')}"
            )
            lines.append(
                f"Description : {sg.get('description', '')}"
            )
            lines.append(
                f"Legal basis : {sg.get('article_ref', '')}"
            )
            lines.append(
                f"Required    : {sg.get('required', False)}"
            )
            lines.append(
                f"Document    : {sg.get('document_title', '')}"
            )

            preds = [p for p in sg.get('predecessors', [])
                     if p]
            if preds:
                lines.append(
                    f"Preceded by : {', '.join(preds)}"
                )

            succs = [s for s in sg.get('successors', [])
                     if s]
            if succs:
                lines.append(
                    f"Followed by : {', '.join(succs)}"
                )

            for cond in sg.get('conditions', []):
                if cond and cond.get('expression'):
                    lines.append(
                        f"Condition   : {cond['expression']} "
                        f"({cond.get('article_ref', '')})"
                    )

            roles = [r for r in sg.get('roles', []) if r]
            if roles:
                lines.append(
                    f"Performed by: {', '.join(roles)}"
                )

        return '\n'.join(lines)