# -*- coding: utf-8 -*-
"""metadata.check_config: a wrong path to a vendor dump stops the command instead of an empty-looking report."""
import os
import tempfile
import unittest

from cfe_rebase import borrowed, metadata, registers

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "..", "corpus", "synthetic")


class CheckConfig(unittest.TestCase):
    def test_dump_with_object_folders_passes(self):
        metadata.check_config(os.path.join(CORPUS, "new"), "--config-new")

    def test_empty_object_folder_exits(self):
        # an empty Catalogs (a half-copied dump) is not a dump either
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Catalogs"))
            open(os.path.join(d, "Configuration.xml"), "w").close()
            self.assertIn("нет папок объектов", metadata.config_problem(d))
            open(os.path.join(d, "Catalogs", "Склады.xml"), "w").close()
            self.assertIsNone(metadata.config_problem(d))

    def test_configuration_xml_alone_exits(self):
        # a stub with only Configuration.xml made borrowed report every adopted object removed
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Configuration.xml"), "w").close()
            with self.assertRaises(SystemExit) as cm:
                metadata.check_config(d, "--config-new")
            self.assertIn("есть только Configuration.xml", cm.exception.code)
            with self.assertRaises(SystemExit) as cm:
                borrowed.main(["--ext-root", os.path.join(CORPUS, "ext"), "--config-old", os.path.join(CORPUS, "old"),
                               "--config-new", d, "--out", os.path.join(d, "out")])
            self.assertIn("--config-new", cm.exception.code)
            self.assertFalse(os.path.exists(os.path.join(d, "out")))

    def test_missing_folder_exits(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                metadata.check_config(os.path.join(d, "nope"), "--old")
            self.assertIn("папки нет", cm.exception.code)

    def test_empty_folder_exits(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                metadata.check_config(d, "--config-new")
            self.assertIn("не выгрузка конфигурации", cm.exception.code)

    def test_registers_does_not_report_an_empty_vendor_root_as_clean(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                registers.main(["--ext-root", os.path.join(CORPUS, "ext"), "--config-old", os.path.join(CORPUS, "old"),
                                "--config-new", d, "--out", os.path.join(d, "out")])
            self.assertIn("--config-new", cm.exception.code)
            self.assertFalse(os.path.exists(os.path.join(d, "out")))


if __name__ == "__main__":
    unittest.main()
