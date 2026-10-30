# -*- coding: utf-8 -*-
"""Parameter names of the vendor's spreadsheet templates that the new release removed or renamed.

    cfe-rebase template-params --ext-root <extension dumps> --config-old <old> --config-new <new> [--ext NAME]
                               --out <dir>

A print form fills an area by parameter name: Область.Параметры.<Имя> = …, Параметры.Заполнить(Структура).
A name the area does not have is skipped without an error, so a template that keeps an old name prints an empty cell
(a real case: 11.5.27 renamed ПредставлениеЕдининицыИзмеренияУпаковки -> ПредставлениеЕдиницыИзмеренияУпаковки in
ПФ_MXL_РасходныйОрдерНаТовары; the user template of the base, taken from 11.5.17, kept the old name, and the order
printed with the unit of the package column empty). Templates that keep the old names after an update are the user
templates of the base (РегистрСведений.ПользовательскиеМакетыПечати), copies of the template in extensions, and
borrowed templates with their own content; code of an extension that fills a parameter by the old name misses the new
vendor template the same way.

Names of a template (Templates/*/Ext/Template.xml and CommonTemplates/*/Ext/Template.xml of a spreadsheet document):
<parameter>, <detailParameter> and [Имя] in the text of a cell whose format has fillType Template (an expression in
brackets, such as [Формат(Дата, "ДФ=dd")], and a footnote such as [1] are not names). Names compare casefolded, as
1C compares them; a count is the number of cells. A template whose file is the same in old and new is not parsed.

Pairs old -> new: in one template, a name gone from old and a name new in new are one renamed parameter when the
Levenshtein distance of the casefolded names is at most 2, the names are at least 8 characters long and differ not
only in digits (Колонка1 -> Колонка2 is two parameters), the counts of cells are equal, and the pair is the only
candidate of both names. A typo fix touches a character or two and keeps the cells; anything less sure stays gone
plus added.

Each finding has `check`:
  CODE_OLD_NAME      risk: a module of the extension fills a gone name of the template - Параметры.<Имя>,
                     Параметры["<Имя>"]; for a renamed name also a string literal holding it as a word (a structure
                     key, a query alias for Параметры.Заполнить) - such a word alone counts only when the same
                     modules write none of the new names as Параметры.<Имя>: a module that moved its writes to the
                     new name keeps old aliases as plumbing. Modules: those of the template's owner object, all
                     modules for a common template. Comments and #Удаление blocks are not code.
  TEMPLATE_OLD_NAME  risk: a template of the extension under the same owner keeps a gone name in effect:
                     - the borrowed vendor template whose content the platform uses (InternalInfo: PropertyState
                       Template = Extended; without it the session prints the vendor's template). In the merge mode
                       («Объединять»: <baseSheet> + <extensionMethod>Merge) a cell the extension did not change takes
                       the vendor's new value, so the old name counts only when the extension sheet holds it in more
                       cells than the base sheet (a real case: ПФ_MXL_ЗаказНаПеремещение, НакладнаяНаПеремещение
                       are Extended + Merge with the old name in both sheets, and 11.5.27 prints the unit - proven by
                       print);
                     - an own copy, when the extension's code fills the new name and takes by name an area of the copy
                       (ПолучитьОбласть on ПолучитьМакет("<copy>") / МакетПечатнойФормы("….<copy>") or on a
                       variable assigned from it) that holds a cell with the old name
  COPY_OLD_NAME      info: an own copy with the old name and the code on the new name, but no area the code takes
                     from the copy holds that cell (or the code takes no area by name): check whether the area with
                     the parameter is read from the copy
  VENDOR_RENAMED     risk, one per vendor print template (name holds «ПФ_», as БСП lists the templates a user may
                     override) with a renamed pair: check the user templates of the base. Risk, not info: the data is
                     still there and the vendor still prints it, only under the new name, so an overridden template
                     silently loses a column (proven by print on a real base)
  VENDOR_GONE        info, the same for a print template with gone names and no pair: the vendor dropped or rebuilt
                     the data (Акциз -> АкцизДоИзменения, АкцизПослеИзменения), the old layout needs a redesign anyway
Templates with only added names are counted, not listed: an old template simply lacks the new cells.
The dumps are never written: --out inside --ext-root, --config-old or --config-new is refused.
"""
import argparse
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict

from . import bsl, fileio, metadata
from .control_rebase import inside

NS = "{http://v8.1c.ru/8.2/data/spreadsheet}"
V8 = "{http://v8.1c.ru/8.1/data/core}"
SPREADSHEET = b"http://v8.1c.ru/8.2/data/spreadsheet"
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё_][0-9A-Za-zА-Яа-яЁё_]*$")
BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")
WORD = r"(?<![0-9A-Za-zА-Яа-яЁё_])%s(?![0-9A-Za-zА-Яа-яЁё_])"
PRINT_MARK = "пф_"  # БСП (УправлениеПечатью): СтрНайти(Макет.Имя, "ПФ_") > 0 - a template a user may override
PAIR_MAX_DIST = 2
PAIR_MIN_LEN = 8
EXTENDED_RE = re.compile(r"<xr:Property>\s*Template\s*</xr:Property>\s*<xr:State>\s*Extended\s*</xr:State>")
LOOSE = "строка"  # a word inside a string literal: a structure key or a query alias, not a write by itself
RISK = "РИСК"
INFO = "ПРОВЕРИТЬ"
ORDER = ("CODE_OLD_NAME", "TEMPLATE_OLD_NAME", "VENDOR_RENAMED", "COPY_OLD_NAME", "VENDOR_GONE")
SILENT = "ячейка печатается пустой, без ошибки"
REGISTER_HINT = "проверить пользовательские макеты в базе (РегистрСведений.ПользовательскиеМакетыПечати)"


# -- template files --------------------------------------------------------------------------------------------------

def template_files(root):
    """{rel casefolded: (rel, kind RU, owner name or None, template name)} of every Template.xml of a dump."""
    out = {}
    if not os.path.isdir(root):
        return out
    for top in sorted(os.listdir(root)):
        kind = metadata.FOLDER2KIND.get(top.lower())
        if kind is None:
            continue
        base = os.path.join(root, top)
        if not os.path.isdir(base):
            continue
        if kind == "ОбщийМакет":
            for t in sorted(os.listdir(base)):
                rel = "%s/%s/Ext/Template.xml" % (top, t)
                if os.path.isfile(os.path.join(root, *rel.split("/"))):
                    out[rel.casefold()] = (rel, kind, None, t)
            continue
        for obj in sorted(os.listdir(base)):
            tdir = os.path.join(base, obj, "Templates")
            if not os.path.isdir(tdir):
                continue
            for t in sorted(os.listdir(tdir)):
                rel = "%s/%s/Templates/%s/Ext/Template.xml" % (top, obj, t)
                if os.path.isfile(os.path.join(root, *rel.split("/"))):
                    out[rel.casefold()] = (rel, kind, obj, t)
    return out


def template_title(kind, owner, name):
    return "ОбщийМакет.%s" % name if owner is None else "%s.%s.%s" % (kind, owner, name)


def is_spreadsheet(data):
    return SPREADSHEET in data[:2048]


def _text(tl):
    return "".join(c.text or "" for c in tl.iter(V8 + "content"))


def _fills(el):
    """fillType of every <format> of a sheet, in order: <f> and <formatIndex> count them from 1."""
    return [(f.findtext(NS + "fillType") or "") for f in el.findall(NS + "format")]


def sheet_names(el):
    """OrderedDict {casefolded name: [name as written first, cells, [(row, column)]]} of one sheet: the <document>
    itself or its <baseSheet>. Rows are the sheet's own rowsItem, so a <baseSheet> inside a document is not read
    with it."""
    fill = _fills(el)

    def fill_of(idx):
        try:
            k = int(idx)
        except (TypeError, ValueError):
            return ""
        return fill[k - 1] if 1 <= k <= len(fill) else ""

    names = OrderedDict()

    def add(name, pos):
        name = (name or "").strip()
        if not NAME_RE.match(name):  # a detailParameter may hold an expression; [1] is a footnote
            return
        got = names.setdefault(name.casefold(), [name, 0, []])
        got[1] += 1
        got[2].append(pos)

    for item in el.findall(NS + "rowsItem"):
        try:
            r = int(item.findtext(NS + "index"))
        except (TypeError, ValueError):
            r = -1
        row = item.find(NS + "row")
        if row is None:
            continue
        row_fill = fill_of(row.findtext(NS + "formatIndex"))
        col = -1
        for group in row.findall(NS + "c"):
            i = group.findtext(NS + "i")
            col = int(i) if i is not None and i.strip().lstrip("-").isdigit() else col + 1
            cell = group.find(NS + "c")
            if cell is None:
                continue
            for tag in ("parameter", "detailParameter"):
                for x in cell.findall(NS + tag):
                    add(x.text, (r, col))
            tl = cell.find(NS + "tl")
            if tl is None:
                continue
            f = cell.findtext(NS + "f")
            if (fill_of(f) if f is not None else row_fill) != "Template":
                continue
            for m in BRACKET_RE.finditer(_text(tl)):
                add(m.group(1), (r, col))
    return names


def _areas(el):
    """{casefolded area name: (type, begin row, end row, begin column, end column)} of the named areas of a sheet."""
    out = {}
    for item in el.findall(NS + "namedItem"):
        area = item.find(NS + "area")
        name = (item.findtext(NS + "name") or "").strip()
        if area is None or not name:
            continue
        nums = []
        for tag in ("beginRow", "endRow", "beginColumn", "endColumn"):
            try:
                nums.append(int(area.findtext(NS + tag)))
            except (TypeError, ValueError):
                nums.append(-1)
        out[name.casefold()] = ((area.findtext(NS + "type") or "").strip(),) + tuple(nums)
    return out


def parse_sheet(data):
    """{names, base (names of <baseSheet> or None), method (<extensionMethod> or ""), areas} of a Template.xml."""
    root = ET.fromstring(data)
    base = root.find(NS + "baseSheet")
    return {"names": sheet_names(root), "base": sheet_names(base) if base is not None else None,
            "method": (root.findtext(NS + "extensionMethod") or "").strip(), "areas": _areas(root)}


def parse_names(data):
    """sheet_names of the document of a spreadsheet Template.xml (bytes): the extension's sheet of a borrowed one."""
    return sheet_names(ET.fromstring(data))


def read_sheet(path):
    """parse_sheet of a file, or None for a template that is not a spreadsheet or does not parse."""
    data = fileio.read_bytes(path)
    if not is_spreadsheet(data):
        return None
    try:
        return parse_sheet(data)
    except ET.ParseError:
        return None


def read_names(path):
    """(names, None) or (None, error text); names is None also for a template that is not a spreadsheet."""
    data = fileio.read_bytes(path)
    if not is_spreadsheet(data):
        return None, None
    try:
        return parse_names(data), None
    except ET.ParseError as exc:
        return None, str(exc)


def in_area(pos, area):
    kind, br, er, bc, ec = area
    row_ok = br < 0 or br <= pos[0] <= er
    col_ok = bc < 0 or bc <= pos[1] <= ec
    if kind == "Rows":
        return row_ok
    if kind == "Columns":
        return col_ok
    return row_ok and col_ok


def template_extended(meta_text):
    """(adopted, extended): the template's metadata XML says ObjectBelonging Adopted, and its InternalInfo says the
    platform uses the extension's content (PropertyState Template = Extended). Without Extended an adopted template
    is a reference only: the session prints the vendor's template, whatever lies in Ext/Template.xml."""
    return (bool(re.search(r"<ObjectBelonging>\s*Adopted\s*</ObjectBelonging>", meta_text)),
            bool(EXTENDED_RE.search(meta_text)))


GET_AREA = {"получитьобласть", "getarea"}
GET_TEMPLATE = {"получитьмакет", "gettemplate", "макетпечатнойформы", "получитьобщиймакет", "getcommontemplate"}


def _call_strings(toks, open_idx):
    """(string arguments, index after the closing parenthesis) of a call whose «(» is toks[open_idx]."""
    depth, strs, j = 0, [], open_idx
    while j < len(toks):
        t = toks[j]
        if t[0] == "op" and t[1] == "(":
            depth += 1
        elif t[0] == "op" and t[1] == ")":
            depth -= 1
            if depth == 0:
                return strs, j + 1
        elif t[0] == "str":
            strs.append(t[1].strip())
        j += 1
    return strs, j


def _names_template(strs, template):
    t = template.casefold()
    return any(x.casefold() == t or x.casefold().endswith("." + t) for x in strs)


def copy_areas(module_texts, template):
    """{casefolded area name: name as written} («A|B» split) of the areas the modules take from the template by
    name: ПолучитьОбласть("X") on ПолучитьМакет("<template>") / МакетПечатнойФормы("….<template>") or on a variable
    assigned from such a call (one variable namespace per module).
    None when no module takes an area from it: what is printed of the copy is then unknown."""
    found = None
    for text in module_texts:
        toks = bsl.tokenize(text)
        holders = set()
        for i, t in enumerate(toks):
            if t[0] != "id" or i + 1 >= len(toks) or toks[i + 1][:2] != ("op", "("):
                continue
            low = t[1].casefold()
            if low in GET_TEMPLATE:
                strs, _ = _call_strings(toks, i + 1)
                k = i - 1
                while k >= 0 and toks[k][0] in ("id", "op") and toks[k][1] == ".":
                    k -= 2  # Модуль.МакетПечатнойФормы(...)
                if _names_template(strs, template) and k >= 1 and toks[k][:2] == ("op", "=") \
                        and toks[k - 1][0] == "id":
                    holders.add(toks[k - 1][1].casefold())
            elif low in GET_AREA:
                strs, _ = _call_strings(toks, i + 1)
                if not strs or i < 2 or toks[i - 1][:2] != ("op", "."):
                    continue
                recv = toks[i - 2]
                hit = recv[0] == "id" and recv[1].casefold() in holders
                if not hit and recv[:2] == ("op", ")"):
                    depth, k = 0, i - 2
                    while k >= 0:
                        if toks[k][:2] == ("op", ")"):
                            depth += 1
                        elif toks[k][:2] == ("op", "("):
                            depth -= 1
                            if depth == 0:
                                break
                        k -= 1
                    if k >= 1 and toks[k - 1][0] == "id" and toks[k - 1][1].casefold() in GET_TEMPLATE:
                        hit = _names_template(_call_strings(toks, k)[0], template)
                if hit:
                    found = found if found is not None else {}
                    for x in strs[0].split("|"):
                        if x.strip():
                            found.setdefault(x.strip().casefold(), x.strip())
    return found


# -- pairs -----------------------------------------------------------------------------------------------------------

def levenshtein(a, b, cap=PAIR_MAX_DIST):
    """Edit distance of a and b, or cap + 1 when it is larger than cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _digits_only(a, b):
    return re.sub(r"\d", "", a) == re.sub(r"\d", "", b)


def pair_names(gone, added):
    """[(old key, new key)] of renamed parameters; gone / added: {key: [name, cells]}."""
    cand = {}
    for g, gv in gone.items():
        for a, av in added.items():
            gc, ac = gv[1], av[1]
            if (gc == ac and min(len(g), len(a)) >= PAIR_MIN_LEN and not _digits_only(g, a)
                    and levenshtein(g, a) <= PAIR_MAX_DIST):
                cand.setdefault(g, []).append(a)
    back = Counter(a for lst in cand.values() for a in lst)
    return [(g, lst[0]) for g, lst in cand.items() if len(lst) == 1 and back[lst[0]] == 1]


def vendor_diff(cfg_old, cfg_new):
    """(changes, stats): changes - one dict per vendor template whose names changed: rel, kind, owner, name, title,
    print, renamed [{old, new, cells}], gone [{name, cells}], added [{name, cells}]."""
    old_t, new_t = template_files(cfg_old), template_files(cfg_new)
    st = Counter(templates_old=len(old_t), templates_new=len(new_t))
    st["templates_removed"] = sum(1 for k in old_t if k not in new_t)
    changes = []
    for key, (rel, kind, owner, name) in old_t.items():
        if key not in new_t:
            continue
        po = os.path.join(cfg_old, *rel.split("/"))
        pn = os.path.join(cfg_new, *new_t[key][0].split("/"))
        a, b = fileio.read_bytes(po), fileio.read_bytes(pn)
        if not (is_spreadsheet(a) and is_spreadsheet(b)):
            continue
        st["spreadsheets"] += 1
        if a == b:
            continue
        st["spreadsheets_changed"] += 1
        try:
            na, nb = parse_names(a), parse_names(b)
        except ET.ParseError:
            st["unreadable"] += 1
            continue
        gone = {k: v for k, v in na.items() if k not in nb}
        added = {k: v for k, v in nb.items() if k not in na}
        if not gone and not added:
            continue
        pairs = pair_names(gone, added)
        paired_old, paired_new = {g for g, _ in pairs}, {x for _, x in pairs}
        ch = {"rel": rel, "kind": kind, "owner": owner, "name": name, "title": template_title(kind, owner, name),
              "print": PRINT_MARK in name.casefold(),
              "renamed": [{"old": gone[g][0], "new": added[x][0], "cells": gone[g][1]} for g, x in pairs],
              "gone": [{"name": v[0], "cells": v[1]} for k, v in gone.items() if k not in paired_old],
              "added": [{"name": v[0], "cells": v[1]} for k, v in added.items() if k not in paired_new]}
        changes.append(ch)
        if gone:
            st["with_gone"] += 1
            st["print_with_gone"] += ch["print"]
        else:
            st["added_only"] += 1
        st["renamed_pairs"] += len(pairs)
        st["print_with_renamed"] += bool(ch["print"] and pairs)
    return changes, st


# -- extensions ------------------------------------------------------------------------------------------------------

def _owner_prefix(ch):
    folder = metadata.KINDS[ch["kind"]][0]
    return None if ch["owner"] is None else ("%s/%s/" % (folder, ch["owner"])).casefold()


def mentions(module_text, names, loose):
    """[(key, line, how)] of the names (casefolded keys) a module fills: «Параметры.X», «Параметры["X"]»; keys in
    loose also as a word inside a string literal («строка»)."""
    toks = bsl.tokenize(module_text)
    out = []
    for i, t in enumerate(toks):
        if t[0] == "id" and t[1].casefold() == "параметры" and i + 2 < len(toks):
            op, nxt = toks[i + 1], toks[i + 2]
            if op == ("op", ".", op[2]) and nxt[0] == "id" and nxt[1].casefold() in names:
                out.append((nxt[1].casefold(), nxt[2], "Параметры.%s" % nxt[1]))
            elif op[0] == "op" and op[1] == "[" and nxt[0] == "str" and nxt[1].strip().casefold() in names:
                out.append((nxt[1].strip().casefold(), nxt[2], 'Параметры["%s"]' % nxt[1].strip()))
        elif t[0] == "str" and loose:
            low = t[1].casefold()
            for key in loose:
                for m in re.finditer(WORD % re.escape(key), low):
                    out.append((key, t[2] + t[1].count("\n", 0, m.start()), LOOSE))
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _meta_rel(rel):
    """Documents/X/Templates/T/Ext/Template.xml -> Documents/X/Templates/T.xml"""
    return rel[:-len("/Ext/Template.xml")] + ".xml"


def _same_owner(ch, kind, owner, name):
    if ch["owner"] is None:
        return owner is None and name.casefold() == ch["name"].casefold()
    return owner is not None and kind == ch["kind"] and owner.casefold() == ch["owner"].casefold()


def ext_templates(ext, tpls, ch, gone, sheets, scope_texts, new_places):
    """(risky, unproven): templates of the extension under the template's owner that keep a gone name in effect.
    A borrowed vendor template counts only when the platform uses its content (Template = Extended); in the merge mode
    («Объединять», <baseSheet>) a cell the extension did not change takes the vendor's new value, so only a gone name
    the extension sheet holds more often than the base sheet stays. An own copy counts when the code was moved to the
    new names (new_places): risky when a cell with the old name lies in an area the code takes from the copy,
    unproven when the code takes other areas or none by name."""
    risky, unproven = [], []
    for rel, kind, owner, name in tpls:
        if not _same_owner(ch, kind, owner, name):
            continue
        if rel not in sheets:
            sheets[rel] = read_sheet(ext.path(rel))
        sh = sheets[rel]
        if not sh:
            continue
        kept = [k for k in gone if k in sh["names"]]
        if not kept:
            continue
        borrowed = name.casefold() == ch["name"].casefold()
        row = {"path": ext.path(rel), "rel": rel, "template": name, "borrowed": borrowed}
        if borrowed:
            meta = ext.path(_meta_rel(rel))
            _, extended = template_extended(fileio.read_text(meta) if os.path.isfile(meta) else "")
            if not extended:
                continue  # the session prints the vendor's template
            base = sh["base"]
            if base is not None:
                kept = [k for k in kept if sh["names"][k][1] > (base[k][1] if k in base else 0)]
                row["merge"] = True
            if kept:
                risky.append(dict(row, names=[sh["names"][k][0] for k in kept]))
            continue
        if not new_places:
            continue
        used = copy_areas(scope_texts, name)
        hit = []
        for k in kept:
            for area_name in used or ():
                parts = [sh["areas"].get(x) for x in area_name.split("|")]
                if any(parts) and any(all(in_area(pos, a) for a in parts if a) for pos in sh["names"][k][2]):
                    hit.append(k)
                    break
        row["areas_used"] = sorted(used.values(), key=str.casefold) if used else []
        if hit:
            risky.append(dict(row, names=[sh["names"][k][0] for k in hit]))
        else:
            unproven.append(dict(row, names=[sh["names"][k][0] for k in kept]))
    return risky, unproven


def ext_findings(ext, changes):
    """CODE_OLD_NAME / TEMPLATE_OLD_NAME / COPY_OLD_NAME findings of one extension."""
    modules = [r for r in ext.files(".bsl")]
    tpls = list(template_files(ext.root).values())
    texts, sheets = {}, {}
    out = []
    for ch in changes:
        gone = {r["old"].casefold(): r["old"] for r in ch["renamed"]}
        gone.update({g["name"].casefold(): g["name"] for g in ch["gone"]})
        if not gone:
            continue
        new = {r["new"].casefold(): r["new"] for r in ch["renamed"]}
        new.update({a["name"].casefold(): a["name"] for a in ch["added"]})
        loose_old = {r["old"].casefold() for r in ch["renamed"]}
        loose_new = {r["new"].casefold() for r in ch["renamed"]}
        prefix = _owner_prefix(ch)
        scope = [r for r in modules if prefix is None or r.casefold().startswith(prefix)]
        old_places, new_places = [], []
        for rel in scope:
            if rel not in texts:
                texts[rel] = fileio.read_text(ext.path(rel))
            for key, line, how in mentions(texts[rel], gone, loose_old):
                old_places.append({"path": ext.path(rel), "rel": rel, "line": line, "name": gone[key], "how": how})
            for key, line, how in mentions(texts[rel], new, loose_new):
                new_places.append({"path": ext.path(rel), "rel": rel, "line": line, "name": new[key], "how": how})
        risky, unproven = ext_templates(ext, tpls, ch, gone, sheets, [texts[r] for r in scope], new_places)
        base = {"ext": ext.name, "template": ch["title"], "rel": ch["rel"], "print": ch["print"],
                "renamed": ch["renamed"], "gone": ch["gone"], "added": ch["added"]}
        strong_new = [p for p in new_places if p["how"] != LOOSE]
        if any(p["how"] != LOOSE for p in old_places) or (old_places and not strong_new):
            out.append(dict(base, check="CODE_OLD_NAME", sev=RISK, places=old_places, templates=risky + unproven,
                            detail=_detail_code(ch, old_places)))
        if risky:
            out.append(dict(base, check="TEMPLATE_OLD_NAME", sev=RISK, places=new_places, templates=risky,
                            detail=_detail_tpl(ch, risky, new_places)))
        if unproven:
            out.append(dict(base, check="COPY_OLD_NAME", sev=INFO, places=new_places, templates=unproven,
                            detail=_detail_copy(unproven, new_places)))
    return out


# -- details ---------------------------------------------------------------------------------------------------------

def _names_change(ch):
    parts = []
    if ch["renamed"]:
        parts.append("переименованы: %s" % ", ".join("%s → %s (ячеек %d)" % (r["old"], r["new"], r["cells"])
                                                      for r in ch["renamed"]))
    if ch["gone"]:
        parts.append("убраны: %s" % ", ".join("%s (%d)" % (g["name"], g["cells"]) for g in ch["gone"]))
    if ch["added"]:
        parts.append("новые: %s" % ", ".join("%s (%d)" % (a["name"], a["cells"]) for a in ch["added"]))
    return "; ".join(parts)


def _detail_code(ch, places):
    names = sorted({p["name"] for p in places}, key=str.casefold)
    return ("код расширения заполняет по старому имени %s, а в макете вендора new его нет (%s); %s" % (
        ", ".join(names), _names_change(ch), SILENT))


def _detail_tpl(ch, tpls, new_places):
    parts = []
    for t in tpls:
        if t["borrowed"]:
            how = ("заимствованный, «Объединять»: в листе расширения чаще, чем в baseSheet — ячейку меняло расширение, "
                   "при слиянии остаётся его значение" if t.get("merge") else
                   "заимствованный, содержимое расширения используется (Template = Extended)")
        else:
            how = "копия; ячейка в области, которую код берёт из копии: %s" % ", ".join(t["areas_used"])
        parts.append("%s (%s) хранит %s" % (t["template"], how, ", ".join(t["names"])))
    why = ("код расширения пишет новое имя (%s)" % ", ".join(sorted({p["name"] for p in new_places}, key=str.casefold))
           if new_places else "его заполняет код вендора new новыми именами")
    return "макет расширения со старым именем: %s; %s; %s" % ("; ".join(parts), why, SILENT)


def _detail_copy(tpls, new_places):
    parts = []
    for t in tpls:
        used = ("код берёт из копии области %s, параметра в них нет" % ", ".join(t["areas_used"]) if t["areas_used"]
                else "по коду не видно, какие области берутся из копии")
        parts.append("%s хранит %s (%s)" % (t["template"], ", ".join(t["names"]), used))
    return ("копия макета в расширении со старым именем: %s; код пишет новое имя (%s) — проверить, читается ли область "
            "с этим параметром из копии" % ("; ".join(parts), ", ".join(sorted({p["name"] for p in new_places},
                                                                                key=str.casefold))))


def vendor_findings(changes):
    out = []
    for ch in changes:
        if not ch["print"] or not (ch["renamed"] or ch["gone"]):
            continue
        check = "VENDOR_RENAMED" if ch["renamed"] else "VENDOR_GONE"
        out.append({"ext": "", "template": ch["title"], "rel": ch["rel"], "print": True, "check": check,
                    "sev": RISK if ch["renamed"] else INFO, "renamed": ch["renamed"], "gone": ch["gone"],
                    "added": ch["added"], "places": [], "templates": [],
                    "detail": "%s; пользовательский макет со старыми именами — %s; %s" % (
                        _names_change(ch), SILENT, REGISTER_HINT)})
    return out


# -- snippet for the toolkit -----------------------------------------------------------------------------------------

def _like(s):
    return s.replace("~", "~~").replace("%", "~%").replace("_", "~_").replace("[", "~[").replace("]", "~]")


def snippet(findings):
    """Query text (1C query language) over РегистрСведений.ПользовательскиеМакетыПечати for the print templates of
    the findings, or "" without any. Объект and ИмяМакета as БСП writes them (УправлениеПечатью.
    ИспользуетсяПользовательскийМакет): «Документ.X» / «ОбщийМакет», the template name, matched with ПОДОБНО «Имя%»
    like БСП does (a language suffix _ru goes with it)."""
    seen = OrderedDict()
    for f in findings:
        if not f.get("print"):
            continue
        parts = f["template"].split(".")
        obj, name = ".".join(parts[:-1]), parts[-1]
        seen.setdefault((obj.casefold(), name.casefold()), (obj, name))
    if not seen:
        return ""
    conds = ["(ПользовательскиеМакетыПечати.Объект = \"%s\"\n\t\t\tИ ПользовательскиеМакетыПечати.ИмяМакета "
             "ПОДОБНО \"%s%%\" СПЕЦСИМВОЛ \"~\")" % (obj, _like(name)) for obj, name in seen.values()]
    return ("ВЫБРАТЬ\n"
            "\tПользовательскиеМакетыПечати.Объект КАК Объект,\n"
            "\tПользовательскиеМакетыПечати.ИмяМакета КАК ИмяМакета,\n"
            "\tПользовательскиеМакетыПечати.Использование КАК Использование\n"
            "ИЗ\n"
            "\tРегистрСведений.ПользовательскиеМакетыПечати КАК ПользовательскиеМакетыПечати\n"
            "ГДЕ\n\t" + "\n\tИЛИ ".join(conds) + "\n"
            "УПОРЯДОЧИТЬ ПО\n\tОбъект,\n\tИмяМакета")


# -- run -------------------------------------------------------------------------------------------------------------

def analyze(ext_roots, cfg_old, cfg_new, only=None, skip=None):
    exts = metadata.find_extensions(ext_roots, only, skip)
    changes, stats = vendor_diff(cfg_old, cfg_new)
    findings = []
    for ext in exts:
        findings.extend(ext_findings(ext, changes))
    findings.extend(vendor_findings(changes))
    findings.sort(key=lambda f: (ORDER.index(f["check"]), f["template"].casefold(), f["ext"].casefold()))
    stats["extensions"] = len(exts)
    for c in ORDER:
        stats[c.lower()] = sum(1 for f in findings if f["check"] == c)
    return exts, changes, findings, stats


def _where(p):
    return "%s:%d" % (p["rel"], p["line"]) if p.get("line") else p["rel"]


def render(args, changes, findings, stats, sql, elapsed):
    md = ["# Параметры макетов вендора: убраны или переименованы в новом релизе", ""]
    md.append("new: `%s`, old: `%s`. Расширений: %d. Табличных макетов в обоих релизах: %d, файл изменился: %d; "
              "с убранными именами: %d (печатных «ПФ_»: %d, с переименованием: %d), только новые имена: %d; пар "
              "переименования: %d; макетов, которых нет в new: %d; XML не разобран: %d. Находки: код расширения по "
              "старому имени — %d, макет расширения со старым именем — %d, печатный макет вендора с переименованием — "
              "%d (все три — РИСК), копия макета со старым именем вне используемых областей — %d, печатный макет "
              "вендора с убранными именами — %d (оба — ПРОВЕРИТЬ). Время: %.1f с." % (
                  args.config_new, args.config_old, stats["extensions"], stats["spreadsheets"],
                  stats["spreadsheets_changed"], stats["with_gone"], stats["print_with_gone"],
                  stats["print_with_renamed"], stats["added_only"], stats["renamed_pairs"],
                  stats["templates_removed"], stats["unreadable"],
                  stats["code_old_name"], stats["template_old_name"], stats["vendor_renamed"], stats["copy_old_name"],
                  stats["vendor_gone"], elapsed))
    md.append("")
    md.append("Область заполняется по имени параметра; имя, которого в области нет, платформа пропускает без ошибки, и "
              "ячейка печатается пустой (реальный случай: ПФ_MXL_РасходныйОрдерНаТовары, пользовательский макет с "
              "11.5.17 — пустая единица упаковки). Старые имена держат пользовательские макеты базы, копии и "
              "заимствованные макеты расширений, код расширений. Пары переименования — расстояние правки ≤ 2, равное "
              "число ячеек, единственный кандидат. Не видно: копии макета под другим объектом, имя параметра, "
              "собранное в коде из частей.")
    if not findings:
        md.extend(["", "Нет."])
    for f in findings:
        md.extend(["", "## %s — %s%s" % (f["template"], f["check"], " — " + f["ext"] if f["ext"] else ""), "",
                   "- " + f["detail"]])
        for p in f["places"]:
            md.append("- `%s` — %s (%s)" % (_where(p), p["name"], p["how"]))
        for t in f["templates"]:
            md.append("- макет `%s` — %s" % (t["rel"], ", ".join(t["names"])))
    if sql:
        md.extend(["", "## Пользовательские макеты в базе", "",
                   "Запрос для тулкита (`execute_query`), печатные макеты из находок; Объект и ИмяМакета — как "
                   "пишет БСП "
                   "(«Документ.X», «ОбщийМакет»), ИмяМакета ПОДОБНО «Имя%», как в "
                   "УправлениеПечатью.ИспользуетсяПользовательскийМакет:", "", "```", sql, "```"])
    return "\n".join(md)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Параметры табличных макетов вендора, которые новый релиз убрал или "
                                             "переименовал, и расширения, которые их заполняют или хранят")
    ap.add_argument("--ext-root", action="extend", nargs="+", required=True,
                    help="папка выгрузок расширений (или одна выгрузка); можно несколько")
    ap.add_argument("--ext", action="extend", nargs="+", help="только эти расширения (имя)")
    ap.add_argument("--skip", action="extend", nargs="+", help="пропустить эти расширения (имя)")
    ap.add_argument("--config-old", required=True, help="выгрузка типовой релиза, под который написаны расширения")
    ap.add_argument("--config-new", required=True, help="выгрузка типовой нового релиза")
    ap.add_argument("--out", required=True, help="папка отчёта template-params.json / .md, вне выгрузок")
    args = ap.parse_args(argv)
    for label, root in [("--ext-root", r) for r in args.ext_root] + [("--config-old", args.config_old),
                                                                     ("--config-new", args.config_new)]:
        if inside(args.out, root):
            sys.exit("--out %s внутри %s %s: template-params в выгрузки не пишет" % (args.out, label, root))
    t0 = time.time()
    metadata.check_config(args.config_new, "--config-new")
    metadata.check_config(args.config_old, "--config-old")
    exts, changes, findings, stats = analyze(args.ext_root, args.config_old, args.config_new, args.ext, args.skip)
    if not exts:
        sys.exit("no extension dumps found under --ext-root")
    elapsed = time.time() - t0
    sql = snippet(findings)
    data = {"config_new": args.config_new, "config_old": args.config_old, "extensions": [e.name for e in exts],
            "stats": dict(stats), "findings": findings, "changes": changes, "snippet": sql}
    md_path, json_path = fileio.write_report(args.out, "template-params",
                                             render(args, changes, findings, stats, sql, elapsed), data)
    print("extensions: %d, spreadsheets: %d (changed: %d, with gone names: %d, print: %d), renamed pairs: %d, "
          "findings: code %d, template %d, vendor renamed %d, copy %d, vendor gone %d, %.1fs" % (
              len(exts), stats["spreadsheets"], stats["spreadsheets_changed"], stats["with_gone"],
              stats["print_with_gone"], stats["renamed_pairs"], stats["code_old_name"], stats["template_old_name"],
              stats["vendor_renamed"], stats["copy_old_name"], stats["vendor_gone"], elapsed))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
