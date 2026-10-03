"""Bounded, readable source excerpts; pagination never asks a model for new facts."""
import re


def excerpt_pages(text: str, limit: int = 138) -> list[str]:
    """Preserve text, preferring sentence/line boundaries over an arbitrary cut.

    An unusually long sentence is continued on another page, without invented
    punctuation. Source excerpts are bounded upstream to 1,200 characters.
    """
    remaining = str(text or "").strip()
    pages = []
    while remaining:
        if len(remaining) <= limit:
            pages.append(remaining)
            break
        window = remaining[:limit]
        boundaries = [match.end() for match in re.finditer(r"[。！？!?；;\n]|\.(?=\s)", window)]
        useful = [end for end in boundaries if end >= limit // 3]
        if not useful:
            useful = [match.end() for match in re.finditer(r"[，,、\s]", window)
                      if match.end() >= limit // 3]
        end = useful[-1] if useful else limit
        pages.append(remaining[:end].strip())
        remaining = remaining[end:].lstrip()
    return pages


def build_evidence_cards(title: str, citations: list[dict]) -> list[dict]:
    """At most two pages per source; explicitly label a partial extract."""
    specs = [{"card_type": "cover", "title": title[:38],
              "subtitle": "有据可查的资料整理", "body": "阅读要点，核对出处，再决定是否发布。",
              "footer": "AI 辅助整理 · 待人工审核"}]
    for citation in citations[:5]:
        pages = excerpt_pages(citation.get("excerpt", ""))
        marker = citation["marker"]
        for index, body in enumerate(pages[:2]):
            truncated = len(pages) > 2 and index == 1
            source_title = str(citation.get("title") or "资料摘录")
            specs.append({"card_type": "content", "title": source_title[:32],
                          "subtitle": marker, "body": body, "highlight": marker,
                          "footer": f"原文{'节选 · 更多内容见来源' if truncated else '摘录 · 发布前核验'}",
                          "style_json": {"component_key": "icon_points", "body_flow": "paragraphs",
                                         "density": "default", "font_size": "default", "body_lines": 12,
                                         "item_lines": 4, "body_scale": 100, "line_height": 145,
                                         "show_highlight": False, "show_footer": True}})
    for index, spec in enumerate(specs, 1):
        spec.update(page_index=index, layout_key="clean_knowledge", theme_key="lab_clean")
    return specs
