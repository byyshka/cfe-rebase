# -*- coding: utf-8 -*-
"""A registry of databases over the synthetic corpus, for the tests of registry, prepare, the CLI and the server.

make(base) writes <base>/registry.json and <base>/fake_dump.py: the dump command of the registry runs that script with
the current Python as `{id} {names} {dir}`; it copies the named extensions of tests/corpus/synthetic/ext into
{dir}/xml/<name> and records its arguments in {dir}/dump-args.json. $CFE_FAKE_DUMP_FAIL makes it fail with code 5,
$CFE_FAKE_DUMP_HANG=<seconds> makes it start a child holding its output and sleep that long first (a hung Designer).
The task folder has a space and Cyrillic in its name; the registry carries foreign keys with a password that must
never reach a message.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "synthetic")
OLD = os.path.join(CORPUS, "old")
NEW = os.path.join(CORPUS, "new")
EXT = os.path.join(CORPUS, "ext")
WORK = os.path.join(CORPUS, "work")
EXT_MAIN = "Синт_Основное"
EXT_PRINT = "Синт_Печать"
DB = "demo-ut-27"
FROM = "11.5.22.1"
TO = "11.5.27.1"
TASK = "задача demo ut"
SECRET = "pa$$-SECRET-42"

FAKE_DUMP = r'''# -*- coding: utf-8 -*-
import json, os, shutil, subprocess, sys, time
db, names, out = sys.argv[1:4]
if os.environ.get("CFE_FAKE_DUMP_FAIL"):
    sys.stderr.buffer.write(("fake dump: база " + db + " недоступна\n").encode("utf-8"))
    sys.exit(5)
if os.environ.get("CFE_FAKE_DUMP_HANG"):
    # a hung Designer the script started: it inherits the output handles of the script
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(%s)" % os.environ["CFE_FAKE_DUMP_HANG"]])
    sys.stderr.buffer.write("fake dump: конфигуратор завис\n".encode("utf-8"))
    sys.stderr.flush()
    time.sleep(float(os.environ["CFE_FAKE_DUMP_HANG"]))
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "dump-args.json"), "w", encoding="utf-8") as fh:
    json.dump(sys.argv[1:], fh, ensure_ascii=False)
dst = os.path.join(out, "xml")
for name in names.split(","):
    shutil.rmtree(os.path.join(dst, name), ignore_errors=True)
    shutil.copytree(os.path.join(@EXT@, name), os.path.join(dst, name))
print("dumped", db, names)
'''


def make(base, db_cfg=None, extensions=None, dump=True, releases=None):
    """(registry path, task folder). db_cfg replaces the database's cfe_rebase object; releases - the registry's."""
    os.makedirs(base, exist_ok=True)
    task = os.path.join(base, TASK)
    fake = os.path.join(base, "fake_dump.py")
    with open(fake, "w", encoding="utf-8") as fh:
        fh.write(FAKE_DUMP.replace("@EXT@", repr(EXT)))
    root = {"releases": releases if releases is not None else
            # one release with forward slashes: the registry may spell paths either way
            {FROM: OLD, TO: NEW.replace("\\", "/")}}
    if dump:
        root["dump"] = '"%s" "%s" {id} {names} {dir}' % (sys.executable, fake)
    data = {
        "v8path": "C:\\Program Files\\1cv8\\bin\\1cv8.exe",
        "default": DB,
        "cfe_rebase": root,
        "databases": [
            {"id": "erp", "name": "ERP", "type": "server", "server": "srv", "ref": "erp", "user": "admin",
             "password": SECRET, "extensions": [{"name": "ЕГ_Сборка", "src": "C:\\src\\ЕГ_Сборка"}]},
            {"id": DB, "name": "Демо УТ", "type": "server", "server": "srv", "ref": "demo", "user": "admin",
             "password": SECRET, "aliases": ["демо", "DEMO"],
             "extensions": extensions if extensions is not None else
             [EXT_MAIN, {"name": EXT_PRINT, "src": "C:\\src\\" + EXT_PRINT, "build": True}],
             "cfe_rebase": db_cfg if db_cfg is not None else
             {"from": FROM, "task_dir": task, "ext_dump": "before\\xml"}},
        ],
    }
    path = os.path.join(base, "registry.json")
    # as the user keeps it: no BOM, CRLF, indent 2
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path, task
