#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成中文论文矢量图（布局与 docs/SVG 英文版一致）。"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _svg_kit import CONTENT_BOT, CONTENT_TOP, CX, MX, W, Svg  # noqa: E402

FONT = "Microsoft YaHei, PingFang SC, Segoe UI, sans-serif"
OUT = HERE


def _write(stem: str, svg: Svg) -> Path:
    path = OUT / f"{stem}.svg"
    svg.write(path)
    return path


def _s(title: str = "") -> Svg:
    return Svg(title, family=FONT)


def build_fig_4_1() -> Path:
    s = _s("图 4.1 部署系统容器视图")
    s.bg()
    s.heading("部署系统容器视图")

    row_y = CONTENT_TOP + 250
    bw, bh, gap = 248, 126, 40
    pad_x, pad_top, pad_bot = 28, 48, 22

    v_pad_top, v_pad_bot = 44, 22
    v_bw, v_bh, v_gap = 196, 108, 20
    vol_inner = 4 * v_bw + 3 * v_gap
    proc_inner = 3 * bw + 2 * gap
    host_inner = max(proc_inner, vol_inner)
    host_w = host_inner + 2 * pad_x
    host_x = (W - host_w) / 2
    host_top = row_y - bh / 2 - pad_top
    host_h = pad_top + bh + pad_bot

    s.rect(host_x, host_top, host_w, host_h, fill="#f8fafc", stroke="#94a3b8", sw=1.8, rx=12)
    s.text(host_x + 18, host_top + 28, "主机 / Docker · 单进程 · 端口 8000", size=17, weight="700", fill="#334155")

    c_fe = host_x + (host_w - proc_inner) / 2 + bw / 2
    c_api = c_fe + bw + gap
    c_rag = c_api + bw + gap
    fe = s.box(c_fe, row_y, bw, bh, ["静态前端", "HTML / CSS / JS"], title_size=17, body_size=15)
    api = s.box(c_api, row_y, bw, bh, ["API 服务", "FastAPI + Uvicorn"], accent=True, title_size=17, body_size=15)
    rag = s.box(c_rag, row_y, bw, bh, ["RAG 领域", "检索 + 生成"], title_size=17, body_size=15)

    side = 32
    browser = s.box(host_x - side - bw / 2, row_y, bw, bh, ["浏览器", "教师 / 学生"], accent=True, title_size=17, body_size=15)
    ext = s.box(
        host_x + host_w + side + bw / 2,
        row_y,
        bw,
        bh,
        ["外部大模型 API", "仅用于生成"],
        accent=True,
        title_size=17,
        body_size=15,
    )

    s.arrow_h(browser, fe, y=row_y, label="HTTPS")
    s.arrow_h(fe, api, y=row_y)
    s.arrow_h(api, rag, y=row_y)
    s.arrow_h(rag, ext, y=row_y, label="API 调用")

    vol_x, vol_w = host_x, host_w
    vol_top = host_top + host_h + 36
    vol_h = v_pad_top + v_bh + v_pad_bot
    s.rect(vol_x, vol_top, vol_w, vol_h, fill="#ffffff", stroke="#94a3b8", sw=1.8, rx=12)
    s.text(vol_x + 18, vol_top + 28, "本地持久化卷", size=17, weight="700", fill="#334155")

    v0 = vol_x + (vol_w - vol_inner) / 2 + v_bw / 2
    vol_cy = vol_top + v_pad_top + v_bh / 2
    for i, lab in enumerate(
        [
            ["鉴权 / 知识库", "SQLite"],
            ["会话", "ChromaDB"],
            ["文件", ".uploads"],
            ["向量", "FAISS 缓存"],
        ]
    ):
        s.box(v0 + i * (v_bw + v_gap), vol_cy, v_bw, v_bh, lab, title_size=17, body_size=15)

    s.line(api["cx"], api["b"] + 6, api["cx"], vol_top - 6, sw=2.0)
    s.footer("嵌入与重排在本地运行；仅答案生成可调用外部服务。")
    return _write("fig_4_1_architecture", s)


def build_fig_4_2() -> Path:
    s = _s("图 4.2 分层组件视图")
    s.bg()
    s.heading("分层组件视图", "依赖仅允许自上而下")
    layers = [
        ("表现层", ["登录 / 教师台", "AI 助手", "课程知识库", "测验 / 导出"], "#eff6ff"),
        ("应用层", ["鉴权与管理路由", "会话 / 文件 / 问答接口", "练习与导出", "问答编排"], "#f0fdf4"),
        ("领域层", ["文档处理", "混合检索", "有据生成", "出题与批改"], "#fff7ed"),
        ("基础设施", ["SQLite（鉴权+知识库）", "ChromaDB（会话）", "FAISS 向量缓存", "文件存储"], "#f8fafc"),
    ]
    n = len(layers)
    gap = 52
    lh = (CONTENT_BOT - CONTENT_TOP - gap * (n - 1)) / n
    label_w, pad = 180, 28
    mg = 28
    area_w = W - 2 * MX - label_w - 36
    mw = min(300, (area_w - 3 * mg) / 4)
    card_h = lh - 2 * pad
    row_tops: list[float] = []
    for i, (name, mods, fill) in enumerate(layers):
        y = CONTENT_TOP + i * (lh + gap)
        row_tops.append(y)
        s.rect(MX, y, W - 2 * MX, lh, fill=fill, stroke="#cbd5e1", sw=1.6, rx=12)
        s.rect(MX, y, label_w, lh, fill="#e2e8f0", stroke="#94a3b8", sw=1.4, rx=12)
        s.text(MX + label_w / 2, y + lh / 2 + 7, name, size=18, weight="700", anchor="middle")
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
    for i in range(n - 1):
        y = row_tops[i]
        s.line(CX, y + lh + 6, CX, y + lh + gap - 6, sw=2.2)
    s.footer("ChromaDB 存会话/消息/测验，不存文档向量；文档向量在 FAISS。")
    return _write("fig_4_2_layers", s)


def build_fig_4_3() -> Path:
    s = _s("图 4.3 检索范围选择")
    s.bg()
    s.heading("检索范围选择")
    q = s.box(CX, CONTENT_TOP + 60, 400, 90, ["学生提问"], accent=True)
    sw, sg = 440, 48
    total = 3 * sw + 2 * sg
    c0 = MX + (W - 2 * MX - total) / 2 + sw / 2
    scope_y = CONTENT_TOP + 260
    scopes = [
        s.box(c0, scope_y, sw, 120, ["仅会话文件", "临时上传"]),
        s.box(c0 + sw + sg, scope_y, sw, 120, ["仅知识库", "共享课程材料"]),
        s.box(c0 + 2 * (sw + sg), scope_y, sw, 120, ["并集（默认）", "知识库 + 会话文件"], accent=True),
    ]
    bus1 = (q["b"] + scopes[0]["t"]) / 2
    s.line(q["cx"], q["b"] + 6, q["cx"], bus1, marker_end=None, sw=2.0)
    s.line(scopes[0]["cx"], bus1, scopes[2]["cx"], bus1, marker_end=None, sw=2.0)
    for sc in scopes:
        s.line(sc["cx"], bus1, sc["cx"], sc["t"] - 6, sw=2.0)
    idx = s.box(CX, CONTENT_TOP + 500, 720, 110, ["本次提问的索引包", "解析切块 → 嵌入 → FAISS"], accent=True)
    bus2 = (scopes[0]["b"] + idx["t"]) / 2
    for sc in scopes:
        s.line(sc["cx"], sc["b"] + 6, sc["cx"], bus2, marker_end=None, sw=2.0)
    s.line(scopes[0]["cx"], bus2, scopes[2]["cx"], bus2, marker_end=None, sw=2.0)
    s.line(idx["cx"], bus2, idx["cx"], idx["t"] - 6, sw=2.0)
    ans = s.box(CX, CONTENT_TOP + 700, 540, 95, ["混合检索 + 有据回答"])
    s.arrow_v(idx, ans)
    s.footer("分类筛选可进一步缩小知识库贡献范围。")
    return _write("fig_4_3_scope", s)


def build_fig_4_4() -> Path:
    s = _s("图 4.4 师生流程")
    s.bg()
    s.heading("教师与学生流程")

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
        lx, y_top, side_w, top_h,
        ["教师", "整理课程材料", "发布课堂练习", "导出成绩"],
        accent=True, title_size=26, body_size=20,
    )
    kb = s.box(
        CX, y_top, kb_w, top_h,
        ["共享课程知识库", "分类 · 笔记 · 附件", "一门课一个知识库"],
        accent=True, title_size=24, body_size=19,
    )
    student = s.box(
        rx, y_top, side_w, top_h,
        ["学生", "提问", "参加测验", "个人会话隔离"],
        accent=True, title_size=26, body_size=20,
    )
    export = s.box(
        lx, y_bot, side_w, bot_h,
        ["导出边界", "仅提问与成绩", "不含对话正文"],
        title_size=24, body_size=19,
    )
    sess = s.box(
        rx, y_bot, side_w, bot_h,
        ["会话上传", "临时文件", "不进入共享知识库"],
        title_size=24, body_size=19,
    )

    s.arrow_h(teacher, kb, y=y_top)
    s.text((teacher["r"] + kb["l"]) / 2, y_top - 18, "写入", size=18, fill="#64748b", anchor="middle")
    s.arrow_h(kb, student, y=y_top)
    s.text((kb["r"] + student["l"]) / 2, y_top - 18, "读取", size=18, fill="#64748b", anchor="middle")
    s.arrow_v(teacher, export)
    s.text(lx + 14, (teacher["b"] + export["t"]) / 2 + 6, "成绩", size=18, fill="#64748b", anchor="start")
    s.arrow_v(student, sess)
    s.text(rx + 14, (student["b"] + sess["t"]) / 2 + 6, "私有", size=18, fill="#64748b", anchor="start")

    s.footer("教师写知识库，学生读知识库；会话附件不进入共享语料。")
    return _write("fig_4_4_roles", s)


def build_fig_5_1() -> Path:
    s = _s("图 5.1 端到端管线")
    s.bg()
    s.heading("端到端检索与生成管线")

    def row(label: str, y: float, items: list[str], accents: set[int]) -> None:
        s.text(MX + 10, y - 48, label, size=18, weight="700", fill="#2563eb")
        n, gap = len(items), 44
        bw = (W - 2 * MX - gap * (n - 1)) / n
        boxes = []
        for i, t in enumerate(items):
            cx = MX + i * (bw + gap) + bw / 2
            boxes.append(s.box(cx, y, bw, 88, [t], accent=i in accents, title_size=15, body_size=13))
        for i in range(1, n):
            s.arrow_h(boxes[i - 1], boxes[i], pad=8)

    row("入库", CONTENT_TOP + 85, ["上传 / 写知识库", "解析（含 OCR）", "按 token 切块", "嵌入", "FAISS"], {4})
    row("检索", CONTENT_TOP + 265, ["提问", "改写+HyDE", "稠密", "BM25", "RRF", "重排", "纠错≤1"], {0, 5})
    row("生成", CONTENT_TOP + 445, ["门控", "证据筛选", "LiM 重排", "大模型作答", "引用", "落库"], {0, 3})
    band_y = CONTENT_TOP + 580
    band_h = CONTENT_BOT - band_y - 8
    s.rect(MX, band_y, W - 2 * MX, band_h, fill="#f8fafc", stroke="#cbd5e1", rx=12)
    s.text(CX, band_y + 32, "依据判定结果", size=20, weight="700", anchor="middle")
    ow, og = 400, 48
    total = 3 * ow + 2 * og
    c0 = MX + (W - 2 * MX - total) / 2 + ow / 2
    cy = band_y + band_h / 2 + 18
    s.box(c0, cy, ow, 110, ["grounded 有据", "课程约束作答"], accent=True, title_size=18, body_size=15)
    s.box(c0 + ow + og, cy, ow, 110, ["weak 偏弱", "通识回答并标注"], title_size=18, body_size=15)
    s.box(c0 + 2 * (ow + og), cy, ow, 110, ["none 无据", "通识回答并标注"], title_size=18, body_size=15)
    s.footer("安全策略：证据不足时走通识回答并明确标注。")
    return _write("fig_5_1_pipeline", s)


def build_fig_5_2() -> Path:
    s = _s("图 5.2 RRF")
    s.bg()
    s.heading("倒数排名融合（RRF）", "RRF(c) = Σ 1 / (k + rankᵢ(c) + 1)，k = 60")
    col_w, item_h, item_gap = 440, 90, 18
    fuse_w, fuse_h = 440, 180
    side = 40
    lx = MX + side + col_w / 2
    rx = W - MX - side - col_w / 2
    hdr_y = CONTENT_TOP + 120
    left = s.box(lx, hdr_y, col_w, 100, ["稠密列表（FAISS）", "语义相似"], title_size=20, body_size=17)
    s.box(rx, hdr_y, col_w, 100, ["词法列表（BM25）", "精确术语 / 数值"], title_size=20, body_size=17)
    list_cy0 = left["b"] + 20 + item_h / 2
    left_items, right_items = [], []
    for i, t in enumerate(["1. 概览页", "2. 相关公式", "3. 邻近例题", "4. 目标百分比段落"]):
        left_items.append(
            s.box(lx, list_cy0 + i * (item_h + item_gap), col_w, item_h, [t], fill="#eff6ff", stroke="#93c5fd", title_size=18)
        )
    for i, t in enumerate(["1. 目标百分比段落", "2. 数值表", "3. 图注行", "4. 其他词命中"]):
        right_items.append(
            s.box(rx, list_cy0 + i * (item_h + item_gap), col_w, item_h, [t], fill="#f0fdf4", stroke="#86efac", title_size=18)
        )
    fuse_cy = (left_items[0]["t"] + left_items[-1]["b"]) / 2
    fuse = s.box(CX, fuse_cy, fuse_w, fuse_h, ["RRF 融合", "候选池 ≤ 36", "无需分数标定"], accent=True, title_size=22, body_size=18)
    s.arrow_h(left_items[1], fuse)
    s.arrow_h(right_items[1], fuse)
    out = s.box(
        CX,
        left_items[-1]["b"] + 85,
        780,
        130,
        ["融合后首位", "目标百分比段落提升", "稠密第4 + 词法第1"],
        accent=True,
        title_size=22,
        body_size=18,
    )
    s.arrow_v(fuse, out)
    s.footer("BM25 找回字面百分比与标识符；融合无需调权即可提升它们。")
    return _write("fig_5_2_rrf", s)


def build_fig_5_3() -> Path:
    s = _s("图 5.3 三档依据门控")
    s.bg()
    s.heading("三档依据门控")
    top = s.box(CX, CONTENT_TOP + 55, 560, 96, ["检索得分前两名  s₁ 、 s₂"], accent=True, title_size=20)
    mid = s.box(CX, CONTENT_TOP + 210, 720, 110, ["按分数尺度选择阈值", "重排体制 或 余弦体制"], title_size=19, body_size=16)
    s.arrow_v(top, mid)
    bw, bg = 440, 48
    total = 3 * bw + 2 * bg
    c0 = MX + (W - 2 * MX - total) / 2 + bw / 2
    band_y = CONTENT_TOP + 430
    none = s.box(c0, band_y, bw, 180, ["none 无据", "证据过弱", "→ 通识回答", "标注非课程结论"], fill="#fef2f2", stroke="#ef4444", title_size=20, body_size=16)
    weak = s.box(c0 + bw + bg, band_y, bw, 180, ["weak 偏弱", "部分支持", "→ 通识回答", "标注非课程结论"], fill="#fff7ed", stroke="#f59e0b", title_size=20, body_size=16)
    grounded = s.box(c0 + 2 * (bw + bg), band_y, bw, 180, ["grounded 有据", "课程可支撑", "→ 约束大模型", "带引用"], accent=True, title_size=20, body_size=16)
    bus = (mid["b"] + none["t"]) / 2
    s.line(mid["cx"], mid["b"] + 6, mid["cx"], bus, marker_end=None, sw=2.0)
    s.line(none["cx"], bus, grounded["cx"], bus, marker_end=None, sw=2.0)
    for box in (none, weak, grounded):
        s.line(box["cx"], bus, box["cx"], box["t"] - 6, sw=2.0)
    note_y = none["b"] + 36
    note_h = CONTENT_BOT - note_y - 8
    s.rect(MX, note_y, W - 2 * MX, note_h, fill="#f8fafc", stroke="#cbd5e1", rx=12)
    s.text(CX, note_y + note_h / 2 - 28, "判定重点", size=20, weight="700", anchor="middle")
    s.text(CX, note_y + note_h / 2 + 8, "更看重两条支撑段落，而不是单条虚高的首位命中。", size=17, anchor="middle")
    s.text(CX, note_y + note_h / 2 + 36, "另设更高的单条强阈值，保留“一条决定性证据”的合理情形。", size=17, anchor="middle")
    s.footer("安全方向：证据不足 → 通识回答并明确标注。")
    return _write("fig_5_3_grounding", s)


def build_fig_5_4() -> Path:
    s = _s("图 5.4 Lost-in-the-Middle")
    s.bg()
    s.heading("Lost-in-the-Middle 证据重排")
    lx, rx = 360, W - 360
    col_w = 400
    s.text(lx, CONTENT_TOP + 28, "重排前（按分数）", size=20, weight="700", anchor="middle")
    s.text(rx, CONTENT_TOP + 28, "重排后（两端优先）", size=20, weight="700", anchor="middle")
    before = ["#1 最强", "#2", "#3", "#4", "#5 最弱"]
    after = ["#1 最强", "#3", "#5 最弱", "#4", "#2"]
    list_top = CONTENT_TOP + 70
    step = (CONTENT_BOT - list_top - 20) / 5
    bh = step - 20
    before_boxes, after_boxes = [], []
    for i, t in enumerate(before):
        before_boxes.append(s.box(lx, list_top + i * step + step / 2, col_w, bh, [t], accent=(i == 0), title_size=18))
    for i, t in enumerate(after):
        after_boxes.append(s.box(rx, list_top + i * step + step / 2, col_w, bh, [t], accent=(i in (0, 2)), title_size=18))
    mid = s.box(CX, (CONTENT_TOP + CONTENT_BOT) / 2, 300, 120, ["重排", "强证据放在", "提示词两端"], accent=True, title_size=18, body_size=15)
    s.line(before_boxes[2]["r"] + 6, mid["cy"], mid["l"] - 6, mid["cy"], sw=2.0)
    s.line(mid["r"] + 6, mid["cy"], after_boxes[2]["l"] - 6, mid["cy"], sw=2.0)
    s.footer("长上下文中模型更关注两端；中间安排较弱证据。", "子块进提示词前可展开为父级页面。")
    return _write("fig_5_4_lim", s)


def build_fig_6_1() -> Path:
    s = _s("图 6.1 SOLO 闭环")
    s.bg()
    s.heading("SOLO 导向的练习与反馈闭环")
    bw, bh = 440, 160
    y1, y2 = CONTENT_TOP + 180, CONTENT_TOP + 500
    c0 = MX + 50 + bw / 2
    c1, c2 = CX, W - MX - 50 - bw / 2
    a = s.box(c0, y1, bw, bh, ["1. 课程材料", "共享知识库"], accent=True, title_size=22, body_size=18)
    b = s.box(c1, y1, bw, bh, ["2. 有据问答", "带引用回答"], accent=True, title_size=22, body_size=18)
    c = s.box(c2, y1, bw, bh, ["3. 分层测验", "SOLO 题型分布"], accent=True, title_size=22, body_size=18)
    d = s.box(c2, y2, bw, bh, ["4. 自动批改", "讲解反馈"], accent=True, title_size=22, body_size=18)
    e = s.box(c1, y2, bw, bh, ["5. 教师导出", "共性难点"], accent=True, title_size=22, body_size=18)
    s.arrow_h(a, b)
    s.arrow_h(b, c)
    s.arrow_v(c, d)
    s.arrow_h(d, e)
    rail = e["b"] + 60
    s.line(e["cx"], e["b"] + 8, e["cx"], rail, marker_end=None, stroke="#94a3b8", sw=2.0)
    s.line(e["cx"], rail, a["cx"], rail, marker_end=None, stroke="#94a3b8", sw=2.0, dash="8 6")
    s.line(a["cx"], rail, a["cx"], a["b"] + 8, stroke="#94a3b8", sw=2.0, dash="8 6")
    s.text((e["cx"] + a["cx"]) / 2, rail - 14, "教学反馈", size=18, fill="#64748b", anchor="middle")
    s.footer("材料理解 → 练习作答 → 作答评价 → 教师反馈", "每步仅一条入边；自动批改只接收来自分层测验的箭头。")
    return _write("fig_6_1_solo", s)


def build_fig_7_1() -> Path:
    s = _s("图 7.1 同步问答生命周期")
    s.bg()
    s.heading("同步问答请求生命周期")
    actors = ["前端", "API", "编排器", "文档索引", "检索管线", "大模型", "会话库"]
    n, gap = len(actors), 20
    bw = (W - 2 * MX - gap * (n - 1)) / n
    xs = []
    for i, a in enumerate(actors):
        cx = MX + i * (bw + gap) + bw / 2
        xs.append(cx)
        s.box(cx, CONTENT_TOP + 45, bw, 76, [a], accent=(a in ("编排器", "检索管线")), title_size=15)
        s.line(cx, CONTENT_TOP + 95, cx, CONTENT_BOT - 36, marker_end=None, stroke="#e2e8f0", sw=1.4)

    def msg(y: float, i: int, j: int, label: str) -> None:
        s.line(xs[i], y, xs[j], y, sw=2.0)
        s.text((xs[i] + xs[j]) / 2, y - 12, label, size=15, fill="#334155", anchor="middle")

    y0 = CONTENT_TOP + 150
    step = (CONTENT_BOT - 50 - y0) / 8
    ys = [y0 + i * step for i in range(9)]
    msg(ys[0], 0, 1, "1. 提交问题（鉴权）")
    msg(ys[1], 1, 2, "2. 会话锁")
    msg(ys[2], 2, 3, "3. 构建/加载索引")
    msg(ys[3], 2, 4, "4. 混合检索 + 重排")
    msg(ys[4], 4, 2, "5. 门控 + 证据筛选")
    msg(ys[5], 2, 5, "6. 调用大模型")
    msg(ys[6], 5, 2, "7. 引用 / 覆盖率")
    msg(ys[7], 2, 6, "8. 消息落库")
    msg(ys[8], 1, 0, "9. 返回回答")
    s.footer("异步路径共用同一 worker；客户端轮询任务 ID。", "落库元数据：路由、依据档、充分性、引用覆盖率。")
    return _write("fig_7_1_lifecycle", s)


def build_fig_8_1() -> Path:
    s = _s("图 8.1 门控标定")
    s.bg()
    s.heading("依据门控标定（余弦体制，65 题）")
    ox, oy = MX + 200, CONTENT_BOT - 48
    pw, ph = W - 2 * MX - 320, CONTENT_BOT - CONTENT_TOP - 100
    s.rect(ox, oy - ph, pw, ph, fill="#ffffff", stroke="#94a3b8", sw=1.8, rx=6)
    s.text(ox + pw / 2, oy + 34, "有据召回率", size=18, weight="700", anchor="middle")
    s.text(ox - 56, oy - ph / 2, "有据", size=17, weight="700", anchor="middle")
    s.text(ox - 56, oy - ph / 2 + 22, "精确率", size=17, weight="700", anchor="middle")

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
        (1.000, 0.905, "原始设置", "0.50 / 0.60 / 0.35", "#94a3b8", "br"),
        (0.754, 1.000, "仅提高首位阈值", "0.69 / 0.72 / 0.35", "#f59e0b", "bl"),
        (0.982, 0.982, "选定设置", "0.62 / 0.75 / 0.62", "#2563eb", "tl"),
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
    s.footer("仅抬高首位阈值会牺牲召回；选定规则同时改善精确率与召回率的前沿。")
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
    print(f"[done] {len(FIGURES)} 中文 SVG → {OUT}")


if __name__ == "__main__":
    main()
