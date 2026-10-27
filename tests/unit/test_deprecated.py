# -*- coding: utf-8 -*-
"""Obsolete vendor methods: bsl.Module.deprecated, TARGET_DEPRECATED / TARGET_UNCALLED of interceptors,
CALLS_DEPRECATED of calls and their place in the plan, on small hand-written dumps."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import bsl, calls, interceptors, plan
from tests.unit.test_borrowed import HEAD, obj, write

ACCOUNTS_OLD = "\n".join([
    "#Область ПрограммныйИнтерфейс",
    "",
    "// Возвращает счёт по умолчанию.",
    "Функция СчетПоУмолчанию(Организация) Экспорт",
    "\tВозврат Неопределено;",
    "КонецФункции",
    "",
    "#КонецОбласти",
])
ACCOUNTS_NEW = "\n".join([
    "#Область ПрограммныйИнтерфейс",
    "",
    "Функция СчетПоУмолчаниюНовый(Параметры) Экспорт",
    "\tВозврат Неопределено;",
    "КонецФункции",
    "",
    "#Область УстаревшиеПроцедурыИФункции",
    "",
    "// Устарела. Следует использовать СчетПоУмолчаниюНовый.",
    "Функция СчетПоУмолчанию(Организация, Валюта = Неопределено) Экспорт",
    "\tВозврат СчетПоУмолчаниюНовый(Организация);",
    "КонецФункции",
    "",
    "#КонецОбласти",
    "",
    "#КонецОбласти",
])
PAYMENTS_OLD = "\n".join([
    "Процедура Заполнить(Объект) Экспорт",
    "\tОбъект.Счет = Справочники.Счета.СчетПоУмолчанию(Объект.Организация);",
    "\tОбъект.Счет2 = Справочники.Счета.СчетПоУмолчанию (Объект.Организация);",
    "\t// Справочники.Счета.СчетПоУмолчанию(Объект.Организация);",
    "\tОбъект.Счет3 = ЗначениеНастроек.СчетПоУмолчанию(Объект.Организация);",
    "\tТекст = \"Справочники.Счета.СчетПоУмолчанию(\";",
    "КонецПроцедуры",
])
PAYMENTS_NEW = "\n".join([
    "Процедура Заполнить(Объект) Экспорт",
    "\tОбъект.Счет = Справочники.Счета.СчетПоУмолчаниюНовый(Объект.Организация);",
    "\tОбъект.Счет3 = ЗначениеНастроек.СчетПоУмолчанию(Объект.Организация);",
    "КонецПроцедуры",
])
MONEY_OLD = "\n".join([
    "Функция Параметры() Экспорт",
    "\tВозврат Новый Структура;",
    "КонецФункции",
    "",
    "#Область УстаревшиеПроцедурыИФункции",
    "Процедура СтароеСообщение(Текст) Экспорт",
    "КонецПроцедуры",
    "#КонецОбласти",
])
MONEY_NEW = "\n".join([
    "// Устарела. Возвращает параметры вызова функции СчетПоУмолчанию.",
    "//",
    "// Возвращаемое значение:",
    "//   Структура",
    "Функция Параметры() Экспорт",
    "\t//++ Устарело_Переработка24",
    "\tВозврат Новый Структура;",
    "КонецФункции",
    "",
    "#Область УстаревшиеПроцедурыИФункции",
    "Процедура СтароеСообщение(Текст) Экспорт",
    "КонецПроцедуры",
    "#КонецОбласти",
])
EXT_ACCOUNTS = "\n".join([
    "&Вместо(\"СчетПоУмолчанию\")",
    "Функция Расш_СчетПоУмолчанию(Организация)",
    "\tВозврат Неопределено;",
    "КонецФункции",
])
EXT_MODULE = "\n".join([
    "Процедура Заполнить(Объект) Экспорт",
    "\tП = Деньги.Параметры();",
    "\tДеньги.СтароеСообщение(\"x\");",
    "КонецПроцедуры",
    "",
    "Процедура ПриЗаписи() Экспорт",
    "\tЗаполнить(ЭтотОбъект);",
    "КонецПроцедуры",
])
# ПриЗаписи is the handler of a subscription: Заполнить runs, its finding stays MUST_FIX (code nothing live calls goes
# to REVIEW)
EXT_SUBSCRIPTION = ('<EventSubscription uuid="es"><Properties><Name>Расш_ПриЗаписи</Name>'
                    '<Handler>CommonModule.Расш_Модуль.ПриЗаписи</Handler></Properties></EventSubscription>'
                    '</MetaDataObject>')


class ModuleDeprecated(unittest.TestCase):

    def test_region_and_header_comment(self):
        new = bsl.Module(ACCOUNTS_NEW)
        self.assertEqual(new.deprecated(new.find("СчетПоУмолчанию")), "область УстаревшиеПроцедурыИФункции")
        self.assertIsNone(new.deprecated(new.find("СчетПоУмолчаниюНовый")))
        old = bsl.Module(ACCOUNTS_OLD)
        self.assertIsNone(old.deprecated(old.find("СчетПоУмолчанию")))
        money = bsl.Module(MONEY_NEW)
        self.assertEqual(money.deprecated(money.find("Параметры")),
                         "комментарий «Устарела. Возвращает параметры вызова функции СчетПоУмолчанию.»")

    def test_marker_inside_a_body_is_not_a_header(self):
        mod = bsl.Module("Функция А() Экспорт\n\t//++ Устарело_Переработка24\nКонецФункции\n\n"
                         "// Устарело_Переработка24\nФункция Б() Экспорт\nКонецФункции")
        self.assertIsNone(mod.deprecated(mod.find("А")))
        self.assertIsNone(mod.deprecated(mod.find("Б")))


class Dumps(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_deprecated_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        for root, acc, pay, money in (("old", ACCOUNTS_OLD, PAYMENTS_OLD, MONEY_OLD),
                                      ("new", ACCOUNTS_NEW, PAYMENTS_NEW, MONEY_NEW)):
            base = os.path.join(t, root)
            write(os.path.join(base, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties><Name>'
                  'Типовая</Name></Properties></Configuration></MetaDataObject>' % root)
            write(os.path.join(base, "Catalogs", "Счета.xml"), obj("Catalog", "Счета", []))
            write(os.path.join(base, "Catalogs", "Счета", "Ext", "ManagerModule.bsl"), acc)
            for name, text in (("Платежи", pay), ("Деньги", money)):
                write(os.path.join(base, "CommonModules", name + ".xml"), obj("CommonModule", name, []))
                write(os.path.join(base, "CommonModules", name, "Ext", "Module.bsl"), text)
        ext = os.path.join(t, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш</Name>'
              '</Properties></Configuration></MetaDataObject>')
        write(os.path.join(ext, "Catalogs", "Счета", "Ext", "ManagerModule.bsl"), EXT_ACCOUNTS)
        write(os.path.join(ext, "CommonModules", "Расш_Модуль", "Ext", "Module.bsl"), EXT_MODULE)
        write(os.path.join(ext, "EventSubscriptions", "Расш_ПриЗаписи.xml"), HEAD + EXT_SUBSCRIPTION)

    def run_tool(self, mod, name):
        out = os.path.join(self.tmp, "out-" + name)
        with contextlib.redirect_stdout(io.StringIO()):
            mod.main(["--ext-root", os.path.join(self.tmp, "ext"), "--config-old", os.path.join(self.tmp, "old"),
                      "--config-new", os.path.join(self.tmp, "new"), "--out", out])
        with open(os.path.join(out, name + ".json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_interceptor_of_an_obsolete_method_nobody_calls(self):
        data = self.run_tool(interceptors, "interceptors")
        row = next(r for r in data["registry"] if r["target"] == "СчетПоУмолчанию")
        self.assertIn("TARGET_DEPRECATED", row["flags"])
        self.assertIn("TARGET_UNCALLED", row["flags"])
        self.assertEqual(row["deprecated"]["grade"], "risk")
        # two qualified calls; the comment, the string and ЗначениеНастроек.СчетПоУмолчанию do not count
        self.assertEqual(row["vendor_calls"], {"old": 2, "new": 0})
        items = plan.build({}, {}, data, {"findings": []}, {"findings": []})["items"]
        must = [x for x in items if x["group"] == "MUST_FIX" and x["method"] == "Расш_СчетПоУмолчанию"]
        self.assertEqual(len(must), 1)
        self.assertIn("interceptors:TARGET_UNCALLED", must[0]["sources"])

    def test_calls_of_a_method_obsolete_only_in_new(self):
        data = self.run_tool(calls, "calls")
        got = {f["call"]: f for f in data["findings"]}
        self.assertEqual(sorted(got), ["Деньги.Параметры"])  # СтароеСообщение was obsolete in old too: counted only
        f = got["Деньги.Параметры"]
        self.assertEqual(f["flags"], ["CALLS_DEPRECATED"])
        self.assertEqual(f["deprecated"]["grade"], "risk")
        self.assertTrue(f["note"].startswith("[риск]"))
        self.assertEqual(data["stats"]["deprecated_in_old_too"], 1)
        items = plan.build({}, {}, {"registry": []}, data, {"findings": []})["items"]
        self.assertEqual([(x["group"], x["method"]) for x in items], [("MUST_FIX", "Заполнить")])

    def vendor_new_calls(self, line):
        """A vendor module of new that still calls an obsolete method."""
        base = os.path.join(self.tmp, "new", "CommonModules")
        write(os.path.join(base, "Продажи.xml"), obj("CommonModule", "Продажи", []))
        write(os.path.join(base, "Продажи", "Ext", "Module.bsl"),
              "Процедура Провести() Экспорт\n\t%s\nКонецПроцедуры" % line)

    def test_calls_of_an_obsolete_method_the_vendor_still_calls(self):
        # УТ 11.5.27: ВзаиморасчетыСервер.ПолучитьМаксимальнуюДатуВКоллекцииДокументов is obsolete in 11.5.27, and
        # РеализацияТоваровУслугЛокализация calls it there: the call works, nothing to fix now
        self.vendor_new_calls("П = Деньги.Параметры();")
        data = self.run_tool(calls, "calls")
        f = next(f for f in data["findings"] if f["call"] == "Деньги.Параметры")
        self.assertEqual(f["flags"], ["CALLS_DEPRECATED"])
        self.assertEqual((f["deprecated"]["grade"], f["deprecated"]["vendor_calls_new"]), ("info", 1))
        self.assertTrue(f["note"].startswith("[инфо]"), f["note"])
        self.assertIn("вендор сам вызывает метод в new", f["note"])
        p = plan.build({}, {}, {"registry": []}, data, {"findings": []})
        self.assertEqual(p["items"], [])
        self.assertEqual(p["hidden"], {"calls:CALLS_DEPRECATED (вендор сам вызывает в new)": 1})

    def test_interceptor_of_an_obsolete_method_the_vendor_still_calls(self):
        self.vendor_new_calls("С = Справочники.Счета.СчетПоУмолчанию(Неопределено);")
        data = self.run_tool(interceptors, "interceptors")
        row = next(r for r in data["registry"] if r["target"] == "СчетПоУмолчанию")
        self.assertEqual(row["vendor_calls"], {"old": 2, "new": 1})
        self.assertNotIn("TARGET_UNCALLED", row["flags"])
        self.assertEqual(row["deprecated"]["grade"], "info")
        self.assertTrue(any("вендор сам вызывает метод в new" in n for n in row["notes"]))
        self.assertFalse(any(n.startswith("[риск]") for n in row["notes"]), row["notes"])
        items = plan.build({}, {}, data, {"findings": []}, {"findings": []})["items"]
        sources = [src for x in items if x["method"] == "Расш_СчетПоУмолчанию" for src in x["sources"]]
        self.assertFalse([src for src in sources if "DEPRECATED" in src or "UNCALLED" in src], sources)


if __name__ == "__main__":
    unittest.main()
