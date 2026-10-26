# -*- coding: utf-8 -*-
"""Golden tests on the synthetic corpus (tests/corpus/synthetic; what each method stands for - its README.md).

Every tool runs once per class through main(argv) into a temporary folder. Its JSON report, normalized (corpus paths
relative to the corpus root, the output folder as <out>, timings dropped), must equal expected/<run>.json; the
explicit asserts below state the intent case by case (letters A-N of the README), so a reader sees what matters
without opening the expected files. Expected files change only through regen_expected.py, after reading report.md.

    python -m unittest discover -s tests -t .

CFE_REBASE_LEGACY_TOOLS=<folder with control_rebase.py ...> runs the original standalone scripts (not part of this
repository) instead of the package: the same checks against the code the package was carved from.
"""
import contextlib
import difflib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter, OrderedDict

from .normalize import dumps, load

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "synthetic")
OLD = os.path.join(CORPUS, "old")
NEW = os.path.join(CORPUS, "new")
EXT = os.path.join(CORPUS, "ext")
WORK = os.path.join(CORPUS, "work")
EXPECTED = os.path.join(CORPUS, "expected")
EXT_MAIN = "Синт_Основное"
EXT_PRINT = "Синт_Печать"
WORK_MAIN = "Синт_Основное_11_5_27_01_10_2026_v_1_0"
LEGACY_ENV = "CFE_REBASE_LEGACY_TOOLS"
BOM = b"\xef\xbb\xbf"


def _configs():
    return ["--config-old", OLD, "--config-new", NEW]


# run name -> (module, JSON report file, argv for an output folder)
RUNS = OrderedDict([
    ("control_rebase", ("control_rebase", "report.json",
                        lambda out: ["--ext", EXT, "--old", OLD, "--new", NEW, "--out", out, "--work", WORK])),
    ("control_rebase_work", ("control_rebase", "report.json",
                             lambda out: ["--ext", WORK, "--old", OLD, "--new", NEW, "--out", out])),
    ("method_diff", ("method_diff", "report.json",
                     lambda out: ["--ext", EXT, "--old", OLD, "--new", NEW, "--out", out])),
    ("interceptors", ("interceptors", "interceptors.json",
                      lambda out: ["--ext-root", EXT] + _configs() + ["--out", out])),
    ("calls", ("calls", "calls.json", lambda out: ["--ext-root", EXT] + _configs() + ["--out", out])),
    ("bodies", ("bodies", "bodies.json", lambda out: ["--ext-root", EXT] + _configs() + ["--out", out])),
    ("queries", ("queries", "queries.json", lambda out: ["--ext-root", EXT] + _configs() + ["--out", out])),
])


def run_tool(module, argv):
    """main(argv) of cfe_rebase.<module>, console output swallowed; with CFE_REBASE_LEGACY_TOOLS set - the legacy script
    <that folder>/<module>.py with the same arguments."""
    legacy = os.environ.get(LEGACY_ENV)
    if legacy:
        proc = subprocess.run([sys.executable, os.path.join(legacy, module + ".py")] + list(argv), capture_output=True,
                              env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        if proc.returncode:
            raise RuntimeError("%s failed: %s" % (module, proc.stderr.decode("utf-8", "replace")))
        return
    mod = importlib.import_module("cfe_rebase." + module)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = mod.main(list(argv))
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise RuntimeError("%s exited: %s" % (module, exc.code))
        rc = None
    if rc not in (None, 0):
        raise RuntimeError("%s returned %s" % (module, rc))


def run_all(base):
    """Every run of RUNS into <base>/<run>: {run: (output folder, normalized JSON report)}."""
    res = OrderedDict()
    for name, (module, report, argv) in RUNS.items():
        out = os.path.join(base, name)
        run_tool(module, argv(out))
        res[name] = (out, load(os.path.join(out, report), {"": CORPUS, "<out>": out}))
    return res


# Verdicts of control_rebase on ext/ (README, case B and the rest): (module title, vendor method) -> verdict.
VERDICTS = {
    ("ОбщийМодуль.ЗаказыСервер", "ЗаполнитьРеквизитыЗаказа"): "UNCHANGED",
    ("ОбщийМодуль.ЗаказыСервер", "РассчитатьСуммуЗаказа"): "REBASED",
    ("ОбщийМодуль.ЗаказыСервер", "ПроверитьЗаполнениеЗаказа"): "REBASED",
    ("ОбщийМодуль.ЗаказыСервер", "ОпределитьСклад"): "CONFLICTS",
    ("ОбщийМодуль.ЗаказыСервер", "ЗаписатьПротоколЗаказа"): "FAIL",
    ("ОбщийМодуль.ЗаказыСервер", "ОбработатьСтрокиЗаказа"): "REBASED",
    ("ОбщийМодуль.ЗаказыСервер", "ПересчитатьСкидки"): "MISSING",
    ("ОбщийМодуль.ЗаказыСервер", "КоличествоПозицийЗаказа"): "OUT_OF_SYNC",
    ("ОбщийМодуль.ЗаказыСервер", "РассчитатьВесЗаказа"): "OUT_OF_SYNC",
    ("ОбщийМодуль.ЗаказыКлиентСервер", "ПредставлениеСуммыЗаказа"): "REBASED",
    ("Документ.ЗаказПокупателя.МодульМенеджера", "СоздатьЗаказПоОснованию"): "REBASED",
    ("Документ.ЗаказПокупателя.МодульМенеджера", "ТоварыЗаказа"): "REBASED",
    ("Документ.ЗаказПокупателя.МодульОбъекта", "ЗаполнитьСкладВСтроках"): "REBASED",
    ("Документ.ЗаказПокупателя.Форма.ФормаДокумента", "ПриСозданииНаСервере"): "REBASED",
    ("Документ.ЗаказПокупателя.Форма.ФормаСписка", "ПриСозданииНаСервере"): "REBASED",
}


def newline_of(data):
    crlf = data.count(b"\r\n")
    return "crlf" if crlf and crlf >= data.count(b"\n") - crlf else "lf"


@unittest.skipUnless(shutil.which("git"), "git is not on PATH: control_rebase merges with git merge-file")
class SyntheticCorpus(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cfe_corpus_")
        runs = run_all(cls.tmp)
        cls.out = {n: v[0] for n, v in runs.items()}
        cls.data = {n: v[1] for n, v in runs.items()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------------------------------------
    def rebase_row(self, title, target, run="control_rebase"):
        rows = [r for r in self.data[run]["methods"] if r["module_title"] == title and r["target"] == target]
        self.assertEqual(len(rows), 1, "%s %s.%s" % (run, title, target))
        return rows[0]

    def candidate(self, row, run="control_rebase"):
        with open(os.path.join(self.out[run], *row["file"].split("/")), "rb") as fh:
            return fh.read()

    def candidate_lines(self, row):
        return self.candidate(row)[len(BOM):].decode("utf-8").replace("\r\n", "\n").split("\n")

    def registry(self, ext, title, target, kind=None, source="annotation"):
        rows = [r for r in self.data["interceptors"]["registry"] if r["ext"] == ext and r["module_title"] == title and
                r["target"] == target and r["source"] == source and (kind is None or r["kind"] == kind)]
        self.assertEqual(len(rows), 1, "%s %s.%s %s" % (ext, title, target, kind))
        return rows[0]

    def golden(self, name):
        path = os.path.join(EXPECTED, name + ".json")
        if not os.path.isfile(path):
            self.fail("%s is missing: python -m tests.corpus.regen_expected --write (after reading report.md)" % path)
        with open(path, encoding="utf-8") as fh:
            expected = json.load(fh)
        actual = self.data[name]
        if actual != expected:
            diff = difflib.unified_diff(dumps(expected).splitlines(), dumps(actual).splitlines(),
                                        "expected/%s.json" % name, "actual", lineterm="", n=2)
            self.fail("%s differs from the expected file:\n%s" % (name, "\n".join(list(diff)[:150])))

    # -- golden files ----------------------------------------------------------------------------------------------
    def test_golden_control_rebase(self):
        self.golden("control_rebase")

    def test_golden_control_rebase_work(self):
        self.golden("control_rebase_work")

    def test_golden_method_diff(self):
        self.golden("method_diff")

    def test_golden_interceptors(self):
        self.golden("interceptors")

    def test_golden_calls(self):
        self.golden("calls")

    def test_golden_bodies(self):
        self.golden("bodies")

    def test_golden_queries(self):
        self.golden("queries")

    # -- A: interception kinds -------------------------------------------------------------------------------------
    def test_A_every_interception_kind_is_registered(self):
        kinds = Counter((r["kind"], r["proceed"]) for r in self.data["interceptors"]["registry"]
                        if r["ext"] == EXT_MAIN and r["source"] == "annotation")
        for kind in (("Перед", False), ("После", False), ("Вместо", True), ("Вместо", False),
                     ("ИзменениеИКонтроль", False)):
            self.assertIn(kind, kinds)
        groups = {(g["module_title"], g["target"]): g for g in self.data["method_diff"]["methods"]}
        frozen = groups[("ОбщийМодуль.ЗаказыСервер", "СформироватьПредставлениеЗаказа")]
        self.assertEqual([(i["kind"], i["proceed"]) for i in frozen["interceptors"]], [("Вместо", False)])
        self.assertTrue(frozen["hunks"])
        shared = groups[("ОбщийМодуль.ЗаказыСервер", "ПодготовитьДанныеПечати")]
        self.assertEqual(sorted((i["ext"], i["proceed"]) for i in shared["interceptors"]),
                         [(EXT_MAIN, True), (EXT_PRINT, False)])

    def test_A_frozen_copy_older_than_old(self):
        cases = {(c["ext"], c["target"]): c for c in self.data["bodies"]["cases"]}
        older = cases[(EXT_MAIN, "СформироватьПредставлениеЗаказа")]
        self.assertEqual(older["basis"], "older")
        copy_only = [t for h in older["diff_old_copy"] for _, t in h["b"]]
        self.assertIn('Представление = "Заказ " + Заказ.Номер + " от " + Формат(Заказ.Дата, "ДЛФ=D");', copy_only)
        self.assertEqual([h["class"] for h in older["three_way"]], ["ДРЕЙФ: копия держит текст old"])
        self.assertEqual(cases[(EXT_PRINT, "ПодготовитьДанныеПечати")]["basis"], "old")

    # -- B: every verdict ------------------------------------------------------------------------------------------
    def test_B_verdict_counts(self):
        self.assertEqual(self.data["control_rebase"]["summary"],
                         {"UNCHANGED": 1, "ALREADY_NEW": 0, "REBASED": 9, "CONFLICTS": 1, "FAIL": 1, "OUT_OF_SYNC": 2,
                          "MISSING": 1})
        self.assertEqual(self.data["control_rebase_work"]["summary"],
                         {"UNCHANGED": 1, "ALREADY_NEW": 13, "REBASED": 0, "CONFLICTS": 0, "FAIL": 0,
                          "OUT_OF_SYNC": 0, "MISSING": 0})

    def test_B_verdict_per_method(self):
        got = {(r["module_title"], r["target"]): r["verdict"] for r in self.data["control_rebase"]["methods"]}
        self.assertEqual(got, VERDICTS)

    def test_B_conflict_is_the_line_both_sides_changed(self):
        r = self.rebase_row("ОбщийМодуль.ЗаказыСервер", "ОпределитьСклад")
        self.assertEqual(r["conflicts"], 1)
        block = "\n".join(r["conflict_blocks"][0][1])
        self.assertIn("Склад = Заказ.СкладОтгрузки;", block)
        self.assertIn("Склад = Синт_СкладИзНастроек(Заказ);", block)
        self.assertTrue(r["file"].endswith("ОпределитьСклад.conflicts.bsl"))

    def test_B_fail_merged_header_does_not_fit_new(self):
        r = self.rebase_row("ОбщийМодуль.ЗаказыСервер", "ЗаписатьПротоколЗаказа")
        self.assertEqual((r["conflicts"], r["post"]), (0, "PASS"))
        self.assertEqual(r["header"], "слит (заголовок расширения отличался от old)")
        self.assertEqual(r["sig_candidate_vs_new"], ["параметр 1: Знач True ≠ False"])
        self.assertTrue(r["file"].endswith("ЗаписатьПротоколЗаказа.fail.bsl"))

    def test_B_out_of_sync_unmarked_author_edit(self):
        r = self.rebase_row("ОбщийМодуль.ЗаказыСервер", "РассчитатьВесЗаказа")
        self.assertIn("ПозицияЗаказа.Коэффициент", r["pre"])

    def test_B_missing_method_removed_in_new(self):
        r = self.rebase_row("ОбщийМодуль.ЗаказыСервер", "ПересчитатьСкидки")
        self.assertEqual(r["note"], "old: есть, new: нет метода")
        self.assertNotIn("file", r)

    def test_B_already_new_on_the_work_copy(self):
        verdicts = {(r["module_title"], r["target"]): r["verdict"] for r in self.data["control_rebase_work"]["methods"]}
        self.assertEqual(verdicts.pop(("ОбщийМодуль.ЗаказыСервер", "ЗаполнитьРеквизитыЗаказа")), "UNCHANGED")
        self.assertEqual(set(verdicts.values()), {"ALREADY_NEW"})
        self.assertNotIn(("ОбщийМодуль.ЗаказыСервер", "ПересчитатьСкидки"), verdicts)

    # -- C: markers ------------------------------------------------------------------------------------------------
    def test_C_marker_with_trailing_comment(self):
        r = self.rebase_row("ОбщийМодуль.ЗаказыСервер", "ПроверитьЗаполнениеЗаказа")
        self.assertEqual((r["pre"], r["verdict"]), ("ok", "REBASED"))
        self.assertIn("\t#Вставка // Автор 01.10 склад обязателен для отгрузки", self.candidate_lines(r))

    def test_C_markers_inside_query_text(self):
        r = self.rebase_row("Документ.ЗаказПокупателя.МодульМенеджера", "ТоварыЗаказа")
        self.assertEqual((r["pre"], r["verdict"], r["post"]), ("ok", "REBASED", "PASS"))
        lines = self.candidate_lines(r)
        i = lines.index("\t#Удаление")
        self.assertEqual(lines[i:i + 6], [
            "\t#Удаление", "\t|\tПозицииЗаказа.Количество КАК Количество", "\t#КонецУдаления",
            "\t#Вставка // Автор 01.10 количество с учетом коэффициента",
            "\t|\tПозицииЗаказа.Количество * ПозицииЗаказа.Коэффициент КАК Количество", "\t#КонецВставки"])
        self.assertIn("\t|УПОРЯДОЧИТЬ ПО", lines)  # the vendor change after the markers is carried over

    def test_C_queries_reads_a_query_with_markers_inside(self):
        # Both query texts of Синт_ТоварыЗаказа are read whole: a marker line inside the literal does not end it,
        # so the table after the marker is checked too.
        self.assertEqual(self.data["queries"]["stats"]["query_tables"], 2)

    # -- D: an author line equal to a vendor line is not a duplicate --------------------------------------------------
    def test_D_author_line_equal_to_vendor_line(self):
        r = self.rebase_row("ОбщийМодуль.ЗаказыСервер", "ОбработатьСтрокиЗаказа")
        self.assertEqual(r["verdict"], "REBASED")
        self.assertEqual([s.strip() for s in r["duplicated"]["new"] + r["duplicated"]["copy"]],
                         ["КонецЕсли;", "КонецЕсли;"])
        self.assertEqual(r["duplicated_unexplained"], {"new": [], "copy": []})
        with open(os.path.join(self.out["control_rebase"], "report.md"), encoding="utf-8") as fh:
            self.assertIn("Повторов, объяснённых строками, которые добавил автор (не дубли): 2.", fh.read())

    # -- E: a header on two lines ------------------------------------------------------------------------------------
    def test_E_two_line_header(self):
        r = self.rebase_row("Документ.ЗаказПокупателя.МодульМенеджера", "СоздатьЗаказПоОснованию")
        self.assertEqual((r["verdict"], r["sig_changed"]), ("REBASED", True))
        self.assertEqual(r["header"], "из new с именем расширения")
        lines = self.candidate_lines(r)
        i = lines.index("Функция Синт_СоздатьЗаказПоОснованию(Основание,")
        self.assertEqual(lines[i + 1], "\tДанныеЗаполнения, ПроверятьОстатки = Ложь) Экспорт")

    # -- F: КонецФункции with a comment at the vendor --------------------------------------------------------------
    def test_F_end_comment_at_the_vendor_is_out_of_sync(self):
        # fixes the observed verdict; whether the platform ignores the comment after КонецФункции is not proven
        r = self.rebase_row("ОбщийМодуль.ЗаказыСервер", "КоличествоПозицийЗаказа")
        self.assertEqual(r["verdict"], "OUT_OF_SYNC")
        self.assertIn("«КонецФункции» | «КонецФункции // КоличествоПозицийЗаказа()»", r["pre"])
        self.assertIn("CONTROL_MISMATCH",
                      self.registry(EXT_MAIN, "ОбщийМодуль.ЗаказыСервер", "КоличествоПозицийЗаказа")["flags"])

    # -- G: #Если inside a &ИзменениеИКонтроль body -----------------------------------------------------------------
    def test_G_preprocessor_branch_inside_the_body(self):
        # fixes the observed behaviour: #Если lines are ordinary controlled lines; platform semantics not proven
        r = self.rebase_row("ОбщийМодуль.ЗаказыКлиентСервер", "ПредставлениеСуммыЗаказа")
        self.assertEqual((r["pre"], r["verdict"]), ("ok", "REBASED"))
        lines = self.candidate_lines(r)
        self.assertLess(lines.index("#Если Сервер Тогда"), lines.index("\t#Вставка"))
        self.assertLess(lines.index("\t#КонецВставки"), lines.index("#Иначе"))
        self.assertIn('\tТекст = Формат(Сумма, "ЧДЦ=2; ЧН=0");', lines)

    # -- H: one handler name in two forms ----------------------------------------------------------------------------
    def test_H_same_method_in_two_forms(self):
        files = sorted(r["file"] for r in self.data["control_rebase"]["methods"]
                       if r["target"] == "ПриСозданииНаСервере")
        self.assertEqual(files, [
            EXT_MAIN + "/ПриСозданииНаСервере@Документ.ЗаказПокупателя.Форма.ФормаДокумента.bsl",
            EXT_MAIN + "/ПриСозданииНаСервере@Документ.ЗаказПокупателя.Форма.ФормаСписка.bsl"])

    # -- I: the hand port in a renamed extension ---------------------------------------------------------------------
    def test_I_work_copy_found_by_uuid(self):
        for r in self.data["control_rebase"]["methods"]:
            self.assertEqual(r["work"]["ext"], WORK_MAIN)
            if r["verdict"] in ("REBASED", "UNCHANGED"):
                self.assertTrue(r["work"]["status"].startswith("SAME;"), r["target"])
                self.assertTrue(r["work"]["status"].endswith("контроль work против new: PASS"), r["target"])
        self.assertEqual(self.rebase_row("ОбщийМодуль.ЗаказыСервер", "ПересчитатьСкидки")["work"]["status"],
                         "в work нет &ИзменениеИКонтроль на метод")

    # -- J: candidates keep BOM and line endings of their module -----------------------------------------------------
    def test_J_candidate_bytes(self):
        seen = Counter()
        for r in self.data["control_rebase"]["methods"]:
            if not r.get("file"):
                continue
            with open(os.path.join(EXT, r["ext"], *r["module"].split("/")), "rb") as fh:
                module = fh.read()
            cand = self.candidate(r)
            self.assertEqual(cand.startswith(BOM), module.startswith(BOM), r["file"])
            if newline_of(module) == "crlf":
                self.assertNotIn(b"\n", cand.replace(b"\r\n", b""), r["file"])
            else:
                self.assertNotIn(b"\r", cand, r["file"])
            seen[newline_of(module)] += 1
        self.assertEqual(seen, {"crlf": 12, "lf": 1})

    def test_J_corpus_line_endings_are_as_designed(self):
        def raw(rel):
            with open(os.path.join(EXT, EXT_MAIN, *rel.split("/")), "rb") as fh:
                return fh.read()
        obj = raw("Documents/ЗаказПокупателя/Ext/ObjectModule.bsl")
        self.assertTrue(obj.startswith(BOM))
        self.assertNotIn(b"\r", obj)
        mgr = raw("Documents/ЗаказПокупателя/Ext/ManagerModule.bsl")
        crlf = mgr.count(b"\r\n")
        self.assertEqual((mgr.count(b"\n") - crlf, newline_of(mgr)), (6, "crlf"))
        r = self.rebase_row("Документ.ЗаказПокупателя.МодульМенеджера", "ТоварыЗаказа")
        self.assertNotIn(b"\n", self.candidate(r).replace(b"\r\n", b""))

    # -- K: interceptors flags ---------------------------------------------------------------------------------------
    def test_K_conflict_between_two_extensions(self):
        printing = self.registry(EXT_PRINT, "ОбщийМодуль.ЗаказыСервер", "ПодготовитьДанныеПечати")
        main = self.registry(EXT_MAIN, "ОбщийМодуль.ЗаказыСервер", "ПодготовитьДанныеПечати")
        self.assertEqual((printing["proceed"], printing["flags"]), (False, ["CONFLICT"]))
        self.assertEqual((main["proceed"], main["flags"]), (True, ["CONFLICT", "OVERRIDDEN?"]))

    def test_K_signature_and_base_flags(self):
        def flags(title, target):
            return self.registry(EXT_MAIN, title, target)["flags"]
        zs, form = "ОбщийМодуль.ЗаказыСервер", "Документ.ЗаказПокупателя.Форма.ФормаДокумента"
        self.assertEqual(flags(form, "ЗаполнитьШапкуПоОснованиюНаСервере"), ["DIRECTIVE_MISMATCH"])
        self.assertEqual(flags(zs, "ОбновитьСтатусЗаказа"), ["SIGNATURE_MISMATCH"])
        self.assertEqual(flags(zs, "ЗарегистрироватьИзменениеЗаказа"), ["SIGNATURE_CHANGED"])
        self.assertEqual(flags("Документ.ЗаказПокупателя.МодульОбъекта", "ОбработкаПроверкиЗаполнения"), ["NEW_BASE"])
        self.assertEqual(flags(zs, "ПересчитатьСкидки"), ["MISSING_BASE"])
        self.assertEqual(flags(zs, "РассчитатьСуммуЗаказа"), ["CONTROL_MISMATCH"])
        self.assertEqual(flags(zs, "ЗаполнитьРеквизитыЗаказа"), [])
        self.assertEqual(flags(zs, "ЗаписатьПротоколЗаказа"),
                         ["SIGNATURE_MISMATCH", "SIGNATURE_CHANGED", "CONTROL_MISMATCH"])
        event = self.registry(EXT_MAIN, "Документ.ЗаказПокупателя.МодульОбъекта", "ПриКопировании")
        self.assertEqual((event["flags"], event["notes"]),
                         ([], ["событие платформы без типового обработчика — перехват допустим"]))

    # -- L: Form.xml callType ----------------------------------------------------------------------------------------
    def test_L_calltype_bindings_and_dead_binding(self):
        rows = {(r["owner"], r["event"]): r for r in self.data["interceptors"]["registry"] if r["source"] == "callType"}
        self.assertEqual(sorted(rows),
                         [("<Форма>", "BeforeWrite"), ("<Форма>", "OnOpen"), ("ПокупательЗаказа", "OnChange")])
        on_open, on_change = rows[("<Форма>", "OnOpen")], rows[("ПокупательЗаказа", "OnChange")]
        self.assertEqual((on_open["kind"], on_open["target"], on_open["flags"]), ("После", "ПриОткрытии", []))
        self.assertEqual((on_change["kind"], on_change["target"], on_change["flags"]),
                         ("После", "ПокупательЗаказаПриИзменении", []))
        dead = rows[("<Форма>", "BeforeWrite")]
        self.assertEqual((dead["kind"], dead["ext_method"], dead["flags"]),
                         ("Перед", "Синт_ФормаПередЗаписьюПеред", ["DEAD_BINDING"]))

    # -- M: calls ----------------------------------------------------------------------------------------------------
    def test_M_calls_broken_by_the_update(self):
        found = {(f["module_title"], f["call"]): f for f in self.data["calls"]["findings"]}
        few = found[("ОбщийМодуль.Синт_ЗаказыСервер", "ЗаказыСервер.СуммаПоСтатусу")]
        self.assertEqual(few["flags"], ["TOO_FEW_ARGS"])
        self.assertIn("в old вызов проходил — сломано обновлением", few["note"])
        removed = found[("ОбщийМодуль.Синт_ЗаказыСервер", "ЗаказыСервер.ПолучитьСтавкуСкидки")]
        self.assertEqual(removed["flags"], ["REMOVED"])
        # the same removed function called from the copied vendor body of a &ИзменениеИКонтроль
        in_copy = found[("ОбщийМодуль.ЗаказыСервер", "ПолучитьСтавкуСкидки")]
        self.assertEqual(in_copy["flags"], ["REMOVED"])
        self.assertEqual(len(found), 3)
        # own module: no copy context; the copy: vendor text whose intercepted method is gone in new
        self.assertIsNone(removed["copy"])
        self.assertEqual(in_copy["copy"], {"annotation": '&ИзменениеИКонтроль("ПересчитатьСкидки")',
                                           "target_in_new": False, "vendor_old": True, "vendor_new": False,
                                           "new_as": []})
        self.assertIn("MISSING_BASE", in_copy["advice"])

    # -- N: queries --------------------------------------------------------------------------------------------------
    def test_N_resource_removed_from_the_register(self):
        findings = self.data["queries"]["findings"]
        self.assertEqual([(f["part"], f["kind"], f["ref"], f["old"]) for f in findings],
                         [("query", "поле", "РезервыСклада.КоличествоВРезервеОстаток", "было корректно")])


if __name__ == "__main__":
    unittest.main()
