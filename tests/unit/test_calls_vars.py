# -*- coding: utf-8 -*-
"""calls: a variable bound to a module resolves by the nearest assignment above the call; ОбщийМодуль("Документы.X")
is the manager of X."""
import unittest

from cfe_rebase import bsl, calls

TEXT = "\n".join([
    "Процедура П()",
    '\tМодуль = ОбщегоНазначения.ОбщийМодуль("ПерваяПодсистема");',
    "\tА = Модуль.ПервыйМетод();",
    '\tМодуль = ОбщегоНазначения.ОбщийМодуль("ВтораяПодсистема");',
    "\tБ = Модуль.ВторойМетод();",
    '\tМенеджер = ОбщегоНазначения.ОбщийМодуль("Документы.ЗаказКлиента");',
    "\tМенеджер.ЗаполнитьПараметры();",
    "\tМенеджер = Новый Структура;",
    "\tМенеджер.Вставить();",
    "КонецПроцедуры",
])


class ModuleString(unittest.TestCase):

    def test_collection_name_is_a_manager(self):
        self.assertEqual(calls.module_string("Документы.ЗаказКлиента"), ("Документ", "ЗаказКлиента", "manager"))
        self.assertEqual(calls.module_string("Константы.ВалютаУчета"), ("Константа", "ВалютаУчета", "valuemanager"))

    def test_a_common_module_name(self):
        self.assertIsNone(calls.module_string("ОбщегоНазначения"))
        self.assertIsNone(calls.module_string("Документы.ЗаказКлиента.Лишнее"))
        self.assertIsNone(calls.module_string("НеКоллекция.Имя"))


class NearestAssignment(unittest.TestCase):

    def setUp(self):
        self.mod = bsl.Module(TEXT, "Module.bsl")
        meth = self.mod.find("П")
        a, b = self.mod.tok_range(meth)
        self.vt = calls.var_targets(self.mod.toks, a, b, "CommonModules/Х/Ext/Module.bsl")
        self.calls = {c["name"]: c for c in self.mod.calls()}

    def picked(self, var, name):
        return calls.nearest(self.vt[var], self.calls[name]["tok"])

    def test_each_call_takes_the_assignment_above_it(self):
        self.assertEqual(self.picked("модуль", "ПервыйМетод"), ("ОбщийМодуль", "ПерваяПодсистема", "module"))
        self.assertEqual(self.picked("модуль", "ВторойМетод"), ("ОбщийМодуль", "ВтораяПодсистема", "module"))

    def test_manager_by_common_module_function(self):
        self.assertEqual(self.picked("менеджер", "ЗаполнитьПараметры"), ("Документ", "ЗаказКлиента", "manager"))

    def test_reassigned_to_something_else(self):
        self.assertIsNone(self.picked("менеджер", "Вставить"))


class SignatureRename(unittest.TestCase):
    """classify_change: a rename alone is info, a reorder or a new default stays a risk."""

    def sig(self, text):
        return bsl.Module(text + "\nКонецПроцедуры").methods[0]

    def grade(self, old, new, nargs=4):
        return calls.classify_change(self.sig(old), self.sig(new), nargs, ["а"] * nargs)

    def test_rename_only_is_info(self):
        # a real case: ПакетнаяОбработкаТабличнойЧастиСервер.ОбработатьСтрокуТЧ 11.5.17 -> 11.5.27
        grade, note = self.grade(
            "Процедура ОбработатьСтрокуТЧ(СтрокаКоллекции, СтруктураДействий, КэшированныеЗначения, "
            "Коллекция = Неопределено) Экспорт",
            "Процедура ОбработатьСтрокуТЧ(ТекущаяСтрока, СтруктураДействий, КэшированныеЗначения, "
            "Коллекция = Неопределено) Экспорт", 3)
        self.assertEqual(grade, "info")
        self.assertIn("поз. 1 СтрокаКоллекции → ТекущаяСтрока", note)

    def test_reorder_is_a_risk(self):
        self.assertEqual(self.grade("Процедура П(А, Б) Экспорт", "Процедура П(Б, А) Экспорт", 2)[0], "risk")

    def test_rename_with_another_default_or_byval_is_a_risk(self):
        self.assertEqual(self.grade("Процедура П(А, Б = 1) Экспорт", "Процедура П(В, Б = 2) Экспорт", 1)[0], "risk")
        self.assertEqual(self.grade("Процедура П(А) Экспорт", "Процедура П(Знач В) Экспорт", 1)[0], "risk")


if __name__ == "__main__":
    unittest.main()
