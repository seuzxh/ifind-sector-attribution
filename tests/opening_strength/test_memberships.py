import unittest

from opening_strength.memberships import resolve_memberships
from opening_strength.models import CandidateStock, ThemeMembership, ThemeType


class FakeStore:
    def __init__(self):
        self.requested_codes = []

    def get_concept_names(self):
        return {"700001.TI": "旧行业", "881001.TI": "二级行业",
                "884001.TI": "行业", "885001.TI": "概念", "886001.TI": "扩展概念",
                "885002.TI": "最新有效空主题", "861001.TI": "海外"}

    def get_concept_members_map(self, codes):
        self.requested_codes.append(codes)
        return {
            "700001.TI": [{"stock_code": "600002.SH"}],
            "884001.TI": [{"stock_code": "600001.SH"}, {"stock_code": "600009.SH"}],
            "885001.TI": [{"stock_code": "600001.SH"}, {"stock_code": "600001.SH"}],
            "886001.TI": [{"stock_code": "000001.SZ"}],
            "885002.TI": [],
        }

    def get_latest_member_date(self):
        return "20261001"

    def get_concept_members(self, code):
        raise AssertionError("Must never fall back to an older or per-theme snapshot")


class MembershipTests(unittest.TestCase):
    def test_bulk_latest_memberships_are_authoritative_and_candidate_scoped(self):
        store = FakeStore()
        candidates = tuple(CandidateStock(code, "", ()) for code in
                           ("600002.SH", "600001.SH", "000001.SZ"))
        result = resolve_memberships(store, candidates)
        self.assertEqual(store.requested_codes,
                         [["884001.TI", "885001.TI", "885002.TI", "886001.TI"]])
        self.assertEqual(result.memberships, (
            ThemeMembership("000001.SZ", "886001.TI", "扩展概念", ThemeType.CONCEPT),
            ThemeMembership("600001.SH", "884001.TI", "行业", ThemeType.INDUSTRY),
            ThemeMembership("600001.SH", "885001.TI", "概念", ThemeType.CONCEPT),
        ))
        self.assertEqual(result.unmapped_stock_codes, ("600002.SH",))
        self.assertEqual(result.hub_member_date, "20261001")
        self.assertEqual(result.mapped_count, 2)
        self.assertAlmostEqual(result.coverage_ratio, 2 / 3)

    def test_empty_candidates_have_zero_coverage(self):
        result = resolve_memberships(FakeStore(), ())
        self.assertEqual(result.memberships, ())
        self.assertEqual(result.unmapped_stock_codes, ())
        self.assertEqual(result.mapped_count, 0)
        self.assertEqual(result.coverage_ratio, 0.0)

    def test_unmapped_codes_are_sorted_and_distinct(self):
        candidates = tuple(CandidateStock(code, "", ()) for code in
                           ("600003.SH", "600002.SH", "600003.SH"))
        result = resolve_memberships(FakeStore(), candidates)
        self.assertEqual(result.unmapped_stock_codes, ("600002.SH", "600003.SH"))
        self.assertEqual(result.coverage_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
