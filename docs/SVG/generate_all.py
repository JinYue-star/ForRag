#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate English thesis SVGs with consistent margins and clearer layout."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _svg_kit import (  # noqa: E402
    CONTENT_BOT,
    CONTENT_TOP,
    CX,
    FOOTER1_Y,
    H,
    MX,
    MY,
    W,
    Svg,
)

OUT = HERE


def _write(stem: str, svg: Svg) -> Path:
    path = OUT / f"{stem}.svg"
    svg.write(path)
    return path


def build_fig_4_1() -> Path:
    s = Svg("Figure 4.1 Container view")
    s.bg()
    s.heading("Container View of the Deployed System")

    # Shared horizontal spine for Browser → FE → API → RAG → External
    row_y = CONTENT_TOP + 250
    bw, bh, gap = 248, 126, 40
    pad_x, pad_top, pad_bot = 28, 48, 22

    # Volume row sizing first — host must be wide enough to contain it
    v_pad_x, v_pad_top, v_pad_bot = 28, 44, 22
    v_bw, v_bh, v_gap = 196, 108, 20
    vol_inner = 4 * v_bw + 3 * v_gap  # 844
    proc_inner = 3 * bw + 2 * gap  # 824
    host_inner = max(proc_inner, vol_inner)
    host_w = host_inner + 2 * pad_x
    host_x = (W - host_w) / 2
    host_top = row_y - bh / 2 - pad_top
    host_h = pad_top + bh + pad_bot

    s.rect(host_x, host_top, host_w, host_h, fill="#f8fafc", stroke="#94a3b8", sw=1.8, rx=12)
    s.text(
        host_x + 18,
        host_top + 28,
        "Host / Docker · single process · port 8000",
        size=17,
        weight="700",
        fill="#334155",
    )

    # Center the three process boxes inside host
    c_fe = host_x + (host_w - proc_inner) / 2 + bw / 2
    c_api = c_fe + bw + gap
    c_rag = c_api + bw + gap
    fe = s.box(c_fe, row_y, bw, bh, ["Static Frontend", "HTML / CSS / JS"], title_size=17, body_size=15)
    api = s.box(c_api, row_y, bw, bh, ["API Server", "FastAPI + Uvicorn"], accent=True, title_size=17, body_size=15)
    rag = s.box(c_rag, row_y, bw, bh, ["RAG Domain", "Retrieve + Generate"], title_size=17, body_size=15)

    side = 32
    browser = s.box(
        host_x - side - bw / 2,
        row_y,
        bw,
        bh,
        ["Browser", "Teacher / Student"],
        accent=True,
        title_size=17,
        body_size=15,
    )
    ext = s.box(
        host_x + host_w + side + bw / 2,
        row_y,
        bw,
        bh,
        ["External LLM API", "Generation only"],
        accent=True,
        title_size=17,
        body_size=15,
    )

    s.arrow_h(browser, fe, y=row_y, label="HTTPS")
    s.arrow_h(fe, api, y=row_y)
    s.arrow_h(api, rag, y=row_y)
    s.arrow_h(rag, ext, y=row_y, label="API call")

    # Volumes: same outer rect as host width; four boxes strictly inside with padding
    vol_x, vol_w = host_x, host_w
    vol_top = host_top + host_h + 36
    vol_h = v_pad_top + v_bh + v_pad_bot
    s.rect(vol_x, vol_top, vol_w, vol_h, fill="#ffffff", stroke="#94a3b8", sw=1.8, rx=12)
    s.text(vol_x + 18, vol_top + 28, "Local persistent volumes", size=17, weight="700", fill="#334155")

    v0 = vol_x + (vol_w - vol_inner) / 2 + v_bw / 2
    vol_cy = vol_top + v_pad_top + v_bh / 2
    for i, lab in enumerate(
        [
            ["Auth / KB", "SQLite"],
            ["Sessions", "ChromaDB"],
            ["Files", ".uploads"],
            ["Vectors", "FAISS cache"],
        ]
    ):
        s.box(v0 + i * (v_bw + v_gap), vol_cy, v_bw, v_bh, lab, title_size=17, body_size=15)

    s.line(api["cx"], api["b"] + 6, api["cx"], vol_top - 6, sw=2.0)
    s.footer("Embeddings and re-ranking run locally; only answer generation may leave the host.")
    return _write("fig_4_1_architecture", s)


def build_fig_4_2() -> Path:
    s = Svg("Figure 4.2 Layered component view")
    s.bg()
    s.heading("Layered Component View", "Dependencies permitted only downward")

    layers = [
        ("Presentation", ["Login / Teacher console", "AI assistant", "Knowledge base", "Quiz / Export"], "#eff6ff"),
        ("Application", ["Auth & admin routes", "Session / file / QA APIs", "Exercise & export", "Orchestration"], "#f0fdf4"),
        ("Domain", ["Document processing", "Hybrid retrieval", "Grounded generation", "Quiz & grading"], "#fff7ed"),
        ("Infrastructure", ["SQLite (auth + KB)", "ChromaDB (sessions)", "FAISS vector cache", "File storage"], "#f8fafc"),
    ]
    n = len(layers)
    gap = 52  # clear channel so arrow + marker sit fully between bands
    usable = CONTENT_BOT - CONTENT_TOP
    lh = (usable - gap * (n - 1)) / n
    label_w, pad = 180, 28
    mg = 28
    area_w = W - 2 * MX - label_w - 36
    # Keep cards narrower than the full band so edges breathe
    mw = min(300, (area_w - 3 * mg) / 4)
    card_h = lh - 2 * pad
    row_tops: list[float] = []
    for i, (name, mods, fill) in enumerate(layers):
        y = CONTENT_TOP + i * (lh + gap)
        row_tops.append(y)
        s.rect(MX, y, W - 2 * MX, lh, fill=fill, stroke="#cbd5e1", sw=1.6, rx=12)
        s.rect(MX, y, label_w, lh, fill="#e2e8f0", stroke="#94a3b8", sw=1.4, rx=12)
        s.text(MX + label_w / 2, y + lh / 2 + 7, name, size=18, weight="700", anchor="middle")
        # Center the four cards in the remaining band
        cards_span = 4 * mw + 3 * mg
        x0 = MX + label_w + 18 + (W - MX - (MX + label_w + 18) - cards_span) / 2 + mw / 2
        for j, m in enumerate(mods):
            s.box(
                x0 + j * (mw + mg),
                y + lh / 2,
                mw,
                card_h,
                [m],
                fill="#ffffff",
                stroke="#64748b",
                title_size=15,
                body_size=13,
            )

    # Draw arrows last so markers are never covered by the next band
    for i in range(n - 1):
        y = row_tops[i]
        s.line(CX, y + lh + 6, CX, y + lh + gap - 6, sw=2.2)

    s.footer("ChromaDB stores sessions/messages/quizzes — not document vectors. Vectors live in FAISS.")
    return _write("fig_4_2_layers", s)


def build_fig_4_3() -> Path:
    s = Svg("Figure 4.3 Retrieval scope")
    s.bg()
    s.heading("Retrieval Scope Selection")

    q = s.box(CX, CONTENT_TOP + 60, 400, 90, ["Student question"], accent=True)

    sw, sg = 440, 48
    total = 3 * sw + 2 * sg
    c0 = MX + (W - 2 * MX - total) / 2 + sw / 2
    scope_y = CONTENT_TOP + 260
    scopes = [
        s.box(c0, scope_y, sw, 120, ["Session files only", "Ephemeral uploads"]),
        s.box(c0 + sw + sg, scope_y, sw, 120, ["Knowledge base only", "Shared course materials"]),
        s.box(c0 + 2 * (sw + sg), scope_y, sw, 120, ["Union (default)", "KB + session files"], accent=True),
    ]

    # Fan-out via shared bus (no crossing through boxes)
    bus1 = (q["b"] + scopes[0]["t"]) / 2
    s.line(q["cx"], q["b"] + 6, q["cx"], bus1, marker_end=None, sw=2.0)
    s.line(scopes[0]["cx"], bus1, scopes[2]["cx"], bus1, marker_end=None, sw=2.0)
    for sc in scopes:
        s.line(sc["cx"], bus1, sc["cx"], sc["t"] - 6, sw=2.0)

    idx = s.box(CX, CONTENT_TOP + 500, 720, 110, ["Index bundle for this question", "Parsed chunks → embeddings → FAISS"], accent=True)
    bus2 = (scopes[0]["b"] + idx["t"]) / 2
    for sc in scopes:
        s.line(sc["cx"], sc["b"] + 6, sc["cx"], bus2, marker_end=None, sw=2.0)
    s.line(scopes[0]["cx"], bus2, scopes[2]["cx"], bus2, marker_end=None, sw=2.0)
    s.line(idx["cx"], bus2, idx["cx"], idx["t"] - 6, sw=2.0)

    ans = s.box(CX, CONTENT_TOP + 700, 540, 95, ["Hybrid retrieval + grounded answer"])
    s.arrow_v(idx, ans)

    s.footer("Category filters may further narrow the knowledge-base contribution.")
    return _write("fig_4_3_scope", s)


def build_fig_4_4() -> Path:
    s = Svg("Figure 4.4 Teacher and student flows")
    s.bg()
    s.heading("Teacher and Student Flows")

    side_w, kb_w = 400, 520
    top_h, bot_h = 210, 180
    gap_x = 100
    total = 2 * side_w + kb_w + 2 * gap_x
    x0 = (W - total) / 2
    lx = x0 + side_w / 2
    rx = x0 + side_w + gap_x + kb_w + gap_x + side_w / 2

    y_top = CONTENT_TOP + 160
    y_bot = CONTENT_BOT - 120

    teacher = s.box(
        lx,
        y_top,
        side_w,
        top_h,
        ["Teacher", "Curate materials", "Publish exercises", "Export scores"],
        accent=True,
        title_size=26,
        body_size=20,
    )
    kb = s.box(
        CX,
        y_top,
        kb_w,
        top_h,
        ["Shared Course Knowledge Base", "Categories · notes · attachments", "One course, one KB"],
        accent=True,
        title_size=24,
        body_size=19,
    )
    student = s.box(
        rx,
        y_top,
        side_w,
        top_h,
        ["Student", "Ask questions", "Take quizzes", "Private sessions"],
        accent=True,
        title_size=26,
        body_size=20,
    )
    export = s.box(
        lx,
        y_bot,
        side_w,
        bot_h,
        ["Export boundary", "Questions & scores only", "No chat transcripts"],
        title_size=24,
        body_size=19,
    )
    sess = s.box(
        rx,
        y_bot,
        side_w,
        bot_h,
        ["Session uploads", "Ephemeral", "Never enter KB"],
        title_size=24,
        body_size=19,
    )

    s.arrow_h(teacher, kb, y=y_top)
    s.text((teacher["r"] + kb["l"]) / 2, y_top - 18, "write", size=18, fill="#64748b", anchor="middle")
    s.arrow_h(kb, student, y=y_top)
    s.text((kb["r"] + student["l"]) / 2, y_top - 18, "read", size=18, fill="#64748b", anchor="middle")
    s.arrow_v(teacher, export)
    s.text(lx + 14, (teacher["b"] + export["t"]) / 2 + 6, "scores", size=18, fill="#64748b", anchor="start")
    s.arrow_v(student, sess)
    s.text(rx + 14, (student["b"] + sess["t"]) / 2 + 6, "private", size=18, fill="#64748b", anchor="start")

    s.footer("Teachers write the KB; students read it. Session files stay out of the shared corpus.")
    return _write("fig_4_4_roles", s)


def build_fig_5_1() -> Path:
    s = Svg("Figure 5.1 Pipeline")
    s.bg()
    s.heading("End-to-End Retrieval and Generation Pipeline")

    def row(label: str, y: float, items: list[str], accents: set[int]) -> None:
        s.text(MX + 10, y - 48, label, size=18, weight="700", fill="#2563eb")
        n = len(items)
        gap = 44  # keep clear channel for arrows
        bw = (W - 2 * MX - gap * (n - 1)) / n
        boxes = []
        for i, t in enumerate(items):
            cx = MX + i * (bw + gap) + bw / 2
            boxes.append(s.box(cx, y, bw, 88, [t], accent=i in accents, title_size=15, body_size=13))
        for i in range(1, n):
            s.arrow_h(boxes[i - 1], boxes[i], pad=8)

    row("Ingest", CONTENT_TOP + 85, ["Upload / KB", "Parse (+ OCR)", "Token chunk", "Embed", "FAISS"], {4})
    row("Retrieve", CONTENT_TOP + 265, ["Question", "Rewrite+HyDE", "Dense", "BM25", "RRF", "Re-rank", "Corrective≤1"], {0, 5})
    row("Generate", CONTENT_TOP + 445, ["Gate", "Refine", "LiM reorder", "LLM answer", "Citations", "Persist"], {0, 3})

    band_y = CONTENT_TOP + 580
    band_h = CONTENT_BOT - band_y - 8
    s.rect(MX, band_y, W - 2 * MX, band_h, fill="#f8fafc", stroke="#cbd5e1", rx=12)
    s.text(CX, band_y + 32, "Grounding outcomes", size=20, weight="700", anchor="middle")
    ow, og = 400, 48
    total = 3 * ow + 2 * og
    c0 = MX + (W - 2 * MX - total) / 2 + ow / 2
    cy = band_y + band_h / 2 + 18
    s.box(c0, cy, ow, 110, ["grounded", "Course-constrained answer"], accent=True, title_size=18, body_size=15)
    s.box(c0 + ow + og, cy, ow, 110, ["weak", "General answer, labelled"], title_size=18, body_size=15)
    s.box(c0 + 2 * (ow + og), cy, ow, 110, ["none", "General answer, labelled"], title_size=18, body_size=15)

    s.footer("Fail-safe: insufficient evidence routes to labelled general answers.")
    return _write("fig_5_1_pipeline", s)


def build_fig_5_2() -> Path:
    s = Svg("Figure 5.2 RRF")
    s.bg()
    s.heading("Reciprocal Rank Fusion (RRF)", "RRF(c) = Σ 1 / (k + rankᵢ(c) + 1),  k = 60")

    # Balanced three-column: Dense | Fusion | Lexical, result under fusion
    col_w, item_h, item_gap = 440, 90, 18
    fuse_w, fuse_h = 440, 180
    side = 40
    lx = MX + side + col_w / 2
    rx = W - MX - side - col_w / 2

    hdr_y = CONTENT_TOP + 120
    left = s.box(lx, hdr_y, col_w, 100, ["Dense list (FAISS)", "semantic similarity"], title_size=20, body_size=17)
    right = s.box(rx, hdr_y, col_w, 100, ["Lexical list (BM25)", "exact terms / numbers"], title_size=20, body_size=17)

    dense = ["1. Overview slide", "2. Related formula", "3. Nearby example", "4. Target % passage"]
    lex = ["1. Target % passage", "2. Table of values", "3. Caption line", "4. Other term hit"]
    list_cy0 = left["b"] + 20 + item_h / 2
    left_items, right_items = [], []
    for i, t in enumerate(dense):
        left_items.append(
            s.box(lx, list_cy0 + i * (item_h + item_gap), col_w, item_h, [t], fill="#eff6ff", stroke="#93c5fd", title_size=18)
        )
    for i, t in enumerate(lex):
        right_items.append(
            s.box(rx, list_cy0 + i * (item_h + item_gap), col_w, item_h, [t], fill="#f0fdf4", stroke="#86efac", title_size=18)
        )

    # Fusion sits in the open center, vertically aligned with the rank lists
    fuse_cy = (left_items[0]["t"] + left_items[-1]["b"]) / 2
    fuse = s.box(CX, fuse_cy, fuse_w, fuse_h, ["RRF fusion", "Pool ≤ 36 candidates", "No score calibration"], accent=True, title_size=22, body_size=18)

    # Short horizontal arrows from list mid-ranks into fusion
    s.arrow_h(left_items[1], fuse)
    s.arrow_h(right_items[1], fuse)

    out = s.box(
        CX,
        left_items[-1]["b"] + 85,
        780,
        130,
        ["Fused top result", "Target % passage promoted", "Dense rank 4 + lexical rank 1"],
        accent=True,
        title_size=22,
        body_size=18,
    )
    s.arrow_v(fuse, out)

    s.footer("BM25 recovers literal percentages and identifiers; fusion promotes them without weights.")
    return _write("fig_5_2_rrf", s)


def build_fig_5_3() -> Path:
    s = Svg("Figure 5.3 Grounding gate")
    s.bg()
    s.heading("Three-Band Grounding Gate")

    top = s.box(CX, CONTENT_TOP + 55, 560, 96, ["Top-2 retrieval scores  s₁ , s₂"], accent=True, title_size=20)
    mid = s.box(CX, CONTENT_TOP + 210, 720, 110, ["Apply scale-specific thresholds", "Re-rank regime or cosine regime"], title_size=19, body_size=16)
    s.arrow_v(top, mid)

    bw, bg = 440, 48
    total = 3 * bw + 2 * bg
    c0 = MX + (W - 2 * MX - total) / 2 + bw / 2
    band_y = CONTENT_TOP + 430
    none = s.box(c0, band_y, bw, 180, ["none", "Evidence too weak", "→ general answer", "labelled non-course"], fill="#fef2f2", stroke="#ef4444", title_size=20, body_size=16)
    weak = s.box(c0 + bw + bg, band_y, bw, 180, ["weak", "Partial support", "→ general answer", "labelled non-course"], fill="#fff7ed", stroke="#f59e0b", title_size=20, body_size=16)
    grounded = s.box(c0 + 2 * (bw + bg), band_y, bw, 180, ["grounded", "Course-supported", "→ constrained LLM", "with citations"], accent=True, title_size=20, body_size=16)

    bus = (mid["b"] + none["t"]) / 2
    s.line(mid["cx"], mid["b"] + 6, mid["cx"], bus, marker_end=None, sw=2.0)
    s.line(none["cx"], bus, grounded["cx"], bus, marker_end=None, sw=2.0)
    for box in (none, weak, grounded):
        s.line(box["cx"], bus, box["cx"], box["t"] - 6, sw=2.0)

    note_y = none["b"] + 36
    note_h = CONTENT_BOT - note_y - 8
    s.rect(MX, note_y, W - 2 * MX, note_h, fill="#f8fafc", stroke="#cbd5e1", rx=12)
    s.text(CX, note_y + note_h / 2 - 28, "Decision emphasis", size=20, weight="700", anchor="middle")
    s.text(CX, note_y + note_h / 2 + 8, "Prefer two supporting passages over a single high top hit.", size=17, anchor="middle")
    s.text(CX, note_y + note_h / 2 + 36, "A higher single-hit threshold still allows one decisive passage.", size=17, anchor="middle")

    s.footer("Fail-safe direction: insufficient evidence → labelled general answer.")
    return _write("fig_5_3_grounding", s)


def build_fig_5_4() -> Path:
    s = Svg("Figure 5.4 Lost-in-the-Middle")
    s.bg()
    s.heading("Lost-in-the-Middle Evidence Re-ordering")

    lx, rx = 360, W - 360
    col_w = 400
    s.text(lx, CONTENT_TOP + 28, "Before (score order)", size=20, weight="700", anchor="middle")
    s.text(rx, CONTENT_TOP + 28, "After (edges-first)", size=20, weight="700", anchor="middle")

    before = ["#1 strongest", "#2", "#3", "#4", "#5 weakest"]
    after = ["#1 strongest", "#3", "#5 weakest", "#4", "#2"]
    list_top = CONTENT_TOP + 70
    usable = CONTENT_BOT - list_top - 20
    step = usable / 5
    bh = step - 20
    before_boxes, after_boxes = [], []
    for i, t in enumerate(before):
        before_boxes.append(s.box(lx, list_top + i * step + step / 2, col_w, bh, [t], accent=(i == 0), title_size=18))
    for i, t in enumerate(after):
        after_boxes.append(s.box(rx, list_top + i * step + step / 2, col_w, bh, [t], accent=(i in (0, 2)), title_size=18))

    # Compact badge in the clear channel between columns
    mid = s.box(CX, (CONTENT_TOP + CONTENT_BOT) / 2, 300, 120, ["Re-order", "Strong evidence", "at prompt edges"], accent=True, title_size=18, body_size=15)
    # Horizontal arrows into / out of mid at its cy — clear of column boxes
    s.line(before_boxes[2]["r"] + 6, mid["cy"], mid["l"] - 6, mid["cy"], sw=2.0)
    s.line(mid["r"] + 6, mid["cy"], after_boxes[2]["l"] - 6, mid["cy"], sw=2.0)

    s.footer(
        "Models attend more to context edges; mid slots get weaker evidence after filtering.",
        "Child chunks may expand to parent pages before prompting.",
    )
    return _write("fig_5_4_lim", s)


def build_fig_6_1() -> Path:
    s = Svg("Figure 6.1 SOLO loop")
    s.bg()
    s.heading("SOLO-Informed Practice and Feedback Loop")

    bw, bh = 440, 160
    y1 = CONTENT_TOP + 180
    y2 = CONTENT_TOP + 500
    c0 = MX + 50 + bw / 2
    c1 = CX
    c2 = W - MX - 50 - bw / 2

    a = s.box(c0, y1, bw, bh, ["1. Course materials", "Shared knowledge base"], accent=True, title_size=22, body_size=18)
    b = s.box(c1, y1, bw, bh, ["2. Grounded Q&A", "Cited answers"], accent=True, title_size=22, body_size=18)
    c = s.box(c2, y1, bw, bh, ["3. Layered quiz", "SOLO distribution"], accent=True, title_size=22, body_size=18)
    d = s.box(c2, y2, bw, bh, ["4. Auto grading", "Explanations"], accent=True, title_size=22, body_size=18)
    e = s.box(c1, y2, bw, bh, ["5. Teacher export", "Common difficulties"], accent=True, title_size=22, body_size=18)

    s.arrow_h(a, b)
    s.arrow_h(b, c)
    s.arrow_v(c, d)
    s.arrow_h(d, e)

    # Feedback under the bottom row, then up the left margin — never crosses forward arrows
    rail = e["b"] + 60
    s.line(e["cx"], e["b"] + 8, e["cx"], rail, marker_end=None, stroke="#94a3b8", sw=2.0)
    s.line(e["cx"], rail, a["cx"], rail, marker_end=None, stroke="#94a3b8", sw=2.0, dash="8 6")
    s.line(a["cx"], rail, a["cx"], a["b"] + 8, stroke="#94a3b8", sw=2.0, dash="8 6")
    s.text((e["cx"] + a["cx"]) / 2, rail - 14, "teaching feedback", size=18, fill="#64748b", anchor="middle")

    s.footer(
        "Material comprehension → question practice → response evaluation → teacher feedback",
        "One inbound arrow per step; auto grading is fed only by the layered quiz.",
    )
    return _write("fig_6_1_solo", s)


def build_fig_7_1() -> Path:
    s = Svg("Figure 7.1 Lifecycle")
    s.bg()
    s.heading("Synchronous Q&A Request Lifecycle")

    actors = ["Frontend", "API", "Orchestrator", "Doc index", "Pipeline", "LLM", "Store"]
    n = len(actors)
    gap = 20
    bw = (W - 2 * MX - gap * (n - 1)) / n
    xs = []
    for i, a in enumerate(actors):
        cx = MX + i * (bw + gap) + bw / 2
        xs.append(cx)
        s.box(cx, CONTENT_TOP + 45, bw, 76, [a], accent=(a in ("Orchestrator", "Pipeline")), title_size=15)
        s.line(cx, CONTENT_TOP + 95, cx, CONTENT_BOT - 36, marker_end=None, stroke="#e2e8f0", sw=1.4)

    def msg(y: float, i: int, j: int, label: str) -> None:
        s.line(xs[i], y, xs[j], y, sw=2.0)
        # Place label on the clear mid-span, slightly above the arrow
        s.text((xs[i] + xs[j]) / 2, y - 12, label, size=15, fill="#334155", anchor="middle")

    y0 = CONTENT_TOP + 150
    step = (CONTENT_BOT - 50 - y0) / 8
    ys = [y0 + i * step for i in range(9)]
    msg(ys[0], 0, 1, "1. POST question (auth)")
    msg(ys[1], 1, 2, "2. Session lock")
    msg(ys[2], 2, 3, "3. Build / load index")
    msg(ys[3], 2, 4, "4. Hybrid retrieve + re-rank")
    msg(ys[4], 4, 2, "5. Gate + evidence refine")
    msg(ys[5], 2, 5, "6. Invoke LLM")
    msg(ys[6], 5, 2, "7. Citations / coverage")
    msg(ys[7], 2, 6, "8. Persist message")
    msg(ys[8], 1, 0, "9. Return answer")

    s.footer(
        "Async path uses the same worker; the client polls a job id.",
        "Persisted metadata: routing, grounding, sufficiency, citation coverage.",
    )
    return _write("fig_7_1_lifecycle", s)


def build_fig_8_1() -> Path:
    s = Svg("Figure 8.1 Calibration")
    s.bg()
    s.heading("Grounding-Gate Calibration (Cosine Regime, 65 Questions)")

    ox, oy = MX + 200, CONTENT_BOT - 48
    pw, ph = W - 2 * MX - 320, CONTENT_BOT - CONTENT_TOP - 100
    s.rect(ox, oy - ph, pw, ph, fill="#ffffff", stroke="#94a3b8", sw=1.8, rx=6)
    s.text(ox + pw / 2, oy + 34, "Grounded recall", size=18, weight="700", anchor="middle")
    s.text(ox - 56, oy - ph / 2, "Grounded", size=17, weight="700", anchor="middle")
    s.text(ox - 56, oy - ph / 2 + 22, "precision", size=17, weight="700", anchor="middle")

    def map_xy(rec: float, prec: float) -> tuple[float, float]:
        return ox + (rec - 0.70) / 0.30 * pw, oy - (prec - 0.70) / 0.30 * ph

    for v in (0.70, 0.80, 0.90, 1.00):
        x, _ = map_xy(v, 0.70)
        s.line(x, oy, x, oy - ph, marker_end=None, stroke="#e2e8f0", sw=1.2)
        s.text(x, oy + 20, f"{v:.2f}", size=15, anchor="middle", fill="#64748b")
        _, y2 = map_xy(0.70, v)
        s.line(ox, y2, ox + pw, y2, marker_end=None, stroke="#e2e8f0", sw=1.2)
        s.text(ox - 14, y2 + 5, f"{v:.2f}", size=15, anchor="end", fill="#64748b")

    points = [
        (1.000, 0.905, "Original", "0.50 / 0.60 / 0.35", "#94a3b8", "br"),
        (0.754, 1.000, "Raise top-1 only", "0.69 / 0.72 / 0.35", "#f59e0b", "bl"),
        (0.982, 0.982, "Selected", "0.62 / 0.75 / 0.62", "#2563eb", "tl"),
    ]
    s.polyline([map_xy(r, p) for r, p, *_ in points], stroke="#cbd5e1", sw=2.5, marker_end=None)
    for rec, prec, name, thr, color, place in points:
        x, y = map_xy(rec, prec)
        s.circle(x, y, 11, fill=color, stroke="#0f172a", sw=1.5)
        if place == "br":
            s.text(x + 18, y + 8, name, size=17, weight="700", fill=color)
            s.text(x + 18, y + 30, f"P={prec:.3f}, R={rec:.3f}", size=15, fill="#334155")
            s.text(x + 18, y + 50, thr, size=14, fill="#64748b")
        elif place == "bl":
            s.text(x + 18, y + 28, name, size=17, weight="700", fill=color)
            s.text(x + 18, y + 50, f"P={prec:.3f}, R={rec:.3f}", size=15, fill="#334155")
            s.text(x + 18, y + 70, thr, size=14, fill="#64748b")
        else:
            s.text(x - 18, y - 36, name, size=17, weight="700", fill=color, anchor="end")
            s.text(x - 18, y - 14, f"P={prec:.3f}, R={rec:.3f}", size=15, fill="#334155", anchor="end")
            s.text(x - 18, y + 8, thr, size=14, fill="#64748b", anchor="end")

    s.footer("Raising only the top-1 threshold trades recall for precision; the selected rule moves the frontier.")
    return _write("fig_8_1_pr", s)


FIGURES = [
    build_fig_4_1,
    build_fig_4_2,
    build_fig_4_3,
    build_fig_4_4,
    build_fig_5_1,
    build_fig_5_2,
    build_fig_5_3,
    build_fig_5_4,
    build_fig_6_1,
    build_fig_7_1,
    build_fig_8_1,
]


def main() -> None:
    for fn in FIGURES:
        print(f"[ok] {fn().name}")
    print(f"[done] {len(FIGURES)} EN SVG → {OUT}")


if __name__ == "__main__":
    main()
