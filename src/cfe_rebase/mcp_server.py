# -*- coding: utf-8 -*-
"""MCP server (stdio) over the cfe-rebase tools: short structured answers with paths to the full reports.

    cfe-rebase mcp

Every tool is a thin wrapper: build argv, run <module>.main(argv) into <out>/<tool>-<run_id[:8]>/ with stdout and
stderr captured, read the JSON report back and keep only the fields an agent needs to decide; bodies and hunks stay
in files and are referenced by path and line range. The tool functions are plain functions (testable without the SDK);
build_server() registers them with the MCP SDK, which is an optional dependency: pip install "cfe-rebase[mcp]".
"""
import argparse
import contextlib
import glob
import importlib
import importlib.util
import inspect
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

from . import __version__, control_rebase, fileio, metadata, prepare as prep, registry as db_registry, structure

SERVER_NAME = "cfe-rebase"
INSTALL_HINT = ("cfe-rebase mcp: не установлен MCP SDK (пакет mcp). Установите extra:\n"
                "    python -m pip install \"cfe-rebase[mcp]\"\n"
                "или из клона репозитория:\n"
                "    python -m pip install -e \"<путь к клону>[mcp]\"")
INSTRUCTIONS = (
    "Перенос расширений 1С (CFE) на новый релиз типовой по XML-выгрузкам. Все пути — абсолютные. out — папка для "
    "отчётов вне выгрузок: каждый вызов пишет в свою подпапку <out>/<инструмент>-<run_id[:8]>/. Ответ — конверт: "
    "status, summary, items (по одному объекту на метод или находку), report.json/report.md — пути к полным отчётам. "
    "items и dead_code ограничены по размеру: если в ответе есть truncated (total / returned), первыми оставлены "
    "строки с флагами, fail и находки «было корректно», полный список — в report; summary считает всё. Тела методов и ханки в ответ не входят: их читать по путям и диапазонам строк (номера строк — как в файле). "
    "Пишут: инструменты с out — отчёты в свою подпапку out; apply_candidate — модуль рабочей копии, только "
    "отслеживаемый git (отчёта не пишет); applied_text — "
    "копию модулей в dst вне выгрузок; prepare — запускает команду выгрузки расширений в ext (dump=True по умолчанию; "
    "если ext — эталонная выгрузка, передавай dump=False) и создаёт рабочую копию, если её нет. Реестр баз: вместо "
    "путей можно передать db (id или alias базы) и to (релиз, на который переносим) — ext, old, new, work и out "
    "возьмутся из реестра (файл registry, иначе $CFE_REBASE_REGISTRY, иначе ~/.cfe-rebase/registry.json), явные пути "
    "перекрывают; from_release — релиз расширений, по умолчанию из реестра. prepare — вся подготовка базы одним "
    "вызовом. Порядок работы: prepare (items — что исправить, весь план — report.plan.md) → по пунктам плана "
    "method_diff, для &ИзменениеИКонтроль — rebase_control + apply_candidate, для копий &Вместо — port_replace → "
    "перед загрузкой проверить рабочую копию: check_control (рабочая копия как ext, каждый метод pass), "
    "check_calls, check_queries, check_borrowed, check_registers (рабочая копия как ext_root), check_work "
    "(errors = 0); check_reads (рабочая копия как ext_root) — информационно: регистры вендора, которые запросы "
    "расширений читают и у которых изменились состав или регистраторы, смотреть глазами. Не видят: копии СКД и "
    "макетов вендора под другим именем, запись в регистр через ЗаполнитьЗначенияСвойств или структуру, чтение "
    "регистра через менеджер (РегистрыНакопления.X.Остатки()) — их сверять по тексту.")

# One tool call at a time: redirect_stdout swaps the process-wide sys.stdout, and the SDK runs sync tools on worker
# threads, so two overlapping captures would restore each other's stream. The tools are CPU-bound anyway.
_LOCK = threading.Lock()
CONTROL_PASS = control_rebase.PASS_VERDICTS  # and the signature fits: control_rebase.ready
INTERCEPTOR_FIELDS = ("id", "ext", "module_title", "source", "kind", "target", "target_old", "ext_method", "proceed",
                      "flags", "notes", "ext_directive", "base_directive", "ext_sig", "base_sig_old", "base_sig_new",
                      "ann_line", "ext_line", "base_line_old", "base_line_new", "callType", "owner_kind", "owner",
                      "event", "own_element", "xml_line")
CALL_FIELDS = ("ext", "module_title", "line", "caller", "call", "args", "target", "flags", "note", "copy", "advice")
QUERY_FIELDS = ("part", "kind", "sev", "ext", "where", "line", "ref", "note", "old")
REGISTER_FIELDS = ("ext", "module_title", "line", "method", "register", "how", "check", "added", "missing", "stale",
                   "to_dims", "to_res", "key_lost", "callers", "detail")
READ_FIELDS = ("ext", "register", "check", "sev", "dims_added", "dims_removed", "res_added", "res_removed",
               "attrs_added", "attrs_removed", "type_changed", "props_changed", "recorders_added",
               "recorders_removed", "reads", "detail")
APPLIED_LINE = re.compile(r"^(?P<rel>.+): опустошено строк (?P<n>\d+)$")
# Serialized size of a list in one answer, characters. A trial run (16.10.2026, 2 extensions of a real base) got 43k characters
# from list_interceptors (~15k tokens, over the 10k warning of Claude Code) in five steps; 20 extensions would pass
# the 50k characters at which Claude Code moves a tool result into a file.
LIST_MAX_CHARS = {"items": 20000, "dead_code": 5000}
# input of a tool -> key of the registry of databases (registry.resolve)
DUMP_KEYS = {"ext_root": "ext", "config_old": "old", "config_new": "new", "out": "out"}
CONTROL_KEYS = {"ext": "ext", "old": "old", "new": "new", "out": "out"}


class BadInput(Exception):
    """Refused input: a tool's sys.exit text or a check of this server."""


class External(Exception):
    """An external program or the file system failed (prepare: the dump command, git, the copy of the work copy)."""


class _Run(object):
    """One tool call: id, start time, console buffer, the per-call output folder."""

    def __init__(self, tool):
        self.tool = tool
        self.id = str(uuid.uuid4())
        self.started = datetime.now().astimezone()
        self.t0 = time.monotonic()
        self.console = io.StringIO()

    def folder(self, out):
        return os.path.join(os.path.abspath(out), "%s-%s" % (self.tool, self.id[:8]))

    def call(self, module, argv):
        """cfe_rebase.<module>.main(argv); its console output is returned, its sys.exit(text) becomes BadInput."""
        mod = importlib.import_module("." + module, __package__)
        mark = len(self.console.getvalue())
        try:
            mod.main(list(argv))
        except SystemExit as exc:
            if exc.code not in (None, 0):
                if isinstance(exc.code, str):
                    raise BadInput(exc.code)
                tail = self.console.getvalue()[mark:].strip()  # argparse: usage + error line on stderr
                raise BadInput(tail.splitlines()[-1] if tail else "%s exited with code %s" % (module, exc.code))
        return self.console.getvalue()[mark:]


def _execute(tool, inputs, paths, body, fill=None):
    """The response envelope around body(run) -> {summary, items, report, dead_code, unjudged, ext_newest}.

    paths: {argument: path or [paths]} that must be absolute and exist, or a function returning it (called after
    fill). fill() -> extra envelope fields: completes inputs in place (paths from the registry of databases)."""
    run = _Run(tool)
    status, error, payload = "ok", None, {}
    try:
        with _LOCK, contextlib.redirect_stdout(run.console), contextlib.redirect_stderr(run.console):
            extra = fill() if fill else {}
            _check_paths(paths() if callable(paths) else paths)
            payload = dict(extra, **body(run))
    except BadInput as exc:
        status, error = "error", {"code": "bad_input", "message": str(exc)}
    except External as exc:
        status, error = "error", {"code": "external", "message": str(exc)}
    except SystemExit as exc:  # a tool exiting outside _Run.call
        status, error = "error", {"code": "bad_input", "message": str(exc.code)}
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never a traceback on the protocol channel
        status, error = "error", {"code": "internal", "message": "%s: %s" % (type(exc).__name__, exc)}
    env = {"tool": tool, "version": __version__, "run_id": run.id,
           "started_at": run.started.isoformat(timespec="seconds"),
           "elapsed_s": round(time.monotonic() - run.t0, 3), "status": status}
    if error:
        env["error"] = error
        console = run.console.getvalue().strip()
        if console:
            env["console"] = console
    env["inputs"] = inputs
    for key in ("ext_newest", "summary", "items", "report", "dead_code", "unjudged", "unreadable_forms"):
        if payload.get(key) not in (None, "", [], {}):
            env[key] = payload[key]
    cut = {}
    for key, budget in LIST_MAX_CHARS.items():
        if isinstance(env.get(key), list):
            total = len(env[key])
            env[key] = _cap(env[key], budget)
            if len(env[key]) < total:
                cut[key] = {"total": total, "returned": len(env[key])}
    if cut:
        cut["rest"] = "полный список - в report; первыми оставлены строки с флагами, fail и находки «было корректно»"
        env["truncated"] = cut
    return env


def _important(row):
    return bool(row.get("flags")) or row.get("result") == "fail" or row.get("old") == "было корректно"


def _cap(rows, budget):
    """The rows that fit into budget characters of JSON, in their own order; important rows are taken first."""
    if len(json.dumps(rows, ensure_ascii=False)) <= budget:
        return rows
    kept, size = set(), 2
    for i in sorted(range(len(rows)), key=lambda i: not _important(rows[i])):
        size += len(json.dumps(rows[i], ensure_ascii=False)) + 2
        if size > budget:
            break
        kept.add(i)
    return [rows[i] for i in sorted(kept)]


def _check_paths(paths):
    for name, value in paths.items():
        for p in ([value] if isinstance(value, str) else list(value or [])):
            if not os.path.isabs(p):
                raise BadInput("%s: path must be absolute: %s" % (name, p))
            if not os.path.exists(p):
                raise BadInput("%s: path does not exist: %s" % (name, p))


def _inside(path, root):
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def _refuse_inside(dest, roots):
    """For the tools that do not check it themselves: never write into a dump."""
    for root in roots:
        if root and _inside(dest, root):
            raise BadInput("out %s is inside the dump %s: cfe-rebase never writes into dumps" % (dest, root))


def _require_out(out):
    if not out or not os.path.isabs(out):
        raise BadInput("out: path must be absolute: %s" % out)


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _report(folder, name):
    return {"json": os.path.join(folder, name + ".json"), "md": os.path.join(folder, name + ".md")}


def _compact(d, keep=("flags",)):
    """Drop None, empty strings and empty containers; booleans and zeros stay, so do the keys in keep."""
    return {k: v for k, v in d.items() if k in keep or v not in (None, "", [], {})}


def _span(text):
    """"48-62" -> [48, 62]; None stays None."""
    if not text:
        return None
    a, _, b = str(text).partition("-")
    return [int(a), int(b or a)]


def _join(root, rel):
    return os.path.normpath(os.path.join(root, *rel.split("/"))) if root and rel else None


def _ext_roots(roots, only=None, skip=None):
    """{extension name: dump root} as the tools find them (Configuration.xml heads only)."""
    return {d.name: d.root for d in metadata.find_extensions(roots, only, skip)}


def _take(inputs, *names):
    return [inputs[n] for n in names]


def _take_dict(inputs, *names):
    return {n: inputs[n] for n in names}


def _flag_counts(rows, key="flags"):
    c = Counter(f for r in rows for f in set(r.get(key) or []))
    return dict(sorted(c.items()))


def _from_registry(inputs, keys, required, relative=None):
    """fill() for _execute: the path inputs a call lacks, from the registry of databases.

    inputs carry db, to, from_release and registry; keys: {input: registry key (ext, old, new, out, work)} - filled
    when empty (ext_root gets a one-element list); relative: {input: registry key} - a relative path is joined to
    that folder; required: inputs that must be set after filling. Returns {"ext_newest": ...} when db is given."""
    relative = relative or {}

    def fill():
        db = inputs.get("db")
        if not db:
            given = [k for k in ("to", "from_release", "registry") if inputs.get(k)]
            if given:
                raise BadInput("%s: только вместе с db" % ", ".join(given))
            missing = [n for n in required if not inputs.get(n)]
            if missing:
                raise BadInput("%s: нужен путь или db (база из реестра)" % ", ".join(missing))
            return {}
        rel = {n: k for n, k in relative.items() if inputs.get(n) and not os.path.isabs(inputs[n])}
        wanted = [k for n, k in keys.items() if not inputs.get(n)] + list(rel.values())
        if "new" in wanted and not inputs.get("to"):
            raise BadInput("to: с db нужен релиз, на который переносим (ключ cfe_rebase.releases реестра)")
        try:  # the old release only when a path is taken from it: check_work needs none
            r = db_registry.resolve(inputs.get("registry"), db, inputs.get("to"), inputs.get("from_release"),
                                    need_old="old" in wanted)
        except db_registry.RegistryError as exc:
            raise BadInput(str(exc))
        for name, key in keys.items():
            if not inputs.get(name) and r[key]:
                inputs[name] = [r[key]] if name == "ext_root" else r[key]
        for name, key in rel.items():
            inputs[name] = os.path.normpath(os.path.join(r[key], inputs[name]))
        missing = [n for n in required if not inputs.get(n)]
        if missing:
            raise BadInput("%s: в реестре для базы %s пути нет" % (", ".join(missing), r["db_id"]))
        ext = inputs.get("ext_root") or inputs.get("ext")
        return {"ext_newest": db_registry.ext_newest(ext)} if ext else {}

    return fill


# ------------------------------------------------------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------------------------------------------------------

def list_interceptors(ext_root: list[str] | None = None, config_old: str | None = None, config_new: str | None = None,
                      out: str | None = None, skip: list[str] = [], order: str | None = None,
                      only_flagged: bool = False, db: str | None = None, to: str | None = None,
                      from_release: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Реестр перехватов расширений против нового релиза (`interceptors`) и мёртвый код — без тел и диффов.

    ext_root — папки с выгрузками расширений (или одна выгрузка); config_old / config_new — выгрузки типовой старого
    и нового релиза; skip — имена расширений, которые пропустить; order — текстовый файл с порядком подключения
    расширений (первое — нижний слой), без него победитель конфликта помечается OVERRIDDEN?; only_flagged — вернуть
    только строки с флагами.
    db / to / from_release / registry — пути из реестра баз вместо ext_root / config_old / config_new / out (явные
    перекрывают); в inputs ответа — разрешённые пути, ext_newest — время самого нового файла выгрузки расширений.

    items — по строке на перехват: id, ext, module_title, path (модуль расширения), source (annotation | callType),
    kind, target, ext_method, proceed (есть ПродолжитьВызов), flags (CONFLICT, OVERRIDDEN, OVERRIDDEN?,
    DIRECTIVE_MISMATCH, MISSING_BASE, NEW_BASE, SIGNATURE_CHANGED, SIGNATURE_MISMATCH, CONTROL_MISMATCH,
    BINDING_CHANGED, DEAD_BINDING), notes, директивы, сигнатуры (base_sig_old — только если отличается от new),
    номера строк: ann_line / ext_line — в модуле расширения, base_line_old / base_line_new — в модулях вендора;
    у callType — form (Form.xml), xml_line, owner, event. dead_code — отдельный список (category UNBOUND,
    COMMENTED_ANNOTATION, DEAD_BINDING_OWN_FORMS). summary — счёты флагов по всем строкам, даже при only_flagged.
    unreadable_forms — Form.xml, которые есть, но не разбираются (dump, form, error): их перехватов и привязок в items
    нет, это не «формы нет»; есть только при непустом списке, тогда же summary.unreadable_forms.
    Что изменил вендор в теле метода — method_diff; полный реестр с группами целей — report.md."""
    inputs = {"ext_root": ext_root, "config_old": config_old, "config_new": config_new, "out": out, "skip": skip,
              "order": order, "only_flagged": only_flagged, "db": db, "to": to, "from_release": from_release,
              "registry": registry}

    def body(run):
        ext_root, config_old, config_new, out = _take(inputs, "ext_root", "config_old", "config_new", "out")
        _require_out(out)
        dest = run.folder(out)
        _refuse_inside(dest, list(ext_root) + [config_old, config_new])
        argv = ["--ext-root"] + list(ext_root) + ["--config-old", config_old, "--config-new", config_new,
                                                  "--out", dest]
        if skip:
            argv += ["--skip"] + list(skip)
        if order:
            argv += ["--order", order]
        run.call("interceptors", argv)
        data = _load(os.path.join(dest, "interceptors.json"))
        roots = {e["name"]: e["root"] for e in data["extensions"]}
        rows = data["registry"]
        items = []
        for r in rows:
            if only_flagged and not r["flags"]:
                continue
            it = {k: r.get(k) for k in INTERCEPTOR_FIELDS}
            if it["target_old"] == it["target"]:
                it["target_old"] = None
            if it["base_sig_old"] == it["base_sig_new"]:
                it["base_sig_old"] = None
            root = roots.get(r["ext"])
            it["path"] = _join(root, r.get("module"))
            it["form"] = _join(root, r.get("form"))
            items.append(_compact(it))
        dead = []
        for category, rows_dead in data["dead_code"].items():
            for d in rows_dead:
                root = roots.get(d["ext"])
                it = dict({"category": category}, **d)
                if "module" in it:
                    it["path"] = _join(root, it.pop("module"))
                if "form" in it:
                    it["form"] = _join(root, it["form"])
                dead.append(_compact(it))
        unreadable = [_compact({"dump": u["dump"], "form": _join(u["root"], u["form"]), "error": u["error"]})
                      for u in data.get("unreadable_forms") or []]
        summary = {"extensions": len(data["extensions"]), "interceptions": len(rows),
                   "flagged": sum(1 for r in rows if r["flags"]), "flags": _flag_counts(rows),
                   "dead_code": {k: len(v) for k, v in data["dead_code"].items()}}
        if unreadable:
            summary["unreadable_forms"] = len(unreadable)
        return {"summary": summary, "items": items, "report": _report(dest, "interceptors"), "dead_code": dead,
                "unreadable_forms": unreadable}

    return _execute("list_interceptors", inputs,
                    lambda: dict(_take_dict(inputs, "ext_root", "config_old", "config_new"),
                                 order=[order] if order else []), body,
                    _from_registry(inputs, DUMP_KEYS, DUMP_KEYS))


def method_diff(ext: str | None = None, old: str | None = None, new: str | None = None, out: str | None = None,
                only: str | None = None, method: str | None = None, with_hunks: bool = False, db: str | None = None,
                to: str | None = None, from_release: str | None = None,
                registry: str | None = None) -> dict[str, Any]:
    """Что вендор изменил old → new в каждом перехваченном методе (`method-diff`).

    ext — папка выгрузок расширений (или одна выгрузка); old / new — выгрузки типовой; only — одно расширение;
    method — один метод вендора (цель перехвата); with_hunks — включить ханки в ответ.
    db / to / from_release / registry — пути из реестра баз вместо ext / old / new / out (явные перекрывают); в inputs
    ответа — разрешённые пути, ext_newest — время самого нового файла выгрузки расширений.

    items — по объекту на метод вендора: module (путь внутри выгрузки), module_title, target, interceptors (ext, kind,
    method, proceed, line — строка аннотации в модуле расширения), status: changed | unchanged | other, detail (текст
    статуса, в т.ч. «только комментарии — контроль &ИзменениеИКонтроль всё равно ломается»), view (контроль | код),
    hunk_count, hunk_lines (изменённых строк), sig_changed (sig_old / sig_new — только если изменилась), old_path /
    old_lines и new_path / new_lines — модуль вендора и диапазон метода [первая, последняя] строка. Сами ханки (hunks:
    tag, old / new — пары [строка, текст], at_old / at_new) — только при with_hunks или заданном method; иначе читать
    report.md по пути report.md, там ханки всех методов с номерами строк модулей вендора."""
    inputs = {"ext": ext, "old": old, "new": new, "out": out, "only": only, "method": method,
              "with_hunks": with_hunks, "db": db, "to": to, "from_release": from_release, "registry": registry}

    def body(run):
        ext, old, new, out = _take(inputs, "ext", "old", "new", "out")
        _require_out(out)
        dest = run.folder(out)
        _refuse_inside(dest, [ext, old, new])
        argv = ["--ext", ext, "--old", old, "--new", new, "--out", dest]
        if only:
            argv += ["--only", only]
        if method:
            argv += ["--method", method]
        run.call("method_diff", argv)
        data = _load(os.path.join(dest, "report.json"))
        hunks_wanted = with_hunks or bool(method)
        items, status = [], Counter()
        for g in data["methods"]:
            st = "changed" if g["hunks"] else ("unchanged" if g["status"] == "тело не изменилось" else "other")
            status[st] += 1
            it = {"module": g["module"], "module_title": g["module_title"], "target": g["target"], "status": st,
                  "detail": None if st == "unchanged" else g["status"], "view": g.get("view"),
                  "hunk_count": len(g["hunks"]),
                  "hunk_lines": sum(len(h["old"]) + len(h["new"]) for h in g["hunks"]),
                  "sig_changed": g["sig_changed"],
                  "sig_old": g["sig_old"] if g["sig_changed"] else None,
                  "sig_new": g["sig_new"] if g["sig_changed"] else None,
                  "old_path": _join(old, g["module"]) if g["old_lines"] else None, "old_lines": _span(g["old_lines"]),
                  "new_path": _join(new, g["module"]) if g["new_lines"] else None, "new_lines": _span(g["new_lines"]),
                  "interceptors": [_compact(i) for i in g["interceptors"]]}
            if hunks_wanted and g["hunks"]:
                it["hunks"] = g["hunks"]
            items.append(_compact(it))
        summary = {"methods": len(data["methods"]),
                   "interceptors": sum(len(g["interceptors"]) for g in data["methods"]),
                   "changed": status["changed"], "unchanged": status["unchanged"], "other": status["other"],
                   "hunk_lines": sum(len(h["old"]) + len(h["new"]) for g in data["methods"] for h in g["hunks"])}
        return {"summary": summary, "items": items, "report": _report(dest, "report")}

    return _execute("method_diff", inputs, lambda: _take_dict(inputs, "ext", "old", "new"), body,
                    _from_registry(inputs, CONTROL_KEYS, CONTROL_KEYS))


def _control(run, ext, old, new, out, work, only):
    """control_rebase into the per-call folder: (folder, report data, ext roots, work roots)."""
    _require_out(out)
    dest = run.folder(out)
    argv = ["--ext", ext, "--old", old, "--new", new, "--out", dest]
    if work:
        argv += ["--work", work]
    if only:
        argv += ["--only", only]
    run.call("control_rebase", argv)
    data = _load(os.path.join(dest, "report.json"))
    return dest, data, _ext_roots([ext], [only] if only else None), _ext_roots([work]) if work else {}


def _control_item(r, dest, roots, work_roots):
    it = {"ext": r["ext"], "module": r["module"], "module_title": r["module_title"], "target": r["target"],
          "ext_method": r["ext_method"], "verdict": r["verdict"], "note": r.get("note"),
          "pre": r["pre"] if r.get("pre") not in (None, "ok") else None,
          "post": r["post"] if r.get("post") not in (None, "PASS") else None,
          "problems": r.get("problems"), "sig_ext_vs_new": r.get("sig_ext_vs_new"),
          "sig_changed": r.get("sig_changed"), "header": r.get("header"),
          "conflicts": r.get("conflicts"),
          "conflict_lines": [b[0] for b in r.get("conflict_blocks") or []],
          "same_point": sum(1 for k in r.get("conflict_kinds") or [] if k["kind"] == "same_point") or None,
          "lost": len(r["lost"]) if "lost" in r else None,
          "duplicated": {k: len(v) for k, v in r["duplicated_unexplained"].items()}
          if "duplicated_unexplained" in r else None,
          "path": _join(roots.get(r["ext"]), r["module"]), "ext_lines": _span(r["ext_lines"]),
          "candidate": _join(dest, r["file"]) if r.get("file") else None}
    if "work" in r:
        w = r["work"]
        it["work"] = _compact({"ext": w.get("ext"), "method": w.get("method"),
                               "path": _join(work_roots.get(w.get("ext")), r["module"]) if w.get("lines") else None,
                               "lines": _span(w.get("lines")), "control": w.get("control"), "status": w.get("status")})
    return _compact(it)


def rebase_control(ext: str | None = None, old: str | None = None, new: str | None = None, out: str | None = None,
                   work: str | None = None, only: str | None = None, db: str | None = None, to: str | None = None,
                   from_release: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Перенос всех `&ИзменениеИКонтроль` расширений на новый релиз трёхсторонним слиянием с проверкой контроля
    платформы (`control-rebase`).

    ext — папка выгрузок расширений (или одна выгрузка); old / new — выгрузки типовой; work — рабочая копия с ручным
    переносом (та же раскладка, что ext; расширение находится и по uuid после переименования) — для сверки; only —
    одно расширение. Выгрузки не пишутся: out внутри любой из них — отказ.
    db / to / from_release / registry — пути из реестра баз вместо ext / old / new / out / work (если папка есть;
    явные перекрывают); в inputs ответа — разрешённые пути, ext_newest — время самого нового файла выгрузки расширений.

    items — по объекту на метод с `&ИзменениеИКонтроль`: ext, module (путь внутри выгрузки), module_title, target,
    ext_method, verdict (UNCHANGED, ALREADY_NEW — переносить нечего, но UNCHANGED с note и problems — заголовок метода
    расширения не подходит к new: метод не готов, заголовок править руками; REBASED — кандидат готов; CONFLICTS — решить
    только блоки конфликтов; FAIL — слито, но кандидат не прошёл проверку; OUT_OF_SYNC — расширение не совпадало с
    old ещё до обновления; MISSING — нет метода вендора), причина: note, pre (контроль до: расширение против old),
    post (контроль кандидата против new), problems, sig_ext_vs_new, header (откуда взят заголовок); conflicts,
    conflict_lines (строки маркеров <<<<<<< в файле кандидата), same_point (сколько из них — вставки вендора и автора в
    одной точке: порядок решает человек, контроль проходит при любом), lost (строк автора потеряно), duplicated {new, copy}
    (задвоится строк), path + ext_lines — модуль расширения и диапазон метода [с аннотацией, по конец], candidate —
    файл кандидата (метод целиком; суффикс .conflicts / .fail / .out-of-sync у не готовых), work — сверка с рабочей
    копией (path, lines — где метод в work, status, control). summary — счёты вердиктов как в report.json.
    Кандидат в рабочую копию переносит apply_candidate по диапазону work.lines (или ext_lines для копии ext)."""
    inputs = {"ext": ext, "old": old, "new": new, "out": out, "work": work, "only": only, "db": db, "to": to,
              "from_release": from_release, "registry": registry}

    def body(run):
        dest, data, roots, work_roots = _control(run, *_take(inputs, "ext", "old", "new", "out", "work"), only)
        items = [_control_item(r, dest, roots, work_roots) for r in data["methods"]]
        return {"summary": data["summary"], "items": items, "report": _report(dest, "report")}

    return _execute("rebase_control", inputs,
                    lambda: dict(_take_dict(inputs, "ext", "old", "new"),
                                 work=[inputs["work"]] if inputs["work"] else []), body,
                    _from_registry(inputs, dict(CONTROL_KEYS, work="work"), CONTROL_KEYS))


def check_control(ext: str | None = None, old: str | None = None, new: str | None = None, out: str | None = None,
                  only: str | None = None, db: str | None = None, to: str | None = None,
                  from_release: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Проверка, что `&ИзменениеИКонтроль` расширения проходят контроль платформы на новом релизе без правок:
    pass / fail по каждому методу (тот же прогон, что rebase_control).

    pass — вердикт UNCHANGED или ALREADY_NEW и заголовок метода расширения подходит к new (текст по контролю совпадает
    с new, sig_ext_vs_new пуст); fail — любой другой вердикт или UNCHANGED с несовпавшей сигнатурой (тело переносить
    не нужно, но перехват не применится: в sig_ext_vs_new / problems), вердикт и причина в ответе. Чтобы проверить
    перенесённую рабочую копию, передай её
    как ext: тогда все перенесённые методы должны дать pass (ALREADY_NEW).
    db / to / from_release / registry — пути из реестра баз вместо ext / old / new / out (явные перекрывают; рабочую
    копию передавать явно как ext); в inputs ответа — разрешённые пути, ext_newest — время самого нового файла выгрузки
    расширений.

    items — по объекту на метод: ext, module_title, target, ext_method, result (pass | fail), verdict, path +
    ext_lines — модуль и диапазон метода; у fail — note / pre / post / problems / sig_ext_vs_new и candidate (файл
    кандидата, если слияние его дало). summary — pass, fail и счёты вердиктов."""
    inputs = {"ext": ext, "old": old, "new": new, "out": out, "only": only, "db": db, "to": to,
              "from_release": from_release, "registry": registry}

    def body(run):
        dest, data, roots, _ = _control(run, *_take(inputs, "ext", "old", "new", "out"), None, only)
        items = []
        for r in data["methods"]:
            full = _control_item(r, dest, roots, {})
            ok = control_rebase.ready(r)
            it = {"ext": r["ext"], "module_title": r["module_title"], "target": r["target"],
                  "ext_method": r["ext_method"], "result": "pass" if ok else "fail", "verdict": r["verdict"],
                  "path": full.get("path"), "ext_lines": full.get("ext_lines"),
                  "sig_ext_vs_new": full.get("sig_ext_vs_new")}
            if not ok:
                for k in ("note", "pre", "post", "problems", "candidate"):
                    it[k] = full.get(k)
            items.append(_compact(it))
        passed = sum(1 for it in items if it["result"] == "pass")
        summary = {"pass": passed, "fail": len(items) - passed, "verdicts": data["summary"]}
        return {"summary": summary, "items": items, "report": _report(dest, "report")}

    return _execute("check_control", inputs, lambda: _take_dict(inputs, "ext", "old", "new"), body,
                    _from_registry(inputs, CONTROL_KEYS, CONTROL_KEYS))


def port_replace(old_module: str, new_module: str, copy_module: str, method: str, out: str | None = None,
                 copy_method: str | None = None, db: str | None = None, to: str | None = None,
                 from_release: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Перенос одной замороженной копии `&Вместо` на тело нового релиза трёхсторонним слиянием (`merge-copy`).

    old_module / new_module — файлы модуля вендора (.bsl) старого и нового релиза; copy_module — модуль расширения
    с копией; method — метод конфигурации, который копия заменяет; copy_method — имя копии в расширении, если
    `&Вместо("method")` в модуле не один. Модуль расширения не пишется.
    db / to / from_release / registry — реестр баз: out берётся из реестра, относительные old_module / new_module /
    copy_module — пути внутри выгрузок old / new / ext базы (CommonModules/X/Ext/Module.bsl,
    <расширение>/CommonModules/X/Ext/Module.bsl); to нужен для относительного new_module.

    items — один объект: method, copy_method, conflicts, same_point (из них — вставки вендора и автора в одной точке:
    порядок решает человек), backport (из них похожие вставки — вероятно, взять new), auto_resolved (конфликты из
    одних пробелов, снятые правилами, в conflicts не входят), conflict_lines (строки <<<<<<< в слитом теле), merged —
    файл слитого тела (BOM и концы строк модуля копии; конфликты в стиле diff3 new / old / copy), merged_lines,
    lost — строки копии, которых нет ни в результате, ни в old (возможная потеря работы автора), dropped_vendor_new
    — новый код вендора, выпавший при слиянии (должно быть пусто), duplicated {new, copy} — строки, которые
    задвоятся при выборе стороны (должно быть пусто). summary — те же счёты числами плюс left_new / added_to_new.
    Разбор конфликтов по строкам — report.md."""
    inputs = {"old_module": old_module, "new_module": new_module, "copy_module": copy_module, "method": method,
              "out": out, "copy_method": copy_method, "db": db, "to": to, "from_release": from_release,
              "registry": registry}

    def body(run):
        from . import merge_copy
        old_module, new_module, copy_module, out = _take(inputs, "old_module", "new_module", "copy_module", "out")
        _require_out(out)
        dest = run.folder(out)
        _refuse_inside(dest, [merge_copy._dump_root(old_module), merge_copy._dump_root(new_module)])
        argv = ["--old", old_module, "--new", new_module, "--copy", copy_module, "--method", method, "--out", dest]
        if copy_method:
            argv += ["--copy-method", copy_method]
        run.call("merge_copy", argv)
        found = sorted(glob.glob(os.path.join(glob.escape(dest), "merge_copy_*.json")))
        if len(found) != 1:
            raise RuntimeError("merge_copy report not found in %s" % dest)
        stem = found[0][:-len(".json")]
        data = _load(found[0])
        ctl = data["control"]
        merged = stem + ".bsl"
        lines = fileio.read_lines(merged)
        if lines and lines[-1] == "":
            lines = lines[:-1]
        strip = lambda xs: [s.strip() for s in xs]  # noqa: E731
        kinds = data.get("conflict_kinds", [])
        item = {"method": data["method"], "copy_method": data["copy_method"], "conflicts": data["conflicts"],
                "same_point": sum(1 for k in kinds if k["kind"] == "same_point") or None,
                "backport": sum(1 for k in kinds if k["kind"] == "same_point" and k["backport"]) or None,
                "auto_resolved": len(data.get("auto_resolved", [])) or None,
                "conflict_lines": [n for n, s in enumerate(lines, 1) if s.startswith("<<<<<<<")],
                "merged": merged, "merged_lines": len(lines), "lost": strip(ctl["lost"]),
                "dropped_vendor_new": strip(ctl["dropped_vendor_new"]),
                "duplicated": _compact({k: strip(v) for k, v in ctl["duplicated"].items()}, keep=())}
        summary = {"conflicts": data["conflicts"], "lost": len(ctl["lost"]),
                   "dropped_vendor_new": len(ctl["dropped_vendor_new"]),
                   "duplicated_new": len(ctl["duplicated"]["new"]), "duplicated_copy": len(ctl["duplicated"]["copy"]),
                   "left_new": len(ctl["left_new"]), "added_to_new": len(ctl["added_to_new"])}
        return {"summary": summary, "items": [_compact(item, keep=("conflicts",))],
                "report": {"json": found[0], "md": stem + ".md"}}

    return _execute("port_replace", inputs, lambda: _take_dict(inputs, "old_module", "new_module", "copy_module"),
                    body, _from_registry(inputs, {"out": "out"}, ("out",),
                                         {"old_module": "old", "new_module": "new", "copy_module": "ext"}))


def check_calls(ext_root: list[str] | None = None, config_old: str | None = None, config_new: str | None = None,
                out: str | None = None, skip: list[str] = [], db: str | None = None, to: str | None = None,
                from_release: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Вызовы методов конфигурации из кода расширений против нового релиза (`calls`): только находки с флагами.

    ext_root — папки с выгрузками расширений; config_old / config_new — выгрузки типовой; skip — имена расширений,
    которые пропустить.
    db / to / from_release / registry — пути из реестра баз вместо ext_root / config_old / config_new / out (явные
    перекрывают); в inputs ответа — разрешённые пути, ext_newest — время самого нового файла выгрузки расширений.

    items — по находке: ext, module_title, path (модуль расширения), line (строка вызова в нём), caller (метод, из
    которого вызов), call, args (число аргументов), target, flags (REMOVED — был в old, в new нет; MISSING — нет ни
    там, ни там; NOT_EXPORT; TOO_FEW_ARGS; TOO_MANY_ARGS; SIGNATURE_CHANGED), grade у SIGNATURE_CHANGED: risk | info
    (info — вызов совместим), note — пояснение. Вызов внутри копии &Вместо / &ИзменениеИКонтроль в заимствованном
    модуле — ещё copy {annotation, target_in_new (перехватываемый метод есть в new), vendor_old / vendor_new (тот же
    вызов есть в теле вендора old / new), new_as (как new вызывает тот же метод с другим квалификатором — куда вендор
    его перенёс)} и advice: «переснять копию по new» (вызов из текста вендора, в new его нет — находка уходит
    пересъёмкой, замену методу искать не надо), «правка автора», MISSING_BASE. summary — счёты флагов, risk / info,
    модулей, in_copies, fixed_by_report (уйдут пересъёмкой копии). Сигнатуры old/new и разбор по видам вызовов —
    report.md."""
    inputs = {"ext_root": ext_root, "config_old": config_old, "config_new": config_new, "out": out, "skip": skip,
              "db": db, "to": to, "from_release": from_release, "registry": registry}

    def body(run):
        ext_root, config_old, config_new, out = _take(inputs, "ext_root", "config_old", "config_new", "out")
        _require_out(out)
        dest = run.folder(out)
        _refuse_inside(dest, list(ext_root) + [config_old, config_new])
        argv = ["--ext-root"] + list(ext_root) + ["--config-old", config_old, "--config-new", config_new,
                                                  "--out", dest]
        if skip:
            argv += ["--skip"] + list(skip)
        run.call("calls", argv)
        data = _load(os.path.join(dest, "calls.json"))
        roots = _ext_roots(ext_root, None, skip)
        flagged = [f for f in data["findings"] if f["flags"]]
        items, grades = [], Counter()
        for f in flagged:
            it = {k: f.get(k) for k in CALL_FIELDS}
            it["path"] = _join(roots.get(f["ext"]), f["module"])
            if "SIGNATURE_CHANGED" in f["flags"]:
                it["grade"] = "risk" if f["note"].startswith("[риск]") else "info"
                grades[it["grade"]] += 1
            items.append(_compact(it))
        copies = [f["copy"] for f in flagged if f.get("copy")]
        summary = {"findings": len(items), "flags": _flag_counts(flagged),
                   "signature_changed": {"risk": grades["risk"], "info": grades["info"]},
                   "extensions": len(data["extensions"]), "modules": data["stats"].get("modules", 0),
                   "in_copies": len(copies),
                   "fixed_by_report": sum(1 for c in copies if c["target_in_new"] and c["vendor_old"] and
                                          not c["vendor_new"])}
        return {"summary": summary, "items": items, "report": _report(dest, "calls")}

    return _execute("check_calls", inputs, lambda: _take_dict(inputs, "ext_root", "config_old", "config_new"), body,
                    _from_registry(inputs, DUMP_KEYS, DUMP_KEYS))


def check_queries(ext_root: list[str] | None = None, config_old: str | None = None, config_new: str | None = None,
                  out: str | None = None, skip: list[str] = [], db: str | None = None, to: str | None = None,
                  from_release: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Тексты запросов, схемы СКД и имена метаданных в строках расширений против нового релиза (`queries`): только
    находки.

    ext_root — папки с выгрузками расширений; config_old / config_new — выгрузки типовой; skip — имена расширений,
    которые пропустить.
    db / to / from_release / registry — пути из реестра баз вместо ext_root / config_old / config_new / out (явные
    перекрывают); в inputs ответа — разрешённые пути, ext_newest — время самого нового файла выгрузки расширений.

    items — по находке: part (query | dcs | literal), kind, sev (ОШИБКА? | РИСК | ПРОВЕРИТЬ), ext, path (модуль,
    Form.xml или схема СКД), line, where, ref (что не разрешилось), note, old («было корректно» — сломано
    обновлением, «тоже не разрешается» — ошибка была и до него). unjudged — раздел «не судится по метаданным»
    (псевдоним переиспользован, имя подставляется через СтрЗаменить и т.п.): число и путь к report.md, смотреть
    глазами. summary — счёты по part/kind и что проверено."""
    inputs = {"ext_root": ext_root, "config_old": config_old, "config_new": config_new, "out": out, "skip": skip,
              "db": db, "to": to, "from_release": from_release, "registry": registry}

    def body(run):
        ext_root, config_old, config_new, out = _take(inputs, "ext_root", "config_old", "config_new", "out")
        _require_out(out)
        dest = run.folder(out)
        _refuse_inside(dest, list(ext_root) + [config_old, config_new])
        argv = ["--ext-root"] + list(ext_root) + ["--config-old", config_old, "--config-new", config_new,
                                                  "--out", dest]
        if skip:
            argv += ["--skip"] + list(skip)
        run.call("queries", argv)
        data = _load(os.path.join(dest, "queries.json"))
        roots = _ext_roots(ext_root, None, skip)
        items = []
        for f in data["findings"]:
            it = {k: f.get(k) for k in QUERY_FIELDS}
            it["path"] = _join(roots.get(f["ext"]), f["file"])
            items.append(_compact(it))
        report = _report(dest, "queries")
        summary = {"findings": len(items),
                   "classes": dict(sorted(Counter("%s/%s" % (f["part"], f["kind"]) for f in data["findings"]).items())),
                   "unjudged": len(data["masked"]), "checked": data["stats"]}
        unjudged = {"count": len(data["masked"]), "report": report["md"]} if data["masked"] else None
        return {"summary": summary, "items": items, "report": report, "unjudged": unjudged}

    return _execute("check_queries", inputs, lambda: _take_dict(inputs, "ext_root", "config_old", "config_new"),
                    body, _from_registry(inputs, DUMP_KEYS, DUMP_KEYS))


def applied_text(src: str, dst: str) -> dict[str, Any]:
    """Применённая копия модулей с `&ИзменениеИКонтроль` (`applied`): блоки #Удаление и строки маркеров опустошены,
    номера строк как в исходном файле — для синтакс-проверки, чтобы ошибки указывали в настоящий модуль.

    src — файл модуля или папка (все .bsl в ней); dst — папка результата вне src и вне любой выгрузки (относительные
    пути сохраняются): если файл результата лёг бы в папку с Configuration.xml — сама dst, её родитель или выгрузка
    внутри dst — отказ (bad_input), ничего не пишется.
    items — по файлу: file (путь от src), path (исходный модуль), dst (готовая копия), blanked (опустошено строк).
    summary — файлов и строк."""
    inputs = {"src": src, "dst": dst}

    def body(run):
        if not dst or not os.path.isabs(dst):
            raise BadInput("dst: path must be absolute: %s" % dst)
        console = run.call("applied", ["--src", src, "--dst", dst])
        src_dir = src if os.path.isdir(src) else os.path.dirname(src)
        items = []
        for line in console.splitlines():
            m = APPLIED_LINE.match(line)
            if m:
                rel = m.group("rel")
                items.append({"file": rel, "path": _join(src_dir, rel), "dst": _join(dst, rel),
                              "blanked": int(m.group("n"))})
        return {"summary": {"files": len(items), "blanked": sum(i["blanked"] for i in items)}, "items": items}

    return _execute("applied_text", inputs, {"src": src}, body)


def check_borrowed(ext_root: list[str] | None = None, config_old: str | None = None, config_new: str | None = None,
                   out: str | None = None, skip: list[str] = [], db: str | None = None, to: str | None = None,
                   from_release: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Заимствованные объекты расширений против нового релиза (`borrowed`): то, на чём споткнётся проверка
    применимости расширения платформой.

    ext_root — папки выгрузок расширений; config_old / config_new — выгрузки типовой; skip — расширения, которые
    пропустить. db / to / from_release / registry — пути из реестра баз (явные перекрывают).

    items — по находке: ext, object (Вид.Имя), file (XML объекта в расширении), path, part (реквизит, ресурс,
    измерение, значение перечисления, табличная часть, форма, …), name, check, detail, forms (формы расширения этого
    объекта, где поле привязано через DataPath). check: OBJECT_REMOVED — заимствованного объекта нет в new; REMOVED —
    заимствованного поля / значения / формы нет в new (переименование вендором в «Удалить<Имя>» — в detail);
    TYPE_CHANGED — вендор сменил тип (длина строки, разрядность, состав типа сузился), в расширении старый; порядок
    типов в составном типе не считается; NAME_CLASH — собственный реквизит или табличная часть расширения совпали по
    имени с новым полем вендора; NOT_IN_VENDOR — поля нет ни в old, ни в new (доработанная конфигурация базы или поле
    другого расширения — уточнить, не ошибка обновления); колонки заимствованных табличных частей — по тем же
    правилам; LIST_FIELD_LOST — вендор изменил запрос динамического списка заимствованной формы, и новый запрос не
    выбирает поле, которое расширение выводит на форму (стандартные реквизиты Список.Ref / Date не сравниваются);
    LIST_CHANGED — этот список меняли и вендор, и расширение (в detail — поля запроса расширения, которых новый запрос
    вендора не выбирает); XML_UNREADABLE — XML объекта расширения или вендора не разобран: других вердиктов по нему нет,
    смотреть файл. Имена сравниваются без учёта регистра.
    summary — счёты по проверкам, расширений и заимствованных объектов."""
    inputs = {"ext_root": ext_root, "config_old": config_old, "config_new": config_new, "out": out, "skip": skip,
              "db": db, "to": to, "from_release": from_release, "registry": registry}

    def body(run):
        ext_root, config_old, config_new, out = _take(inputs, "ext_root", "config_old", "config_new", "out")
        _require_out(out)
        dest = run.folder(out)
        _refuse_inside(dest, list(ext_root) + [config_old, config_new])
        argv = ["--ext-root"] + list(ext_root) + ["--config-old", config_old, "--config-new", config_new,
                                                  "--out", dest] + (["--skip"] + list(skip) if skip else [])
        run.call("borrowed", argv)
        data = _load(os.path.join(dest, "borrowed.json"))
        roots = _ext_roots(ext_root, None, skip)
        items = [_compact(dict({k: f.get(k) for k in ("ext", "object", "file", "part", "name", "check", "detail",
                                                      "forms")}, path=_join(roots.get(f["ext"]), f["file"])))
                 for f in data["findings"]]
        summary = dict({"findings": len(items), "checks": dict(sorted(Counter(f["check"] for f in data["findings"])
                                                                      .items()))},
                       extensions=len(data["extensions"]), adopted=data["stats"].get("adopted", 0))
        return {"summary": summary, "items": items, "report": _report(dest, "borrowed")}

    return _execute("check_borrowed", inputs, lambda: _take_dict(inputs, "ext_root", "config_old", "config_new"),
                    body, _from_registry(inputs, DUMP_KEYS, DUMP_KEYS))


def check_registers(ext_root: list[str] | None = None, config_old: str | None = None, config_new: str | None = None,
                    out: str | None = None, skip: list[str] = [], db: str | None = None, to: str | None = None,
                    from_release: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Запись в регистры, у которых новый релиз изменил состав (`registers`): ключ записи поменялся.

    ext_root — папки выгрузок расширений; config_old / config_new — выгрузки типовой; skip — расширения, которые
    пропустить. db / to / from_release / registry — пути из реестра баз (явные перекрывают).

    items — по методу, который пишет в регистр (РегистрыX.Имя.СоздатьМенеджерЗаписи / СоздатьНаборЗаписей,
    Движения.Имя — любое обращение): ext, module_title, path, line, method, register, how (manager | recordset |
    movements), check (CHANGED — состав изменился; REGISTER_REMOVED — регистра в new нет, «Удалить<Имя>» вендора — в
    detail; XML_UNREADABLE — XML регистра не разобран, судить нельзя), added (новые
    измерения; «Удалить…» вендора не в счёт), missing (новые измерения, которых метод не упоминает — менеджер записи
    читает или заменяет другую запись, набор пишет пустое измерение), stale (поля, которых в new нет, а метод их
    использует), to_dims (ресурсы и реквизиты, ставшие измерениями), to_res (измерения old, которые в new стали
    ресурсом или реквизитом), key_lost (те из to_res, которые метод использует: поле есть, но из ключа записи и отбора
    набора оно ушло), callers (методы модуля, которые его вызывают и,
    возможно, передают ключ), detail. Проверка по тексту метода: измерение, заполняемое через ЗаполнитьЗначенияСвойств
    или структуру, не видно. summary — находок, регистров, в которые пишет код, сколько из них изменилось, удалено
    вендором и не разобрано."""
    inputs = {"ext_root": ext_root, "config_old": config_old, "config_new": config_new, "out": out, "skip": skip,
              "db": db, "to": to, "from_release": from_release, "registry": registry}

    def body(run):
        ext_root, config_old, config_new, out = _take(inputs, "ext_root", "config_old", "config_new", "out")
        _require_out(out)
        dest = run.folder(out)
        _refuse_inside(dest, list(ext_root) + [config_old, config_new])
        argv = ["--ext-root"] + list(ext_root) + ["--config-old", config_old, "--config-new", config_new,
                                                  "--out", dest] + (["--skip"] + list(skip) if skip else [])
        run.call("registers", argv)
        data = _load(os.path.join(dest, "registers.json"))
        roots = _ext_roots(ext_root, None, skip)
        items = [_compact(dict({k: f.get(k) for k in REGISTER_FIELDS}, path=_join(roots.get(f["ext"]), f["module"])))
                 for f in data["findings"]]
        summary = {"findings": len(items), "registers_written": data["stats"].get("registers_written", 0),
                   "registers_changed": data["stats"].get("registers_changed", 0),
                   "registers_removed": data["stats"].get("registers_removed", 0),
                   "registers_unreadable": data["stats"].get("registers_unreadable", 0),
                   "extensions": len(data["extensions"])}
        return {"summary": summary, "items": items, "report": _report(dest, "registers")}

    return _execute("check_registers", inputs, lambda: _take_dict(inputs, "ext_root", "config_old", "config_new"),
                    body, _from_registry(inputs, DUMP_KEYS, DUMP_KEYS))


def check_reads(ext_root: list[str] | None = None, config_old: str | None = None, config_new: str | None = None,
                out: str | None = None, skip: list[str] = [], db: str | None = None, to: str | None = None,
                from_release: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Чтение регистров вендора в запросах расширений, у которых новый релиз изменил состав или регистраторов
    (`reads`). Проверка информационная, без вердиктов: новое измерение, ресурс или регистратор не ломают текст
    запроса, и check_queries такой запрос не покажет, но результат может измениться — новое измерение дробит остатки и
    обороты, новый регистратор добавляет движения, сменившийся тип меняет соединения и суммы. Смотреть глазами.

    ext_root — папки выгрузок расширений; config_old / config_new — выгрузки типовой; skip — расширения, которые
    пропустить. db / to / from_release / registry — пути из реестра баз (явные перекрывают).

    Источники — только тексты запросов: строки модулей (закомментированный код не считается), ручной запрос
    динамического списка в Form.xml, наборы СКД; таблицы РегистрНакопления, РегистрСведений, РегистрБухгалтерии,
    РегистрРасчета, с виртуальной таблицей и без. Чтение через менеджер (РегистрыНакопления.X.Остатки()) в этой
    версии не смотрится. Не попадают: временные таблицы и параметры, собственные регистры расширения, регистры, которых
    нет в old, и регистры без изменений (их только считает summary).

    items — по паре (расширение, регистр): ext, register (Вид.Имя), check, sev (всегда ПРОВЕРИТЬ), dims_added,
    dims_removed, res_added, res_removed, attrs_added, attrs_removed (имена; поле, которое вендор переименовал в
    «Удалить<Имя>», — в removed, новое имя в detail, в added его нет), type_changed ({part: dimension | resource |
    attribute, name, old, new} — и у поля, перешедшего в другую часть (part — где оно теперь); defined_type — если
    сменился определяемый тип, которым типизировано поле, а XML самого поля прежний; порядок типов в составном типе
    не считается), props_changed ({name, old, new}: RegisterType, InformationRegisterPeriodicity, WriteMode,
    <Измерение>.UseInTotals — свойства, от которых зависит результат запроса), recorders_added, recorders_removed
    (документы-регистраторы), reads (места чтения: path — модуль, Form.xml или
    Template.xml, line — строка, 0 — если неизвестна, where — метод, список или набор, table — таблица как в запросе,
    с виртуальной, fields — поля, которые запрос берёт через её псевдоним), detail. Пустые списки не передаются.
    check: CHANGED — изменились состав, типы, свойства или регистраторы; REGISTER_REMOVED — регистра в new нет (в old был),
    «Удалить<Имя>» вендора — в detail; XML_UNREADABLE — XML регистра в old или new не разобран, состав не сравнивался.
    summary — extensions, reads (мест чтения), registers_read (регистров вендора, которые читают запросы), из них
    registers_changed, registers_removed, registers_unreadable, registers_unchanged."""
    inputs = {"ext_root": ext_root, "config_old": config_old, "config_new": config_new, "out": out, "skip": skip,
              "db": db, "to": to, "from_release": from_release, "registry": registry}

    def body(run):
        ext_root, config_old, config_new, out = _take(inputs, "ext_root", "config_old", "config_new", "out")
        _require_out(out)
        dest = run.folder(out)
        _refuse_inside(dest, list(ext_root) + [config_old, config_new])
        argv = ["--ext-root"] + list(ext_root) + ["--config-old", config_old, "--config-new", config_new,
                                                  "--out", dest] + (["--skip"] + list(skip) if skip else [])
        run.call("reads", argv)
        data = _load(os.path.join(dest, "reads.json"))
        items = [_compact({k: f.get(k) for k in READ_FIELDS}) for f in data["findings"]]
        summary = {k: data["stats"].get(k, 0) for k in ("extensions", "reads", "registers_read", "registers_changed",
                                                        "registers_removed", "registers_unreadable",
                                                        "registers_unchanged")}
        return {"summary": summary, "items": items, "report": _report(dest, "reads")}

    return _execute("check_reads", inputs, lambda: _take_dict(inputs, "ext_root", "config_old", "config_new"),
                    body, _from_registry(inputs, DUMP_KEYS, DUMP_KEYS))


def check_work(work: str | None = None, ext: str | None = None, out: str | None = None, only: str | None = None,
               db: str | None = None, registry: str | None = None) -> dict[str, Any]:
    """Проверка рабочей копии перед загрузкой в базу (`lint`): то, что платформа увидит только при загрузке.

    work — рабочая копия (папка расширений или одно расширение); ext — выгрузки, с которых начинался перенос (сверка
    файлов, BOM и концов строк модулей; расширение находится и по uuid после переименования; ext без выгрузок
    расширений — отказ); only — одно расширение: имя, папка или uuid расширения рабочей копии или его пары в ext (имя
    до переименования тоже подходит); out — папка отчёта вне рабочей копии и вне ext. db / registry — work, ext и out
    из реестра баз (релиз не нужен).

    items — находки, сначала ошибки: ext, file (путь от расширения), path, line, check, severity (error | info),
    detail. Проверки: MERGE_MARKER / MERGE_MARKER_ORPHAN (маркеры конфликта слияния в любом текстовом файле),
    MARKER_* (#Вставка/#Удаление: не закрыт, пересекается, без пары, вне метода, в методе без &ИзменениеИКонтроль,
    неизвестное написание), METHOD_* (не закрыт, конец без начала, Процедура закрыта КонецФункции), ANNOTATION_*
    (аннотация перехвата без метода, без цели, несколько), DUP_METHOD, PREPROC_UNBALANCED (незакрытый #Если — по
    находке на каждый, в строке, где он открыт; ветка или #КонецЕсли без #Если), REGION_UNBALANCED (так же для
    #Область), PREPROC_UNKNOWN (неизвестная инструкция препроцессора — error: такой модуль платформа не компилирует),
    ENCODING (.bsl или .xml не в UTF-8: строка первого плохого байта, прочие проверки текста этого файла не делались);
    с ext — FILE_MISSING, BOM_LOST, EOL_LF (исходный модуль в CRLF, в work — LF), EOL_MIXED (в work — смесь CRLF и
    LF, line — первая строка с LF), OBJECT_REMOVED (info: объект убран вместе с Configuration.xml — подтвердить) и
    CHILD_REMOVED (info: форма, макет или команда убраны вместе с записью в ChildObjects XML объекта — подтвердить;
    file — Вид/Объект/Forms/Имя). Номера строк — по CR LF, LF и CR, как в файле.
    summary — расширений, файлов, модулей, errors, info, счёт по проверкам; without_source и without_source_names —
    расширения рабочей копии без пары в ext (для них только проверка структуры, файлы не сверялись)."""
    inputs = {"work": work, "ext": ext, "out": out, "only": only, "db": db, "registry": registry}

    def body(run):
        work, ext, out = _take(inputs, "work", "ext", "out")
        _require_out(out)
        dest = run.folder(out)
        _refuse_inside(dest, [work, ext])
        argv = ["--work", work, "--out", dest] + (["--ext", ext] if ext else []) + (["--only", only] if only else [])
        run.call("lint", argv)
        data = _load(os.path.join(dest, "lint.json"))
        issues = data["issues"]
        errors = sum(1 for x in issues if x["severity"] == "error")
        summary = dict(data["stats"], errors=errors, info=len(issues) - errors,
                       checks=dict(sorted(Counter(x["check"] for x in issues).items())))
        items = [_compact({k: x.get(k) for k in ("ext", "file", "path", "line", "check", "severity", "detail")})
                 for x in issues]
        return {"summary": summary, "items": items, "report": _report(dest, "lint")}

    return _execute("check_work", inputs, lambda: dict({"work": inputs["work"]},
                                                        **({"ext": inputs["ext"]} if inputs["ext"] else {})),
                    body, _from_registry(inputs, {"work": "work", "ext": "ext", "out": "out"}, ["work", "out"]))


def _git(args, cwd):
    return subprocess.run(["git", "-c", "core.quotepath=off"] + list(args), cwd=cwd, capture_output=True)


def apply_candidate(work_root: str, module: str, candidate: str, start_line: int, end_line: int) -> dict[str, Any]:
    """Заменить строки start_line..end_line (с 1, включительно — как ext_lines / work.lines в ответе
    rebase_control) модуля рабочей копии содержимым файла-кандидата. Единственный инструмент, который правит модули
    рабочей копии.

    work_root — рабочая копия, обязана быть в git-репозитории (откат — средствами git); module — путь модуля от
    work_root или абсолютный внутри него; candidate — файл кандидата (из rebase_control). BOM и окончания строк
    модуля сохраняются (смешанные окончания приводятся к преобладающему). Отказ (bad_input): work_root не в git,
    модуль вне work_root, модуль не отслеживается git (игнорируется или не добавлен — откатить было бы нечем),
    кандидата нет, диапазон вне файла; модуль или кандидат не в UTF-8 (как ENCODING у
    check_work: файл переписывается целиком и испортился бы); в кандидате маркеры конфликта слияния (<<<<<<< …:
    сначала разрешить конфликт); после замены в модуле появились бы новые структурные ошибки — незакрытый или лишний
    КонецПроцедуры, задвоенный метод, аннотация без метода, несбалансированные #Вставка/#Удаление (обычно диапазон не
    тот). При отказе модуль не меняется.

    items — один объект: module, replaced (заменённый диапазон до правки), candidate_lines (где кандидат теперь в
    модуле), git_numstat {added, deleted} — git diff --numstat по файлу против индекса. summary — строк в диапазоне
    до и после, строк в модуле до и после."""
    inputs = {"work_root": work_root, "module": module, "candidate": candidate, "start_line": start_line,
              "end_line": end_line}

    def body(run):
        # realpath: one spelling for both (8.3 short names, symlinks) before comparing and taking relpath
        root = os.path.realpath(work_root)
        if not os.path.isdir(root):
            raise BadInput("work_root is not a folder: %s" % work_root)
        if _git(["rev-parse", "--show-toplevel"], root).returncode:
            raise BadInput("work_root is not inside a git repository: %s" % work_root)
        path = os.path.realpath(module if os.path.isabs(module) else os.path.join(root, module))
        if not _inside(path, root):
            raise BadInput("module %s is outside work_root %s" % (module, work_root))
        if not os.path.isfile(path):
            raise BadInput("module does not exist: %s" % path)
        # a repository above a git-ignored work copy passes rev-parse: only a tracked module can be rolled back
        if _git(["ls-files", "--error-unmatch", "--", os.path.relpath(path, root)], root).returncode:
            raise BadInput("module is not tracked by git (ignored or never added), the module is unchanged: %s" % path)
        if not os.path.isfile(candidate):
            raise BadInput("candidate does not exist: %s" % candidate)
        for label, src in (("module", path), ("candidate", candidate)):
            bad = fileio.utf8_error(fileio.read_bytes(src))
            if bad:  # the whole module is rewritten: a lenient read would turn its other bytes into U+FFFD
                raise BadInput("%s is not UTF-8 (line %d: byte 0x%02X, %s), the module is unchanged: %s"
                               % ((label,) + bad + (src,)))
        lines = fileio.read_lines(path)
        count = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
        if not (1 <= start_line <= end_line <= count):
            raise BadInput("range %d-%d is outside the module (%d lines)" % (start_line, end_line, count))
        cand = fileio.read_lines(candidate)
        if cand and cand[-1] == "":
            cand = cand[:-1]
        if not cand:
            raise BadInput("candidate is empty: %s" % candidate)
        markers = structure.merge_markers(cand)
        if markers:
            raise BadInput("candidate has merge conflict markers (lines %s): resolve the conflict first, the module is "
                           "unchanged" % ", ".join(str(n) for _, n, _ in markers[:10]))
        result = lines[:start_line - 1] + cand + lines[end_line:]
        broken = structure.new_issues(structure.module_issues(lines), structure.module_issues(result))
        if broken:
            raise BadInput("the replacement would break the module structure, the module is unchanged (check the "
                           "range %d-%d): %s" % (start_line, end_line, "; ".join(
                               "%s, line %d: %s" % issue for issue in broken[:5])))
        fileio.write_like(path, "\n".join(result), path)
        stat = _git(["diff", "--numstat", "--", os.path.relpath(path, root)], root)
        numstat = None
        first = stat.stdout.decode("utf-8", "replace").strip().splitlines()[:1]
        if first:
            added, deleted = first[0].split("\t")[:2]
            numstat = {"added": int(added) if added.isdigit() else None,
                       "deleted": int(deleted) if deleted.isdigit() else None}
        after = count - (end_line - start_line + 1) + len(cand)
        item = {"module": path, "replaced": [start_line, end_line],
                "candidate_lines": [start_line, start_line + len(cand) - 1], "git_numstat": numstat}
        summary = {"lines_before": end_line - start_line + 1, "lines_after": len(cand),
                   "module_lines_before": count, "module_lines_after": after}
        return {"summary": summary, "items": [_compact(item)]}

    return _execute("apply_candidate", inputs, {"work_root": work_root, "candidate": candidate}, body)


def prepare(db: str, to: str, from_release: str | None = None, registry: str | None = None, dump: bool = True,
            names: list[str] | None = None, only: str | None = None) -> dict[str, Any]:
    """Подготовка базы из реестра к адаптации расширений одним вызовом (`prepare`).

    db — база (id или alias), to — релиз, на который переносим, from_release — релиз расширений (по умолчанию
    cfe_rebase.from базы), registry — файл реестра. Шаги: проверить выгрузки типовой обоих релизов; если dump —
    выполнить команду выгрузки расширений из реестра (cfe_rebase.dump; names — выгрузить эти расширения вместо
    extensions базы), без dump — взять то, что уже лежит в ext; рабочая копия work: если её нет — копия ext под git с
    коммитом «before: <db> <from>», если есть и под git — не трогается (тогда control_rebase сверяет с ней перенос),
    если есть без git — отказ; прогнать control_rebase, interceptors, calls, queries, bodies, borrowed, registers в
    <reports>/prepare-<run_id[:8]>/<инструмент>/ (only — одно расширение), свести находки в план адаптации и записать
    там prepare.json, plan.json, plan.md.

    summary — экран prepare: db, from, to, ext, ext_newest, dumped, work (path, state created | existing, commit,
    compared), verdicts control_rebase, interceptors (rows, flagged, flags), calls (findings — с флагами, flags),
    queries (findings, unjudged), bodies (copies, vendor_changed), borrowed (findings, checks), registers (findings), plan (MUST_FIX / REVIEW / CLARIFY, hidden — сколько
    находок скрыто как шум). items — пункты MUST_FIX плана: ext, module, module_title, method, what (что произошло и что
    делать). План не покрывает собственные копии СКД и макетов вендора под другим именем и запись в регистры через
    ЗаполнитьЗначенияСвойств или структуру — их сверять по тексту. report — json (prepare.json), plan {json, md} и
    {json, md} каждого из семи отчётов. Ошибки: bad_input (до любого запуска) — реестр, релиз, рабочая копия без git
    или не корень своего git-репозитория, рабочая копия внутри ext / old / new или ext внутри рабочей копии,
    dump=True у базы с cfe_rebase.proof_corpus (её ext — эталонная выгрузка: только dump=False); external — команда
    выгрузки или git завершились с ошибкой (хвост вывода — в console), выгрузка не уложилась в
    $CFE_REBASE_DUMP_TIMEOUT секунд (по умолчанию 1800; она и запущенные ею процессы остановлены) или не удалось
    скопировать ext в рабочую копию (недоделанная копия удаляется). Конфликты переноса — не ошибка."""
    inputs = {"db": db, "to": to, "from_release": from_release, "registry": registry, "dump": dump, "names": names,
              "only": only}

    def body(run):
        try:
            res = prep.run(db, to, from_release, registry, dump, names, only, run_id=run.id)
        except prep.PrepareError as exc:
            if exc.detail:
                print(exc.detail)
            raise (External if exc.code == prep.FAILED else BadInput)(exc.message)
        inputs.update({"ext": res["ext"], "old": res["old"], "new": res["new"], "work": res["work"]["path"],
                       "out": os.path.dirname(os.path.dirname(res["prepare_json"]))})
        summary = {"db": res["db"], "from": res["from"], "to": res["to"], "ext": res["ext"],
                   "ext_newest": res["ext_newest"], "dumped": bool(res["dump"]), "work": res["work"],
                   "verdicts": res["control_rebase"]["verdicts"], "interceptors": res["interceptors"],
                   "calls": res["calls"], "queries": res["queries"], "bodies": res["bodies"], "borrowed": res["borrowed"],
                   "registers": res["registers"],
                   "plan": dict(res["plan"]["counts"], hidden=res["plan"]["hidden"])}
        report = dict({"json": res["prepare_json"], "plan": {"json": res["plan"]["json"], "md": res["plan"]["md"]}},
                      **res["reports"])
        items = [_compact({"ext": x["ext"], "module": x["module"], "module_title": x["module_title"],
                           "method": x["method"], "what": x["what"]}) for x in res["plan"]["must_fix"]]
        return {"ext_newest": res["ext_newest"], "summary": summary, "items": items, "report": report}

    return _execute("prepare", inputs, {}, body)


TOOLS = (list_interceptors, method_diff, rebase_control, check_control, port_replace, check_calls, check_queries,
         applied_text, apply_candidate, prepare, check_work, check_borrowed, check_registers, check_reads)


# ------------------------------------------------------------------------------------------------------------------
# Server
# ------------------------------------------------------------------------------------------------------------------

def build_server():
    """The MCP server with every tool registered; raises ImportError without the SDK."""
    try:
        from mcp.server.mcpserver import MCPServer  # mcp 2.x
        server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=__version__)
    except ImportError:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x
        server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)
    for fn in TOOLS:
        server.add_tool(fn, name=fn.__name__, description=inspect.cleandoc(fn.__doc__))
    return server


def main(argv=None):
    ap = argparse.ArgumentParser(description="MCP server (stdio) over the cfe-rebase tools: short structured answers "
                                             "with paths to the full reports")
    ap.parse_args(argv)
    if importlib.util.find_spec("mcp") is None:
        print(INSTALL_HINT, file=sys.stderr)
        sys.exit(2)
    build_server().run("stdio")


if __name__ == "__main__":
    main()
