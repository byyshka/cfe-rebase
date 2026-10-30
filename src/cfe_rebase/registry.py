# -*- coding: utf-8 -*-
"""Registry of infobases: one JSON file maps a database id to every path cfe-rebase needs.

    {"cfe_rebase": {"releases": {"<release>": "<vendor dump>", ...},
                    "dump": "<command that dumps the extensions of a database: {id} {names} {dir}>"},
     "databases": [{"id": "...", "aliases": [...], "extensions": ["<name>" | {"name": "<name>", ...}, ...],
                    "cfe_rebase": {"from": "<release>", "task_dir": "...",
                                   "ext_dump": "old\\xml", "work": "work", "reports": "reports",
                                   "ext_all": "all\\xml", "proof_corpus": true}}]}

proof_corpus: true marks ext_dump as a reference dump (a proof corpus) that is never re-dumped: prepare runs on it
only without the dump (--no-dump / dump=False); a missing key allows the dump.
ext_all: a dump of ALL extensions of the database (no default, relative to task_dir); prepare checks the ones outside
the task's perimeter (ext_dump) with borrowed and control_rebase. Never dumped by prepare.
Only these keys are read. Everything else in the file (connection strings, users, passwords) is ignored and never
appears in a message. The file: --registry, else $CFE_REBASE_REGISTRY, else ~/.cfe-rebase/registry.json.
"""
import json
import os
from datetime import datetime

ENV = "CFE_REBASE_REGISTRY"
DEFAULT = os.path.join("~", ".cfe-rebase", "registry.json")
# database key -> default, relative to task_dir
DEFAULTS = {"ext_dump": os.path.join("old", "xml"), "work": "work", "reports": "reports"}


class RegistryError(Exception):
    """The registry is missing, unreadable or lacks a key; the text names the key, never a value of other keys."""


def registry_path(path=None):
    """The registry file: the argument, else $CFE_REBASE_REGISTRY, else ~/.cfe-rebase/registry.json."""
    return _norm(path or os.environ.get(ENV) or DEFAULT)


def load(path=None):
    """(absolute path, parsed JSON object) of the registry."""
    p = registry_path(path)
    if not os.path.isfile(p):
        raise RegistryError("реестр баз не найден: %s (путь задаётся --registry, переменной %s, по умолчанию %s)"
                            % (p, ENV, DEFAULT))
    try:
        with open(p, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RegistryError("реестр баз %s не читается как JSON: %s" % (p, exc))
    if not isinstance(data, dict):
        raise RegistryError("реестр баз %s: на верхнем уровне ожидается объект JSON" % p)
    return p, data


def find_database(data, db, path="реестр"):
    """The database entry whose id or one of aliases equals db (case-insensitive)."""
    dbs = [d for d in data.get("databases") or [] if isinstance(d, dict)]
    if not dbs:
        raise RegistryError("в реестре %s нет списка databases" % path)
    key = str(db).strip().casefold()
    for d in dbs:
        names = [d.get("id")] + list(d.get("aliases") or [])
        if any(isinstance(n, str) and n.strip().casefold() == key for n in names):
            return d
    known = ", ".join(str(d["id"]) for d in dbs if d.get("id"))
    raise RegistryError("базы «%s» нет в реестре %s (ищется по id и aliases); есть: %s" % (db, path, known))


def extension_names(entry):
    """Names from databases[].extensions: a string is a name, an object gives its "name" (other fields ignored)."""
    names = []
    for e in entry.get("extensions") or []:
        name = e.get("name") if isinstance(e, dict) else e
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def split_command(text):
    """A command line template into arguments, Windows style: whitespace separates, double or single quotes group
    (and are dropped), backslashes are literal - C:\\dumps and paths with spaces survive."""
    args, cur, quote, quoted = [], [], None, False
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in "\"'":
            quote, quoted = ch, True
        elif ch.isspace():
            if cur or quoted:
                args.append("".join(cur))
            cur, quoted = [], False
        else:
            cur.append(ch)
    if quote:
        raise RegistryError("cfe_rebase.dump: незакрытая кавычка %s" % quote)
    if cur or quoted:
        args.append("".join(cur))
    return args


def dump_argv(template, db_id, names, dump_dir):
    """argv of the dump command: the template is split first, then {id}, {names} (comma-separated) and {dir} are
    substituted inside each argument, so a value with spaces or Cyrillic stays one argument. The template may also
    be a JSON list of arguments."""
    args = [str(a) for a in template] if isinstance(template, list) else split_command(str(template))
    if not args:
        raise RegistryError("cfe_rebase.dump: пустая команда")
    subst = (("{id}", str(db_id)), ("{names}", ",".join(names)), ("{dir}", dump_dir))
    out = []
    for a in args:
        for k, v in subst:
            a = a.replace(k, v)
        out.append(a)
    return out


def resolve(registry_path=None, db=None, to=None, from_=None, need_old=True):
    """Every path of one database for one move old -> new.

    to may be None (then new is None); from_ overrides the database's cfe_rebase.from. need_old=False - the caller
    needs no old release (lint, or old given explicitly): from and old are resolved only when the registry has them,
    else None, never an error. Keys: db_id, from, to, registry, task_dir, ext (dump of the extensions), old, new
    (vendor dumps), work (None while the folder does not exist), work_path (where it is or will be), out (reports),
    names (extensions), dump_cmd (argv with the registry's names, None without cfe_rebase.dump), dump_template,
    proof_corpus (cfe_rebase.proof_corpus of the database is true: ext_dump is never re-dumped), ext_all (dump of all
    extensions of the database, None without cfe_rebase.ext_all)."""
    path, data = load(registry_path)
    if not db:
        raise RegistryError("не задана база (--db / db)")
    entry = find_database(data, db, path)
    db_id = str(entry.get("id") or db)
    cfg = entry.get("cfe_rebase")
    if not isinstance(cfg, dict):
        raise RegistryError("у базы %s нет ключа cfe_rebase в реестре %s" % (db_id, path))
    root = data.get("cfe_rebase") if isinstance(data.get("cfe_rebase"), dict) else {}
    releases = root.get("releases") if isinstance(root.get("releases"), dict) else {}
    base = os.path.dirname(path)

    def release(rel, role):
        if rel not in releases:
            known = ", ".join(sorted(releases)) or "ни одного"
            raise RegistryError("релиза %s (%s) нет в cfe_rebase.releases реестра %s; есть: %s"
                                % (rel, role, path, known))
        return _norm(releases[rel], base)

    frm = str(from_ or cfg.get("from") or "")
    if need_old:
        if not frm:
            raise RegistryError("у базы %s нет cfe_rebase.from (релиз, под который написаны расширения): задайте его "
                                "в реестре или --from / from_release" % db_id)
        old = release(frm, "from")
    else:
        old = _norm(releases[frm], base) if frm in releases else None
    new = release(str(to), "to") if to else None
    task_dir = _norm(cfg["task_dir"], base) if cfg.get("task_dir") else None
    paths = {}
    for key, default in DEFAULTS.items():
        value = cfg.get(key) or default
        if not os.path.isabs(os.path.expanduser(str(value))) and not task_dir:
            raise RegistryError("у базы %s нет cfe_rebase.task_dir, а путь cfe_rebase.%s относительный (%s)"
                                % (db_id, key, value))
        paths[key] = _norm(value, task_dir)
    ext_all = cfg.get("ext_all") or None
    if ext_all and not os.path.isabs(os.path.expanduser(str(ext_all))) and not task_dir:
        raise RegistryError("у базы %s нет cfe_rebase.task_dir, а путь cfe_rebase.ext_all относительный (%s)"
                            % (db_id, ext_all))
    names = extension_names(entry)
    template = root.get("dump") or None
    ext = paths["ext_dump"]
    proof = cfg.get("proof_corpus")
    return {"db_id": db_id, "from": frm or None, "to": str(to) if to else None, "registry": path, "task_dir": task_dir,
            "ext": ext, "old": old, "new": new,
            "work": paths["work"] if os.path.isdir(paths["work"]) else None, "work_path": paths["work"],
            "out": paths["reports"], "names": names,
            "dump_cmd": dump_argv(template, db_id, names, os.path.dirname(ext)) if template else None,
            "dump_template": template,
            "proof_corpus": proof is True or str(proof).strip().casefold() == "true",
            "ext_all": _norm(ext_all, task_dir) if ext_all else None}


def ext_newest(ext_dir):
    """Local ISO time (with zone) of the newest file under the dump(s) of the extensions; None without files."""
    newest, stack = None, [ext_dir] if isinstance(ext_dir, str) else list(ext_dir)
    while stack:
        try:
            it = os.scandir(stack.pop())
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file():
                        m = e.stat().st_mtime
                        newest = m if newest is None or m > newest else newest
                except OSError:
                    continue
    return datetime.fromtimestamp(newest).astimezone().isoformat(timespec="seconds") if newest is not None else None


def _norm(value, base=None):
    """~ expanded, relative to base, one spelling of separators."""
    p = os.path.expanduser(str(value))
    if base and not os.path.isabs(p):
        p = os.path.join(base, p)
    return os.path.normpath(os.path.abspath(p))
