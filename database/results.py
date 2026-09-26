# -*- coding: utf-8 -*-
"""每日计算结果域。表归属：concept_strength / stock_attribution。"""

import json
from typing import Dict, List, Optional


class ResultsMixin:
    """板块强度评分与个股归因结果读写"""

    def save_concept_strength(self, records: List[Dict]):
        """全量保存某日板块强度评分，先清理该日旧范围，避免残留板块混入。"""
        if not records:
            return
        calc_dates = sorted({r["calc_date"] for r in records})
        with self._connect() as conn:
            conn.executemany(
                "DELETE FROM concept_strength WHERE calc_date = ?",
                [(d,) for d in calc_dates],
            )
            conn.executemany("""
                    INSERT OR REPLACE INTO concept_strength
                    (calc_date, concept_code, s1_return, s2_breadth, s4_relative,
                     score_1d, score_5d, score_20d, score_final, rank_1d, coherency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [(
                    r["calc_date"], r["concept_code"], r.get("s1_return"),
                    r.get("s2_breadth"), r.get("s4_relative"),
                    r.get("score_1d"), r.get("score_5d"), r.get("score_20d"),
                    r.get("score_final"), r.get("rank_1d"), r.get("coherency")
                ) for r in records])

    def get_sector_rankings(self, calc_date: str, top_n: int = None) -> List[Dict]:
        """获取某日的板块强度排名"""
        with self._connect() as conn:
            sql = """
                SELECT cs.*, tcd.concept_name
                FROM concept_strength cs
                JOIN ths_concept_dict tcd ON cs.concept_code = tcd.concept_code
                WHERE cs.calc_date = ?
                ORDER BY cs.rank_1d
            """
            if top_n:
                sql += f" LIMIT {top_n}"
            cursor = conn.execute(sql, (calc_date,))
            return [dict(row) for row in cursor.fetchall()]

    def save_stock_attribution(self, records: List[Dict]):
        """保存个股归因结果"""
        with self._connect() as conn:
            for r in records:
                conn.execute("""
                    INSERT OR REPLACE INTO stock_attribution
                    (stock_code, calc_date, total_return, top_concept, top_contrib_pct, attribution_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    r["stock_code"], r["calc_date"], r.get("total_return"),
                    r.get("top_concept"), r.get("top_contrib_pct"),
                    json.dumps(r.get("attributions", []), ensure_ascii=False)
                ))

    def get_stock_attribution(self, stock_code: str, calc_date: str) -> Optional[Dict]:
        """获取个股归因结果"""
        with self._connect() as conn:
            cursor = conn.execute("""
                SELECT * FROM stock_attribution
                WHERE stock_code = ? AND calc_date = ?
            """, (stock_code, calc_date))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                result["attributions"] = json.loads(result.get("attribution_json", "[]"))
                return result
            return None
