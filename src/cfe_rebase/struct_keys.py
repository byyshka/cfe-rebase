# -*- coding: utf-8 -*-
"""STRUCT_KEY_NEW of `calls`: the vendor method reads a key of a structure parameter in new that it did not read in old,
and the extension builds that structure itself without the key.

A real case (11.5.17 -> 11.5.27): the extension's own мр_ЗаполнитьТабличныйДокументУПДСТНВЭД builds ОпцииПечати as
`Новый Структура` + `Вставить("НомерСформированВСчетеФактуре")` and passes it to the vendor ЗаполнитьРеквизитыШапкиУПД;
11.5.27 reads ОпцииПечати.ВерсияПостановления1137 there (the vendor puts the key in its own caller), the signature is
the same, and printing fails with «Поле объекта не обнаружено (ВерсияПостановления1137)».

Vendor side (old and new body of the called method alike): reads <Параметр>.<Ключ> in live code. Not a read: a key
the body checks with <Параметр>.Свойство("<Ключ>") anywhere in it, a key it puts itself (<Параметр>.<Ключ> = …,
<Параметр>.Вставить("<Ключ>", …)), a method call <Параметр>.<Метод>(…). ЗначениеЗаполнено(<Параметр>.<Ключ>) is a
read: the error is raised by the read itself. A parameter the body assigns (<Параметр> = …) says nothing - skipped.
The parameter passed on as a whole argument to a method of the same module or of a common module is followed
DEPTH - 1 levels down. Candidates: keys read in new and not in old.
Extension side, the argument at that position: `Новый Структура(…)` written in the call, or a variable whose nearest
assignment above the call in the same method is `Новый Структура(…)`; its keys are the literal of the constructor and
the literal Вставить(…) between that assignment and the call. A candidate absent from them is the finding: error,
or risk when keys may come from elsewhere (a non-literal key, the structure passed to another method before the call).
A structure that came from a parameter, a function or anything else cannot be judged here: counted only
(stats struct_key_unchecked) - on the corpora those were objects, selections and vendor-built structures. A position
whose parameter the vendor renamed, in a call already reported for its arguments, holds another structure there: skipped
(stats struct_key_reported).
"""
from . import bsl, metadata

DEPTH = 2  # the called method and the methods it passes the parameter on to
STMT_HEAD = {"тогда", "then", "иначе", "else", "цикл", "do", "попытка", "try", "исключение", "except"}
STRUCT = {"структура", "structure"}
INSERT = {"вставить", "insert"}
PROPERTY = {"свойство", "property"}
NO_KEYS = {"заполнитьзначениясвойств", "fillpropertyvalues"}  # takes the structure, adds no key to it


def _op(t, v):
    return t[0] == "op" and t[1] == v


def stmt_start(toks, i, a):
    """Token i starts a statement (an assignment, not a comparison inside a condition)."""
    if i <= a:
        return True
    k, v, _ = toks[i - 1]
    return (k == "op" and v == ";") or k == "pre" or (k == "id" and v.casefold() in STMT_HEAD)


def member(toks, i):
    return i > 0 and _op(toks[i - 1], ".")


def arg_spans(toks, i, end):
    """Token ranges [(s, e)] of the arguments of the call whose name is token i."""
    spans, depth, s = [], 0, i + 2
    for j in range(i + 1, end):
        k, v, _ = toks[j]
        if k == "op" and v in ("(", "["):
            depth += 1
        elif k == "op" and v in (")", "]"):
            depth -= 1
            if depth == 0:
                spans.append((s, j))
                break
        elif depth == 1 and k == "op" and v == ",":
            spans.append((s, j))
            s = j + 1
    return [] if spans == [(i + 2, i + 2)] else spans


def usage(toks, a, b, param):
    """How a method body [a, b) uses its parameter: {reads: {key: (Key, line)}, put: {key}, guarded: {key},
    reassigned: bool}."""
    res = {"reads": {}, "put": set(), "guarded": set(), "reassigned": False}
    for i in range(a, b - 1):
        k, v, line = toks[i]
        if k != "id" or v.casefold() != param or member(toks, i):
            continue
        nxt = toks[i + 1]
        if _op(nxt, "=") and stmt_start(toks, i, a):
            res["reassigned"] = True
        elif _op(nxt, ".") and i + 2 < b and toks[i + 2][0] == "id":
            key = toks[i + 2][1]
            after = toks[i + 3] if i + 3 < b else ("op", ";", line)
            if _op(after, "("):
                lit = toks[i + 4] if i + 4 < b else None
                if lit is not None and lit[0] == "str":
                    if key.casefold() in INSERT:
                        res["put"].add(lit[1].strip().casefold())
                    elif key.casefold() in PROPERTY:
                        res["guarded"].add(lit[1].strip().casefold())
            elif _op(after, "=") and stmt_start(toks, i, a):
                res["put"].add(key.casefold())
            else:
                res["reads"].setdefault(key.casefold(), (key, toks[i + 2][2]))
    return res


class VendorKeys(object):
    """Keys a vendor method requires of a parameter, cached per dump."""

    def __init__(self):
        self._cache = {}
        self.unchecked = []  # (module path, line, method, argument, keys, reason) of the structures not judged

    def required(self, dump, rel, name, pos, depth=DEPTH):
        """{key: (Key, rel, line, method)} the method reads from its parameter #pos (0-based) without a guard, the methods it
        passes the parameter to included; None - the parameter is reassigned in the body (nothing to say)."""
        ck = (dump.root, metadata.rel_key(rel), name.casefold(), pos, depth)
        if ck in self._cache:
            return self._cache[ck]
        self._cache[ck] = {}  # a recursive call of itself adds nothing
        mod = dump.module(rel)
        m = mod.find(name) if mod is not None else None
        if m is None or pos >= len(m.params):
            return {}
        param = m.params[pos]["name"].casefold()
        toks = mod.toks
        a, b = mod.tok_range(m)
        u = usage(toks, a, b, param)
        if u["reassigned"]:
            self._cache[ck] = None
            return None
        skip = u["put"] | u["guarded"]
        res = {k: (key, rel, line, m.name) for k, (key, line) in u["reads"].items() if k not in skip}
        if depth > 1:
            for c in bsl.extract_calls(toks, a, b):
                for j, (s, e) in enumerate(arg_spans(toks, c["tok"], b)):
                    if e - s != 1 or toks[s][0] != "id" or toks[s][1].casefold() != param:
                        continue
                    callee = self._callee(dump, rel, mod, c)
                    sub = self.required(dump, callee, c["name"], j, depth - 1) if callee else None
                    for k, v in (sub or {}).items():
                        if k not in skip:
                            res.setdefault(k, v)
        self._cache[ck] = res
        return res

    @staticmethod
    def _callee(dump, rel, mod, c):
        if not c["chain"]:
            return rel if mod.find(c["name"]) is not None else None
        if len(c["chain"]) == 1:
            real = dump.common_modules().get(c["chain"][0].casefold())
            return metadata.common_module_rel(real) if real else None
        return None


def ctor_keys(toks, i, end):
    """Keys of `Новый Структура…` at token i (Новый): a set, or None when they are not a literal."""
    if i + 4 >= len(toks) or i + 2 >= end or not _op(toks[i + 2], "("):
        return set()
    if _op(toks[i + 3], ")"):
        return set()
    lit = toks[i + 3]
    if lit[0] != "str" or not (_op(toks[i + 4], ",") or _op(toks[i + 4], ")")):
        return None
    return {x.strip().casefold() for x in lit[1].split(",") if x.strip()}


def is_ctor(toks, i, end):
    return i + 1 < end and toks[i][0] == "id" and toks[i][1].casefold() in bsl.KW_NEW and \
        toks[i + 1][0] == "id" and toks[i + 1][1].casefold() in STRUCT


def origin(toks, a, call_tok, span, params):
    """What the extension passes at one argument: {how: inline | local, var, line (of Новый Структура), keys (set or
    None - not all literal), passed_on: [lines]} for a structure built in the calling method, else the reason it is not
    judged: "param" | "value" (a function result, another variable, an expression)."""
    s, e = span
    if is_ctor(toks, s, e):
        return {"how": "inline", "var": None, "line": toks[s][2], "keys": ctor_keys(toks, s, e), "passed_on": []}
    if e - s != 1 or toks[s][0] != "id":
        return "value"
    var = toks[s][1].casefold()
    assign = None
    for i in range(a, call_tok):
        if toks[i][0] == "id" and toks[i][1].casefold() == var and not member(toks, i) and \
                _op(toks[i + 1], "=") and stmt_start(toks, i, a):
            assign = i
    if assign is None:
        return "param" if var in params else "value"
    if not is_ctor(toks, assign + 2, call_tok):
        return "value"
    keys = ctor_keys(toks, assign + 2, call_tok)
    passed = []
    for i in range(assign + 3, min(call_tok, len(toks) - 4)):
        if toks[i][0] != "id" or toks[i][1].casefold() != var or member(toks, i):
            continue
        if _op(toks[i + 1], ".") and toks[i + 2][0] == "id" and toks[i + 2][1].casefold() in INSERT and \
                _op(toks[i + 3], "("):
            lit = toks[i + 4]
            if lit[0] == "str" and keys is not None:
                keys.add(lit[1].strip().casefold())
            elif lit[0] != "str":
                keys = None
        elif (_op(toks[i - 1], "(") or _op(toks[i - 1], ",")) and (_op(toks[i + 1], ")") or _op(toks[i + 1], ",")):
            fn = enclosing_call(toks, i)
            if fn[0] not in NO_KEYS:
                passed.append((toks[i][2],) + fn)
    return {"how": "local", "var": toks[s][1], "line": toks[assign][2], "keys": keys, "passed_on": passed}


def enclosing_call(toks, i):
    """(name, qualifier, argument index) of the call whose argument list holds token i, casefold; ('', '', -1)."""
    depth = pos = 0
    for j in range(i - 1, -1, -1):
        if _op(toks[j], ")") or _op(toks[j], "]"):
            depth += 1
        elif _op(toks[j], "(") or _op(toks[j], "["):
            if depth == 0:
                if not (j and toks[j - 1][0] == "id"):
                    break
                q, p = [], j - 2
                while p >= 1 and _op(toks[p], ".") and toks[p - 1][0] == "id":
                    q.insert(0, toks[p - 1][1].casefold())
                    p -= 2
                return toks[j - 1][1].casefold(), ".".join(q), pos
            depth -= 1
        elif depth == 0 and _op(toks[j], ","):
            pos += 1
        elif _op(toks[j], ";"):
            break
    return "", "", -1


def vendor_puts(dump, rel, key, limit=3):
    """Where the vendor module puts the key into a structure: ["Метод:строка"] (Вставить("Key") or a constructor
    literal)."""
    mod = dump.module(rel)
    if mod is None:
        return []
    toks, k, out = mod.toks, key.casefold(), []
    for i in range(2, len(toks)):
        t = toks[i]
        if t[0] != "str":
            continue
        hit = (_op(toks[i - 1], "(") and toks[i - 2][0] == "id" and toks[i - 2][1].casefold() in INSERT and
               t[1].strip().casefold() == k) or \
              (_op(toks[i - 1], "(") and i >= 3 and is_ctor(toks, i - 3, i) and
               k in {x.strip().casefold() for x in t[1].split(",")})
        if hit:
            m = mod.method_at_line(t[2])
            place = "%s:%d" % (m.name if m else "модуль", t[2])
            if place not in out:
                out.append(place)
            if len(out) >= limit:
                break
    return out


def check(vk, mod, caller, call, cfg_new, rel_new, t_new, cfg_old, rel_old, t_old, stats, reported=False):
    """STRUCT_KEY_NEW rows (struct_key dict with note) of one resolved call of a vendor method from extension code.
    reported - the call already has an argument finding (TOO_FEW_ARGS / TOO_MANY_ARGS / SIGNATURE_CHANGED risk): a
    position whose parameter the vendor renamed is another structure there (a real case: ГлобальныеПеременные ->
    ТекущиеПеременные of ДобавитьПриход), skipped - counted in stats struct_key_reported."""
    rows = []
    toks = mod.toks
    a = None
    params = {p["name"].casefold() for p in caller.params}
    for pos, span in enumerate(arg_spans(toks, call["tok"], len(toks))):
        if pos >= len(t_new.params) or span[0] == span[1]:
            continue
        new = vk.required(cfg_new, rel_new, t_new.name, pos)
        if not new:
            continue
        old = vk.required(cfg_old, rel_old, t_old.name, pos)
        if old is None:
            continue
        cand = {k: v for k, v in new.items() if k not in old}
        if not cand:
            continue
        if reported and (pos >= len(t_old.params) or
                         t_old.params[pos]["name"].casefold() != t_new.params[pos]["name"].casefold()):
            stats["struct_key_reported"] += 1
            continue
        if a is None:
            a = mod.tok_range(caller)[0]
        src = origin(toks, a, call["tok"], span, params)
        if not isinstance(src, dict):
            stats["struct_key_unchecked"] += 1
            vk.unchecked.append((mod.path, call["line"], t_new.name, pos + 1, sorted(cand), src))
            continue
        missing = [k for k in sorted(cand) if src["keys"] is None or k not in src["keys"]]
        if not missing:
            continue
        # the same vendor method called with the structure in another branch puts none of these keys (they are
        # candidates because its body does not put them)
        same = (t_new.name.casefold(), ".".join(call["chain"]).casefold(), pos)
        src["passed_on"] = [p[0] for p in src["passed_on"] if p[1:] != same]
        sure = src["keys"] is not None and not src["passed_on"]
        grade = "error" if sure else "risk"
        shown = [cand[k][0] for k in missing]
        reads = [{"key": cand[k][0], "module": metadata.module_title(cand[k][1]), "method": cand[k][3],
                  "line": cand[k][2]} for k in missing]
        puts = {cand[k][0]: vendor_puts(cfg_new, rel_new, cand[k][0]) for k in missing}
        what = "аргумент %d (%s) — %s, построенная в методе (стр. %d)" % (
            pos + 1, t_new.params[pos]["name"], src["var"] or "Новый Структура", src["line"])
        if src["keys"] is None:
            why = "; часть ключей задана не литералом — проверить глазами"
        elif src["passed_on"]:
            why = "; до вызова структура передаётся в другой метод (стр. %s) — ключ могли добавить там" % ", ".join(
                str(x) for x in src["passed_on"])
        else:
            why = ""
        note = "[%s] new читает %s %s (в old не читал), в структуре расширения %s нет: %s; чтение — %s%s" % (
            {"error": "ошибка", "risk": "риск"}[grade], "ключи" if len(shown) > 1 else "ключ", ", ".join(shown),
            "их" if len(shown) > 1 else "его", what,
            ", ".join("%s %s:%d" % (r["key"], r["method"], r["line"]) for r in reads), why)
        hint = ["%s: %s" % (k, ", ".join(v)) for k, v in puts.items() if v]
        if hint:
            note += "; вендор кладёт ключ сам — " + "; ".join(hint)
        stats["struct_key_" + grade] += 1
        rows.append({"note": note, "advice": advice(puts, caller), "grade": grade, "arg": pos + 1,
                     "param": t_new.params[pos]["name"], "var": src["var"], "built_line": src["line"], "keys": shown,
                     "reads": reads, "vendor_puts": puts, "passed_on": src["passed_on"],
                     "literal": src["keys"] is not None})
    return rows


def advice(puts, caller):
    """What to do: a copy of an old vendor body whose new body puts the key itself is re-ported, else the key is put."""
    for kind, target, _ in caller.interceptors:
        if kind in ("Вместо", "ИзменениеИКонтроль") and target:
            if any(p.split(":")[0].casefold() == target.casefold() for v in puts.values() for p in v):
                return "вызов в копии &%s(\"%s\") старого тела вендора, в new вендор кладёт ключ в самом %s: переснять " \
                       "копию по new (%s)" % (kind, target, target, "port_replace / merge-copy" if kind == "Вместо"
                                              else "rebase_control / control-rebase")
            break
    return "положить ключ в структуру перед вызовом (значение — как у вендора в new)"
