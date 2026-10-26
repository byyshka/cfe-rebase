# -*- coding: utf-8 -*-
"""Structural checks of a BSL module and of any text file: what a broken port leaves behind before the platform sees it.

merge_markers(lines) - git conflict markers (<<<<<<< ||||||| ======= >>>>>>>) at the start of a line;
bsl_issues(lines)    - &ИзменениеИКонтроль markers (#Вставка/#КонецВставки, #Удаление/#КонецУдаления: unclosed,
                       overlapping, orphan, outside a method or in a method without &ИзменениеИКонтроль, misspelled),
                       method boundaries (not closed, end without a start, Процедура closed by КонецФункции), an
                       interception annotation not followed by a method, duplicate methods (DUP_METHOD, see below),
                       unbalanced #Если / #Область, an unknown preprocessor word.
Each issue is (check, line, detail), lines counted from 1. Marker rules are those of bsl: a marker may carry a trailing
«// …» comment and sits on its own line, inside a multi-line query literal too; code inside #Удаление is not compiled.
An unclosed #Если or #Область is one issue per instruction, at the line that opened it.
DUP_METHOD: two methods with one name where the #Если path of one (the #Если blocks around it with their branch) is a
prefix of the other's - the same branch, or one of them outside it and so compiled everywhere; the conditions are not
evaluated, so methods in different branches of one #Если or in two separate #Если blocks are not duplicates.
new_issues(before, after) - the issues of a changed text that the original did not have (compared by check kind and
detail without its line references «строка N»: those move with every edit, every other number is part of the issue).
"""
import re
from collections import Counter

from . import bsl

MERGE_START = re.compile(r"^<{7}(?:\s|$)")
MERGE_BASE = re.compile(r"^\|{7}(?:\s|$)")
MERGE_SEP = re.compile(r"^={7}\s*$")
MERGE_END = re.compile(r"^>{7}(?:\s|$)")

PRE_WORD = re.compile(r"^\s*#\s*([A-Za-zА-Яа-яЁё_]+)")
PRE_IF = {"если", "if"}
PRE_ELIF = {"иначеесли", "elsif"}
PRE_ELSE = {"иначе", "else"}
PRE_ENDIF = {"конецесли", "endif"}
PRE_REG = {"область", "region"}
PRE_ENDREG = {"конецобласти", "endregion"}
PRE_MARK = {"вставка", "insert", "конецвставки", "endinsert", "удаление", "delete", "конецудаления", "enddelete"}
# spellings that look like markers but are not: the platform does not know them
PRE_TYPO = {"вставить", "удалить", "конецвставить", "конецудалить"}
PRE_KNOWN = PRE_IF | PRE_ELIF | PRE_ELSE | PRE_ENDIF | PRE_REG | PRE_ENDREG | PRE_MARK

MARK_NAME = {"INS": "#Вставка", "DEL": "#Удаление"}
# the only numbers of a detail that are not part of the issue: the line references «(строка %d)» below
LINE_REF = re.compile(r"(?<=строка )\d+(?=\))")


def merge_markers(lines):
    """[(check, line, detail)]: MERGE_MARKER for every conflict start, MERGE_MARKER_ORPHAN for a separator or an end
    outside a conflict."""
    issues = []
    open_at = None
    for n, s in enumerate(lines, 1):
        if MERGE_START.match(s):
            issues.append(("MERGE_MARKER", n, "маркер конфликта слияния: " + s[:40]))
            open_at = n
        elif MERGE_BASE.match(s) or MERGE_SEP.match(s) or MERGE_END.match(s):
            if open_at is None:
                issues.append(("MERGE_MARKER_ORPHAN", n, "маркер конфликта слияния вне конфликта: " + s[:40]))
            if MERGE_END.match(s):
                open_at = None
    return issues


def _mark(raw):
    if bsl.MARK_INS.match(raw):
        return "INS"
    if bsl.MARK_INS_END.match(raw):
        return "INS_END"
    if bsl.MARK_DEL.match(raw):
        return "DEL"
    if bsl.MARK_DEL_END.match(raw):
        return "DEL_END"
    return None


def bsl_issues(lines):
    """[(check, line, detail)] of one module; lines - physical lines without line terminators."""
    issues = []
    lx = bsl.lex_lines(lines)
    n = len(lines)
    block = None          # ("INS"|"DEL", line): the open change block
    meth = None           # the method being walked
    pending = []          # [(annotation, argument, line)] waiting for a declaration
    methods = []
    pre_stack = []        # [[if id, branch, line, text]] of #Если: the branch for duplicates, line and text if unclosed
    reg_stack = []        # [(line, text)] of #Область
    if_seq = 0

    def close_block(where, line):
        nonlocal block
        if block is not None:
            issues.append(("MARKER_UNCLOSED", block[1], "%s (строка %d) не закрыт до %s (строка %d)" % (
                MARK_NAME[block[0]], block[1], where, line)))
            block = None

    for i in range(n):
        raw = lines[i]
        t = lx[i]
        ln = i + 1
        if t.kind == "marker":
            mk = _mark(raw)
            if meth is None:
                issues.append(("MARKER_OUTSIDE_METHOD", ln, "маркер вне метода: " + raw.strip()[:40]))
            elif not meth["control"]:
                issues.append(("MARKER_IN_NON_CONTROL_METHOD", ln, "маркер в методе без &ИзменениеИКонтроль: %s в %s"
                               % (raw.strip()[:30], meth["name"])))
            if mk in ("INS", "DEL"):
                if block is not None:
                    issues.append(("MARKER_OVERLAP", ln, "%s внутри открытого %s (строка %d)" % (
                        MARK_NAME[mk], MARK_NAME[block[0]], block[1])))
                block = (mk, ln)
            else:
                want = "INS" if mk == "INS_END" else "DEL"
                if block is None:
                    issues.append(("MARKER_ORPHAN_END", ln, "закрывающий маркер без открывающего: " + raw.strip()[:40]))
                elif block[0] != want:
                    issues.append(("MARKER_MISMATCH", ln, "%s закрывает %s (строка %d)" % (
                        raw.strip()[:30], MARK_NAME[block[0]], block[1])))
                    block = None
                else:
                    block = None
            continue
        if block is not None and block[0] == "DEL":
            continue  # deleted code is not compiled
        if t.kind == "preproc":
            m = PRE_WORD.match(raw)
            w = m.group(1).casefold() if m else ""
            if w in PRE_TYPO:
                issues.append(("MARKER_MALFORMED", ln, "платформа не знает такого маркера: " + raw.strip()[:40]))
            elif w in PRE_MARK:
                issues.append(("MARKER_MALFORMED", ln, "после маркера текст без «//»: " + raw.strip()[:40]))
            elif w in PRE_IF:
                if_seq += 1
                pre_stack.append([if_seq, 0, ln, raw.strip()[:40]])
            elif w in PRE_ELIF or w in PRE_ELSE:
                if pre_stack:
                    pre_stack[-1][1] += 1
                else:
                    issues.append(("PREPROC_UNBALANCED", ln, "ветка без #Если: " + raw.strip()[:40]))
            elif w in PRE_ENDIF:
                if pre_stack:
                    pre_stack.pop()
                else:
                    issues.append(("PREPROC_UNBALANCED", ln, "#КонецЕсли без #Если"))
            elif w in PRE_REG:
                reg_stack.append((ln, raw.strip()[:40]))
            elif w in PRE_ENDREG:
                if reg_stack:
                    reg_stack.pop()
                else:
                    issues.append(("REGION_UNBALANCED", ln, "#КонецОбласти без #Область"))
            elif w not in PRE_KNOWN:
                issues.append(("PREPROC_UNKNOWN", ln, "неизвестная инструкция препроцессора: " + raw.strip()[:40]))
            continue
        if t.kind != "code":
            continue
        rest = t.code
        anns = []
        while True:
            m = bsl.LEAD_ANN_RE.match(rest)
            if not m:
                break
            anns.append((m.group(1), m.group(2), ln))
            rest = rest[m.end():]
        if anns:
            if meth is not None:
                issues.append(("ANNOTATION_INSIDE_METHOD", ln, "&%s внутри метода %s" % (anns[0][0], meth["name"])))
            pending.extend(anns)
        if rest.strip() == "":
            continue
        md = bsl.DECL_RE.match(rest)
        if md:
            if meth is not None:
                issues.append(("METHOD_NOT_CLOSED", meth["line"], "%s (строка %d) не закрыт до объявления %s "
                               "(строка %d)" % (meth["name"], meth["line"], md.group(3), ln)))
                close_block("следующего метода", ln)
            inter = [(a, arg, al) for a, arg, al in pending if a.casefold() in bsl.INTERCEPT]
            for a, arg, al in inter:
                if arg is None:
                    issues.append(("ANNOTATION_NO_TARGET", al, "&%s без имени метода" % a))
            if len(inter) > 1:
                issues.append(("ANNOTATION_MULTI", inter[0][2], "несколько аннотаций перехвата: " +
                               ", ".join("&" + a for a, _, _ in inter)))
            meth = {"name": md.group(3), "kind": "Функция" if md.group(2).casefold() in bsl.KW_FUNC else "Процедура",
                    "line": ln, "control": any(bsl.INTERCEPT[a.casefold()] == "ИзменениеИКонтроль"
                                               for a, _, _ in inter),
                    "path": tuple((x[0], x[1]) for x in pre_stack)}
            methods.append(meth)
            pending = []
            # a method on one line: its end follows the declaration on the same line
            pos = t.blank.find(md.group(3))
            if pos >= 0 and bsl.END_RE.search(t.blank[pos + len(md.group(3)):]):
                meth = None
            continue
        if pending:
            inter = [(a, al) for a, _, al in pending if a.casefold() in bsl.INTERCEPT]
            if inter:
                issues.append(("ANNOTATION_ORPHAN", inter[0][1], "&%s, а следом код (строка %d), не метод" % (
                    inter[0][0], ln)))
            pending = []
        me = bsl.END_RE.search(t.blank)
        if me:
            if meth is None:
                issues.append(("METHOD_ORPHAN_END", ln, "%s без начала метода" % me.group(1)))
            else:
                end_kind = "Функция" if me.group(1).casefold() in ("конецфункции", "endfunction") else "Процедура"
                if end_kind != meth["kind"]:
                    issues.append(("METHOD_KIND_MISMATCH", ln, "%s %s закрыт %s" % (meth["kind"], meth["name"],
                                                                                    me.group(1))))
                close_block("конца метода " + meth["name"], ln)
                meth = None
    if meth is not None:
        issues.append(("METHOD_NOT_CLOSED", meth["line"], "%s (строка %d) не закрыт до конца модуля" % (
            meth["name"], meth["line"])))
    if block is not None:
        close_block("конца модуля", n)
    if pending:
        inter = [(a, al) for a, _, al in pending if a.casefold() in bsl.INTERCEPT]
        if inter:
            issues.append(("ANNOTATION_ORPHAN", inter[0][1], "&%s в конце модуля без метода" % inter[0][0]))
    for _, _, line, text in pre_stack:
        issues.append(("PREPROC_UNBALANCED", line, "нет #КонецЕсли до конца модуля: " + text))
    for line, text in reg_stack:
        issues.append(("REGION_UNBALANCED", line, "нет #КонецОбласти до конца модуля: " + text))
    # duplicates: the same name where one #Если path is a prefix of the other (a path - the (#Если, branch) pairs from
    # the outermost); conditions are not evaluated, so two separate #Если blocks are never compared
    by_name = {}
    for m in methods:
        by_name.setdefault(m["name"].casefold(), []).append(m)
    for ms in by_name.values():
        for a in range(len(ms)):
            for b in range(a + 1, len(ms)):
                pa, pb = sorted((ms[a]["path"], ms[b]["path"]), key=len)
                if pb[:len(pa)] == pa:
                    issues.append(("DUP_METHOD", ms[b]["line"], "%s объявлен второй раз (первый — строка %d)" % (
                        ms[b]["name"], ms[a]["line"])))
    return issues


def module_issues(lines):
    """Every issue of a BSL module: merge markers and structure, in line order."""
    return sorted(merge_markers(lines) + bsl_issues(lines), key=lambda x: (x[1], x[0]))


def _key(issue):
    # line numbers move with every edit: an issue is the same if its kind and its text without line references are;
    # other digits stay (a method name, an instruction text), so two issues differing only there are two issues
    return issue[0], LINE_REF.sub("#", issue[2])


def new_issues(before, after):
    """Issues of `after` that `before` does not have (both - lists of issues); repeated issues count as a multiset."""
    have = Counter(_key(x) for x in before)
    out = []
    for issue in after:
        k = _key(issue)
        if have[k]:
            have[k] -= 1
        else:
            out.append(issue)
    return out
