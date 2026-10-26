# -*- coding: utf-8 -*-
"""plan.build: findings of five tools merged into work items and grouped MUST_FIX / REVIEW / CLARIFY / hidden."""
import unittest

from cfe_rebase import plan

MOD = "CommonModules/М/Ext/Module.bsl"
COPY = {"annotation": '&Вместо("Цель")', "target_in_new": True, "vendor_old": True, "vendor_new": False,
        "new_as": []}


def build(ctl=(), bodies=(), registry=(), unbound=(), calls=(), queries=()):
    return plan.build({"methods": list(ctl)}, {"cases": list(bodies)},
                      {"registry": list(registry), "dead_code": {"UNBOUND": list(unbound)}},
                      {"findings": list(calls)}, {"findings": list(queries)})


def groups(p):
    return {(x["module"], x["method"]): x["group"] for x in p["items"]}


class Rules(unittest.TestCase):

    def test_control_verdicts(self):
        p = build(ctl=[{"ext": "Р", "module": MOD, "ext_method": "Р_А", "verdict": "REBASED"},
                       {"ext": "Р", "module": MOD, "ext_method": "Р_Б", "verdict": "CONFLICTS", "conflicts": 2,
                        "conflict_kinds": [{"kind": "same_point", "backport": False}]},
                       {"ext": "Р", "module": MOD, "ext_method": "Р_В", "verdict": "UNCHANGED"}])
        self.assertEqual(groups(p), {(MOD, "Р_А"): "MUST_FIX", (MOD, "Р_Б"): "MUST_FIX"})
        self.assertIn("вставок в одной точке 1", p["items"][1]["what"][0])
        self.assertEqual(p["hidden"], {"control_rebase:UNCHANGED": 1})

    def test_bodies(self):
        p = build(bodies=[{"ext": "Р", "module": MOD, "copy_method": "Р_А", "change": {"old->new": "-1/+2"}},
                          {"ext": "Р", "module": MOD, "copy_method": "Р_Б", "change": {"old->new": "="}},
                          {"ext": "Р", "module": MOD, "copy_method": "Р_В", "note": "нет базового метода в old"},
                          {"ext": "Р", "module": MOD, "copy_method": "Р_Г", "note": "нет базового метода в new"}])
        self.assertEqual(groups(p), {(MOD, "Р_А"): "MUST_FIX", (MOD, "Р_В"): "CLARIFY", (MOD, "Р_Г"): "MUST_FIX"})

    def test_interceptor_flags(self):
        row = {"ext": "Р", "module": MOD, "kind": "После"}
        p = build(registry=[dict(row, ext_method="Р_А", flags=["SIGNATURE_CHANGED"]),
                            dict(row, ext_method="Р_Б", flags=["NEW_BASE"]),
                            dict(row, ext_method="Р_В", flags=["DEAD_BINDING"]),
                            dict(row, ext_method="Р_Г", flags=[], body_change="-1/+1")])
        self.assertEqual(groups(p), {(MOD, "Р_А"): "MUST_FIX", (MOD, "Р_Б"): "CLARIFY"})
        self.assertEqual(p["hidden"], {"interceptors:DEAD_BINDING": 1,
                                       "interceptors:тело вендора изменилось под &После": 1})

    def test_calls(self):
        call = {"ext": "Р", "module": MOD, "call": "М.Ф", "note": ""}
        p = build(calls=[dict(call, caller="Р_А", flags=["REMOVED"], copy=COPY),
                         dict(call, caller="Р_Б", flags=["TOO_FEW_ARGS"], note="в old вызов проходил — сломано "
                                                                                "обновлением"),
                         dict(call, caller="Р_В", flags=["MISSING"]),
                         dict(call, caller="Р_Г", flags=["SIGNATURE_CHANGED"], note="[инфо] совместимо")])
        self.assertEqual(groups(p), {(MOD, "Р_А"): "MUST_FIX", (MOD, "Р_Б"): "MUST_FIX", (MOD, "Р_В"): "REVIEW"})
        self.assertIn("пересъёмкой копии", p["items"][0]["what"][0])
        self.assertEqual(p["hidden"], {"calls:SIGNATURE_CHANGED (совместимо)": 1})

    def test_queries_by_method_and_by_place(self):
        q = {"ext": "Р", "file": MOD, "part": "query", "kind": "поле", "ref": "Т.Х", "note": ""}
        p = build(queries=[dict(q, where="метод Р_А", old="было корректно"),
                           dict(q, where="набор Основной", old="тоже не разрешается"),
                           dict(q, where="метод Р_Б", old="от релиза не зависит")])
        self.assertEqual(groups(p), {(MOD, "Р_А"): "MUST_FIX", (MOD, None): "REVIEW"})
        self.assertTrue(p["items"][1]["what"][0].startswith("набор Основной, query"))

    def test_query_items_have_a_module_title(self):
        # the queries report has no module_title: plan.md showed the raw path and sorted it before Cyrillic titles
        q = {"ext": "Р", "part": "query", "kind": "поле", "ref": "Т.Х", "note": "", "old": "было корректно"}
        p = build(ctl=[{"ext": "Р", "module": "CommonModules/А/Ext/Module.bsl", "module_title": "ОбщийМодуль.А",
                        "ext_method": "Р_А", "verdict": "REBASED"}],
                  queries=[dict(q, file="CommonModules/Я/Ext/Module.bsl", where="метод Р_Я"),
                           dict(q, file="Reports/Отчет1/Templates/Схема/Ext/Template.xml", where="набор Основной"),
                           dict(q, file="Documents/Д/Forms/ФормаСписка/Ext/Form.xml", where="список Список")])
        self.assertEqual([x["module_title"] for x in p["items"]], ["Документ.Д.Форма.ФормаСписка", "ОбщийМодуль.А",
                                                                   "ОбщийМодуль.Я", "Отчет.Отчет1.Макет.Схема"])
        self.assertIn("| Р | ОбщийМодуль.Я | Р_Я |", plan.render(p, "база"))
        self.assertEqual(plan.file_title("CommonTemplates/Т/Ext/Template.xml"), "ОбщийМакет.Т")

    def test_one_item_per_method_across_tools(self):
        p = build(bodies=[{"ext": "Р", "module": MOD, "copy_method": "Р_А", "change": {"old->new": "-1/+1"}}],
                  calls=[{"ext": "Р", "module": MOD, "caller": "Р_А", "call": "М.Ф", "flags": ["REMOVED"],
                          "note": "", "copy": COPY}],
                  queries=[{"ext": "Р", "file": MOD, "where": "метод Р_А", "old": "было корректно", "part": "query",
                            "kind": "поле", "ref": "Т.Х", "note": ""}])
        self.assertEqual(len(p["items"]), 1)
        self.assertEqual(len(p["items"][0]["what"]), 3)
        self.assertEqual(p["counts"], {"MUST_FIX": 1, "REVIEW": 0, "CLARIFY": 0})

    def test_borrowed_and_registers(self):
        b = {"ext": "Р", "object": "Справочник.С", "detail": "…"}
        r = {"ext": "Р", "module": MOD, "detail": "…", "stale": []}
        p = plan.build({"methods": []}, {"cases": []}, {"registry": [], "dead_code": {}}, {"findings": []},
                       {"findings": []},
                       {"findings": [dict(b, file="Catalogs/А.xml", check="REMOVED"),
                                     dict(b, file="Catalogs/Б.xml", check="XML_UNREADABLE")]},
                       {"findings": [dict(r, method="Р_А", check="CHANGED", missing=["Серия"]),
                                     dict(r, method="Р_Б", check="REGISTER_REMOVED"),
                                     dict(r, method="Р_В", check="XML_UNREADABLE")]})
        self.assertEqual(groups(p), {("Catalogs/А.xml", None): "MUST_FIX", ("Catalogs/Б.xml", None): "CLARIFY",
                                     (MOD, "Р_А"): "MUST_FIX", (MOD, "Р_Б"): "MUST_FIX", (MOD, "Р_В"): "CLARIFY"})
        sources = {x["method"]: x["sources"] for x in p["items"] if x["module"] == MOD}
        self.assertEqual(sources, {"Р_А": ["registers:new_dimensions"], "Р_Б": ["registers:removed"],
                                   "Р_В": ["registers:unreadable"]})

    def test_dead_code_goes_to_review(self):
        p = build(bodies=[{"ext": "Р", "module": MOD, "copy_method": "Р_А", "change": {"old->new": "-1/+1"}}],
                  unbound=[{"ext": "Р", "module": MOD, "method": "Р_А"}])
        self.assertEqual(groups(p), {(MOD, "Р_А"): "REVIEW"})
        self.assertIn("мёртвый код", p["items"][0]["what"][-1])

    def test_render_and_screen(self):
        p = build(ctl=[{"ext": "Р", "module": MOD, "module_title": "ОбщийМодуль.М", "ext_method": "Р_А",
                        "verdict": "REBASED"}])
        md = plan.render(p, "база")
        self.assertIn("## Исправить — 1", md)
        self.assertIn("| Р | ОбщийМодуль.М | Р_А |", md)
        lines = plan.screen_lines(p)
        self.assertEqual(lines[0], "план: исправить 1, проверить 0, уточнить 0; скрыто: 0")
        self.assertIn(plan.NOT_COVERED, lines[-1])


if __name__ == "__main__":
    unittest.main()
