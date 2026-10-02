import unittest

from opening_strength.models import PoolSource
from opening_strength.source_pools import (
    IFindSourcePoolProvider, SOURCE_POOL_SPECS, SourcePoolError, resolve_candidates,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_concept_members(self, code, date):
        self.calls.append((code, date))
        return self.responses[code]


def response(names, codes):
    return {"errorcode": 0, "tables": [{"table": {
        "p03473_f003": names, "p03473_f002": codes,
    }}]}


class SourcePoolTests(unittest.TestCase):
    def test_candidates_merge_sources_keep_first_nonempty_name_and_api_rank(self):
        client = FakeClient({
            "883926.TI": response(["港股", "", "首个名称", "重复"],
                                    ["00700.HK", "600001.SH", "000001.SZ", "600001.SH"]),
            "883409.TI": response(["北交股"], ["830001.BJ"]),
            "883910.TI": response(["补充名称", "后续名称", "新股"],
                                    ["600001.SH", "000001.SZ", "600002.SH"]),
        })
        result = resolve_candidates(IFindSourcePoolProvider(client), "20261002")
        self.assertEqual([s.stock_code for s in result],
                         ["600001.SH", "000001.SZ", "830001.BJ", "600002.SH"])
        self.assertEqual(result[0].stock_name, "重复")
        self.assertEqual(result[1].stock_name, "首个名称")
        self.assertEqual(result[0].sources,
                         (PoolSource("high_beta", 2), PoolSource("hot_stock", 1)))
        self.assertEqual(client.calls, [("883926.TI", "20261002"),
                                        ("883409.TI", "20261002"),
                                        ("883910.TI", "20261002")])

    def test_first_nonempty_name_can_come_from_later_pool(self):
        class Provider:
            def resolve(self, spec, date):
                return (("600001.SH", "" if spec.pool_id == "high_beta" else "有效名称"),)

        result = resolve_candidates(Provider(), "20261002")
        self.assertEqual(result[0].stock_name, "有效名称")

    def test_malformed_row_values_block_the_entire_source_pool(self):
        fixtures = [
            ([{"bad": "name"}, "bad row"], ["600001.SH", None]),
            (["有效名称", "坏代码"], ["600001.SH", None]),
            (["有效名称", "坏代码"], ["600001.SH", 600002]),
            (["有效名称", "空代码"], ["600001.SH", ""]),
            (["有效名称", "空白代码"], ["600001.SH", " "]),
            ([{"bad": "name"}], ["600001.SH"]),
            ([None], ["600001.SH"]),
            ([123], ["600001.SH"]),
            (["有效名称", {"bad": "name"}], ["600001.SH", "00700.HK"]),
        ]
        for names, codes in fixtures:
            with self.subTest(names=names, codes=codes):
                provider = IFindSourcePoolProvider(FakeClient({
                    "883926.TI": response(names, codes),
                }))
                with self.assertRaises(SourcePoolError) as caught:
                    resolve_candidates(provider, "20261002", specs=SOURCE_POOL_SPECS[:1])
                self.assertEqual(caught.exception.reason_code, "invalid_row_values")
                self.assertEqual(caught.exception.pool_id, "high_beta")

    def test_malformed_or_empty_pool_has_distinct_reason(self):
        fixtures = [
            ({"errorcode": 0}, "missing_tables"),
            (response(["名称"], ["600001.SH", "600002.SH"]), "field_length_mismatch"),
            (response([], []), "empty_pool"),
            (response(["港股"], ["00700.HK"]), "empty_pool"),
            ({"errorcode": -1, "errmsg": "upstream failed"}, "upstream_error"),
            ({"tables": [{"table": {"p03473_f002": ["600001.SH"]}}]}, "missing_fields"),
        ]
        for payload, reason in fixtures:
            with self.subTest(reason=reason, payload=payload):
                provider = IFindSourcePoolProvider(FakeClient({"883926.TI": payload}))
                with self.assertRaises(SourcePoolError) as caught:
                    provider.resolve(SOURCE_POOL_SPECS[0], "20261002")
                self.assertEqual(caught.exception.reason_code, reason)
                self.assertEqual(caught.exception.pool_id, "high_beta")


if __name__ == "__main__":
    unittest.main()
