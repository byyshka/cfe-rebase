# -*- coding: utf-8 -*-
"""python -m cfe_rebase: the same as the cfe-rebase command."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
