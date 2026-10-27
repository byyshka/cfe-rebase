# -*- coding: utf-8 -*-
"""calls: a call into a module of an adopted object the vendor removed (OWNER_REMOVED) or renamed (OWNER_RENAMED).

A real case (УТ 11.5.17 -> 11.5.27): the extension adopted ОбработкаТабличнойЧастиСервер and added its own export function
there; 11.5.27 deleted the module. The form's call resolved to the extension's own method and gave
no finding, a worker read the empty check_calls selection as «вызова нет, не трогать», yet once the adoption is dropped
(borrowed OBJECT_REMOVED) the call does not compile.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import borrow_edit, calls, plan, selection

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" version="2.20">\n')
ADOPTED = "<ObjectBelonging>Adopted</ObjectBelonging>"
VENDOR_CM, VENDOR_DOC = "4d4f485d-0000-0000-0000-0000000000c1", "4d4f485d-0000-0000-0000-0000000000d1"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))


def element(tag, name, uuid, extends=None):
    return HEAD + '<%s uuid="%s"><Properties>%s<Name>%s</Name>%s</Properties><ChildObjects/></%s></MetaDataObject>' % (
        tag, uuid, ADOPTED if extends else "", name,
        "<ExtendedConfigurationObject>%s</ExtendedConfigurationObject>" % extends if extends else "", tag)


def configuration(name, extension=False):
    return HEAD + '<Configuration uuid="c-%s"><Properties><Name>%s</Name>%s</Properties></Configuration>' \
                  '</MetaDataObject>' % (name, name, "<ConfigurationExtensionPurpose>Customization"
                                                     "</ConfigurationExtensionPurpose>" if extension else "")


CALLER = """Процедура Расш_Заполнить(Параметр) Экспорт
\tА = ОбработкаСервер.Своя(Параметр);
\tБ = ОбработкаСервер.Вендорская(Параметр);
\tДокументы.Сверка.Печать(Параметр);
\tДокументы.Сверка.Расш_Своя();
\tВ = ОстаётсяСервер.Вендорская(Параметр);
КонецПроцедуры
"""


class _Owners(unittest.TestCase):
    """old: common module ОбработкаСервер, document Сверка, common module ОстаётсяСервер; new: ОбработкаСервер gone,
    Сверка renamed Сверка2_4 (uuid kept), ОстаётсяСервер as it was. The extension adopts all three, adds its own Своя
    to ОбработкаСервер and Расш_Своя to the manager of Сверка; its own module Расш_Модуль calls them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_calls_owner_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        for root, doc, removed in (("old", "Сверка", False), ("new", "Сверка2_4", True)):
            write(os.path.join(t, root, "Configuration.xml"), configuration("Типовая"))
            if not removed:
                write(os.path.join(t, root, "CommonModules", "ОбработкаСервер.xml"),
                      element("CommonModule", "ОбработкаСервер", VENDOR_CM))
                write(os.path.join(t, root, "CommonModules", "ОбработкаСервер", "Ext", "Module.bsl"),
                      "Функция Вендорская(П) Экспорт\n\tВозврат П;\nКонецФункции\n")
            write(os.path.join(t, root, "CommonModules", "ОстаётсяСервер.xml"),
                  element("CommonModule", "ОстаётсяСервер", "4d4f485d-0000-0000-0000-0000000000e1"))
            write(os.path.join(t, root, "CommonModules", "ОстаётсяСервер", "Ext", "Module.bsl"),
                  "Функция Вендорская(П) Экспорт\n\tВозврат П;\nКонецФункции\n")
            write(os.path.join(t, root, "Documents", doc + ".xml"), element("Document", doc, VENDOR_DOC))
            write(os.path.join(t, root, "Documents", doc, "Ext", "ManagerModule.bsl"),
                  "Процедура Печать(П) Экспорт\nКонецПроцедуры\n")
        e = self.ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(e, "Configuration.xml"), configuration("Расш", True))
        write(os.path.join(e, "CommonModules", "ОбработкаСервер.xml"),
              element("CommonModule", "ОбработкаСервер", "e-cm", VENDOR_CM))
        write(os.path.join(e, "CommonModules", "ОбработкаСервер", "Ext", "Module.bsl"),
              "Функция Своя(П) Экспорт\n\tВозврат Вспомогательная(П);\nКонецФункции\n\n"
              "Функция Вспомогательная(П)\n\tВозврат Вендорская(П);\nКонецФункции\n")
        write(os.path.join(e, "CommonModules", "ОстаётсяСервер.xml"),
              element("CommonModule", "ОстаётсяСервер", "e-cm2", "4d4f485d-0000-0000-0000-0000000000e1"))
        write(os.path.join(e, "Documents", "Сверка.xml"), element("Document", "Сверка", "e-doc", VENDOR_DOC))
        write(os.path.join(e, "Documents", "Сверка", "Ext", "ManagerModule.bsl"),
              "Процедура Расш_Своя() Экспорт\nКонецПроцедуры\n")
        write(os.path.join(e, "CommonModules", "Расш_Модуль.xml"), element("CommonModule", "Расш_Модуль", "e-own"))
        write(os.path.join(e, "CommonModules", "Расш_Модуль", "Ext", "Module.bsl"), CALLER)

    def run_calls(self):
        out = os.path.join(self.tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            calls.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                        "--config-new", os.path.join(self.tmp, "new"), "--out", out])
        with open(os.path.join(out, "calls.json"), encoding="utf-8") as fh:
            return json.load(fh)


class Calls(_Owners):

    def test_flags(self):
        data = self.run_calls()
        got = {(f["module"].split("/")[1], f["line"], f["call"]): f["flags"] for f in data["findings"]}
        self.assertEqual(got, {
            # the extension's own function in the adopted module the vendor deleted: no finding without OWNER_REMOVED
            ("Расш_Модуль", 2, "ОбработкаСервер.Своя"): ["OWNER_REMOVED"],
            ("Расш_Модуль", 3, "ОбработкаСервер.Вендорская"): ["OWNER_REMOVED", "REMOVED"],
            # the vendor method is there under the new name: only the name in the call is wrong
            ("Расш_Модуль", 4, "Документы.Сверка.Печать"): ["OWNER_RENAMED"],
            ("Расш_Модуль", 5, "Документы.Сверка.Расш_Своя"): ["OWNER_RENAMED"],
            # inside the removed module itself: the code goes with the adoption (borrowed OBJECT_REMOVED)
            ("ОбработкаСервер", 6, "Вендорская"): ["REMOVED"],
        })

    def test_notes(self):
        by_line = {f["line"]: f for f in self.run_calls()["findings"] if f["module"].startswith("CommonModules/Расш_")}
        own = by_line[2]
        self.assertEqual(own["layer"], "расширение Расш")
        self.assertEqual(own["owner"], {"flag": "OWNER_REMOVED", "object": "ОбщийМодуль.ОбработкаСервер",
                                        "new_name": None})
        self.assertIn("перенести его в собственный модуль расширения", own["note"])
        self.assertIn("найти, что вендор вызывает вместо него", by_line[3]["note"])
        renamed = by_line[5]
        self.assertEqual(renamed["owner"]["new_name"], "Сверка2_4")
        self.assertIn("заменить Сверка на Сверка2_4", renamed["note"])
        self.assertIn("borrow-edit --rename Документ.Сверка", renamed["note"])

    def test_error_level_and_plan(self):
        findings = [f for f in self.run_calls()["findings"] if f["flags"][0].startswith("OWNER_")]
        self.assertEqual({selection.level("calls", f) for f in findings}, {"error"})
        self.assertEqual(sorted(findings, key=lambda f: selection.rank("calls", f))[0]["flags"][0], "OWNER_REMOVED")
        for f in findings:
            f.pop("caller_live", None)
        p = plan.build({}, {}, {}, {"findings": findings}, {})
        self.assertEqual(p["counts"]["MUST_FIX"], 1)
        self.assertEqual(len(p["items"][0]["what"]), 4)

    def test_the_ported_copy_has_none(self):
        # the port: the own function moved to the extension's own module, the adoption of the removed module dropped,
        # the renamed adoption renamed, the calls rewritten
        shutil.rmtree(os.path.join(self.ext, "CommonModules", "ОбработкаСервер"))
        os.remove(os.path.join(self.ext, "CommonModules", "ОбработкаСервер.xml"))
        os.rename(os.path.join(self.ext, "Documents", "Сверка"), os.path.join(self.ext, "Documents", "Сверка2_4"))
        os.remove(os.path.join(self.ext, "Documents", "Сверка.xml"))
        write(os.path.join(self.ext, "Documents", "Сверка2_4.xml"), element("Document", "Сверка2_4", "e-doc",
                                                                            VENDOR_DOC))
        write(os.path.join(self.ext, "CommonModules", "Расш_Модуль", "Ext", "Module.bsl"),
              CALLER.replace("ОбработкаСервер.Своя", "Своя").replace("\tБ = ОбработкаСервер.Вендорская(Параметр);\n",
                                                                       "").replace("Сверка", "Сверка2_4")
              + "\nФункция Своя(П)\n\tВозврат П;\nКонецФункции\n")
        self.assertEqual(self.run_calls()["findings"], [])


class BorrowEditRemove(_Owners):

    def test_remove_refused_while_a_call_of_the_own_method_stays(self):
        # the adoption cannot be dropped while the call of the extension's own function in it is there
        res = borrow_edit.run(os.path.join(self.tmp, "ext"), remove=["ОбщийМодуль.ОбработкаСервер"], dry_run=True)
        self.assertTrue(res["refused"])
        self.assertEqual([(r["file"], r["line"]) for r in res["refs"]],
                         [("CommonModules/Расш_Модуль/Ext/Module.bsl", 2),
                          ("CommonModules/Расш_Модуль/Ext/Module.bsl", 3)])


if __name__ == "__main__":
    unittest.main()
