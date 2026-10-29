# -*- coding: utf-8 -*-
"""Adaptation plan: the findings of control_rebase, bodies, interceptors, calls and queries merged into work items.

One item per extension + module + method (or call site / query place), grouped:
  MUST_FIX - what the update broke or changed under the extension: a rebased or conflicting &ИзменениеИКонтроль, an
             &Вместо copy (or a form handler replaced by callType="Override") whose vendor original changed, a
             signature that no longer matches, a removed base method, a call or a query that worked in old and does
             not in new, adopted metadata the vendor removed or retyped, a dynamic list of an adopted form whose new
             query drops a field of the extension, a register write that misses a new dimension, uses a dimension
             the vendor made a resource or an attribute, or goes to a register the vendor removed, an interceptor or
             a call of a method the vendor made obsolete in this release (TARGET_DEPRECATED, CALLS_DEPRECATED), an
             interceptor of a method the vendor code stopped calling on all paths (TARGET_UNCALLED) or on some of a
             few, through a vendor method that is still there (CALLERS_LOST, at most FEW_CALLERS callers in old), a
             call into a module of an adopted object the vendor removed or renamed, the extension's own method there
             included (OWNER_REMOVED / OWNER_RENAMED), a dynamic list whose query the extension replaces without a
             field the new vendor form needs (names: LIST_QUERY_FIELD_MISSING);
  REVIEW  - defects that were there before the update too (a call or a query that did not resolve in old either, a
             list field the old vendor form needed as well),
             and MUST_FIX items in code nothing live calls (no references, or references only from such code);
  CLARIFY  - questions of design: the same target in several extensions, CALLERS_LOST of a method with many vendor
             callers or lost only with the vendor methods that called it, a base method new in this release, an
             interception of a method that was not in old, an adopted object or field in neither release, an object
             or register XML that does not parse (look at the file), a list field the extension's UseAlways may not
             load (names: LIST_FIELD_NOT_LOADED - whether the platform replaces or merges UseAlways is not proven);
  the rest is counted by kind only (DEAD_BINDING, DIRECTIVE_MISMATCH, a vendor change under &Перед / &После, a
  compatible signature change, query fields that do not depend on the release).
The rules come from three hand-made ports (UT 11.5.22 -> 11.5.27): the human changed 41 of the 45 MUST_FIX items; of
everything hidden only three &После interceptors were touched, each next to another item (96 of 99 such were not).
The plan does not cover the extension's own copies of vendor DCS / templates under another name, and register writes
that fill dimensions through ЗаполнитьЗначенияСвойств or a structure.
"""
from collections import Counter, OrderedDict

from . import metadata, refs

GROUPS = ("MUST_FIX", "REVIEW", "CLARIFY")
GROUP_RU = OrderedDict([("MUST_FIX", "исправить"), ("REVIEW", "проверить"), ("CLARIFY", "уточнить")])
NOT_COVERED = ("план не покрывает собственные копии СКД и макетов вендора под другим именем и запись в регистры через ЗаполнитьЗначенияСвойств или структуру — их сверять по тексту")

CTL_ACTION = {
    "REBASED": "&ИзменениеИКонтроль: вендор изменил тело, кандидат готов — применить (apply_candidate)",
    "CONFLICTS": "&ИзменениеИКонтроль: конфликтов слияния %(conflicts)s — разрешить в кандидате, затем применить",
    "FAIL": "&ИзменениеИКонтроль: кандидат не прошёл проверку (%(problems)s)",
    "OUT_OF_SYNC": "&ИзменениеИКонтроль: расширение не совпадало с old ещё до обновления — перенос вручную",
    "MISSING": "&ИзменениеИКонтроль: метода вендора в new нет — снять перехват или перенести логику",
}
INT_MUST = {"SIGNATURE_CHANGED": "сигнатура базового метода поменялась old → new",
            "SIGNATURE_MISMATCH": "сигнатура перехватчика не совпадает с базовым методом",
            "MISSING_BASE": "перехватываемого метода в new нет",
            "TARGET_RENAMED": "вендор переименовал объект модуля (uuid тот же) — переименовать заимствование "
                              "(borrow-edit --rename), ссылки на старое имя в коде поправить",
            "TARGET_UNCALLED": "код вендора в old базовый метод вызывал, в new — нигде: перехват не выполнится — "
                               "перенести на метод, который вендор вызывает вместо него",
            "CALLERS_LOST": "код вендора в new перестал вызывать базовый метод на части путей (callers_lost): там "
                            "доработка больше не выполняется — найти, что вендор вызывает вместо него, и перенести "
                            "или продублировать перехват"}
# CALLERS_LOST is a fix when a business method (at most FEW_CALLERS vendor callers in old) lost a caller that is still
# there in new and calls something else now - a real break: ПриЗаписи of ТоварыКОтгрузке (3 callers) calls
# ДанныеДляФормированияОчереди… + АктуализироватьОчередь… instead of ДобавитьВОчередьФормированияРасходныхОрдеров.
# It is a question otherwise: a library method (ДлительныеОперации.ВыполнитьВФоне - 498 callers in UT 11.5.22) loses
# callers in every release, and a caller the vendor deleted with its whole path (СервисEDIУТ.ПриОтменеЗаказаКлиента,
# СоглашениеПриИзмененииЗавершение of the ЗаказКлиента form, which a hand port left alone) says the logic
# moved, not where - whether the customization is needed there is up to its author
FEW_CALLERS = 10
CALLERS_LOST_MANY = ("код вендора в new перестал вызывать базовый метод на части путей (callers_lost), а вызывающих "
                     "его мест много (%d в old) — проверить, нужна ли доработка на потерянных путях")
CALLERS_LOST_GONE = ("код вендора в new перестал вызывать базовый метод: вызывавшие его методы вендор удалил "
                     "(callers_lost, method_in_new: false) — проверить, куда перенесена их логика и нужна ли там "
                     "доработка")
INT_CLARIFY = {"NEW_BASE": "базовый метод появился только в new",
               "CONFLICT": "одна цель перехвачена в нескольких расширениях",
               "OVERRIDDEN?": "перехват, возможно, перекрыт другим расширением (порядок подключения не задан)",
               "OVERRIDDEN": "перехват перекрыт другим расширением",
               "BINDING_CHANGED": "привязка события формы у вендора поменялась"}


class Plan(object):
    def __init__(self):
        self.items = OrderedDict()
        self.hidden = Counter()

    def item(self, ext, module, method, title=None):
        key = (ext, module, method or "")
        if key not in self.items:
            self.items[key] = {"ext": ext, "module": module, "module_title": title, "method": method,
                               "groups": set(), "what": [], "sources": []}
        it = self.items[key]
        if title and not it["module_title"]:
            it["module_title"] = title
        return it

    def add(self, group, ext, module, method, what, source, title=None):
        it = self.item(ext, module, method, title)
        it["groups"].add(group)
        if what not in it["what"]:
            it["what"].append(what)
        it["sources"].append(source)

    def hide(self, kind, n=1):
        self.hidden[kind] += n


def _control(p, ctl):
    for m in ctl.get("methods", []):
        v = m.get("verdict")
        if v in CTL_ACTION:
            what = CTL_ACTION[v] % {"conflicts": m.get("conflicts", "?"), "problems": "; ".join(m.get("problems") or [])}
            same = sum(1 for k in m.get("conflict_kinds") or [] if k["kind"] == "same_point")
            if same:
                what += "; из них вставок в одной точке %d" % same
            p.add("MUST_FIX", m["ext"], m["module"], m["ext_method"], what, "control_rebase:" + v, m.get("module_title"))
        else:
            p.hide("control_rebase:" + str(v))


def _bodies(p, bodies):
    for c in bodies.get("cases", []):
        note = c.get("note") or ""
        args = (c["ext"], c["module"], c["copy_method"])
        # a form handler replaced by callType="Override" in Form.xml is the same frozen copy, bound in the XML
        calltype = c.get("binding") == "callType"
        what, port = "копия &Вместо", "port_replace / merge-copy"
        if calltype:
            what = "копия Вместо (callType %s.%s → %s)" % (c.get("owner"), c.get("event"), c["target"])
            port = "port_replace с copy_method=%s / merge-copy --copy-method" % c["copy_method"]
        if note:
            if "new" in note and "old и new" not in note:
                p.add("MUST_FIX", *args, what + ": " + note, "bodies:" + note, c.get("module_title"))
            else:
                p.add("CLARIFY", *args, what + ": " + note, "bodies:" + note, c.get("module_title"))
            continue
        change = (c.get("change") or {}).get("old->new", "=")
        if change != "=":
            p.add("MUST_FIX", *args, "%s: вендор изменил оригинал (old→new %s) — перенести копию (%s)" % (
                what, change, port), "bodies:vendor_changed", c.get("module_title"))
        else:
            p.hide("bodies:оригинал копии не менялся")


def _interceptors(p, icp):
    for r in icp.get("registry", []):
        args = (r["ext"], r["module"], r["ext_method"])
        shown = False
        for f in r.get("flags") or []:
            if f == "CALLERS_LOST" and (r.get("vendor_callers") or {}).get("old", 0) > FEW_CALLERS:
                p.add("CLARIFY", *args, CALLERS_LOST_MANY % r["vendor_callers"]["old"], "interceptors:" + f,
                      r.get("module_title"))
                shown = True
            elif f == "CALLERS_LOST" and all(c.get("method_in_new") is False for c in r.get("callers_lost") or []):
                p.add("CLARIFY", *args, CALLERS_LOST_GONE, "interceptors:" + f, r.get("module_title"))
                shown = True
            elif f in INT_MUST:
                p.add("MUST_FIX", *args, INT_MUST[f], "interceptors:" + f, r.get("module_title"))
                shown = True
            elif f in INT_CLARIFY:
                p.add("CLARIFY", *args, INT_CLARIFY[f], "interceptors:" + f, r.get("module_title"))
                shown = True
            elif f == "CONTROL_MISMATCH":
                shown = True  # the same fact as the control_rebase verdict of this method
            elif f == "TARGET_DEPRECATED" and (r.get("deprecated") or {}).get("grade") == "risk":
                p.add("MUST_FIX", *args, "вендор пометил базовый метод устаревшим в new — проверить, вызывает ли он "
                                         "его ещё", "interceptors:" + f, r.get("module_title"))
                shown = True
            else:
                p.hide("interceptors:" + f)
        if not shown and r.get("body_change") not in (None, "", "=") and r.get("kind") in ("Перед", "После"):
            p.hide("interceptors:тело вендора изменилось под &%s" % r["kind"])


def _calls(p, calls):
    for f in calls.get("findings", []):
        flags = f.get("flags") or []
        if not flags:
            continue
        args = (f["ext"], f["module"], f.get("caller") or None)
        what = "вызов %s: %s" % (f["call"], f.get("note", ""))
        copy = f.get("copy")
        dep_risk = (f.get("deprecated") or {}).get("grade") == "risk"
        rest = [x for x in flags if x != "CALLS_DEPRECATED"]
        if copy and copy.get("target_in_new") and copy.get("vendor_old") and not copy.get("vendor_new"):
            p.add("MUST_FIX", *args, "в копии есть вызовы, которые вендор в new убрал — уйдут пересъёмкой копии",
                  "calls:" + ",".join(flags) + ":fixed_by_report", f.get("module_title"))
        elif not rest or (rest == ["SIGNATURE_CHANGED"] and not f.get("note", "").startswith("[риск]")):
            if dep_risk:
                p.add("MUST_FIX", *args, what, "calls:" + ",".join(flags), f.get("module_title"))
            elif (f.get("deprecated") or {}).get("vendor_calls_new"):
                p.hide("calls:CALLS_DEPRECATED (вендор сам вызывает в new)")
            else:
                p.hide("calls:%s" % ("CALLS_DEPRECATED (устарел и в old)" if not rest else
                                     ",".join(flags) + " (совместимо)"))
        elif {"REMOVED", "OWNER_REMOVED", "OWNER_RENAMED", "SIGNATURE_CHANGED"} & set(flags) or \
                "сломано обновлением" in f.get("note", ""):
            p.add("MUST_FIX", *args, what, "calls:" + ",".join(flags), f.get("module_title"))
        else:
            p.add("REVIEW", *args, what + " (было и до обновления)", "calls:" + ",".join(flags), f.get("module_title"))


def file_title(rel):
    """A title for a file the queries report names: a module or a form as metadata.module_title has it, a template
    (Template.xml of a DCS schema) as <Вид>.<Объект>.Макет.<Имя> or ОбщийМакет.<Имя>; None without a path."""
    if not rel:
        return None
    parts = metadata.norm_rel(rel).split("/")
    if parts[0].casefold() == "commontemplates" and len(parts) >= 2:
        return "ОбщийМакет." + parts[1]
    kind = metadata.FOLDER2KIND.get(parts[0].lower())
    if kind and len(parts) >= 4 and parts[2].casefold() == "templates":
        return "%s.%s.Макет.%s" % (kind, parts[1], parts[3])
    return metadata.module_title(rel)


def _queries(p, queries):
    for f in queries.get("findings", []):
        state = f.get("old") or ""
        where = f.get("where") or ""
        # "метод X" is a method of the module; "набор …", "список …", "строка", "код" are places of the file
        method = where[len("метод "):] if where.startswith("метод ") else None
        args = (f["ext"], f.get("file"), method)
        title = f.get("module_title") or file_title(f.get("file"))
        what = "%s%s: %s %s — %s" % ("" if method or not where else where + ", ", f.get("part"), f.get("kind"),
                                     f.get("ref"), f.get("note", ""))
        if state == "было корректно":
            p.add("MUST_FIX", *args, what + " (сломано обновлением)", "queries:" + str(f.get("part")), title)
        elif state == "тоже не разрешается":
            p.add("REVIEW", *args, what + " (не разрешалось и в old)", "queries:" + str(f.get("part")), title)
        else:
            p.hide("queries:%s %s" % (f.get("part"), state or "—"))


def _borrowed(p, borrowed):
    for f in (borrowed or {}).get("findings", []):
        what = f["detail"] + ("; формы расширения с этим полем: " + ", ".join(f["forms"]) if f.get("forms") else "")
        # an XML that does not parse is not judged: the file is to be looked at, not the adoption fixed
        group = "CLARIFY" if f["check"] in ("NOT_IN_VENDOR", "XML_UNREADABLE") else "MUST_FIX"
        p.add(group, f["ext"], f["file"], None, "заимствование %s: %s" % (f["object"], what), "borrowed:" + f["check"],
              f["object"])


def _registers(p, registers):
    for f in (registers or {}).get("findings", []):
        check = f.get("check", "CHANGED")
        if check == "REGISTER_REMOVED":
            kind = "removed"
        elif check == "XML_UNREADABLE":
            kind = "unreadable"
        elif f.get("stale"):
            kind = "stale"
        else:
            kind = "key_lost" if f.get("key_lost") else "new_dimensions"
        p.add("CLARIFY" if check == "XML_UNREADABLE" else "MUST_FIX", f["ext"], f["module"], f["method"] or None,
              "запись в регистр " + f["detail"], "registers:" + kind, f.get("module_title"))


def _names(p, names):
    """Only LIST_QUERY_FIELD_MISSING and LIST_FIELD_NOT_LOADED of the names report: the other names checks are not in
    the plan."""
    for f in (names or {}).get("findings", []):
        if f.get("check") not in ("LIST_QUERY_FIELD_MISSING", "LIST_FIELD_NOT_LOADED"):
            continue
        method = (f.get("methods") or [None])[0]
        if f["check"] == "LIST_FIELD_NOT_LOADED":
            group = "CLARIFY"
        else:
            group = "MUST_FIX" if f.get("level") == "error" else "REVIEW"
        p.add(group, f["ext"], f["module"], method, f["detail"], "names:" + f["check"], f.get("form"))


def build(ctl, bodies, icp, calls, queries, borrowed=None, registers=None, names=None):
    """{'items': [...], 'counts': {group: n}, 'hidden': {kind: n}, 'not_covered': text}; items are ordered by group,
    then extension and module. A MUST_FIX item in code no extension calls (UNBOUND, or a caller without references)
    goes to REVIEW with a note; so does one whose method is referenced only from such code (refs.liveness: the
    caller_live of calls, the method_live of queries and registers). borrowed - the report of borrowed.py (adopted metadata against new), registers - of
    registers.py (writes to registers whose structure changed), names - of names.py (only LIST_QUERY_FIELD_MISSING and
    LIST_FIELD_NOT_LOADED are taken); all three optional."""
    p = Plan()
    _control(p, ctl)
    _bodies(p, bodies)
    _interceptors(p, icp)
    _calls(p, calls)
    _queries(p, queries)
    _borrowed(p, borrowed)
    _registers(p, registers)
    _names(p, names)
    dead = {(d["ext"], d["module"], d["method"]) for d in (icp.get("dead_code") or {}).get("UNBOUND", [])}
    dead |= {(f["ext"], f["module"], f.get("caller") or "") for f in calls.get("findings", [])
             if f.get("caller_live") in refs.DEAD}
    # a query or a register write in a method nothing live calls (queries / registers: method_live)
    dead |= {(f["ext"], f.get("file"), f["where"][len("метод "):]) for f in queries.get("findings", [])
             if f.get("method_live") in refs.DEAD and (f.get("where") or "").startswith("метод ")}
    dead |= {(f["ext"], f["module"], f.get("method") or "") for f in (registers or {}).get("findings", [])
             if f.get("method_live") in refs.DEAD}
    items = []
    for key, it in p.items.items():
        group = next(g for g in GROUPS if g in it["groups"])
        if group == "MUST_FIX" and key in dead:
            group = "REVIEW"
            it["what"].append("метод не вызывается из расширений (мёртвый код?) — сначала решить, нужен ли он")
        items.append({"group": group, "ext": it["ext"], "module": it["module"], "module_title": it["module_title"],
                      "method": it["method"], "what": it["what"], "sources": it["sources"]})
    order = {g: k for k, g in enumerate(GROUPS)}
    items.sort(key=lambda x: (order[x["group"]], x["ext"], x["module_title"] or x["module"] or "", x["method"] or ""))
    counts = Counter(x["group"] for x in items)
    return {"items": items, "counts": {g: counts.get(g, 0) for g in GROUPS}, "hidden": dict(sorted(p.hidden.items())),
            "not_covered": NOT_COVERED}


def render(plan, title):
    """plan.md for a person."""
    md = ["# План адаптации — %s" % title, ""]
    md.append(", ".join("%s: %d" % (GROUP_RU[g], plan["counts"][g]) for g in GROUPS) + ".")
    md.append("")
    md.append("Скрыто (только счёт): " + (", ".join("%s — %d" % kv for kv in plan["hidden"].items()) or "нет") + ".")
    md.append("")
    md.append("**Не покрыто:** " + plan["not_covered"] + ".")
    for g in GROUPS:
        rows = [x for x in plan["items"] if x["group"] == g]
        md.append("")
        md.append("## %s — %d" % (GROUP_RU[g].capitalize(), len(rows)))
        md.append("")
        if not rows:
            md.append("Нет.")
            continue
        md.append("| Расширение | Модуль | Метод | Что произошло и что делать |")
        md.append("|---|---|---|---|")
        for x in rows:
            cell = "<br>".join(w.replace("|", "\\|") for w in x["what"])
            md.append("| %s | %s | %s | %s |" % (x["ext"], x["module_title"] or x["module"] or "—", x["method"] or "—",
                                                 cell))
    return "\n".join(md)


def screen_lines(plan, limit=40):
    """Lines of the prepare screen: the counts, then the MUST_FIX items, at most `limit`."""
    lines = ["план: " + ", ".join("%s %d" % (GROUP_RU[g], plan["counts"][g]) for g in GROUPS) +
             "; скрыто: %d" % sum(plan["hidden"].values())]
    must = [x for x in plan["items"] if x["group"] == "MUST_FIX"]
    for x in must[:limit]:
        lines.append("  - %s / %s / %s: %s" % (x["ext"], x["module_title"] or x["module"], x["method"] or "—",
                                               x["what"][0]))
    if len(must) > limit:
        lines.append("  … ещё %d — в plan.md" % (len(must) - limit))
    lines.append("  " + plan["not_covered"])
    return lines
