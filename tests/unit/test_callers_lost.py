# -*- coding: utf-8 -*-
"""CALLERS_LOST / TARGET_UNCALLED of interceptors: the vendor stopped calling an intercepted method on one path or on
all of them (a real case, UT 11.5.22 -> 11.5.27: СкладыСервер.ДобавитьВОчередьФормированияРасходныхОрдеров lost its
call from RecordSetModule of ТоварыКОтгрузке, the posting path, and kept the two calls of a form). Small hand-written
dumps."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from cfe_rebase import interceptors, plan, selection
from tests.unit.test_borrowed import HEAD, obj, write

STORES = "\n".join([
    "Процедура ДобавитьВОчередь(Склад) Экспорт",
    "КонецПроцедуры",
    "",
    "Процедура АктуализироватьОчередь(Данные) Экспорт",
    "КонецПроцедуры",
    "",
    "Процедура Пересчитать(Склад) Экспорт",
    "КонецПроцедуры",
    "",
    "Процедура Служебная() Экспорт",
    "КонецПроцедуры",
    "",
    "Процедура Своя() Экспорт",
    "\tДобавитьВОчередь(Неопределено);",
    "КонецПроцедуры",
])
RECORDSET = {
    "old": "\n".join([
        "Процедура ПриЗаписи(Отказ, Замещение)",
        "\tЕсли Отказ Тогда",
        "\t\tВозврат;",
        "\tКонецЕсли;",
        "\tСклады.ДобавитьВОчередь(Отбор.Регистратор.Значение);",
        "\tСклады.Служебная();",
        "КонецПроцедуры",
    ]),
    "new": "\n".join([
        "Процедура ПриЗаписи(Отказ, Замещение)",
        "\tЕсли Отказ Тогда",
        "\t\tВозврат;",
        "\tКонецЕсли;",
        "\tСклады.АктуализироватьОчередь(Отбор.Регистратор.Значение);",
        "КонецПроцедуры",
    ]),
}
DELIVERY = "\n".join([
    "Процедура Обновить() Экспорт",
    "\tСклады.ДобавитьВОчередь(Неопределено);",
    "\tОбщегоНазначения.ОбщийМодуль(\"Склады\").Пересчитать(Неопределено);",
    "\t// Склады.Служебная();",
    "\tТекст = \"Служебная()\";",
    "КонецПроцедуры",
])
# the caller of Пересчитать moved to another module under the same name: not a lost path
MOVED = "Процедура Перенести() Экспорт\n\tСклады.Пересчитать(Неопределено);\nКонецПроцедуры"
SUBSCRIPTION = ('<EventSubscription uuid="es"><Properties><Name>Пересчет</Name><Handler>CommonModule.Склады.Пересчитать'
                '</Handler></Properties></EventSubscription></MetaDataObject>')
EXT = "\n".join([
    "&После(\"ДобавитьВОчередь\")",
    "Процедура Расш_ДобавитьВОчередь(Склад)",
    "КонецПроцедуры",
    "",
    "&Перед(\"Пересчитать\")",
    "Процедура Расш_Пересчитать(Склад)",
    "КонецПроцедуры",
    "",
    "&Перед(\"Служебная\")",
    "Процедура Расш_Служебная()",
    "КонецПроцедуры",
])


class Dumps(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_callers_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for side in ("old", "new"):
            base = os.path.join(self.tmp, side)
            write(os.path.join(base, "Configuration.xml"), HEAD + '<Configuration uuid="c-%s"><Properties><Name>'
                  'Типовая</Name></Properties></Configuration></MetaDataObject>' % side)
            moved = "Перенос" if side == "old" else "ПереносНовый"
            for name, text in (("Склады", STORES), ("Доставка", DELIVERY), (moved, MOVED)):
                write(os.path.join(base, "CommonModules", name + ".xml"), obj("CommonModule", name, []))
                write(os.path.join(base, "CommonModules", name, "Ext", "Module.bsl"), text)
            write(os.path.join(base, "AccumulationRegisters", "Отгрузка.xml"),
                  obj("AccumulationRegister", "Отгрузка", []))
            write(os.path.join(base, "AccumulationRegisters", "Отгрузка", "Ext", "RecordSetModule.bsl"),
                  RECORDSET[side])
            write(os.path.join(base, "EventSubscriptions", "Пересчет.xml"), HEAD + SUBSCRIPTION)
        ext = os.path.join(self.tmp, "ext", "Расш")
        write(os.path.join(ext, "Configuration.xml"), HEAD + '<Configuration uuid="c-ext"><Properties><Name>Расш</Name>'
              '</Properties></Configuration></MetaDataObject>')
        write(os.path.join(ext, "CommonModules", "Склады", "Ext", "Module.bsl"), EXT)

    def run_tool(self):
        out = os.path.join(self.tmp, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            interceptors.main(["--ext-root", os.path.join(self.tmp, "ext"),
                               "--config-old", os.path.join(self.tmp, "old"),
                               "--config-new", os.path.join(self.tmp, "new"), "--out", out])
        with open(os.path.join(out, "interceptors.json"), encoding="utf-8") as fh:
            return {r["target"]: r for r in json.load(fh)["registry"]}

    def test_one_path_lost(self):
        row = self.run_tool()["ДобавитьВОчередь"]
        self.assertEqual(row["flags"], ["CALLERS_LOST"])
        # the call inside its own module (Своя) counts too
        self.assertEqual(row["vendor_calls"], {"old": 3, "new": 2})
        self.assertEqual(len(row["callers_lost"]), 1)
        lost = row["callers_lost"][0]
        self.assertEqual((lost["file"], lost["method"], lost["line_old"]),
                         ("AccumulationRegisters/Отгрузка/Ext/RecordSetModule.bsl", "ПриЗаписи", 5))
        self.assertEqual(lost["instead"], ["Склады.АктуализироватьОчередь"])
        self.assertNotIn("callers_new", row)
        note = next(n for n in row["notes"] if "перестал вызывать" in n)
        self.assertIn("РегистрНакопления.Отгрузка.МодульНабораЗаписей.ПриЗаписи (old стр. 5; в new там вызывает: "
                      "Склады.АктуализироватьОчередь)", note)
        self.assertIn("доработка больше не выполняется", note)
        self.assertEqual(selection.level("interceptors", row), "risk")

    def test_all_paths_lost(self):
        row = self.run_tool()["Служебная"]
        self.assertEqual(row["flags"], ["TARGET_UNCALLED"])  # no TARGET_DEPRECATED needed
        self.assertEqual(row["vendor_calls"], {"old": 1, "new": 0})  # the comment and the string do not count
        self.assertEqual(row["callers_lost"][0]["method"], "ПриЗаписи")
        self.assertEqual(selection.level("interceptors", row), "error")

    def test_all_lost_but_named_in_a_string(self):
        # 11.5.27: a deferred job runs СостоянияДокументов.ОтразитьСостоянияДокументовВРегистрахСостояний by name
        write(os.path.join(self.tmp, "new", "CommonModules", "Задания", "Ext", "Module.bsl"),
              "Процедура Запланировать(Параметры) Экспорт\n\tПараметры.ИмяМетода = \"Склады.Служебная\";\n"
              "КонецПроцедуры")
        row = self.run_tool()["Служебная"]
        self.assertEqual(row["flags"], ["CALLERS_LOST"])
        self.assertEqual(row["vendor_mentions"], [{"title": "ОбщийМодуль.Задания.Запланировать", "line": 2,
                                                   "how": "строка"}])
        self.assertTrue(any("упоминается через переменную или строку" in n for n in row["notes"]), row["notes"])

    def test_caller_reaches_the_target_through_two_wrappers(self):
        # A real case, form РеализацияТоваровУслуг in 11.5.27: ДоговорПриИзмененииЗавершение -> ...Сервер ->
        # ЗаполнитьЦеныПоСоглашениюНаСервере -> ЗаполнитьЦеныПоСоглашениюСервер: the path is there, two calls deeper
        def module(side, name, text):
            base = os.path.join(self.tmp, side, "CommonModules")
            write(os.path.join(base, name + ".xml"), obj("CommonModule", name, []))
            write(os.path.join(base, name, "Ext", "Module.bsl"), text)
        module("old", "Цены", "Процедура ПриИзменении() Экспорт\n\tСклады.Пересчитать(1);\nКонецПроцедуры")
        module("new", "Цены", "Процедура ПриИзменении() Экспорт\n\tПриИзмененииСервер();\nКонецПроцедуры\n\n"
                              "Процедура ПриИзмененииСервер()\n\tЦеныОбертка.Заполнить();\nКонецПроцедуры")
        module("new", "ЦеныОбертка", "Процедура Заполнить() Экспорт\n\tСклады.Пересчитать(2);\nКонецПроцедуры")
        row = self.run_tool()["Пересчитать"]
        self.assertEqual(row["flags"], [])
        self.assertNotIn("callers_lost", row)
        self.assertEqual([(d["method"], d["via"]) for d in row["callers_moved_deeper"]],
                         [("ПриИзменении", ["ОбщийМодуль.Цены.ПриИзмененииСервер",
                                            "ОбщийМодуль.ЦеныОбертка.Заполнить"])])
        self.assertTrue(any(n.startswith("[инфо] вызов из ОбщийМодуль.Цены.ПриИзменении") for n in row["notes"]))
        # the posting path shape stays lost: ПриЗаписи of the record set reaches ДобавитьВОчередь by no chain
        self.assertEqual(self.run_tool()["ДобавитьВОчередь"]["flags"], ["CALLERS_LOST"])

    def test_moved_caller_dynamic_call_and_subscription(self):
        row = self.run_tool()["Пересчитать"]
        self.assertEqual(row["flags"], [])
        # ОбщийМодуль("Склады").Пересчитать, Перенос / ПереносНовый.Перенести and the subscription handler
        self.assertEqual(row["vendor_calls"], {"old": 3, "new": 3})
        self.assertNotIn("callers_lost", row)

    def test_plan(self):
        data = {"registry": list(self.run_tool().values())}
        items = plan.build({}, {}, data, {"findings": []}, {"findings": []})["items"]
        got = {x["method"]: (x["group"], x["sources"]) for x in items}
        self.assertEqual(got["Расш_ДобавитьВОчередь"], ("MUST_FIX", ["interceptors:CALLERS_LOST"]))
        self.assertEqual(got["Расш_Служебная"], ("MUST_FIX", ["interceptors:TARGET_UNCALLED"]))
        self.assertNotIn("Расш_Пересчитать", got)

    def test_plan_many_callers_is_a_question(self):
        # a library method (ДлительныеОперации.ВыполнитьВФоне: 498 vendor callers in 11.5.22) loses some every release
        row = {"ext": "Р", "module": "CommonModules/ДлительныеОперации/Ext/Module.bsl",
               "ext_method": "Р_ВыполнитьВФоне", "flags": ["CALLERS_LOST"],
               "vendor_callers": {"old": plan.FEW_CALLERS + 1, "new": 40}}
        items = plan.build({}, {}, {"registry": [row]}, {"findings": []}, {"findings": []})["items"]
        self.assertEqual([(x["group"], x["sources"]) for x in items], [("CLARIFY", ["interceptors:CALLERS_LOST"])])

    def test_plan_callers_deleted_with_their_path_is_a_question(self):
        # A real case: 11.5.27 deleted СоглашениеПриИзмененииЗавершение of the ЗаказКлиента form, the hand port did not
        # touch the interceptor; a lost caller still in new (the posting path case) stays a fix
        row = {"ext": "Р", "module": "Documents/ЗаказКлиента/Forms/ФормаДокумента/Ext/Form/Module.bsl",
               "ext_method": "Р_СоглашениеПриИзмененииСервер", "flags": ["CALLERS_LOST"],
               "vendor_callers": {"old": 3, "new": 2},
               "callers_lost": [{"method": "СоглашениеПриИзмененииЗавершение", "method_in_new": False}]}
        items = plan.build({}, {}, {"registry": [row]}, {"findings": []}, {"findings": []})["items"]
        self.assertEqual([x["group"] for x in items], ["CLARIFY"])
        row["callers_lost"].append({"method": "ПриЗаписи", "instead": ["Склады.АктуализироватьОчередь"]})
        items = plan.build({}, {}, {"registry": [row]}, {"findings": []}, {"findings": []})["items"]
        self.assertEqual([x["group"] for x in items], ["MUST_FIX"])


if __name__ == "__main__":
    unittest.main()
