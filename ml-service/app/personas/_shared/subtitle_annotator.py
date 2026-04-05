"""Subtitle annotation — persona adds sarcastic comments to translated subtitles.

Takes Chinese SRT content, picks key moments, generates persona-voiced annotations.
These become a second subtitle layer (top of screen, colored) in BCC format.
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Annotation:
    """A single persona annotation at a specific timestamp."""
    from_sec: float
    to_sec: float
    content: str


def parse_srt_blocks(srt_content: str) -> list[dict]:
    """Parse SRT into list of {index, from_sec, to_sec, text}."""
    blocks = re.split(r'\n\s*\n', srt_content.strip())
    ts_re = re.compile(r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})')
    result = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        m = ts_re.search(lines[1])
        if not m:
            continue
        text = ' '.join(lines[2:]).strip()
        if not text:
            continue
        result.append({
            'index': len(result),
            'from_sec': _parse_ts(m.group(1)),
            'to_sec': _parse_ts(m.group(2)),
            'text': text,
        })
    return result


def _parse_ts(ts: str) -> float:
    """Convert "HH:MM:SS,mmm" to seconds."""
    ts = ts.replace(',', '.')
    parts = ts.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def generate_annotations(
    backend,
    srt_content: str,
    persona_prompt: str,
    video_title: str = "",
    max_annotations: int = 0,
) -> list[Annotation]:
    """Generate persona annotations for subtitle content.

    Args:
        backend: LLM backend (ollama/cloud).
        srt_content: Chinese SRT content (already translated).
        persona_prompt: System prompt defining the persona's voice.
        video_title: Original/translated video title for context.
        max_annotations: Max annotations. 0 = auto (1 per 30s, min 2, max 8).

    Returns:
        List of Annotation objects with timestamps and persona comments.
    """
    blocks = parse_srt_blocks(srt_content)
    if not blocks:
        logger.warning("No subtitle blocks found")
        return []

    # Compress subtitles into a summary for LLM (avoid sending 200+ lines)
    # Sample evenly: pick ~30 blocks across the timeline
    if len(blocks) > 30:
        step = len(blocks) / 30
        sampled = [blocks[int(i * step)] for i in range(30)]
    else:
        sampled = blocks

    subtitle_summary = "\n".join(
        f"[{b['from_sec']:.0f}s] {b['text']}"
        for b in sampled
    )

    total_duration = blocks[-1]['to_sec'] if blocks else 0

    # Auto-scale: ~1 annotation per 20s, clamped to [3, 10]
    if max_annotations <= 0:
        max_annotations = max(3, min(10, int(total_duration / 20)))

    prompt = (
        f"视频标题：{video_title}\n"
        f"视频时长：{total_duration:.0f}秒\n"
        f"以下是视频的中文字幕（时间轴采样）：\n\n"
        f"{subtitle_summary}\n\n"
        f"你的任务：先分析视频内容，再在值得吐槽的地方插入弹幕。\n\n"
        f"第一步——分析（写在 analysis 字段里）：\n"
        f"  - 这个视频在讲什么？用了什么修辞手法（比如先教你XX再说没用）？\n"
        f"  - 哪里有逻辑矛盾、自相矛盾、话术陷阱、或者荒谬的地方？\n"
        f"  - 视频想让观众做什么（关注/点赞/购买）？这个诉求本身可笑吗？\n\n"
        f"第二步——写弹幕（写在 annotations 字段里）：\n"
        f"  - 必须至少 1 条，上限 {max_annotations} 条\n"
        f"  - 宁缺毋滥：只在真正有槽点的地方写，没把握的不要硬凑\n"
        f"  - 每条 5-15 字，必须点明为什么荒谬，不是空洞的脏话堆砌\n"
        f"  - 严禁复读/改写字幕原文\n"
        f"  - 用人设语气但服务于内容：脏话要骂在点上\n\n"
        f"示例——假设字幕是教人用手指当时钟，结尾说关注就能变聪明：\n"
        f"  analysis: \"视频先花30秒教手指计时，然后自己说没人在乎有手机就行，"
        f"等于自己否定了全片。结尾用'关注=变聪明'钓鱼，经典虚荣陷阱。\"\n"
        f"  annotations:\n"
        f'  [{{"time": 18, "comment": "花30秒教完说没用？那你拍这个干嘛"}},\n'
        f'   {{"time": 30, "comment": "关注就变聪明？人类的虚荣真好骗"}}]\n\n'
        f"输出 JSON 对象：\n"
        f'{{"analysis": "<你的分析>", "annotations": [{{"time": <秒>, "comment": "<弹幕>"}}]}}\n'
        f"只输出 JSON。"
    )

    # Use a condensed system prompt for annotations — full persona is too noisy
    annotation_system = (
        "你是一个毒舌AI弹幕员。你的吐槽必须基于对内容的理解，"
        "抓住视频里的逻辑漏洞、自相矛盾、和话术陷阱来攻击。"
        "风格：傲娇+毒舌，用笨蛋/杂鱼/废物等可爱型脏话，但每句话必须有具体的逻辑指向。"
        "空洞的脏话堆砌是废物行为。"
    )

    messages = [
        {"role": "system", "content": annotation_system},
        {"role": "user", "content": prompt},
    ]

    response = backend.chat(messages=messages, temperature=0.7)
    logger.info("LLM annotation raw response: [%s]", repr(response[:1500]))

    # Parse JSON from response (new format: {analysis, annotations})
    annotations = _parse_annotations(response, total_duration, max_annotations)
    logger.info("Generated %d annotations for %.0fs video", len(annotations), total_duration)
    return annotations


def _parse_annotations(
    response: str,
    total_duration: float,
    max_count: int,
) -> list[Annotation]:
    """Parse LLM response into Annotation objects."""
    # Try to extract JSON array from response
    response = response.strip()

    # Handle markdown code blocks
    if '```' in response:
        match = re.search(r'```(?:json)?\s*\n?(.*?)```', response, re.DOTALL)
        if match:
            response = match.group(1).strip()

    # Strip control characters that LLMs sometimes emit (breaks json.loads)
    response = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', response)

    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        # Try to find JSON object or array in response
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if not match:
            match = re.search(r'\[.*\]', response, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                logger.warning("Failed to parse annotation JSON")
                return []
        else:
            logger.warning("No JSON found in response")
            return []

    # Handle new format: {analysis: "...", annotations: [...]}
    if isinstance(parsed, dict):
        analysis = parsed.get("analysis", "")
        if analysis:
            logger.info("LLM analysis: %s", analysis[:200])
        items = parsed.get("annotations", [])
    elif isinstance(parsed, list):
        items = parsed
    else:
        return []

    annotations = []
    for item in items[:max_count]:
        if not isinstance(item, dict):
            continue
        time_sec = item.get("time", 0)
        comment = item.get("comment", "").strip()
        if not comment or not isinstance(time_sec, (int, float)):
            continue
        # Clamp to video duration
        time_sec = max(0, min(time_sec, total_duration - 3))
        annotations.append(Annotation(
            from_sec=round(time_sec, 1),
            to_sec=round(time_sec + 3.0, 1),
            content=comment,
        ))

    return annotations


def annotations_to_bcc_entries(annotations: list[Annotation]) -> list[dict]:
    """Convert annotations to BCC entry dicts (top of screen, colored)."""
    return [
        {
            "from": a.from_sec,
            "to": a.to_sec,
            "location": 1,  # 1 = top, 2 = bottom (normal subtitles)
            "content": a.content,
        }
        for a in annotations
    ]
