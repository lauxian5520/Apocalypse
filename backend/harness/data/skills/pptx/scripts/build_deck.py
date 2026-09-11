#!/usr/bin/env python3
"""Markdown outline -> .pptx

Slide kinds are chosen from what the block contains rather than from a
directive the model has to remember: a table makes a table slide, a chart
fence makes a chart, an image makes a picture slide, a bare title makes a
section divider. See SKILL.md for the syntax.
"""
import csv
import io
import re
import sys
from pathlib import Path

try:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.util import Emu, Inches, Pt
except ImportError:
    sys.exit("缺少 python-pptx。服务端执行：pip install python-pptx")

MAX_BULLETS_WARN = 6
IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)")
CHART_TYPES = {
    "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "hbar": XL_CHART_TYPE.BAR_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "pie": XL_CHART_TYPE.PIE,
}

# Layout indices in the default template.
L_TITLE, L_BULLETS, L_SECTION, L_TITLE_ONLY, L_BLANK = 0, 1, 2, 5, 6


def safe_path(raw: str, base: Path) -> Path:
    """Resolve a referenced asset, refusing anything outside the workspace."""
    target = (base / raw).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise SystemExit(f"素材路径越界，只能引用工作区内的文件：{raw}")
    if not target.is_file():
        raise SystemExit(f"找不到素材文件：{raw}")
    return target


def parse(markdown: str) -> list[dict]:
    """Split an outline into slide specs."""
    slides = []
    for block in re.split(r"^---\s*$", markdown, flags=re.M):
        block = block.strip()
        if not block:
            continue

        spec = {"title": "", "subtitle": "", "bullets": [], "notes": [],
                "images": [], "table": [], "chart": None}
        chart_kind, chart_rows, in_chart = "", [], False

        for raw in block.splitlines():
            line, stripped = raw.rstrip(), raw.strip()

            if stripped.startswith("```"):
                fence = stripped[3:].strip().split()
                if in_chart:
                    spec["chart"] = (chart_kind, chart_rows)
                    in_chart = False
                elif fence and fence[0] == "chart":
                    chart_kind = fence[1] if len(fence) > 1 else "bar"
                    chart_rows, in_chart = [], True
                continue
            if in_chart:
                if stripped:
                    chart_rows.append(stripped)
                continue

            if not stripped:
                continue
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if not all(set(c) <= {"-", ":"} for c in cells if c):
                    spec["table"].append(cells)
                continue

            found = IMAGE.search(stripped)
            if found:
                spec["images"].append((found.group("src"), found.group("alt")))
                continue
            if stripped.startswith("## "):
                spec["subtitle"] = stripped[3:].strip()
            elif stripped.startswith("# "):
                spec["title"] = stripped[2:].strip()
            elif stripped.startswith("> "):
                spec["notes"].append(stripped[2:].strip())
            elif stripped.startswith("- "):
                indent = len(line) - len(line.lstrip())
                spec["bullets"].append((min(indent // 2, 2), stripped[2:].strip()))
            else:
                spec["bullets"].append((0, stripped))

        if in_chart:
            spec["chart"] = (chart_kind, chart_rows)
        if any((spec["title"], spec["bullets"], spec["images"], spec["table"], spec["chart"])):
            slides.append(spec)
    return slides


def add_bullets(slide, bullets: list[tuple[int, str]]) -> None:
    body = slide.placeholders[1].text_frame
    body.clear()
    for i, (level, text) in enumerate(bullets):
        para = body.paragraphs[0] if i == 0 else body.add_paragraph()
        para.text = text
        para.level = level
        para.font.size = Pt(20 - 3 * level)


def add_table(slide, rows: list[list[str]], top: Emu) -> None:
    cols = max(len(r) for r in rows)
    shape = slide.shapes.add_table(len(rows), cols, Inches(0.6), top,
                                   Inches(8.8), Inches(0.4 * len(rows)))
    for r, row in enumerate(rows):
        for c in range(cols):
            cell = shape.table.cell(r, c)
            cell.text = row[c] if c < len(row) else ""
            for para in cell.text_frame.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(14)
                    run.font.bold = r == 0


def add_chart(slide, kind: str, rows: list[str], top: Emu) -> None:
    reader = list(csv.reader(io.StringIO("\n".join(rows))))
    if len(reader) < 2:
        raise SystemExit("chart 数据至少要有表头和一行数据")
    header, body = reader[0], reader[1:]

    data = CategoryChartData()
    data.categories = [r[0] for r in body]
    for col in range(1, len(header)):
        values = []
        for r in body:
            try:
                values.append(float(r[col]) if col < len(r) and r[col] else None)
            except ValueError:
                raise SystemExit(f"chart 数据里 {r[col]!r} 不是数字")
        data.add_series(header[col], values)

    frame = slide.shapes.add_chart(
        CHART_TYPES.get(kind, XL_CHART_TYPE.COLUMN_CLUSTERED),
        Inches(0.8), top, Inches(8.4), Inches(4.6), data)
    chart = frame.chart
    if len(header) > 2 or kind == "pie":
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False


def add_images(slide, images: list[tuple[str, str]], base: Path, top: Emu) -> None:
    """Lay pictures out in a row, scaled to share the remaining width."""
    slide_width = Inches(10)
    margin = Inches(0.6)
    gap = Inches(0.3)
    usable = slide_width - 2 * margin - gap * (len(images) - 1)
    each = int(usable / len(images))
    for i, (src, _alt) in enumerate(images):
        slide.shapes.add_picture(str(safe_path(src, base)),
                                 margin + i * (each + gap), top, width=each)


def build(slides: list[dict], out_path: Path, base: Path) -> tuple[int, list[str]]:
    prs = Presentation()
    warnings: list[str] = []

    for index, spec in enumerate(slides):
        has_body = any((spec["bullets"], spec["table"], spec["chart"], spec["images"]))
        if index == 0 and not has_body:
            layout = L_TITLE
        elif not has_body:
            layout = L_SECTION            # a bare title later on divides sections
        elif spec["bullets"] and not (spec["table"] or spec["chart"] or spec["images"]):
            layout = L_BULLETS
        else:
            layout = L_TITLE_ONLY         # mixed content is placed by hand

        slide = prs.slides.add_slide(prs.slide_layouts[layout])
        if slide.shapes.title is not None:
            slide.shapes.title.text = spec["title"] or "（无标题）"

        if layout in (L_TITLE, L_SECTION):
            if spec["subtitle"] and len(slide.placeholders) > 1:
                slide.placeholders[1].text = spec["subtitle"]
        elif layout == L_BULLETS:
            add_bullets(slide, spec["bullets"])
            if len(spec["bullets"]) > MAX_BULLETS_WARN:
                warnings.append(f"第 {index + 1} 页有 {len(spec['bullets'])} 条要点，"
                                f"超过建议的 {MAX_BULLETS_WARN} 条，考虑拆页")
        else:
            top = Inches(1.8)
            if spec["bullets"]:
                box = slide.shapes.add_textbox(Inches(0.6), top, Inches(8.8), Inches(1.4))
                frame = box.text_frame
                frame.word_wrap = True
                for i, (level, text) in enumerate(spec["bullets"]):
                    para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
                    para.text = ("• " if level == 0 else "– ") + text
                    para.level = level
                    para.font.size = Pt(16)
                top = Emu(top + Inches(0.45 * len(spec["bullets"]) + 0.2))
            if spec["table"]:
                add_table(slide, spec["table"], top)
            elif spec["chart"]:
                add_chart(slide, spec["chart"][0], spec["chart"][1], top)
            elif spec["images"]:
                add_images(slide, spec["images"], base, top)

        if spec["notes"]:
            slide.notes_slide.notes_text_frame.text = "\n".join(spec["notes"])

    prs.save(out_path)
    return len(slides), warnings


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("用法: build_deck.py <大纲.md> <输出.pptx>")
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    if not source.is_file():
        sys.exit(f"找不到大纲文件：{source}")

    slides = parse(source.read_text(encoding="utf-8"))
    if not slides:
        sys.exit("大纲里没有解析出任何幻灯片，检查是否用 --- 分页、用 # 写页标题")

    count, warnings = build(slides, target, Path.cwd())
    for w in warnings:
        print(f"提示：{w}", file=sys.stderr)
    print(f"已生成 {target}，共 {count} 页")


if __name__ == "__main__":
    main()
