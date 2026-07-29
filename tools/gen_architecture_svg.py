#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HKU Teacher-student Co-learning (SOLO) Bot — 16:9 system architecture SVG.

Usage:
  py -3.12 tools/gen_architecture_svg.py              # Chinese SVG + prompt
  py -3.12 tools/gen_architecture_svg.py --lang en    # English SVG + prompt
  py -3.12 tools/gen_architecture_svg.py --lang en --chatgpt
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
W, H = 1920, 1080

CONTENT = {
    "zh": {
        "title": "HKU Teacher-student Co-learning (SOLO) Bot 系统架构",
        "out_svg": REPO / "docs" / "SCG_CN" / "architecture_system_16x9.svg",
        "out_prompt": REPO / "docs" / "SVG" / "architecture_chatgpt_prompt.txt",
        "legend": [
            "蓝色强调框 = 主链路（助手问答 · 检索索引 · 嵌入）",
            "自上而下：前端 → 接口 → 业务 → 存储 → 模型 → 流程",
        ],
        "layers": [
            {
                "id": "L1",
                "name": "第 1 层 · 前端交互层",
                "label": "前端交互",
                "modules": [
                    {
                        "title": "登录 / 教师台",
                        "bullets": ["师生角色入口", "账号登录与注册", "用户与注册码管理"],
                    },
                    {
                        "title": "AI 助手",
                        "bullets": ["个人会话（他人不可见）", "上传临时材料并提问", "回答带出处引用"],
                        "accent": True,
                    },
                    {
                        "title": "课程知识库",
                        "bullets": ["分类与笔记管理", "上传课程材料", "发布课堂练习"],
                    },
                    {
                        "title": "测验 / 导出",
                        "bullets": ["在线作答与批改", "导出提问与成绩", "不导出对话正文"],
                    },
                ],
            },
            {
                "id": "L2",
                "name": "第 2 层 · 接口服务层",
                "label": "接口服务",
                "modules": [
                    {
                        "title": "身份与管理",
                        "bullets": ["登录注册鉴权", "教师管理用户", "注册码查看与轮换"],
                    },
                    {
                        "title": "会话与文件",
                        "bullets": ["创建与管理会话", "上传 / 删除附件", "聊天消息存取"],
                        "accent": True,
                    },
                    {
                        "title": "知识库与问答",
                        "bullets": ["知识库读写接口", "同步 / 异步问答", "可选检索范围"],
                    },
                    {
                        "title": "测验与导出",
                        "bullets": ["生成与批改测验", "课堂练习发布", "成绩 / 提问导出"],
                    },
                ],
            },
            {
                "id": "L3",
                "name": "第 3 层 · 核心业务层",
                "label": "核心业务",
                "modules": [
                    {
                        "title": "问答编排",
                        "bullets": ["汇总可用材料", "调度检索与生成", "控制并发与任务"],
                        "accent": True,
                    },
                    {
                        "title": "文档处理",
                        "bullets": ["解析课件与附件", "智能切分文本", "建立向量索引"],
                    },
                    {
                        "title": "检索增强",
                        "bullets": ["语义 + 关键词检索", "结果融合与重排", "必要时自动再查"],
                    },
                    {
                        "title": "生成与练习",
                        "bullets": ["依据材料生成回答", "引用与质量门控", "出题、批改、导出"],
                    },
                ],
            },
            {
                "id": "L4",
                "name": "第 4 层 · 数据与存储层",
                "label": "数据存储",
                "modules": [
                    {
                        "title": "会话数据库",
                        "bullets": ["会话与聊天记录", "测验题目与结果", "不存文档向量"],
                    },
                    {
                        "title": "业务数据库",
                        "bullets": ["用户与登录令牌", "知识库分类与笔记", "课堂练习清单"],
                    },
                    {
                        "title": "文件存储",
                        "bullets": ["会话临时附件", "课程原始材料", "笔记相关文件"],
                    },
                    {
                        "title": "向量索引",
                        "bullets": ["课程内容向量化", "支撑语义检索", "随材料更新重建"],
                        "accent": True,
                    },
                ],
            },
            {
                "id": "L5",
                "name": "第 5 层 · 模型与外部能力",
                "label": "模型能力",
                "modules": [
                    {
                        "title": "文本嵌入",
                        "bullets": ["把材料变成向量", "支持中英双语", "本地运行"],
                        "accent": True,
                    },
                    {
                        "title": "结果重排",
                        "bullets": ["精排检索片段", "提高相关度", "本地运行"],
                    },
                    {
                        "title": "大语言模型",
                        "bullets": ["生成回答与讲解", "改写问题 / 出题", "云端 API 调用"],
                    },
                    {
                        "title": "部署约束",
                        "bullets": ["单机单进程服务", "浏览器直接访问", "前端不直连数据库"],
                    },
                ],
            },
            {
                "id": "L6",
                "name": "第 6 层 · 主流程",
                "label": "主流程",
                "flow": [
                    "上传课程材料",
                    "解析并建索引",
                    "智能检索相关内容",
                    "大模型生成回答",
                    "保存回答与引用",
                    "生成测验练习",
                    "批改并导出成绩",
                ],
            },
        ],
        "prompt_intro": (
            "你是一名信息架构可视化专家。请根据下面的"
            "「HKU Teacher-student Co-learning (SOLO) Bot 系统架构」规格，"
            "生成一张 **16:9 横向系统架构矢量图**。"
        ),
        "prompt_rules": [
            "**只输出一份完整、可保存的 SVG 代码**（以 <svg 开头、</svg> 结束），不要 Markdown 围栏外的解释。",
            'viewBox 必须是 `0 0 1920 1080`（16:9），可用 `width="1920" height="1080"`。',
            "纯矢量：矩形、圆角矩形、文字、细线；不要嵌入位图；不要 emoji。",
            "风格：扁平、专业、浅色底；左侧竖向层标签 + 右侧模块卡片。",
            "主链路模块用稍深描边或浅色填充区分；其余模块浅灰描边。",
            '中文为主；字体用系统无衬线（如 "Microsoft YaHei", "PingFang SC", sans-serif）。',
            "**不要出现工程代号、文件名、路径或 API 路径**；不要副标题；只保留职责说明。",
            "**禁止编造**规格以外的模块或职责（尤其不要把文档向量画进会话数据库）。",
        ],
        "prompt_layout": [
            "顶部仅主标题（无副标题）；其下 6 个水平层带（L1→L6）。",
            "L1–L5：每层 4 个等宽卡片；L6：一条 7 步流程箭头。",
            "左侧固定约 120px 宽的层编号标签列；底部放图例一行。",
        ],
        "step_label": "步骤",
        "main_path": "主链路",
        "flow_wrap": 8,
    },
    "en": {
        "title": "HKU Teacher-student Co-learning (SOLO) Bot System Architecture",
        "out_svg": REPO / "docs" / "SVG" / "architecture_system_16x9_en.svg",
        "out_prompt": REPO / "docs" / "SVG" / "architecture_chatgpt_prompt_en.txt",
        "legend": [
            "Blue accent = primary path (assistant Q&A · retrieval index · embedding)",
            "Top to bottom: UI → API → Business → Storage → Models → Pipeline",
        ],
        "layers": [
            {
                "id": "L1",
                "name": "Layer 1 · Frontend",
                "label": "Frontend",
                "modules": [
                    {
                        "title": "Login / Teacher Console",
                        "bullets": [
                            "Teacher & student entry",
                            "Account login & registration",
                            "User & registration-code admin",
                        ],
                    },
                    {
                        "title": "AI Assistant",
                        "bullets": [
                            "Private sessions per user",
                            "Upload files and ask questions",
                            "Answers with source citations",
                        ],
                        "accent": True,
                    },
                    {
                        "title": "Course Knowledge Base",
                        "bullets": [
                            "Categories and notes",
                            "Upload course materials",
                            "Publish class exercises",
                        ],
                    },
                    {
                        "title": "Quiz / Export",
                        "bullets": [
                            "Online quiz & grading",
                            "Export questions & scores",
                            "No full chat transcripts",
                        ],
                    },
                ],
            },
            {
                "id": "L2",
                "name": "Layer 2 · API Services",
                "label": "API",
                "modules": [
                    {
                        "title": "Auth & Admin",
                        "bullets": [
                            "Login / register auth",
                            "Teacher user management",
                            "Registration code control",
                        ],
                    },
                    {
                        "title": "Sessions & Files",
                        "bullets": [
                            "Create & manage sessions",
                            "Upload / delete attachments",
                            "Chat message storage",
                        ],
                        "accent": True,
                    },
                    {
                        "title": "Knowledge Base & Q&A",
                        "bullets": [
                            "KB read / write APIs",
                            "Sync & async Q&A",
                            "Selectable search scope",
                        ],
                    },
                    {
                        "title": "Quiz & Export",
                        "bullets": [
                            "Generate & grade quizzes",
                            "Publish class exercises",
                            "Export scores / questions",
                        ],
                    },
                ],
            },
            {
                "id": "L3",
                "name": "Layer 3 · Core Business",
                "label": "Business",
                "modules": [
                    {
                        "title": "Q&A Orchestration",
                        "bullets": [
                            "Collect available materials",
                            "Schedule retrieve & generate",
                            "Concurrency & job control",
                        ],
                        "accent": True,
                    },
                    {
                        "title": "Document Processing",
                        "bullets": [
                            "Parse slides & attachments",
                            "Smart text chunking",
                            "Build vector indexes",
                        ],
                    },
                    {
                        "title": "Retrieval Augmentation",
                        "bullets": [
                            "Dense + keyword search",
                            "Fusion and re-ranking",
                            "Corrective re-query if needed",
                        ],
                    },
                    {
                        "title": "Generation & Exercises",
                        "bullets": [
                            "Grounded answer generation",
                            "Citations & quality gates",
                            "Quiz create, grade, export",
                        ],
                    },
                ],
            },
            {
                "id": "L4",
                "name": "Layer 4 · Data & Storage",
                "label": "Storage",
                "modules": [
                    {
                        "title": "Session Store",
                        "bullets": [
                            "Sessions & chat history",
                            "Quiz items & results",
                            "Not for document vectors",
                        ],
                    },
                    {
                        "title": "Business Store",
                        "bullets": [
                            "Users & login tokens",
                            "KB categories & notes",
                            "Class exercise list",
                        ],
                    },
                    {
                        "title": "File Storage",
                        "bullets": [
                            "Session attachments",
                            "Course source files",
                            "Note-related files",
                        ],
                    },
                    {
                        "title": "Vector Index",
                        "bullets": [
                            "Course content embeddings",
                            "Powers semantic search",
                            "Rebuilds when materials change",
                        ],
                        "accent": True,
                    },
                ],
            },
            {
                "id": "L5",
                "name": "Layer 5 · Models & External",
                "label": "Models",
                "modules": [
                    {
                        "title": "Text Embedding",
                        "bullets": [
                            "Turn text into vectors",
                            "Bilingual support",
                            "Runs locally",
                        ],
                        "accent": True,
                    },
                    {
                        "title": "Re-ranking",
                        "bullets": [
                            "Refine retrieved passages",
                            "Improve relevance",
                            "Runs locally",
                        ],
                    },
                    {
                        "title": "Large Language Model",
                        "bullets": [
                            "Generate answers & explanations",
                            "Rewrite queries / create quizzes",
                            "Cloud API",
                        ],
                    },
                    {
                        "title": "Deployment Notes",
                        "bullets": [
                            "Single-process service",
                            "Browser access",
                            "UI never talks to DB directly",
                        ],
                    },
                ],
            },
            {
                "id": "L6",
                "name": "Layer 6 · Main Pipeline",
                "label": "Pipeline",
                "flow": [
                    "Upload materials",
                    "Parse & index",
                    "Retrieve relevant content",
                    "LLM generates answer",
                    "Save answer & citations",
                    "Create quiz / exercise",
                    "Grade & export scores",
                ],
            },
        ],
        "prompt_intro": (
            "You are an information-architecture visualization expert. "
            "Using the specification below for "
            "「HKU Teacher-student Co-learning (SOLO) Bot System Architecture」, "
            "produce one **16:9 landscape system-architecture vector diagram**."
        ),
        "prompt_rules": [
            "**Output only one complete SVG** (starts with <svg, ends with </svg>). No explanations outside the SVG.",
            'viewBox must be `0 0 1920 1080` (16:9); `width="1920" height="1080"` is fine.',
            "Pure vector: rectangles, rounded rectangles, text, thin lines. No bitmaps. No emoji.",
            "Style: flat, professional, light background; left layer labels + right module cards.",
            "Primary-path modules: stronger stroke or light blue fill; others: light gray stroke.",
            'English text; font stack like "Segoe UI", "Helvetica Neue", sans-serif.',
            "**No codenames, filenames, paths, or API routes. No subtitle.** Role descriptions only.",
            "**Do not invent** modules beyond the spec (especially: document vectors do not live in the session store).",
        ],
        "prompt_layout": [
            "Title only at the top (no subtitle); then six horizontal bands L1→L6.",
            "L1–L5: four equal-width cards per layer; L6: a 7-step flow with arrows.",
            "Left ~120px layer-id column; one legend line at the bottom.",
        ],
        "step_label": "Steps",
        "main_path": "primary path",
        "flow_wrap": 18,
    },
}


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _wrap_flow_text(text: str, max_chars: int) -> list[str]:
    """Wrap pipeline step labels without splitting whole words."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    # Prefer breaking on spaces (English / mixed labels)
    if " " in text:
        words = text.split()
        lines: list[str] = []
        cur = ""
        for w in words:
            trial = w if not cur else f"{cur} {w}"
            if len(trial) <= max_chars:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                # Extremely long token: keep intact on its own line
                cur = w
        if cur:
            lines.append(cur)
        return lines[:3]

    # CJK / no spaces: wrap by character budget
    lines = []
    rest = text
    while rest and len(lines) < 3:
        if len(rest) <= max_chars or len(lines) == 2:
            lines.append(rest)
            break
        lines.append(rest[:max_chars])
        rest = rest[max_chars:]
    return lines


def build_chatgpt_prompt(lang: str) -> str:
    cfg = CONTENT[lang]
    lines: list[str] = []
    for layer in cfg["layers"]:
        lines.append(f"### {layer['name']}")
        if "flow" in layer:
            lines.append(f"{cfg['step_label']}: " + " → ".join(layer["flow"]))
        else:
            for m in layer["modules"]:
                tag = f" [{cfg['main_path']}]" if m.get("accent") else ""
                lines.append(f"- {m['title']}{tag}: " + "; ".join(m["bullets"]))
        lines.append("")
    body = "\n".join(lines).rstrip()
    rules = "\n".join(f"{i}. {r}" for i, r in enumerate(cfg["prompt_rules"], 1))
    layout = "\n".join(f"- {x}" for x in cfg["prompt_layout"])
    legend = "\n".join(f"- {x}" for x in cfg["legend"])
    closing = "Now output the SVG only." if lang == "en" else "现在直接输出 SVG。"
    return (
        f"{cfg['prompt_intro']}\n\n"
        f"## Hard requirements\n{rules}\n\n"
        f"## Title\n- {cfg['title']}\n"
        f"- No subtitle.\n\n"
        f"## Layer specification (must include all; layout may flex, meaning must not)\n\n"
        f"{body}\n\n"
        f"## Legend\n{legend}\n\n"
        f"## Layout hints\n{layout}\n\n"
        f"{closing}"
    )


def _card(
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    bullets: list[str],
    accent: bool = False,
) -> str:
    stroke = "#2563eb" if accent else "#94a3b8"
    fill = "#eff6ff" if accent else "#ffffff"
    sw = 2.0 if accent else 1.2
    parts = [
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="10" ry="10" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>',
        f'<text x="{x + 14:.1f}" y="{y + 28:.1f}" font-size="15" font-weight="700" '
        f'fill="#0f172a">{_esc(title)}</text>',
    ]
    by = y + 52
    for b in bullets[:3]:
        parts.append(
            f'<text x="{x + 14:.1f}" y="{by:.1f}" font-size="12.5" fill="#334155">'
            f"· {_esc(b)}</text>"
        )
        by += 19
    return "\n".join(parts)


def build_svg(lang: str) -> str:
    cfg = CONTENT[lang]
    title = cfg["title"]
    layers = cfg["layers"]
    legend = cfg["legend"]
    wrap = int(cfg["flow_wrap"])

    margin_x, margin_top = 36, 28
    label_w = 118
    content_x = margin_x + label_w + 16
    content_w = W - content_x - margin_x
    title_h = 52  # no subtitle
    legend_h = 34
    gap_y = 10
    usable_h = H - margin_top - title_h - legend_h - 18
    layer_h = (usable_h - gap_y * (len(layers) - 1)) / len(layers)
    title_size = 22 if len(title) > 55 else 24

    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="{_esc(title)}">',
        "<defs>",
        '<style type="text/css"><![CDATA[',
        "  text { font-family: 'Segoe UI', 'Helvetica Neue', 'Microsoft YaHei', "
        "'PingFang SC', sans-serif; }",
        "]]></style>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/>',
        "</marker>",
        "</defs>",
        f'<rect width="{W}" height="{H}" fill="#f8fafc"/>',
        f'<rect x="0" y="0" width="{W}" height="4" fill="#2563eb"/>',
        f'<text x="{margin_x}" y="{margin_top + 30}" font-size="{title_size}" '
        f'font-weight="700" fill="#0f172a">{_esc(title)}</text>',
    ]

    y0 = margin_top + title_h
    for i, layer in enumerate(layers):
        y = y0 + i * (layer_h + gap_y)
        out.append(
            f'<rect x="{margin_x}" y="{y:.1f}" width="{W - 2 * margin_x}" '
            f'height="{layer_h:.1f}" rx="12" fill="#ffffff" stroke="#e2e8f0" '
            f'stroke-width="1"/>'
        )
        out.append(
            f'<rect x="{margin_x}" y="{y:.1f}" width="{label_w}" height="{layer_h:.1f}" '
            f'rx="12" fill="#f1f5f9" stroke="#e2e8f0"/>'
        )
        out.append(
            f'<text x="{margin_x + label_w / 2:.1f}" y="{y + layer_h / 2 - 10:.1f}" '
            f'text-anchor="middle" font-size="13" font-weight="700" fill="#2563eb">'
            f"{_esc(layer['id'])}</text>"
        )
        out.append(
            f'<text x="{margin_x + label_w / 2:.1f}" y="{y + layer_h / 2 + 10:.1f}" '
            f'text-anchor="middle" font-size="11" fill="#475569">'
            f"{_esc(layer['label'])}</text>"
        )

        # No layer title text — cards use the full band height
        pad_in = 14
        inner_top = y + pad_in
        inner_h = layer_h - 2 * pad_in

        if "flow" in layer:
            steps = layer["flow"]
            n = len(steps)
            gap = 40  # keep a clear channel so arrows are fully visible
            sw = (content_w - gap * (n - 1)) / n
            step_boxes: list[tuple[float, float]] = []
            for j, step in enumerate(steps):
                sx = content_x + j * (sw + gap)
                step_boxes.append((sx, sw))
                out.append(
                    f'<rect x="{sx:.1f}" y="{inner_top:.1f}" width="{sw:.1f}" '
                    f'height="{inner_h:.1f}" rx="8" fill="#eff6ff" stroke="#93c5fd" '
                    f'stroke-width="1.2"/>'
                )
                out.append(
                    f'<text x="{sx + 10:.1f}" y="{inner_top + 22:.1f}" font-size="12" '
                    f'font-weight="700" fill="#2563eb">{j + 1:02d}</text>'
                )
                lines = _wrap_flow_text(step, wrap)
                for k, line in enumerate(lines):
                    out.append(
                        f'<text x="{sx + 10:.1f}" y="{inner_top + 46 + k * 18:.1f}" '
                        f'font-size="13" fill="#0f172a">{_esc(line)}</text>'
                    )
            # Draw arrows after all step boxes so markers are never covered
            ay = inner_top + inner_h / 2
            for j in range(n - 1):
                sx, swj = step_boxes[j]
                nx = step_boxes[j + 1][0]
                ax1 = sx + swj + 4
                ax2 = nx - 4
                out.append(
                    f'<line x1="{ax1:.1f}" y1="{ay:.1f}" x2="{ax2:.1f}" y2="{ay:.1f}" '
                    f'stroke="#64748b" stroke-width="1.8" marker-end="url(#arrow)"/>'
                )
        else:
            mods = layer["modules"]
            n = len(mods)
            gap = 12
            cw = (content_w - gap * (n - 1)) / n
            for j, m in enumerate(mods):
                cx = content_x + j * (cw + gap)
                out.append(
                    _card(
                        cx,
                        inner_top,
                        cw,
                        inner_h,
                        m["title"],
                        m["bullets"],
                        bool(m.get("accent")),
                    )
                )

    ly = H - legend_h + 10
    out.append(
        f'<text x="{margin_x}" y="{ly}" font-size="12" fill="#64748b">'
        f"{_esc(' · '.join(legend))}</text>"
    )
    out.append("</svg>")
    return "\n".join(out)


def write_outputs(lang: str, output: Path | None = None) -> tuple[Path, Path]:
    cfg = CONTENT[lang]
    svg_path = output or cfg["out_svg"]
    prompt_path = cfg["out_prompt"]
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(build_svg(lang), encoding="utf-8")
    prompt_path.write_text(build_chatgpt_prompt(lang), encoding="utf-8")
    return svg_path, prompt_path


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate SOLO Bot 16:9 architecture SVG / ChatGPT prompt"
    )
    ap.add_argument("--lang", choices=("zh", "en"), default="zh")
    ap.add_argument("--chatgpt", action="store_true", help="Print ChatGPT prompt only")
    ap.add_argument("--all", action="store_true", help="Write both zh and en outputs")
    ap.add_argument("-o", "--output", type=Path, default=None)
    args = ap.parse_args()

    if args.chatgpt:
        print(build_chatgpt_prompt(args.lang))
        return

    langs = ("zh", "en") if args.all else (args.lang,)
    for lang in langs:
        svg_path, prompt_path = write_outputs(
            lang, args.output if (not args.all and args.output) else None
        )
        print(f"[ok] {lang}: {svg_path}")
        print(f"[ok] {lang}: {prompt_path}")


if __name__ == "__main__":
    main()
