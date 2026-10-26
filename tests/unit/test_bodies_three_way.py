# -*- coding: utf-8 -*-
"""bodies.three_way: vendor hunks old->new classified against a frozen &Вместо copy."""
import os
import sys
import unittest

from cfe_rebase import bodies


def numbered(lines):
    return [(k, t) for k, t in enumerate(lines, 1)]


def classes(old, copy, new):
    return [h["class"] for h in bodies.three_way(numbered(old), numbered(copy), numbered(new))]


# Vendor moves two lines up (real case: ERP 2.5.22 -> 2.5.27, ПечатьРасходногоОрдераНаТовары);
# the author wrapped the barcode output line into his own block.
PREFIX = ["Функция Печать()", "А = 1;", "Если Не ПервыйДокумент Тогда", "Разделитель();", "КонецЕсли;"]
MOVED = ["ПервыйДокумент = Ложь;", "НомерСтрокиНачало = 1;"]
HEAD = ["// Выведем шапку", "ВывестиШтрихкод(Ссылка);"]
TAIL = ["Если Адресное Тогда", "Зона();", "КонецЕсли;", "КонецФункции"]
AUTHOR = ["// + ЕГ", "Док = Основание(Ссылка);", "Если ЗначениеЗаполнено(Док) Тогда", "ВывестиШтрихкод(Док);",
          "Иначе", "ВывестиШтрихкод(Ссылка);", "КонецЕсли;", "// -"]

OLD = PREFIX + HEAD + MOVED + TAIL
NEW = PREFIX + MOVED + HEAD + TAIL
COPY_PORTED = PREFIX + MOVED + ["// Выведем шапку"] + AUTHOR + TAIL
COPY_STALE = PREFIX + ["// Выведем шапку"] + AUTHOR + MOVED + TAIL


class VendorMove(unittest.TestCase):

    def test_stale_copy_reports_the_move_as_drift(self):
        # The copy keeps the old order: the move was not carried over and must not look settled -
        # neither half of the pair may read as "already in the copy".
        cls = classes(OLD, COPY_STALE, NEW)
        self.assertTrue(cls, cls)
        self.assertTrue(all(c.startswith("ДРЕЙФ") and "перемещ" in c for c in cls), cls)

    def test_ported_copy_move_is_carried(self):
        # The copy already follows the new order: the lines around the move keep the new order in the copy.
        cls = classes(OLD, COPY_PORTED, NEW)
        self.assertFalse(any(c.startswith("ДРЕЙФ") for c in cls), cls)
        self.assertTrue(cls and all("в копии порядок new" in c for c in cls), cls)

    def test_move_without_evidence_stays_drift_with_hint(self):
        # The author rewrote the block the vendor lines jumped over: no order can be read from the copy.
        copy = PREFIX + ["Автор();"] + MOVED + TAIL
        cls = classes(OLD, copy, NEW)
        drift = [c for c in cls if c.startswith("ДРЕЙФ")]
        self.assertTrue(drift, cls)
        self.assertTrue(all("перемещ" in c for c in drift), cls)

    def test_non_unique_line_is_not_called_a_move(self):
        # A lone КонецЕсли; occurs many times; a delete and an insert of it are not evidence of a move.
        old = ["Если А Тогда", "Х();", "КонецЕсли;", "Если Б Тогда", "У();", "КонецЕсли;", "Z();"]
        new = ["Если А Тогда", "Х();", "Если Б Тогда", "У();", "КонецЕсли;", "КонецЕсли;", "Z();"]
        copy = old[:6] + ["Автор();"] + old[6:]
        cls = classes(old, copy, new)
        self.assertFalse(any("перемещ" in c for c in cls), cls)


class PlainHunks(unittest.TestCase):

    def test_missing_vendor_insert_is_drift(self):
        cls = classes(["А;", "Б;", "В;"], ["А;", "Б;", "Автор;", "В;"], ["А;", "Вендор;", "Б;", "В;"])
        self.assertIn("ДРЕЙФ: вставки new нет в копии", cls)

    def test_vendor_insert_already_in_copy(self):
        cls = classes(["А;", "Б;", "В;"], ["А;", "Вендор;", "Б;", "Автор;", "В;"], ["А;", "Вендор;", "Б;", "В;"])
        self.assertEqual(cls, ["вставка new уже есть в копии"])

    def test_vendor_delete_kept_in_copy_is_drift(self):
        cls = classes(["А;", "Б;", "В;", "Г;"], ["А;", "Б;", "В;", "Автор;", "Г;"], ["А;", "В;", "Г;"])
        self.assertEqual(cls, ["ДРЕЙФ: копия держит текст old"])

    def test_author_changed_the_same_place(self):
        cls = classes(["А;", "Б = 1;", "В;"], ["А;", "Б = 3;", "В;"], ["А;", "Б = 2;", "В;"])
        self.assertEqual(cls, ["автор тоже менял это место"])


# Real ERP pair and a ported copy (Documents/РасходныйОрдерНаТовары/Ext/ManagerModule.bsl of each), not part of the
# repository: the test runs only when the three paths are given, and its numbers belong to the author's own copy.
REAL = {
    "old": os.environ.get("CFE_REBASE_REAL_OLD", ""),
    "new": os.environ.get("CFE_REBASE_REAL_NEW", ""),
    "ported": os.environ.get("CFE_REBASE_REAL_PORTED", ""),
}


@unittest.skipUnless(all(os.path.exists(p) for p in REAL.values()),
                     "set CFE_REBASE_REAL_OLD, CFE_REBASE_REAL_NEW, CFE_REBASE_REAL_PORTED to run on real modules")
class RealErpPortedCopy(unittest.TestCase):

    def test_vendor_move_is_carried_and_no_drift_left(self):
        from cfe_rebase import bsl
        mods = {k: bsl.load_module(p) for k, p in REAL.items()}
        old = bodies.body(mods["old"], mods["old"].find("ПечатьРасходногоОрдераНаТовары"), False)
        new = bodies.body(mods["new"], mods["new"].find("ПечатьРасходногоОрдераНаТовары"), False)
        copy = bodies.body(mods["ported"], mods["ported"].find("ЕГ_ПечатьРасходногоОрдераНаТовары"), False)
        cls = [h["class"] for h in bodies.three_way(old, copy, new)]
        self.assertEqual([c for c in cls if c.startswith("ДРЕЙФ")], [], cls)
        self.assertEqual(sum(1 for c in cls if "в копии порядок new" in c), 2, cls)


if __name__ == "__main__":
    unittest.main()
