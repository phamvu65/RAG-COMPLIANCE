import os
import re
import time
from typing import List, Dict
import anthropic
from dotenv import load_dotenv

load_dotenv()


def _strip_markdown(text: str) -> str:
    """
    Loại bỏ ký hiệu Markdown khỏi text để in ra console sạch.
    Xử lý: ##, **, __, *, _, `code`
    """
    # Bỏ heading ##, ###, # (giữ nội dung)
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Bỏ bold/italic **text**, __text__, *text*, _text_
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__',     r'\1', text)
    text = re.sub(r'\*(.+?)\*',     r'\1', text)
    text = re.sub(r'_(.+?)_',       r'\1', text)
    # Bỏ inline code `text`
    text = re.sub(r'`(.+?)`',       r'\1', text)
    # Chuẩn hoá dòng trống thừa
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class LLMExplainer:
    """
    Sinh giải thích vi phạm bằng Claude claude-haiku-4-5-20251001.
    Output là plain text (không Markdown) để in trực tiếp ra console.
    """

    SYSTEM_PROMPT = """Bạn là chuyên gia kiểm tra tuân thủ quy trình nghiệp vụ, chuyên về Chỉ thị Tín dụng Tiêu dùng EU (Directive 2008/48/EC).

Trả lời ĐÚNG theo cấu trúc 3 dòng sau, KHÔNG thêm gì khác:

TÓM TẮT: [1 câu duy nhất mô tả vi phạm, tối đa 30 từ]
LÝ DO: [2-3 câu giải thích tại sao vi phạm theo điều khoản cụ thể, tối đa 60 từ]
KHẮC PHỤC: [1-2 câu hành động cụ thể cần làm, tối đa 40 từ]

Quy tắc bắt buộc:
- Viết tiếng Việt
- KHÔNG dùng Markdown, KHÔNG dùng ##, **, *, --
- KHÔNG đánh số mục con (1.1, 1.2...)
- Mỗi phần chỉ 1 đoạn văn liền mạch
- Tổng cộng tối đa 130 từ"""

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
        # Chỉ lấy evidence quan trọng, bỏ các key dài
        SKIP_KEYS = {'call_timestamps', 'present_activities'}
        evidence_str = '\n'.join([
            f"  {k}: {v}"
            for k, v in violation.get('evidence', {}).items()
            if k not in SKIP_KEYS
        ])

        return (
            f"Vi phạm: {violation['type'].upper()} | "
            f"{violation['article_ref']} | "
            f"Mức độ: {violation['severity']}\n"
            f"Mô tả: {violation['description']}\n"
            f"Kỳ vọng: {violation['expected']}\n"
            f"Thực tế: {violation['actual']}\n"
            f"Bằng chứng:\n{evidence_str}\n\n"
            f"Hồ sơ: {case_info['case_id']} | "
            f"{case_info['application_type']} | "
            f"{case_info['loan_goal']} | "
            f"{case_info['requested_amount']:,.0f} EUR"
        )

    def explain_violation(self,
                           case_info : Dict,
                           violation : Dict,
                           context   : Dict) -> str:
        """Sinh giải thích cho 1 vi phạm — trả về plain text."""
        try:
            response = self.client.messages.create(
                model      = self.model,
                max_tokens = 1000,
                system     = self.SYSTEM_PROMPT,
                messages   = [{
                    "role"   : "user",
                    "content": self._build_prompt(
                        case_info, violation, context
                    )
                }]
            )
            raw         = response.content[0].text.strip()
            stop_reason = response.stop_reason

            # Phát hiện bị cắt giữa chừng
            if stop_reason == 'max_tokens':
                raw += '\n[Cảnh báo: giải thích bị cắt ngắn]'

            return _strip_markdown(raw)

        except Exception as e:
            return (
                f"TÓM TẮT: {violation['description']}\n"
                f"LÝ DO: Vi phạm {violation['article_ref']} — "
                f"{violation['expected']}\n"
                f"KHẮC PHỤC: {violation.get('suggestion', 'Liên hệ bộ phận tuân thủ.')}\n"
                f"[LLM error: {str(e)[:120]}]"
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