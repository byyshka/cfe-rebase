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


if __name__ == "__main__":
    unittest.main()
