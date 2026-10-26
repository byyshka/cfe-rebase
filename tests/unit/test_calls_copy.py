# -*- coding: utf-8 -*-
"""calls.copy_context / advice: a finding inside an &Вместо / &ИзменениеИКонтроль copy against the vendor body."""
import unittest

from cfe_rebase import calls

REL = "CommonModules/М/Ext/Module.bsl"


class Dump(object):
    def __init__(self, methods):
        self.methods = methods          # {method name: {norm: {qualifier key: call as written}}}

    def find_method(self, rel, name):
        return object() if name in self.methods else None


class Resolver(object):
    def method_calls(self, dump, rel, name):
        return dump.methods.get(name, {})


class Caller(object):
    def __init__(self, interceptors):
        self.interceptors = interceptors


CALL = {"chain": ["СтарыйМодуль"], "norm": "метод(а)"}


def context(old, new, interceptors=(("Вместо", "Цель", 1),)):
    return calls.copy_context(Resolver(), Dump(new), Dump(old) if old is not None else None, REL,
                              Caller(list(interceptors)), CALL)


class CopyContext(unittest.TestCase):

    def test_vendor_text_moved_by_the_vendor(self):
        ctx = context({"Цель": {"метод(а)": {"старыймодуль": "СтарыйМодуль.Метод"}}},
                      {"Цель": {"метод(а)": {"новыймодуль": "НовыйМодуль.Метод"}}})
        self.assertEqual(ctx, {"annotation": '&Вместо("Цель")', "target_in_new": True, "vendor_old": True,
                               "vendor_new": False, "new_as": ["НовыйМодуль.Метод"]})
        text = calls.advice(["REMOVED"], ctx)
        self.assertIn("переснять копию по new (port_replace / merge-copy)", text)
        self.assertIn("в new вендор вызывает: НовыйМодуль.Метод", text)
        self.assertTrue(calls.fixed_by_report({"copy": ctx}))

    def test_vendor_text_in_a_change_and_validate_copy(self):
        ctx = context({"Цель": {"метод(а)": {"старыймодуль": "СтарыйМодуль.Метод"}}},
                      {"Цель": {"метод(а)": {"новыймодуль": "НовыйМодуль.Метод"}}},
                      (("ИзменениеИКонтроль", "Цель", 1),))
        self.assertEqual(ctx["annotation"], '&ИзменениеИКонтроль("Цель")')
        text = calls.advice(["REMOVED"], ctx)
        self.assertIn("переснять копию по new (rebase_control / control-rebase)", text)
        self.assertNotIn("port_replace", text)
        self.assertTrue(calls.fixed_by_report({"copy": ctx}))

    def test_call_added_by_the_author(self):
        ctx = context({"Цель": {}}, {"Цель": {}}, (("ИзменениеИКонтроль", "Цель", 1),))
        self.assertEqual((ctx["vendor_old"], ctx["vendor_new"]), (False, False))
        self.assertEqual(calls.advice(["REMOVED"], ctx), "вызов добавил автор копии: нужна правка автора")
        self.assertFalse(calls.fixed_by_report({"copy": ctx}))

    def test_same_call_in_the_new_body(self):
        same = {"Цель": {"метод(а)": {"старыймодуль": "СтарыйМодуль.Метод"}}}
        ctx = context(same, same)
        self.assertEqual(calls.advice(["TOO_FEW_ARGS"], ctx), "тот же вызов есть и в теле вендора new")
        self.assertFalse(calls.fixed_by_report({"copy": ctx}))

    def test_intercepted_method_removed_in_new(self):
        ctx = context({"Цель": {"метод(а)": {"старыймодуль": "СтарыйМодуль.Метод"}}}, {})
        self.assertFalse(ctx["target_in_new"])
        self.assertIn("MISSING_BASE", calls.advice(["REMOVED"], ctx))
        self.assertFalse(calls.fixed_by_report({"copy": ctx}))

    def test_without_an_old_release(self):
        ctx = context(None, {"Цель": {}})
        self.assertIsNone(ctx["vendor_old"])
        self.assertIsNone(calls.advice(["REMOVED"], ctx))

    def test_not_a_copy(self):
        self.assertIsNone(context({}, {}, (("Перед", "Цель", 1),)))
        self.assertIsNone(calls.copy_context(Resolver(), Dump({}), Dump({}), REL, None, CALL))


if __name__ == "__main__":
    unittest.main()
