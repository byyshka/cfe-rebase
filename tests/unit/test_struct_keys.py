# -*- coding: utf-8 -*-
"""calls STRUCT_KEY_NEW (struct_keys): the vendor method reads a key of a structure parameter in new it did not read in
old, and the extension builds that structure itself without the key.

A real case (11.5.17 -> 11.5.27): мр_ЗаполнитьТабличныйДокументУПДСТНВЭД builds ОпцииПечати as
Новый Структура + Вставить("НомерСформированВСчетеФактуре") and calls the vendor ЗаполнитьРеквизитыШапкиУПД, which
reads ОпцииПечати.ВерсияПостановления1137 in 11.5.27 only; the signature is the same, check_calls said nothing and
printing «УПД с ТНВЭД» failed with «Поле объекта не обнаружено (ВерсияПостановления1137)». The fragments below are
cut from the 11.5.17 / 11.5.27 dumps and the extension.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from collections import defaultdict

from cfe_rebase import bsl, calls, plan, selection, struct_keys

REL = "DataProcessors/ПечатьОбщихФорм/Ext/ManagerModule.bsl"

OLD = """Процедура ЗаполнитьРеквизитыШапкиУПД(ДанныеПечати, СведенияОбОрганизации, ВыборкаКонтрагентов, ОбластьМакета, ВыборкаПоставщиков, ОпцииПечати) Экспорт
	Если ОпцииПечати.НомерСформированВСчетеФактуре Тогда
		ОбластьМакета.Параметры.НомерДокумента = ДанныеПечати.Номер;
	КонецЕсли;
КонецПроцедуры

Процедура ЗаполнитьТабличныйДокументУПД(ТабличныйДокумент, ДанныеДляПечати, ОбъектыПечати) Экспорт
	ОпцииПечати = Новый Структура;
	ОпцииПечати.Вставить("НомерСформированВСчетеФактуре", Истина);
	ЗаполнитьРеквизитыШапкиУПД(Неопределено, Неопределено, Неопределено, Неопределено, Неопределено, ОпцииПечати);
КонецПроцедуры
"""

NEW = """Процедура ЗаполнитьРеквизитыШапкиУПД(ДанныеПечати, СведенияОбОрганизации, ВыборкаКонтрагентов, ОбластьМакета, ВыборкаПоставщиков, ОпцииПечати) Экспорт
	Если ОпцииПечати.НомерСформированВСчетеФактуре Тогда
		ОбластьМакета.Параметры.НомерДокумента = ДанныеПечати.Номер;
	КонецЕсли;
	Если ОпцииПечати.ВерсияПостановления1137 = 8 Тогда
		ОбластьМакета.Параметры.Версия = "8";
	ИначеЕсли ОпцииПечати.ВерсияПостановления1137 = 9 Тогда
		ОбластьМакета.Параметры.Версия = "9";
	КонецЕсли;
КонецПроцедуры

Процедура ЗаполнитьТабличныйДокументУПД(ТабличныйДокумент, ДанныеДляПечати, ОбъектыПечати) Экспорт
	ОпцииПечати = Новый Структура;
	ОпцииПечати.Вставить("НомерСформированВСчетеФактуре", Истина);
	ОпцииПечати.Вставить("ВерсияПостановления1137", ВерсияПостановления1137);
	ЗаполнитьРеквизитыШапкиУПД(Неопределено, Неопределено, Неопределено, Неопределено, Неопределено, ОпцииПечати);
КонецПроцедуры
"""

EXT = """Процедура мр_ЗаполнитьТабличныйДокументУПДСТНВЭД(ТабличныйДокумент, ДанныеДляПечати, ОбъектыПечати) Экспорт
	ОпцииПечати = Новый Структура;
	ОпцииПечати.Вставить("НомерСформированВСчетеФактуре", ДанныеДляПечати.Свойство("НомерСформированВСчетеФактуре"));
	ЗаполнитьРеквизитыШапкиУПД(ДанныеПечати, СведенияОбОрганизации, ВыборкаКонтрагентов, ОбластьШапка, Неопределено, ОпцииПечати);
КонецПроцедуры
"""


class FakeDump(object):
    def __init__(self, root, modules):
        self.root = root
        self.modules = {k: bsl.Module(v) for k, v in modules.items()}

    def module(self, rel):
        return self.modules.get(rel)

    def find_method(self, rel, name):
        mod = self.module(rel)
        return mod.find(name) if mod else None

    def common_modules(self):
        return {r.split("/")[1].casefold(): r.split("/")[1] for r in self.modules if r.startswith("CommonModules/")}


def method(text, name):
    return bsl.Module(text).find(name)


def required(text, name, pos, extra=None):
    mods = {REL: text}
    mods.update(extra or {})
    return struct_keys.VendorKeys().required(FakeDump("x", mods), REL, name, pos)


class VendorSide(unittest.TestCase):

    def test_reads_of_the_parameter(self):
        new = required(NEW, "ЗаполнитьРеквизитыШапкиУПД", 5)
        self.assertEqual(sorted(new), ["версияпостановления1137", "номерсформированвсчетефактуре"])
        self.assertEqual(new["версияпостановления1137"][2], 5)       # the first read line
        self.assertEqual(sorted(required(OLD, "ЗаполнитьРеквизитыШапкиУПД", 5)), ["номерсформированвсчетефактуре"])
        self.assertEqual(required(NEW, "ЗаполнитьРеквизитыШапкиУПД", 4), {})   # ОбластьМакета.Параметры: assigned

    def body(self, text, pos=0):
        return required("Процедура М(П, Б) Экспорт\n%s\nКонецПроцедуры\n" % text, "М", pos)

    def test_guarded_by_svoystvo_is_not_required(self):
        self.assertEqual(self.body('Если П.Свойство("К") И П.К Тогда\n\tА = 1;\nКонецЕсли;'), {})
        self.assertEqual(self.body('П.Свойство("К", Значение);\nА = П.К;'), {})

    def test_zapolneno_is_a_read(self):
        # «Поле объекта не обнаружено» is raised by the read itself, ЗначениеЗаполнено does not guard it
        self.assertEqual(list(self.body("Если ЗначениеЗаполнено(П.К) Тогда\n\tА = 1;\nКонецЕсли;")), ["к"])

    def test_key_put_in_the_body_is_not_required(self):
        self.assertEqual(self.body('П.Вставить("К", 1);\nА = П.К;'), {})
        self.assertEqual(self.body("П.К = 1;\nА = П.К;"), {})

    def test_comparison_in_a_condition_is_not_an_assignment(self):
        self.assertEqual(list(self.body("Если П.К = 1 Тогда\n\tА = 1;\nКонецЕсли;")), ["к"])
        self.assertEqual(list(self.body("Если П = Неопределено Тогда\n\tА = П.К;\nКонецЕсли;")), ["к"])

    def test_reassigned_parameter_says_nothing(self):
        self.assertIsNone(self.body("П = Новый Структура;\nА = П.К;"))

    def test_method_calls_and_other_params_are_not_reads(self):
        self.assertEqual(self.body("А = П.Количество();\nБ.К = 1;\nВ = Б.Х;"), {})
        self.assertEqual(list(self.body("А = П.К.Подчиненный;")), ["к"])

    def test_parameter_passed_on_to_a_module_method(self):
        text = "Процедура М(П) Экспорт\n\tДругой(1, П);\nКонецПроцедуры\n\n" \
               "Процедура Другой(Х, Опции)\n\tА = Опции.Глубокий;\nКонецПроцедуры\n"
        res = required(text, "М", 0)
        self.assertEqual(res["глубокий"][3], "Другой")

    def test_parameter_passed_on_to_a_common_module(self):
        cm = "CommonModules/ПечатьСервер/Ext/Module.bsl"
        res = required("Процедура М(П) Экспорт\n\tПечатьСервер.Вывести(П);\nКонецПроцедуры\n", "М", 0,
                       {cm: "Процедура Вывести(О) Экспорт\n\tА = О.Ключ;\nКонецПроцедуры\n"})
        self.assertEqual(res["ключ"][1:], (cm, 2, "Вывести"))

    def test_depth_is_limited(self):
        text = "Процедура М(П) Экспорт\n\tУр1(П);\nКонецПроцедуры\n\nПроцедура Ур1(П)\n\tУр2(П);\nКонецПроцедуры\n\n" \
               "Процедура Ур2(П)\n\tА = П.Глубоко;\nКонецПроцедуры\n"
        self.assertEqual(required(text, "М", 0), {})

    def test_vendor_puts(self):
        dump = FakeDump("x", {REL: NEW})
        self.assertEqual(struct_keys.vendor_puts(dump, REL, "ВерсияПостановления1137"),
                         ["ЗаполнитьТабличныйДокументУПД:15"])


class ExtensionSide(unittest.TestCase):

    def origin(self, text, target="Цель", pos=0):
        mod = bsl.Module(text)
        caller = mod.methods[0]
        call = [c for c in mod.calls() if c["name"] == target][0]
        span = struct_keys.arg_spans(mod.toks, call["tok"], len(mod.toks))[pos]
        return struct_keys.origin(mod.toks, mod.tok_range(caller)[0], call["tok"], span,
                                  {p["name"].casefold() for p in caller.params})

    def test_literal_and_inserts(self):
        res = self.origin('Процедура П()\n\tС = Новый Структура("А, Б", 1, 2);\n\tС.Вставить("В");\n\tЦель(С);\n'
                          'КонецПроцедуры\n')
        self.assertEqual((res["how"], res["keys"], res["passed_on"], res["line"]), ("local", {"а", "б", "в"}, [], 2))

    def test_inline_constructor(self):
        res = self.origin('Процедура П()\n\tЦель(Новый Структура("А"));\nКонецПроцедуры\n')
        self.assertEqual((res["how"], res["keys"]), ("inline", {"а"}))

    def test_non_literal_keys(self):
        self.assertIsNone(self.origin("Процедура П(Ключи)\n\tС = Новый Структура(Ключи);\n\tЦель(С);\n"
                                      "КонецПроцедуры\n")["keys"])
        self.assertIsNone(self.origin("Процедура П(К)\n\tС = Новый Структура;\n\tС.Вставить(К, 1);\n\tЦель(С);\n"
                                      "КонецПроцедуры\n")["keys"])

    def test_passed_on_before_the_call(self):
        res = self.origin("Процедура П()\n\tС = Новый Структура;\n\tДополнить(С);\n"
                          "\tЗаполнитьЗначенияСвойств(С, Х);\n\tЦель(С);\nКонецПроцедуры\n")
        self.assertEqual(res["passed_on"], [(3, "дополнить", "", 0)])  # ЗаполнитьЗначенияСвойств adds no key

    def test_not_judged(self):
        self.assertEqual(self.origin("Процедура П(С)\n\tЦель(С);\nКонецПроцедуры\n"), "param")
        self.assertEqual(self.origin("Процедура П()\n\tС = Опции();\n\tЦель(С);\nКонецПроцедуры\n"), "value")
        self.assertEqual(self.origin("Процедура П()\n\tЦель(Опции());\nКонецПроцедуры\n"), "value")

    def test_nearest_assignment_wins(self):
        res = self.origin("Процедура П()\n\tС = Опции();\n\tС = Новый Структура;\n\tЦель(С);\nКонецПроцедуры\n")
        self.assertEqual(res["keys"], set())

    def check(self, ext, old=OLD, new=NEW, reported=False):
        mod = bsl.Module(ext)
        caller = mod.methods[0]
        call = [c for c in mod.calls() if c["name"] == "ЗаполнитьРеквизитыШапкиУПД"][0]
        stats = defaultdict(int)
        cfg_old, cfg_new = FakeDump("old", {REL: old}), FakeDump("new", {REL: new})
        t_old = cfg_old.find_method(REL, "ЗаполнитьРеквизитыШапкиУПД")
        t_new = cfg_new.find_method(REL, "ЗаполнитьРеквизитыШапкиУПД")
        rows = struct_keys.check(struct_keys.VendorKeys(), mod, caller, call, cfg_new, REL, t_new, cfg_old, REL,
                                 t_old, stats, reported)
        return rows, stats

    def test_real_call(self):
        rows, _ = self.check(EXT)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["grade"], r["arg"], r["param"], r["var"], r["keys"]),
                         ("error", 6, "ОпцииПечати", "ОпцииПечати", ["ВерсияПостановления1137"]))
        self.assertEqual(r["reads"], [{"key": "ВерсияПостановления1137", "module": "Обработка.ПечатьОбщихФорм."
                                       "МодульМенеджера", "method": "ЗаполнитьРеквизитыШапкиУПД", "line": 5}])
        self.assertEqual(r["vendor_puts"], {"ВерсияПостановления1137": ["ЗаполнитьТабличныйДокументУПД:15"]})
        self.assertIn("ЗаполнитьРеквизитыШапкиУПД:5", r["note"])
        self.assertIn("вендор кладёт ключ сам", r["note"])
        self.assertEqual(r["advice"], "положить ключ в структуру перед вызовом (значение — как у вендора в new)")

    def test_key_put_by_the_extension(self):
        fixed = EXT.replace("\tЗаполнитьРеквизитыШапкиУПД(",
                            '\tОпцииПечати.Вставить("ВерсияПостановления1137", 9);\n\tЗаполнитьРеквизитыШапкиУПД(')
        self.assertEqual(self.check(fixed)[0], [])

    def test_key_read_in_old_too(self):
        self.assertEqual(self.check(EXT, old=NEW)[0], [])

    def test_structure_from_a_parameter_is_counted(self):
        ext = EXT.replace("ОбъектыПечати) Экспорт", "ОбъектыПечати, ОпцииПечати) Экспорт").replace(
            "\tОпцииПечати = Новый Структура;\n", "")
        rows, stats = self.check(ext)
        self.assertEqual((rows, stats["struct_key_unchecked"]), ([], 1))

    def test_structure_passed_elsewhere_is_a_risk(self):
        ext = EXT.replace("\tЗаполнитьРеквизитыШапкиУПД(", "\tДополнитьОпции(ОпцииПечати);\n\tЗаполнитьРеквизитыШапкиУПД(")
        rows, _ = self.check(ext)
        self.assertEqual(rows[0]["grade"], "risk")
        self.assertEqual(rows[0]["passed_on"], [4])

    def test_same_vendor_call_in_another_branch_is_not_passed_on(self):
        call = "ЗаполнитьРеквизитыШапкиУПД(ДанныеПечати, СведенияОбОрганизации, ВыборкаКонтрагентов, ОбластьШапка, " \
               "Неопределено, ОпцииПечати);"
        ext = EXT.replace("\t" + call, "\tЕсли А Тогда\n\t\t" + call + "\n\tИначе\n\t\t" + call + "\n\tКонецЕсли;")
        mod = bsl.Module(ext)
        calls_ = [c for c in mod.calls() if c["name"] == "ЗаполнитьРеквизитыШапкиУПД"]
        cfg_old, cfg_new = FakeDump("old", {REL: OLD}), FakeDump("new", {REL: NEW})
        t_old = cfg_old.find_method(REL, "ЗаполнитьРеквизитыШапкиУПД")
        t_new = cfg_new.find_method(REL, "ЗаполнитьРеквизитыШапкиУПД")
        rows = struct_keys.check(struct_keys.VendorKeys(), mod, mod.methods[0], calls_[1], cfg_new, REL, t_new,
                                 cfg_old, REL, t_old, defaultdict(int))
        self.assertEqual(rows[0]["grade"], "error")

    def test_renamed_parameter_of_a_reported_call_is_skipped(self):
        # a real case: ДобавитьПриход(ГлобальныеПеременные, …) -> ДобавитьПриход(ТекущиеПеременные, …) - another structure
        # at that position, the call already has TOO_MANY_ARGS
        new = NEW.replace("ВыборкаПоставщиков, ОпцииПечати) Экспорт", "ВыборкаПоставщиков, ТекущиеОпции) Экспорт") \
            .replace("ОпцииПечати.НомерСформированВСчетеФактуре Тогда\n\t\tОбластьМакета",
                     "ТекущиеОпции.НомерСформированВСчетеФактуре Тогда\n\t\tОбластьМакета") \
            .replace("Если ОпцииПечати.ВерсияПостановления1137 = 8", "Если ТекущиеОпции.ВерсияПостановления1137 = 8") \
            .replace("ИначеЕсли ОпцииПечати.Версия", "ИначеЕсли ТекущиеОпции.Версия")
        rows, stats = self.check(EXT, new=new, reported=True)
        self.assertEqual((rows, stats["struct_key_reported"]), ([], 1))
        self.assertEqual(len(self.check(EXT, new=new, reported=False)[0]), 1)  # a pure rename: still checked

    def test_copy_of_the_vendor_body_is_reported(self):
        ext = '&Вместо("ЗаполнитьТабличныйДокументУПД")\n' + EXT.replace(
            "мр_ЗаполнитьТабличныйДокументУПДСТНВЭД", "Расш_ЗаполнитьТабличныйДокументУПД")
        rows, _ = self.check(ext)
        self.assertIn("переснять копию по new (port_replace / merge-copy)", rows[0]["advice"])


HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xs="http://www.w3.org/2001/XMLSchema" version="2.20">\n')
DP_UUID = "4d4f485d-0000-0000-0000-0000000000f1"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))


def element(name, uuid, extends=None):
    return HEAD + '<DataProcessor uuid="%s"><Properties>%s<Name>%s</Name>%s</Properties><ChildObjects/>' \
                  '</DataProcessor></MetaDataObject>' % (
                      uuid, "<ObjectBelonging>Adopted</ObjectBelonging>" if extends else "", name,
                      "<ExtendedConfigurationObject>%s</ExtendedConfigurationObject>" % extends if extends else "")


def configuration(name, extension=False):
    return HEAD + '<Configuration uuid="c-%s"><Properties><Name>%s</Name>%s</Properties></Configuration>' \
                  '</MetaDataObject>' % (name, name, "<ConfigurationExtensionPurpose>Customization"
                                                     "</ConfigurationExtensionPurpose>" if extension else "")


class ThroughCalls(unittest.TestCase):
    """calls end to end: the adopted manager module of ПечатьОбщихФорм calls the vendor method without a qualifier."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_struct_keys_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for root, text in (("old", OLD), ("new", NEW)):
            write(os.path.join(self.tmp, root, "Configuration.xml"), configuration("Типовая"))
            write(os.path.join(self.tmp, root, "DataProcessors", "ПечатьОбщихФорм.xml"),
                  element("ПечатьОбщихФорм", DP_UUID))
            write(os.path.join(self.tmp, root, REL), text)
        ext = os.path.join(self.tmp, "ext", "МоёРасширение")
        write(os.path.join(ext, "Configuration.xml"), configuration("МоёРасширение", True))
        write(os.path.join(ext, "DataProcessors", "ПечатьОбщихФорм.xml"), element("ПечатьОбщихФорм", "e-dp", DP_UUID))
        write(os.path.join(ext, REL), EXT)
        out = os.path.join(self.tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            calls.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                        "--config-new", os.path.join(self.tmp, "new"), "--out", out])
        with open(os.path.join(out, "calls.json"), encoding="utf-8") as f:
            self.data = json.load(f)
        with open(os.path.join(out, "calls.md"), encoding="utf-8") as f:
            self.md = f.read()

    def test_finding(self):
        rows = [f for f in self.data["findings"] if f["flags"] == ["STRUCT_KEY_NEW"]]
        self.assertEqual(len(rows), 1)
        f = rows[0]
        self.assertEqual((f["caller"], f["line"], f["call"], f["struct_key"]["keys"]),
                         ("мр_ЗаполнитьТабличныйДокументУПДСТНВЭД", 4, "ЗаполнитьРеквизитыШапкиУПД",
                          ["ВерсияПостановления1137"]))
        self.assertTrue(f["note"].startswith("[ошибка]"))
        self.assertEqual(self.data["stats"]["struct_key_error"], 1)
        self.assertIn("STRUCT_KEY_NEW", self.md)

    def test_selection_and_plan(self):
        f = [f for f in self.data["findings"] if f["flags"] == ["STRUCT_KEY_NEW"]][0]
        self.assertEqual(selection._calls_level(f), "error")
        self.assertEqual(selection._calls_level(dict(f, struct_key=dict(f["struct_key"], grade="risk"))), "risk")
        flags = selection.SPECS["calls"][5](f)
        self.assertEqual(flags, ["STRUCT_KEY_NEW", "error"])
        p = plan.Plan()
        plan._calls(p, {"findings": [f]})
        it = list(p.items.values())[0]
        self.assertEqual((it["groups"], it["sources"]), ({"MUST_FIX"}, ["calls:STRUCT_KEY_NEW"]))
        self.assertIn("положить ключ в структуру", it["what"][0])


if __name__ == "__main__":
    unittest.main()
