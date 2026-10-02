"""Reverse the hub's latest member snapshots into candidate theme relations."""

from collections.abc import Sequence

from config import is_a_share_code, is_a_share_concept

from .models import CandidateStock, MembershipResolution, ThemeMembership, ThemeType


def resolve_memberships(store, candidates: Sequence[CandidateStock]) -> MembershipResolution:
    names = store.get_concept_names()
    theme_codes = sorted(code for code in names
                         if is_a_share_concept(code) and code.startswith(("884", "885", "886")))
    member_map = store.get_concept_members_map(theme_codes)
    candidate_codes = {candidate.stock_code for candidate in candidates
                       if is_a_share_code(candidate.stock_code)}
    relations = set()
    for theme_code in theme_codes:
        theme_type = ThemeType.INDUSTRY if theme_code.startswith("884") else ThemeType.CONCEPT
        for member in member_map.get(theme_code, ()):
            stock_code = member["stock_code"]
            if stock_code in candidate_codes:
                relations.add(ThemeMembership(stock_code, theme_code, names[theme_code], theme_type))
    memberships = tuple(sorted(relations, key=lambda relation: (
        relation.stock_code, 0 if relation.theme_type == ThemeType.INDUSTRY else 1,
        relation.theme_code,
    )))
    mapped_codes = {relation.stock_code for relation in memberships}
    return MembershipResolution(
        memberships=memberships,
        unmapped_stock_codes=tuple(sorted(candidate_codes - mapped_codes)),
        hub_member_date=store.get_latest_member_date(),
        mapped_count=len(mapped_codes),
        coverage_ratio=len(mapped_codes) / len(candidate_codes) if candidate_codes else 0.0,
    )
