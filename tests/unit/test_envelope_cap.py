# -*- coding: utf-8 -*-
"""The size cap of the MCP answer: long lists are cut, important rows stay, the summary still counts everything."""
import json
import unittest

from cfe_rebase import mcp_server as srv


def answer(rows, dead=()):
    return srv._execute("t", {}, {}, lambda run: {"summary": {"rows": len(rows)}, "items": list(rows),
                                                 "dead_code": list(dead), "report": {"md": "r.md"}})


class EnvelopeCap(unittest.TestCase):
    def test_small_answer_is_untouched(self):
        rows = [{"id": i, "note": "x"} for i in range(5)]
        env = answer(rows)
        self.assertEqual(env["items"], rows)
        self.assertNotIn("truncated", env)

    def test_long_list_is_cut_within_budget(self):
        rows = [{"id": i, "note": "н" * 200} for i in range(400)]
        env = answer(rows)
        self.assertLessEqual(len(json.dumps(env["items"], ensure_ascii=False)), srv.LIST_MAX_CHARS["items"])
        self.assertEqual(env["truncated"]["items"], {"total": 400, "returned": len(env["items"])})
        self.assertEqual(env["summary"], {"rows": 400})
        self.assertIn("report", env["truncated"]["rest"])

    def test_important_rows_come_first_in_their_own_order(self):
        rows = [{"id": i, "note": "н" * 200} for i in range(400)]
        for i in (5, 390, 399):
            rows[i]["flags"] = ["CONFLICT"]
        rows[200]["old"] = "было корректно"
        rows[300]["result"] = "fail"
        ids = [r["id"] for r in answer(rows)["items"]]
        for i in (5, 200, 300, 390, 399):
            self.assertIn(i, ids)
        self.assertEqual(ids, sorted(ids))

    def test_dead_code_has_its_own_budget(self):
        dead = [{"category": "UNBOUND", "path": "п" * 100} for _ in range(200)]
        env = answer([{"id": 1}], dead)
        self.assertEqual(env["items"], [{"id": 1}])
        self.assertLessEqual(len(json.dumps(env["dead_code"], ensure_ascii=False)), srv.LIST_MAX_CHARS["dead_code"])
        self.assertEqual(env["truncated"]["dead_code"]["total"], 200)
        self.assertNotIn("items", env["truncated"])


if __name__ == "__main__":
    unittest.main()
