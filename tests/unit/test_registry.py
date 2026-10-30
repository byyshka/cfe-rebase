# -*- coding: utf-8 -*-
"""Registry of databases (cfe_rebase.registry) and --db of the CLI: path resolution, errors that name the key and never
a password, the dump command template, the age of the dump, and the same reports with --db as with explicit paths.

    python -m unittest tests.unit.test_registry
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from cfe_rebase import cli, control_rebase, registry
from tests.corpus.normalize import load
from tests.unit import registry_fixture as fx

HAVE_GIT = shutil.which("git") is not None


class Resolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_reg_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg, self.task = fx.make(self.tmp)

    def error(self, *args, **kwargs):
        with self.assertRaises(registry.RegistryError) as cm:
            registry.resolve(*args, **kwargs)
        text = str(cm.exception)
        self.assertNotIn(fx.SECRET, text)
        return text

    def test_paths(self):
        r = registry.resolve(self.reg, fx.DB, fx.TO)
        self.assertEqual((r["db_id"], r["from"], r["to"]), (fx.DB, fx.FROM, fx.TO))
        self.assertEqual(r["old"], fx.OLD)
        self.assertEqual(r["new"], fx.NEW)  # written with forward slashes in the registry
        self.assertEqual(r["task_dir"], self.task)
        self.assertEqual(r["ext"], os.path.join(self.task, "before", "xml"))
        self.assertEqual(r["out"], os.path.join(self.task, "reports"))
        self.assertIsNone(r["work"])
        self.assertEqual(r["work_path"], os.path.join(self.task, "work"))
        os.makedirs(r["work_path"])
        self.assertEqual(registry.resolve(self.reg, fx.DB, fx.TO)["work"], r["work_path"])
        self.assertEqual(r["registry"], os.path.normpath(self.reg))

    def test_extensions_as_strings_and_objects(self):
        r = registry.resolve(self.reg, fx.DB, fx.TO)
        self.assertEqual(r["names"], [fx.EXT_MAIN, fx.EXT_PRINT])
        entry = {"extensions": ["А", {"name": "Б", "src": "C:\\x"}, {"src": "без имени"}, 7, " ", {"name": " В "}]}
        self.assertEqual(registry.extension_names(entry), ["А", "Б", "В"])

    def test_dump_command(self):
        r = registry.resolve(self.reg, fx.DB, fx.TO)
        self.assertEqual(r["dump_cmd"], [sys.executable, os.path.join(self.tmp, "fake_dump.py"), fx.DB,
                                         fx.EXT_MAIN + "," + fx.EXT_PRINT, os.path.join(self.task, "before")])
        self.assertIn(" ", r["dump_cmd"][-1])  # a path with a space stays one argument

    def test_lookup_by_alias_any_case(self):
        for name in ("демо", "ДЕМО", "demo", "DEMO-UT-27"):
            self.assertEqual(registry.resolve(self.reg, name, fx.TO)["db_id"], fx.DB, name)

    def test_to_is_optional_and_from_overrides(self):
        r = registry.resolve(self.reg, fx.DB, None)
        self.assertIsNone(r["new"])
        r = registry.resolve(self.reg, fx.DB, fx.FROM, from_=fx.TO)
        self.assertEqual((r["old"], r["new"]), (fx.NEW, fx.OLD))

    def test_defaults_absolute_and_home(self):
        reg, task = fx.make(os.path.join(self.tmp, "b"), db_cfg={"from": fx.FROM, "task_dir": "~/задачи/demo"})
        home = os.path.join(self.tmp, "home")
        with mock.patch.dict(os.environ, {"USERPROFILE": home, "HOME": home}):
            r = registry.resolve(reg, fx.DB, fx.TO)
        task = os.path.join(home, "задачи", "demo")
        self.assertEqual(r["task_dir"], task)
        self.assertEqual(r["ext"], os.path.join(task, "old", "xml"))
        self.assertEqual(r["work_path"], os.path.join(task, "work"))
        self.assertEqual(r["out"], os.path.join(task, "reports"))
        cfg = {"from": fx.FROM, "ext_dump": fx.EXT.replace("\\", "/"), "work": os.path.join(self.tmp, "w"),
               "reports": os.path.join(self.tmp, "r")}
        reg, _ = fx.make(os.path.join(self.tmp, "c"), db_cfg=cfg)
        r = registry.resolve(reg, fx.DB, fx.TO)  # every path absolute: task_dir is not needed
        self.assertEqual((r["ext"], r["work_path"], r["out"]), (fx.EXT, cfg["work"], cfg["reports"]))

    def test_proof_corpus_flag(self):
        self.assertIs(registry.resolve(self.reg, fx.DB, fx.TO)["proof_corpus"], False)  # no key: the dump is allowed
        for value, want in ((True, True), ("true", True), (False, False), ("false", False)):
            with self.subTest(value):
                reg, _ = fx.make(os.path.join(self.tmp, "p%s" % value), db_cfg={
                    "from": fx.FROM, "task_dir": self.task, "ext_dump": "before\\xml", "proof_corpus": value})
                self.assertIs(registry.resolve(reg, fx.DB, fx.TO)["proof_corpus"], want)

    def test_ext_all(self):
        self.assertIsNone(registry.resolve(self.reg, fx.DB, fx.TO)["ext_all"])
        reg, task = fx.make(os.path.join(self.tmp, "all"), db_cfg={"from": fx.FROM, "task_dir": "задача",
                                                                    "ext_all": "все\\xml"})
        self.assertEqual(registry.resolve(reg, fx.DB, fx.TO)["ext_all"],
                         os.path.join(self.tmp, "all", "задача", "все", "xml"))
        reg, _ = fx.make(os.path.join(self.tmp, "abs"), db_cfg={"from": fx.FROM, "ext_dump": fx.EXT, "work": fx.WORK,
                                                                 "reports": self.tmp, "ext_all": fx.EXT})
        self.assertEqual(registry.resolve(reg, fx.DB, fx.TO)["ext_all"], fx.EXT)
        reg, _ = fx.make(os.path.join(self.tmp, "rel"), db_cfg={"from": fx.FROM, "ext_dump": fx.EXT, "work": fx.WORK,
                                                                 "reports": self.tmp, "ext_all": "все"})
        self.assertIn("cfe_rebase.ext_all", self.error(reg, fx.DB, fx.TO))

    def test_old_release_only_when_needed(self):
        # lint and the tools given old explicitly need no release: no cfe_rebase.from, an empty releases list
        reg, _ = fx.make(os.path.join(self.tmp, "b"), releases={}, db_cfg={"task_dir": self.task})
        r = registry.resolve(reg, fx.DB, None, need_old=False)
        self.assertEqual((r["from"], r["old"], r["new"]), (None, None, None))
        self.assertEqual(r["ext"], os.path.join(self.task, "old", "xml"))
        self.assertIn("cfe_rebase.from", self.error(reg, fx.DB, None))  # still an error when old is needed
        r = registry.resolve(self.reg, fx.DB, None, need_old=False)
        self.assertEqual((r["from"], r["old"]), (fx.FROM, fx.OLD))  # resolved when the registry has it

    def test_registry_file_lookup(self):
        with mock.patch.dict(os.environ, {registry.ENV: self.reg}):
            self.assertEqual(registry.resolve(None, fx.DB, fx.TO)["db_id"], fx.DB)
        home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(home, ".cfe-rebase"))
        shutil.copyfile(self.reg, os.path.join(home, ".cfe-rebase", "registry.json"))
        env = {"USERPROFILE": home, "HOME": home}
        with mock.patch.dict(os.environ, env):
            os.environ.pop(registry.ENV, None)
            self.assertEqual(registry.registry_path(), os.path.join(home, ".cfe-rebase", "registry.json"))
            self.assertEqual(registry.resolve(None, fx.DB, fx.TO)["db_id"], fx.DB)

    def test_bom_is_accepted(self):
        with open(self.reg, "rb") as fh:
            data = fh.read()
        with open(self.reg, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + data)
        self.assertEqual(registry.resolve(self.reg, fx.DB, fx.TO)["db_id"], fx.DB)

    def test_errors_name_the_key(self):
        missing = os.path.join(self.tmp, "нет", "registry.json")
        self.assertIn(missing, self.error(missing, fx.DB, fx.TO))
        text = self.error(self.reg, "nope", fx.TO)
        self.assertIn("«nope»", text)
        self.assertIn("erp, demo-ut-27", text)
        self.assertIn("11.5.99", self.error(self.reg, fx.DB, "11.5.99"))
        self.assertIn("cfe_rebase.releases", self.error(self.reg, fx.DB, fx.TO, from_="1.0"))
        self.assertIn("cfe_rebase", self.error(self.reg, "erp", fx.TO))  # a database without the package's key
        reg, _ = fx.make(os.path.join(self.tmp, "b"), db_cfg={"task_dir": self.task})
        self.assertIn("cfe_rebase.from", self.error(reg, fx.DB, fx.TO))
        reg, _ = fx.make(os.path.join(self.tmp, "c"), db_cfg={"from": fx.FROM})
        self.assertIn("task_dir", self.error(reg, fx.DB, fx.TO))
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write('{"databases": [{"id": "x", "password": "%s"' % fx.SECRET)
        self.assertIn("JSON", self.error(bad, "x", fx.TO))


class Command(unittest.TestCase):
    def test_split(self):
        self.assertEqual(registry.split_command(r'pwsh -File "C:\My Scripts\backup.ps1" -DbId {id} -X ""'),
                         ["pwsh", "-File", r"C:\My Scripts\backup.ps1", "-DbId", "{id}", "-X", ""])
        self.assertEqual(registry.split_command(r"a 'b c'd -P=" + '"x y"'), ["a", "b cd", "-P=x y"])
        with self.assertRaises(registry.RegistryError):
            registry.split_command('a "b')

    def test_substitution_inside_arguments(self):
        argv = registry.dump_argv(r'pwsh -File C:\backup.ps1 -DbId {id} -Names {names} -OutDir "{dir}"', "demo",
                                  ["ЕГ_доработки", "Второе"], r"C:\задача с пробелом\before")
        self.assertEqual(argv, ["pwsh", "-File", r"C:\backup.ps1", "-DbId", "demo", "-Names", "ЕГ_доработки,Второе",
                                "-OutDir", r"C:\задача с пробелом\before"])
        self.assertEqual(registry.dump_argv(["x y", "{dir}"], "d", [], r"C:\a b"), ["x y", r"C:\a b"])


class ExtNewest(unittest.TestCase):
    def test_newest_file(self):
        tmp = tempfile.mkdtemp(prefix="cfe_newest_")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertIsNone(registry.ext_newest(tmp))
        self.assertIsNone(registry.ext_newest(os.path.join(tmp, "нет")))
        stamps = {"a.xml": 1_700_000_000, os.path.join("Ext", "Глубоко", "Module.bsl"): 1_760_000_000}
        for rel, ts in stamps.items():
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write("x")
            os.utime(path, (ts, ts))
        got = datetime.fromisoformat(registry.ext_newest(tmp))
        self.assertIsNotNone(got.tzinfo)
        self.assertEqual(got.timestamp(), 1_760_000_000)
        self.assertEqual(registry.ext_newest([os.path.join(tmp, "a.xml"), tmp]), registry.ext_newest(tmp))


def run_cli(argv):
    """cli.main(argv): (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv) or 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            if isinstance(exc.code, str):
                err.write(exc.code)
    return code, out.getvalue(), err.getvalue()


@unittest.skipUnless(HAVE_GIT, "git is not on PATH: control_rebase merges with git merge-file")
class CliDb(unittest.TestCase):
    """--db fills the missing paths: the reports equal those of the same command with explicit paths."""
    maxDiff = None
    # command -> (module report name, explicit argv without --out)
    RUNS = {
        "control-rebase": ("report", ["--ext", fx.EXT, "--old", fx.OLD, "--new", fx.NEW]),
        "method-diff": ("report", ["--ext", fx.EXT, "--old", fx.OLD, "--new", fx.NEW]),
        "interceptors": ("interceptors", ["--ext-root", fx.EXT, "--config-old", fx.OLD, "--config-new", fx.NEW]),
        "calls": ("calls", ["--ext-root", fx.EXT, "--config-old", fx.OLD, "--config-new", fx.NEW]),
        "bodies": ("bodies", ["--ext-root", fx.EXT, "--config-old", fx.OLD, "--config-new", fx.NEW]),
        "queries": ("queries", ["--ext-root", fx.EXT, "--config-old", fx.OLD, "--config-new", fx.NEW]),
    }

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_clidb_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # the extension dump is the corpus itself (an absolute ext_dump), reports and work under the task folder
        self.reg, self.task = fx.make(self.tmp, db_cfg={"from": fx.FROM, "task_dir": os.path.join(self.tmp, fx.TASK),
                                                        "ext_dump": fx.EXT})
        self.reports = os.path.join(self.task, "reports")

    def report_of(self, stdout, name):
        """The report folder a command printed (every --db call gets <reports>/<command>-<id8>)."""
        m = re.search(r"(\S.*%s)\.(md|json)" % re.escape(name), stdout)
        self.assertTrue(m, stdout)
        path = m.group(1)
        if not os.path.isabs(path):
            path = path.split("report: ")[-1]
        return os.path.dirname(path)

    def test_same_report_as_explicit_paths(self):
        for command, (name, explicit) in self.RUNS.items():
            with self.subTest(command):
                code, stdout, stderr = run_cli([command, "--db", fx.DB, "--to", fx.TO, "--registry", self.reg])
                self.assertEqual(code, 0, stderr)
                folder = self.report_of(stdout, name)
                self.assertEqual(os.path.dirname(folder), self.reports)
                self.assertRegex(os.path.basename(folder), r"^%s-[0-9a-f]{8}$" % command)
                ref = os.path.join(self.tmp, "explicit", command)
                code, _, stderr = run_cli([command] + explicit + ["--out", ref])
                self.assertEqual(code, 0, stderr)
                self.assertEqual(load(os.path.join(folder, name + ".json"), {"<out>": folder}),
                                 load(os.path.join(ref, name + ".json"), {"<out>": ref}))
                last = stdout.strip().splitlines()[-1]
                self.assertTrue(last.startswith("ext: %s, выгрузка от " % fx.EXT), last)
                datetime.fromisoformat(last.rsplit(" ", 1)[-1])

    def test_explicit_paths_win(self):
        out = os.path.join(self.tmp, "явный out")
        only_print = os.path.join(fx.EXT, fx.EXT_PRINT)
        code, stdout, stderr = run_cli(["interceptors", "--db", "демо", "--to", fx.TO, "--registry", self.reg,
                                        "--out", out, "--ext-root", only_print])
        self.assertEqual(code, 0, stderr)
        with open(os.path.join(out, "interceptors.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual([e["name"] for e in data["extensions"]], [fx.EXT_PRINT])  # not added to the registry's ext
        self.assertIn("ext: %s, выгрузка от" % only_print, stdout)
        # an abbreviated flag counts as given, as argparse of the command reads it
        code, stdout, stderr = run_cli(["calls", "--db", fx.DB, "--to", fx.TO, "--registry", self.reg,
                                        "--ext-r", only_print, "--o", out])
        self.assertEqual(code, 0, stderr)
        with open(os.path.join(out, "calls.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["extensions"], [fx.EXT_PRINT])

    def test_work_from_the_registry(self):
        work = os.path.join(self.task, "work")
        shutil.copytree(fx.WORK, work)
        code, stdout, stderr = run_cli(["control-rebase", "--db", fx.DB, "--to", fx.TO, "--registry", self.reg])
        self.assertEqual(code, 0, stderr)
        with open(os.path.join(self.report_of(stdout, "report"), "report.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["work"], work)

    def test_bad_input(self):
        for argv, text in (
                (["control-rebase", "--db", fx.DB, "--registry", self.reg], "--to"),
                (["calls", "--db", "nope", "--to", fx.TO, "--registry", self.reg], "«nope»"),
                (["calls", "--db", fx.DB, "--to", "11.5.99", "--registry", self.reg], "11.5.99"),
                (["calls", "--db", fx.DB, "--to", fx.TO, "--registry", os.path.join(self.tmp, "нет.json")],
                 "реестр баз не найден"),
                (["calls", "--to", fx.TO, "--ext-root", fx.EXT], "только вместе с --db")):
            with self.subTest(argv):
                code, _, stderr = run_cli(argv)
                self.assertEqual(code, 2)
                self.assertIn(text, stderr)
                self.assertNotIn(fx.SECRET, stderr)

    def test_explicit_old_needs_no_release(self):
        reg, _ = fx.make(os.path.join(self.tmp, "b"), db_cfg={"task_dir": os.path.join(self.tmp, fx.TASK),
                                                               "ext_dump": fx.EXT})  # no cfe_rebase.from
        code, stdout, stderr = run_cli(["calls", "--db", fx.DB, "--to", fx.TO, "--registry", reg,
                                        "--config-old", fx.OLD])
        self.assertEqual(code, 0, stderr)
        code, _, stderr = run_cli(["calls", "--db", fx.DB, "--to", fx.TO, "--registry", reg])
        self.assertEqual(code, 2)
        self.assertIn("cfe_rebase.from", stderr)

    def test_without_db_nothing_changes(self):
        out = os.path.join(self.tmp, "plain")
        code, stdout, stderr = run_cli(["control-rebase", "--ext", fx.EXT, "--old", fx.OLD, "--new", fx.NEW,
                                        "--out", out])
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("ext: ", stdout)
        self.assertTrue(os.path.isfile(os.path.join(out, "report.json")))

    def test_help_mentions_the_registry(self):
        code, stdout, _ = run_cli(["method-diff", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--db ID", stdout)
        self.assertIn(registry.ENV, stdout)


if __name__ == "__main__":
    unittest.main()
