# -*- coding: utf-8 -*-
"""bsl.tokenize and the &ИзменениеИКонтроль markers: outside a literal and inside a multi-line query literal."""
import unittest

from cfe_rebase import bsl

# Markers outside any literal: the token lists below were recorded on the tokenizer before markers inside a literal
# stopped ending it.
OUTSIDE = "\n".join([
    "Функция Ф(А)",
    "\t#Удаление",
    "\tБ = Старый(А, \"с\");",
    "\t#КонецУдаления",
    "\t#Вставка // автор",
    "\tБ = Новый1(А, 1);",
    "\t#КонецВставки",
    "\tВозврат Б;",
    "КонецФункции",
])
OUTSIDE_LIVE = [
    ("id", "Функция", 1), ("id", "Ф", 1), ("op", "(", 1), ("id", "А", 1), ("op", ")", 1),
    ("pre", "#Вставка // автор", 5),
    ("id", "Б", 6), ("op", "=", 6), ("id", "Новый1", 6), ("op", "(", 6), ("id", "А", 6), ("op", ",", 6),
    ("num", "1", 6), ("op", ")", 6), ("op", ";", 6),
    ("pre", "#КонецВставки", 7),
    ("id", "Возврат", 8), ("id", "Б", 8), ("op", ";", 8), ("id", "КонецФункции", 9),
]
OUTSIDE_ALL = OUTSIDE_LIVE[:5] + [
    ("id", "Б", 3), ("op", "=", 3), ("id", "Старый", 3), ("op", "(", 3), ("id", "А", 3), ("op", ",", 3),
    ("str", "с", 3), ("op", ")", 3), ("op", ";", 3),
] + OUTSIDE_LIVE[5:]

# A literal opened inside a deletion: its #КонецУдаления ends it (the deleted code is gone, the state before the
# deletion comes back). Recorded before the fix as well.
LIT_IN_DELETE = "\n".join([
    "#Удаление",
    "Т = \"ВЫБРАТЬ",
    "|\tА",
    "#КонецУдаления",
    "#Вставка",
    "Т = \"ВЫБРАТЬ\";",
    "#КонецВставки",
    "Х = 1;",
])
LIT_IN_DELETE_LIVE = [
    ("pre", "#Вставка", 5),
    ("id", "Т", 6), ("op", "=", 6), ("str", "ВЫБРАТЬ", 6), ("op", ";", 6),
    ("pre", "#КонецВставки", 7),
    ("id", "Х", 8), ("op", "=", 8), ("num", "1", 8), ("op", ";", 8),
]
LIT_IN_DELETE_ALL = [("id", "Т", 2), ("op", "=", 2), ("str", "ВЫБРАТЬ\n\tА", 2)] + LIT_IN_DELETE_LIVE

# A preprocessor line that is not a change marker still ends a literal, as before the fix.
PREPROC = "\n".join([
    "Т = \"ВЫБРАТЬ",
    "|\tА",
    "#Если Сервер Тогда",
    "|\tБ\";",
    "#КонецЕсли",
])
PREPROC_TOKS = [
    ("id", "Т", 1), ("op", "=", 1), ("str", "ВЫБРАТЬ\n\tА", 1), ("pre", "#Если Сервер Тогда", 3),
    ("op", "|", 4), ("id", "Б", 4), ("str", ";", 4), ("pre", "#КонецЕсли", 5),
]

# Markers inside a query literal, as &ИзменениеИКонтроль bodies have them.
INSIDE_LINES = [
    "Т =",                                  # 1
    "\"ВЫБРАТЬ",                            # 2
    "|\tТ.А КАК А,",                        # 3
    "\t#Удаление",                          # 4
    "|\tТ.Б КАК Б",                         # 5
    "\t#КонецУдаления",                     # 6
    "\t#Вставка // автор 01.10",            # 7
    "|\tТ.В КАК В",                         # 8
    "\t#КонецВставки",                      # 9
    "|ИЗ",                                  # 10
    "|\tСправочник.С КАК Т\";",             # 11
    "Х = 1;",                               # 12
]
INSIDE = "\n".join(INSIDE_LINES)
# one "\n" per source line, skipped lines included, so a position in the literal still maps to its source line
QUERY_LIVE = "ВЫБРАТЬ\n\tТ.А КАК А,\n\n\n\n\n\tТ.В КАК В\n\nИЗ\n\tСправочник.С КАК Т"
QUERY_ALL = "ВЫБРАТЬ\n\tТ.А КАК А,\n\n\tТ.Б КАК Б\n\n\n\tТ.В КАК В\n\nИЗ\n\tСправочник.С КАК Т"
AFTER = [("op", ";", 11), ("id", "Х", 12), ("op", "=", 12), ("num", "1", 12), ("op", ";", 12)]


class MarkersOutsideLiterals(unittest.TestCase):

    def test_markers_in_code(self):
        self.assertEqual(bsl.tokenize(OUTSIDE), OUTSIDE_LIVE)
        self.assertEqual(bsl.tokenize(OUTSIDE, drop_deleted=False), OUTSIDE_ALL)

    def test_literal_opened_in_a_deletion_ends_at_its_end_marker(self):
        self.assertEqual(bsl.tokenize(LIT_IN_DELETE), LIT_IN_DELETE_LIVE)
        self.assertEqual(bsl.tokenize(LIT_IN_DELETE, drop_deleted=False), LIT_IN_DELETE_ALL)

    def test_other_preprocessor_line_ends_a_literal(self):
        self.assertEqual(bsl.tokenize(PREPROC), PREPROC_TOKS)
        self.assertEqual(bsl.tokenize(PREPROC, drop_deleted=False), PREPROC_TOKS)


class MarkersInsideALiteral(unittest.TestCase):

    def check(self, text, drop_deleted, query):
        toks = bsl.tokenize(text, drop_deleted=drop_deleted)
        strs = [t for t in toks if t[0] == "str"]
        self.assertEqual(len(strs), 1, toks)
        value = strs[0][1].replace("\r", "")
        self.assertEqual(value, query)
        self.assertEqual(strs[0][2], 2)
        self.assertEqual(value.count("\n"), 11 - 2)
        self.assertEqual(toks[:2], [("id", "Т", 1), ("op", "=", 1)])
        self.assertEqual(toks[3:], AFTER)

    def test_one_literal_deletion_dropped(self):
        self.check(INSIDE, True, QUERY_LIVE)
        value = [t for t in bsl.tokenize(INSIDE) if t[0] == "str"][0][1]
        for part in ("ВЫБРАТЬ", "Т.А КАК А,", "Т.В КАК В", "ИЗ", "Справочник.С КАК Т"):
            self.assertIn(part, value)
        self.assertNotIn("Т.Б", value)
        self.assertNotIn("#", value)

    def test_one_literal_deletion_kept(self):
        self.check(INSIDE, False, QUERY_ALL)

    def test_crlf(self):
        self.check("\r\n".join(INSIDE_LINES), True, QUERY_LIVE)
        self.check("\r\n".join(INSIDE_LINES), False, QUERY_ALL)


if __name__ == "__main__":
    unittest.main()
