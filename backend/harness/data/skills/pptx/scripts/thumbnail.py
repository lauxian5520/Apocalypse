#!/usr/bin/env python3
"""Render a deck to PNG pages with LibreOffice.

Two uses: the user gets something to eyeball, and a successful render is proof
the file actually opens in a real Office implementation — which reading the XML
back cannot tell you.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="把 pptx 渲染成 PNG（每页一张）")
    ap.add_argument("source")
    ap.add_argument("-d", "--outdir", default="thumbnails")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    source = Path(args.source)
    if not source.is_file():
        sys.exit(f"找不到文件：{source}")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        sys.exit("找不到 LibreOffice（soffice）。装了才能出缩略图。")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # LibreOffice writes its profile into HOME; the sandbox points HOME at the
    # workspace, so give it a scratch profile instead of littering.
    with tempfile.TemporaryDirectory() as profile:
        done = subprocess.run(
            [soffice, "--headless", "--norestore",
             f"-env:UserInstallation=file://{profile}",
             "--convert-to", "pdf", "--outdir", str(outdir), str(source)],
            capture_output=True, text=True, timeout=args.timeout)
    if done.returncode != 0:
        sys.exit(f"LibreOffice 转换失败：{(done.stderr or done.stdout).strip()[:400]}")

    pdf = outdir / (source.stem + ".pdf")
    if not pdf.is_file():
        sys.exit("LibreOffice 没有产出 PDF，文件可能损坏")

    made = [pdf.name]
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        subprocess.run([pdftoppm, "-png", "-r", "80", str(pdf),
                        str(outdir / source.stem)], check=False, timeout=args.timeout)
        made = sorted(p.name for p in outdir.glob(f"{source.stem}*.png")) or made

    print(f"已渲染到 {outdir}/：{', '.join(made)}")
    print("（渲染成功即说明这个文件能被真正的 Office 程序打开）")


if __name__ == "__main__":
    main()
