#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Ch-3 Kahoot.pdf 转成课堂练习可导入的 xlsx（列格式同 exercise_service.BANK_COLUMNS）。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from rag_api.exercise_service import bank_to_xlsx, parse_bank_rows  # noqa: E402

# 答案依据：Kahoot 详情页绿色勾选（截图 OCR + 人工核对）。
# 原题含图示处，已把关键信息写进题干，便于无图导入后仍可作答。
ROWS = [
    {
        "type": "single",
        "question": (
            "What type of filter structure does the following figure belong to? "
            "(Figure: multi-level HPF/LPF bank with downsampling by 2, producing detail/approximation bands.)"
        ),
        "option1": "Synthesis Filter",
        "option2": "Analysis Filter",
        "option3": "",
        "option4": "",
        "option5": "",
        "option6": "",
        "correct": "B",
    },
    {
        "type": "single",
        "question": "Which of the following is a correct approach for extracting the QRS complex?",
        "option1": "High pass filter",
        "option2": "Adaptive Filter",
        "option3": "Matched Filter",
        "option4": "Low pass filter",
        "option5": "",
        "option6": "",
        "correct": "C",
    },
    {
        "type": "tf",
        "question": (
            "Is R1 the output of A3? "
            "(In the 3-level analysis filter bank / wavelet tree, R1 is the lowest-frequency band "
            "corresponding to approximation A3.)"
        ),
        "option1": "True",
        "option2": "False",
        "option3": "",
        "option4": "",
        "option5": "",
        "option6": "",
        "correct": "True",
    },
    {
        "type": "single",
        "question": (
            "Which output does P1 correspond to? "
            "(P1 is the lower half-band [0, π/2] after the first analysis stage.)"
        ),
        "option1": "A1",
        "option2": "A2",
        "option3": "D1",
        "option4": "D2",
        "option5": "",
        "option6": "",
        "correct": "A",
    },
    {
        "type": "single",
        "question": (
            "Which frequency band is the output of D3? "
            "(D3 is the level-3 detail/high-pass branch in the wavelet analysis tree.)"
        ),
        "option1": "R1",
        "option2": "R2",
        "option3": "R3",
        "option4": "R4",
        "option5": "",
        "option6": "",
        "correct": "B",
    },
    {
        "type": "multi",
        "question": "Which of the following are correct descriptions regarding wavelet / DWT?",
        "option1": "DWT supports arbitrary scale",
        "option2": "DWT can be implemented using filter banks",
        "option3": "The scale and translation of DWT must be an integer",
        "option4": "DWT is computationally more expensive than CWT",
        "option5": "",
        "option6": "",
        "correct": "B|C",
    },
    {
        "type": "single",
        "question": (
            "Which category of feature does amplitude in the figure belong to? "
            "(Figure: ERP waveform of potential vs time after stimulus; amplitude marked as peak height.)"
        ),
        "option1": "Temporal",
        "option2": "Frequency",
        "option3": "Spatial",
        "option4": "",
        "option5": "",
        "option6": "",
        "correct": "A",
    },
    {
        "type": "single",
        "question": (
            "Which category of feature does amplitude in Fig. 2 belong to? "
            "(Fig. 2: power spectral density Power(μV²/Hz) vs Frequency(Hz) for eyes-open/closed EEG.)"
        ),
        "option1": "Temporal",
        "option2": "Frequency",
        "option3": "Spatial",
        "option4": "",
        "option5": "",
        "option6": "",
        "correct": "B",
    },
    {
        "type": "single",
        "question": (
            "In a spectrogram (time–frequency colour plot of an EEG signal), "
            "which visual channel represents the power spectrum / power intensity?"
        ),
        "option1": "X-axis",
        "option2": "Y-axis",
        "option3": "Color intensity",
        "option4": "",
        "option5": "",
        "option6": "",
        "correct": "C",
    },
    {
        "type": "single",
        "question": (
            "In the frequency response of a window function, which is Q? "
            "(P marks the central peak; Q marks a smaller neighbouring peak.)"
        ),
        "option1": "Mainlobe",
        "option2": "Sidelobe",
        "option3": "",
        "option4": "",
        "option5": "",
        "option6": "",
        "correct": "B",
    },
    {
        "type": "single",
        "question": (
            "Which of the following cannot be achieved by STFT? "
            "(Choose the time–frequency tiling that STFT cannot produce.)"
        ),
        "option1": "Uniform time–frequency tiles (fixed resolution across the plane)",
        "option2": "Short-window STFT tiling (finer time, coarser frequency)",
        "option3": (
            "Multi-resolution / wavelet-like tiling "
            "(better frequency resolution at low frequencies, better time resolution at high frequencies)"
        ),
        "option4": "",
        "option5": "",
        "option6": "",
        "correct": "C",
    },
]


def main() -> int:
    items, errors = parse_bank_rows(ROWS)
    if errors:
        print("Validation failed:", file=sys.stderr)
        for e in errors:
            print(" ", e, file=sys.stderr)
        return 1
    out = Path(__file__).resolve().parent / "ELEC6081_Ch3_Kahoot_class_exercise.xlsx"
    out.write_bytes(bank_to_xlsx(items))
    print(f"Wrote {out} ({len(items)} questions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
