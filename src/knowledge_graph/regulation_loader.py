import json
import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()


class RegulationLoader:
    """
    Load Regulation Graph từ file JSON vào Neo4j.
    Tách riêng khỏi notebook để có thể tái sử dụng
    và rebuild khi quy định thay đổi.

    Schema sau khi load:
      Nodes : Activity (13), Condition (4), Role (3), Document (1)
      Edges : MUST_PRECEDE (6), REQUIRES (4+), PERFORMED_BY (13),
              DEFINED_IN (17)
    """

    def __init__(self, env_path: str = None):
        if env_path:
            load_dotenv(env_path)

        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USERNAME"),
                  os.getenv("NEO4J_PASSWORD"))
        )

    def clear_regulation(self):
        """
        Xoá toàn bộ Regulation graph.
        Giữ nguyên Trace graph (Case, ApplicationEvent,
        WorkflowEvent, OfferEvent).
        """
        with self.driver.session() as session:
            session.run("""
                MATCH (n)
                WHERE n:Activity OR n:Document
                   OR n:Condition OR n:Role
                DETACH DELETE n
            """)
        print("Đã xoá Regulation graph cũ")

    def load_from_json(self, json_path: str):
        """Load toàn bộ Regulation graph từ file JSON."""
        with open(json_path, 'r', encoding='utf-8') as f:
            reg = json.load(f)

        print(f"Đọc file: {json_path}")
        print(f"  Activities : {len(reg['activities'])}")
        print(f"  Sequences  : {len(reg['sequences'])}")
        print(f"  Conditions : {len(reg['conditions'])}")
        print(f"  Roles      : {len(reg['roles'])}")

        self._create_document(reg['document'])
        self._create_activities(reg['activities'],
                                reg['document']['id'])
        self._create_sequences(reg['sequences'])
        self._create_conditions(reg['conditions'],
                                reg['document']['id'])
        self._create_roles(reg['roles'])

        print("Load Regulation graph hoàn thành")

    def _create_document(self, doc: dict):
        with self.driver.session() as session:
            session.run("""
                MERGE (d:Document {id: $id})
                SET d.title          = $title,
                    d.issuer         = $issuer,
                    d.effective_date = $effective_date,
                    d.jurisdiction   = $jurisdiction
            """, **doc)
        print(f"  Document: {doc['title']}")

    def _create_activities(self, activities: list,
                            doc_id: str):
        with self.driver.session() as session:
            for act in activities:
                session.run("""
                    MERGE (a:Activity {id: $id})
                    SET a.name         = $name,
                        a.description  = $description,
                        a.event_origin = $event_origin,
                        a.article_ref  = $article_ref,
                        a.required     = $required
                    WITH a
                    MATCH (d:Document {id: $doc_id})
                    MERGE (a)-[:DEFINED_IN]->(d)
                """, doc_id=doc_id, **act)
        print(f"  {len(activities)} Activity nodes")

    def _create_sequences(self, sequences: list):
        with self.driver.session() as session:
            for seq in sequences:
                session.run("""
                    MATCH (a1:Activity {id: $from_id})
                    MATCH (a2:Activity {id: $to_id})
                    MERGE (a1)-[r:MUST_PRECEDE]->(a2)
                    SET r.article_ref = $article_ref,
                        r.description = $description,
                        r.sequence_id = $id
                """, from_id=seq['from'],
                     to_id=seq['to'],
                     **{k: v for k, v in seq.items()
                        if k not in ['from', 'to']})
        print(f"  {len(sequences)} MUST_PRECEDE edges")

    def _create_conditions(self, conditions: list,
                            doc_id: str):
        """
        Tạo Condition nodes, DEFINED_IN edges (→ Document),
        và REQUIRES edges (Activity → Condition).
        """
        requires_count = 0
        with self.driver.session() as session:
            for cond in conditions:
                # Tạo Condition node + DEFINED_IN → Document
                session.run("""
                    MERGE (c:Condition {id: $id})
                    SET c.description    = $description,
                        c.expression     = $expression,
                        c.article_ref    = $article_ref,
                        c.violation_type = $violation_type
                    WITH c
                    MATCH (d:Document {id: $doc_id})
                    MERGE (c)-[:DEFINED_IN]->(d)
                """, doc_id=doc_id,
                     **{k: v for k, v in cond.items()
                        if k not in ['threshold', 'unit',
                                     'linked_activities']})

                # Tạo REQUIRES edges (Activity → Condition)
                linked = cond.get('linked_activities', [])
                for act_id in linked:
                    result = session.run("""
                        MATCH (a:Activity {id: $act_id})
                        MATCH (c:Condition {id: $cond_id})
                        MERGE (a)-[:REQUIRES]->(c)
                    """, act_id=act_id,
                         cond_id=cond['id'])
                    requires_count += 1

        print(f"  {len(conditions)} Condition nodes")
        if requires_count > 0:
            print(f"  {requires_count} REQUIRES edges")

    def _create_roles(self, roles: list):
        with self.driver.session() as session:
            for role in roles:
                session.run("""
                    MERGE (r:Role {id: $id})
                    SET r.name = $name,
                        r.type = $type
                """, id=role['id'],
                     name=role['name'],
                     type=role['type'])

                for act_id in role['performs']:
                    session.run("""
                        MATCH (a:Activity {id: $act_id})
                        MATCH (r:Role {id: $role_id})
                        MERGE (a)-[:PERFORMED_BY]->(r)
                    """, act_id=act_id,
                         role_id=role['id'])
        print(f"  {len(roles)} Role nodes")

    def verify(self) -> dict:
        """Kiểm tra số lượng node và edge sau khi load."""
        with self.driver.session() as session:
            counts = {}
            queries = {
                'activities'  : "MATCH (n:Activity) RETURN count(n) AS c",
                'must_precede': "MATCH ()-[r:MUST_PRECEDE]->() RETURN count(r) AS c",
                'conditions'  : "MATCH (n:Condition) RETURN count(n) AS c",
                'requires'    : "MATCH ()-[r:REQUIRES]->() RETURN count(r) AS c",
                'roles'       : "MATCH (n:Role) RETURN count(n) AS c",
                'performed_by': "MATCH ()-[r:PERFORMED_BY]->() RETURN count(r) AS c",
                'documents'   : "MATCH (n:Document) RETURN count(n) AS c",
                'defined_in'  : "MATCH ()-[r:DEFINED_IN]->() RETURN count(r) AS c",
            }
            for key, query in queries.items():
                counts[key] = session.run(query).single()['c']

        print(f"Regulation loaded: "
              f"{counts['activities']} activities, "
              f"{counts['must_precede']} sequences, "
              f"{counts['conditions']} conditions, "
              f"{counts['requires']} requires, "
              f"{counts['roles']} roles")
        return counts

    def close(self):
        self.driver.close()