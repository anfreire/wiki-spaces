"""Cross-link scoring for wiki-spaces.

Pure functions, stdlib only. This is the authoritative implementation of the
cross-link scoring policy described in CONVENTIONS.md / Linking rules — kept
as tested code so the policy has one verifiable definition instead of living
only in skill prose, where the numbers drift.

`wiki-tend`'s cross-link pass builds a `LinkCandidate` for each unlinked
mention of one page found in another, then calls `should_link` to decide
whether to add the link.
"""

from __future__ import annotations

from dataclasses import dataclass

# Score weights — CONVENTIONS.md / Linking rules. Signals are additive.
SCORE_EXACT_NAME = 4
SCORE_PARTIAL_NAME = 1
SCORE_SHARED_TAGS = 2
SCORE_SAME_PROJECT = 2
SCORE_CROSS_CATEGORY = 2

# Shared-tag count at or above which the shared-tags bonus applies.
SHARED_TAGS_MIN = 2

# Minimum score for a link to be applied.
LINK_SCORE_THRESHOLD = 3


@dataclass(frozen=True)
class LinkCandidate:
    """One potential cross-link: an unlinked mention of a target page found
    in a source page's body.

    - `name_match` — how the mention matched the target's name/title/alias:
      `"exact"` (the whole name) or `"partial"`. A candidate only exists
      because a mention was found, so one of those two always applies.
    - `shared_tags` — number of tags the source and target pages share.
    - `same_project` — both pages live under the same project space.
    - `cross_category` — the pages sit in different top-level categories.
    """

    name_match: str
    shared_tags: int = 0
    same_project: bool = False
    cross_category: bool = False


def score_cross_link(candidate: LinkCandidate) -> int:
    """Return the additive cross-link score per CONVENTIONS / Linking rules.

    exact name match +4, partial name match +1, shared tags (>= 2) +2,
    same project +2, cross-category +2. An unrecognized `name_match`
    contributes 0.
    """
    score = 0
    if candidate.name_match == "exact":
        score += SCORE_EXACT_NAME
    elif candidate.name_match == "partial":
        score += SCORE_PARTIAL_NAME
    if candidate.shared_tags >= SHARED_TAGS_MIN:
        score += SCORE_SHARED_TAGS
    if candidate.same_project:
        score += SCORE_SAME_PROJECT
    if candidate.cross_category:
        score += SCORE_CROSS_CATEGORY
    return score


def should_link(candidate: LinkCandidate) -> bool:
    """True when `candidate` scores at or above `LINK_SCORE_THRESHOLD`."""
    return score_cross_link(candidate) >= LINK_SCORE_THRESHOLD
