# -*- coding: utf-8 -*-
"""让 pytest 能找到 v3 目录下的模块（app / rules / bemfa）。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
