# -*- coding: utf-8 -*-
"""A selection over a finished check report: severity, filters, before/after delta and accepted findings.

    cfe-rebase select --report <calls.json | report folder> [--module S] [--object S] [--flag F ...]
                      [--severity error|risk|info] [--baseline <report of an earlier run>] [--accepted <file>]
                      [--out <dir>]

The same rules serve the MCP server (mcp_server: the check_* tools and list_interceptors filter, rank and compare
with them before the answer is cut to size) and this command, which reads a report already on disk - for example one
of the eight reports of `prepare` - instead of running the check again.

Reports: calls, queries, borrowed, reads, registers, names, interceptors (rows of `registry`), lint (rows of
`issues`).
Every finding gets
  level   error | risk | info - how bad it is for the port (LEVELS: per report below); the ranking of a cut answer
          puts error before risk before info, so the rows that matter survive a size limit;
  key     a stable key without line numbers: the same finding in a report of the extension before the port and in
          the work copy has the same key (KEYS below). Equal keys are matched one to one (a call repeated in one
          method is two findings with one key);
  delta   with a baseline (report of an earlier run - the extension before the port, the previous state of the work
          copy): new - not in the baseline, kept - in both, gone - only in the baseline (the row is the baseline's);
  accepted  with an accepted file: the reason, if the key is listed there; such a finding is not open.
Filters, all optional and combined with AND: module - substring of the place of the finding (module or file path from
the extension root, its title such as Документ.X.МодульОбъекта, the method), case-insensitive, alternatives split by
«|»; object - a metadata object as a whole name segment (Документ.ЗаказКлиента, ЗаказКлиента or
Documents/ЗаказКлиента), matched against the object the finding is in or about; flag - any of the flags, checks or
severities of the finding (calls: OWNER_REMOVED … SIGNATURE_CHANGED and risk / info of it; queries: sev, kind, part,
old; borrowed, reads, registers, lint: check; names: check, how, level; interceptors: flags); severity - the lowest
level to keep (error: errors only, risk: errors and risks, info: everything).
Extensions renamed between the baseline and the current run (a work copy is renamed «<name>_<release>_<date>_v_…»):
the MCP server pairs them by the uuid of Configuration when both sides are dumps; otherwise, when exactly one name of
each side has no pair, the two are one extension, and a current name that starts with exactly one unpaired baseline
name is that extension.
The accepted file is JSON: a list of {"key": …, "reason": …} or an object {key: reason}.
"""
import argparse
import io
import json
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict

from . import fileio

LEVELS = ("error", "risk", "info")
DELTAS = ("new", "kept", "gone")
# report name -> MCP tool
TOOLS = OrderedDict([("calls", "check_calls"), ("queries", "check_queries"), ("borrowed", "check_borrowed"),
                     ("reads", "check_reads"), ("registers", "check_registers"), ("interceptors", "list_interceptors"),
                     ("lint", "check_work"), ("names", "check_names")])
ROWS = {"interceptors": "registry", "lint": "issues"}  # the list of findings in <report>.json; the others: findings

CALL_ERRORS = ("OWNER_REMOVED", "OWNER_RENAMED", "REMOVED", "TOO_FEW_ARGS", "TOO_MANY_ARGS", "MISSING",
               "NOT_EXPORT")  # in this order of rank
BORROWED_ORDER = ("COMPAT_MODE", "OBJECT_REMOVED", "OBJECT_RENAMED", "REMOVED", "RENAMED", "TYPE_CHANGED",
                  "PROPERTY_CHANGED", "LIST_FIELD_LOST", "NAME_CLASH", "XML_UNREADABLE", "LIST_CHANGED", "NOT_IN_VENDOR")
BORROWED_LEVEL = {"XML_UNREADABLE": "risk", "LIST_CHANGED": "risk", "NOT_IN_VENDOR": "info"}  # the rest: error
# interceptors: the flags the adaptation plan calls MUST_FIX, CLARIFY and hides (plan.INT_MUST / INT_CLARIFY)
INTERCEPTOR_ORDER = ("MISSING_BASE", "TARGET_RENAMED", "TARGET_UNCALLED", "SIGNATURE_MISMATCH", "SIGNATURE_CHANGED",
                     "CONTROL_MISMATCH", "CALLERS_LOST", "CONFLICT", "OVERRIDDEN", "OVERRIDDEN?", "BINDING_CHANGED",
                     "NEW_BASE", "TARGET_DEPRECATED", "DIRECTIVE_MISMATCH", "DEAD_BINDING")
INTERCEPTOR_LEVEL = dict({f: "error" for f in INTERCEPTOR_ORDER[:6]}, **{f: "risk" for f in INTERCEPTOR_ORDER[6:12]})


def _interceptor_level(row):
    """TARGET_DEPRECATED: risk when the method became obsolete only in new (deprecated.grade), info otherwise."""
    levels = [INTERCEPTOR_LEVEL.get(f, "info") for f in _l(row.get("flags"))]
    if "TARGET_DEPRECATED" in _l(row.get("flags")) and (row.get("deprecated") or {}).get("grade") == "risk":
        levels.append("risk")
    return min(levels, key=LEVELS.index, default="info")
QUERY_LEVEL = {"ОШИБКА?": "error", "РИСК": "risk"}
READ_RISK = ("dims_added", "dims_removed", "res_removed", "attrs_removed", "props_changed")


class SelectionError(Exception):
    """A report, baseline or accepted file that cannot be used."""


def _l(x):
    return list(x or [])


def _risk_call(row):
    return (row.get("note") or "").startswith("[риск]")


def _title(row):
    """Title of the file of a queries finding (the plan's titles: a module, a form, a template)."""
    from . import plan
    return plan.file_title(row.get("file")) or ""


def _calls_level(row):
    flags = set(_l(row.get("flags")))
    if flags - {"SIGNATURE_CHANGED", "CALLS_DEPRECATED"}:
        return "error"
    return "risk" if flags and _risk_call(row) else "info"


def _reads_level(row):
    if row.get("check") == "REGISTER_REMOVED":
        return "error"
    if row.get("check") == "XML_UNREADABLE" or any(row.get(k) for k in READ_RISK):
        return "risk"
    return "info"


def _registers_level(row):
    if row.get("check") == "REGISTER_REMOVED" or row.get("stale") or row.get("key_lost"):
        return "error"
    return "risk"


def _first(order, values):
    return min((order.index(v) for v in values if v in order), default=len(order))


# report -> (key fields, level, finer rank, place fields for module, object fields for object, flag tokens)
SPECS = {
    "calls": (("ext", "module", "caller", "call", "flags"), _calls_level,
              lambda r: _first(CALL_ERRORS, _l(r.get("flags"))),
              lambda r: [r.get("module"), r.get("module_title"), r.get("caller")],
              lambda r: [r.get("module_title"), r.get("module"), r.get("target")],
              lambda r: _l(r.get("flags")) + (["risk" if _risk_call(r) else "info"]
                                              if {"SIGNATURE_CHANGED", "CALLS_DEPRECATED"} & set(_l(r.get("flags")))
                                              else [])),
    "queries": (("ext", "file", "where", "part", "kind", "ref"),
                lambda r: QUERY_LEVEL.get(r.get("sev"), "info"),
                lambda r: 0 if r.get("old") == "было корректно" else 1,
                lambda r: [r.get("file"), _title(r), r.get("where")],
                lambda r: [_title(r), r.get("file"), r.get("ref")],
                lambda r: [r.get("sev"), r.get("kind"), r.get("part"), r.get("old")]),
    "borrowed": (("ext", "object", "part", "name", "check"),
                 lambda r: BORROWED_LEVEL.get(r.get("check"), "error"),
                 lambda r: _first(BORROWED_ORDER, [r.get("check")]),
                 lambda r: [r.get("file")] + _l(r.get("forms")),
                 lambda r: [r.get("object"), r.get("file")],
                 lambda r: [r.get("check")]),
    "reads": (("ext", "register", "check"), _reads_level,
              lambda r: 0,
              lambda r: [x for read in _l(r.get("reads")) for x in (read.get("path"), read.get("where"))],
              lambda r: [r.get("register")],
              lambda r: [r.get("check")]),
    "registers": (("ext", "module", "method", "register", "check"), _registers_level,
                  lambda r: 0,
                  lambda r: [r.get("module"), r.get("module_title"), r.get("method")],
                  lambda r: [r.get("register"), r.get("module_title"), r.get("module")],
                  lambda r: [r.get("check")]),
    "interceptors": (("ext", "module", "kind", "target", "ext_method", "flags"),
                     _interceptor_level,
                     lambda r: _first(INTERCEPTOR_ORDER, _l(r.get("flags"))),
                     lambda r: [r.get("module"), r.get("module_title"), r.get("ext_method"), r.get("form")],
                     lambda r: [r.get("module_title"), r.get("module"), r.get("target")],
                     lambda r: _l(r.get("flags"))),
    "names": (("ext", "module", "how", "name", "check"),
              lambda r: r.get("level") if r.get("level") in LEVELS else "info",
              lambda r: 0 if r.get("check") in ("GONE_IN_NEW", "LIST_QUERY_FIELD_MISSING", "FIELD_NOT_ADOPTED") else 1,
              lambda r: [r.get("module"), r.get("form")] + _l(r.get("methods")),
              lambda r: [r.get("form"), r.get("module")],
              lambda r: [r.get("check"), r.get("how"), r.get("level")]),
    "lint": (("ext", "file", "check"),
             lambda r: "error" if r.get("severity") == "error" else "info",
             lambda r: 0,
             lambda r: [r.get("file"), r.get("path")],
             lambda r: [r.get("file")],
             lambda r: [r.get("check"), r.get("severity")]),
}

KEYS = {name: "|".join(spec[0]) for name, spec in SPECS.items()}  # documented form of the key of every report


def key(report, row, ext=None):
    """The stable key of a finding: its KEYS fields joined by «|», lists (flags) sorted and joined by «,»; ext - the
    extension name to use instead of the row's (a renamed extension of a baseline)."""
    parts = []
    for f in SPECS[report][0]:
        v = ext if f == "ext" and ext is not None else row.get(f)
        parts.append(",".join(sorted(str(x) for x in v)) if isinstance(v, list) else ("" if v is None else str(v)))
    return "|".join(parts)


def level(report, row):
    return SPECS[report][1](row)


def rank(report, row):
    """Sort key of a finding, smaller first: level, then the finer order of the report (calls: REMOVED and the
    argument count before MISSING; queries: «было корректно» first; borrowed and interceptors: by check / flag)."""
    return LEVELS.index(level(report, row)), SPECS[report][2](row)


def _alternatives(text):
    return [a.strip().casefold() for a in (text or "").split("|") if a.strip()]


def _segment(name):
    return re.compile(r"(?:^|[./\\])%s(?:$|[./\\])" % re.escape(name))


def _norm(values):
    return [str(v).replace("\\", "/").casefold() for v in values if v]


class Filter(object):
    """module / object / flag / severity of the module docstring; empty arguments select everything."""

    def __init__(self, module=None, object=None, flag=None, severity=None):  # noqa: A002 - the name of the parameter
        if severity and severity not in LEVELS:
            raise SelectionError("severity: одно из %s, не %r" % (", ".join(LEVELS), severity))
        self.module = [a.replace("\\", "/") for a in _alternatives(module)]
        self.object = [_segment(a.replace("\\", "/")) for a in _alternatives(object)]
        self.flag = {str(f).casefold() for f in (flag or []) if str(f).strip()}
        self.severity = severity
        self.given = OrderedDict((k, v) for k, v in (("module", module), ("object", object),
                                                     ("flag", list(flag) if flag else None), ("severity", severity))
                                 if v)

    def __bool__(self):
        return bool(self.given)

    def place(self, report, row):
        """For reads: the row with only the read places the module filter selects (the row itself without it)."""
        if report != "reads" or not self.module:
            return row
        reads = [x for x in _l(row.get("reads")) if self._module_hit(_norm((x.get("path"), x.get("where"))))]
        return dict(row, reads=reads)

    def _module_hit(self, values):
        return any(a in v for a in self.module for v in values)

    def match(self, report, row):
        _, lvl, _, place, objects, tokens = SPECS[report]
        if self.module and not self._module_hit(_norm(place(row))):
            return False
        if self.object and not any(rx.search(v) for rx in self.object for v in _norm(objects(row))):
            return False
        if self.flag and not self.flag & {str(t).casefold() for t in tokens(row) if t}:
            return False
        if self.severity and LEVELS.index(lvl(row)) > LEVELS.index(self.severity):
            return False
        return True


def rows_of(report, data):
    """The findings of a report; calls - only those with flags, as check_calls answers."""
    rows = list(data.get(ROWS.get(report, "findings")) or [])
    return [r for r in rows if r.get("flags")] if report == "calls" else rows


def report_name(path):
    """calls.json -> calls; a folder -> the one known report in it; anything else -> SelectionError."""
    if os.path.isdir(path):
        found = [n for n in SPECS if os.path.isfile(os.path.join(path, n + ".json"))]
        if len(found) != 1:
            raise SelectionError("%s: в папке нужен ровно один отчёт из %s, найдено: %s"
                                 % (path, ", ".join(n + ".json" for n in SPECS), ", ".join(found) or "ни одного"))
        return found[0]
    name = os.path.splitext(os.path.basename(path))[0]
    if name not in SPECS:
        raise SelectionError("%s: не отчёт проверки (%s)" % (path, ", ".join(n + ".json" for n in SPECS)))
    return name


def load_report(path, expect=None):
    """(report name, data) of <report>.json or of a folder holding it; expect - the name it must have. With expect, a
    folder that holds <expect>/<expect>.json - the report folder of prepare, one subfolder per tool - gives that
    subfolder's report."""
    if not os.path.exists(path):
        raise SelectionError("%s: нет такого файла или папки" % path)
    if (expect and os.path.isdir(path) and not os.path.isfile(os.path.join(path, expect + ".json"))
            and os.path.isfile(os.path.join(path, expect, expect + ".json"))):
        path = os.path.join(path, expect)
    if expect and os.path.isdir(path) and os.path.isfile(os.path.join(path, expect + ".json")):
        name = expect
    else:
        name = report_name(path)
    if expect and name != expect:
        raise SelectionError("%s: это отчёт %s, а нужен %s" % (path, name, expect))
    file = os.path.join(path, name + ".json") if os.path.isdir(path) else path
    try:
        with io.open(file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SelectionError("%s: не читается как JSON отчёта: %s" % (file, exc))
    if not isinstance(data, dict) or not isinstance(data.get(ROWS.get(name, "findings")), list):
        raise SelectionError("%s: в отчёте нет списка %s" % (file, ROWS.get(name, "findings")))
    return name, data


def load_accepted(path):
    """{key: reason} from a JSON list of {key, reason} or an object {key: reason}."""
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SelectionError("accepted %s: не читается как JSON: %s" % (path, exc))
    if isinstance(data, dict):
        return {str(k): str(v or "") for k, v in data.items()}
    if isinstance(data, list) and all(isinstance(x, dict) and x.get("key") for x in data):
        return {str(x["key"]): str(x.get("reason") or "") for x in data}
    raise SelectionError("accepted %s: нужен список {\"key\": …, \"reason\": …} или объект {ключ: причина}" % path)


def ext_names(data):
    """Extension names of a report: lint keeps them only in its rows."""
    names = data.get("extensions")
    if isinstance(names, list):
        return [e if isinstance(e, str) else e.get("name") for e in names]
    return sorted({r.get("ext") for r in rows_of("lint", data) if r.get("ext")})


def ext_map(base_names, cur_names):
    """{baseline name: current name} for renamed extensions (a work copy is renamed «<name>_<release>_<date>_v_…»):
    exactly one name without a pair on each side, else the unpaired current names that start with exactly one
    unpaired baseline name and are its only such name."""
    b, c = set(base_names or []) - set(cur_names or []), set(cur_names or []) - set(base_names or [])
    if len(b) == 1 and len(c) == 1:
        return {b.pop(): c.pop()}
    pairs = {x: [y for y in c if y.casefold().startswith(x.casefold())] for x in b}
    taken = Counter(y for ys in pairs.values() for y in ys)
    return {x: ys[0] for x, ys in pairs.items() if len(ys) == 1 and taken[ys[0]] == 1}


def select(report, rows, flt=None, baseline=None, accepted=None, base_exts=None, cur_exts=None, rename=None):
    """Entries of the selection, ranked (open new, open kept, accepted, gone; then rank()):
    {row, key, level, delta (with baseline), accepted (reason, with accepted), baseline (True: a gone row of the
    baseline), order (position in the report: the current rows, then the gone ones)}, and the summary {filter,
    matched, levels, delta, baseline_findings, renamed_ext, accepted, open, accepted_unused}.
    rows / baseline - finding rows of the report (baseline None: no comparison); accepted - {key: reason} or None;
    rename - {baseline extension name: current name} when known (by Configuration uuid), else ext_map of the names."""
    flt = flt or Filter()
    if baseline is None:
        rename = {}
    elif rename is None:
        rename = ext_map(base_exts, cur_exts)
    entries = [{"row": r, "key": key(report, r), "baseline": False, "order": i} for i, r in enumerate(rows)]
    if baseline is not None:
        pool = defaultdict(list)
        for b in baseline:
            pool[key(report, b, rename.get(b.get("ext")))].append(b)
        for e in entries:
            if pool.get(e["key"]):
                pool[e["key"]].pop(0)
                e["delta"] = "kept"
            else:
                e["delta"] = "new"
        for k, left in pool.items():
            entries.extend({"row": b, "key": k, "baseline": True, "delta": "gone"} for b in left)
        for i, e in enumerate(entries):
            e["order"] = i
    used = set()
    picked = []
    for e in entries:
        if not flt.match(report, e["row"]):
            continue
        e["row"] = flt.place(report, e["row"])
        e["level"] = level(report, e["row"])
        if accepted is not None and e["key"] in accepted:
            used.add(e["key"])
            if e.get("delta") != "gone":
                e["accepted"] = accepted[e["key"]]
        picked.append(e)
    # open new, open kept (or no baseline), accepted, gone - then the severity of the finding
    group = {"new": 0, "kept": 1, None: 1, "gone": 3}
    picked.sort(key=lambda e: (2 if "accepted" in e else group[e.get("delta")],) + rank(report, e["row"]))
    summary = OrderedDict()
    if flt:
        summary["filter"] = dict(flt.given)
    summary["matched"] = len(picked)
    summary["levels"] = {lv: sum(1 for e in picked if e["level"] == lv) for lv in LEVELS}
    if baseline is not None:
        summary["delta"] = {d: sum(1 for e in picked if e.get("delta") == d) for d in DELTAS}
        summary["baseline_findings"] = len(baseline)
        if rename:
            summary["renamed_ext"] = rename
    if accepted is not None:
        summary["accepted"] = sum(1 for e in picked if "accepted" in e)
        summary["open"] = sum(1 for e in picked if "accepted" not in e and e.get("delta") != "gone")
        summary["accepted_unused"] = len(set(accepted) - used)
    return picked, summary


def document(report, entries, summary, inputs):
    """selection.json: the whole selection, nothing cut."""
    return {"report": report, "inputs": inputs, "summary": summary, "key_fields": list(SPECS[report][0]),
            "findings": [_entry_out(e) for e in entries]}


def _entry_out(e):
    out = OrderedDict([("key", e["key"]), ("level", e["level"])])
    for k in ("delta", "accepted"):
        if k in e:
            out[k] = e[k]
    out["finding"] = e["row"]
    return out


def render(report, entries, summary, inputs):
    md = ["# Выборка из отчёта %s" % report, ""]
    md.append("Отчёт: `%s`." % inputs.get("report"))
    for k in ("baseline", "accepted"):
        if inputs.get(k):
            md.append("%s: `%s`." % ("Сравнение с" if k == "baseline" else "Принятые", inputs[k]))
    md.append("")
    md.append("Итог: " + json.dumps(summary, ensure_ascii=False))
    md.append("")
    md.append("Ключ находки: поля %s через «|»." % ", ".join(SPECS[report][0]))
    md.append("")
    md.append(fileio.md_table(["Уровень", "Дельта", "Принято", "Ключ"],
                              [(e["level"], e.get("delta", "—"), e.get("accepted", "—"), e["key"]) for e in entries]))
    return "\n".join(md)


def write(out, report, entries, summary, inputs):
    """<out>/selection.json and selection.md; returns (md, json) paths."""
    return fileio.write_report(out, "selection", render(report, entries, summary, inputs),
                               document(report, entries, summary, inputs))


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Выборка из готового отчёта проверки: фильтр по модулю, объекту, флагу и "
                                             "уровню, сравнение с отчётом прошлого прогона (new / kept / gone), "
                                             "принятые находки")
    ap.add_argument("--report", required=True, help="<отчёт>.json (%s) или папка с ним" % ", ".join(SPECS))
    ap.add_argument("--module", help="подстрока места находки (путь модуля или файла, его имя, метод); «|» — или")
    ap.add_argument("--object", help="объект метаданных целым сегментом имени (Документ.X, X, Documents/X); «|» — или")
    ap.add_argument("--flag", action="extend", nargs="+", help="флаг, проверка или sev находки (любой из)")
    ap.add_argument("--severity", choices=LEVELS, help="нижний уровень: error — только ошибки, risk — и риски")
    ap.add_argument("--baseline", help="отчёт того же вида прошлого прогона (файл или папка) — дельта new/kept/gone")
    ap.add_argument("--accepted", help="JSON принятых находок: [{\"key\": …, \"reason\": …}] или {ключ: причина}")
    ap.add_argument("--out", help="папка для selection.json / selection.md; без неё — только экран")
    args = ap.parse_args(argv)
    try:
        report, data = load_report(args.report)
        base = load_report(args.baseline, report)[1] if args.baseline else None
        accepted = load_accepted(args.accepted) if args.accepted else None
        flt = Filter(args.module, args.object, args.flag, args.severity)
    except SelectionError as exc:
        sys.exit(str(exc))
    rows = rows_of(report, data)
    entries, summary = select(report, rows, flt, rows_of(report, base) if base else None, accepted,
                              ext_names(base) if base else None, ext_names(data))
    inputs = {"report": args.report, "baseline": args.baseline, "accepted": args.accepted}
    print("%s: находок %d, выбрано %d; %s" % (report, len(rows), summary["matched"],
                                              json.dumps(summary, ensure_ascii=False)))
    for e in entries:
        print("\t".join([e["level"], e.get("delta", "-"), "accepted" if "accepted" in e else "open", e["key"]]))
    if args.out:
        for path in write(args.out, report, entries, summary, inputs):
            print(path)
    return 0


if __name__ == "__main__":
    main()
