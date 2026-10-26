# -*- coding: utf-8 -*-
"""lint: a ported work copy against the extension it started from - markers, structure, lost files, BOM and EOL."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import cli, lint
from tests.unit import registry_fixture as fx
from tests.unit.test_registry import run_cli

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "synthetic")
WORK_EXT = "Синт_Основное_11_5_27_01_10_2026_v_1_0"
COMMON = "CommonModules/ЗаказыСервер/Ext/Module.bsl"
OBJECT = "Documents/ЗаказПокупателя/Ext/ObjectModule.bsl"


class Lint(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_lint_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "work")
        shutil.copytree(os.path.join(CORPUS, "work"), self.work)
        self.ext = os.path.join(CORPUS, "ext")

    def path(self, rel):
        return os.path.join(self.work, WORK_EXT, *rel.split("/"))

    def checks(self, ext=True):
        issues, stats = lint.lint(self.work, self.ext if ext else None)
        return sorted((x["check"], x["file"], x["severity"]) for x in issues), stats

    def test_the_hand_port_is_clean(self):
        issues, stats = self.checks()
        self.assertEqual(issues, [])
        self.assertEqual((stats["extensions"], stats["modules"]), (1, 7))
        self.assertNotIn("without_source", stats)  # the renamed work copy is matched by uuid

    def test_merge_markers_and_a_broken_module(self):
        with open(self.path(COMMON), "ab") as fh:
            fh.write(b"\r\n<<<<<<< new\r\n\xd0\x90 = 1;\r\n=======\r\n>>>>>>> copy\r\n\xd0\x9a\xd0\xbe\xd0\xbd\xd0\xb5\xd1\x86"
                     b"\xd0\x9f\xd1\x80\xd0\xbe\xd1\x86\xd0\xb5\xd0\xb4\xd1\x83\xd1\x80\xd1\x8b\r\n")
        found = {c for c, f, _ in self.checks()[0] if f == COMMON}
        self.assertEqual(found, {"MERGE_MARKER", "METHOD_ORPHAN_END"})

    def test_lost_file_bom_and_line_endings(self):
        os.remove(self.path("Documents/ЗаказПокупателя/Forms/ФормаДокумента/Ext/Form/Module.bsl"))
        with open(self.path(COMMON), "rb") as fh:
            data = fh.read()
        with open(self.path(COMMON), "wb") as fh:
            fh.write(data[3:].replace(b"\r\n", b"\n"))
        issues, _ = self.checks()
        self.assertEqual(issues, [("BOM_LOST", COMMON, "error"), ("EOL_LF", COMMON, "error"),
                                  ("FILE_MISSING", "Documents/ЗаказПокупателя/Forms/ФормаДокумента/Ext/Form/Module.bsl",
                                   "error")])
        self.assertEqual([c for c, _, _ in self.checks(ext=False)[0]], [])  # without --ext: structure only

    def test_mixed_line_endings_in_a_crlf_module(self):
        # an edit that wrote some lines with LF only: the module is neither pure CRLF nor pure LF
        with open(self.path(COMMON), "rb") as fh:
            data = fh.read()
        self.assertEqual(lint.eol(data), "CRLF")
        crlf = [n for n in range(len(data)) if data.startswith(b"\r\n", n)]
        with open(self.path(COMMON), "wb") as fh:
            fh.write(data[:crlf[2]] + data[crlf[2] + 1:])  # the third line now ends with a bare LF
        issues, _ = lint.lint(self.work, self.ext)
        self.assertEqual([(x["check"], x["file"], x["line"], x["severity"]) for x in issues],
                         [("EOL_MIXED", COMMON, 3, "error")])
        self.assertIn("смешаны", issues[0]["detail"])

    def test_a_removed_object_is_one_item_to_confirm(self):
        shutil.rmtree(self.path("Documents/ЗаказПокупателя"))
        os.remove(self.path("Documents/ЗаказПокупателя.xml"))
        cfg = os.path.join(self.work, WORK_EXT, "Configuration.xml")
        with open(cfg, "rb") as fh:
            data = fh.read()
        with open(cfg, "wb") as fh:
            fh.write(data.replace("<Document>ЗаказПокупателя</Document>".encode("utf-8"), b""))
        issues, _ = self.checks()
        self.assertEqual(issues, [("OBJECT_REMOVED", "Documents/ЗаказПокупателя", "info")])

    def test_cli_report_and_registry(self):
        out = os.path.join(self.tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            cli.main(["lint", "--work", self.work, "--ext", self.ext, "--out", out])
        self.assertIn("errors: 0", buf.getvalue())
        with open(os.path.join(out, "lint.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["issues"], [])
        reg, task = fx.make(os.path.join(self.tmp, "reg"), db_cfg={"from": fx.FROM, "task_dir": self.tmp,
                                                                     "ext_dump": self.ext, "work": self.work})
        argv, _ = cli.with_registry("lint", ["--db", fx.DB, "--registry", reg])  # no --to: lint needs no release
        self.assertEqual(argv[argv.index("--work") + 1], self.work)
        self.assertEqual(argv[argv.index("--ext") + 1], self.ext)

    def test_out_inside_the_work_copy_is_refused(self):
        with self.assertRaises(SystemExit):
            lint.main(["--work", self.work, "--out", os.path.join(self.work, "out")])

    def test_out_inside_the_source_dumps_is_refused(self):
        out = os.path.join(self.ext, "_lint_out")
        try:
            with self.assertRaises(SystemExit):
                lint.main(["--work", self.work, "--ext", self.ext, "--out", out])
            self.assertFalse(os.path.exists(out))
        finally:
            shutil.rmtree(out, True)

    def test_unknown_preprocessor_instruction_is_an_error(self):
        with open(self.path(COMMON), "ab") as fh:
            fh.write("\r\n#Использовать Библиотека\r\n".encode("utf-8"))
        self.assertEqual([x for x in self.checks()[0] if x[1] == COMMON], [("PREPROC_UNKNOWN", COMMON, "error")])

    def test_a_module_not_in_utf8_is_one_encoding_error(self):
        # cp1251 with the BOM kept: a lenient decode would lose «КонецПроцедуры» and the marker - and report it clean
        with open(self.path(COMMON), "rb") as fh:
            text = fh.read()[3:].decode("utf-8") + "\r\n<<<<<<< new\r\nКонецПроцедуры\r\n"
        with open(self.path(COMMON), "wb") as fh:
            fh.write(lint.BOM + text.encode("cp1251"))
        issues, _ = lint.lint(self.work, self.ext)
        got = [x for x in issues if x["file"] == COMMON]
        self.assertEqual([(x["check"], x["severity"]) for x in got], [("ENCODING", "error")])
        first = next(n for n, s in enumerate(text.split("\n"), 1) if any(ord(ch) > 127 for ch in s))
        self.assertEqual(got[0]["line"], first)
        self.assertIn("не UTF-8", got[0]["detail"])

    def test_a_wrong_ext_is_refused(self):
        # a typo in --ext used to give «without source» and errors: 0 - the comparison silently skipped
        for ext, text in ((os.path.join(self.tmp, "ext-typo"), "не существует"),
                          (os.path.join(CORPUS, "old"), "нет выгрузок расширений")):
            with self.subTest(ext):
                with self.assertRaises(SystemExit) as cm:
                    lint.main(["--work", self.work, "--ext", ext, "--out", os.path.join(self.tmp, "out")])
                self.assertIn(text, str(cm.exception.code))
                self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))

    def test_an_extension_without_source_is_named(self):
        only_print = os.path.join(self.tmp, "ext_print")
        shutil.copytree(os.path.join(self.ext, fx.EXT_PRINT), os.path.join(only_print, fx.EXT_PRINT))
        issues, stats = lint.lint(self.work, only_print)
        self.assertEqual((issues, stats["without_source"], stats["without_source_names"]), ([], 1, [WORK_EXT]))
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            lint.main(["--work", self.work, "--ext", only_print, "--out", os.path.join(self.tmp, "out")])
        self.assertIn("without source in --ext (structure only, files not compared): 1: " + WORK_EXT, buf.getvalue())

    def test_a_form_removed_from_child_objects_is_one_item_to_confirm(self):
        form = "Documents/ЗаказПокупателя/Forms/ФормаСписка"
        shutil.rmtree(self.path(form))
        os.remove(self.path(form + ".xml"))
        issues, _ = self.checks()  # the owner still lists the form: its files are lost
        self.assertEqual(issues, [("FILE_MISSING", form + rel, "error") for rel in
                                  (".xml", "/Ext/Form.xml", "/Ext/Form/Module.bsl")])
        owner = self.path("Documents/ЗаказПокупателя.xml")
        with open(owner, "rb") as fh:
            data = fh.read()
        with open(owner, "wb") as fh:
            fh.write(data.replace("<Form>ФормаСписка</Form>".encode("utf-8"), b""))
        issues, _ = lint.lint(self.work, self.ext)
        self.assertEqual([(x["check"], x["file"], x["severity"]) for x in issues],
                         [("CHILD_REMOVED", form, "info")])
        self.assertIn("файлов: 3", issues[0]["detail"])
        self.assertIn("Documents/ЗаказПокупателя.xml", issues[0]["detail"])

    def test_line_numbers_count_only_cr_and_lf(self):
        # str.splitlines also breaks on U+2028, \x0c, \x1c-\x1e, \x85: every line after them would shift
        with open(self.path(COMMON), "rb") as fh:
            data = fh.read()
        extra = "\r\n// разделители:   \x0c \x1c \x1d \x1e \x85 — внутри одной строки\r\n<<<<<<< new\r\n"
        data += extra.encode("utf-8")
        with open(self.path(COMMON), "wb") as fh:
            fh.write(data)
        marker = data.count(b"\n", 0, data.index(b"<<<<<<<")) + 1
        got = [(x["check"], x["line"]) for x in lint.lint(self.work, self.ext)[0] if x["file"] == COMMON]
        self.assertEqual(got, [("MERGE_MARKER", marker)])
        self.assertEqual(lint.text_lines(b"a\r\nb\rc\n"), ["a", "b", "c"])
        self.assertEqual(lint.text_lines(b""), [])

    def test_only_takes_the_source_name_of_a_renamed_work_copy(self):
        for only in (fx.EXT_MAIN, fx.EXT_MAIN.upper(), WORK_EXT, "40d5f884-2b31-5d2a-9582-a67477d83994"):
            with self.subTest(only):
                issues, stats = lint.lint(self.work, self.ext, only)
                self.assertEqual((issues, stats["extensions"], stats["modules"]), ([], 1, 7))
        self.assertEqual(lint.lint(self.work, None, fx.EXT_MAIN)[1]["extensions"], 0)  # no source: no source name
        self.assertEqual(lint.lint(self.work, self.ext, fx.EXT_PRINT)[1]["extensions"], 0)
        with self.assertRaises(SystemExit) as cm:
            lint.main(["--work", self.work, "--ext", self.ext, "--only", "Нет_Такого", "--out",
                       os.path.join(self.tmp, "out")])
        self.assertIn("нет расширения Нет_Такого", str(cm.exception.code))

    def test_db_without_a_release(self):
        # lint needs no release: a base without cfe_rebase.from and an empty releases list still resolves
        reg, _ = fx.make(os.path.join(self.tmp, "reg"), releases={},
                         db_cfg={"task_dir": self.tmp, "ext_dump": self.ext, "work": self.work})
        argv, _ = cli.with_registry("lint", ["--db", fx.DB, "--registry", reg])
        self.assertEqual((argv[argv.index("--work") + 1], argv[argv.index("--ext") + 1]), (self.work, self.ext))
        code, stdout, stderr = run_cli(["lint", "--db", fx.DB, "--registry", reg])
        self.assertEqual(code, 0, stderr)
        self.assertIn("errors: 0", stdout)

    def test_encoding_by_file_kind(self):
        text = "Процедура А()\r\n\t#Вставка\r\nКонецПроцедуры\r\n"
        self.assertEqual(lint.file_issues("М.bsl", lint.BOM + text.encode("utf-8")),
                         lint.file_issues("М.bsl", text.encode("utf-8")))  # UTF-8 with or without BOM: no ENCODING
        self.assertNotIn("ENCODING", [x["check"] for x in lint.file_issues("М.bsl", text.encode("utf-8"))])
        for rel in ("М.bsl", "Form.xml"):
            self.assertEqual([x["check"] for x in lint.file_issues(rel, text.encode("cp1251"))], ["ENCODING"], rel)
        self.assertEqual(lint.file_issues("Справка.html", text.encode("cp1251")), [])  # other text: lenient


if __name__ == "__main__":
    unittest.main()
