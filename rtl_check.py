"""
Flags CSS that is hard-coded to left/right and may break in right-to-left (Arabic/Hebrew) layouts.
Works on any text file: a .css file or your exported APEX application .sql file.

Usage:  python rtl_check.py path/to/file.css
"""
import re
import sys
from pathlib import Path

RULES = [
    (r"text-align\s*:\s*(left|right)", "text-align is direction-specific; use start/end"),
    (r"float\s*:\s*(left|right)", "float is direction-specific; consider flexbox or inline-start/end"),
    (r"(margin|padding)-(left|right)\s*:", "use margin-inline-start/end or padding-inline-start/end"),
    (r"border-(left|right)(-\w+)?\s*:", "use border-inline-start/end"),
    (r"(?<![\w-])(left|right)\s*:\s*-?\d", "absolute left/right offset; use inset-inline-start/end"),
    (r"translateX\(", "translateX moves the wrong way in RTL; check this one visually"),
]


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python rtl_check.py <file>")
    path = Path(sys.argv[1])
    hits = 0
    for no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        for pattern, advice in RULES:
            if re.search(pattern, line, re.I):
                hits += 1
                print(f"line {no}: {line.strip()[:110]}\n         -> {advice}")
    print(f"\n{hits} potential RTL issue(s) found.")


if __name__ == "__main__":
    main()
