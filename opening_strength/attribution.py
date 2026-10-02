"""Pure, deterministic attribution within authoritative theme memberships."""

from collections import defaultdict
from dataclasses import replace
from math import fsum

from .models import AttributionItem, DEFAULT_ATTRIBUTION_CONFIG, ThemeType


FEATURE_NAMES = ("theme_recent_strength", "peer_support", "source_pool_diversity",
                 "stock_theme_sync")


def score_attributions(candidates, memberships, history, config=DEFAULT_ATTRIBUTION_CONFIG):
    """Score each real relation, select qualifying themes, normalize per stock."""
    stock_pools = defaultdict(set)
    for candidate in candidates:
        stock_pools[candidate.stock_code].update(source.pool_id for source in candidate.sources)
    relations = {}
    for relation in memberships:
        if relation.stock_code not in stock_pools:
            continue
        key = (relation.stock_code, relation.theme_code)
        previous = relations.get(key)
        # The code identifies a relation. Conflicting display names never add votes.
        if previous is None or (relation.theme_name, relation.theme_type.value) < (
                previous.theme_name, previous.theme_type.value):
            relations[key] = relation
    theme_members = defaultdict(set)
    for stock, theme in relations:
        theme_members[theme].add(stock)
    theme_pools = {theme: set().union(*(stock_pools[stock] for stock in stocks))
                   for theme, stocks in theme_members.items()}
    scored = defaultdict(list)
    total_feature_weight = fsum(config.feature_weights)
    for (stock, theme), relation in sorted(relations.items()):
        peers = len(theme_members[theme] - {stock})
        pool_count = len(theme_pools[theme])
        support = min(peers / config.support_saturation, 1.0)
        diversity = min(pool_count / 3, 1.0)
        recent = history.theme_recent_strength.get(theme)
        sync = history.stock_theme_sync.get((stock, theme))
        features = (recent, support, diversity, sync)
        available_weight = fsum(weight for feature, weight in zip(features, config.feature_weights)
                                if feature is not None)
        coverage = available_weight / total_feature_weight if total_feature_weight else 0.0
        raw_score = (fsum(feature * weight for feature, weight in zip(features, config.feature_weights)
                          if feature is not None) / available_weight if available_weight else 0.0)
        confidence = fsum((.45 * coverage, .35 * support, .20 * diversity))
        reasons = ["PEER_CONFIRMATION" if peers >= 2 else "LOW_SUPPORT"]
        if recent is not None and recent >= .75:
            reasons.append("STRONG_RECENT_THEME")
        if pool_count >= 2:
            reasons.append("MULTI_POOL_SUPPORT")
        if sync is not None and sync >= .75:
            reasons.append("HIGH_SYNCHRONY")
        missing = [name for name, value in zip(FEATURE_NAMES, features) if value is None]
        if missing:
            reasons.append("MISSING_HISTORY")
        if confidence < config.min_confidence:
            reasons.append("LOW_CONFIDENCE")
        evidence = dict(zip(FEATURE_NAMES, features))
        evidence.update(feature_coverage=coverage, missing_features=missing,
                        peer_count=peers, source_pool_count=pool_count,
                        source_pool_ids=sorted(theme_pools[theme]))
        scored[stock].append(AttributionItem(
            stock, theme, relation.theme_name, relation.theme_type, 0, raw_score,
            0.0, confidence, tuple(sorted(reasons)), evidence))

    result = []
    for stock in sorted(scored):
        ordered = sorted(scored[stock], key=lambda item: (
            -item.raw_score, -item.evidence["peer_count"],
            -item.evidence["source_pool_count"], item.theme_code))
        selected = []
        counts = defaultdict(int)
        limits = {ThemeType.INDUSTRY: config.max_industries,
                  ThemeType.CONCEPT: config.max_concepts}
        for item in ordered:
            if item.confidence >= config.min_confidence and counts[item.theme_type] < limits[item.theme_type]:
                selected.append(item)
                counts[item.theme_type] += 1
        if not selected:
            selected = ordered[:1]
        total_score = fsum(item.raw_score for item in selected)
        for rank, item in enumerate(selected, 1):
            weight = item.raw_score / total_score if total_score else 1 / len(selected)
            result.append(replace(item, rank=rank, weight=weight))
    return tuple(result)
