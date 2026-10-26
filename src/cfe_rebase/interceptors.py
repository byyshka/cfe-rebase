# -*- coding: utf-8 -*-
"""Registry of every interception in extension dumps, checked against the new configuration release,
plus dead code of the extensions.

Interceptions: annotations &Перед/&После/&Вместо/&ИзменениеИКонтроль in modules and callType handlers in
Form.xml. For each: target, directive of the interceptor and of the base method in the new release,
ПродолжитьВызов, signatures old/new, change of the base body old->new.

Flags (registry):
  CONFLICT            one target in >=2 extensions and one of them is &Вместо without ПродолжитьВызов
  OVERRIDDEN          will not run: an upper extension replaces the same method by &Вместо without ПродолжитьВызов
                      (OVERRIDDEN? - the winner depends on the connection order, pass --order)
  DIRECTIVE_MISMATCH  compile directive of the interceptor differs from the base method (form modules)
  MISSING_BASE        base module/method/form element is absent in the new release
  NEW_BASE            the base method did not exist in the old release: the interceptor now acts on new vendor logic
  SIGNATURE_CHANGED   base method signature differs between old and new
  SIGNATURE_MISMATCH  interceptor parameters (count, Знач, kind) do not match the new base method
  CONTROL_MISMATCH    &ИзменениеИКонтроль: reconstructed original text differs from the new base body
  BINDING_CHANGED     the vendor re-bound the form event (callType target or handler of an intercepted method)
  DEAD_BINDING        Form.xml binds a handler that the extension form module does not have
Dead code (separate section):
  UNBOUND             handler-like method (…Перед/…После/…Вместо, event names) with no annotation, no Form.xml or
                      XML binding and no call or string reference anywhere in the extensions
  COMMENTED_ANNOTATION  commented interceptor annotation right above a live method
  DEAD_BINDING        the same check for own (not borrowed) forms
"""
import argparse
import os
import re
import sys
import time
from collections import defaultdict, OrderedDict

from . import bsl, fileio, forms, metadata, refs

KILL = ("Вместо",)
EXPLAIN = OrderedDict([
    ("CONFLICT", "одна цель в ≥2 расширениях, среди них `&Вместо` без `ПродолжитьВызов`"),
    ("OVERRIDDEN", "не выполнится: выше по порядку тот же метод заменён `&Вместо` без `ПродолжитьВызов`"),
    ("OVERRIDDEN?", "то же, но кто выше — зависит от порядка подключения (передать `--order`)"),
    ("DIRECTIVE_MISMATCH", "директива компиляции перехватчика ≠ директиве базового метода"),
    ("MISSING_BASE", "в новой конфигурации нет базового модуля, метода или элемента формы"),
    ("NEW_BASE", "базового метода не было в old, он появился в new: перехват теперь меняет новую типовую логику"),
    ("SIGNATURE_CHANGED", "сигнатура базового метода old ≠ new"),
    ("SIGNATURE_MISMATCH", "параметры перехватчика не совпадают с базовым методом new (число, Знач, вид)"),
    ("CONTROL_MISMATCH", "`&ИзменениеИКонтроль`: восстановленный исходный текст ≠ тело base new"),
    ("BINDING_CHANGED", "вендор перепривязал событие формы"),
    ("DEAD_BINDING", "в Form.xml назначен обработчик, которого нет в модуле формы расширения"),
])
SEVERITY = {"CONFLICT": 1, "OVERRIDDEN": 1, "DEAD_BINDING": 2, "MISSING_BASE": 2, "NEW_BASE": 2, "SIGNATURE_MISMATCH": 1,
            "CONTROL_MISMATCH": 1, "DIRECTIVE_MISMATCH": 2, "OVERRIDDEN?": 2, "SIGNATURE_CHANGED": 3,
            "BINDING_CHANGED": 3}

HANDLER_SUFFIX_RE = re.compile(r"(Перед|После|Вместо)$", re.IGNORECASE)
ITEM_EVENTS = [x for x in """
ПриИзменении НачалоВыбора ОбработкаВыбора ПриАктивизацииСтроки ПриАктивизацииПоля ПриАктивизацииЯчейки
ПередНачаломДобавления ПередНачаломИзменения ПередУдалением ПриНачалеРедактирования ПриОкончанииРедактирования
ПередОкончаниемРедактирования ОкончаниеВводаТекста АвтоПодбор Очистка ОбработкаРасшифровки ОбработкаНавигационнойСсылки
ПриПолученииДанных ПередРазворачиванием ПередСворачиванием ОбработкаПеретаскивания ПроверкаПеретаскивания
""".split()]
COMMENTED_ANN_RE = re.compile(r"^\s*//\s*&\s*(Вместо|Перед|После|ИзменениеИКонтроль|Around|Before|After|"
                              r"ChangeAndValidate)\s*\(", re.IGNORECASE)
COMMENTED_DECL_RE = re.compile(r"^\s*//\s*(Асинх\s+)?(Процедура|Функция|КонецПроцедуры|КонецФункции|Procedure|"
                               r"Function|EndProcedure|EndFunction)(?![\wЁё])", re.IGNORECASE)


def effective_directive(method, mkind):
    if method is None:
        return None
    if method.directives:
        return "+".join(method.directives)
    return "НаСервере" if mkind == "form" else ""


def sig_problems(ext_m, base_m):
    """Differences the platform checks when it binds an interceptor (kind, Асинх, count, Знач)."""
    crit = []
    if ext_m.kind != base_m.kind:
        crit.append("вид %s ≠ %s" % (ext_m.kind, base_m.kind))
    if ext_m.is_async != base_m.is_async:
        crit.append("Асинх %s ≠ %s" % (ext_m.is_async, base_m.is_async))
    if len(ext_m.params) != len(base_m.params):
        crit.append("параметров %d ≠ %d" % (len(ext_m.params), len(base_m.params)))
    else:
        for k, (a, b) in enumerate(zip(ext_m.params, base_m.params), 1):
            if a["byval"] != b["byval"]:
                crit.append("параметр %d: Знач %s ≠ %s" % (k, a["byval"], b["byval"]))
    return crit


class Context(object):
    def __init__(self, args):
        metadata.check_config(args.config_new, "--config-new")
        if args.config_old:
            metadata.check_config(args.config_old, "--config-old")
        self.cfg_new = metadata.Dump(args.config_new, "new")
        self.cfg_old = metadata.Dump(args.config_old, "old") if args.config_old else None
        self.exts = metadata.find_extensions(args.ext_root, args.ext, args.skip)
        self.order = metadata.load_order(args.order) if args.order else None
        self.form = refs.form_cache()


def collect(ctx):
    rows = []
    for ext in ctx.exts:
        for rel in ext.files(".bsl"):
            mod = ext.module(rel)
            if mod is None:
                continue
            for m in mod.methods:
                for kind, target, ann_line in m.interceptors:
                    rows.append({"ext": ext.name, "ext_purpose": ext.purpose, "module": rel,
                                 "module_title": metadata.module_title(rel), "mkind": metadata.module_kind(rel),
                                 "source": "annotation", "kind": kind, "target": target, "ext_method": m.name,
                                 "ext_line": m.decl_line, "ann_line": ann_line, "proceed": m.has_proceed,
                                 "_ext": ext, "_m": m})
        for frel in ext.files("/Form.xml"):
            f = ctx.form(ext, frel)
            if f is None or not f.borrowed:
                continue
            mrel = metadata.form_module_rel(frel)
            mod = ext.module(mrel)
            base_names = f.names(f.base)
            for b in f.bindings():
                if not b["callType"]:
                    continue
                m = mod.find(b["handler"]) if mod is not None else None
                own = b["owner_kind"] != "form" and b["owner"] not in base_names
                rows.append({"ext": ext.name, "ext_purpose": ext.purpose, "module": mrel, "form": frel,
                             "module_title": metadata.module_title(mrel), "mkind": "form", "source": "callType",
                             "kind": forms.CALLTYPE_RU.get(b["callType"], b["callType"]), "callType": b["callType"],
                             "owner_kind": b["owner_kind"], "owner": b["owner"], "event": b["event"],
                             "xml_line": b["line"], "target": None, "ext_method": b["handler"],
                             "ext_line": m.decl_line if m else None, "proceed": m.has_proceed if m else False,
                             "own_element": own, "_ext": ext, "_m": m})
    return rows


def check_row(ctx, r):
    flags = []
    notes = []
    m = r["_m"]
    mk = r["mkind"]
    new_mod = ctx.cfg_new.module(r["module"])
    old_mod = ctx.cfg_old.module(r["module"]) if ctx.cfg_old else None
    t_new = t_old = None
    if r["source"] == "annotation":
        t_new = new_mod.find(r["target"]) if new_mod else None
        t_old = old_mod.find(r["target"]) if old_mod else None
        r["target_old"] = r["target"]
        if new_mod is None:
            flags.append("MISSING_BASE")
            notes.append("базового модуля нет в new" + (" (в old был)" if old_mod else ""))
        elif t_new is None:
            is_event = (r["target"] or "").lower() in bsl.EVENTS
            if is_event:
                notes.append("событие платформы без типового обработчика — перехват допустим")
            else:
                flags.append("MISSING_BASE")
                notes.append("метода нет в new" + (" (в old был — удалён обновлением)" if t_old else " (не было и в old)"))
        elif ctx.cfg_old is not None and t_old is None:
            flags.append("NEW_BASE")
            notes.append("в old метода не было: раньше перехват срабатывал без типового обработчика, "
                         "теперь заменяет или дополняет новый типовой")
    else:
        if r["own_element"]:
            notes.append("собственный элемент расширения — не перехват")
        f_new = ctx.form(ctx.cfg_new, r["form"])
        f_old = ctx.form(ctx.cfg_old, r["form"]) if ctx.cfg_old else None
        own_new, h_new = forms.handler_for(f_new.root if f_new else None, r["owner_kind"], r["owner"], r["event"])
        own_old, h_old = forms.handler_for(f_old.root if f_old else None, r["owner_kind"], r["owner"], r["event"])
        r["target"], r["target_old"] = h_new, h_old
        if m is None:
            flags.append("DEAD_BINDING")
            notes.append("модуля формы нет" if ctx_module_missing(r) else "процедуры %s нет в модуле формы" % r["ext_method"])
        if not r["own_element"]:
            if f_new is None:
                flags.append("MISSING_BASE")
                notes.append("формы нет в new")
            elif not own_new:
                flags.append("MISSING_BASE")
                notes.append("элемента/команды %s нет в форме new" % r["owner"] + (" (в old был)" if own_old else ""))
            if ctx.cfg_old and f_old is not None and f_new is not None and h_old != h_new:
                flags.append("BINDING_CHANGED")
                notes.append("обработчик события в конфигурации: old %s → new %s" % (h_old or "—", h_new or "—"))
            t_new = new_mod.find(h_new) if (new_mod and h_new) else None
            t_old = old_mod.find(h_old) if (old_mod and h_old) else None
            if h_new is None and own_new:
                notes.append("у события нет типового обработчика")
    r["ext_directive"] = effective_directive(m, mk)
    r["base_directive"] = effective_directive(t_new, mk)
    r["base_line_new"] = t_new.decl_line if t_new else None
    r["base_line_old"] = t_old.decl_line if t_old else None
    r["base_sig_new"] = t_new.signature() if t_new else None
    r["base_sig_old"] = t_old.signature() if t_old else None
    r["ext_sig"] = m.signature() if m else None
    if m is not None and t_new is not None:
        if mk == "form" and r["ext_directive"] != r["base_directive"]:
            flags.append("DIRECTIVE_MISMATCH")
            notes.append("перехватчик &%s, база &%s" % (r["ext_directive"] or "—", r["base_directive"] or "—"))
        elif mk != "form" and m.directives and t_new.directives and m.directives != t_new.directives:
            flags.append("DIRECTIVE_MISMATCH")
            notes.append("перехватчик &%s, база &%s" % (r["ext_directive"], r["base_directive"]))
        crit = sig_problems(m, t_new) if r["source"] == "annotation" else (
            ["параметров %d ≠ %d" % (len(m.params), len(t_new.params))] if len(m.params) != len(t_new.params) else [])
        if crit:
            flags.append("SIGNATURE_MISMATCH")
            notes.append("; ".join(crit))
    if t_new is not None and t_old is not None and t_new.sig_key() != t_old.sig_key():
        flags.append("SIGNATURE_CHANGED")
        notes.append("old: %s → new: %s" % (t_old.signature(), t_new.signature()))
    r["body_change"] = None
    if t_new is not None and t_old is not None:
        r["body_change"] = bsl.change_label(bsl.diff_stats(bsl.body_code(old_mod, t_old)[1:],
                                                           bsl.body_code(new_mod, t_new)[1:]))
    if r["kind"] == "ИзменениеИКонтроль" and m is not None and t_new is not None:
        ext_mod = r["_ext"].module(r["module"])
        ext_ctrl = bsl.body_control(ext_mod, m)[1:]
        base_ctrl = bsl.body_control(new_mod, t_new)[1:]
        st = bsl.diff_stats(base_ctrl, ext_ctrl)
        r["control"] = bsl.change_label(st)
        if st["hunks"]:
            flags.append("CONTROL_MISMATCH")
            notes.append("исходный текст ИиК против base new: %s" % r["control"])
    if r["source"] == "annotation" and mk == "form" and ctx.cfg_old and t_new is not None:
        frel = metadata.form_xml_rel(r["module"])
        f_new = ctx.form(ctx.cfg_new, frel)
        f_old = ctx.form(ctx.cfg_old, frel)
        if f_new is not None and f_old is not None:
            b_new = forms.bound_to(f_new.root, r["target"])
            b_old = forms.bound_to(f_old.root, r["target"])
            if b_new != b_old:
                flags.append("BINDING_CHANGED")
                notes.append("события, вызывающие %s: old %s → new %s" % (r["target"], b_old or "—", b_new or "—"))
    r["flags"] = flags
    r["notes"] = notes


def ctx_module_missing(r):
    return not r["_ext"].exists(r["module"])


def target_key(r):
    if r["source"] == "annotation":
        return (metadata.rel_key(r["module"]), (r["target"] or "").casefold())
    if r["target"]:
        return (metadata.rel_key(r["module"]), r["target"].casefold())
    return (metadata.rel_key(r["module"]), "event:%s.%s" % (r["owner"], r["event"]))


def above(ctx, ext_a, ext_b, purposes):
    """True/False: extension a is connected after (above) b; None - unknown. Second item - how decided."""
    if ctx.order:
        idx = {n.casefold(): i for i, n in enumerate(ctx.order)}
        ia, ib = idx.get(ext_a.casefold()), idx.get(ext_b.casefold())
        if ia is not None and ib is not None:
            return ia > ib, "по --order"
    ra = metadata.PURPOSE_RANK.get((purposes.get(ext_a) or "").lower())
    rb = metadata.PURPOSE_RANK.get((purposes.get(ext_b) or "").lower())
    if ra is not None and rb is not None and ra != rb:
        return ra > rb, "по назначению"
    return None, "зависит от порядка"


def check_groups(ctx, rows):
    purposes = {e.name: e.purpose for e in ctx.exts}
    groups = defaultdict(list)
    for r in rows:
        if r["source"] == "callType" and r.get("own_element"):
            continue
        groups[target_key(r)].append(r)
    out = []
    for key, items in groups.items():
        exts = {r["ext"] for r in items}
        if len(exts) < 2:
            continue
        ann = [r for r in items if r["source"] == "annotation"]
        killers = [r for r in ann if r["kind"] in KILL and not r["proceed"]]
        verdict = []
        if killers:
            for r in items:
                if "CONFLICT" not in r["flags"]:
                    r["flags"].append("CONFLICT")
            for victim in ann:
                for k in killers:
                    if k is victim or k["ext"] == victim["ext"]:
                        continue
                    up, how = above(ctx, k["ext"], victim["ext"], purposes)
                    if up is True:
                        victim["flags"].append("OVERRIDDEN")
                        victim["notes"].append("выше подключено %s с `&Вместо` без ПродолжитьВызов (%s)" % (k["ext"], how))
                        break
                    if up is None:
                        victim["flags"].append("OVERRIDDEN?")
                        victim["notes"].append("`&Вместо` без ПродолжитьВызов в %s; кто выше — %s" % (k["ext"], how))
                        break
            live = [r for r in ann if "OVERRIDDEN" not in r["flags"] and "OVERRIDDEN?" not in r["flags"]]
            top = [r for r in live if r in killers]
            if top:
                verdict.append("выполняется `&Вместо` из %s; базовое тело и перехваты ниже не выполняются" % top[0]["ext"])
                top[0]["notes"].append("верхний `&Вместо` без ПродолжитьВызов: выполняется он, базовое тело и "
                                       "перехваты ниже — нет")
            else:
                verdict.append("кто выполняется — зависит от порядка подключения")
        shown = next((r["target"] for r in items if r["target"]), key[1])
        out.append({"module": items[0]["module_title"], "target": shown, "items": [
            {"ext": r["ext"], "purpose": metadata.PURPOSE_RU.get((r["ext_purpose"] or "").lower(), r["ext_purpose"]),
             "what": ("&" + r["kind"]) if r["source"] == "annotation" else "callType=%s %s.%s" % (
                 r["callType"], r["owner"], r["event"]),
             "proceed": r["proceed"], "method": r["ext_method"]} for r in items],
            "conflict": bool(killers), "verdict": verdict})
    return out


# ------------------------------------------------------------------------------------------------------------------
# Dead code
# ------------------------------------------------------------------------------------------------------------------

def handler_like(name, mkind):
    """Name looks like an interceptor or an event handler."""
    low = name.casefold()
    if HANDLER_SUFFIX_RE.search(name):
        return True
    bare = low.split("_", 1)[1] if "_" in low else low
    for ev in bsl.EVENTS | refs.EXTRA_EVENTS:
        if bare.startswith(ev) or low.startswith(ev):
            return True
    for ev in ITEM_EVENTS:
        if low.endswith(ev.lower()):
            return True
    return False


def dead_code(ctx, rows):
    index = refs.build(ctx.exts, ctx.form)
    implicit_events = bsl.EVENTS | refs.EXTRA_EVENTS
    unbound = []
    commented = []
    for ext in ctx.exts:
        for rel in ext.files(".bsl"):
            mod = ext.module(rel)
            if mod is None:
                continue
            mk = metadata.module_kind(rel)
            for m in mod.methods:
                # commented interceptor annotation above a live method: walk up through live annotations,
                # comments and blank lines; a commented declaration or end of method stops the walk
                k = m.decl_line - 1
                while k >= 1:
                    raw = mod.lines[k - 1]
                    kind = mod.lx[k - 1].kind
                    if kind == "comment":
                        if COMMENTED_DECL_RE.match(raw):
                            break
                        if COMMENTED_ANN_RE.match(raw):
                            commented.append({"ext": ext.name, "module": rel, "module_title": metadata.module_title(rel),
                                              "line": k, "annotation": raw.strip(), "method": m.name,
                                              "method_line": m.decl_line, "live_interceptors": [
                                                  "&%s(\"%s\")" % (a, t) for a, t, _ in m.interceptors]})
                        k -= 1
                        continue
                    if kind == "empty" or (kind == "code" and raw.strip().startswith("&")):
                        k -= 1
                        continue
                    break
                if m.interceptors or not handler_like(m.name, mk):
                    continue
                if mk in refs.IMPLICIT_KINDS and m.name.casefold() in implicit_events:
                    continue
                if index.referenced(rel, m):
                    continue
                unbound.append({"ext": ext.name, "module": rel, "module_title": metadata.module_title(rel),
                                "method": m.name, "line": m.decl_line, "export": m.export,
                                "directive": "+".join(m.directives)})
    dead_own = []
    for ext in ctx.exts:
        for frel in ext.files("/Form.xml"):
            f = ctx.form(ext, frel)
            if f is None or f.borrowed:
                continue
            mrel = metadata.form_module_rel(frel)
            mod = ext.module(mrel)
            for b in f.bindings():
                if mod is None or mod.find(b["handler"]) is None:
                    dead_own.append({"ext": ext.name, "form": frel, "form_title": metadata.form_title(frel),
                                     "xml_line": b["line"], "owner": b["owner"], "event": b["event"],
                                     "handler": b["handler"], "module_exists": mod is not None})
    return {"UNBOUND": unbound, "COMMENTED_ANNOTATION": commented, "DEAD_BINDING_OWN_FORMS": dead_own}


# ------------------------------------------------------------------------------------------------------------------
# Report
# ------------------------------------------------------------------------------------------------------------------

def where(r):
    if r["source"] == "annotation":
        return "%s:%s" % (r["module"], r["ann_line"])
    return "%s:%s" % (r["form"], r["xml_line"])


def what(r):
    if r["source"] == "annotation":
        return "&%s(\"%s\")%s" % (r["kind"], r["target"], " +ПВ" if r["proceed"] else "")
    return "callType=%s %s.%s → %s" % (r["callType"], r["owner"], r["event"], r["target"] or "—")


def render(ctx, rows, groups, dead, elapsed):
    md = ["# Реестр перехватов расширений", ""]
    md.append("Конфигурация new: `%s` (%s), old: `%s` (%s). Расширений: %d. Время: %.1f с." % (
        ctx.cfg_new.root, ctx.cfg_new.version, ctx.cfg_old.root if ctx.cfg_old else "—",
        ctx.cfg_old.version if ctx.cfg_old else "—", len(ctx.exts), elapsed))
    md.append("Порядок подключения: %s." % ("из `--order` (%d имён)" % len(ctx.order) if ctx.order
                                              else "не передан — победитель конфликта определяется только по назначению"))
    md.append("")
    counts = defaultdict(int)
    for r in rows:
        for f in set(r["flags"]):
            counts[f] += 1
    kinds = defaultdict(int)
    for r in rows:
        kinds[("&" + r["kind"] + (" +ПВ" if r["proceed"] else "")) if r["source"] == "annotation"
              else "callType " + r["callType"]] += 1
    md.append("Перехватов: %d (%s)." % (len(rows), ", ".join("%s — %d" % kv for kv in sorted(kinds.items()))))
    md.append("")
    md.append(fileio.md_table(["Флаг", "Строк", "Смысл"],
                              [(f, counts.get(f, 0), EXPLAIN[f]) for f in EXPLAIN]))
    md.append("")
    if ctx.form.unreadable:
        md.append("## Не прочитаны Form.xml — %d" % len(ctx.form.unreadable))
        md.append("")
        md.append("Файл есть, но не разбирается как XML: перехваты и привязки событий этих форм в реестр не попали, "
                  "флаги по ним не считались.")
        md.append("")
        md.append(fileio.md_table(["Выгрузка", "Form.xml", "Ошибка"],
                                  [(u["dump"], u["form"], u["error"]) for u in ctx.form.unreadable]))
        md.append("")
    md.append("## Строки с флагами")
    md.append("")
    flagged = [r for r in rows if r["flags"]]
    flagged.sort(key=lambda r: (min(SEVERITY.get(f, 9) for f in r["flags"]), r["ext"], r["module"]))
    md.append(fileio.md_table(
        ["№", "Флаги", "Расширение", "Модуль", "Перехват", "Метод расширения", "Директива расш./база",
         "Где", "Пояснение"],
        [(r["id"], ", ".join(r["flags"]), r["ext"], r["module_title"], what(r), r["ext_method"],
          "%s / %s" % (r.get("ext_directive") or "—", r.get("base_directive") or "—"), where(r),
          "; ".join(r["notes"])) for r in flagged]))
    md.append("")
    md.append("## Цели в нескольких расширениях")
    md.append("")
    if not groups:
        md.append("Нет.")
    for g in groups:
        md.append("- **%s → %s**%s" % (g["module"], g["target"], " — CONFLICT" if g["conflict"] else ""))
        for it in g["items"]:
            md.append("  - %s (%s): %s%s, метод `%s`" % (it["ext"], it["purpose"], it["what"],
                                                         " +ПВ" if it["proceed"] else "", it["method"]))
        for v in g["verdict"]:
            md.append("  - Фактически: %s" % v)
    md.append("")
    md.append("## Мёртвый код")
    md.append("")
    md.append("### UNBOUND — %d" % len(dead["UNBOUND"]))
    md.append("")
    md.append("Метод с именем перехватчика или обработчика события без аннотации, без привязки в XML и без "
              "вызовов или строковых ссылок во всех расширениях набора.")
    md.append("")
    md.append(fileio.md_table(["Расширение", "Модуль", "Метод", "Строка", "Экспорт"],
                              [(d["ext"], d["module_title"], d["method"], d["line"], "да" if d["export"] else "")
                               for d in dead["UNBOUND"]]))
    md.append("")
    md.append("### COMMENTED_ANNOTATION — %d" % len(dead["COMMENTED_ANNOTATION"]))
    md.append("")
    md.append(fileio.md_table(["Расширение", "Модуль", "Строка", "Закомментировано", "Живой метод ниже",
                               "Живые аннотации"],
                              [(d["ext"], d["module_title"], d["line"], d["annotation"],
                                "%s (стр. %d)" % (d["method"], d["method_line"]), ", ".join(d["live_interceptors"]))
                               for d in dead["COMMENTED_ANNOTATION"]]))
    md.append("")
    md.append("### DEAD_BINDING в собственных формах — %d" % len(dead["DEAD_BINDING_OWN_FORMS"]))
    md.append("")
    md.append(fileio.md_table(["Расширение", "Форма", "Элемент.Событие", "Обработчик", "Form.xml"],
                              [(d["ext"], d["form_title"], "%s.%s" % (d["owner"], d["event"]), d["handler"],
                                d["xml_line"]) for d in dead["DEAD_BINDING_OWN_FORMS"]]))
    md.append("")
    md.append("## Полный реестр")
    md.append("")
    md.append(fileio.md_table(
        ["№", "Расширение", "Модуль", "Перехват", "Метод расширения", "Директива расш./база", "Тело базы old→new",
         "Флаги"],
        [(r["id"], r["ext"], r["module_title"], what(r), r["ext_method"],
          "%s / %s" % (r.get("ext_directive") or "—", r.get("base_directive") or "—"), r.get("body_change") or "—",
          ", ".join(r["flags"])) for r in rows]))
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ext-root", action="extend", nargs="+", required=True,
                    help="folder with extension dumps (or one dump); repeatable")
    ap.add_argument("--ext", action="extend", nargs="+", help="only these extensions (name); repeatable")
    ap.add_argument("--skip", action="extend", nargs="+", help="skip these extensions (name); repeatable")
    ap.add_argument("--config-old", help="dump of the old configuration release")
    ap.add_argument("--config-new", required=True, help="dump of the new configuration release")
    ap.add_argument("--order", help="text file: extension names in session order (first = lowest layer)")
    ap.add_argument("--out", required=True, help="output folder for interceptors.md / interceptors.json")
    args = ap.parse_args(argv)
    from .control_rebase import refuse_out_inside  # here: control_rebase imports sig_problems from this module
    refuse_out_inside(args.out, [("--ext-root", r) for r in args.ext_root] +
                      [("--config-old", args.config_old), ("--config-new", args.config_new)], "interceptors")
    t0 = time.time()
    ctx = Context(args)
    if not ctx.exts:
        sys.exit("no extension dumps found under --ext-root")
    rows = collect(ctx)
    for n, r in enumerate(rows, 1):
        r["id"] = n
        check_row(ctx, r)
    groups = check_groups(ctx, rows)
    dead = dead_code(ctx, rows)
    elapsed = time.time() - t0
    md = render(ctx, rows, groups, dead, elapsed)
    data = {"config_new": ctx.cfg_new.root, "config_old": ctx.cfg_old.root if ctx.cfg_old else None,
            "extensions": [{"name": e.name, "purpose": e.purpose, "root": e.root} for e in ctx.exts],
            "order": ctx.order,
            "registry": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
            "groups": groups, "dead_code": dead, "unreadable_forms": ctx.form.unreadable}
    md_path, json_path = fileio.write_report(args.out, "interceptors", md, data)
    counts = defaultdict(int)
    for r in rows:
        for f in set(r["flags"]):
            counts[f] += 1
    print("extensions: %d, interceptions: %d, flagged: %d, %.1fs" % (
        len(ctx.exts), len(rows), sum(1 for r in rows if r["flags"]), elapsed))
    print("flags: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print("dead code: " + ", ".join("%s=%d" % (k, len(v)) for k, v in dead.items()))
    if ctx.form.unreadable:
        print("UNREADABLE Form.xml: %d — not in the registry: %s" % (
            len(ctx.form.unreadable), ", ".join("%s/%s" % (u["dump"], u["form"]) for u in ctx.form.unreadable[:5])))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
