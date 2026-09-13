import os
import json
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv

from src.compliance.alignment_engine import AlignmentEngine
from src.graph_rag.retriever         import GraphRAGRetriever
from src.graph_rag.explainer         import LLMExplainer

load_dotenv()


@dataclass
class ExplainedViolation:
    """Vi phạm kèm context Graph RAG và giải thích LLM."""
    violation   : Dict
    context     : Dict
    explanation : str

    def to_dict(self) -> Dict:
        return {
            'violation'  : self.violation,
            'context'    : {
                'query'           : self.context.get('query'),
                'entry_nodes'     : self.context.get(
                    'entry_nodes'
                ),
                'regulation_text' : self.context.get(
                    'regulation_text'
                ),
            },
            'explanation': self.explanation,
        }


@dataclass
class ComplianceReport:
    """
    Báo cáo tuân thủ cuối cùng — tích hợp đầy đủ:
    - Kết quả alignment (rule-based)
    - Context Graph RAG
    - Giải thích LLM
    """
    case_id              : str
    application_type     : str
    loan_goal            : str
    requested_amount     : float
    num_events           : int
    is_compliant         : bool
    fitness_score        : float
    violation_summary    : Dict
    explained_violations : List[ExplainedViolation] = \
        field(default_factory=list)

    @property
    def num_violations(self) -> int:
        return len(self.explained_violations)

    def to_dict(self) -> Dict:
        return {
            'case_id'          : self.case_id,
            'application_type' : self.application_type,
            'loan_goal'        : self.loan_goal,
            'requested_amount' : self.requested_amount,
            'num_events'       : self.num_events,
            'is_compliant'     : self.is_compliant,
            'fitness_score'    : self.fitness_score,
            'violation_summary': self.violation_summary,
            'violations'       : [
                ev.to_dict()
                for ev in self.explained_violations
            ],
        }

    def print_report(self):
        """In báo cáo đẹp ra console."""
        sep = "=" * 60
        print(f"\n{sep}")
        print(f"COMPLIANCE REPORT")
        print(f"{sep}")
        print(f"Mã hồ sơ     : {self.case_id}")
        print(f"Loại hồ sơ   : {self.application_type}")
        print(f"Mục đích vay : {self.loan_goal}")
        print(
            f"Số tiền vay  : "
            f"{self.requested_amount:,.0f} EUR"
        )
        print(f"Số sự kiện   : {self.num_events}")
        print(f"Tuân thủ     : "
              f"{'Có' if self.is_compliant else 'Không'}")
        print(f"Fitness score: {self.fitness_score:.2f}")

        if not self.explained_violations:
            print(f"\nKhông phát hiện vi phạm.")
            return

        print(f"\nVi phạm ({self.num_violations}):")
        print("-" * 60)

        for i, ev in enumerate(
                self.explained_violations, 1):
            v = ev.violation
            print(
                f"\n[{i}] {v['severity'].upper()} — "
                f"{v['type'].upper()}"
            )
            print(f"     Điều khoản : {v['article_ref']}")
            print(f"     Mô tả      : {v['description']}")

            print(f"\n     Graph RAG context:")
            print(f"     Query: {ev.context.get('query', '')}")
            nodes = ev.context.get('entry_nodes', [])
            if nodes:
                node_names = [
                    n.get('name', '') for n in nodes
                ]
                print(
                    f"     Nodes retrieved: "
                    f"{', '.join(node_names)}"
                )

            print(f"\n     Giải thích (LLM):")
            for line in ev.explanation.split('\n'):
                if line.strip():
                    print(f"     {line}")

        print(f"\n{sep}")


class ComplianceReportGenerator:
    """
    Orchestrate toàn bộ Graph RAG pipeline:

    ViolationReport (alignment engine)
        ↓
    GraphRAGRetriever (vector search + graph traversal)
        ↓
    LLMExplainer (Gemini)
        ↓
    ComplianceReport (kết quả cuối)
    """

    def __init__(self,
                 engine    : AlignmentEngine,
                 retriever : GraphRAGRetriever,
                 explainer : LLMExplainer):
        self.engine    = engine
        self.retriever = retriever
        self.explainer = explainer

    def generate(self, case_id: str) -> ComplianceReport:
        """
        Sinh ComplianceReport đầy đủ cho 1 case.

        Bước 1: AlignmentEngine → ViolationReport
        Bước 2: GraphRAGRetriever → context cho mỗi vi phạm
        Bước 3: LLMExplainer → giải thích tự nhiên
        Bước 4: Tổng hợp → ComplianceReport
        """
        # Bước 1: Rule-based compliance check
        vreport  = self.engine.check_case(case_id)
        vdict    = vreport.to_dict()
        case_info = {
            'case_id'         : vdict['case_id'],
            'application_type': vdict['application_type'],
            'loan_goal'       : vdict['loan_goal'],
            'requested_amount': vdict['requested_amount'],
            'num_events'      : vdict['num_events'],
        }

        # Bước 2 + 3: Graph RAG + LLM cho từng vi phạm
        explained = []
        for violation in vdict['violations']:

            # Graph RAG retrieval
            context = self.retriever.retrieve_for_violation(
                violation
            )

            # LLM explanation
            explanation = self.explainer.explain_violation(
                case_info, violation, context
            )

            explained.append(ExplainedViolation(
                violation   = violation,
                context     = context,
                explanation = explanation,
            ))

        # Bước 4: Tổng hợp
        return ComplianceReport(
            case_id              = vdict['case_id'],
            application_type     = vdict['application_type'],
            loan_goal            = vdict['loan_goal'],
            requested_amount     = vdict['requested_amount'],
            num_events           = vdict['num_events'],
            is_compliant         = vdict['is_compliant'],
            fitness_score        = vdict['fitness_score'],
            violation_summary    = vdict['violation_summary'],
            explained_violations = explained,
        )

    def generate_batch(
            self,
            case_ids  : List[str],
            save_path : Optional[str] = None,
            verbose   : bool = True
    ) -> List[ComplianceReport]:
        """
        Sinh report cho nhiều case.
        Lưu JSON nếu có save_path.
        """
        reports = []
        total   = len(case_ids)
        errors  = []

        for i, case_id in enumerate(case_ids):
            try:
                report = self.generate(case_id)
                reports.append(report)

                if verbose and (i + 1) % 5 == 0:
                    print(f"  {i+1}/{total} generated")

            except Exception as e:
                errors.append({
                    'case_id': case_id,
                    'error'  : str(e)
                })
                if verbose:
                    print(f"  Lỗi [{case_id}]: "
                          f"{str(e)[:80]}")

        if save_path and reports:
            os.makedirs(
                os.path.dirname(save_path),
                exist_ok=True
            )
            with open(save_path, 'w',
                      encoding='utf-8') as f:
                json.dump(
                    [r.to_dict() for r in reports],
                    f, ensure_ascii=False, indent=2
                )
            print(
                f"Đã lưu {len(reports)} report: "
                f"{save_path}"
            )

        if errors:
            print(f"Có {len(errors)} lỗi")

        return reports