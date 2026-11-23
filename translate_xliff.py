"""
APEX XLIFF auto-translator.

Reads an XLIFF file exported from Oracle APEX (Shared Components > Globalization >
Translate Application), translates the <source> text with an LLM while protecting
APEX substitutions (&ITEM.), #SUBSTITUTIONS#, %0/%s, HTML tags and entities,
validates every result, and writes a new XLIFF ready to upload back into APEX.

Strings that fail validation keep their English text (safe fallback) and are
listed in the review report CSV.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent


def load_env_file():
    """Tiny .env loader so no extra package is needed."""
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env_file()

# Things the LLM must never touch
PROTECT = re.compile(
    r"(&\{[A-Za-z0-9_$]+\}\.?|"   # &{TEXT_MESSAGE}.  text-message substitution (APEX 26.1+)
    r"&[A-Za-z0-9_$]+\.|"        # &P1_NAME.   APEX substitution
    r"#[A-Z0-9_]+#|"            # #APP_ID#
    r"%\d+|%[sd]|"              # %0  %1  %s  %d
    r"&[a-z]+;|"                # &amp; &nbsp;
    r"<[^>]+>)"                 # HTML tags
)
TOKEN = re.compile(r"\[\[(\d+)\]\]")


def protect(text):
    """Replace protected pieces with [[1]], [[2]] ... tokens."""
    found = []

    def sub(match):
        found.append(match.group(0))
        return f"[[{len(found)}]]"

    return PROTECT.sub(sub, text), found


def restore(text, found):
    for i, original in enumerate(found, 1):
        text = text.replace(f"[[{i}]]", original)
    return text


def translate_batch(batch, lang_name, glossary, dry_run=False):
    """batch: {key: protected_text} -> {key: translated_protected_text}"""
    if dry_run:  # offline test mode: fake "translation" so you can test the pipeline
        return {k: "ترجمة " + v for k, v in batch.items()}

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY is not set. Put it in .env or set the environment variable.")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    prompt = f"""Translate these UI strings from English to {lang_name} for a business application.
Rules:
- Keep every [[n]] token exactly as written. Never translate, remove, duplicate or reorder the tokens' identity.
- Keep translations concise (they are labels, buttons and messages).
- Use this glossary where the terms appear: {json.dumps(glossary, ensure_ascii=False)}
- Return ONLY a JSON object with exactly the same keys as the input.
Input: {json.dumps(batch, ensure_ascii=False)}"""

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    for attempt in range(4):
        r = requests.post(url, headers={"x-goog-api-key": api_key}, json=body, timeout=90)
        if r.status_code == 429:  # free-tier rate limit: wait and retry
            wait = 20 * (attempt + 1)
            print(f"  rate limited, waiting {wait}s ...")
            time.sleep(wait)
            continue
        if not r.ok:
            hint = ""
            if r.status_code == 404:
                hint = ("\nThe model name was not found (it may have been retired). "
                        "Run: python translate_xliff.py --list-models   and set GEMINI_MODEL in .env")
            sys.exit(f"Gemini API error {r.status_code}: {r.text[:400]}{hint}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
    raise RuntimeError("Rate limited too many times. Try a smaller --batch-size or wait a bit.")


def list_models():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY is not set.")
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                     headers={"x-goog-api-key": api_key}, params={"pageSize": 200}, timeout=60)
    if not r.ok:
        sys.exit(f"Gemini API error {r.status_code}: {r.text[:400]}")
    print("Models that support generateContent (put one name in GEMINI_MODEL):")
    for m in r.json().get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            print("  ", m["name"].replace("models/", ""))


def validate(src, tgt, lang_code):
    issues = []
    if not tgt.strip():
        return ["empty"]
    if sorted(PROTECT.findall(src)) != sorted(PROTECT.findall(tgt)):
        issues.append("placeholder/HTML mismatch")
    ratio = len(tgt) / max(len(src), 1)
    if ratio > 2.5 or ratio < 0.3:
        issues.append(f"length ratio {ratio:.1f}")
    has_words = re.search(r"[A-Za-z]{2,}", PROTECT.sub("", src))
    if has_words and lang_code == "ar" and not re.search(r"[\u0600-\u06FF]", tgt):
        issues.append("no Arabic characters")
    if TOKEN.search(tgt):
        issues.append("leftover token")
    return issues


def run(args):
    glossary = {}
    if Path(args.glossary).exists():
        glossary = json.loads(Path(args.glossary).read_text(encoding="utf-8"))

    tree = ET.parse(args.input)
    root = tree.getroot()
    ns = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    if ns:
        ET.register_namespace("", ns)  # keep the original namespace when saving
    q = f"{{{ns}}}" if ns else ""

    items = {}
    for n, unit in enumerate(root.iter(f"{q}trans-unit")):
        src_el = unit.find(f"{q}source")
        if src_el is None:
            continue
        src = "".join(src_el.itertext())
        # nothing to translate if the string is only numbers / placeholders
        if not re.search(r"[A-Za-z]", PROTECT.sub("", src)):
            continue
        protected, found = protect(src)
        items[str(n)] = (unit, src, protected, found)

    if args.limit:
        items = dict(list(items.items())[: args.limit])
    print(f"Found {len(items)} translatable strings.")

    report, keys = [], list(items)
    for i in range(0, len(keys), args.batch_size):
        chunk = keys[i : i + args.batch_size]
        result = translate_batch({k: items[k][2] for k in chunk}, args.lang_name, glossary, args.dry_run)
        for k in chunk:
            unit, src, _, found = items[k]
            tgt = restore(result.get(k, ""), found)
            issues = validate(src, tgt, args.lang)
            tgt_el = unit.find(f"{q}target")
            if tgt_el is None:
                tgt_el = ET.SubElement(unit, f"{q}target")
            # Only keep safe translations; failed ones fall back to English for human review
            tgt_el.text = tgt if not issues else src
            report.append([k, src, tgt, "; ".join(issues) or "OK"])
        print(f"Translated {min(i + args.batch_size, len(keys))}/{len(keys)}")
        if not args.dry_run:
            time.sleep(args.delay)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out, encoding="utf-8", xml_declaration=True)

    rep = Path(args.report)
    rep.parent.mkdir(parents=True, exist_ok=True)
    with open(rep, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel shows Arabic
        csv.writer(f).writerows([["id", "source", "translation", "status"]] + report)

    bad = sum(1 for r in report if r[3] != "OK")
    print(f"\nDone. Translated XLIFF: {out}")
    print(f"Review report:          {rep}")
    print(f"{bad} of {len(report)} strings need human review.")


def main():
    p = argparse.ArgumentParser(description="Translate an Oracle APEX XLIFF file with an LLM, safely.")
    p.add_argument("input", nargs="?", help="XLIFF exported from APEX")
    p.add_argument("-o", "--output", default="output/translated.xlf")
    p.add_argument("--lang", default="ar", help="target language code (ar, fr, de ...)")
    p.add_argument("--lang-name", default="Arabic", help="target language name used in the prompt")
    p.add_argument("--glossary", default=str(BASE_DIR / "glossary.json"))
    p.add_argument("--report", default="output/report.csv")
    p.add_argument("--batch-size", type=int, default=30)
    p.add_argument("--delay", type=float, default=4.0, help="seconds between API calls")
    p.add_argument("--limit", type=int, default=0, help="only translate the first N strings (testing)")
    p.add_argument("--dry-run", action="store_true", help="no API call; fake translation to test the pipeline")
    p.add_argument("--list-models", action="store_true", help="show Gemini models available to your key")
    args = p.parse_args()
    if args.list_models:
        return list_models()
    if not args.input:
        p.error("input file is required")
    run(args)


if __name__ == "__main__":
    main()