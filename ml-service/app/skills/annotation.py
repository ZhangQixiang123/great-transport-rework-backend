"""AnnotationSkill — self-improving danmaku generation.

Wraps the annotation prompt in the Skill framework so it can evolve
based on user feedback (edits/deletes during subtitle review).
"""
import json
import logging
from typing import Optional

from .base import Skill

logger = logging.getLogger(__name__)

DEFAULT_ANNOTATION_SYSTEM = (
    "你是一个毒舌AI弹幕员。你的吐槽必须基于对内容的理解，"
    "抓住视频里的逻辑漏洞、自相矛盾、和话术陷阱来攻击。"
    "风格：傲娇+毒舌，用笨蛋/杂鱼/废物等可爱型脏话，但每句话必须有具体的逻辑指向。"
    "空洞的脏话堆砌是废物行为。"
)

DEFAULT_ANNOTATION_TEMPLATE = (
    "视频标题：{video_title}\n"
    "视频时长：{total_duration}秒\n"
    "以下是视频的中文字幕（时间轴采样）：\n\n"
    "{subtitle_summary}\n\n"
    "你的任务：先分析视频内容，再在值得吐槽的地方插入弹幕。\n\n"
    "第一步——分析（写在 analysis 字段里）：\n"
    "  - 这个视频在讲什么？用了什么修辞手法（比如先教你XX再说没用）？\n"
    "  - 哪里有逻辑矛盾、自相矛盾、话术陷阱、或者荒谬的地方？\n"
    "  - 视频想让观众做什么（关注/点赞/购买）？这个诉求本身可笑吗？\n\n"
    "第二步——写弹幕（写在 annotations 字段里）：\n"
    "  - 必须至少 1 条，上限 {max_annotations} 条\n"
    "  - 宁缺毋滥：只在真正有槽点的地方写，没把握的不要硬凑\n"
    "  - 每条 5-15 字，必须点明为什么荒谬，不是空洞的脏话堆砌\n"
    "  - 严禁复读/改写字幕原文\n"
    "  - 用人设语气但服务于内容：脏话要骂在点上\n\n"
    "示例——假设字幕是教人用手指当时钟，结尾说关注就能变聪明：\n"
    '  analysis: "视频先花30秒教手指计时，然后自己说没人在乎有手机就行，'
    '等于自己否定了全片。结尾用\'关注=变聪明\'钓鱼，经典虚荣陷阱。"\n'
    "  annotations:\n"
    '  [{{"time": 18, "comment": "花30秒教完说没用？那你拍这个干嘛"}},\n'
    '   {{"time": 30, "comment": "关注就变聪明？人类的虚荣真好骗"}}]\n\n'
    "输出 JSON 对象：\n"
    '{{"analysis": "<你的分析>", "annotations": [{{"time": <秒>, "comment": "<弹幕>"}}]}}\n'
    "只输出 JSON。"
)


class AnnotationSkill(Skill):
    """Self-improving danmaku annotation skill."""

    def __init__(self, name: str = "annotation", db=None, backend=None):
        super().__init__(name, db, backend)

    def _default_system_prompt(self) -> str:
        return DEFAULT_ANNOTATION_SYSTEM

    def _default_prompt_template(self) -> str:
        return DEFAULT_ANNOTATION_TEMPLATE

    def _output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "analysis": {"type": "string"},
                "annotations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": {"type": "number"},
                            "comment": {"type": "string"},
                        },
                        "required": ["time", "comment"],
                    },
                },
            },
            "required": ["annotations"],
        }

    def execute(self, context: dict) -> dict:
        """Generate annotations using current (possibly evolved) prompts."""
        prompt = self.prompt_template.format(**context)
        response = self.backend.chat(
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        return self._parse_response(response)

    def reflect_on_feedback(self, feedback_list: list, apply: bool = False) -> Optional[dict]:
        """Analyze annotation feedback and propose prompt updates.

        When apply=False (default), returns proposed changes without saving.
        When apply=True, saves changes to DB immediately.
        """
        if not feedback_list:
            return None

        feedback_report = self._format_feedback(feedback_list)

        reflection_prompt = (
            "你是弹幕生成AI。以下是用户对你最近生成的弹幕的审核结果。\n\n"
            "## 你当前的弹幕生成系统提示\n"
            f"{self.system_prompt}\n\n"
            "## 用户审核反馈\n"
            f"{feedback_report}\n\n"
            "## 任务\n"
            "分析用户的偏好：\n"
            "1. 被保留的弹幕有什么共性？\n"
            "2. 被删除的弹幕有什么问题？\n"
            "3. 被修改的弹幕，用户改了什么方向？\n\n"
            "根据分析更新你的系统提示，让未来的弹幕更符合用户偏好。\n\n"
            "输出JSON:\n"
            '{\n'
            '  "analysis": "用中文自然语言写一段总结，概括用户偏好和你的改动理由",\n'
            '  "updated_system_prompt": "更新后的系统提示（完整版）"\n'
            '}'
        )

        response = self.backend.chat(
            messages=[
                {"role": "system", "content": "你在分析用户反馈来改进弹幕生成。输出JSON。"
                 "analysis字段必须是中文自然语言段落，不要用JSON或列表。"},
                {"role": "user", "content": reflection_prompt},
            ],
            temperature=0.3,
        )
        result = self._parse_response(response)

        proposed_prompt = result.get("updated_system_prompt")
        if proposed_prompt and apply:
            self._update_prompt(
                {"system_prompt": proposed_prompt},
                changed_by="annotation_feedback",
                reason=result.get("analysis", "Annotation feedback reflection"),
            )

        result["proposed_system_prompt"] = proposed_prompt
        result["current_system_prompt"] = self.system_prompt
        return result

    @staticmethod
    def _format_feedback(feedback_list: list) -> str:
        lines = []
        for f in feedback_list:
            title = f.get("video_title", "?")
            lines.append(f"视频: {title}")
            kept = f.get("kept", [])
            deleted = f.get("deleted", [])
            edited = f.get("edited", [])
            if kept:
                lines.append(f"  保留({len(kept)}): " + " / ".join(k.get("comment", "") for k in kept[:5]))
            if deleted:
                lines.append(f"  删除({len(deleted)}): " + " / ".join(d.get("comment", "") for d in deleted[:5]))
            if edited:
                for e in edited[:3]:
                    lines.append(f"  修改: \"{e.get('original', '')}\" → \"{e.get('edited', '')}\"")
            lines.append("")
        return "\n".join(lines)
