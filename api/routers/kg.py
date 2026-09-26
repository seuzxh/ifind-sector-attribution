# -*- coding: utf-8 -*-
"""知识图谱域路由：5 查询 + 3 图供数（重组装在 api.kg_views）"""

from fastapi import APIRouter

from api.deps import db
from api.kg_views import (
    build_projection_graph,
    build_sector_graph,
    build_star_graph,
    get_kg_changes_view,
    get_kg_communities_view,
    get_kg_locate_groups_view,
    resolve_kg_node,
)

router = APIRouter()


@router.get("/api/kg/stock/{code}/sectors")
def kg_stock_sectors(code: str):
    """个股归属板块（图谱口径，含 confidence / corr_20d / 生效日期），按|ρ|降序。"""
    edges = db.get_kg_edges_for_stock(code)
    if not edges:
        return {"error": f"图谱中未找到个股 {code}"}
    edges.sort(key=lambda e: -(abs(e["corr_20d"]) if e["corr_20d"] is not None else -1))
    return {"code": code, "count": len(edges), "sectors": edges}


@router.get("/api/kg/sector/{code}/stocks")
def kg_sector_stocks(code: str, order: str = "corr"):
    """板块成分股（图谱口径）。order=corr 按|ρ|降序，=code 按代码。"""
    edges = db.get_kg_edges_for_sector(code)
    if not edges:
        return {"error": f"图谱中未找到板块 {code}"}
    if order == "corr":
        edges.sort(key=lambda e: -(abs(e["corr_20d"]) if e["corr_20d"] is not None else -1))
    else:
        edges.sort(key=lambda e: e["stock_code"] or "")
    return {"code": code, "count": len(edges), "stocks": edges}


@router.get("/api/kg/linked/{code}")
def kg_linked(code: str, top_n: int = 10):
    """联动股 TopN（共享板块 + Σ|ρ| 评分，含共享明细）。"""
    from kg_analysis import linked_stocks
    rows = linked_stocks(db, code, top_n=top_n)
    if not rows:
        return {"error": f"图谱中未找到个股 {code} 或无联动数据"}
    return {"code": code, "count": len(rows), "linked": rows}


@router.get("/api/kg/communities")
def kg_communities(only_sector: bool = True):
    """板块族群（最近一次 kg_corr 的 Louvain 结果，按规模降序）。"""
    return get_kg_communities_view(db, only_sector=only_sector)


@router.get("/api/kg/changes")
def kg_changes(date: str = None, change_type: str = None, limit: int = 200):
    """变更日志（周 diff 产物）。默认最近日期。"""
    return get_kg_changes_view(db, date=date, change_type=change_type, limit=limit)


@router.get("/api/kg/locate")
def kg_locate(codes: str = "", group: str = "", min_hits: int = 2,
              top_n: int = 20, order: str = "lift"):
    """
    组合定位：一批股票 → 共同指向的板块（富集倍数/命中数双指标）。
    :param codes: 股票代码，逗号/换行分隔，可不带后缀（自动补 .SH/.SZ/.BJ）
    :param group: 自选分组名（与 codes 二选一；优先 codes）
    :param order: lift=富集倍数降序（默认，找异常聚集的小圈子）| hits=命中数降序（找最大公约数）
    """
    from kg_analysis import locate_sectors
    source = None
    if codes:
        resolved, unresolved = [], []
        for c in codes.replace("，", ",").replace("\n", ",").replace(" ", ",").split(","):
            c = c.strip()
            if not c:
                continue
            node = resolve_kg_node(db, c)
            if node and node["node_type"] == "stock":
                resolved.append(node["code"])
            else:
                unresolved.append(c)
        if not resolved:
            return {"error": f"输入的代码均未在图谱中找到：{unresolved[:10]}"}
        result = locate_sectors(db, resolved, min_hits=min_hits, top_n=top_n, order=order)
        result["unresolved"] = sorted(set(result["unresolved"]) | set(unresolved))
        source = f"codes:{len(resolved)}只"
    elif group:
        group_codes = db.get_custom_group_codes_by_name(group)
        if not group_codes:
            return {"error": f"自选分组 {group!r} 不存在或为空"}
        result = locate_sectors(db, group_codes, min_hits=min_hits, top_n=top_n, order=order)
        source = f"group:{group.strip()}"
    else:
        return {"error": "请传 codes（股票代码列表）或 group（自选分组名）"}
    return {"source": source, **result}


@router.get("/api/kg/locate/groups")
def kg_locate_groups():
    """自选分组列表（组合定位下拉用）。"""
    return get_kg_locate_groups_view(db)


@router.get("/api/kg/graph/projection")
def kg_graph_projection(min_jaccard: float = 0.3):
    """板块投影图：节点=板块（族群着色分组 + 成分数 + 平均关联度），边=成分重叠 Jaccard≥阈值。"""
    return build_projection_graph(db, min_jaccard=min_jaccard)


@router.get("/api/kg/graph/star")
def kg_graph_star(code: str, limit: int = 20):
    """个股星型图：中心=个股，邻接=归属板块（按|ρ|），外圈=联动股（按评分 TopN）。"""
    return build_star_graph(db, code, limit=limit)


@router.get("/api/kg/graph/sector/{code}")
def kg_graph_sector(code: str, limit: int = 30):
    """板块展开图：中心=板块，邻接=成分股（按|ρ| TopN 截断）。"""
    return build_sector_graph(db, code, limit=limit)
