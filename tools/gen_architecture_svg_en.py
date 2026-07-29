#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""English outputs for SOLO Bot architecture diagram (SVG + ChatGPT prompt).

Thin wrapper around gen_architecture_svg.py --lang en.
Also regenerates the Chinese set without subtitle when run with --all via parent.

  py -3.12 tools/gen_architecture_svg_en.py
  → docs/architecture_system_16x9_en.svg
  → docs/architecture_chatgpt_prompt_en.txt
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    # Delegate to the shared generator in English mode.
    sys.argv = [str(HERE / "gen_architecture_svg.py"), "--lang", "en", *sys.argv[1:]]
    runpy.run_path(str(HERE / "gen_architecture_svg.py"), run_name="__main__")


if __name__ == "__main__":
    main()
