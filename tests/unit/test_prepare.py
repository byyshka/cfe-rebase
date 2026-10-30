# -*- coding: utf-8 -*-
"""cfe-rebase prepare on the synthetic corpus through a registry whose dump command is a fake script: the work copy
under git with one commit, prepare.json and the four reports, a second run that leaves the work copy alone, --no-dump,
--names, and the stops with their exit codes (2 - bad input, 3 - the dump or git failed).

    python -m unittest tests.unit.test_prepare
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from cfe_rebase import prepare
from tests.unit import registry_fixture as fx
from tests.unit.test_registry import run_cli

HAVE_GIT = shutil.which("git") is not None


def rmtree(path):
    """shutil.rmtree that also removes the read-only object files git leaves on Windows."""
    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onexc=retry)


def git(cwd, *args):
    proc = subprocess.run(["git", "-c", "core.quotepath=false"] + list(args), cwd=cwd, capture_output=True,
                          check=True)
    return proc.stdout.decode("utf-8").strip()


@unittest.skipUnless(HAVE_GIT, "git is not on PATH: prepare makes the work copy a git repository")
class Prepare(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfe_prepare_")
        self.addCleanup(rmtree, self.tmp)
        self.reg, self.task = fx.make(self.tmp)
        self.ext = os.path.join(self.task, "before", "xml")
        self.work = os.path.join(self.task, "work")
        self.args_file = os.path.join(self.task, "before", "dump-args.json")

    def prepare(self, *extra):
        return run_cli(["prepare", "--db", fx.DB, "--to", fx.TO, "--registry", self.reg] + list(extra))

    def prepare_json(self, stdout):
        line = [s for s in stdout.splitlines() if s.startswith("prepare.json: ")]
        self.assertEqual(len(line), 1, stdout)
        with open(line[0][len("prepare.json: "):], encoding="utf-8") as fh:
            return json.load(fh)

    def test_first_and_second_run(self):
        code, stdout, stderr = self.prepare()
        self.assertEqual(code, 0, stderr)
        res = self.prepare_json(stdout)
        # the dump command got the database, the names from both spellings of extensions and the parent of ext_dump
        with open(self.args_file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), [fx.DB, fx.EXT_MAIN + "," + fx.EXT_PRINT, os.path.dirname(self.ext)])
        self.assertEqual(sorted(os.listdir(self.ext)), sorted([fx.EXT_MAIN, fx.EXT_PRINT]))
        self.assertEqual(res["dump"]["names"], [fx.EXT_MAIN, fx.EXT_PRINT])
        # the work copy: a copy of ext under git, one commit
        self.assertEqual(res["work"]["state"], "created")
        self.assertFalse(res["work"]["compared"])
        self.assertTrue(os.path.isdir(os.path.join(self.work, ".git")))
        self.assertEqual(git(self.work, "log", "--format=%s"), "before: %s %s" % (fx.DB, fx.FROM))
        self.assertEqual(git(self.work, "status", "--porcelain"), "")
        self.assertEqual(git(self.work, "config", "core.autocrlf"), "false")
        # dumps hold paths over 260 characters: without it git add fails on Windows
        self.assertEqual(git(self.work, "config", "core.longpaths"), "true")
        for rel in (os.path.join(fx.EXT_MAIN, "CommonModules", "ЗаказыСервер", "Ext", "Module.bsl"),
                    os.path.join(fx.EXT_PRINT, "Configuration.xml")):
            with open(os.path.join(self.ext, rel), "rb") as a, open(os.path.join(self.work, rel), "rb") as b:
                self.assertEqual(a.read(), b.read(), rel)  # byte-exact: BOM and CRLF kept
        # the data of the screen
        self.assertEqual((res["db"], res["from"], res["to"], res["ext"]), (fx.DB, fx.FROM, fx.TO, self.ext))
        self.assertTrue(res["ext_newest"])
        self.assertEqual(res["control_rebase"]["methods"], 15)
        self.assertEqual(res["control_rebase"]["verdicts"]["REBASED"], 9)
        self.assertEqual(res["control_rebase"]["verdicts"]["CONFLICTS"], 1)
        self.assertGreater(res["interceptors"]["flagged"], 0)
        self.assertIn("CONFLICT", res["interceptors"]["flags"])
        self.assertGreater(res["calls"]["findings"], 0)
        self.assertIn("unjudged", res["queries"])
        folder = os.path.dirname(res["prepare_json"])
        self.assertEqual(os.path.dirname(folder), os.path.join(self.task, "reports"))
        self.assertEqual(os.path.basename(folder), "prepare-" + res["run_id"][:8])
        for tool in prepare.TOOLS:
            self.assertEqual(os.path.dirname(res["reports"][tool]["json"]), os.path.join(folder, tool))
            self.assertTrue(os.path.isfile(res["reports"][tool]["json"]), tool)
            self.assertTrue(os.path.isfile(res["reports"][tool]["md"]), tool)
            self.assertIn(res["reports"][tool]["md"], stdout)
        for text in ("база: " + fx.DB, "релизы: %s → %s" % (fx.FROM, fx.TO), "ext: " + self.ext,
                     "work: " + self.work, "control_rebase: методов 15", "interceptors: строк", "calls: находок",
                     "queries: находок"):
            self.assertIn(text, stdout)
        self.assertNotIn(fx.SECRET, stdout + stderr)
        # bodies runs too, and the findings make one plan
        self.assertGreater(res["bodies"]["copies"], 0)
        self.assertTrue(os.path.isfile(res["plan"]["json"]) and os.path.isfile(res["plan"]["md"]))
        self.assertEqual(os.path.dirname(res["plan"]["json"]), folder)
        with open(res["plan"]["json"], encoding="utf-8") as fh:
            plan = json.load(fh)
        must = [x for x in plan["items"] if x["group"] == "MUST_FIX"]
        self.assertEqual(res["plan"]["counts"]["MUST_FIX"], len(must))
        rebased = [x for x in must if any(s == "control_rebase:REBASED" for s in x["sources"])]
        self.assertEqual(len(rebased), 9)
        self.assertIn("interceptors:DEAD_BINDING", plan["hidden"])
        self.assertEqual(res["borrowed"], {"findings": 0, "checks": {}})
        self.assertEqual(res["registers"], {"findings": 0, "checks": {}})
        for text in ("bodies: копий &Вместо", "borrowed: находок 0", "registers: находок 0", "план: исправить %d" % len(must),
                     "plan.md: " + res["plan"]["md"], "план не покрывает собственные копии СКД"):
            self.assertIn(text, stdout)

        # second run: the work copy exists and is left untouched; control_rebase now compares with it
        marker = os.path.join(self.work, fx.EXT_MAIN, "Configuration.xml")
        with open(marker, "ab") as fh:
            fh.write(b"<!-- hand edit -->")
        head = git(self.work, "rev-parse", "HEAD")
        code, stdout, stderr = self.prepare()
        self.assertEqual(code, 0, stderr)
        again = self.prepare_json(stdout)
        self.assertEqual(again["work"], {"path": self.work, "state": "existing", "compared": True})
        self.assertNotEqual(again["run_id"], res["run_id"])
        self.assertEqual(git(self.work, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.work, "status", "--porcelain"), "M %s/Configuration.xml" % fx.EXT_MAIN)
        with open(again["reports"]["control_rebase"]["json"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["work"], self.work)
        self.assertIn("уже была, не тронута", stdout)

    def test_no_dump_and_names(self):
        code, _, stderr = self.prepare("--no-dump")
        self.assertEqual(code, 2)
        self.assertIn("нет выгрузок расширений", stderr)
        self.assertFalse(os.path.exists(self.work))
        code, stdout, stderr = self.prepare("--names", fx.EXT_PRINT, "--only", fx.EXT_PRINT)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(os.listdir(self.ext), [fx.EXT_PRINT])
        res = self.prepare_json(stdout)
        self.assertEqual(res["only"], fx.EXT_PRINT)
        os.remove(self.args_file)
        code, stdout, stderr = self.prepare("--no-dump")
        self.assertEqual(code, 0, stderr)
        self.assertFalse(os.path.exists(self.args_file))  # the dump command did not run
        self.assertIsNone(self.prepare_json(stdout)["dump"])
        self.assertIn("взята как есть (--no-dump)", stdout)

    def test_dump_fails(self):
        with mock.patch.dict(os.environ, {"CFE_FAKE_DUMP_FAIL": "1"}):
            code, _, stderr = self.prepare()
        self.assertEqual(code, 3)
        self.assertIn("выгрузка завершилась с кодом 5", stderr)
        self.assertIn("fake_dump.py", stderr)
        self.assertIn("база %s недоступна" % fx.DB, stderr)  # the tail of its stderr
        self.assertFalse(os.path.exists(self.work))

    def test_missing_release(self):
        empty = os.path.join(self.tmp, "пустой релиз")
        os.makedirs(empty)
        reg, task = fx.make(os.path.join(self.tmp, "b"), releases={fx.FROM: fx.OLD, fx.TO: empty})
        code, _, stderr = run_cli(["prepare", "--db", fx.DB, "--to", fx.TO, "--registry", reg])
        self.assertEqual(code, 2)
        self.assertIn("релиза %s (to)" % fx.TO, stderr)
        self.assertFalse(os.path.exists(os.path.join(task, "before")))  # stopped before the dump
        code, _, stderr = self.prepare("--from", "10.0")
        self.assertEqual(code, 2)
        self.assertIn("10.0", stderr)

    def test_work_without_git_is_refused(self):
        os.makedirs(self.work)
        keep = os.path.join(self.work, "мои правки.txt")
        with open(keep, "w", encoding="utf-8") as fh:
            fh.write("x")
        code, _, stderr = self.prepare()
        self.assertEqual(code, 2)
        self.assertIn("не под git", stderr)
        self.assertEqual(os.listdir(self.work), ["мои правки.txt"])

    def test_work_inside_another_repository_is_refused(self):
        # rev-parse --is-inside-work-tree is true under any outer repository, which may ignore the work copy
        os.makedirs(self.work)
        with open(os.path.join(self.work, "мои правки.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        with open(os.path.join(self.task, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("work/\n")
        git(self.task, "init", "-q")
        code, _, stderr = self.prepare()
        self.assertEqual(code, 2)
        self.assertIn("не корень своего git-репозитория", stderr)
        self.assertEqual(os.listdir(self.work), ["мои правки.txt"])
        git(self.work, "init", "-q")  # its own repository inside the outer one: accepted
        code, stdout, stderr = self.prepare()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.prepare_json(stdout)["work"]["state"], "existing")

    def test_proof_corpus_is_never_dumped(self):
        reg, task = fx.make(os.path.join(self.tmp, "b"), db_cfg={
            "from": fx.FROM, "task_dir": os.path.join(self.tmp, "b", fx.TASK), "ext_dump": "before\\xml",
            "proof_corpus": True})
        code, _, stderr = run_cli(["prepare", "--db", fx.DB, "--to", fx.TO, "--registry", reg])
        self.assertEqual(code, 2)
        for text in (fx.DB, "proof_corpus", "--no-dump"):
            self.assertIn(text, stderr)
        self.assertFalse(os.path.exists(os.path.join(task, "before")))  # stopped before the dump command
        self.assertFalse(os.path.exists(os.path.join(task, "work")))
        shutil.copytree(fx.EXT, os.path.join(task, "before", "xml"))
        code, stdout, stderr = run_cli(["prepare", "--db", fx.DB, "--to", fx.TO, "--registry", reg, "--no-dump"])
        self.assertEqual(code, 0, stderr)
        self.assertIsNone(self.prepare_json(stdout)["dump"])

    def test_work_copy_and_dumps_never_nest(self):
        base = os.path.join(self.tmp, "n")
        task = os.path.join(base, fx.TASK)
        for cfg, text in (({"ext_dump": "before\\xml", "work": "before\\xml\\work"}, "внутри ext"),
                          ({"ext_dump": "work\\xml", "work": "work"}, "внутри рабочей копии"),
                          ({"ext_dump": "before\\xml", "work": os.path.join(fx.NEW, "work")}, "внутри new")):
            with self.subTest(cfg):
                reg, _ = fx.make(base, db_cfg=dict(cfg, **{"from": fx.FROM, "task_dir": task}))
                code, _, stderr = run_cli(["prepare", "--db", fx.DB, "--to", fx.TO, "--registry", reg])
                self.assertEqual(code, 2)
                self.assertIn(text, stderr)
                self.assertFalse(os.path.exists(os.path.join(task, "before")))  # nothing ran
                self.assertFalse(os.path.exists(os.path.join(task, "work")))
                self.assertFalse(os.path.exists(os.path.join(fx.NEW, "work")))

    def test_a_hung_dump_is_stopped(self):
        env = {"CFE_FAKE_DUMP_HANG": "60", prepare.DUMP_TIMEOUT_ENV: "2"}
        t0 = time.monotonic()
        with mock.patch.dict(os.environ, env):
            code, _, stderr = self.prepare()
        self.assertLess(time.monotonic() - t0, 40)  # with pipes it waited for the child the script started
        self.assertEqual(code, 3)
        self.assertIn("не завершилась за 2 с", stderr)
        self.assertIn("конфигуратор завис", stderr)  # the output so far
        self.assertFalse(os.path.exists(self.work))
        with mock.patch.dict(os.environ, {prepare.DUMP_TIMEOUT_ENV: "полчаса"}):
            code, _, stderr = self.prepare()
        self.assertEqual(code, 2)
        self.assertIn(prepare.DUMP_TIMEOUT_ENV, stderr)
        self.assertFalse(os.path.exists(self.args_file))  # refused before the dump command

    def test_git_identity_when_none_is_configured(self):
        empty = os.path.join(self.tmp, "gitconfig")
        open(empty, "w").close()
        env = {"GIT_CONFIG_GLOBAL": empty, "GIT_CONFIG_NOSYSTEM": "1"}
        with mock.patch.dict(os.environ, env):
            code, _, stderr = self.prepare()
            self.assertEqual(code, 0, stderr)
            self.assertEqual(git(self.work, "log", "--format=%an <%ae>"), "cfe-rebase <noreply>")

    def test_a_failed_copy_leaves_no_half_copy(self):
        shutil.copytree(fx.EXT, self.ext)

        def half_copy(src, dst, *args, **kwargs):  # a Windows long path: some files are copied, then the copy fails
            os.makedirs(os.path.join(dst, fx.EXT_MAIN))
            with open(os.path.join(dst, fx.EXT_MAIN, "Configuration.xml"), "wb") as fh:
                fh.write(b"<MetaDataObject/>")
            raise OSError(206, "The filename or extension is too long")

        with mock.patch.object(prepare.shutil, "copytree", side_effect=half_copy):
            with self.assertRaises(prepare.PrepareError) as cm:
                prepare.run(fx.DB, fx.TO, registry_path=self.reg, dump=False)
        self.assertEqual(cm.exception.code, prepare.FAILED)
        self.assertIn("не удалось скопировать", cm.exception.message)
        self.assertIn("too long", cm.exception.message)
        self.assertFalse(os.path.exists(self.work))
        res = prepare.run(fx.DB, fx.TO, registry_path=self.reg, dump=False)  # the next run is not refused
        self.assertEqual(res["work"]["state"], "created")

    def test_copy_error_names_the_first_failed_file(self):
        exc = shutil.Error([("a/1.bsl", "b/1.bsl", "too long"), ("a/2.bsl", "b/2.bsl", "too long")])
        self.assertEqual(prepare._copy_error(exc), "a/1.bsl: too long (и ещё файлов: 1)")
        self.assertEqual(prepare._copy_error(OSError(28, "No space left on device")),
                         "[Errno 28] No space left on device")

    def test_out_of_perimeter(self):
        base = os.path.join(self.tmp, "all")
        task = os.path.join(base, fx.TASK)
        ext, ext_all = fx.make_ext_all(task)
        reg, _ = fx.make(base, db_cfg={"from": fx.FROM, "task_dir": task, "ext_dump": "before\\xml",
                                       "ext_all": "all\\xml"})
        res = prepare.run(fx.DB, fx.TO, registry_path=reg, dump=False)
        o = res["out_of_perimeter"]
        self.assertEqual(o["ext_all"], ext_all)
        self.assertEqual(o["perimeter"], [fx.EXT_ALL_V2])  # renamed in the base: matched by the uuid of Configuration
        self.assertEqual(sorted(o["extensions"]), sorted([fx.EXT_MAIN, fx.EXT_FOREIGN, fx.EXT_CLEAN]))
        self.assertEqual([x["ext"] for x in o["not_applied"]], [fx.EXT_FOREIGN])
        self.assertTrue(o["not_applied"][0]["first"].startswith("COMPAT_MODE"), o["not_applied"][0])
        self.assertEqual([x["ext"] for x in o["control_only"]], [fx.EXT_MAIN])
        self.assertEqual(o["control_only"][0]["borrowed"], 0)
        self.assertGreaterEqual(o["control_only"][0]["control"], 10)  # 9 REBASED and 1 CONFLICTS at least
        self.assertIn("&ИзменениеИКонтроль", o["control_only"][0]["first"])
        self.assertEqual(o["clean"], [fx.EXT_CLEAN])
        # the perimeter extension of ext_all is not checked again
        with open(o["reports"]["borrowed"]["json"], encoding="utf-8") as fh:
            self.assertEqual({f["ext"] for f in json.load(fh)["findings"]}, {fx.EXT_FOREIGN})
        with open(o["reports"]["control_rebase"]["json"], encoding="utf-8") as fh:
            self.assertEqual({m["ext"] for m in json.load(fh)["methods"]}, {fx.EXT_MAIN})
        self.assertEqual(os.path.dirname(os.path.dirname(o["reports"]["borrowed"]["json"])),
                         os.path.join(os.path.dirname(res["prepare_json"]), "out_of_perimeter"))
        # FOREIGN_OBJECT: the perimeter reads a constant of an extension that will not be applied (error) and queries
        # a catalog of a clean one (info); a comment, a vendor object and a name nobody has are not findings
        got = {(f["object"], f["owner"], f["level"], tuple(f["how"])) for f in o["foreign"]}
        self.assertEqual(got, {("Константа.Синт_Дата", fx.EXT_FOREIGN, "error", ("code",)),
                               ("Справочник.Синт_Склады2", fx.EXT_CLEAN, "info", ("query",))})
        by = {f["object"]: f for f in o["foreign"]}
        self.assertEqual(by["Константа.Синт_Дата"]["method"], "Синт_ДатаНачала")
        self.assertEqual(len(by["Константа.Синт_Дата"]["lines"]), 1)  # the comment line is not code
        self.assertEqual(by["Константа.Синт_Дата"]["module"], "CommonModules/ЗаказыСервер/Ext/Module.bsl")
        with open(res["plan"]["json"], encoding="utf-8") as fh:
            plan = json.load(fh)
        foreign = [x for x in plan["items"] if "out_of_perimeter:FOREIGN_OBJECT" in x["sources"]]
        # the query of the clean one is a REVIEW item of queries too («не разрешалось и в old»): FOREIGN_OBJECT names
        # whose object it is in the same item
        self.assertEqual(sorted(x["group"] for x in foreign), ["MUST_FIX", "REVIEW"])
        review = next(x for x in foreign if x["group"] == "REVIEW")
        self.assertIn("queries:query", review["sources"])
        self.assertTrue(any(fx.EXT_CLEAN in w for w in review["what"]))
        must = next(x for x in foreign if x["group"] == "MUST_FIX")
        self.assertIn(fx.EXT_FOREIGN, must["what"][0])
        self.assertIn("не применится", must["what"][0])
        self.assertIn("Синт_Дата", " ".join(x["what"][0] for x in res["plan"]["must_fix"]))
        # the outside extensions themselves are never plan items
        self.assertFalse({x["ext"] for x in plan["items"]} & {fx.EXT_MAIN, fx.EXT_FOREIGN, fx.EXT_CLEAN})
        self.assertEqual(plan["out_of_perimeter"]["foreign"], {"error": 1, "risk": 0, "info": 1})
        with open(res["plan"]["md"], encoding="utf-8") as fh:
            md = fh.read()
        for text in ("## Вне периметра — не применятся — 1", "### Только &ИзменениеИКонтроль не проходит контроль — 1",
                     "Без ошибок заимствований и контроля: " + fx.EXT_CLEAN, "FOREIGN_OBJECT"):
            self.assertIn(text, md)
        self.assertLess(md.index("## Исправить"), md.index("## Вне периметра"))
        screen = prepare.screen(res)
        self.assertIn("вне периметра (%s): не применятся 1, только &ИзменениеИКонтроль 1, без ошибок 1" % ext_all,
                      screen)
        self.assertIn("FOREIGN_OBJECT): error 1, risk 0, info 1", screen)
        # without ext_all - no pass, no section in the plan
        shutil.copytree(fx.EXT, self.ext)
        plain = prepare.run(fx.DB, fx.TO, registry_path=self.reg, dump=False)
        self.assertIsNone(plain["out_of_perimeter"])
        with open(plain["plan"]["json"], encoding="utf-8") as fh:
            self.assertNotIn("out_of_perimeter", json.load(fh))
        self.assertNotIn("вне периметра", prepare.screen(plain))

    def test_ext_all_without_extensions_is_refused(self):
        base = os.path.join(self.tmp, "empty")
        task = os.path.join(base, fx.TASK)
        os.makedirs(os.path.join(task, "all"))
        shutil.copytree(fx.EXT, os.path.join(task, "before", "xml"))
        reg, _ = fx.make(base, db_cfg={"from": fx.FROM, "task_dir": task, "ext_dump": "before\\xml",
                                       "ext_all": "all"})
        with self.assertRaises(prepare.PrepareError) as cm:
            prepare.run(fx.DB, fx.TO, registry_path=reg, dump=False)
        self.assertEqual(cm.exception.code, prepare.BAD_INPUT)
        self.assertIn("cfe_rebase.ext_all", cm.exception.message)
        self.assertFalse(os.path.exists(os.path.join(task, "work")))  # refused before the work copy

    def test_run_returns_the_data(self):
        with self.assertRaises(prepare.PrepareError) as cm:
            prepare.run(fx.DB, None, registry_path=self.reg)
        self.assertEqual(cm.exception.code, prepare.BAD_INPUT)
        shutil.copytree(fx.EXT, self.ext)
        res = prepare.run(fx.DB, fx.TO, registry_path=self.reg, dump=False, run_id="0123456789abcdef")
        self.assertEqual(os.path.basename(os.path.dirname(res["prepare_json"])), "prepare-01234567")
        with open(res["prepare_json"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), res)


if __name__ == "__main__":
    unittest.main()
