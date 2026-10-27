# -*- coding: utf-8 -*-
"""cfe_rebase.selection: levels, stable keys, filters, the before/after delta, accepted findings and the command
`cfe-rebase select`."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import cli, selection as sel


def call(module, caller, call_, flags, line=1, note="", ext="Расш"):
    return {"ext": ext, "module": module, "module_title": "Документ.%s.МодульОбъекта" % module.split("/")[1],
            "line": line, "caller": caller, "call": call_, "flags": flags, "note": note, "target": "Т." + call_}


DOC = "Documents/ЗаказКлиента/Ext/ObjectModule.bsl"
ORDER = "Documents/ЗаказПоставщику/Ext/ObjectModule.bsl"


class Levels(unittest.TestCase):
    def test_calls(self):
        self.assertEqual(sel.level("calls", call(DOC, "А", "Б", ["TOO_FEW_ARGS"])), "error")
        self.assertEqual(sel.level("calls", call(DOC, "А", "Б", ["SIGNATURE_CHANGED"], note="[риск] x")), "risk")
        self.assertEqual(sel.level("calls", call(DOC, "А", "Б", ["SIGNATURE_CHANGED"], note="совместимо")), "info")
        self.assertEqual(sel.level("calls", call(DOC, "А", "Б", ["SIGNATURE_CHANGED", "NOT_EXPORT"])), "error")
        # REMOVED and the argument count before MISSING
        self.assertLess(sel.rank("calls", call(DOC, "А", "Б", ["REMOVED"])),
                        sel.rank("calls", call(DOC, "А", "Б", ["MISSING"])))

    def test_other_reports(self):
        self.assertEqual(sel.level("queries", {"sev": "ОШИБКА?"}), "error")
        self.assertEqual(sel.level("queries", {"sev": "ПРОВЕРИТЬ"}), "info")
        self.assertLess(sel.rank("queries", {"sev": "РИСК", "old": "было корректно"}),
                        sel.rank("queries", {"sev": "РИСК", "old": "тоже не разрешается"}))
        self.assertEqual(sel.level("borrowed", {"check": "REMOVED"}), "error")
        self.assertEqual(sel.level("borrowed", {"check": "NOT_IN_VENDOR"}), "info")
        self.assertEqual(sel.level("reads", {"check": "REGISTER_REMOVED"}), "error")
        self.assertEqual(sel.level("reads", {"check": "CHANGED", "dims_added": ["Х"]}), "risk")
        self.assertEqual(sel.level("reads", {"check": "CHANGED", "recorders_added": ["Х"]}), "info")
        self.assertEqual(sel.level("registers", {"check": "CHANGED", "key_lost": ["Х"]}), "error")
        self.assertEqual(sel.level("registers", {"check": "CHANGED", "missing": ["Х"]}), "risk")
        self.assertEqual(sel.level("interceptors", {"flags": ["DEAD_BINDING", "MISSING_BASE"]}), "error")
        self.assertEqual(sel.level("interceptors", {"flags": ["DEAD_BINDING"]}), "info")
        self.assertEqual(sel.level("interceptors", {"flags": ["CONFLICT"]}), "risk")
        self.assertEqual(sel.level("lint", {"severity": "error"}), "error")

    def test_flags_of_renames_and_deprecated(self):
        # the flags of the uuid renames and of obsolete vendor methods
        self.assertEqual(sel.level("borrowed", {"check": "OBJECT_RENAMED"}), "error")
        self.assertEqual(sel.level("borrowed", {"check": "RENAMED"}), "error")
        self.assertLess(sel.rank("borrowed", {"check": "OBJECT_RENAMED"}),
                        sel.rank("borrowed", {"check": "LIST_CHANGED"}))
        self.assertEqual(sel.level("interceptors", {"flags": ["TARGET_RENAMED"]}), "error")
        self.assertEqual(sel.level("interceptors", {"flags": ["TARGET_UNCALLED", "TARGET_DEPRECATED"]}), "error")
        self.assertEqual(sel.level("interceptors", {"flags": ["CONTROL_MISMATCH"]}), "error")
        self.assertEqual(sel.level("interceptors", {"flags": ["NEW_BASE"]}), "risk")
        self.assertEqual(sel.level("interceptors", {"flags": ["TARGET_DEPRECATED"],
                                                    "deprecated": {"grade": "risk"}}), "risk")
        self.assertEqual(sel.level("interceptors", {"flags": ["TARGET_DEPRECATED"],
                                                    "deprecated": {"grade": "info"}}), "info")
        self.assertEqual(sel.level("calls", call(DOC, "А", "Б", ["CALLS_DEPRECATED"], note="[риск] x")), "risk")
        self.assertEqual(sel.level("calls", call(DOC, "А", "Б", ["CALLS_DEPRECATED", "REMOVED"])), "error")


class Keys(unittest.TestCase):
    def test_key_has_no_line_and_sorted_flags(self):
        a = call(DOC, "А", "Б", ["TOO_FEW_ARGS", "SIGNATURE_CHANGED"], line=10)
        b = call(DOC, "А", "Б", ["SIGNATURE_CHANGED", "TOO_FEW_ARGS"], line=99)
        self.assertEqual(sel.key("calls", a), sel.key("calls", b))
        self.assertEqual(sel.key("calls", a), "Расш|%s|А|Б|SIGNATURE_CHANGED,TOO_FEW_ARGS" % DOC)
        self.assertEqual(set(sel.KEYS), set(sel.SPECS))

    def test_every_report_has_a_key(self):
        for name in sel.SPECS:
            self.assertTrue(sel.key(name, {}).count("|") >= 2, name)


class Filters(unittest.TestCase):
    rows = [call(DOC, "Заполнить", "Б", ["REMOVED"]), call(ORDER, "Провести", "В", ["SIGNATURE_CHANGED"], note="ок"),
            call(DOC, "Записать", "Г", ["SIGNATURE_CHANGED"], note="[риск] y")]

    def pick(self, **kw):
        entries, summary = sel.select("calls", self.rows, sel.Filter(**kw))
        return [e["row"]["call"] for e in sorted(entries, key=lambda e: e["order"])], summary

    def test_module_is_a_substring_of_the_place(self):
        self.assertEqual(self.pick(module="заказклиента")[0], ["Б", "Г"])
        self.assertEqual(self.pick(module="Провести|Записать")[0], ["В", "Г"])
        self.assertEqual(self.pick(module="Documents\\ЗаказПоставщику")[0], ["В"])

    def test_object_is_a_whole_segment(self):
        self.assertEqual(self.pick(object="Документ.ЗаказКлиента")[0], ["Б", "Г"])
        self.assertEqual(self.pick(object="ЗаказКлиента")[0], ["Б", "Г"])
        self.assertEqual(self.pick(object="Заказ")[0], [])  # not a prefix of a name

    def test_flag_and_severity(self):
        self.assertEqual(self.pick(flag=["risk"])[0], ["Г"])
        self.assertEqual(self.pick(flag=["removed", "info"])[0], ["Б", "В"])
        self.assertEqual(self.pick(severity="risk")[0], ["Б", "Г"])
        got, summary = self.pick(severity="error")
        self.assertEqual(got, ["Б"])
        self.assertEqual(summary["filter"], {"severity": "error"})
        self.assertEqual(summary["levels"], {"error": 1, "risk": 0, "info": 0})
        with self.assertRaises(sel.SelectionError):
            sel.Filter(severity="fatal")

    def test_ranked_most_severe_first(self):
        entries, _ = sel.select("calls", self.rows)
        self.assertEqual([e["row"]["call"] for e in entries], ["Б", "Г", "В"])

    def test_reads_keep_only_the_places_of_the_module(self):
        row = {"ext": "Р", "register": "РегистрНакопления.Т", "check": "CHANGED",
               "reads": [{"path": "C:/x/CommonModules/А/Ext/Module.bsl", "where": "метод М"},
                         {"path": "C:/x/CommonModules/Б/Ext/Module.bsl", "where": "метод Н"}]}
        entries, _ = sel.select("reads", [row], sel.Filter(module="CommonModules/Б"))
        self.assertEqual([r["where"] for r in entries[0]["row"]["reads"]], ["метод Н"])
        self.assertEqual(len(row["reads"]), 2)  # the report row itself is untouched


class Delta(unittest.TestCase):
    def test_new_kept_gone_one_to_one(self):
        before = [call(DOC, "А", "Б", ["REMOVED"], line=5), call(DOC, "А", "Б", ["REMOVED"], line=9),
                  call(DOC, "В", "Г", ["MISSING"])]
        after = [call(DOC, "А", "Б", ["REMOVED"], line=7), call(ORDER, "Д", "Е", ["TOO_FEW_ARGS"])]
        entries, summary = sel.select("calls", after, baseline=before)
        self.assertEqual(summary["delta"], {"new": 1, "kept": 1, "gone": 2})
        self.assertEqual(summary["baseline_findings"], 3)
        by = {(e["row"]["call"], e["delta"]) for e in entries}
        self.assertEqual(by, {("Б", "kept"), ("Е", "new"), ("Б", "gone"), ("Г", "gone")})
        # new before kept before gone; the gone rows are the baseline's
        self.assertEqual([e["delta"] for e in entries][:2], ["new", "kept"])
        self.assertTrue(all(e["baseline"] for e in entries if e["delta"] == "gone"))
        self.assertEqual([e["order"] for e in sorted(entries, key=lambda e: e["order"])], [0, 1, 2, 3])

    def test_a_renamed_extension_is_the_same(self):
        before = [call(DOC, "А", "Б", ["REMOVED"], ext="Расш_v1")]
        after = [call(DOC, "А", "Б", ["REMOVED"], ext="Расш_v2")]
        _, summary = sel.select("calls", after, baseline=before, base_exts=["Расш_v1"], cur_exts=["Расш_v2"])
        self.assertEqual(summary["delta"], {"new": 0, "kept": 1, "gone": 0})
        self.assertEqual(summary["renamed_ext"], {"Расш_v1": "Расш_v2"})
        self.assertEqual(sel.ext_map(["А", "Б"], ["В", "Г"]), {})  # two without a pair: not guessed

    def test_accepted(self):
        rows = [call(DOC, "А", "Б", ["REMOVED"]), call(DOC, "В", "Г", ["MISSING"])]
        accepted = {sel.key("calls", rows[0]): "убрано вместе с формой", "нет|такого": "старое"}
        entries, summary = sel.select("calls", rows, accepted=accepted)
        self.assertEqual((summary["accepted"], summary["open"], summary["accepted_unused"]), (1, 1, 1))
        self.assertEqual(entries[-1]["accepted"], "убрано вместе с формой")  # accepted after the open ones


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_sel_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, data):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return path

    def test_load_report_and_accepted(self):
        path = self.write("run/calls.json", {"extensions": ["Расш"], "findings": [call(DOC, "А", "Б", ["REMOVED"])]})
        self.assertEqual(sel.load_report(path)[0], "calls")
        self.assertEqual(sel.load_report(os.path.dirname(path))[0], "calls")
        with self.assertRaises(sel.SelectionError):
            sel.load_report(path, "queries")
        with self.assertRaises(sel.SelectionError):
            sel.load_report(self.write("x/other.json", {}))
        self.assertEqual(sel.load_accepted(self.write("a.json", [{"key": "k", "reason": "r"}])), {"k": "r"})
        self.assertEqual(sel.load_accepted(self.write("b.json", {"k": "r"})), {"k": "r"})
        with self.assertRaises(sel.SelectionError):
            sel.load_accepted(self.write("c.json", ["k"]))

    def test_prepare_root_as_baseline(self):
        # the report folder of prepare holds one subfolder per tool: with the expected report the root is enough
        self.write("prepare/calls/calls.json", {"extensions": ["Расш"], "findings": [call(DOC, "А", "Б", ["REMOVED"])]})
        self.write("prepare/queries/queries.json", {"extensions": ["Расш"], "findings": []})
        root = os.path.join(self.tmp, "prepare")
        name, data = sel.load_report(root, "calls")
        self.assertEqual((name, len(data["findings"])), ("calls", 1))
        self.assertEqual(sel.load_report(root, "queries")[0], "queries")
        with self.assertRaises(sel.SelectionError):  # no expected report: a folder of several is ambiguous
            sel.load_report(root)
        with self.assertRaises(sel.SelectionError):
            sel.load_report(root, "reads")

    def test_select_command(self):
        before = self.write("before/calls.json", {"extensions": ["Расш"], "findings": [
            call(DOC, "А", "Б", ["REMOVED"]), call(DOC, "В", "Г", ["MISSING"])]})
        after = self.write("after/calls.json", {"extensions": ["Расш"], "findings": [
            call(DOC, "А", "Б", ["REMOVED"], line=40), call(ORDER, "Д", "Е", ["TOO_FEW_ARGS"])]})
        accepted = self.write("acc.json", [{"key": sel.key("calls", call(DOC, "А", "Б", ["REMOVED"])),
                                            "reason": "так задумано"}])
        out = os.path.join(self.tmp, "sel")
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            cli.main(["select", "--report", after, "--baseline", os.path.dirname(before), "--accepted", accepted,
                      "--object", "ЗаказКлиента", "--out", out])
        with open(os.path.join(out, "selection.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["summary"]["delta"], {"new": 0, "kept": 1, "gone": 1})
        self.assertEqual((doc["summary"]["accepted"], doc["summary"]["open"]), (1, 0))
        self.assertEqual(doc["summary"]["filter"], {"object": "ЗаказКлиента"})
        self.assertEqual(doc["key_fields"], ["ext", "module", "caller", "call", "flags"])
        self.assertEqual([f["delta"] for f in doc["findings"]], ["kept", "gone"])
        self.assertIn("calls: находок 2, выбрано 2", console.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(out, "selection.md")))

    def test_select_command_refuses_another_report(self):
        after = self.write("after/calls.json", {"extensions": [], "findings": []})
        other = self.write("q/queries.json", {"extensions": [], "findings": []})
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["select", "--report", after, "--baseline", other])
        self.assertIn("нужен calls", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
