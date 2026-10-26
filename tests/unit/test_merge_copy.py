# -*- coding: utf-8 -*-
"""merge_copy: port a frozen &Вместо copy onto the new-release body by a three-way merge.

Contract: base = old release body, ours = new release body, theirs = the copy. Author lines survive verbatim,
whitespace-only differences on untouched lines do not produce conflicts, overlapping edits do, and every copy
line that disappears without existing in the old body is reported as possibly lost author work."""
import os
import re
import shutil
import sys
import unittest

from cfe_rebase import merge_copy

NEEDS_GIT = unittest.skipIf(shutil.which("git") is None, "git is not on PATH")


@NEEDS_GIT
class Merge(unittest.TestCase):

    def test_author_insert_and_distant_vendor_edit(self):
        old = ["Функция Ф()", "\tА = 1;", "\tБ = 2;", "\tВ = 3;", "\tГ = 4;", "КонецФункции"]
        new = ["Функция Ф()", "\tА = 1;", "\tБ = 2;", "\tВ = 3;", "\tГ = 40;", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\tА = 1;", "\t// + ЕГ", "\tЕГ = 1;", "\t// -", "\tБ = 2;", "\tВ = 3;",
                "\tГ = 4;", "КонецФункции"]
        res = merge_copy.merge_bodies(old, new, copy)
        self.assertEqual(res["conflicts"], 0)
        self.assertEqual(res["lines"], ["Функция ЕГ_Ф()", "\tА = 1;", "\t// + ЕГ", "\tЕГ = 1;", "\t// -",
                                        "\tБ = 2;", "\tВ = 3;", "\tГ = 40;", "КонецФункции"])

    def test_whitespace_only_drift_does_not_conflict(self):
        # The copy re-indented untouched lines; the vendor changed one of them. Without aligning whitespace
        # every re-indented line is an "author edit" and collides with the vendor change.
        old = ["Функция Ф()", "\t\t\t|\tПоле1,", "\t\t\t|\tПоле2", "КонецФункции"]
        new = ["Функция Ф()", "\t\t\t|\tПоле1,", "\t\t\t|\tПоле2,", "\t\t\t|\tПоле3", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\t\t|\tПоле1,", "\t\t|\tПоле2", "КонецФункции"]
        res = merge_copy.merge_bodies(old, new, copy)
        self.assertEqual(res["conflicts"], 0)
        self.assertEqual(res["lines"], ["Функция ЕГ_Ф()", "\t\t\t|\tПоле1,", "\t\t\t|\tПоле2,",
                                        "\t\t\t|\tПоле3", "КонецФункции"])

    def test_author_lines_kept_verbatim(self):
        old = ["Функция Ф()", "\tА = 1;", "КонецФункции"]
        new = ["Функция Ф()", "\tА = 1;", "\tБ = 2;", "КонецФункции"]
        author = "\tЕГ = 1;  \t"
        copy = ["Функция ЕГ_Ф()", author, "\tА = 1;", "КонецФункции"]
        res = merge_copy.merge_bodies(old, new, copy)
        self.assertIn(author, res["lines"])

    def test_overlapping_edits_conflict(self):
        old = ["Функция Ф()", "\tА = 1;", "КонецФункции"]
        new = ["Функция Ф()", "\tА = 2;", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\tА = 3;", "КонецФункции"]
        res = merge_copy.merge_bodies(old, new, copy)
        self.assertEqual(res["conflicts"], 1)
        text = "\n".join(res["lines"])
        self.assertIn("А = 2;", text)
        self.assertIn("А = 3;", text)
        self.assertRegex(text, re.compile(r"^<{7}", re.M))

    def test_vendor_move_next_to_author_block(self):
        old = ["Функция Ф()", "\tЕсли Не П Тогда", "\t\tР();", "\tКонецЕсли;", "\t// Шапка",
               "\tШК(Ссылка);", "\tП = Ложь;", "\tН = 1;", "\tЕсли А Тогда", "\t\tЗ();", "\tКонецЕсли;",
               "КонецФункции"]
        new = ["Функция Ф()", "\tЕсли Не П Тогда", "\t\tР();", "\tКонецЕсли;", "\tП = Ложь;", "\tН = 1;",
               "\t// Шапка", "\tШК(Ссылка);", "\tЕсли А Тогда", "\t\tЗ();", "\tКонецЕсли;", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\tЕсли Не П Тогда", "\t\tР();", "\tКонецЕсли;", "\t// Шапка",
                "\t// + ЕГ", "\tД = Осн(Ссылка);", "\tЕсли ЗначениеЗаполнено(Д) Тогда", "\t\tШК(Д);", "\tИначе",
                "\tШК(Ссылка);", "\tКонецЕсли;", "\t// -", "\tП = Ложь;", "\tН = 1;", "\tЕсли А Тогда",
                "\t\tЗ();", "\tКонецЕсли;", "КонецФункции"]
        res = merge_copy.merge_bodies(old, new, copy)
        lines = res["lines"]
        if res["conflicts"] == 0:
            self.assertEqual(lines.count("\tП = Ложь;"), 1, lines)
            self.assertLess(lines.index("\tП = Ложь;"), lines.index("\t// Шапка"), lines)
        for a in ["\tД = Осн(Ссылка);", "\t\tШК(Д);", "\t// + ЕГ", "\t// -"]:
            self.assertIn(a, lines)

    def test_preprocessor_block_of_author_stays_whole(self):
        old = ["Функция Ф()", "\tА = 1;", "\tВ = 3;", "\tБ = 2;", "КонецФункции"]
        new = ["Функция Ф()", "\tА = 1;", "\tВ = 3;", "\tБ = 20;", "КонецФункции"]
        block = ["#Если Сервер Тогда", "\tЕГ();", "#КонецЕсли"]
        copy = ["Функция ЕГ_Ф()", "\tА = 1;"] + block + ["\tВ = 3;", "\tБ = 2;", "КонецФункции"]
        res = merge_copy.merge_bodies(old, new, copy)
        self.assertEqual(res["conflicts"], 0)
        self.assertEqual(res["lines"], ["Функция ЕГ_Ф()", "\tА = 1;"] + block + ["\tВ = 3;", "\tБ = 20;",
                                                                               "КонецФункции"])

    def test_repeated_endif_lines(self):
        old = ["Функция Ф()", "\tЕсли А Тогда", "\t\tХ();", "\tКонецЕсли;", "\tЕсли Б Тогда", "\t\tУ();",
               "\tКонецЕсли;", "КонецФункции"]
        new = old[:5] + ["\t\tУ2();"] + old[6:]
        copy = ["Функция ЕГ_Ф()"] + old[1:4] + ["\tЕГ();"] + old[4:]
        res = merge_copy.merge_bodies(old, new, copy)
        self.assertEqual(res["conflicts"], 0)
        self.assertEqual(res["lines"], ["Функция ЕГ_Ф()"] + old[1:4] + ["\tЕГ();"] + new[4:])

    def test_whitespace_only_author_edit_is_not_kept(self):
        # Documented behaviour: in a &Вместо copy only leading/trailing whitespace differs, the old text wins.
        old = ["Функция Ф()", "\tА = 1;", "КонецФункции"]
        new = ["Функция Ф()", "\tА = 1;", "\tБ = 2;", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\t\tА = 1;", "КонецФункции"]
        res = merge_copy.merge_bodies(old, new, copy)
        self.assertEqual(res["lines"], ["Функция ЕГ_Ф()", "\tА = 1;", "\tБ = 2;", "КонецФункции"])


@NEEDS_GIT
class Cli(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ext = os.path.join(self.tmp, "ext")
        rel = os.path.join("CommonModules", "М", "Ext", "Module.bsl")
        self.paths = {k: os.path.join(self.tmp, k, rel) for k in ("old", "new")}
        self.paths["copy"] = os.path.join(self.ext, rel)
        texts = {
            "old": "Функция Ф()\n\tА = 1;\n\tБ = 2;\nКонецФункции\n",
            "new": "Функция Ф()\n\tА = 1;\n\tБ = 20;\nКонецФункции\n",
            "copy": "&Вместо(\"Ф\")\nФункция ЕГ_Ф()\n\tА = 1;\n\tЕГ();\n\tБ = 2;\nКонецФункции\n",
        }
        for k, p in self.paths.items():
            os.makedirs(os.path.dirname(p), exist_ok=True)
            data = texts[k].encode("utf-8")
            if k == "copy":
                data = b"\xef\xbb\xbf" + texts[k].replace("\n", "\r\n").encode("utf-8")
            with open(p, "wb") as fh:
                fh.write(data)
        with open(os.path.join(self.ext, "Configuration.xml"), "w", encoding="utf-8") as fh:
            fh.write("<MetaDataObject/>")

    def run_cli(self, out):
        import subprocess
        return subprocess.run([sys.executable, "-m", "cfe_rebase.merge_copy", "--old", self.paths["old"],
                               "--new", self.paths["new"], "--copy", self.paths["copy"], "--method", "Ф",
                               "--out", out], capture_output=True)

    def snapshot(self):
        out = {}
        for k, p in self.paths.items():
            with open(p, "rb") as fh:
                out[k] = fh.read()
        return out

    def test_writes_body_like_copy_module_and_leaves_sources(self):
        before = self.snapshot()
        out = os.path.join(self.tmp, "out")
        proc = self.run_cli(out)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(self.snapshot(), before)
        bsl_files = [f for f in os.listdir(out) if f.endswith(".bsl")]
        self.assertEqual(len(bsl_files), 1, os.listdir(out))
        with open(os.path.join(out, bsl_files[0]), "rb") as fh:
            data = fh.read()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))
        self.assertIn("\tБ = 20;".encode("utf-8"), data)
        self.assertIn("\tЕГ();".encode("utf-8"), data)
        self.assertTrue(any(f.endswith(".md") for f in os.listdir(out)))

    def test_refuses_out_inside_extension_dump(self):
        before = self.snapshot()
        proc = self.run_cli(os.path.join(self.ext, "merged"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(os.path.exists(os.path.join(self.ext, "merged")))


class LostLines(unittest.TestCase):

    def test_dropped_copy_line_absent_in_old_is_reported(self):
        old = ["Функция Ф()", "\tА = 1;", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\tА = 1;", "\tЕГ = 1;", "КонецФункции"]
        merged = ["Функция ЕГ_Ф()", "\tА = 1;", "КонецФункции"]
        self.assertEqual(merge_copy.lost_lines(old, copy, merged), ["\tЕГ = 1;"])

    def test_dropped_vendor_line_is_not_reported(self):
        old = ["Функция Ф()", "\tА = 1;", "\tСтарое();", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\tА = 1;", "\tСтарое();", "КонецФункции"]
        merged = ["Функция ЕГ_Ф()", "\tА = 1;", "\tНовое();", "КонецФункции"]
        self.assertEqual(merge_copy.lost_lines(old, copy, merged), [])


class Control(unittest.TestCase):

    def test_new_vendor_line_dropped_from_result_is_reported(self):
        old = ["Функция Ф()", "\tА = 1;", "КонецФункции"]
        new = ["Функция Ф()", "\tА = 1;", "\tНовоеВендора();", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\tА = 1;", "КонецФункции"]
        merged = ["Функция ЕГ_Ф()", "\tА = 1;", "КонецФункции"]
        ctl = merge_copy.control(old, new, copy, merged)
        self.assertEqual(ctl["dropped_vendor_new"], ["\tНовоеВендора();"])

    def test_author_replacement_of_old_text_is_not_vendor_loss(self):
        old = ["Функция Ф()", "\tА = Полное;", "КонецФункции"]
        new = ["Функция Ф()", "\tА = Полное;", "\tБ = 2;", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\tА = Краткое;", "КонецФункции"]
        merged = ["Функция ЕГ_Ф()", "\tА = Краткое;", "\tБ = 2;", "КонецФункции"]
        ctl = merge_copy.control(old, new, copy, merged)
        self.assertEqual(ctl["dropped_vendor_new"], [])
        self.assertIn("\tА = Полное;", ctl["left_new"])


class Duplicates(unittest.TestCase):
    """Resolving a conflict by one side must not leave a line more times than new or the copy has it."""

    MERGED = ["Функция ЕГ_Ф()", "<<<<<<< new", "||||||| old", "\tШК();", "=======", "\tЕГ();", "\tШК();",
              ">>>>>>> copy", "\tШК();", "КонецФункции"]

    def test_resolve_takes_one_side_of_every_conflict(self):
        self.assertEqual(merge_copy.resolve(self.MERGED, "new"), ["Функция ЕГ_Ф()", "\tШК();", "КонецФункции"])
        self.assertEqual(merge_copy.resolve(self.MERGED, "copy"),
                         ["Функция ЕГ_Ф()", "\tЕГ();", "\tШК();", "\tШК();", "КонецФункции"])

    def test_side_that_repeats_a_vendor_line_is_reported(self):
        new = ["Функция Ф()", "\tШК();", "КонецФункции"]
        copy = ["Функция ЕГ_Ф()", "\tЕГ();", "\tШК();", "КонецФункции"]
        dup = merge_copy.duplicated(new, copy, self.MERGED)
        self.assertEqual(dup, {"new": [], "copy": ["\tШК();"]})

    @NEEDS_GIT
    def test_stale_copy_with_vendor_move(self):
        # git reads the move as "the header went down": the conflict holds the author block with the old header,
        # and the header is inserted once more below it. Taking the copy side prints the barcode twice.
        old = ["Функция Ф()", "\tЕсли Не П Тогда", "\t\tР();", "\tКонецЕсли;", "\t// Шапка", "\tШК(Ссылка);",
               "\tП = Ложь;", "\tН = 1;", "\tЕсли А Тогда", "\t\tЗ();", "\tКонецЕсли;", "КонецФункции"]
        new = old[:4] + old[6:8] + old[4:6] + old[8:]
        copy = ["Функция ЕГ_Ф()"] + old[1:5] + ["\t// + ЕГ", "\tД = Осн(Ссылка);", "\tЕсли ЗначениеЗаполнено(Д) Тогда",
                                                "\t\tШК(Д);", "\tИначе", "\tШК(Ссылка);", "\tКонецЕсли;", "\t// -"] + old[6:]
        res = merge_copy.merge_bodies(old, new, copy)
        ctl = merge_copy.control(old, new, copy, res["lines"])
        self.assertIn("\tШК(Ссылка);", ctl["duplicated"]["copy"])


class InlineDiff(unittest.TestCase):
    """Report aid: inside a conflict, what exactly differs in a line. [-…-] only in new, {+…+} only in the copy."""

    def test_changed_character(self):
        self.assertEqual(merge_copy.inline_diff("Б = 2;", "Б = 3;"), "Б = [-2-]{+3+};")

    def test_author_insertions_in_a_call(self):
        self.assertEqual(
            merge_copy.inline_diff("ДанныеПечати = ПолучитьДанные(М, П);",
                                   "ДанныеПечати = ЕГ_ПолучитьДанные(М, П, Истина);"),
            "ДанныеПечати = {+ЕГ_+}ПолучитьДанные(М, П{+, Истина+});")

    def test_conflict_lines_are_paired(self):
        merged = ["Функция ЕГ_Ф()", "<<<<<<< new", "\tА = 2;", "||||||| old", "\tА = 1;", "=======", "\tА = 3;",
                  "\tЕГ();", ">>>>>>> copy", "КонецФункции"]
        self.assertEqual(merge_copy.conflict_diffs(merged), [["А = [-2-]{+3+};", "{+ЕГ();+}"]])

    def test_empty_side_shows_whole_lines(self):
        self.assertEqual(merge_copy.conflict_diffs(Duplicates.MERGED), [["{+ЕГ();+}", "{+ШК();+}"]])

    def test_lone_shared_letter_between_changes_is_absorbed(self):
        self.assertEqual(merge_copy.inline_diff("ЛОЖЬ КАК Маркируемая,", "&Опр КАК Маркируемая,"),
                         "[-ЛОЖЬ-]{+&Опр+} КАК Маркируемая,")

    def test_no_common_run_marks_whole_line(self):
        self.assertEqual(merge_copy.inline_diff("|, ТаблицаСоединения", "|ЛЕВОЕ СОЕДИНЕНИЕ Х"),
                         "[-|, ТаблицаСоединения-]{+|ЛЕВОЕ СОЕДИНЕНИЕ Х+}")

    def test_lines_are_paired_by_similarity_not_position(self):
        merged = ["<<<<<<< new", "\tЛОЖЬ КАК Маркируемая,", "\tТ.Упаковка", "||||||| old", "=======",
                  "\tТ.Упаковка,", "\t&Опр КАК Маркируемая,", "\tВЫБОР", ">>>>>>> copy"]
        self.assertEqual(merge_copy.conflict_diffs(merged),
                         [["[-ЛОЖЬ-]{+&Опр+} КАК Маркируемая,", "Т.Упаковка{+,+}", "{+ВЫБОР+}"]])

    def test_dissimilar_lines_are_not_paired(self):
        merged = ["<<<<<<< new", "|, ТаблицаСоединения", "||||||| old", "=======",
                  "|\t\tЛЕВОЕ СОЕДИНЕНИЕ Справочник.Х КАК Д", ">>>>>>> copy"]
        self.assertEqual(merge_copy.conflict_diffs(merged),
                         [["[-|, ТаблицаСоединения-]", "{+|\t\tЛЕВОЕ СОЕДИНЕНИЕ Справочник.Х КАК Д+}"]])


class AlignWhitespace(unittest.TestCase):

    def test_equal_after_strip_takes_old_text(self):
        old = ["\t\t|\tПоле", "\tА = 1;"]
        copy = ["\t|\tПоле", "\tЕГ = 1;", "\tА = 1;"]
        self.assertEqual(merge_copy.align_whitespace(old, copy), ["\t\t|\tПоле", "\tЕГ = 1;", "\tА = 1;"])


def diff3(new, old, copy):
    return ["<<<<<<< new"] + new + ["||||||| old"] + old + ["======="] + copy + [">>>>>>> copy"]


class WhitespaceRules(unittest.TestCase):

    def test_sides_equal_up_to_whitespace_take_new(self):
        merged = ["А;"] + diff3(["\tБ  = 1;"], ["\tБ = 0;"], ["\tБ = 1;"]) + ["В;"]
        lines, resolved = merge_copy.auto_resolve(merged)
        self.assertEqual(lines, ["А;", "\tБ  = 1;", "В;"])
        self.assertEqual(resolved, [{"rule": "ws_identical", "line": 2}])

    def test_vendor_changed_only_whitespace_take_the_copy(self):
        merged = diff3(["\t|\tТ.Склад  КАК Склад,"], ["\t|\tТ.Склад КАК Склад,"], ["\t|\tТ.Цех КАК Склад,"])
        lines, resolved = merge_copy.auto_resolve(merged)
        self.assertEqual((lines, resolved[0]["rule"]), (["\t|\tТ.Цех КАК Склад,"], "vendor_ws_only"))

    def test_author_changed_only_whitespace_take_new(self):
        merged = diff3(["\tА = 2;"], ["\tА = 1;"], ["\tА  =  1;"])
        lines, resolved = merge_copy.auto_resolve(merged)
        self.assertEqual((lines, resolved[0]["rule"]), (["\tА = 2;"], "author_ws_only"))

    def test_a_real_conflict_stays(self):
        merged = ["А;"] + diff3(["\tБ = 2;"], ["\tБ = 1;"], ["\tБ = 3;"])
        self.assertEqual(merge_copy.auto_resolve(merged), (merged, []))

    def test_whitespace_inside_a_string_literal_is_a_change(self):
        # "x  y" -> "x y" is another value, not whitespace: each rule used to take one side and drop the other
        for merged in (diff3(['\tА = "x y";', "\tБ = 1;"], ['\tА = "x  y";', "\tБ = 1;"],
                             ['\tА = "x  y";', "\tБ = 2;"]),
                       diff3(['\tР = " ";'], ['\tР = "\t";'], ['\tР = "\t"; Б = 2;']),          # a tab in a literal
                       diff3(['\tА = "x y";'], ['\tА = 0;'], ['\tА = "x  y";']),                # sides differ in it
                       diff3(['\tА = 2;'], ['\tА = "x y";'], ['\tА = "x  y";']),                # the author's literal
                       diff3(['\t|ГДЕ Т.Имя = ""x y""'], ['\t|ГДЕ Т.Имя = ""x  y""'], ['\t|ГДЕ Т.Код = ""x  y""'])):
            self.assertEqual(merge_copy.auto_resolve(merged), (merged, []), merged)  # a string inside a query text

    def test_whitespace_outside_literals_still_resolves(self):
        merged = diff3(['\tА  =  "x  y";', "\tБ = 1;"], ['\tА = "x  y";', "\tБ = 1;"], ['\tА = "x  y";', "\tБ = 2;"])
        lines, resolved = merge_copy.auto_resolve(merged)
        self.assertEqual((lines, resolved[0]["rule"]), (['\tА = "x  y";', "\tБ = 2;"], "vendor_ws_only"))
        # query text: whitespace between its words does not count, the opening line of the query included
        merged = diff3(['\tТекст = "ВЫБРАТЬ  Т.Ссылка'], ['\tТекст = "ВЫБРАТЬ Т.Ссылка'], ['\tТекст = "ВЫБРАТЬ Т.Код'])
        lines, resolved = merge_copy.auto_resolve(merged)
        self.assertEqual((lines, resolved[0]["rule"]), (['\tТекст = "ВЫБРАТЬ Т.Код'], "vendor_ws_only"))

    @NEEDS_GIT
    def test_vendor_literal_edit_is_not_lost_by_the_merge(self):
        old = ["Процедура П()", '\tА = "x  y";', "\tБ = 1;", "КонецПроцедуры"]
        new = ["Процедура П()", '\tА = "x y";', "\tБ = 1;", "КонецПроцедуры"]
        copy = ["Процедура П()", '\tА = "x  y";', "\tБ = 2;", "КонецПроцедуры"]
        res = merge_copy.merge_bodies(old, new, copy, auto=True)
        self.assertEqual((res["conflicts"], res["auto"]), (1, []))
        self.assertIn('\tА = "x y";', res["lines"])


class ConflictKinds(unittest.TestCase):

    def test_same_point_insertions(self):
        merged = diff3(["\tВендор();"], [""], ["\tАвтор();"]) + diff3(["\tА = 2;"], ["\tА = 1;"], ["\tА = 3;"])
        self.assertEqual(merge_copy.conflict_kinds(merged), [{"kind": "same_point", "backport": False},
                                                             {"kind": "overlap", "backport": False}])
        self.assertIn("порядок решает человек", merge_copy.kind_label({"kind": "same_point", "backport": False}))

    def test_backport_insertion(self):
        vendor = ["\tКолонки.Добавить(\"СуммаНаАванс\");", "\tКолонки.Добавить(\"НДСНаАванс\");"]
        merged = diff3(vendor, [], list(reversed(vendor)))
        self.assertEqual(merge_copy.conflict_kinds(merged), [{"kind": "same_point", "backport": True}])
        self.assertIn("бэкпорт", merge_copy.kind_label(merge_copy.conflict_kinds(merged)[0]))


# Real ERP pair and a ported copy (Documents/РасходныйОрдерНаТовары/Ext/ManagerModule.bsl of each), not part of the
# repository: the test runs only when the three paths are given, and its numbers belong to the author's own copy.
REAL = {
    "old": os.environ.get("CFE_REBASE_REAL_OLD", ""),
    "new": os.environ.get("CFE_REBASE_REAL_NEW", ""),
    "copy": os.environ.get("CFE_REBASE_REAL_COPY", ""),
}


def _function(path, name):
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    start = next(i for i, s in enumerate(lines) if re.match(r"Функция " + name + r"\(", s))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("КонецФункции"))
    return lines[start:end + 1]


@NEEDS_GIT
@unittest.skipUnless(all(os.path.exists(p) for p in REAL.values()),
                     "set CFE_REBASE_REAL_OLD, CFE_REBASE_REAL_NEW, CFE_REBASE_REAL_COPY to run on real modules")
class RealErpPrintForm(unittest.TestCase):

    def test_erp_2_5_22_to_2_5_27(self):
        old = _function(REAL["old"], "ПечатьРасходногоОрдераНаТовары")
        new = _function(REAL["new"], "ПечатьРасходногоОрдераНаТовары")
        copy = _function(REAL["copy"], "ЕГ_ПечатьРасходногоОрдераНаТовары")
        res = merge_copy.merge_bodies(old, new, copy)
        # Conflicts only where the author and the vendor edited the same query spots: the author's Артикул field
        # and join against the vendor's marking placeholders. Plain git merge style coalesces the two neighbouring
        # conflicts of the first query into one (2 in total); diff3 keeps them apart with the old section (3).
        self.assertEqual(res["conflicts"], 3)
        ours = []
        for s in res["lines"]:
            if s.startswith("<<<<<<<"):
                ours = []
            elif s.startswith("|||||||"):
                self.assertTrue(any("МаркируемаяПродукция" in x or "ТаблицаСоединения" in x for x in ours), ours)
            else:
                ours.append(s)
        self.assertEqual(merge_copy.lost_lines(old, copy, res["lines"]), [])
        ctl = merge_copy.control(old, new, copy, res["lines"])
        self.assertEqual(ctl["dropped_vendor_new"], [])
        self.assertEqual(ctl["duplicated"], {"new": [], "copy": []})
        diffs = merge_copy.conflict_diffs(res["lines"])
        self.assertEqual(len(diffs), res["conflicts"])
        self.assertTrue(all(diffs), diffs)
        old_norm = {" ".join(s.split()) for s in old}
        merged_norm = {" ".join(s.split()) for s in res["lines"]}
        author = [s for s in copy[1:] if s.strip() and " ".join(s.split()) not in old_norm]
        self.assertEqual([s for s in author if " ".join(s.split()) not in merged_norm], [])


if __name__ == "__main__":
    unittest.main()
