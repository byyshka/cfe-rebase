# -*- coding: utf-8 -*-
"""cfe-rebase prepare --db <id> --to <release>: everything before adapting the extensions of one database, in one go.

    cfe-rebase prepare --db demo-ut-27 --to 11.5.27.79 [--from R] [--registry FILE] [--no-dump] [--names a,b]
                       [--only NAME]

Paths come from the registry of databases (registry.py). Steps: check the vendor dumps of both releases; run the
registry's dump command (an external program: cfe-rebase knows nothing about the 1C platform) unless --no-dump;
make the work copy - a copy of the extension dump under git with the commit "before: <db> <from>" - unless it exists
already (then it is left untouched); run control_rebase, interceptors, calls, queries, bodies, borrowed and registers into
<reports>/prepare-<run_id[:8]>/<tool>/; merge their findings into an adaptation plan (plan.py: plan.json, plan.md);
print one screen and write the same data to prepare.json next to them.

Exit code: 0 when every tool ran (conflicts are findings, not errors), 2 on bad input, 3 when the dump command, git or
the copy of ext into the work copy failed (a half-made work copy is removed).

Stops before anything runs (bad input): a base whose cfe_rebase.proof_corpus is true (its ext_dump is a reference
dump that must not be re-dumped) unless --no-dump; a work copy inside ext, old or new, or ext inside the work copy; an
existing work copy that is not the top level of its own git repository (inside another repository it may be ignored,
and nothing could be rolled back).

The dump command gets $CFE_REBASE_DUMP_TIMEOUT seconds (default 1800 = 30 minutes); past that the command and every
process it started (a hung Designer) are killed and prepare stops with code 3. Its output goes to temporary files, not
pipes: a process the command starts inherits them, and waiting on a pipe would wait for that process too.
"""
import argparse
import contextlib
import importlib
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from datetime import datetime

from . import fileio, metadata, plan as plan_mod, registry
from .control_rebase import VERDICTS

BAD_INPUT = 2
FAILED = 3
TOOLS = ("control_rebase", "interceptors", "calls", "queries", "bodies", "borrowed", "registers")
REPORT_NAMES = {"control_rebase": "report", "interceptors": "interceptors", "calls": "calls", "queries": "queries",
                "bodies": "bodies", "borrowed": "borrowed", "registers": "registers"}
GIT_USER = ("cfe-rebase", "noreply")
TAIL_LINES = 20
DUMP_TIMEOUT_ENV = "CFE_REBASE_DUMP_TIMEOUT"
DUMP_TIMEOUT = 1800


class PrepareError(Exception):
    """A stop: code 2 (bad input) or 3 (dump, git or the copy failed); detail - the tail of a failed command's output."""

    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail


def run(db, to, from_=None, registry_path=None, dump=True, names=None, only=None, run_id=None, log=None):
    """The whole preparation; returns the data of prepare.json, raises PrepareError."""
    say = log or (lambda _text: None)
    run_id = run_id or str(uuid.uuid4())
    started = datetime.now().astimezone()
    if not to:
        raise PrepareError(BAD_INPUT, "не задан релиз, на который переносим (--to)")
    try:
        r = registry.resolve(registry_path, db, to, from_)
    except registry.RegistryError as exc:
        raise PrepareError(BAD_INPUT, str(exc))
    if dump and r["proof_corpus"]:
        raise PrepareError(BAD_INPUT, "у базы %s в реестре cfe_rebase.proof_corpus: выгрузка расширений %s — эталон "
                                      "(корпус доказательств), её не перевыгружают: запустите prepare с --no-dump "
                                      "(MCP: dump=False)" % (r["db_id"], r["ext"]))
    for label, root in (("ext", r["ext"]), ("old", r["old"]), ("new", r["new"])):
        if _inside(r["work_path"], root):
            raise PrepareError(BAD_INPUT, "рабочая копия %s внутри %s %s: в выгрузке рабочую копию prepare не "
                                          "создаёт и не берёт — задайте cfe_rebase.work вне выгрузок"
                               % (r["work_path"], label, root))
    if _inside(r["ext"], r["work_path"]):
        raise PrepareError(BAD_INPUT, "выгрузка расширений %s внутри рабочей копии %s: выгрузка перезаписала бы "
                                      "рабочую копию — задайте cfe_rebase.ext_dump вне неё"
                           % (r["ext"], r["work_path"]))
    # the vendor dumps first: a missing release must not cost a dump of the extensions
    for role, rel, root in (("from", r["from"], r["old"]), ("to", r["to"], r["new"])):
        problem = metadata.config_problem(root)  # the same rule the tools apply, before anything is dumped or copied
        if problem:
            raise PrepareError(BAD_INPUT, "нет выгрузки типовой релиза %s (%s) в %s: %s" % (rel, role, root, problem))
    folder = os.path.join(r["out"], "prepare-" + run_id[:8])
    for label, root in (("ext", r["ext"]), ("old", r["old"]), ("new", r["new"]), ("work", r["work_path"])):
        if _inside(folder, root):
            raise PrepareError(BAD_INPUT, "папка отчётов %s внутри %s %s: в выгрузки и рабочую копию prepare не пишет"
                               % (r["out"], label, root))
    dumped = _dump(r, names, say) if dump else None
    if not metadata.find_extensions([r["ext"]]):
        raise PrepareError(FAILED if dump else BAD_INPUT,
                           "в %s нет выгрузок расширений (папок с Configuration.xml)%s"
                           % (r["ext"], "" if dump else ": выгрузите их или запустите без --no-dump"))
    newest = registry.ext_newest(r["ext"])
    existed = os.path.exists(r["work_path"])
    work = _work(r, say)
    reports = _analyze(r, folder, r["work_path"] if existed else None, only, say)
    data = {name: _load(reports[name]["json"]) for name in TOOLS}
    ctl = data["control_rebase"]
    rows = data["interceptors"]["registry"]
    calls = [f for f in data["calls"]["findings"] if f["flags"]]
    plan = plan_mod.build(ctl, data["bodies"], data["interceptors"], data["calls"], data["queries"], data["borrowed"],
                          data["registers"])
    plan_json, plan_md = os.path.join(folder, "plan.json"), os.path.join(folder, "plan.md")
    with io.open(plan_json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
    with io.open(plan_md, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(plan_mod.render(plan, "%s, %s → %s" % (r["db_id"], r["from"], r["to"])) + "\n")
    cases = data["bodies"]["cases"]
    result = {
        "run_id": run_id, "started_at": started.isoformat(timespec="seconds"), "registry": r["registry"],
        "db": r["db_id"], "from": r["from"], "to": r["to"], "only": only,
        "ext": r["ext"], "ext_newest": newest, "old": r["old"], "new": r["new"],
        "dump": dumped,
        "work": dict(work, compared=existed),
        "control_rebase": {"methods": len(ctl["methods"]),
                           "verdicts": {v: ctl["summary"].get(v, 0) for v in VERDICTS}},
        "interceptors": {"rows": len(rows), "flagged": sum(1 for x in rows if x["flags"]),
                         "flags": _flag_counts(rows)},
        "calls": {"findings": len(calls), "flags": _flag_counts(calls)},
        "queries": {"findings": len(data["queries"]["findings"]), "unjudged": len(data["queries"]["masked"])},
        "bodies": {"copies": len(cases), "vendor_changed": sum(
            1 for c in cases if (c.get("change") or {}).get("old->new", "=") != "=")},
        "borrowed": {"findings": len(data["borrowed"]["findings"]), "checks": dict(sorted(Counter(
            f["check"] for f in data["borrowed"]["findings"]).items()))},
        "registers": {"findings": len(data["registers"]["findings"]), "checks": dict(sorted(Counter(
            f.get("check", "CHANGED") for f in data["registers"]["findings"]).items()))},
        "plan": {"counts": plan["counts"], "hidden": sum(plan["hidden"].values()), "json": plan_json, "md": plan_md,
                 "must_fix": [x for x in plan["items"] if x["group"] == "MUST_FIX"]},
        "reports": reports,
    }
    result["prepare_json"] = os.path.join(folder, "prepare.json")
    with io.open(result["prepare_json"], "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    return result


def screen(res):
    """The one screen of prepare (Russian, for a person)."""
    w = res["work"]
    if w["state"] == "created":
        work = "создана: копия ext под git, коммит %s «%s»" % (w["commit"], w["message"])
    else:
        work = "уже была, не тронута; control_rebase сверил с ней перенос"
    dump = res["dump"]
    source = "выгружено сейчас" if dump else "взята как есть (--no-dump)"
    ctl = res["control_rebase"]
    lines = [
        "база: %s" % res["db"] + (", только %s" % res["only"] if res.get("only") else ""),
        "релизы: %s → %s" % (res["from"], res["to"]),
        "ext: %s, выгрузка от %s — %s" % (res["ext"], res["ext_newest"] or "—", source),
        "work: %s — %s" % (w["path"], work),
        "control_rebase: методов %d; %s" % (ctl["methods"], ", ".join("%s %d" % kv for kv in ctl["verdicts"].items())),
        "interceptors: строк %d, с флагами %d%s" % (res["interceptors"]["rows"], res["interceptors"]["flagged"],
                                                   _flags_text(res["interceptors"]["flags"])),
        "calls: находок с флагами %d%s" % (res["calls"]["findings"], _flags_text(res["calls"]["flags"])),
        "queries: находок %d, не судится %d" % (res["queries"]["findings"], res["queries"]["unjudged"]),
        "bodies: копий &Вместо %d, оригинал у вендора изменился %d" % (res["bodies"]["copies"],
                                                                      res["bodies"]["vendor_changed"]),
        "borrowed: находок %d%s" % (res["borrowed"]["findings"], _flags_text(res["borrowed"]["checks"])),
        "registers: находок %d%s" % (res["registers"]["findings"], _flags_text(res["registers"]["checks"])),
    ]
    plan = _load(res["plan"]["json"])
    lines.extend(plan_mod.screen_lines(plan))
    lines.append("отчёты:")
    width = max(len(t) for t in TOOLS)
    lines.extend("  %-*s  %s" % (width, t, res["reports"][t]["md"]) for t in TOOLS)
    lines.append("plan.md: %s" % res["plan"]["md"])
    lines.append("prepare.json: %s" % res["prepare_json"])
    return "\n".join(lines)


def main(argv=None):
    fileio.console_utf8()
    ap = argparse.ArgumentParser(description="Подготовка к адаптации расширений базы из реестра: выгрузка, рабочая "
                                             "копия под git, control_rebase / interceptors / calls / queries / "
                                             "bodies / borrowed / registers и план адаптации")
    ap.add_argument("--db", required=True, help="база из реестра (id или alias)")
    ap.add_argument("--to", required=True, help="релиз, на который переносим (ключ cfe_rebase.releases)")
    ap.add_argument("--from", dest="from_", help="релиз, под который написаны расширения; по умолчанию "
                                                 "cfe_rebase.from базы")
    ap.add_argument("--registry", help="файл реестра; по умолчанию $%s или %s" % (registry.ENV, registry.DEFAULT))
    ap.add_argument("--no-dump", action="store_true", help="не выгружать: взять то, что уже лежит в ext (для базы с "
                                                           "cfe_rebase.proof_corpus — обязательно)")
    ap.add_argument("--names", help="выгрузить эти расширения (через запятую) вместо extensions базы")
    ap.add_argument("--only", help="отчёты только по этому расширению")
    args = ap.parse_args(argv)
    names = [n.strip() for n in args.names.split(",") if n.strip()] if args.names else None
    try:
        res = run(args.db, args.to, args.from_, args.registry, not args.no_dump, names, args.only,
                  log=lambda text: print(text, file=sys.stderr, flush=True))
    except PrepareError as exc:
        print("cfe-rebase prepare: " + exc.message, file=sys.stderr)
        if exc.detail:
            print(exc.detail, file=sys.stderr)
        sys.exit(exc.code)
    print(screen(res))
    return 0


# ------------------------------------------------------------------------------------------------------------------

def _dump(r, names, say):
    template = r["dump_template"]
    if not template:
        raise PrepareError(BAD_INPUT, "в реестре нет cfe_rebase.dump (команды выгрузки): выгрузите расширения в %s "
                                      "сами и запустите с --no-dump" % r["ext"])
    names = list(names or r["names"])
    if not names and "{names}" in json.dumps(template, ensure_ascii=False):
        raise PrepareError(BAD_INPUT, "у базы %s нет extensions в реестре: задайте --names" % r["db_id"])
    dump_dir = os.path.dirname(r["ext"])
    try:
        argv = registry.dump_argv(template, r["db_id"], names, dump_dir)
    except registry.RegistryError as exc:
        raise PrepareError(BAD_INPUT, str(exc))
    shown = subprocess.list2cmdline(argv)
    timeout = _dump_timeout()
    os.makedirs(dump_dir, exist_ok=True)
    say("выгрузка расширений: " + shown)
    try:
        code, stdout, stderr = _run_dump(argv, timeout)
    except OSError as exc:
        raise PrepareError(FAILED, "команда выгрузки не запустилась (%s): %s" % (exc, shown))
    if code is None:
        raise PrepareError(FAILED, "команда выгрузки не завершилась за %s с (%s, по умолчанию %d): она и запущенные "
                                   "ею процессы остановлены: %s" % (_seconds(timeout), DUMP_TIMEOUT_ENV, DUMP_TIMEOUT,
                                                                   shown), _tail(stderr) or _tail(stdout))
    if code:
        raise PrepareError(FAILED, "выгрузка завершилась с кодом %d: %s" % (code, shown),
                           _tail(stderr) or _tail(stdout))
    return {"cmd": argv, "names": names}


def _dump_timeout():
    raw = os.environ.get(DUMP_TIMEOUT_ENV, "").strip()
    if not raw:
        return DUMP_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        value = 0
    if not value > 0:
        raise PrepareError(BAD_INPUT, "%s=%s: ожидается число секунд больше нуля" % (DUMP_TIMEOUT_ENV, raw))
    return value


def _seconds(value):
    return "%d" % value if float(value).is_integer() else "%g" % value


def _run_dump(argv, timeout):
    """(exit code, or None after the timeout; stdout bytes; stderr bytes). The output goes to temporary files: a process
    the command starts (1cv8 from a script) inherits the handles, and with pipes subprocess.run(timeout=) on Windows
    still waits until that process closes them - a hung Designer would hang prepare and, in the MCP server, every later
    call. On the timeout the whole process tree is killed."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        extra = {} if os.name == "nt" else {"start_new_session": True}
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, **extra)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            code = None
        out.seek(0)
        err.seek(0)
        return code, out.read(), err.read()


def _kill_tree(proc):
    """Kill a process and every process it started; the direct child is killed whatever happens to the tree kill."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=60)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
        proc.wait(timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass


def _work(r, say):
    """{path, state: created | existing, commit, message}: the work copy is created once and never touched again."""
    path = r["work_path"]
    if os.path.exists(path):
        top = _git(["rev-parse", "--show-toplevel"], path) if os.path.isdir(path) else None
        if top is None or top.returncode or not top.stdout.strip():
            raise PrepareError(BAD_INPUT, "рабочая копия %s есть, но не под git: prepare её не трогает — уберите "
                                          "папку или сделайте в ней git init сами" % path)
        root = _decode(top.stdout).strip()
        if not _same_path(root, path):
            # inside another repository the work copy may be git-ignored: nothing of it could be rolled back
            raise PrepareError(BAD_INPUT, "рабочая копия %s есть, но это не корень своего git-репозитория: она лежит "
                                          "внутри репозитория %s — prepare её не трогает; сделайте в ней git init "
                                          "сами или задайте другую папку" % (path, os.path.normpath(root)))
        return {"path": path, "state": "existing"}
    say("рабочая копия: %s → %s" % (r["ext"], path))
    message = "before: %s %s" % (r["db_id"], r["from"])
    try:
        try:
            shutil.copytree(r["ext"], path)
        except OSError as exc:  # shutil.Error too: a long Windows path, no space, a locked file
            raise PrepareError(FAILED, "не удалось скопировать %s в рабочую копию %s: %s"
                               % (r["ext"], path, _copy_error(exc)))
        _git_ok(["init", "-q"], path)
        # a dump is byte-exact (BOM, CRLF): git must not normalize it in this repository
        _git_ok(["config", "core.autocrlf", "false"], path)
        # 1C dumps hold paths over 260 characters: without it git add fails with «Filename too long» on Windows
        _git_ok(["config", "core.longpaths", "true"], path)
        for key, value in zip(("user.name", "user.email"), GIT_USER):
            if _git(["config", key], path).returncode:
                _git_ok(["config", key, value], path)
        _git_ok(["add", "-A"], path)
        _git_ok(["commit", "-q", "-m", message], path)
        commit = _git_ok(["rev-parse", "--short", "HEAD"], path).stdout.decode("ascii", "replace").strip()
    except PrepareError as exc:
        # our own fresh copy: the next run starts clean instead of refusing a half-made folder as «not under git»
        if os.path.exists(path):
            try:
                _rmtree(path)
            except OSError as rm_exc:
                exc.message += "; недоделанную копию %s удалить не удалось (%s) — уберите её сами" % (path, rm_exc)
        raise
    return {"path": path, "state": "created", "commit": commit, "message": message}


def _copy_error(exc):
    """The first failure of shutil.copytree (shutil.Error carries [(src, dst, why)] of every failed file)."""
    errors = exc.args[0] if isinstance(exc, shutil.Error) and exc.args and isinstance(exc.args[0], list) else None
    if not errors:
        return str(exc)
    src, _, why = errors[0]
    return "%s: %s" % (src, why) + (" (и ещё файлов: %d)" % (len(errors) - 1) if len(errors) > 1 else "")


def _analyze(r, folder, work, only, say):
    """The seven tools of TOOLS into <folder>/<tool>/; {tool: {json, md}}."""
    dumps = ["--config-old", r["old"], "--config-new", r["new"]]
    argvs = {
        "control_rebase": ["--ext", r["ext"], "--old", r["old"], "--new", r["new"]] +
                          (["--work", work] if work else []) + (["--only", only] if only else []),
        "interceptors": ["--ext-root", r["ext"]] + dumps + (["--ext", only] if only else []),
        "calls": ["--ext-root", r["ext"]] + dumps + (["--ext", only] if only else []),
        "queries": ["--ext-root", r["ext"]] + dumps + (["--ext", only] if only else []),
        "bodies": ["--ext-root", r["ext"]] + dumps + (["--ext", only] if only else []),
        "borrowed": ["--ext-root", r["ext"]] + dumps + (["--ext", only] if only else []),
        "registers": ["--ext-root", r["ext"]] + dumps + (["--ext", only] if only else []),
    }
    reports = {}
    for tool in TOOLS:
        dest = os.path.join(folder, tool)
        say("%s…" % tool)
        module = importlib.import_module("." + tool, __package__)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                module.main(argvs[tool] + ["--out", dest])
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise PrepareError(BAD_INPUT, "%s: %s" % (tool, exc.code))
        name = REPORT_NAMES[tool]
        reports[tool] = {"json": os.path.join(dest, name + ".json"), "md": os.path.join(dest, name + ".md")}
    return reports


def _git(args, cwd):
    return subprocess.run(["git", "-c", "core.quotepath=false"] + list(args), cwd=cwd, capture_output=True,
                          stdin=subprocess.DEVNULL)


def _git_ok(args, cwd):
    try:
        proc = _git(args, cwd)
    except OSError as exc:
        raise PrepareError(FAILED, "git не запустился (%s)" % exc)
    if proc.returncode:
        raise PrepareError(FAILED, "git %s завершился с кодом %d в %s" % (" ".join(args), proc.returncode, cwd),
                           _tail(proc.stderr) or _tail(proc.stdout))
    return proc


def _tail(data):
    text = _decode(data).strip()
    return "\n".join(text.splitlines()[-TAIL_LINES:]) if text else ""


def _decode(data):
    """Output of an external program: UTF-8, else the console code page of Windows (cp866 for Russian)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("oem" if os.name == "nt" else "latin-1", "replace")


def _inside(path, root):
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def _same_path(a, b):
    """One folder whatever the spelling: git prints C:/x/y, the registry may give a short 8.3 name or a symlink."""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _flag_counts(rows):
    return dict(sorted(Counter(f for x in rows for f in set(x.get("flags") or [])).items()))


def _flags_text(counts):
    return "; " + ", ".join("%s=%d" % kv for kv in counts.items()) if counts else ""


def _rmtree(path):
    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onexc=retry)


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


if __name__ == "__main__":
    main()
