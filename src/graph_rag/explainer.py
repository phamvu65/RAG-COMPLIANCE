import os
import time
from typing import List, Dict
import anthropic
from dotenv import load_dotenv

load_dotenv()


class LLMExplainer:
    """
    Sinh giải thích vi phạm bằng Claude claude-haiku-4-5-20251001.
    Nhanh, rẻ, không bị rate limit như Gemini free tier.
    """

    SYSTEM_PROMPT = """Bạn là chuyên gia kiểm tra tuân thủ
quy trình nghiệp vụ, chuyên về Chỉ thị Tín dụng Tiêu dùng
EU (Directive 2008/48/EC).

Nhiệm vụ của bạn là giải thích các vi phạm quy trình
một cách rõ ràng và có thể hành động được.

Quy tắc khi trả lời:
1. Luôn trích dẫn điều khoản cụ thể (Article X)
2. Giải thích TẠI SAO đây là vi phạm bằng ngôn ngữ đơn giản
3. Nêu rõ điều gì LẼ RA phải xảy ra
4. Đưa ra đề xuất khắc phục cụ thể
5. Trả lời bằng tiếng Việt
6. Tối đa 5 câu, súc tích và chuyên nghiệp"""

    def __init__(self,
                 model: str = 'claude-haiku-4-5-20251001'):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
        self.model = model
        print(f"LLM Explainer ready: {model}")

    def _build_prompt(self,
                       case_info : Dict,
                       violation : Dict,
                       context   : Dict) -> str:
        evidence_str = '\n'.join([
            f"  - {k}: {v}"
            for k, v in violation.get('evidence', {}).items()
            if k != 'call_timestamps'
        ])

        return f"""
THÔNG TIN HỒ SƠ:
- Mã hồ sơ       : {case_info['case_id']}
- Loại hồ sơ     : {case_info['application_type']}
- Mục đích vay   : {case_info['loan_goal']}
- Số tiền vay    : {case_info['requested_amount']:,.0f} EUR
- Số sự kiện     : {case_info['num_events']}

VI PHẠM PHÁT HIỆN:
- Loại vi phạm   : {violation['type']}
- Mức độ         : {violation['severity']}
- Điều khoản     : {violation['article_ref']}
- Mô tả          : {violation['description']}
- Kỳ vọng        : {violation['expected']}
- Thực tế        : {violation['actual']}
- Bằng chứng     :
{evidence_str}

{context['regulation_text']}

Hãy giải thích vi phạm này theo cấu trúc:
1. **Tóm tắt**: [1 câu mô tả vi phạm]
2. **Lý do vi phạm**: [tại sao đây là vi phạm theo quy định]
3. **Hành động cần thực hiện**: [đề xuất khắc phục cụ thể]
"""

    def explain_violation(self,
                           case_info : Dict,
                           violation : Dict,
                           context   : Dict) -> str:
        """Sinh giải thích cho 1 vi phạm."""
        try:
            response = self.client.messages.create(
                model      = self.model,
                max_tokens = 400,
                system     = self.SYSTEM_PROMPT,
                messages   = [
                    {
                        "role"   : "user",
                        "content": self._build_prompt(
                            case_info, violation, context
                        )
                    }
                ]
            )
            return response.content[0].text.strip()

        except Exception as e:
            return (
                f"**Tóm tắt**: {violation['description']}\n"
                f"**Lý do vi phạm**: Vi phạm "
                f"{violation['article_ref']} — "
                f"{violation['expected']}\n"
                f"**Hành động**: "
                f"{violation.get('suggestion', '')}\n"
                f"[LLM error: {str(e)[:100]}]"
            )

    def explain_report(self,
                        case_info  : Dict,
                        violations : List[Dict],
                        contexts   : List[Dict]) -> List[str]:
        """Sinh giải thích cho toàn bộ vi phạm của 1 case."""
        return [
            self.explain_violation(case_info, v, c)
            for v, c in zip(violations, contexts)
        ]