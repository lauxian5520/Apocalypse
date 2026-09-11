#!/usr/bin/env python3
"""Markdown -> .docx

Covers the subset listed in SKILL.md. Anything outside it is written through
as plain text rather than silently dropped, so nothing disappears without the
reader noticing.
"""
import argparse
import re
import sys
from pathlib import Path

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt
except ImportError:
    sys.exit("缺少 python-docx。服务端执行：pip install python-docx")

# Inline spans, longest marker first so ** is not eaten by *.
INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*]+?\*|`[^`]+?`)")
IMAGE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)$")
ORDERED = re.compile(r"^(\d+)\.\s+")


def safe_path(raw: str, base: Path) -> Path:
    target = (base / raw).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise SystemExit(f"素材路径越界，只能引用工作区内的文件：{raw}")
    if not target.is_file():
        raise SystemExit(f"找不到素材文件：{raw}")
    return target


def add_rich_text(paragraph, text: str) -> None:
    """Write text, honouring **bold**, *italic* and `code`."""
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            paragraph.add_run(piece[2:-2]).bold = True
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            run = paragraph.add_run(piece[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(9.5)
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            paragraph.add_run(piece[1:-1]).italic = True
        else:
            paragraph.add_run(piece)


def _field(paragraph, instruction: str) -> None:
    """Insert a Word field. Fields are XML, not text — python-docx has no API."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instr, separate, end):
        run._r.append(node)


def add_toc(doc) -> None:
    """A real TOC field. Word fills it in on open (or on F9)."""
    doc.add_heading("目录", level=1)
    _field(doc.add_paragraph(), r'TOC \o "1-3" \h \z \u')
    doc.add_paragraph(
        "（在 Word 中打开后，若目录为空，按 Ctrl+A 再按 F9 刷新域）"
    ).runs[0].italic = True
    doc.add_page_break()


def add_page_numbers(doc) -> None:
    footer = doc.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _field(footer, "PAGE")


def flush_table(doc, rows: list[list[str]]) -> None:
    if not rows:
        return
    body = [r for r in rows if not all(set(c.strip()) <= {"-", ":"} for c in r if c.strip())]
    if not body:
        return
    table = doc.add_table(rows=len(body), cols=max(len(r) for r in body))
    table.style = "Table Grid"
    for r, row in enumerate(body):
        for c, cell in enumerate(row):
            para = table.cell(r, c).paragraphs[0]
            add_rich_text(para, cell.strip())
            if r == 0:
                for run in para.runs:
                    run.bold = True


def convert(markdown: str, out_path: Path, base: Path, *,
            toc: bool = False, page_numbers: bool = False, title: str = "") -> int:
    doc = Document()
    if title:
        doc.core_properties.title = title
    if page_numbers:
        add_page_numbers(doc)
    if toc:
        add_toc(doc)

    blocks = 0
    in_code = False
    code_lines: list[str] = []
    table_rows: list[list[str]] = []

    def close_table():
        nonlocal table_rows, blocks
        if table_rows:
            flush_table(doc, table_rows)
            table_rows = []
            blocks += 1

    for raw in markdown.splitlines():
        line, stripped = raw.rstrip(), raw.strip()

        if stripped.startswith("```"):
            if in_code:
                run = doc.add_paragraph().add_run("\n".join(code_lines))
                run.font.name = "Consolas"
                run.font.size = Pt(9)
                code_lines = []
                blocks += 1
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(raw)
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            table_rows.append(stripped.strip("|").split("|"))
            continue
        close_table()

        if not stripped:
            continue

        # A horizontal rule is the page break; nothing else in Markdown means it.
        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            blocks += 1
            continue

        picture = IMAGE.match(stripped)
        if picture:
            doc.add_picture(str(safe_path(picture.group("src"), base)), width=Inches(5.8))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            if picture.group("alt"):
                caption = doc.add_paragraph(picture.group("alt"))
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                caption.runs[0].italic = True
                caption.runs[0].font.size = Pt(9)
            blocks += 1
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if heading:
            doc.add_heading(heading.group(2), level=len(heading.group(1)))
        elif stripped.startswith("> "):
            add_rich_text(doc.add_paragraph(style="Intense Quote"), stripped[2:])
        elif stripped.startswith("- "):
            indent = len(line) - len(line.lstrip())
            style = "List Bullet 2" if indent >= 2 else "List Bullet"
            add_rich_text(doc.add_paragraph(style=style), stripped[2:])
        elif ORDERED.match(stripped):
            add_rich_text(doc.add_paragraph(style="List Number"), ORDERED.sub("", stripped))
        else:
            add_rich_text(doc.add_paragraph(), stripped)
        blocks += 1

    close_table()
    doc.save(out_path)
    return blocks


def main() -> None:
    ap = argparse.ArgumentParser(description="Markdown 转 Word")
    ap.add_argument("source")
    ap.add_argument("target")
    ap.add_argument("--toc", action="store_true", help="在正文前插入目录域")
    ap.add_argument("--page-numbers", action="store_true", help="页脚加页码")
    ap.add_argument("--title", default="", help="写入文档属性的标题")
    args = ap.parse_args()

    source, target = Path(args.source), Path(args.target)
    if not source.is_file():
        sys.exit(f"找不到源文件：{source}")

    blocks = convert(source.read_text(encoding="utf-8"), target, Path.cwd(),
                     toc=args.toc, page_numbers=args.page_numbers, title=args.title)
    if not blocks:
        sys.exit("源文件里没有解析出任何内容")
    print(f"已生成 {target}，共 {blocks} 个段落/表格/图片")


if __name__ == "__main__":
    main()
