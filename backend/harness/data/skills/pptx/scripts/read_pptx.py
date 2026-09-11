#!/usr/bin/env python3
"""Read a .pptx back as a text outline.

Same purpose as the docx reader: a deck cannot be checked with `read`, so this
prints what is actually on each slide — including speaker notes and table
contents — for the agent to verify against what it intended.
"""
import argparse
import sys
from pathlib import Path

try:
    from pptx import Presentation
except ImportError:
    sys.exit("缺少 python-pptx。服务端执行：pip install python-pptx")


def describe(slide, index: int) -> list[str]:
    title = slide.shapes.title.text.strip() if slide.shapes.title else ""
    out = [f"## 第 {index} 页" + (f"：{title}" if title else "")]

    for shape in slide.shapes:
        if shape == slide.shapes.title:
            continue
        if shape.has_table:
            out.append("  [表格]")
            for row in shape.table.rows:
                out.append("    | " + " | ".join(c.text.strip() for c in row.cells))
        elif getattr(shape, "has_chart", False) and shape.has_chart:
            chart = shape.chart
            names = [s.name for s in chart.plots[0].series] if chart.plots else []
            cats = [str(c) for c in chart.plots[0].categories] if chart.plots else []
            out.append(f"  [图表 {chart.chart_type}] 系列={names} 类别={cats}")
        elif shape.shape_type == 13:      # PICTURE
            out.append("  [图片]")
        elif shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                text = para.text.strip()
                if not text:
                    continue
                # Hand-placed text boxes carry their own bullet glyph; don't
                # print a second one and make it look like a formatting bug.
                marker = "" if text[0] in "•–-*" else "• "
                out.append("  " + "  " * para.level + marker + text)

    if slide.has_notes_slide:
        note = slide.notes_slide.notes_text_frame.text.strip()
        if note:
            out.append(f"  [备注] {note}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="把 .pptx 读成文本大纲")
    ap.add_argument("source")
    ap.add_argument("--limit", type=int, default=20000, help="输出字符上限")
    args = ap.parse_args()

    source = Path(args.source)
    if not source.is_file():
        sys.exit(f"找不到文件：{source}")

    try:
        prs = Presentation(str(source))
    except Exception as e:
        sys.exit(f"无法打开（可能不是合法的 pptx）：{e}")

    lines = [f"# {source.name} — 共 {len(prs.slides)} 页"]
    for i, slide in enumerate(prs.slides, 1):
        lines.extend(describe(slide, i))

    text = "\n".join(lines)
    sys.stdout.write(text[:args.limit])
    if len(text) > args.limit:
        print(f"\n\n[已截断：全文 {len(text)} 字符]")


if __name__ == "__main__":
    main()
