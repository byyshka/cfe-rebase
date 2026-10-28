# -*- coding: utf-8 -*-
"""A vendor form handler replaced in the extension's Form.xml by callType="Override" without ПродолжитьВызов is a
frozen copy of the vendor body exactly like &Вместо: bodies (check_copies), method_diff and the plan compare it with the
vendor handler. Own elements and commands of the extension, Before / After, wrappers with ПродолжитьВызов and events
the vendor binds to no handler are not copies.

Synthetic dumps first; then a real port (УТ 11.5.22.159 -> 11.5.27.79, an extension that replaces two handlers of the
vendor form ПечатьЭтикетокИЦенников.ФормаТовары by callType="Override"), run only when CFE_REBASE_REAL_CALLTYPE_BEFORE /
CFE_REBASE_REAL_CALLTYPE_WORK (extension dumps root before and after porting) and CFE_REBASE_REAL_UT_11_5_22 /
CFE_REBASE_REAL_UT_11_5_27 (vendor dumps) point to existing folders."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import bodies, interceptors, mcp_server as srv, merge_copy, method_diff, plan
from tests.unit.test_borrowed import HEAD, obj, write

FORM_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<Form xmlns="http://v8.1c.ru/8.3/xcf/logform" ' \
            'xmlns:v8="http://v8.1c.ru/8.1/data/core" version="2.20">\n'
FORM_REL = ("Documents", "Заказ", "Forms", "ФормаДокумента", "Ext")
MODULE_REL = "Documents/Заказ/Forms/ФормаДокумента/Ext/Form/Module.bsl"

VENDOR_FORM = (
    '<ChildItems>'
    '<InputField name="Контрагент" id="1"><Events><Event name="OnChange">КонтрагентПриИзменении</Event></Events>'
    '</InputField>'
    '<InputField name="Склад" id="2"><Events><Event name="OnChange">СкладПриИзменении</Event></Events></InputField>'
    '<InputField name="Комментарий" id="3"/>'
    '</ChildItems>'
    '<Events><Event name="OnOpen">ПриОткрытии</Event>'
    '<Event name="OnCreateAtServer">ПриСозданииНаСервере</Event></Events>'
    '<Commands><Command name="Заполнить" id="1"><Action>Заполнить</Action></Command></Commands>')

EXT_FORM = (
    '<ChildItems>'
    '<InputField name="Контрагент" id="1"><Events><Event name="OnChange">КонтрагентПриИзменении</Event>'
    '<Event name="OnChange" callType="Before">Расш_КонтрагентПриИзмененииПеред</Event></Events></InputField>'
    '<InputField name="Склад" id="2"><Events><Event name="OnChange">СкладПриИзменении</Event>'
    '<Event name="OnChange" callType="Override">Расш_СкладПриИзмененииВместо</Event></Events></InputField>'
    '<InputField name="Комментарий" id="3"><Events>'
    '<Event name="DragStart" callType="Override">Расш_КомментарийНачалоПеретаскиванияВместо</Event></Events>'
    '</InputField>'
    '</ChildItems>'
    '<Events><Event name="OnOpen" callType="Override">Расш_ПриОткрытииВместо</Event>'
    '<Event name="OnCreateAtServer" callType="Override">Расш_ПриСозданииНаСервереВместо</Event></Events>'
    '<Commands><Command name="Заполнить" id="1"><Action callType="After">Расш_ЗаполнитьПосле</Action></Command>'
    '<Command name="МояКоманда" id="1000000"><Action callType="Override">Расш_МояКомандаВместо</Action></Command>'
    '</Commands>')

VENDOR_OLD = """&НаСервере
Процедура ПриСозданииНаСервере(Отказ, СтандартнаяОбработка)
	Заголовок = "Заказ";
	Объект.Флаг = Истина;
КонецПроцедуры

&НаКлиенте
Процедура ПриОткрытии(Отказ)
	Модифицированность = Ложь;
КонецПроцедуры

&НаКлиенте
Процедура КонтрагентПриИзменении(Элемент)
	КонтрагентПриИзмененииНаСервере();
КонецПроцедуры

&НаКлиенте
Процедура СкладПриИзменении(Элемент)
	Склад = Неопределено;
КонецПроцедуры

&НаКлиенте
Процедура Заполнить(Команда)
	ЗаполнитьНаСервере();
КонецПроцедуры
"""
NEW_LINE = '\tИспользоватьСерии = ПолучитьФункциональнуюОпцию("ИспользоватьСерии");\n'
VENDOR_NEW = VENDOR_OLD.replace('\tОбъект.Флаг = Истина;\n', NEW_LINE + '\tОбъект.Флаг = Истина;\n') \
    .replace("\tКонтрагентПриИзмененииНаСервере();\n",
             "\tОчиститьСообщения();\n\tКонтрагентПриИзмененииНаСервере();\n") \
    .replace("\tСклад = Неопределено;\n", "\tОчиститьСообщения();\n\tСклад = Неопределено;\n") \
    .replace("\tЗаполнитьНаСервере();\n", "\tОчиститьСообщения();\n\tЗаполнитьНаСервере();\n")

EXT_MODULE = """&НаСервере
Процедура Расш_ПриСозданииНаСервереВместо(Отказ, СтандартнаяОбработка)
	Заголовок = "Заказ";
	Объект.Флаг = Истина;
	Элементы.Расш_Поле.Видимость = Истина;
КонецПроцедуры

&НаКлиенте
Процедура Расш_ПриОткрытииВместо(Отказ)
	Модифицированность = Ложь;
	Расш_Подсказка = "";
КонецПроцедуры

&НаКлиенте
Процедура Расш_КонтрагентПриИзмененииПеред(Элемент)
	Расш_Флаг = Истина;
КонецПроцедуры

&НаКлиенте
Процедура Расш_СкладПриИзмененииВместо(Элемент)
	Расш_Флаг = Истина;
	ПродолжитьВызов(Элемент);
КонецПроцедуры

&НаКлиенте
Процедура Расш_ЗаполнитьПосле(Команда)
	Расш_Флаг = Ложь;
КонецПроцедуры

&НаКлиенте
Процедура Расш_МояКомандаВместо(Команда)
	Расш_Флаг = Истина;
КонецПроцедуры

&НаКлиенте
Процедура Расш_КомментарийНачалоПеретаскиванияВместо(Элемент, ПараметрыПеретаскивания, Выполнение)
	Выполнение = Ложь;
КонецПроцедуры
"""
EXT_MODULE_PORTED = EXT_MODULE.replace('\tОбъект.Флаг = Истина;\n', NEW_LINE + '\tОбъект.Флаг = Истина;\n', 1)


def quiet(fn, argv):
    with contextlib.redirect_stdout(io.StringIO()):
        fn(argv)


class CallTypeCopies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_calltype_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        for root, module in (("old", VENDOR_OLD), ("new", VENDOR_NEW)):
            write(os.path.join(t, root, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties><Name>'
                  'Типовая</Name></Properties></Configuration></MetaDataObject>' % root)
            write(os.path.join(t, root, "Documents", "Заказ.xml"), obj("Document", "Заказ", []))
            write(os.path.join(t, root, *FORM_REL, "Form.xml"), FORM_HEAD + VENDOR_FORM + "</Form>")
            write(os.path.join(t, root, *FORM_REL, "Form", "Module.bsl"), module)
        for root, module in (("ext", EXT_MODULE), ("work", EXT_MODULE_PORTED)):
            ext = os.path.join(t, root, "Расш")
            write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш'
                  '</Name><ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose></Properties>'
                  '</Configuration></MetaDataObject>')
            write(os.path.join(ext, "Documents", "Заказ.xml"), obj("Document", "Заказ", [], True))
            write(os.path.join(ext, *FORM_REL, "Form.xml"), FORM_HEAD + EXT_FORM + '<BaseForm version="2.20">' +
                  VENDOR_FORM + "</BaseForm></Form>")
            write(os.path.join(ext, *FORM_REL, "Form", "Module.bsl"), module)
        self.old, self.new = os.path.join(t, "old"), os.path.join(t, "new")

    def run_bodies(self, root):
        out = os.path.join(self.tmp, "bodies-" + root)
        quiet(bodies.main, ["--ext-root", os.path.join(self.tmp, root), "--config-old", self.old, "--config-new",
                            self.new, "--out", out, "--code-only"])
        with open(os.path.join(out, "bodies.json"), encoding="utf-8") as fh:
            return json.load(fh)["cases"], out

    def test_only_override_copies_of_vendor_handlers(self):
        cases, _ = self.run_bodies("ext")
        self.assertEqual(sorted(c["copy_method"] for c in cases),
                         ["Расш_ПриОткрытииВместо", "Расш_ПриСозданииНаСервереВместо"])
        for c in cases:
            self.assertEqual((c["binding"], c["kind"], c["module"]), ("callType", "Вместо (callType)", MODULE_REL))
        c = next(c for c in cases if c["copy_method"] == "Расш_ПриСозданииНаСервереВместо")
        self.assertEqual((c["target"], c["owner"], c["event"]), ("ПриСозданииНаСервере", "<Форма>", "OnCreateAtServer"))
        self.assertEqual(c["form"], "Documents/Заказ/Forms/ФормаДокумента/Ext/Form.xml")

    def test_stale_copy_fails_ported_passes(self):
        res = srv.check_copies([os.path.join(self.tmp, "ext")], self.old, self.new, os.path.join(self.tmp, "o1"))
        got = {it["copy_method"]: it for it in res["items"]}
        stale = got["Расш_ПриСозданииНаСервереВместо"]
        self.assertEqual((stale["result"], stale["basis"], stale["kind"]), ("fail", "old", "Вместо (callType)"))
        self.assertEqual([m["new"] for m in stale["missing"]], [[4, 4]])
        self.assertEqual((stale["owner"], stale["event"]), ("<Форма>", "OnCreateAtServer"))
        self.assertTrue(os.path.isfile(stale["form"]))
        self.assertEqual(got["Расш_ПриОткрытииВместо"]["result"], "pass")  # the vendor did not change ПриОткрытии
        self.assertEqual((res["summary"]["copies"], res["summary"]["calltype"], res["summary"]["fail"]), (2, 2, 1))
        work = srv.check_copies([os.path.join(self.tmp, "work")], self.old, self.new, os.path.join(self.tmp, "o2"))
        self.assertEqual({it["copy_method"]: it["result"] for it in work["items"]},
                         {"Расш_ПриСозданииНаСервереВместо": "pass", "Расш_ПриОткрытииВместо": "pass"})

    def test_report_names_the_binding(self):
        _, out = self.run_bodies("ext")
        with open(os.path.join(out, "bodies.md"), encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("ПриСозданииНаСервере (callType <Форма>.OnCreateAtServer)", md)
        self.assertIn("Привязка: `Вместо (callType)` <Форма>.OnCreateAtServer", md)

    def test_method_diff_groups(self):
        out = os.path.join(self.tmp, "md")
        quiet(method_diff.main, ["--ext", os.path.join(self.tmp, "ext"), "--old", self.old, "--new", self.new,
                                 "--out", out])
        with open(os.path.join(out, "report.json"), encoding="utf-8") as fh:
            groups = {g["target"]: g for g in json.load(fh)["methods"]}
        # only the two copies: no Before / After / own command / wrapper / event without a vendor handler
        self.assertEqual(sorted(groups), ["ПриОткрытии", "ПриСозданииНаСервере"])
        g = groups["ПриСозданииНаСервере"]
        self.assertEqual([(i["kind"], i["method"], i["event"]) for i in g["interceptors"]],
                         [("Вместо (callType)", "Расш_ПриСозданииНаСервереВместо", "<Форма>.OnCreateAtServer")])
        self.assertEqual([[k for k, _ in h["new"]] for h in g["hunks"]], [[4]])
        self.assertEqual(groups["ПриОткрытии"]["status"], "тело не изменилось")
        with open(os.path.join(out, "report.md"), encoding="utf-8") as fh:
            self.assertIn("Расш Вместо (callType) → Расш_ПриСозданииНаСервереВместо", fh.read())
        res = srv.method_diff(os.path.join(self.tmp, "ext"), self.old, self.new, os.path.join(self.tmp, "md2"),
                              method="ПриСозданииНаСервере")
        self.assertEqual([(it["target"], it["status"]) for it in res["items"]], [("ПриСозданииНаСервере", "changed")])

    def test_plan_must_fix(self):
        cases, _ = self.run_bodies("ext")
        p = plan.build({"methods": []}, {"cases": cases}, {"registry": []}, {"findings": []}, {"findings": []})
        must = [x for x in p["items"] if x["group"] == "MUST_FIX"]
        self.assertEqual([x["method"] for x in must], ["Расш_ПриСозданииНаСервереВместо"])
        self.assertIn("копия Вместо (callType <Форма>.OnCreateAtServer → ПриСозданииНаСервере): вендор изменил "
                      "оригинал", must[0]["what"][0])
        self.assertIn("copy_method=Расш_ПриСозданииНаСервереВместо", must[0]["what"][0])
        self.assertEqual(p["hidden"], {"bodies:оригинал копии не менялся": 1})

    def test_registry_note(self):
        out = os.path.join(self.tmp, "icp")
        quiet(interceptors.main, ["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", self.old,
                                  "--config-new", self.new, "--out", out])
        with open(os.path.join(out, "interceptors.json"), encoding="utf-8") as fh:
            rows = {r["ext_method"]: r for r in json.load(fh)["registry"]}
        self.assertTrue(any("check_copies" in n for n in rows["Расш_ПриСозданииНаСервереВместо"]["notes"]))
        for name in ("Расш_ПриОткрытииВместо", "Расш_СкладПриИзмененииВместо", "Расш_МояКомандаВместо",
                     "Расш_ЗаполнитьПосле"):
            self.assertFalse([n for n in rows[name]["notes"] if "check_copies" in n], name)

    def test_port_replace_with_copy_method(self):
        # no annotation names the target: port_replace finds the copy by copy_method
        mod = MODULE_REL.split("/")
        out = os.path.join(self.tmp, "merge")
        quiet(merge_copy.main, ["--old", os.path.join(self.old, *mod), "--new", os.path.join(self.new, *mod),
                                "--copy", os.path.join(self.tmp, "ext", "Расш", *mod), "--method",
                                "ПриСозданииНаСервере", "--copy-method", "Расш_ПриСозданииНаСервереВместо",
                                "--out", out])
        with open(os.path.join(out, "merge_copy_Расш_ПриСозданииНаСервереВместо.json"), encoding="utf-8") as fh:
            rep = json.load(fh)
        self.assertEqual(rep["conflicts"], 0)
        with open(os.path.join(out, "merge_copy_Расш_ПриСозданииНаСервереВместо.bsl"), encoding="utf-8-sig") as fh:
            merged = fh.read()
        self.assertIn(NEW_LINE.strip(), merged)
        self.assertIn("Элементы.Расш_Поле.Видимость = Истина;", merged)
        self.assertTrue(merged.startswith("Процедура Расш_ПриСозданииНаСервереВместо("))


REAL = {k: os.environ.get("CFE_REBASE_REAL_" + k) for k in ("CALLTYPE_BEFORE", "CALLTYPE_WORK", "UT_11_5_22",
                                                              "UT_11_5_27")}
REAL_FORM = "Обработка.ПечатьЭтикетокИЦенников.Форма.ФормаТовары"


@unittest.skipUnless(all(v and os.path.isdir(v) for v in REAL.values()),
                     "CFE_REBASE_REAL_CALLTYPE_BEFORE / _CALLTYPE_WORK / _UT_11_5_22 / _UT_11_5_27 are not set")
class RealCallType(unittest.TestCase):
    def check(self, root):
        out = tempfile.mkdtemp(prefix="cfe_calltype_real_")
        self.addCleanup(shutil.rmtree, out, True)
        res = srv.check_copies([root], REAL["UT_11_5_22"], REAL["UT_11_5_27"], out)
        self.assertEqual(res["status"], "ok")
        # callType copies of the vendor form, by the vendor handler they replace
        return {it["target"]: it for it in res["items"]
                if it["module_title"] == REAL_FORM and it.get("kind") == "Вместо (callType)"}

    def test_before(self):
        got = self.check(REAL["CALLTYPE_BEFORE"])
        # only the two vendor handlers: the extension's own command with callType="Override" is not a copy
        self.assertEqual(sorted(got), ["ПриОткрытии", "ПриСозданииНаСервере"])
        stale = got["ПриСозданииНаСервере"]
        self.assertEqual((stale["result"], stale["basis"]), ("fail", "old"))
        # ЭтаФорма -> ЭтотОбъект twice, ИспользоватьСерииНоменклатуры instead of ПравоРегистрации…, the new
        # СообщитьОПроблемахЗаполненияСерийНоменклатуры block (11.5.27 module lines)
        self.assertEqual([m["new"] for m in stale["missing"]], [[63, 63], [70, 70], [73, 73], [156, 161]])
        self.assertEqual(got["ПриОткрытии"]["result"], "pass")

    def test_method_diff(self):
        out = tempfile.mkdtemp(prefix="cfe_calltype_real_md_")
        self.addCleanup(shutil.rmtree, out, True)
        res = srv.method_diff(REAL["CALLTYPE_BEFORE"], REAL["UT_11_5_22"], REAL["UT_11_5_27"], out,
                              method="ПриСозданииНаСервере")
        self.assertEqual([(it["status"], [i["kind"] for i in it["interceptors"]])
                          for it in res["items"] if it["module_title"] == REAL_FORM],
                         [("changed", ["Вместо (callType)"])])

    def test_work(self):
        got = self.check(REAL["CALLTYPE_WORK"])
        self.assertEqual({t: it["result"] for t, it in got.items()},
                         {"ПриСозданииНаСервере": "pass", "ПриОткрытии": "pass"})


if __name__ == "__main__":
    unittest.main()
