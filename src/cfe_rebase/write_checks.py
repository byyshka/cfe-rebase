# -*- coding: utf-8 -*-
"""Objects the extension creates and writes in its code whose vendor write handlers refuse more in new
(WRITE_CHECKS_NEW, a row of calls).

A refusal the vendor adds to a write handler does not change a signature or a name: every check stays silent, and the
extension's write fails at run time. A real case, 11.5.17 -> 11.5.27: ПередЗаписью of Задача.ЗадачаИсполнителя got «Исполнителем
задачи выбран недействительный пользователь» (Отказ); the author's scheduled job created a task for every member of a
group and fell as a whole. Looked for in one method of the extension (live code only):
  <Перем> = <Коллекция>.<Имя>.Создать…(…)   СоздатьЭлемент, СоздатьГруппу, СоздатьДокумент, СоздатьЗадачу,
                                              СоздатьБизнесПроцесс, СоздатьУзел, Создать, СоздатьНаборЗаписей,
                                              СоздатьМенеджерЗаписи (calls.CREATE_OBJECT)
  <Перем>.Записать(…) below it
The vendor module of the object (ObjectModule, or RecordSetModule of a register) is compared old -> new: in ПередЗаписью
and ПриЗаписи, and for a document written with РежимЗаписиДокумента.Проведение also in ОбработкаПроверкиЗаполнения and
ОбработкаПроведения, the refusals are counted - Отказ / Cancel named in the body (the parameter list is not the body)
and ВызватьИсключение / Raise outside the Исключение part of a Попытка (a re-raise there passes on a failed lock or
write - Справочник.Партнеры.ПриЗаписи in 11.5.27 - and checks no data). More in new than in old in any of them is
the finding; a handler new in new counts from zero. Not seen: a refusal in a common module the handler calls, an event
subscription, a variable assigned elsewhere (a parameter, another method). The finding is a risk to check against the
data the code writes, not a proven break.
"""
from . import metadata

CREATE = {"создатьэлемент": "object", "создатьгруппу": "object", "создатьдокумент": "object",
          "создатьзадачу": "object", "создатьбизнеспроцесс": "object", "создатьузел": "object", "создать": "object",
          "createitem": "object", "createfolder": "object", "createdocument": "object", "createtask": "object",
          "createbusinessprocess": "object", "createnode": "object",
          "создатьнаборзаписей": "recordset", "создатьменеджерзаписи": "recordset",
          "createrecordset": "recordset", "createrecordmanager": "recordset"}
WRITE = {"записать", "write"}
POSTING = {"проведение", "posting"}
HANDLERS = ("ПередЗаписью", "ПриЗаписи")
POSTING_HANDLERS = ("ОбработкаПроверкиЗаполнения", "ОбработкаПроведения")
REFUSAL = {"отказ", "cancel"}
RAISE = {"вызватьисключение", "raise"}
TRY, EXCEPT, END_TRY = {"попытка", "try"}, {"исключение", "except"}, {"конецпопытки", "endtry"}


def module_rel(kind, name, part):
    folder = metadata.KINDS[kind][0]
    return "%s/%s/Ext/%s.bsl" % (folder, name, "RecordSetModule" if part == "recordset" else "ObjectModule")


def _args_end(toks, i, b):
    """Index after the «)» closing the call whose «(» is toks[i]."""
    depth = 0
    for j in range(i, b):
        v = toks[j][1]
        if toks[j][0] == "op" and v == "(":
            depth += 1
        elif toks[j][0] == "op" and v == ")":
            depth -= 1
            if depth == 0:
                return j + 1
    return b


def writes(mod, meth):
    """[(kind, name, part, line of the creation, line of the write, posting)] of one method: an object created by
    <Коллекция>.<Имя>.Создать…() into a variable and written by <Перем>.Записать() below, each pair once."""
    toks = mod.toks
    a, b = mod.tok_range(meth)
    made, out, seen = {}, [], set()
    for i in range(a, b):
        k, v, line = toks[i]
        if k != "id":
            continue
        if i + 7 < b and toks[i + 1][1] == "=" and toks[i + 2][0] == "id" and toks[i + 3][1] == "." \
                and toks[i + 4][0] == "id" and toks[i + 5][1] == "." and toks[i + 6][0] == "id" \
                and toks[i + 7][1] == "(" and not (i > a and toks[i - 1][1] == "."):
            kind = metadata.COLL2KIND.get(toks[i + 2][1].casefold())
            part = CREATE.get(toks[i + 6][1].casefold())
            if kind and part and kind in metadata.KINDS:
                made[v.casefold()] = (kind, toks[i + 4][1], part, line)
                continue
        if i + 3 < b and v.casefold() in made and toks[i + 1][1] == "." and toks[i + 2][0] == "id" \
                and toks[i + 2][1].casefold() in WRITE and toks[i + 3][1] == "(" \
                and not (i > a and toks[i - 1][1] == "."):
            kind, name, part, created = made[v.casefold()]
            end = _args_end(toks, i + 3, b)
            posting = kind == "Документ" and any(t[0] == "id" and t[1].casefold() in POSTING
                                                for t in toks[i + 4:end])
            key = (kind, name.casefold(), posting)
            if key not in seen:
                seen.add(key)
                out.append((kind, name, part, created, line, posting))
    return out


def refusals(dump, rel, handler):
    """(Отказ / Cancel named, ВызватьИсключение / Raise) in the body of a handler of a vendor module, None when the
    module or the handler is absent."""
    mod = dump.module(rel) if dump is not None else None
    m = mod.find(handler) if mod is not None else None
    if m is None:
        return None
    a, b = mod.tok_range(m)
    named = raised = 0
    handlers = []  # one flag per open Попытка: inside its Исключение part
    for t in mod.toks[a:b]:
        if t[0] != "id":
            continue
        v = t[1].casefold()
        if v in TRY:
            handlers.append(False)
        elif v in EXCEPT and handlers:
            handlers[-1] = True
        elif v in END_TRY and handlers:
            handlers.pop()
        elif v in REFUSAL:
            named += 1
        elif v in RAISE and not any(handlers):
            raised += 1  # a re-raise in an Исключение part passes on a failure (a lock, a write), it checks no data
    return named, raised


def compare(cfg_old, cfg_new, rel, posting):
    """[{handler, old, new}] of the handlers whose refusals grew old -> new: old / new - [Отказ, ВызватьИсключение],
    old None when the handler is new in new."""
    grew = []
    for h in HANDLERS + (POSTING_HANDLERS if posting else ()):
        n = refusals(cfg_new, rel, h)
        if n is None:
            continue
        o = refusals(cfg_old, rel, h)
        if sum(n) > sum(o or (0, 0)):
            grew.append({"handler": h, "old": list(o) if o is not None else None, "new": list(n)})
    return grew


def note(obj, grew, created, written):
    parts = ["%s (%s)" % (g["handler"], "в old не было" if g["old"] is None else
                          "Отказ %d → %d, ВызватьИсключение %d → %d" % (g["old"][0], g["new"][0], g["old"][1],
                                                                      g["new"][1])) for g in grew]
    return ("[риск] вендор добавил в new отказы при записи %s: %s — расширение создаёт объект (стр. %d) и записывает "
            "его кодом (стр. %d): запись может получить отказ; сверить новые проверки с данными, которые кладёт код"
            % (obj, "; ".join(parts), created, written))


def check(ext, rel, mod, cfg_old, cfg_new, cache):
    """WRITE_CHECKS_NEW rows (calls findings) of one module of an extension; cache - {(rel, posting): grew}."""
    out = []
    if cfg_old is None:
        return out
    for meth in mod.methods:
        for kind, name, part, created, written, posting in writes(mod, meth):
            vrel = module_rel(kind, name, part)
            key = (metadata.rel_key(vrel), posting)
            if key not in cache:
                cache[key] = compare(cfg_old, cfg_new, vrel, posting)
            grew = cache[key]
            if not grew:
                continue
            obj = "%s.%s" % (kind, name)
            out.append({"ext": ext.name, "module": rel, "module_title": metadata.module_title(rel), "line": written,
                        "caller": meth.name, "call": "%s.Записать" % obj, "args": None, "class": "write",
                        "target": metadata.module_title(vrel), "layer": "конфигурация", "sig_new": None,
                        "sig_old": None, "line_new": None, "line_old": None, "flags": ["WRITE_CHECKS_NEW"],
                        "note": note(obj, grew, created, written), "copy": None, "advice": None,
                        "src": mod.lines[written - 1].strip()[:200] if 0 < written <= len(mod.lines) else "",
                        "write_checks": {"object": obj, "line_created": created, "posting": posting,
                                         "handlers": grew}})
    return out
