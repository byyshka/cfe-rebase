# -*- coding: utf-8 -*-
"""BSL module parser for migration review.

Two layers over the same text:
  * a line lexer (lex_lines) - every physical line gets a kind and its code with comments removed;
    methods, annotations, compile directives, parameters and bodies come from it;
  * a tokenizer (tokenize) - identifiers, strings, operators with line numbers; calls and their
    argument counts come from it.
Comments never produce code. String literals are kept as one token (multi-line literals follow the
BSL rules: a continuation line starts with '|', a line starting with '//' inside a literal is a comment).
Code inside #Удаление ... #КонецУдаления is dropped by the tokenizer (it does not run), but kept in
lines so that &ИзменениеИКонтроль bodies can be reconstructed.
"""
import re

from . import fileio

IDENT = r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*"
IDENT_RE = re.compile(IDENT)
NUM_RE = re.compile(r"\d+(?:\.\d+)?")

DECL_RE = re.compile(r"^\s*(?:(Асинх|Async)\s+)?(Процедура|Функция|Procedure|Function)\s+(" + IDENT + r")\s*\(",
                     re.IGNORECASE)
# anywhere in the code of a line: vendor modules have "КонецЕсли;КонецПроцедуры"
END_RE = re.compile(r"(?<![\wЁё])(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)(?![\wЁё])", re.IGNORECASE)
LEAD_ANN_RE = re.compile(r"^\s*&\s*(" + IDENT + r")\s*(?:\(\s*\"((?:[^\"]|\"\")*)\"\s*\))?")
EXPORT_RE = re.compile(r"^\s*(Экспорт|Export)(?![\wЁё])", re.IGNORECASE)
PROCEED_RE = re.compile(r"(?<![\wЁё.])(ПродолжитьВызов|ProceedWithCall)\s*\(", re.IGNORECASE)

# A marker may carry a trailing line comment (`#Вставка // Иванов 24.12 - причина`): the platform accepts it.
_MARK_TAIL = r"\s*(?://.*)?$"
MARK_INS = re.compile(r"^\s*#\s*(Вставка|Insert)" + _MARK_TAIL, re.IGNORECASE)
MARK_INS_END = re.compile(r"^\s*#\s*(КонецВставки|EndInsert)" + _MARK_TAIL, re.IGNORECASE)
MARK_DEL = re.compile(r"^\s*#\s*(Удаление|Delete)" + _MARK_TAIL, re.IGNORECASE)
MARK_DEL_END = re.compile(r"^\s*#\s*(КонецУдаления|EndDelete)" + _MARK_TAIL, re.IGNORECASE)

INTERCEPT = {
    "вместо": "Вместо", "around": "Вместо",
    "перед": "Перед", "before": "Перед",
    "после": "После", "after": "После",
    "изменениеиконтроль": "ИзменениеИКонтроль", "changeandvalidate": "ИзменениеИКонтроль",
}
DIRECTIVES = {
    "насервере": "НаСервере", "atserver": "НаСервере",
    "наклиенте": "НаКлиенте", "atclient": "НаКлиенте",
    "насерверебезконтекста": "НаСервереБезКонтекста", "atservernocontext": "НаСервереБезКонтекста",
    "наклиентенасерверебезконтекста": "НаКлиентеНаСервереБезКонтекста",
    "atclientatservernocontext": "НаКлиентеНаСервереБезКонтекста",
    "наклиентенасервере": "НаКлиентеНаСервере", "atclientatserver": "НаКлиентеНаСервере",
}

KW_PROC = {"процедура", "procedure"}
KW_FUNC = {"функция", "function"}
KW_NEW = {"новый", "new"}
KEYWORDS = {x.lower() for x in """
Если Тогда ИначеЕсли Иначе КонецЕсли Для Каждого Из По Цикл КонецЦикла Пока Возврат Новый И Или Не
Попытка Исключение КонецПопытки ВызватьИсключение Процедура Функция КонецПроцедуры КонецФункции Перем
Экспорт Знач Ждать Асинх Прервать Продолжить Выполнить Перейти ДобавитьОбработчик УдалитьОбработчик
If Then ElsIf Else EndIf For Each In To Do EndDo While Return New And Or Not Try Except EndTry Raise
Procedure Function EndProcedure EndFunction Var Export Val Await Async Break Continue Execute Goto
AddHandler RemoveHandler
""".split()}

# Platform events: interception of an event with no vendor handler is allowed by the platform.
EVENTS = {x.lower() for x in """
ОбработкаПроведения ОбработкаУдаленияПроведения ПередЗаписью ПриЗаписи ПередУдалением ОбработкаПроверкиЗаполнения
ОбработкаЗаполнения ПриКопировании ПриУстановкеНовогоНомера ПриУстановкеНовогоКода ОбработкаПолученияДанныхВыбора
ОбработкаПолученияПредставления ОбработкаПолученияПолейПредставления ОбработкаПолученияФормы
ОбработкаПолученияСтатистикиПоПолямСравнения ПриНачалеРаботыСистемы ПередНачаломРаботыСистемы
ПередЗавершениемРаботыСистемы ПриЗавершенииРаботыСистемы ОбработкаВнешнегоСобытия ОбработкаОтображенияОшибки
УстановкаПараметровСеанса ПриСозданииНаСервере ПриОткрытии ПередЗакрытием ПриЗакрытии ОбработкаОповещения
ОбработкаВыбора ПриЧтенииНаСервере ПередЗаписьюНаСервере ПриЗаписиНаСервере ПослеЗаписиНаСервере ПослеЗаписи
ОбработкаПроверкиЗаполненияНаСервере ПриЗагрузкеДанныхИзНастроекНаСервере ПередЗагрузкойДанныхИзНастроекНаСервере
ПриСохраненииДанныхВНастройкахНаСервере ВнешнееСобытие ОбработкаАктивизацииОбъекта ОбработкаНавигационнойСсылки
ПриИзмененииПараметровЭкрана ОбработкаКоманды
""".split()}


# ----------------------------------------------------------------------------------------------------------------
# Line lexer
# ----------------------------------------------------------------------------------------------------------------

def _scan_line(s, in_str):
    """Scan one physical line. Returns (blank, keep, in_str_after).

    blank: comment removed, string contents blanked; keep: comment removed, strings kept."""
    blank = []
    keep = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if in_str:
            if c == '"':
                if i + 1 < n and s[i + 1] == '"':
                    keep.append('""')
                    i += 2
                    continue
                in_str = False
                blank.append('"')
                keep.append('"')
                i += 1
                continue
            keep.append(c)
            i += 1
            continue
        if c == '"':
            in_str = True
            blank.append('"')
            keep.append('"')
            i += 1
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j == -1:
                j = n - 1
            blank.append("''")
            keep.append(s[i:j + 1])
            i = j + 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            break
        blank.append(c)
        keep.append(c)
        i += 1
    return "".join(blank), "".join(keep), in_str


class Line(object):
    __slots__ = ("kind", "blank", "code")

    def __init__(self, kind, blank="", code=""):
        self.kind = kind    # code | strcont | comment | strcomment | empty | marker | preproc
        self.blank = blank  # code with string contents blanked
        self.code = code    # code with comments removed, strings kept, indentation kept


def lex_lines(lines):
    """Line kinds. Inside #Удаление the string state is restored afterwards (the effective variant)."""
    out = []
    in_str = False
    saved = None
    for raw in lines:
        st = raw.strip(" \t﻿\xa0")
        if MARK_INS.match(raw) or MARK_INS_END.match(raw) or MARK_DEL.match(raw) or MARK_DEL_END.match(raw):
            if MARK_DEL.match(raw):
                saved = in_str
            elif MARK_DEL_END.match(raw) and saved is not None:
                in_str = saved
                saved = None
            out.append(Line("marker", "", st))
            continue
        if in_str:
            if st.startswith("//"):
                out.append(Line("strcomment"))
                continue
            if st.startswith("|"):
                pos = raw.index("|")
                b, k, in_str = _scan_line(raw[pos + 1:], True)
                out.append(Line("strcont", '"' + b, raw[:pos + 1] + k))
                continue
            if st == "":
                out.append(Line("empty"))
                continue
            in_str = False  # malformed continuation: the literal ended at the line break
        if st == "":
            out.append(Line("empty"))
            continue
        if st.startswith("//"):
            out.append(Line("comment"))
            continue
        if st.startswith("#"):
            out.append(Line("preproc", st, st))
            continue
        b, k, in_str = _scan_line(raw, False)
        out.append(Line("code", b, k.rstrip()))
    return out


# ----------------------------------------------------------------------------------------------------------------
# Methods
# ----------------------------------------------------------------------------------------------------------------

def split_params(text):
    """Parameter list text (inside the parentheses) -> [{'name', 'byval', 'default'}]."""
    parts = []
    cur = []
    depth = 0
    in_str = False
    i = 0
    while i < len(text):
        c = text[i]
        if in_str:
            cur.append(c)
            if c == '"':
                if i + 1 < len(text) and text[i + 1] == '"':
                    cur.append('"')
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            cur.append(c)
        elif c == "(":
            depth += 1
            cur.append(c)
        elif c == ")":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    res = []
    for p in parts:
        p = " ".join(p.split())
        if not p:
            continue
        m = re.match(r"^(Знач|Val)\s+(.*)$", p, re.IGNORECASE)
        rest = m.group(2) if m else p
        name, eq, default = rest.partition("=")
        res.append({"name": name.strip(), "byval": bool(m), "default": default.strip() if eq else None})
    return res


def norm_default(d):
    return None if d is None else d.casefold().replace(" ", "")


class Method(object):
    __slots__ = ("name", "kind", "is_async", "export", "params", "params_raw", "directives", "interceptors",
                 "annotations", "start_line", "decl_line", "decl_end_line", "end_line", "has_proceed", "markers")

    def __init__(self):
        self.name = None
        self.kind = None             # Процедура | Функция
        self.is_async = False
        self.export = False
        self.params = []
        self.params_raw = ""
        self.directives = []         # normalized compile directives, e.g. ['НаКлиенте']
        self.interceptors = []       # [(kind, target, line)] kind: Вместо | Перед | После | ИзменениеИКонтроль
        self.annotations = []        # other annotations as written, e.g. ['&Что-то']
        self.start_line = 0          # first annotation line (or decl line)
        self.decl_line = 0           # line of Процедура/Функция
        self.decl_end_line = 0       # line of the closing parenthesis of the parameter list
        self.end_line = 0            # line of КонецПроцедуры/КонецФункции
        self.has_proceed = False     # ПродолжитьВызов( in live code of the body
        self.markers = False         # #Вставка/#Удаление inside the body

    def required(self):
        return sum(1 for p in self.params if p["default"] is None)

    def total(self):
        return len(self.params)

    def signature(self):
        parts = []
        for p in self.params:
            s = ("Знач " if p["byval"] else "") + p["name"]
            if p["default"] is not None:
                s += " = " + p["default"]
            parts.append(s)
        return "%s%s %s(%s)%s" % ("Асинх " if self.is_async else "", self.kind, self.name, ", ".join(parts),
                                  " Экспорт" if self.export else "")

    def sig_key(self):
        """What the platform compares for interception and what a caller depends on."""
        return (self.kind, self.is_async, self.export,
                tuple((p["name"].casefold(), p["byval"], norm_default(p["default"])) for p in self.params))

    def to_dict(self):
        return {"name": self.name, "kind": self.kind, "async": self.is_async, "export": self.export,
                "params": self.params, "directives": self.directives,
                "interceptors": [{"kind": k, "target": t, "line": ln} for k, t, ln in self.interceptors],
                "start_line": self.start_line, "decl_line": self.decl_line, "end_line": self.end_line,
                "has_proceed": self.has_proceed, "signature": self.signature()}


def parse_methods(lines, lx):
    methods = []
    pending = []  # (annotation name, argument or None, line number)
    i = 0
    n = len(lines)
    while i < n:
        t = lx[i]
        if t.kind in ("empty", "comment", "preproc"):
            i += 1
            continue
        if t.kind != "code":
            pending = []
            i += 1
            continue
        rest = t.code
        while True:
            m = LEAD_ANN_RE.match(rest)
            if not m:
                break
            arg = m.group(2).replace('""', '"') if m.group(2) is not None else None
            pending.append((m.group(1), arg, i + 1))
            rest = rest[m.end():]
        if rest.strip() == "":
            i += 1
            continue
        md = DECL_RE.match(rest)
        if not md:
            pending = []
            i += 1
            continue
        meth = Method()
        meth.name = md.group(3)
        meth.kind = "Функция" if md.group(2).lower() in KW_FUNC else "Процедура"
        meth.is_async = bool(md.group(1))
        meth.decl_line = i + 1
        # parameter text up to the matching ')'
        buf = rest[md.end():]
        j = i
        depth = 1
        ptext = []
        after = ""
        while True:
            k = 0
            in_s = False
            done = False
            while k < len(buf):
                c = buf[k]
                if in_s:
                    if c == '"':
                        if k + 1 < len(buf) and buf[k + 1] == '"':
                            k += 2
                            continue
                        in_s = False
                    k += 1
                    continue
                if c == '"':
                    in_s = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        ptext.append(buf[:k])
                        after = buf[k + 1:]
                        done = True
                        break
                k += 1
            if done:
                break
            ptext.append(buf)
            j += 1
            if j >= n:
                break
            buf = lx[j].code if lx[j].kind in ("code", "strcont") else ""
        meth.decl_end_line = j + 1
        meth.params_raw = " ".join(" ".join(ptext).split())
        meth.params = split_params(" ".join(ptext))
        meth.export = bool(EXPORT_RE.match(after))
        e = j + 1
        while e < n:
            if lx[e].kind == "code" and END_RE.search(lx[e].blank):
                break
            e += 1
        meth.end_line = min(e + 1, n)
        meth.start_line = pending[0][2] if pending else meth.decl_line
        for name, arg, ln in pending:
            low = name.lower()
            if low in INTERCEPT:
                meth.interceptors.append((INTERCEPT[low], arg, ln))
            elif low in DIRECTIVES:
                meth.directives.append(DIRECTIVES[low])
            else:
                meth.annotations.append("&" + name + ('("%s")' % arg if arg is not None else ""))
        in_delete = False
        for b in range(j + 1, e):
            if lx[b].kind == "marker":
                meth.markers = True
                if MARK_DEL.match(lines[b]):
                    in_delete = True
                elif MARK_DEL_END.match(lines[b]):
                    in_delete = False
                continue
            if not in_delete and lx[b].kind == "code" and PROCEED_RE.search(lx[b].blank):
                meth.has_proceed = True
        methods.append(meth)
        pending = []
        i = e + 1
    return methods


# ----------------------------------------------------------------------------------------------------------------
# Tokenizer and calls
# ----------------------------------------------------------------------------------------------------------------

def tokenize(text, drop_deleted=True):
    """Tokens (kind, value, line); kinds: id, str, num, date, op, pre, ann. Comments are dropped."""
    toks = []
    i = 0
    n = len(text)
    line = 1
    in_delete = False
    at_line_start = True
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            at_line_start = True
            continue
        if c in " \t\r﻿\xa0":
            i += 1
            continue
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        live = not (drop_deleted and in_delete)
        if c == "#" and at_line_start:
            j = text.find("\n", i)
            j = n if j < 0 else j
            directive = text[i:j].strip()
            if MARK_DEL.match(directive):
                in_delete = True
            elif MARK_DEL_END.match(directive):
                in_delete = False
            elif live:
                toks.append(("pre", directive, line))
            i = j
            continue
        at_line_start = False
        if c == '"':
            start_line = line
            j = i + 1
            buf = []
            lit_del = False  # inside a #Удаление opened in this literal (drop_deleted=False only)
            while j < n:
                ch = text[j]
                if ch == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    j += 1
                    break
                if ch == "\n":
                    # continuation: blanks, then '|' continues, '//' is a comment line, empty line is kept
                    k = j + 1
                    while k < n and text[k] in " \t\r\xa0":
                        k += 1
                    if text.startswith("//", k):
                        e = text.find("\n", k)
                        line += 1
                        buf.append("\n")
                        j = n if e < 0 else e
                        continue
                    if k < n and text[k] == "#":
                        # A change marker does not end the literal: the platform removes marker lines and
                        # #Удаление blocks before compiling. A skipped line keeps its "\n" in the value, so a
                        # position in the literal still maps to its source line.
                        e = text.find("\n", k)
                        e = n if e < 0 else e
                        mark = text[k:e]
                        if drop_deleted and MARK_DEL.match(mark):
                            while True:  # the block up to its #КонецУдаления inclusive
                                line += 1
                                buf.append("\n")
                                if e >= n or MARK_DEL_END.match(text[k:e]):
                                    break
                                k = e + 1
                                e = text.find("\n", k)
                                e = n if e < 0 else e
                            j = e
                            continue
                        if MARK_DEL.match(mark):
                            lit_del = True
                        elif lit_del and MARK_DEL_END.match(mark):
                            lit_del = False
                        elif not (MARK_INS.match(mark) or MARK_INS_END.match(mark)):
                            # not a marker, or #КонецУдаления of a deletion opened before the literal: the
                            # literal ends here as before, the line is handled outside it
                            break
                        line += 1
                        buf.append("\n")
                        j = e
                        continue
                    if k < n and text[k] == "|":
                        line += 1
                        buf.append("\n")
                        j = k + 1
                        continue
                    if k < n and text[k] == "\n":
                        line += 1
                        buf.append("\n")
                        j = k
                        continue
                    break  # malformed literal: it ends at the line break
                buf.append(ch)
                j += 1
            if live:
                toks.append(("str", "".join(buf), start_line))
            i = j
            continue
        if c == "'":
            j = text.find("'", i + 1)
            e = text.find("\n", i + 1)
            if j < 0 or (0 <= e < j):
                j = e if e >= 0 else n
            else:
                j += 1
            if live:
                toks.append(("date", text[i:j], line))
            i = j
            continue
        if c == "&":
            m = IDENT_RE.match(text, i + 1)
            if m:
                j = m.end()
                arg = None
                k = j
                while k < n and text[k] in " \t":
                    k += 1
                if k < n and text[k] == "(":
                    e = text.find(")", k)
                    if e > 0:
                        arg = text[k + 1:e].strip().strip('"')
                        j = e + 1
                if live:
                    toks.append(("ann", (m.group(0), arg), line))
                i = j
                continue
        m = IDENT_RE.match(text, i)
        if m:
            if live:
                toks.append(("id", m.group(0), line))
            i = m.end()
            continue
        m = NUM_RE.match(text, i)
        if m:
            if live:
                toks.append(("num", m.group(0), line))
            i = m.end()
            continue
        two = text[i:i + 2]
        if two in ("<>", "<=", ">="):
            if live:
                toks.append(("op", two, line))
            i += 2
            continue
        if live:
            toks.append(("op", c, line))
        i += 1
    return toks


def _tok_text(t):
    return '"%s"' % t[1] if t[0] == "str" else (t[1][0] if t[0] == "ann" else t[1])


def extract_calls(toks, start=0, end=None):
    """Call records: {'chain', 'name', 'args', 'line', 'tok', 'arg_list', 'norm', 'dyn'}.

    chain - qualifiers before the name: Документы.X.Метод( -> ['Документы', 'X'];
    ОбщийМодуль("М").Метод( -> ['@module', 'М']; МенеджерОбъектаПоПолномуИмени("Вид.Имя").Метод( ->
    ['@manager', 'Вид.Имя']; a qualifier that is an index or another call result -> ['?', ...]."""
    if end is None:
        end = len(toks)
    for i in range(start, min(end, len(toks)) - 1):
        k, v, line = toks[i]
        if k != "id":
            continue
        if toks[i + 1][0] != "op" or toks[i + 1][1] != "(":
            continue
        if i > 0 and toks[i - 1][0] == "id" and toks[i - 1][1].lower() in (KW_PROC | KW_FUNC | KW_NEW):
            continue
        depth = 0
        j = i + 1
        commas = 0
        nonempty = False
        arg_texts = [[]]
        while j < len(toks):
            tk, tv, _ = toks[j]
            if tk == "op" and tv in ("(", "["):
                depth += 1
                if depth >= 2:
                    arg_texts[-1].append(tv)
                    nonempty = True
            elif tk == "op" and tv in (")", "]"):
                depth -= 1
                if depth == 0:
                    break
                arg_texts[-1].append(tv)
            elif depth == 1 and tk == "op" and tv == ",":
                commas += 1
                arg_texts.append([])
            else:
                nonempty = True
                arg_texts[-1].append(_tok_text(toks[j]))
            j += 1
        args = 0 if (commas == 0 and not nonempty) else commas + 1
        arg_list = [" ".join(a) for a in arg_texts] if args else []
        norm = (v + "".join(_tok_text(t) for t in toks[i + 1:j + 1])).lower()
        chain = []
        dyn = None
        p = i - 1
        while p >= 1 and toks[p][0] == "op" and toks[p][1] == ".":
            q = p - 1
            qk, qv, _ = toks[q]
            if qk == "id":
                chain.insert(0, qv)
                p = q - 1
                continue
            if qk == "op" and qv == ")":
                if (q - 3 >= 0 and toks[q - 1][0] == "str" and toks[q - 2][0] == "op" and toks[q - 2][1] == "("
                        and toks[q - 3][0] == "id"):
                    fn = toks[q - 3][1].lower()
                    if fn in ("общиймодуль", "commonmodule"):
                        chain = ["@module", toks[q - 1][1]] + chain
                        dyn = toks[q - 1][1]
                        break
                    if fn in ("менеджеробъектапополномуимени", "objectmanagerbyfullname"):
                        chain = ["@manager", toks[q - 1][1]] + chain
                        dyn = toks[q - 1][1]
                        break
                chain = ["?"] + chain
                break
            chain = ["?"] + chain
            break
        yield {"chain": chain, "name": v, "args": args, "line": line, "tok": i, "arg_list": arg_list,
               "norm": norm, "dyn": dyn}


def assigned_names(toks, start, end):
    """Identifiers assigned or declared inside a token range (locals, loop variables)."""
    names = set()
    for i in range(start, min(end, len(toks) - 1)):
        k, v, _ = toks[i]
        if k != "id":
            continue
        nk, nv, _ = toks[i + 1]
        if nk == "op" and nv == "=" and not (i > 0 and toks[i - 1][0] == "op" and toks[i - 1][1] == "."):
            names.add(v.casefold())
        low = v.lower()
        if low in ("перем", "var", "каждого", "each") and nk == "id":
            names.add(nv.casefold())
    return names


def module_vars(toks, methods_ranges):
    """Module-level Перем names (outside any method)."""
    names = set()
    for i in range(len(toks) - 1):
        k, v, line = toks[i]
        if k != "id" or v.lower() not in ("перем", "var"):
            continue
        if any(a <= line <= b for a, b in methods_ranges):
            continue
        j = i + 1
        while j < len(toks) and toks[j][0] in ("id", "op") and toks[j][1] != ";":
            if toks[j][0] == "id" and toks[j][1].lower() not in ("экспорт", "export"):
                names.add(toks[j][1].casefold())
            j += 1
    return names


# ----------------------------------------------------------------------------------------------------------------
# Module
# ----------------------------------------------------------------------------------------------------------------

class Module(object):
    """Parsed module. Tokens are built on first use (only extension modules need them)."""

    def __init__(self, text, path=None):
        self.path = path
        self.lines = text.split("\n")
        self.lx = lex_lines(self.lines)
        self.methods = parse_methods(self.lines, self.lx)
        self.by_name = {}
        for m in self.methods:
            self.by_name.setdefault(m.name.casefold(), []).append(m)
        self._toks = None
        self._text = text

    @property
    def toks(self):
        if self._toks is None:
            self._toks = tokenize(self._text)
        return self._toks

    def find(self, name):
        if not name:
            return None
        lst = self.by_name.get(name.casefold())
        return lst[0] if lst else None

    def method_at_line(self, line):
        for m in self.methods:
            if m.decl_line <= line <= m.end_line:
                return m
        return None

    def tok_range(self, method):
        """Token index range [a, b) of a method body (after the parameter list)."""
        toks = self.toks
        a = b = None
        for idx, t in enumerate(toks):
            if a is None and t[2] > method.decl_end_line:
                a = idx
            if t[2] > method.end_line:
                b = idx
                break
        if a is None:
            a = len(toks)
        if b is None:
            b = len(toks)
        return a, b

    def calls(self):
        for c in extract_calls(self.toks):
            c["method"] = self.method_at_line(c["line"])
            if c["method"] is not None and c["line"] <= c["method"].decl_end_line:
                continue  # parameter defaults are not calls
            yield c

    def body_lines(self, method, with_header=True):
        """[(line_no, raw_text)] of a method, from the first annotation (or declaration) to the end line."""
        a = method.start_line if with_header else method.decl_end_line + 1
        b = method.end_line
        return [(k, self.lines[k - 1]) for k in range(a, b + 1)]


def load_module(path):
    return Module(fileio.read_text(path), path)


# ----------------------------------------------------------------------------------------------------------------
# Body normalization for diffs
# ----------------------------------------------------------------------------------------------------------------

def norm_ws(text):
    return " ".join(text.split())


def body_code(module, method, reconstruct=None):
    """[(line_no, normalized_text)] of a method from declaration to end, whitespace collapsed,
    empty lines and comment lines dropped, trailing comments removed.

    reconstruct: None - lines as written; 'original' - &ИзменениеИКонтроль view of the base text
    (#Вставка blocks dropped, #Удаление contents kept); 'effective' - what runs (#Удаление dropped)."""
    out = []
    state = None
    for k in range(method.decl_line, method.end_line + 1):
        raw = module.lines[k - 1]
        t = module.lx[k - 1]
        if MARK_INS.match(raw):
            state = "ins"
            continue
        if MARK_DEL.match(raw):
            state = "del"
            continue
        if MARK_INS_END.match(raw) or MARK_DEL_END.match(raw):
            state = None
            continue
        if reconstruct == "original" and state == "ins":
            continue
        if reconstruct == "effective" and state == "del":
            continue
        if t.kind in ("empty", "comment", "strcomment", "marker"):
            continue
        text = norm_ws(t.code if t.kind in ("code", "strcont", "preproc") else raw)
        if text:
            out.append((k, text))
    return out


def body_control(module, method):
    """&ИзменениеИКонтроль control view: lines trimmed on the edges, empty lines dropped,
    #Вставка blocks dropped, #Удаление contents kept (comments are part of the controlled text)."""
    out = []
    state = None
    for k in range(method.decl_line, method.end_line + 1):
        raw = module.lines[k - 1]
        if MARK_INS.match(raw):
            state = "ins"
            continue
        if MARK_DEL.match(raw):
            state = "del"
            continue
        if MARK_INS_END.match(raw) or MARK_DEL_END.match(raw):
            state = None
            continue
        if state == "ins":
            continue
        s = raw.strip(" \t﻿")
        if s:
            out.append((k, s))
    return out


# ----------------------------------------------------------------------------------------------------------------
# Diff helpers over [(line_no, text)] lists
# ----------------------------------------------------------------------------------------------------------------

def _matcher(a, b):
    import difflib
    return difflib.SequenceMatcher(None, [t for _, t in a], [t for _, t in b], autojunk=False)


def diff_stats(a, b):
    """{'hunks', 'removed', 'added', 'ratio'} for two normalized line lists."""
    sm = _matcher(a, b)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"]
    return {"hunks": len(ops), "removed": sum(i2 - i1 for _, i1, i2, _, _ in ops),
            "added": sum(j2 - j1 for _, _, _, j1, j2 in ops), "ratio": round(sm.ratio(), 3)}


def diff_hunks(a, b):
    """[{'tag', 'a': [(line, text)], 'b': [(line, text)]}] - changed blocks only."""
    sm = _matcher(a, b)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            out.append({"tag": tag, "a": a[i1:i2], "b": b[j1:j2]})
    return out


def change_label(st):
    return "=" if not st["hunks"] else "-%d/+%d" % (st["removed"], st["added"])
