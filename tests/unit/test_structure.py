# -*- coding: utf-8 -*-
"""structure: merge markers, &ИзменениеИКонтроль markers, method boundaries, annotations, duplicates, preprocessor."""
import unittest

from cfe_rebase import structure


def kinds(text):
    return sorted({c for c, _, _ in structure.bsl_issues(text.split("\n"))})


class Markers(unittest.TestCase):

    def test_clean_control_with_trailing_comments(self):
        self.assertEqual(kinds('&ИзменениеИКонтроль("Х")\nПроцедура Р_Х(А)\n\t#Вставка // Автор 01.10 причина\n'
                               '\tБ = 1;\n\t#КонецВставки // Автор\n\t#Удаление\n\tВ = 2;\n\t#КонецУдаления\n'
                               'КонецПроцедуры'), [])

    def test_markers_inside_a_query_literal(self):
        self.assertEqual(kinds('&ИзменениеИКонтроль("Х")\nФункция Р_Х()\n\tЗапрос = Новый Запрос;\n\tЗапрос.Текст =\n'
                               '\t"ВЫБРАТЬ\n\t|\tТ.А КАК А,\n\t#Удаление\n\t|\tТ.Б КАК Б\n\t#КонецУдаления\n'
                               '\t#Вставка // автор\n\t|\tТ.В КАК В\n\t#КонецВставки\n\t|ИЗ\n\t|\tТ КАК Т";\n'
                               '\tВозврат Запрос;\nКонецФункции'), [])

    def test_overlap_and_orphan_end(self):
        self.assertEqual(kinds('&ИзменениеИКонтроль("Х")\nПроцедура Р_Х()\n\t#Вставка\n\t#Удаление\n\tА=1;\n'
                               '\t#КонецУдаления\n\t#КонецВставки\nКонецПроцедуры'),
                         ["MARKER_ORPHAN_END", "MARKER_OVERLAP"])

    def test_insert_not_closed_by_the_end_of_the_method(self):
        self.assertEqual(kinds('&ИзменениеИКонтроль("Х")\nПроцедура Р_Х()\n\t#Вставка\n\tА=1;\nКонецПроцедуры\n'
                               'Процедура У()\nКонецПроцедуры'), ["MARKER_UNCLOSED"])

    def test_unclosed_delete_swallows_the_end_of_the_method(self):
        self.assertEqual(kinds('&ИзменениеИКонтроль("Х")\nПроцедура Р_Х()\n\t#Удаление\n\tА=1;\nКонецПроцедуры\n'
                               'Процедура У()\nКонецПроцедуры'), ["MARKER_UNCLOSED", "METHOD_NOT_CLOSED"])

    def test_misspelled_marker(self):
        self.assertEqual(kinds('&ИзменениеИКонтроль("Х")\nПроцедура Р_Х()\n\t#Вставить\n\tА=1;\n\t#КонецВставки\n'
                               'КонецПроцедуры'), ["MARKER_MALFORMED", "MARKER_ORPHAN_END"])

    def test_marker_with_text_but_no_comment(self):
        self.assertEqual(kinds('&ИзменениеИКонтроль("Х")\nПроцедура Р_Х()\n\t#Вставка Автор\n\tА=1;\n'
                               'КонецПроцедуры'), ["MARKER_MALFORMED"])

    def test_marker_in_a_method_without_change_and_validate(self):
        self.assertEqual(kinds('&Перед("Х")\nПроцедура Р_Х()\n\t#Вставка\n\tА=1;\n\t#КонецВставки\nКонецПроцедуры'),
                         ["MARKER_IN_NON_CONTROL_METHOD"])


class Methods(unittest.TestCase):

    def test_orphan_annotation(self):
        self.assertEqual(kinds('&Перед("Х")\n\n// комментарий\nПерем А;\nПроцедура У()\nКонецПроцедуры\n&После("Y")'),
                         ["ANNOTATION_ORPHAN"])

    def test_annotation_then_directive_then_declaration(self):
        self.assertEqual(kinds('&НаСервере\n&После("Х")\nПроцедура Р_Х()\nКонецПроцедуры'), [])

    def test_several_interception_annotations_on_one_method(self):
        self.assertEqual(found('&Перед("Х")\n&НаСервере\n&После("Х")\nПроцедура Р_Х()\nКонецПроцедуры'),
                         [("ANNOTATION_MULTI", 1)])
        self.assertEqual(kinds('&ИзменениеИКонтроль("Х")\n&Вместо("Х")\nПроцедура Р_Х()\nКонецПроцедуры'),
                         ["ANNOTATION_MULTI"])
        self.assertEqual(kinds('&НаКлиенте\n&Вместо("Х")\nПроцедура Р_Х()\nКонецПроцедуры'), [])  # one interceptor

    def test_duplicate_in_one_branch_but_not_across_if_else(self):
        self.assertEqual(kinds("#Если Сервер Тогда\nПроцедура А()\nКонецПроцедуры\n#Иначе\nПроцедура А()\n"
                               "КонецПроцедуры\n#КонецЕсли\nПроцедура Б()\nКонецПроцедуры\nпроцедура б()\n"
                               "КонецПроцедуры"), ["DUP_METHOD"])

    def test_duplicate_only_when_one_if_path_is_a_prefix_of_the_other(self):
        # conditions are not evaluated: a method is a duplicate only where both copies would compile together for sure
        a = "Процедура А()\nКонецПроцедуры\n"
        cases = {
            "two separate #Если blocks": ("#Если Сервер Тогда\n" + a + "#КонецЕсли\n#Если Клиент Тогда\n" + a +
                                          "#КонецЕсли", []),
            "siblings nested in one branch": ("#Если Сервер Тогда\n#Если ВнешнееСоединение Тогда\n" + a +
                                              "#КонецЕсли\n#Если ТолстыйКлиентОбычноеПриложение Тогда\n" + a +
                                              "#КонецЕсли\n#КонецЕсли", []),
            "top level and inside #Если": (a + "#Если Сервер Тогда\n" + a + "#КонецЕсли", ["DUP_METHOD"]),
            "one branch of one #Если": ("#Если Сервер Тогда\n" + a + a + "#КонецЕсли", ["DUP_METHOD"]),
            "nested #Если and its outer branch": ("#Если Сервер Тогда\n" + a + "#Если ВнешнееСоединение Тогда\n" + a +
                                                  "#КонецЕсли\n#КонецЕсли", ["DUP_METHOD"]),
            "nested in #Иначе and the outer #Если": ("#Если Сервер Тогда\n" + a + "#Иначе\n#Если Клиент Тогда\n" + a +
                                                     "#КонецЕсли\n#КонецЕсли", []),
        }
        for name, (text, want) in cases.items():
            with self.subTest(name):
                self.assertEqual(kinds(text), want)

    def test_kind_mismatch_orphan_end_and_a_method_on_one_line(self):
        self.assertEqual(kinds("Процедура А()\n\tЕсли Истина Тогда А=1; КонецЕсли;КонецФункции\nКонецПроцедуры\n"
                               "Функция В() Возврат 1; КонецФункции"), ["METHOD_KIND_MISMATCH", "METHOD_ORPHAN_END"])

    def test_end_keyword_in_a_string_and_a_comment(self):
        self.assertEqual(kinds('Процедура А()\n\tС = "КонецПроцедуры";\n\t// КонецПроцедуры\n\tТ = "\n'
                               '\t|КонецПроцедуры";\nКонецПроцедуры'), [])

    def test_unbalanced_region(self):
        self.assertEqual(kinds("#Область А\nПроцедура А()\nКонецПроцедуры\n#КонецОбласти\n#КонецОбласти"),
                         ["REGION_UNBALANCED"])

    def test_method_not_closed(self):
        self.assertEqual(kinds("Процедура А()\n\tБ = 1;\nПроцедура В()\nКонецПроцедуры"), ["METHOD_NOT_CLOSED"])


def found(text):
    return [(c, n) for c, n, _ in structure.bsl_issues(text.split("\n"))]


class Preprocessor(unittest.TestCase):
    IF_A = "#Если Сервер Тогда\nПроцедура А()\nКонецПроцедуры\n"
    IF_B = "#Если Клиент Тогда\nПроцедура Б()\nКонецПроцедуры\n"

    def test_every_unclosed_if_is_an_issue_at_its_line(self):
        self.assertEqual(found(self.IF_A + self.IF_B), [("PREPROC_UNBALANCED", 1), ("PREPROC_UNBALANCED", 4)])
        self.assertEqual(found(self.IF_A + self.IF_B + "#КонецЕсли"), [("PREPROC_UNBALANCED", 1)])

    def test_every_unclosed_region_is_an_issue_at_its_line(self):
        self.assertEqual(found("#Область А\n#Область Б\nПроцедура А()\nКонецПроцедуры"),
                         [("REGION_UNBALANCED", 1), ("REGION_UNBALANCED", 2)])

    def test_orphan_end_and_branch(self):
        self.assertEqual(found("#КонецЕсли\nПроцедура А()\nКонецПроцедуры\n#Иначе\n#ИначеЕсли Клиент Тогда"),
                         [("PREPROC_UNBALANCED", 1), ("PREPROC_UNBALANCED", 4), ("PREPROC_UNBALANCED", 5)])

    def test_unknown_instruction(self):
        self.assertEqual(found("#Использовать Библиотека\nПроцедура А()\nКонецПроцедуры"), [("PREPROC_UNKNOWN", 1)])

    def test_every_known_instruction_in_both_languages(self):
        self.assertEqual(found("#Если Сервер Или ТолстыйКлиентОбычноеПриложение Тогда\n#ИначеЕсли Клиент Тогда\n"
                               "#Иначе\n#КонецЕсли\n#If Server Then\n#ElsIf Client Then\n#Else\n#EndIf\n"
                               "#Область А\n#КонецОбласти\n#Region B\n#EndRegion"), [])


class Merge(unittest.TestCase):

    def test_conflict_and_a_stray_separator(self):
        lines = "а\n<<<<<<< new\nx\n||||||| old\ny\n=======\nz\n>>>>>>> copy\n=======".split("\n")
        self.assertEqual([(c, n) for c, n, _ in structure.merge_markers(lines)],
                         [("MERGE_MARKER", 2), ("MERGE_MARKER_ORPHAN", 9)])

    def test_lookalikes_are_not_markers(self):
        lines = ["// =======", "\t|=======", "Текст = \"<<<<<<< \";", "<<<<<<<<", "========"]
        self.assertEqual(structure.merge_markers(lines), [])


class NewIssues(unittest.TestCase):
    BASE = "Процедура А()\nКонецПроцедуры\nПроцедура А()\nКонецПроцедуры\n"

    def test_an_old_issue_moved_down_is_not_new(self):
        before = structure.module_issues(self.BASE.split("\n"))
        after = structure.module_issues(("// строка\n// строка\n" + self.BASE).split("\n"))
        self.assertEqual([c for c, _, _ in before], ["DUP_METHOD"])
        self.assertEqual(structure.new_issues(before, after), [])

    def test_new_issue_next_to_an_old_one_of_the_same_kind(self):
        before = structure.module_issues(self.BASE.split("\n"))
        after = structure.module_issues((self.BASE + "Процедура Б()\nКонецПроцедуры\nПроцедура Б()\n"
                                         "КонецПроцедуры").split("\n"))
        self.assertEqual([(c, n) for c, n, _ in structure.new_issues(before, after)], [("DUP_METHOD", 7)])

    def test_merge_markers_counted_as_many(self):
        conflict = "<<<<<<< new\nА = 1;\n=======\nА = 2;\n>>>>>>> copy\n"
        before = structure.module_issues(("Процедура А()\n" + conflict + "КонецПроцедуры").split("\n"))
        after = structure.module_issues(("Процедура А()\n" + conflict + conflict + "КонецПроцедуры").split("\n"))
        self.assertEqual([c for c, _, _ in structure.new_issues(before, after)], ["MERGE_MARKER"])

    def new(self, before, after):
        return [(c, n) for c, n, _ in structure.new_issues(structure.module_issues(before.split("\n")),
                                                           structure.module_issues(after.split("\n")))]

    def test_a_second_unclosed_if_is_new(self):
        # the module is already broken by one #Если; a candidate that opens a second one must still be refused
        one = Preprocessor.IF_A + "Процедура Б()\nКонецПроцедуры\n"
        self.assertEqual(self.new(one, Preprocessor.IF_A + Preprocessor.IF_B), [("PREPROC_UNBALANCED", 4)])
        self.assertEqual(self.new(one, Preprocessor.IF_A + Preprocessor.IF_A.replace("А()", "Б()")),
                         [("PREPROC_UNBALANCED", 4)])  # the same instruction text twice: counted as many

    def test_closing_one_of_two_unclosed_ifs_is_not_new(self):
        two = Preprocessor.IF_A + Preprocessor.IF_B
        self.assertEqual(self.new(two, Preprocessor.IF_A + Preprocessor.IF_B + "#КонецЕсли\n"), [])
        self.assertEqual(self.new(two, "// x\n// y\n" + two), [])  # moved down: not new

    def test_regions_like_ifs(self):
        one = "#Область А\nПроцедура А()\nКонецПроцедуры\n"
        two = one + "#Область Б\nПроцедура Б()\nКонецПроцедуры\n"
        self.assertEqual(self.new(one, two), [("REGION_UNBALANCED", 4)])
        self.assertEqual(self.new(two, two + "#КонецОбласти\n"), [])
        self.assertEqual(self.new(two, "\n\n" + two), [])

    def test_digits_of_a_method_name_are_part_of_the_issue(self):
        dup = "Процедура Метод%d()\nКонецПроцедуры\nПроцедура Метод%d()\nКонецПроцедуры\n"
        self.assertEqual(self.new(dup % (1, 1), dup % (2, 2)), [("DUP_METHOD", 3)])
        self.assertEqual(self.new(dup % (1, 1), "\n" * 7 + dup % (1, 1)), [])


if __name__ == "__main__":
    unittest.main()
