# -*- coding: utf-8 -*-
"""cfe-rebase <command> [arguments of the command]: one entry point for every tool of the package."""
import argparse
import contextlib
import importlib
import io
import os
import sys
import uuid

from . import __version__, fileio, registry

# command -> (module, one-line description)
COMMANDS = {
    "interceptors": ("interceptors", "реестр перехватов расширений против нового релиза и мёртвый код расширений"),
    "calls": ("calls", "вызовы методов конфигурации из расширений: наличие метода и число параметров в новом релизе"),
    "bodies": ("bodies", "замороженные копии &Вместо против тела метода в старом и новом релизе"),
    "queries": ("queries", "тексты запросов, схемы СКД и имена метаданных в строках против нового релиза"),
    "merge-copy": ("merge_copy", "перенос одной копии &Вместо на тело нового релиза трёхсторонним слиянием"),
    "control-rebase": ("control_rebase", "перенос всех &ИзменениеИКонтроль на новый релиз с проверкой контроля"),
    "method-diff": ("method_diff", "что вендор изменил в каждом перехваченном методе: блоки с номерами строк"),
    "hygiene-added": ("hygiene_added", "эвристики гигиены кода только по добавленным строкам"),
    "ext-check-diff": ("ext_check_diff", "разница двух прогонов /CheckConfig (папки NN.txt + index.json)"),
    "applied": ("applied", "применённая копия модуля: #Удаление и маркеры опустошены, номера строк сохранены"),
    "lint": ("lint", "проверка рабочей копии перед загрузкой: маркеры слияния, структура модулей, потерянные файлы"),
    "borrowed": ("borrowed", "заимствованные объекты против нового релиза: удалённые реквизиты, ресурсы, значения, типы"),
    "registers": ("registers", "запись в регистры, у которых новый релиз изменил измерения: что код не заполняет"),
    "reads": ("reads", "чтение регистров в запросах, у которых новый релиз изменил состав или регистраторов"),
    "prepare": ("prepare", "подготовка к адаптации базы из реестра: выгрузка, рабочая копия под git, семь отчётов и план"),
    "mcp": ("mcp_server", "MCP-сервер (stdio) для нейросетей: короткие ответы со ссылками на отчёты"),
}

# --db: command -> {registry key: flag of the command}; a flag missing on the command line is taken from the registry
_DUMPS = {"ext": "--ext-root", "old": "--config-old", "new": "--config-new", "out": "--out"}
DB_FLAGS = {
    "interceptors": _DUMPS, "calls": _DUMPS, "bodies": _DUMPS, "queries": _DUMPS, "borrowed": _DUMPS, "registers": _DUMPS,
    "reads": _DUMPS,
    "control-rebase": {"ext": "--ext", "old": "--old", "new": "--new", "out": "--out", "work": "--work"},
    "method-diff": {"ext": "--ext", "old": "--old", "new": "--new", "out": "--out"},
    "lint": {"work": "--work", "ext": "--ext", "out": "--out"},
}
# every long option of those commands: a parser with the same names resolves abbreviations as the command does
OPTIONS = {
    "interceptors": ("--ext-root", "--ext", "--skip", "--config-old", "--config-new", "--order", "--out"),
    "calls": ("--ext-root", "--ext", "--skip", "--config-old", "--config-new", "--out"),
    "bodies": ("--ext-root", "--ext", "--skip", "--config-old", "--config-new", "--out", "--code-only",
               "--max-hunks"),
    "queries": ("--ext-root", "--ext", "--skip", "--config-old", "--config-new", "--out"),
    "borrowed": ("--ext-root", "--ext", "--skip", "--config-old", "--config-new", "--out"),
    "registers": ("--ext-root", "--ext", "--skip", "--config-old", "--config-new", "--out"),
    "reads": ("--ext-root", "--ext", "--skip", "--config-old", "--config-new", "--out"),
    "control-rebase": ("--ext", "--old", "--new", "--out", "--work", "--only"),
    "method-diff": ("--ext", "--old", "--new", "--out", "--only", "--method"),
    "lint": ("--work", "--ext", "--only", "--out"),
}
DB_HELP = """
реестр баз (вместо путей; явные пути перекрывают взятые из реестра):
  --db ID          база из реестра (id или alias)
  --to RELEASE     релиз, на который переносим (ключ cfe_rebase.releases); нужен, если не задан путь new
  --from RELEASE   релиз, под который написаны расширения; по умолчанию cfe_rebase.from базы
  --registry FILE  файл реестра; по умолчанию $%s или %s""" % (registry.ENV, registry.DEFAULT)


def usage():
    width = max(len(c) for c in COMMANDS)
    lines = ["usage: cfe-rebase <команда> [аргументы команды]", "       cfe-rebase --version", "", "Команды:"]
    lines.extend("  %-*s  %s" % (width, c, d) for c, (_, d) in COMMANDS.items())
    lines.extend(["", "Аргументы команды: cfe-rebase <команда> --help",
                  "Пути из реестра баз: %s принимают --db <id> --to <релиз> [--from <релиз>] [--registry <файл>]"
                  % ", ".join(DB_FLAGS)])
    return "\n".join(lines)


def main(argv=None):
    fileio.console_utf8()
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        print(usage(), file=sys.stderr)
        sys.exit(2)
    name, rest = argv[0], argv[1:]
    if name == "--version":
        print("cfe-rebase " + __version__)
        return 0
    if name in ("-h", "--help"):
        print(usage())
        return 0
    if name not in COMMANDS:
        print("cfe-rebase: неизвестная команда «%s»\n\n%s" % (name, usage()), file=sys.stderr)
        sys.exit(2)
    module = importlib.import_module("." + COMMANDS[name][0], __package__)
    help_wanted = name in DB_FLAGS and any(a in ("-h", "--help") for a in rest)
    ext_line = None
    if name in DB_FLAGS and not help_wanted:
        try:
            rest, ext_line = with_registry(name, rest)
        except registry.RegistryError as exc:
            print("cfe-rebase %s: %s" % (name, exc), file=sys.stderr)
            sys.exit(2)
    # argparse of the command takes its usage name from argv[0]: show «cfe-rebase <command>»
    saved = sys.argv[:1]
    sys.argv[:1] = ["cfe-rebase " + name]
    try:
        code = module.main(rest)
    except SystemExit as exc:
        if help_wanted and exc.code in (None, 0):
            print(DB_HELP)
        raise
    finally:
        sys.argv[:1] = saved
    if ext_line:
        print(ext_line)
    return code


def with_registry(name, argv):
    """argv of a path command with --db/--to/--from/--registry taken out and the path flags the command line lacks
    filled in from the registry of databases. Returns (argv for the command, «ext: …» line or None without --db)."""
    ap = argparse.ArgumentParser(prog="cfe-rebase " + name, add_help=False, allow_abbrev=False)
    ap.add_argument("--db")
    ap.add_argument("--to")
    ap.add_argument("--from", dest="from_")
    ap.add_argument("--registry")
    ns, rest = ap.parse_known_args(argv)
    if not ns.db:
        given = [o for o, v in (("--to", ns.to), ("--from", ns.from_), ("--registry", ns.registry)) if v]
        if given:
            raise registry.RegistryError("%s — только вместе с --db" % ", ".join(given))
        return rest, None
    flags = DB_FLAGS[name]
    explicit = _explicit(name, rest)
    if "new" in flags and explicit.get(flags["new"]) is None and not ns.to:
        raise registry.RegistryError("с --db нужен --to: релиз, на который переносим (ключ cfe_rebase.releases)")
    # a release is resolved only when the command takes old from the registry: lint needs none
    need_old = "old" in flags and explicit.get(flags["old"]) is None
    paths = registry.resolve(ns.registry, ns.db, ns.to, ns.from_, need_old=need_old)
    # every call in its own folder under reports, as in the MCP server: control-rebase and method-diff both write
    # report.json, and candidates of an earlier run must not linger next to a new report
    paths["out"] = os.path.join(paths["out"], "%s-%s" % (name, uuid.uuid4().hex[:8]))
    head = []
    for key, flag in flags.items():
        if explicit.get(flag) is None and paths[key]:
            head += [flag, paths[key]]
    ext = explicit.get(flags["ext"]) or [paths["ext"]]
    return head + rest, "ext: %s, выгрузка от %s" % (", ".join(ext), registry.ext_newest(ext) or "— (файлов нет)")


def _explicit(name, argv):
    """{long option: values or None} as the command will parse argv; {} when argparse refuses it (the command then
    reports the error itself)."""
    ap = argparse.ArgumentParser(add_help=False)
    for opt in OPTIONS[name]:
        ap.add_argument(opt, dest=opt, nargs="*", action="extend")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            ns, _ = ap.parse_known_args(argv)
    except SystemExit:
        return {}
    return {opt: getattr(ns, opt) for opt in OPTIONS[name]}
