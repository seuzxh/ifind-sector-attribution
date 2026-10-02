"""Immutable domain values for premarket opening strength."""

from dataclasses import dataclass
from enum import Enum


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
