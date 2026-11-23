# APEX XLIFF Auto-Translator

Translate an Oracle APEX app (English -> Arabic and more) with an LLM while protecting
`&ITEM.` substitutions, `#SUBSTITUTIONS#`, `%0`, HTML tags and entities.

## Quick start
1. `python -m venv .venv` then activate it
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env`, paste your free Gemini API key
4. Test without API: `python translate_xliff.py sample/sample_en.xlf --dry-run`
5. Real run: `python translate_xliff.py input/your_export.xlf --lang ar --lang-name Arabic`
6. Check `output/report.csv`, upload `output/translated.xlf` into APEX
7. RTL check on CSS or exported app SQL: `python rtl_check.py file.css`

Note: `sample/sample_en.xlf` is a hand-written test file, not a real APEX export.
Always compare with a real export from your own workspace.
