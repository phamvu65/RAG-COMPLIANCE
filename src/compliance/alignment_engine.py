import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from typing import List, Dict, Optional
import json

from .trace_fetcher      import TraceFetcher
from .regulation_fetcher import RegulationFetcher
from .checkers import (
    check_skip_violations,
    check_order_violations,
    check_temporal_violations,
    check_incomplete_handling_violation,
    check_offer_sending_delay,
)
from .violation_report import ViolationReport

load_dotenv()


class AlignmentEngine:
    """
    Orchestrate toàn bộ quá trình compliance checking:
    1. Lấy trace từ Neo4j
    2. Lấy regulation từ Neo4j
    3. Chạy 4 checker
    4. Trả về ViolationReport
    """

    def __init__(self, env_path: str = None):
        if env_path:
            load_dotenv(env_path)

        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USERNAME"),
                  os.getenv("NEO4J_PASSWORD"))
        )
        self.trace_fetcher  = TraceFetcher(self.driver)
        self.reg_fetcher    = RegulationFetcher(self.driver)

        # Load regulation một lần — cache lại
        self.regulation     = self.reg_fetcher.get_regulation()
        print(f"Regulation loaded: "
              f"{len(self.regulation['activities'])} activities, "
              f"{len(self.regulation['sequences'])} sequences, "
              f"{len(self.regulation['conditions'])} conditions")

    def check_case(self, case_id: str) -> ViolationReport:
        """Kiểm tra tuân thủ 1 case — 5 checker."""
        trace = self.trace_fetcher.get_trace(case_id)

        report = ViolationReport(
            case_id          = case_id,
            application_type = trace['application_type'],
            loan_goal        = trace['loan_goal'],
            requested_amount = trace['requested_amount'],
            num_events       = trace['num_events'],
        )

        report.violations.extend(
            check_skip_violations(trace, self.regulation)
        )
        report.violations.extend(
            check_order_violations(trace, self.regulation)
        )
        report.violations.extend(
            check_temporal_violations(trace, self.regulation)
        )
        report.violations.extend(
            check_incomplete_handling_violation(trace, self.regulation)
        )
        report.violations.extend(
            check_offer_sending_delay(trace, self.regulation)
        )

        return report

    def check_all(self,
                  limit: int = None,
                  save_path: str = None) -> List[ViolationReport]:
        """
        Kiểm tra toàn bộ case trong Neo4j.
        Tuỳ chọn lưu kết quả ra JSON.
        """
        case_ids = self.trace_fetcher.get_all_case_ids(limit=limit)
        total    = len(case_ids)
        reports  = []
        errors   = []

        print(f"Bắt đầu kiểm tra {total} case...")

        for i, case_id in enumerate(case_ids):
            try:
                report = self.check_case(case_id)
                reports.append(report)
            except Exception as e:
                errors.append({'case_id': case_id, 'error': str(e)})

            if (i + 1) % 1000 == 0 or (i + 1) == total:
                compliant = sum(1 for r in reports if r.is_compliant)
                print(f"  {i+1}/{total} — "
                      f"tuân thủ: {compliant}, "
                      f"vi phạm: {len(reports)-compliant}, "
                      f"lỗi: {len(errors)}")

        # Lưu kết quả nếu có save_path
        if save_path and reports:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(
                    [r.to_dict() for r in reports],
                    f, ensure_ascii=False, indent=2
                )
            print(f"Đã lưu {len(reports)} report: {save_path}")

        if errors:
            print(f"Có {len(errors)} case lỗi")

        return reports

    def close(self):
        self.driver.close()