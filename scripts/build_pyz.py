# -*- coding: utf-8 -*-
"""Build dist/cfe-rebase.pyz: the cfe_rebase package as one zipapp file, run as `python cfe-rebase.pyz <command> ...`.

    python scripts/build_pyz.py
"""
import os
import shutil
import tempfile
import zipapp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    src = os.path.join(ROOT, "src", "cfe_rebase")
    target = os.path.join(ROOT, "dist", "cfe-rebase.pyz")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(src, os.path.join(tmp, "cfe_rebase"), ignore=shutil.ignore_patterns("__pycache__"))
        zipapp.create_archive(tmp, target=target, interpreter="/usr/bin/env python3", main="cfe_rebase.cli:main",
                              compressed=True)
    print(target)


if __name__ == "__main__":
    main()
