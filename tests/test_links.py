"""Tests for cross-link scoring (wiki_spaces._links).

Pins the CONVENTIONS / Linking rules policy as verifiable code: each signal,
the shared-tag boundary, additivity, and the apply threshold.
"""

from __future__ import annotations

from wiki_spaces._links import (
    LINK_SCORE_THRESHOLD,
    LinkCandidate,
    score_cross_link,
    should_link,
)


def test_exact_name_match_alone_links():
    c = LinkCandidate(name_match="exact")
    assert score_cross_link(c) == 4
    assert should_link(c) is True


def test_partial_name_match_alone_does_not_link():
    c = LinkCandidate(name_match="partial")
    assert score_cross_link(c) == 1
    assert should_link(c) is False


def test_partial_plus_shared_tags_links():
    c = LinkCandidate(name_match="partial", shared_tags=2)
    assert score_cross_link(c) == 3
    assert should_link(c) is True


def test_shared_tags_below_minimum_scores_zero():
    c = LinkCandidate(name_match="partial", shared_tags=1)
    assert score_cross_link(c) == 1
    assert should_link(c) is False


def test_partial_plus_same_project_links():
    c = LinkCandidate(name_match="partial", same_project=True)
    assert score_cross_link(c) == 3
    assert should_link(c) is True


def test_partial_plus_cross_category_links():
    c = LinkCandidate(name_match="partial", cross_category=True)
    assert score_cross_link(c) == 3
    assert should_link(c) is True


def test_all_signals_are_additive():
    c = LinkCandidate(
        name_match="exact", shared_tags=5, same_project=True, cross_category=True
    )
    assert score_cross_link(c) == 4 + 2 + 2 + 2


def test_unrecognized_name_match_contributes_zero():
    c = LinkCandidate(name_match="none", shared_tags=2)
    assert score_cross_link(c) == 2


def test_threshold_is_three():
    assert LINK_SCORE_THRESHOLD == 3
