# -*- coding: utf-8 -*-
"""The size cap of the MCP answer: long lists are cut, important rows stay, the summary still counts everything."""
import json
import os
import unittest

from cfe_rebase import mcp_server as srv

# reports of a real run (read-only), not part of the repository
REPORTS = os.environ.get("CFE_REBASE_REAL_REPORTS", "")


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

    def test_a_single_row_over_the_budget_is_trimmed_not_dropped(self):
        # method_diff with method=: 366 lines of hunks used to leave items empty (a real run)
        hunk = {"tag": "replace", "old": [[n, "Старая строка %d " % n + "х" * 60] for n in range(1, 184)],
                "new": [[n, "Новая строка %d " % n + "у" * 60] for n in range(1, 184)], "at_old": 1, "at_new": 1}
        row = {"module": "CommonModules/М/Ext/Module.bsl", "target": "ПолучитьДанные", "status": "changed",
               "sig_changed": True, "sig_old": "Функция ПолучитьДанные(А)", "sig_new": "Функция ПолучитьДанные(А, Б)",
               "old_lines": [10, 400], "new_lines": [12, 420], "hunks": [hunk, dict(hunk)]}
        env = answer([row])
        self.assertEqual(len(env["items"]), 1)
        it = env["items"][0]
        self.assertLessEqual(len(json.dumps(env["items"], ensure_ascii=False)), srv.LIST_MAX_CHARS["items"])
        for k in ("module", "target", "status", "sig_changed", "sig_old", "sig_new", "old_lines", "new_lines"):
            self.assertEqual(it[k], row[k], k)
        self.assertTrue(it["hunks"] and it["hunks"][0]["new"][0] == hunk["new"][0])
        self.assertTrue(any(n.startswith("hunks") for n in it["trimmed"]), it["trimmed"])
        self.assertEqual(env["truncated"]["items"], {"total": 1, "returned": 1, "trimmed": 1})

    def test_a_long_text_is_cut_at_the_front_kept(self):
        row = {"id": 1, "flags": ["X"], "note": "н" * 50000}
        it = answer([row])["items"][0]
        self.assertEqual((it["id"], it["flags"]), (1, ["X"]))
        self.assertTrue(it["note"].startswith("ннн") and it["note"].endswith("…"))
        self.assertIn("note", it["trimmed"][0])

    def test_dead_code_has_its_own_budget(self):
        dead = [{"category": "UNBOUND", "path": "п" * 100} for _ in range(200)]
        env = answer([{"id": 1}], dead)
        self.assertEqual(env["items"], [{"id": 1}])
        self.assertLessEqual(len(json.dumps(env["dead_code"], ensure_ascii=False)), srv.LIST_MAX_CHARS["dead_code"])
        self.assertEqual(env["truncated"]["dead_code"]["total"], 200)
        self.assertNotIn("items", env["truncated"])


def tool_answer(report, rows, shape, keep=None, **sel):
    """The answer of a check tool over report rows, as its body builds it: selection, ranks, the size cap."""
    picked = srv._selected(report, {"extensions": ["Р"]}, {}, rows, shape, sel, None, None, keep)
    return srv._execute("t", {}, {}, lambda run: {"items": picked["items"], "ranks": picked["ranks"],
                                                 "summary": {"rows": len(rows)}})


PAD = "п" * 400  # a heavy row: a few dozen of them are over the budget


class SevereRowsSurvive(unittest.TestCase):
    """Per tool: rows that all carry a flag are cut by severity, not by their place in the report."""

    def assert_kept(self, env, wanted, total):
        self.assertEqual(env["truncated"]["items"]["total"], total)
        self.assertLess(env["truncated"]["items"]["returned"], total)
        self.assertLessEqual(len(json.dumps(env["items"], ensure_ascii=False)), srv.LIST_MAX_CHARS["items"])
        for w in wanted:
            self.assertIn(w, env["items"])

    def test_check_calls(self):
        # a real run: 23 compatible SIGNATURE_CHANGED first in the report, the one TOO_FEW_ARGS and the risks after them
        def row(n, flags, note):
            return {"ext": "Р", "module": "CommonModules/М%d/Ext/Module.bsl" % n, "module_title": "ОбщийМодуль.М%d" % n,
                    "line": n, "caller": "Метод%d" % n, "call": "Вызов%d" % n, "args": 1, "target": "Ц", "flags": flags,
                    "note": note + PAD}
        rows = [row(n, ["SIGNATURE_CHANGED"], "совместимо ") for n in range(60)]
        rows += [row(100 + n, ["SIGNATURE_CHANGED"], "[риск] ") for n in range(8)] + [row(200, ["TOO_FEW_ARGS"], "")]
        env = tool_answer("calls", rows, srv._call_item)
        wanted = [srv._call_item(r, {}) for r in rows[60:]]
        self.assert_kept(env, wanted, len(rows))
        self.assertEqual([it["line"] for it in env["items"]], sorted(it["line"] for it in env["items"]))

    def test_check_queries(self):
        rows = [{"part": "query", "kind": "поле", "sev": "ПРОВЕРИТЬ", "ext": "Р", "file": "F%d.bsl" % n, "line": n,
                 "where": "метод М", "ref": "Х" + PAD, "old": "тоже не разрешается"} for n in range(60)]
        rows.append(dict(rows[0], sev="ОШИБКА?", old="было корректно", ref="Справочник.Удалённый"))
        env = tool_answer("queries", rows, lambda f, _: srv._compact({k: f.get(k) for k in srv.QUERY_FIELDS}))
        self.assert_kept(env, [srv._compact({k: rows[-1].get(k) for k in srv.QUERY_FIELDS})], len(rows))

    def test_check_borrowed(self):
        rows = [{"ext": "Р", "object": "Справочник.С%d" % n, "file": "Catalogs/С%d.xml" % n, "part": "реквизит",
                 "name": "Р%d" % n, "check": "NOT_IN_VENDOR", "detail": PAD} for n in range(60)]
        rows.append(dict(rows[0], check="REMOVED", name="Удалённый"))
        env = tool_answer("borrowed", rows, lambda f, _: dict(f))
        self.assert_kept(env, [rows[-1]], len(rows))

    def test_check_registers(self):
        rows = [{"ext": "Р", "module": "M%d" % n, "method": "Записать", "register": "РегистрСведений.Р%d" % n,
                 "check": "CHANGED", "missing": ["Новое"], "detail": PAD} for n in range(60)]
        rows.append(dict(rows[0], check="REGISTER_REMOVED", missing=[]))
        env = tool_answer("registers", rows, lambda f, _: dict(f))
        self.assert_kept(env, [rows[-1]], len(rows))

    def test_check_reads_compacts_composite_types(self):
        docs = ["cfg:DocumentRef.Документ%d" % n for n in range(60)]
        place = {"path": "C:/x/CommonModules/М/Ext/Module.bsl", "line": 1, "where": "метод М", "table": "Т.Обороты",
                 "fields": ["Поле%d" % n for n in range(30)]}
        rows = [{"ext": "Р", "register": "РегистрНакопления.Р%d" % n, "check": "CHANGED", "sev": "ПРОВЕРИТЬ",
                 "type_changed": [{"part": "dimension", "name": "Регистратор", "old": ", ".join(docs),
                                   "new": ", ".join(docs + ["cfg:DocumentRef.Новый"])}],
                 "reads": [dict(place, line=k) for k in range(12)], "detail": "сменился тип"} for n in range(23)]
        rows.append(dict(rows[0], register="РегистрНакопления.Удалённый", check="REGISTER_REMOVED"))
        env = tool_answer("reads", rows, lambda f, _: srv._read_item(f))
        # 24 rows of ~5k characters each: before, four of them fitted, and in the order of the report
        self.assertGreaterEqual(len(env["items"]), 15)
        self.assertIn("РегистрНакопления.Удалённый", [it["register"] for it in env["items"]])
        it = env["items"][0]
        self.assertEqual(it["type_changed"][0]["added"], ["cfg:DocumentRef.Новый"])
        self.assertEqual((it["type_changed"][0]["old_count"], it["type_changed"][0]["new_count"]), (60, 61))
        self.assertEqual((len(it["reads"]), it["reads_total"]), (srv.READ_PLACES_MAX, 12))
        self.assertEqual((len(it["reads"][0]["fields"]), it["reads"][0]["fields_total"]), (srv.READ_FIELDS_MAX, 30))
        short = srv._read_type({"part": "resource", "name": "Сумма", "old": "xs:decimal, Digits=15",
                                "new": "xs:decimal, Digits=17"})
        self.assertEqual((short["old"], short["new"]), ("xs:decimal, Digits=15", "xs:decimal, Digits=17"))

    def test_list_interceptors(self):
        rows = [{"id": n, "ext": "Р", "module": "M%d" % n, "kind": "После", "target": "Т%d" % n, "ext_method": "Е",
                 "flags": ["DEAD_BINDING"], "notes": [PAD]} for n in range(60)]
        rows += [dict(rows[0], id=100, flags=["MISSING_BASE"]), dict(rows[1], id=101, flags=["SIGNATURE_MISMATCH"])]
        env = tool_answer("interceptors", rows, lambda f, _: dict(f))
        self.assert_kept(env, rows[-2:], len(rows))

    def test_check_work(self):
        rows = [{"ext": "Р", "file": "F%d" % n, "check": "OBJECT_REMOVED", "severity": "info", "detail": PAD}
                for n in range(60)]
        rows.append(dict(rows[0], check="MERGE_MARKER", severity="error"))
        env = tool_answer("lint", rows, lambda f, _: dict(f))
        self.assert_kept(env, [rows[-1]], len(rows))

    def test_delta_puts_gone_last(self):
        rows = [{"ext": "Р", "file": "F%d" % n, "check": "OBJECT_REMOVED", "severity": "info", "detail": PAD}
                for n in range(30)]
        base = [dict(r, check="MERGE_MARKER", severity="error") for r in rows] + rows[:10]
        env = tool_answer("lint", rows[5:], lambda f, _: dict(f), baseline=None)
        self.assertNotIn("delta", env["items"][0])
        picked = srv._selected("lint", {}, {}, rows[5:], lambda f, _: dict(f), {}, None, None)
        self.assertNotIn("key", picked["items"][0])
        # gone errors of the baseline rank after the open rows, even though they are errors
        entries, _ = srv.selection.select("lint", rows[5:], baseline=base)
        self.assertEqual([e["delta"] for e in entries[:25]], ["new"] * 20 + ["kept"] * 5)
        self.assertTrue(all(e["delta"] == "gone" for e in entries[25:]))


@unittest.skipUnless(REPORTS and os.path.isdir(REPORTS), "set CFE_REBASE_REAL_REPORTS to run on real reports")
class RealReports(unittest.TestCase):
    """Real reports of a run (read-only): what the workers lost to the size cap now survives."""

    def report(self, folder):
        return srv.selection.load_report(os.path.join(REPORTS, folder))[1]

    def test_check_calls_keeps_too_few_args_and_every_risk(self):
        data = self.report("check_calls-48e43b2a")
        flagged = [f for f in data["findings"] if f["flags"]]
        env = tool_answer("calls", flagged, srv._call_item)
        risk = [f for f in flagged if "SIGNATURE_CHANGED" in f["flags"] and srv._call_grade(f) == "risk"]
        kept = {(it["line"], it["call"]) for it in env["items"]}
        for f in risk + [f for f in flagged if "TOO_FEW_ARGS" in f["flags"]]:
            self.assertIn((f["line"], f["call"]), kept)

    def test_check_reads_returns_more_rows(self):
        data = self.report("check_reads-a2b42038")
        env = tool_answer("reads", data["findings"], lambda f, _: srv._read_item(f))
        self.assertGreaterEqual(len(env["items"]), 10)  # 0.2.10 returned 5 of 23

    def test_before_after_delta_of_check_calls(self):
        before = [f for f in self.report("check_calls-6d78ed83")["findings"] if f["flags"]]
        work = [f for f in self.report("check_calls-48e43b2a")["findings"] if f["flags"]]
        _, summary = srv.selection.select("calls", work, baseline=before)
        d = summary["delta"]
        self.assertEqual((d["new"] + d["kept"], d["kept"] + d["gone"]), (len(work), len(before)))
        self.assertGreater(d["kept"], 0)
        self.assertGreater(d["gone"], 0)


if __name__ == "__main__":
    unittest.main()
