#!/usr/bin/env python3
"""Read a .docx back as Markdown, via pandoc.

The point is verification: a generated document is a ZIP of XML, so the agent
that just wrote one cannot open it with `read` and check. This turns it back
into text it can actually inspect — and it is also how a document the user
uploaded gets read in the first place.
"""
import argparse
import subprocess
import sys
from pathlib import Path


def pandoc_binary() -> str:
    """Prefer pandoc on PATH; fall back to the copy pypandoc bundles."""
    from shutil import which
    found = which("pandoc")
    if found:
        return found
    try:
        import pypandoc
        return pypandoc.get_pandoc_path()
    except Exception:
        sys.exit("找不到 pandoc。服务端执行：pip install pypandoc_binary")


def main() -> None:
    ap = argparse.ArgumentParser(description="把 .docx 读成 Markdown")
    ap.add_argument("source")
    ap.add_argument("-o", "--output", default="", help="写入文件；留空则打印到标准输出")
    ap.add_argument("--limit", type=int, default=20000,
                    help="打印到标准输出时的字符上限，避免灌爆上下文")
    args = ap.parse_args()

    source = Path(args.source)
    if not source.is_file():
        sys.exit(f"找不到文件：{source}")

    cmd = [pandoc_binary(), "-f", "docx", "-t", "markdown", "--wrap=none", str(source)]
    if args.output:
        cmd += ["-o", args.output]

    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"pandoc 转换失败：{done.stderr.strip()[:500]}")

    if args.output:
        print(f"已写出 {args.output}")
        return

    text = done.stdout
    sys.stdout.write(text[:args.limit])
    if len(text) > args.limit:
        print(f"\n\n[已截断：全文 {len(text)} 字符，用 -o 写成文件再分段读]")


if __name__ == "__main__":
    main()
