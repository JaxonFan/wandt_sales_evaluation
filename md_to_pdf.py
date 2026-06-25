#!/usr/bin/env python3
"""Render a markdown doc -> self-contained styled HTML -> PDF (headless Chrome).

Usage: sales_evaluation/bin/python md_to_pdf.py wandt_bonus_explainer.md
"""
import re
import subprocess
import sys
from pathlib import Path

import markdown

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CSS = """
body { font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; font-size:13.5px; line-height:1.7; color:#1f2733; max-width:780px; margin:34px auto; padding:0 28px; }
h1 { font-size:24px; margin:0 0 4px; color:#11243a; }
h1 + p em, h1 + p { color:#6b7785; }
h2 { font-size:16.5px; margin:24px 0 8px; border-bottom:1px solid #e2e7ee; padding-bottom:5px; color:#11243a; }
blockquote { color:#34404f; border-left:4px solid #2b6cb0; margin:14px 0; padding:8px 16px; background:#eef4fb; border-radius:0 6px 6px 0; }
strong { color:#11243a; } code { background:#eef2f7; padding:1px 5px; border-radius:4px; font-size:12px; }
ul,ol { margin:8px 0; padding-left:24px; } li { margin:6px 0; }
hr { border:none; border-top:1px solid #e2e7ee; margin:22px 0; }
table { border-collapse:collapse; width:100%; margin:12px 0; font-size:12.5px; }
th,td { border:1px solid #dde3ea; padding:6px 9px; text-align:left; } th { background:#f2f5f9; }
p { margin:9px 0; } em { color:#6b7785; }
@page { margin:14mm; }
"""


def convert(md):
    out = []
    for line in md.split("\n"):
        m = re.match(r"^( +)(\S.*)$", line)
        if m and re.match(r"([-*]|\d+\.)\s", m.group(2)):
            depth = round(len(m.group(1)) / 2) or 1
            out.append("    " * depth + m.group(2))
        else:
            out.append(line)
    return markdown.markdown("\n".join(out), extensions=["extra", "sane_lists"], output_format="html5")


def main():
    src = Path(sys.argv[1])
    body = convert(src.read_text(encoding="utf-8"))
    doc = f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><style>{CSS}</style></head><body>{body}</body></html>'
    html_path = src.with_suffix(".html")
    pdf_path = src.with_suffix(".pdf")
    html_path.write_text(doc, encoding="utf-8")
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                   check=True, capture_output=True)
    html_path.unlink(missing_ok=True)
    print(f"wrote {pdf_path.name}")


if __name__ == "__main__":
    main()
