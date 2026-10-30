# -*- coding: utf-8 -*-
"""Objects of other extensions of the database that the perimeter's code reaches for (FOREIGN_OBJECT).

A reference to a metadata object that is neither in new nor in any extension of the perimeter resolves only while
another extension of the database that adds it is applied: a real case - the УПД copy of an extension read
a constant of another extension of the base; on 11.5.27 that extension was not
applied, and the print fell with «Поле объекта не обнаружено». Looked for (live code only: comments, strings and
#Удаление blocks are not code):
  code    <Коллекция>.<Имя> of a manager collection (Константы.X, Справочники.X, Документы.X, РегистрыСведений.X, …,
          English names too), Метаданные.<Коллекция>.<Имя> included; a chain after another dot is not one
  query   <Вид>.<Имя> of a table or ЗНАЧЕНИЕ(<Вид>.<Имя>.<Значение>) in a live query text, a list of a form, a DCS set
Only an object some extension outside the perimeter adds as its own (not adopted) is reported: an unknown name is the
business of calls / queries. Fields an outside extension adds to a vendor object (Склад.Расш_Реквизит) are not looked for.
"""
from . import metadata, qparse
from .queries import line_of, query_texts

COLLECTIONS = {}
for _kind, (_folder, _ru, _en) in metadata.KINDS.items():
    for _coll in (_ru, _en):
        if _coll:
            COLLECTIONS[_coll.casefold()] = _kind
METADATA = {"метаданные", "metadata"}


def code_refs(ext):
    """(rel, line, method, kind, name) of manager-collection chains in the live code of one extension."""
    for rel in ext.files(".bsl"):
        mod = ext.module(rel)
        if mod is None:
            continue
        toks = mod.toks
        for i in range(len(toks) - 2):
            k, v, line = toks[i]
            if k != "id" or v.casefold() not in COLLECTIONS:
                continue
            if toks[i + 1][1] != "." or toks[i + 2][0] != "id":
                continue
            if i and toks[i - 1][1] == "." and not (i > 1 and toks[i - 2][0] == "id" and
                                                   toks[i - 2][1].casefold() in METADATA):
                continue  # Структура.Документы.X - a field, not the collection
            m = mod.method_at_line(line)
            yield rel, line, m.name if m else None, COLLECTIONS[v.casefold()], toks[i + 2][1]


def query_refs(ext):
    """(rel, line, method, kind, name) of tables and values in the live query texts of one extension."""
    for rel, where, text, strs, _ in query_texts(ext):
        method = where[len("метод "):] if where.startswith("метод ") else None
        q = qparse.clean(text)
        for m in qparse.TABLE_RE.finditer(q):
            kind = qparse.KIND_CF.get(m.group(1).casefold())
            if kind:
                line = line_of(strs, m.start()) if strs else None
                yield rel, line, method, kind, m.group(2)


def analyze(perimeter, cfg_new, outside):
    """[{ext, module, line, method, how, object, owner}] - one per reference: perimeter - dumps of the task's
    extensions, cfg_new - the vendor dump of new, outside - dumps of the other extensions of the database."""
    found, cache = [], {}
    for ext in perimeter:
        for how, refs in (("code", code_refs(ext)), ("query", query_refs(ext))):
            for rel, line, method, kind, name in refs:
                key = (kind, name.casefold())
                if key not in cache:
                    cache[key] = _owner(kind, name, perimeter, cfg_new, outside)
                owner = cache[key]
                if owner:
                    found.append({"ext": ext.name, "module": rel, "line": line, "method": method, "how": how,
                                  "object": "%s.%s" % (kind, owner[1]), "owner": owner[0]})
    return found


def _owner(kind, name, perimeter, cfg_new, outside):
    """(extension, name as it spells it) of the outside extension that adds the object as its own; None when new or
    an extension of the perimeter has it, or no outside extension adds it."""
    if cfg_new.object_exists(kind, name) or any(d.object_exists(kind, name) for d in perimeter):
        return None
    for d in outside:
        if d.object_exists(kind, name):
            o = d.object(kind, name)
            if o is not None and not o.adopted:
                return d.name, o.name
    return None
