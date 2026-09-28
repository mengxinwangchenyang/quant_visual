# -*- coding: utf-8 -*-
"""定位 quant_fresh 交易仓库并挂到 sys.path。

默认布局: quant_visual 与 quant_fresh 为同级目录
    <parent>/quant_fresh/   交易仓库(config / util / auto_buy/predict_client 及交易数据)
    <parent>/quant_visual/  本仓库
可用环境变量 QUANT_FRESH_ROOT 指定其他位置。
"""

import os
import sys
from pathlib import Path

VISUAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("QUANT_FRESH_ROOT") or VISUAL_ROOT.parent / "quant_fresh").resolve()

if not (PROJECT_ROOT / "config.py").exists():
    raise RuntimeError("quant_fresh not found at %s (set QUANT_FRESH_ROOT)" % PROJECT_ROOT)

for _d in (str(VISUAL_ROOT), str(PROJECT_ROOT)):
    if _d not in sys.path:
        sys.path.insert(0, _d)
