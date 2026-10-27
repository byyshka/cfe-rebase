# -*- coding: utf-8 -*-
"""bodies.three_way: vendor hunks old->new classified against a frozen &Вместо copy."""
import os
import sys
import unittest

from cfe_rebase import bodies, bsl


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


class Basis(unittest.TestCase):
    """basis follows the vendor hunks the copy carries; the ratios decide only when the hunks do not."""

    @staticmethod
    def case(old, copy, new):
        o, c, n = numbered(old), numbered(copy), numbered(new)
        tw = bodies.three_way(o, c, n)
        st = [bsl.diff_stats(a[1:], b[1:]) for a, b in ((o, c), (n, c), (o, n))]
        return bodies.basis(st[0], st[1], st[2], len(o) - 1, tw), tw

    # A real case in small: a query text, the vendor added one empty «|» line, the author added a field to every part
    # of the union. copy~new beats copy~old by less than the 0.01 margin, so the ratio alone called the copy «old».
    PART = ["|ВЫБРАТЬ", "|	Т.Период КАК Период,", "|	Т.Сумма КАК Сумма", "|ИЗ", "|	Таблица%d КАК Т", "|ОБЪЕДИНИТЬ ВСЕ"]

    def parts(self, author, vendor_line_at=None):
        out = ["Функция Текст()", "Возврат \""]
        for k in range(12):
            rows = list(self.PART)
            rows[4] = rows[4] % k
            if author:
                rows.insert(2, "|	\"\"Таблица%d\"\" КАК Расш_Анализ," % k)
            out += rows
            if k == vendor_line_at:
                out.append("|")
        return out + ["|\";", "КонецФункции"]

    def test_ported_copy_with_one_vendor_line_is_new(self):
        old, new = self.parts(False), self.parts(False, 7)
        copy = self.parts(True, 7)
        copy[0] = "Функция Расш_Текст()"
        st_co = bsl.diff_stats(numbered(old)[1:], numbered(copy)[1:])
        st_cn = bsl.diff_stats(numbered(new)[1:], numbered(copy)[1:])
        self.assertLess(st_cn["ratio"] - st_co["ratio"], 0.01)  # the ratio alone does not tell
        b, tw = self.case(old, copy, new)
        self.assertEqual(b, "new")
        self.assertEqual([(h["class"], h["in_copy"]) for h in tw], [("вставка new уже есть в копии", True)])

    def test_stale_copy_without_the_vendor_line_is_old(self):
        old, new = self.parts(False), self.parts(False, 7)
        b, tw = self.case(old, self.parts(True), new)
        self.assertEqual(b, "old")
        # «|» is everywhere in the copy, yet not at the vendor's place: the insert is drift, not «already there»
        self.assertEqual([(h["class"], h["in_copy"]) for h in tw], [("ДРЕЙФ: вставки new нет в копии", False)])

    def test_partly_ported_copy_is_mixed(self):
        old = ["Ф()", "А;", "Б;", "В;", "Г;", "Д;", "Е;", "К"]
        new = ["Ф()", "А;", "Б2;", "В;", "Г;", "Д2;", "Е;", "К"]
        copy = ["Ф2()", "А;", "Б2;", "В;", "Автор;", "Г;", "Д;", "Е;", "К"]
        self.assertEqual(self.case(old, copy, new)[0], "mixed")


# Real UT pair and a ported &Вместо copy (Documents/РасчетКурсовыхРазниц/Ext/ManagerModule.bsl of each), not part of the
# repository: the test runs only when the three paths and the copy's method name are given, and its numbers belong to
# the author's own copy.
REAL_UT = {
    "old": os.environ.get("CFE_REBASE_REAL_UT_OLD", ""),
    "new": os.environ.get("CFE_REBASE_REAL_UT_NEW", ""),
    "work": os.environ.get("CFE_REBASE_REAL_UT_WORK", ""),
}
REAL_UT_COPY = os.environ.get("CFE_REBASE_REAL_UT_COPY", "")


@unittest.skipUnless(REAL_UT_COPY and all(os.path.exists(p) for p in REAL_UT.values()),
                     "set CFE_REBASE_REAL_UT_OLD, _NEW, _WORK and _COPY to run on real modules")
class RealUtPortedCopy(unittest.TestCase):

    def test_ported_copy_reads_as_new(self):
        mods = {k: bsl.load_module(p) for k, p in REAL_UT.items()}
        target = "ТекстЗапросаТаблицаПрочиеДоходы"
        o = bodies.body(mods["old"], mods["old"].find(target), True)
        n = bodies.body(mods["new"], mods["new"].find(target), True)
        c = bodies.body(mods["work"], mods["work"].find(REAL_UT_COPY), True)
        st = [bsl.diff_stats(a[1:], b[1:]) for a, b in ((o, c), (n, c), (o, n))]
        self.assertEqual((st[0]["ratio"], st[1]["ratio"]), (0.971, 0.973))
        tw = bodies.three_way(o, c, n)
        self.assertEqual([h["in_copy"] for h in tw], [True])
        self.assertEqual(bodies.basis(st[0], st[1], st[2], len(o) - 1, tw), "new")


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
