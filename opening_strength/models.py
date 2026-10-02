"""Immutable domain values for premarket opening strength."""

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping


class ThemeType(str, Enum):
    INDUSTRY = "INDUSTRY"
    CONCEPT = "CONCEPT"


@dataclass(frozen=True)
class SourcePoolSpec:
    pool_id: str
    name: str
    index_code: str


@dataclass(frozen=True)
class PoolSource:
    pool_id: str
    source_rank: int


@dataclass(frozen=True)
class CandidateStock:
    stock_code: str
    stock_name: str
    sources: tuple[PoolSource, ...]


@dataclass(frozen=True)
class ThemeMembership:
    stock_code: str
    theme_code: str
    theme_name: str
    theme_type: ThemeType


@dataclass(frozen=True)
class MembershipResolution:
    memberships: tuple[ThemeMembership, ...]
    unmapped_stock_codes: tuple[str, ...]
    hub_member_date: str
    mapped_count: int
    coverage_ratio: float


@dataclass(frozen=True)
class AttributionConfig:
    model_version: str = "opening-attribution-v1"
    config_version: str = "opening-default-v1"
    feature_weights: tuple[float, ...] = (0.30, 0.35, 0.20, 0.15)
    return_window_weights: tuple[float, ...] = (0.4, 0.3, 0.2, 0.1)
    min_confidence: float = 0.35
    support_saturation: int = 5
    sync_window: int = 20
    sync_min_observations: int = 10
    max_industries: int = 1
    max_concepts: int = 2


DEFAULT_ATTRIBUTION_CONFIG = AttributionConfig()


@dataclass(frozen=True)
class HistoricalFeatures:
    theme_recent_strength: Mapping[str, float | None]
    stock_theme_sync: Mapping[tuple[str, str], float | None]
    coverage_ratio: float


@dataclass(frozen=True)
class AttributionItem:
    stock_code: str
    theme_code: str
    theme_name: str
    theme_type: ThemeType
    rank: int
    raw_score: float
    weight: float
    confidence: float
    reason_codes: tuple[str, ...]
    evidence: Mapping[str, object]
