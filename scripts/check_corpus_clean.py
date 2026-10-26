# -*- coding: utf-8 -*-
"""Check that no line of the synthetic test corpus is taken from a real configuration dump.

    python scripts/check_corpus_clean.py --real DIR [DIR ...] [--corpus tests/corpus/synthetic] [--min-len 40]

Takes every line of the corpus .bsl files that is at least --min-len characters long after trimming and searches the
real dumps (*.bsl) for each one as a fixed string: rg -F if rg is on PATH, else grep -rF, else git grep --no-index -F
(git is required by the project anyway). --real names the configuration dumps to compare with (vendor releases,
extensions); they are not part of the repository, so the check runs only locally.

Not searched, because every configuration shares them and none of them is vendor code:
  * purely syntactic lines - nothing but keywords, punctuation and numbers;
  * region lines (#Область / #КонецОбласти): the region names are fixed by the 1C development standards;
  * the declaration of a platform event handler (Процедура ОбработкаПроведения(Отказ, РежимПроведения)): the
    Designer generates it with the same name and parameters in every configuration.

Exit code: 0 - no corpus line occurs in the real dumps; 1 - some do (each printed with the files it occurs in);
2 - the check could not run (a dump folder is missing, no search tool).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CORPUS = os.path.join(ROOT, "tests", "corpus", "synthetic")
IDENT_RE = re.compile(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*")
# BSL statements, preprocessor and annotation words, query language keywords, literals: a line made only of them
# (and of punctuation and numbers) is syntax that any module may share by chance.
SYNTAX_WORDS = {w.casefold() for w in """
Если Тогда ИначеЕсли Иначе КонецЕсли Для Каждого Из По Цикл КонецЦикла Пока Возврат Новый И Или Не Попытка
Исключение КонецПопытки ВызватьИсключение Процедура Функция КонецПроцедуры КонецФункции Перем Экспорт Знач Ждать
Асинх Прервать Продолжить Выполнить Перейти ДобавитьОбработчик УдалитьОбработчик Истина Ложь Неопределено NULL
Область КонецОбласти Вставка КонецВставки Удаление КонецУдаления Сервер Клиент ТонкийКлиент ВебКлиент
ТолстыйКлиентОбычноеПриложение ТолстыйКлиентУправляемоеПриложение ВнешнееСоединение МобильноеПриложениеСервер
МобильноеПриложениеКлиент МобильныйКлиент НаСервере НаКлиенте НаСервереБезКонтекста НаКлиентеНаСервереБезКонтекста
НаКлиентеНаСервере Перед После Вместо ИзменениеИКонтроль
ВЫБРАТЬ РАЗРЕШЕННЫЕ РАЗЛИЧНЫЕ ПЕРВЫЕ ПОМЕСТИТЬ КАК ГДЕ СОЕДИНЕНИЕ ЛЕВОЕ ПРАВОЕ ВНУТРЕННЕЕ ПОЛНОЕ ВНЕШНЕЕ
ОБЪЕДИНИТЬ ВСЕ СГРУППИРОВАТЬ ИМЕЮЩИЕ УПОРЯДОЧИТЬ ВОЗР УБЫВ ИТОГИ ОБЩИЕ ВЫБОР КОГДА КОНЕЦ ЕСТЬ ПОДОБНО МЕЖДУ
В ИЕРАРХИИ ДЛЯ ИЗМЕНЕНИЯ УНИЧТОЖИТЬ ИНДЕКСИРОВАТЬ АВТОУПОРЯДОЧИВАНИЕ
If Then ElsIf Else EndIf For Each In To Do EndDo While Return New And Or Not Try Except EndTry Raise Procedure
Function EndProcedure EndFunction Var Export Val Await Async Break Continue True False Undefined Region EndRegion
Insert EndInsert Delete EndDelete SELECT FROM WHERE AS
""".split()}


PLATFORM_EVENTS = """
ОбработкаПроведения ОбработкаУдаленияПроведения ПередЗаписью ПриЗаписи ПередУдалением ОбработкаПроверкиЗаполнения
ОбработкаЗаполнения ПриКопировании ПриУстановкеНовогоНомера ПриУстановкеНовогоКода ОбработкаПолученияДанныхВыбора
ОбработкаПолученияПредставления ОбработкаПолученияПолейПредставления ОбработкаПолученияФормы ПриНачалеРаботыСистемы
ПередНачаломРаботыСистемы ПередЗавершениемРаботыСистемы ПриЗавершенииРаботыСистемы УстановкаПараметровСеанса
ПриСозданииНаСервере ПриОткрытии ПередЗакрытием ПриЗакрытии ОбработкаОповещения ОбработкаВыбора ПриЧтенииНаСервере
ПередЗаписьюНаСервере ПриЗаписиНаСервере ПослеЗаписиНаСервере ПослеЗаписи ОбработкаПроверкиЗаполненияНаСервере
ПриЗагрузкеДанныхИзНастроекНаСервере ПередЗагрузкойДанныхИзНастроекНаСервере ПриСохраненииДанныхВНастройкахНаСервере
ОбработкаАктивизацииОбъекта ОбработкаНавигационнойСсылки ОбработкаКоманды ПриКомпоновкеРезультата
""".split()
EVENT_DECL_RE = re.compile(r"^(?:Асинх\s+)?(?:Процедура|Функция)\s+(?:%s)\s*\(" % "|".join(PLATFORM_EVENTS),
                           re.IGNORECASE)
REGION_RE = re.compile(r"^#\s*(?:Область|КонецОбласти|Region|EndRegion)(?![\wЁё])", re.IGNORECASE)


def is_syntax(line):
    """A line every configuration may share: keywords only, a region line, a platform event handler declaration."""
    if REGION_RE.match(line) or EVENT_DECL_RE.match(line):
        return True
    return all(w.casefold() in SYNTAX_WORDS for w in IDENT_RE.findall(line))


def corpus_lines(corpus, min_len):
    """{trimmed line: [corpus files]} of the lines to look for."""
    found = {}
    for dp, _, files in os.walk(corpus):
        for f in sorted(files):
            if not f.lower().endswith(".bsl"):
                continue
            path = os.path.join(dp, f)
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8-sig")
            for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                s = raw.strip(" \t\xa0")
                if len(s) >= min_len and not is_syntax(s):
                    found.setdefault(s, []).append(os.path.relpath(path, corpus))
    return found


def search_command(patterns_file, root):
    """(argv, cwd, tool name) listing the *.bsl files under root that contain any pattern, NUL-separated."""
    rg = shutil.which("rg")
    if rg:
        return [rg, "-F", "-l", "--null", "--no-messages", "-f", patterns_file, "--glob", "*.bsl", root], None, "rg"
    grep = shutil.which("grep")
    if grep:
        return [grep, "-r", "-F", "-l", "-Z", "-f", patterns_file, "--include=*.bsl", root], None, "grep"
    git = shutil.which("git")
    if git:
        return [git, "-c", "core.quotepath=false", "grep", "--no-index", "-F", "-l", "-z", "-f", patterns_file,
                "--", "*.bsl"], root, "git grep"
    return None, None, None


def search(patterns, root):
    """(tool name, [files under root containing at least one pattern])."""
    fd, pfile = tempfile.mkstemp(prefix="corpus_lines_", suffix=".txt")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(("\n".join(patterns) + "\n").encode("utf-8"))
        argv, cwd, tool = search_command(pfile, root)
        if argv is None:
            sys.exit("no search tool: neither rg, grep nor git is on PATH")
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, env=dict(os.environ, LC_ALL="C"))
        if proc.returncode not in (0, 1):
            sys.exit("%s failed on %s: %s" % (tool, root, proc.stderr.decode("utf-8", "replace").strip()))
        names = [n for n in proc.stdout.decode("utf-8", "replace").split("\0") if n.strip()]
        return tool, [n if os.path.isabs(n) or cwd is None else os.path.join(cwd, n) for n in names]
    finally:
        os.remove(pfile)


def main(argv=None):
    ap = argparse.ArgumentParser(description="No line of the synthetic corpus may occur in a real dump")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS, help="corpus root (default: tests/corpus/synthetic)")
    ap.add_argument("--real", nargs="+", required=True, help="real configuration dumps to search (*.bsl)")
    ap.add_argument("--min-len", type=int, default=40, help="shortest trimmed line to check (default 40)")
    args = ap.parse_args(argv)
    missing = [r for r in args.real if not os.path.isdir(r)]
    if missing:
        print("real dump not found: %s" % ", ".join(missing), file=sys.stderr)
        return 2
    lines = corpus_lines(args.corpus, args.min_len)
    if not lines:
        print("no corpus line of %d+ characters under %s" % (args.min_len, args.corpus), file=sys.stderr)
        return 2
    hits = {}
    for root in args.real:
        tool, files = search(sorted(lines), root)
        print("%s: %d corpus lines searched with %s in %s, files matched: %d" % (
            os.path.basename(args.corpus.rstrip("\\/")), len(lines), tool, root, len(files)))
        for path in files:
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8", "replace")
            for s in lines:
                if s in text:
                    hits.setdefault(s, []).append(path)
    if not hits:
        print("clean: no corpus line occurs in the real dumps")
        return 0
    print("NOT CLEAN: %d corpus lines occur in the real dumps" % len(hits))
    for s, paths in sorted(hits.items()):
        print("- %s" % s)
        print("    corpus: %s" % ", ".join(sorted(set(lines[s]))))
        for p in paths[:5]:
            print("    real:   %s" % p)
        if len(paths) > 5:
            print("    real:   ... and %d more" % (len(paths) - 5))
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
