"""Build, validate and publish immutable opening-strength runs.

Model rules: docs/superpowers/specs/2026-10-02-opening-strength-premarket-design.md.
The repository accepts primitives; all domain conversion happens here.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from math import fsum, isfinite
from uuid import uuid4
from zoneinfo import ZoneInfo

from config import is_a_share_code, is_a_share_concept

from .attribution import score_attributions
from .memberships import resolve_memberships
from .models import ThemeType
from .source_pools import SourcePoolError, resolve_candidates


SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class PremarketRunResult:
    run_id: str
    trade_date: str
    status: str
    candidate_count: int
    mapped_count: int
    coverage_ratio: float
    history_coverage_ratio: float


class PremarketRunError(ValueError):
    """Safe public error identifying the failed run without upstream content."""

    def __init__(self, run_id, failure_code, failure_detail):
        self.run_id = run_id
        self.failure_code = failure_code
        self.failure_detail = failure_detail
        super().__init__(f"{run_id}: {failure_code}: {failure_detail}")


class _ValidationError(ValueError):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)


def _reject(code, detail):
    raise _ValidationError(code, detail)


def _validate_metadata(run):
    if any(not isinstance(run[key], str) or not run[key].strip()
           for key in ("run_id", "trade_date", "model_version", "config_version")):
        _reject("MISSING_RUN_METADATA", "Required run metadata is missing")
    date = run["trade_date"]
    try:
        valid = len(date) == 8 and date.isdigit() and datetime.strptime(date, "%Y%m%d").strftime("%Y%m%d") == date
    except ValueError:
        valid = False
    if not valid:
        _reject("INVALID_TRADE_DATE", "Trade date must be a valid YYYYMMDD date")


def _validate_theme(code, theme_type):
    if (not is_a_share_concept(code) or not code.startswith(("884", "885", "886"))
            or theme_type != (ThemeType.INDUSTRY if code.startswith("884") else ThemeType.CONCEPT)):
        _reject("INVALID_THEME_CODE", "Theme code or type is outside the supported scope")


def _validate_snapshot(candidates, resolution, items, history):
    codes = {candidate.stock_code for candidate in candidates}
    if not codes:
        _reject("EMPTY_CANDIDATES", "Candidate set is empty")
    if any(not is_a_share_code(code) for code in codes):
        _reject("INVALID_STOCK_CODE", "Candidate code is outside the A-share scope")
    relations = {}
    for membership in resolution.memberships:
        if membership.stock_code not in codes or not is_a_share_code(membership.stock_code):
            _reject("INVALID_STOCK_CODE", "Membership stock is outside the candidate scope")
        _validate_theme(membership.theme_code, membership.theme_type)
        relations[(membership.stock_code, membership.theme_code)] = membership.theme_type
    mapped = {stock for stock, _ in relations}
    coverage = len(mapped) / len(codes)
    if coverage < .90:
        _reject("LOW_MEMBERSHIP_COVERAGE", "Membership coverage is below 0.90")
    selected = defaultdict(list)
    for item in items:
        if item.stock_code not in codes or not is_a_share_code(item.stock_code):
            _reject("INVALID_STOCK_CODE", "Attribution stock is outside the candidate scope")
        _validate_theme(item.theme_code, item.theme_type)
        if relations.get((item.stock_code, item.theme_code)) != item.theme_type:
            _reject("INVALID_ATTRIBUTION_MEMBERSHIP", "Attribution has no matching authoritative Membership")
        if any(not isfinite(value) for value in (item.raw_score, item.weight, item.confidence)):
            _reject("INVALID_ATTRIBUTION_WEIGHT", "Attribution contains a non-finite numeric value")
        if item.weight < 0 or not 0 <= item.confidence <= 1 or item.raw_score < 0:
            _reject("INVALID_ATTRIBUTION_WEIGHT", "Attribution numeric values are outside their valid range")
        selected[item.stock_code].append(item)
    if set(selected) != mapped:
        _reject("MISSING_ATTRIBUTION", "Every mapped stock must have an attribution")
    for stock_items in selected.values():
        counts = Counter(item.theme_type for item in stock_items)
        if counts[ThemeType.INDUSTRY] > 1 or counts[ThemeType.CONCEPT] > 2 or len(stock_items) > 3:
            _reject("THEME_LIMIT_EXCEEDED", "Attribution exceeds the per-stock Theme limits")
        if abs(fsum(item.weight for item in stock_items) - 1) > 1e-6:
            _reject("INVALID_ATTRIBUTION_WEIGHT", "Per-stock attribution weights do not sum to one")
    if not isfinite(history.coverage_ratio) or not 0 <= history.coverage_ratio <= 1:
        _reject("INVALID_HISTORY_COVERAGE", "Historical coverage must be between zero and one")
    return len(codes), len(mapped), coverage


class OpeningPremarketService:
    def __init__(self, repository, pool_provider, membership_store, history_provider, config, clock):
        self.repository = repository
        self.pool_provider = pool_provider
        self.membership_store = membership_store
        self.history_provider = history_provider
        self.config = config
        self.clock = clock

    def _now(self):
        value = self.clock()
        return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)

    def _check_replacement(self, trade_date, force_replace, now, has_existing=None):
        if has_existing is None:
            has_existing = self.repository.get_frozen_opening_run(trade_date) is not None
        if (has_existing and trade_date == now.strftime("%Y%m%d")
                and now.time() >= time(9, 30) and not force_replace):
            _reject("REPLACEMENT_AFTER_OPEN", "Today's frozen run requires explicit replacement after 09:30")
        return has_existing

    def run_and_freeze(self, trade_date: str, force_replace: bool = False) -> PremarketRunResult:
        run_id = str(uuid4())
        created = False
        stage = "RUN_CREATION"
        try:
            now = self._now()
            run = {"run_id": run_id, "trade_date": trade_date or "", "status": "RUNNING",
                   "model_version": self.config.model_version or "",
                   "config_version": self.config.config_version or "",
                   "started_at": now.isoformat(timespec="seconds"),
                   "override_reason": "FORCE_REPLACE" if force_replace else None}
            self.repository.create_opening_run(run)
            created = True
            _validate_metadata(run)
            self._check_replacement(trade_date, force_replace, now)
            stage = "SOURCE_POOL_REQUEST_FAILED"
            candidates = resolve_candidates(self.pool_provider, trade_date)
            stage = "SNAPSHOT_PERSISTENCE_FAILED"
            self.repository.save_opening_candidates(run_id, [
                {"stock_code": candidate.stock_code, "stock_name": candidate.stock_name,
                 "source_pool_id": source.pool_id, "source_rank": source.source_rank, "source_score": None}
                for candidate in candidates for source in candidate.sources])
            stage = "MEMBERSHIP_RESOLUTION_FAILED"
            resolution = resolve_memberships(self.membership_store, candidates)
            stage = "SNAPSHOT_PERSISTENCE_FAILED"
            self.repository.save_opening_memberships(run_id, [
                {"stock_code": membership.stock_code, "theme_code": membership.theme_code,
                 "theme_name": membership.theme_name, "theme_type": membership.theme_type.value,
                 "source": "sector_hub_authoritative"} for membership in resolution.memberships])
            stage = "HISTORY_FEATURES_FAILED"
            history = self.history_provider.build(trade_date, candidates, resolution.memberships)
            stage = "ATTRIBUTION_SCORING_FAILED"
            items = score_attributions(candidates, resolution.memberships, history, self.config)
            stage = "SNAPSHOT_PERSISTENCE_FAILED"
            self.repository.save_opening_attributions(run_id, [
                {"stock_code": item.stock_code, "theme_code": item.theme_code,
                 "theme_name": item.theme_name, "theme_type": item.theme_type.value,
                 "rank": item.rank, "raw_score": item.raw_score, "weight": item.weight,
                 "confidence": item.confidence, "reason_codes": item.reason_codes,
                 "evidence": dict(item.evidence)} for item in items])
            count, mapped, coverage = _validate_snapshot(candidates, resolution, items, history)
            stage = "RUN_VALIDATION_FAILED"
            validated_at = self._now().isoformat(timespec="seconds")
            self.repository.mark_opening_run_validated(run_id, {
                "hub_member_date": resolution.hub_member_date, "candidate_count": count,
                "mapped_count": mapped, "coverage_ratio": coverage,
                "history_coverage_ratio": history.coverage_ratio, "validated_at": validated_at})
            def publication_policy(date, replacing):
                # This callback runs after publication acquires the SQLite write lock.
                publication_time = self._now()
                self._check_replacement(date, force_replace, publication_time, replacing)
                return publication_time.isoformat(timespec="seconds")

            stage = "RUN_FREEZE_FAILED"
            self.repository.freeze_opening_run(run_id, True, publication_policy=publication_policy)
            return PremarketRunResult(run_id, trade_date, "FROZEN", count, mapped, coverage, history.coverage_ratio)
        except Exception as error:
            if isinstance(error, _ValidationError):
                code, detail = error.code, error.detail
            elif isinstance(error, SourcePoolError):
                code = ("EMPTY_SOURCE_POOL" if error.reason_code == "empty_pool" else
                        "SOURCE_POOL_REQUEST_FAILED" if error.reason_code == "upstream_error" else
                        "INVALID_SOURCE_POOL_RESPONSE")
                detail = "A fixed source pool could not produce a valid snapshot"
            else:
                code, detail = stage, "Premarket run failed during an input, scoring or persistence operation"
            code, detail = code[:64], detail[:256]
            if created:
                try:
                    self.repository.fail_opening_run(run_id, code, detail)
                except Exception:
                    # A storage outage cannot persist FAILED; keep the public error safe.
                    detail = (detail + "; Failure status could not be recorded")[:256]
            raise PremarketRunError(run_id, code, detail) from None
