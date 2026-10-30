# -*- coding: utf-8 -*-
"""calls WRITE_CHECKS_NEW (write_checks): the extension creates an object in code and writes it, and the vendor added
refusals to the object's write handlers in new.

A real case (11.5.17 -> 11.5.27): a common module of the extension creates Задачи.ЗадачаИсполнителя.СоздатьЗадачу() for
every member of a group and writes it; 11.5.27 added to ПередЗаписью of ЗадачаИсполнителя «Исполнителем задачи выбран
недействительный пользователь» (Отказ), and the scheduled job fell as a whole. The handlers below are cut from the
11.5.17 / 11.5.27 dumps.
"""
import unittest

from cfe_rebase import bsl, plan, selection, write_checks

TASK = "Tasks/ЗадачаИсполнителя/Ext/ObjectModule.bsl"

OLD = """Процедура ПередЗаписью(Отказ)
	Если ОбменДанными.Загрузка Тогда
		Возврат;
	КонецЕсли;
	Если НЕ ИсходныеРеквизиты.Выполнена И Выполнена Тогда
		Если СостояниеБизнесПроцесса = Перечисления.СостоянияБизнесПроцессов.Остановлен Тогда
			ВызватьИсключение НСтр("ru = 'Нельзя выполнять задачи остановленных бизнес-процессов.'");
		КонецЕсли;
	КонецЕсли;
КонецПроцедуры
"""

NEW = """Процедура ПередЗаписью(Отказ)
	Если ОбменДанными.Загрузка Тогда
		Возврат;
	КонецЕсли;
	Если ЭтоПользовательИсполнитель(Исполнитель) И Не ЭтоПроверка И Не ЭтоПеренаправление Тогда
		ОписаниеПользователя = ОбщегоНазначения.ЗначенияРеквизитовОбъекта(Исполнитель, "Недействителен,ПометкаУдаления");
		Если ОписаниеПользователя.Недействителен Или ОписаниеПользователя.ПометкаУдаления Тогда
			ОбщегоНазначения.СообщитьПользователю(НСтр("ru = 'Исполнителем задачи выбран недействительный пользователь.'"),,,, Отказ);
			Возврат;
		КонецЕсли;
	КонецЕсли;
	Если НЕ ИсходныеРеквизиты.Выполнена И Выполнена Тогда
		Если СостояниеБизнесПроцесса = Перечисления.СостоянияБизнесПроцессов.Остановлен Тогда
			ВызватьИсключение НСтр("ru = 'Нельзя выполнять задачи остановленных бизнес-процессов.'");
		КонецЕсли;
	КонецЕсли;
КонецПроцедуры
"""

EXT = """Процедура СоздатьЗадачиИсполнителям() Экспорт
	Для Каждого Исполнитель Из Исполнители Цикл
		Задача = Задачи.ЗадачаИсполнителя.СоздатьЗадачу();
		Задача.Дата = ТекущаяДатаСеанса();
		Задача.Исполнитель = Исполнитель;
		Задача.Важность = Перечисления.ВариантыВажностиЗадачи.Высокая;
		Задача.Записать();
	КонецЦикла;
КонецПроцедуры
"""

# Справочник.Партнеры.ПриЗаписи in 11.5.27: a re-raise after a failed lock, no data check
RERAISE = """Процедура ПриЗаписи(Отказ)
	Если ДанныеДляСозданияТД <> Неопределено Тогда
		Попытка
			Блокировка.Заблокировать();
		Исключение
			ВызватьИсключение(Уточнение.Текст, Уточнение.Категория,, ПредставлениеОшибки, Информация);
		КонецПопытки;
	КонецЕсли;
КонецПроцедуры
"""


class FakeDump(object):
    def __init__(self, modules):
        self.modules = {k: bsl.Module(v) for k, v in modules.items()}

    def module(self, rel):
        return self.modules.get(rel)


class FakeExt(object):
    name = "Расш"


def ext_module(text=EXT):
    return bsl.Module(text)


class WriteChecksTest(unittest.TestCase):
    def test_writes(self):
        mod = ext_module()
        got = write_checks.writes(mod, mod.methods[0])
        self.assertEqual(got, [("Задача", "ЗадачаИсполнителя", "object", 3, 7, False)])

    def test_no_write_no_pair(self):
        mod = ext_module(EXT.replace("\t\tЗадача.Записать();\n", ""))
        self.assertEqual(write_checks.writes(mod, mod.methods[0]), [])

    def test_posting_and_recordset(self):
        mod = ext_module("""Процедура Создать() Экспорт
	Док = Документы.ЗаказКлиента.СоздатьДокумент();
	Док.Записать(РежимЗаписиДокумента.Проведение);
	Набор = РегистрыСведений.ЦеныНоменклатуры.СоздатьНаборЗаписей();
	Набор.Записать(Ложь);
КонецПроцедуры
""")
        got = write_checks.writes(mod, mod.methods[0])
        self.assertEqual([(k, n, p, posting) for k, n, p, _, _, posting in got],
                         [("Документ", "ЗаказКлиента", "object", True),
                          ("РегистрСведений", "ЦеныНоменклатуры", "recordset", False)])
        self.assertEqual(write_checks.module_rel("РегистрСведений", "ЦеныНоменклатуры", "recordset"),
                         "InformationRegisters/ЦеныНоменклатуры/Ext/RecordSetModule.bsl")

    def test_refusals_grew(self):
        old, new = FakeDump({TASK: OLD}), FakeDump({TASK: NEW})
        self.assertEqual(write_checks.refusals(old, TASK, "ПередЗаписью"), (0, 1))
        self.assertEqual(write_checks.refusals(new, TASK, "ПередЗаписью"), (1, 1))
        self.assertEqual(write_checks.compare(old, new, TASK, False),
                         [{"handler": "ПередЗаписью", "old": [0, 1], "new": [1, 1]}])
        self.assertEqual(write_checks.compare(new, new, TASK, False), [])

    def test_reraise_in_except_is_not_a_check(self):
        rel = "Catalogs/Партнеры/Ext/ObjectModule.bsl"
        old = FakeDump({rel: "Процедура ПриЗаписи(Отказ)\nКонецПроцедуры\n"})
        new = FakeDump({rel: RERAISE})
        self.assertEqual(write_checks.refusals(new, rel, "ПриЗаписи"), (0, 0))
        self.assertEqual(write_checks.compare(old, new, rel, False), [])

    def test_handler_new_in_new(self):
        old, new = FakeDump({TASK: "Процедура ПриЗаписи(Отказ)\nКонецПроцедуры\n"}), FakeDump({TASK: NEW})
        self.assertEqual(write_checks.compare(old, new, TASK, False),
                         [{"handler": "ПередЗаписью", "old": None, "new": [1, 1]}])

    def test_check_row_plan_selection(self):
        rel = "CommonModules/мр_СерверныйМодуль/Ext/Module.bsl"
        rows = write_checks.check(FakeExt(), rel, ext_module(), FakeDump({TASK: OLD}), FakeDump({TASK: NEW}), {})
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["flags"], r["line"], r["caller"]), (["WRITE_CHECKS_NEW"], 7,
                                                               "СоздатьЗадачиИсполнителям"))
        self.assertEqual(r["write_checks"]["object"], "Задача.ЗадачаИсполнителя")
        self.assertIn("ПередЗаписью (Отказ 0 → 1", r["note"])
        self.assertEqual(selection._calls_level(r), "risk")
        r["caller_live"] = "вызывается"
        items = plan.build({}, {}, {}, {"findings": [r]}, {"findings": []})["items"]
        self.assertEqual([x["group"] for x in items], ["CLARIFY"])
        # without an old release nothing is compared
        self.assertEqual(write_checks.check(FakeExt(), rel, ext_module(), None, FakeDump({TASK: NEW}), {}), [])


if __name__ == "__main__":
    unittest.main()
