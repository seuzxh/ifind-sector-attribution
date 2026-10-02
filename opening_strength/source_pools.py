"""Resolve fixed iFinD source pools and merge candidate provenance."""

from config import is_a_share_code

from .models import CandidateStock, PoolSource, SourcePoolSpec


SOURCE_POOL_SPECS = (
    SourcePoolSpec("high_beta", "高贝塔值", "883926.TI"),
    SourcePoolSpec("recent_strong", "近期强势", "883409.TI"),
    SourcePoolSpec("hot_stock", "同花顺热股", "883910.TI"),
)


class SourcePoolError(ValueError):
    """A fixed pool could not produce a valid A-share candidate list."""

    def __init__(self, pool_id: str, reason_code: str):
        self.pool_id = pool_id
        self.reason_code = reason_code
        super().__init__(f"{pool_id}: {reason_code}")


class IFindSourcePoolProvider:
    def __init__(self, client):
        self.client = client

    def resolve(self, spec: SourcePoolSpec, trade_date: str) -> tuple[tuple[str, str], ...]:
        payload = self.client.get_concept_members(spec.index_code, trade_date)
        if not isinstance(payload, dict):
            raise SourcePoolError(spec.pool_id, "missing_tables")
        if payload.get("errorcode", 0) != 0:
            raise SourcePoolError(spec.pool_id, "upstream_error")
        tables = payload.get("tables")
        if not isinstance(tables, list) or not tables:
            raise SourcePoolError(spec.pool_id, "missing_tables")
        rows = []
        for entry in tables:
            table = entry.get("table") if isinstance(entry, dict) else None
            if not isinstance(table, dict):
                raise SourcePoolError(spec.pool_id, "missing_fields")
            names, codes = table.get("p03473_f003"), table.get("p03473_f002")
            if not isinstance(names, list) or not isinstance(codes, list):
                raise SourcePoolError(spec.pool_id, "missing_fields")
            if len(names) != len(codes):
                raise SourcePoolError(spec.pool_id, "field_length_mismatch")
            for code, name in zip(codes, names):
                if not isinstance(code, str) or not code.strip() or not isinstance(name, str):
                    raise SourcePoolError(spec.pool_id, "invalid_row_values")
                rows.append((code, name))
        if not any(isinstance(code, str) and is_a_share_code(code) for code, _ in rows):
            raise SourcePoolError(spec.pool_id, "empty_pool")
        # Filtering belongs to the merge so the raw API positions remain source ranks.
        return tuple(rows)


def resolve_candidates(provider, trade_date: str, specs=SOURCE_POOL_SPECS) -> tuple[CandidateStock, ...]:
    merged = {}
    for spec in specs:
        rows = provider.resolve(spec, trade_date)
        if not any(isinstance(code, str) and is_a_share_code(code) for code, _ in rows):
            raise SourcePoolError(spec.pool_id, "empty_pool")
        for rank, (code, name) in enumerate(rows, 1):
            if not isinstance(code, str) or not is_a_share_code(code):
                continue
            previous = merged.get(code)
            if previous is None:
                merged[code] = CandidateStock(code, name, (PoolSource(spec.pool_id, rank),))
                continue
            sources = previous.sources
            if not any(source.pool_id == spec.pool_id for source in sources):
                sources += (PoolSource(spec.pool_id, rank),)
            merged[code] = CandidateStock(code, previous.stock_name or name, sources)
    return tuple(merged.values())
