from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class ViolationType(Enum):
    SKIP      = "skip"        # bỏ qua bước bắt buộc
    ORDER     = "order"       # sai thứ tự
    TEMPORAL  = "temporal"    # vi phạm thời hạn
    FREQUENCY = "frequency"   # vi phạm tần suất


class Severity(Enum):
    HIGH   = "high"    # vi phạm bắt buộc của Directive
    MEDIUM = "medium"  # vi phạm khuyến nghị
    LOW    = "low"     # bất thường nhưng không rõ vi phạm


@dataclass
class Violation:
    violation_id   : str
    violation_type : ViolationType
    severity       : Severity
    article_ref    : str
    description    : str
    expected       : str        # quy định phải làm gì
    actual         : str        # thực tế xảy ra gì
    evidence       : dict       # dữ liệu thô làm bằng chứng
    suggestion     : str        # đề xuất khắc phục


@dataclass
class ViolationReport:
    case_id         : str
    application_type: str
    loan_goal       : str
    requested_amount: float
    num_events      : int
    violations      : List[Violation] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        """Tuân thủ khi không có vi phạm HIGH hoặc MEDIUM."""
        return not any(
            v.severity in [Severity.HIGH, Severity.MEDIUM]
            for v in self.violations
        )

    @property
    def fitness_score(self) -> float:
        """
        Điểm tuân thủ từ 0.0 đến 1.0.
        Trừ điểm theo mức độ nghiêm trọng:
          HIGH   → trừ 0.3
          MEDIUM → trừ 0.15
          LOW    → trừ 0.05
        """
        deductions = {
            Severity.HIGH  : 0.30,
            Severity.MEDIUM: 0.15,
            Severity.LOW   : 0.05,
        }
        total = sum(
            deductions.get(v.severity, 0)
            for v in self.violations
        )
        return max(0.0, round(1.0 - total, 2))

    @property
    def violation_summary(self) -> dict:
        """Đếm vi phạm theo loại và mức độ."""
        summary = {
            'total' : len(self.violations),
            'high'  : sum(1 for v in self.violations
                          if v.severity == Severity.HIGH),
            'medium': sum(1 for v in self.violations
                          if v.severity == Severity.MEDIUM),
            'low'   : sum(1 for v in self.violations
                          if v.severity == Severity.LOW),
            'by_type': {}
        }
        for vtype in ViolationType:
            summary['by_type'][vtype.value] = sum(
                1 for v in self.violations
                if v.violation_type == vtype
            )
        return summary

    def to_dict(self) -> dict:
        """Chuyển sang dict để lưu JSON hoặc đưa vào LLM."""
        return {
            'case_id'         : self.case_id,
            'application_type': self.application_type,
            'loan_goal'       : self.loan_goal,
            'requested_amount': self.requested_amount,
            'num_events'      : self.num_events,
            'is_compliant'    : self.is_compliant,
            'fitness_score'   : self.fitness_score,
            'violation_summary': self.violation_summary,
            'violations'      : [
                {
                    'violation_id'  : v.violation_id,
                    'type'          : v.violation_type.value,
                    'severity'      : v.severity.value,
                    'article_ref'   : v.article_ref,
                    'description'   : v.description,
                    'expected'      : v.expected,
                    'actual'        : v.actual,
                    'evidence'      : v.evidence,
                    'suggestion'    : v.suggestion,
                }
                for v in self.violations
            ]
        }