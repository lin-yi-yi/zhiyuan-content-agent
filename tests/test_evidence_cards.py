import re

from app.services.evidence_cards import build_evidence_cards, excerpt_pages


def test_pagination_preserves_text_and_sentence_boundaries():
    sentence = "审核者需要核对资料来源、适用范围和生效日期，完成后才可以批准当前稿件。"
    text = "\n\n".join([sentence] * 14)
    pages = excerpt_pages(text)
    assert len(pages) > 2
    assert all(len(page) <= 138 and page.endswith("。") for page in pages)
    assert re.sub(r"\s", "", "".join(pages)) == re.sub(r"\s", "", text)


def test_long_unpunctuated_sentence_is_continued_without_inventing_a_full_stop():
    text = "这是没有断句的真实原文" * 70
    pages = excerpt_pages(text)
    assert "".join(pages) == text
    assert all(0 < len(page) <= 138 for page in pages)


def test_card_budget_is_visible_and_every_excerpt_keeps_its_source():
    citations = [{"marker": f"[chunk:{i}]", "title": "合成指南", "excerpt": "检查原文后再发布。" * 100}
                 for i in range(5)]
    cards = build_evidence_cards("面向新用户的知识分享", citations)
    assert len(cards) == 11
    assert [card["page_index"] for card in cards] == list(range(1, 12))
    for offset, source in enumerate(citations):
        first, last = cards[1 + offset * 2:3 + offset * 2]
        assert first["subtitle"] == last["highlight"] == source["marker"]
        assert first["body"] in source["excerpt"]
        assert len(last["body"]) <= 138
        assert "更多内容见来源" in last["footer"]
