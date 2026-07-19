"""
Generate the per-language page shells (e.g. web/ko/index.html) from the canonical English
web/index.html. The tab/view markup is identical across languages — only a handful of things
differ — so the localized pages are *derived*, never hand-edited:

  - <html lang="…">                     the page language
  - the stylesheet + script srcs        prefixed "../" (the page lives one directory deeper)
  - ui.en.js  -> ui.<suffix>.js          the translated UI-string map
  - data.js   -> data.<suffix>.js        the translated data file
  - the .lang-switch anchor              points back to the English page

To add a language, add an entry to LANGS (dir, html lang, file suffix, switch-back anchor) and
run `python gen_pages.py`. Keep the i18n string maps (ui.<suffix>.js) and the data files in sync
separately — this script only stamps out the HTML shell.
"""
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "web", "index.html")

LANGS = {
    "ko": {
        "dir": "ko",                 # web/<dir>/index.html
        "html_lang": "ko",           # <html lang="…">
        "suffix": "kor",             # ui.<suffix>.js / data.<suffix>.js
        # the language switcher on the localized page links back to English (pretty dir URL; the
        # .dir-index marker lets i18n.js re-append index.html under the file:// protocol)
        "switch": '<a class="lang-switch dir-index" href="../" hreflang="en" lang="en">English</a>',
    },
}


def generate(cfg):
    html = open(SRC, encoding="utf-8").read()
    # 1. page language
    html = html.replace('<html lang="en">', f'<html lang="{cfg["html_lang"]}">', 1)
    # 2. stylesheet — the localized page sits one directory deeper
    html = html.replace('href="style.css"', 'href="../style.css"', 1)
    # 3. language switcher → back to English
    html = re.sub(r'<a class="lang-switch[^>]*>.*?</a>', cfg["switch"], html, count=1)
    # 4. scripts: prefix "../", swapping the English UI-string + data files for the localized ones.
    #    The regex matches only bare-filename srcs (no "/"), i.e. exactly the page's own scripts.
    rename = {"ui.en.js": f"ui.{cfg['suffix']}.js", "data.js": f"data.{cfg['suffix']}.js",
              "items.js": f"items.{cfg['suffix']}.js"}
    html = re.sub(r'<script src="([^"/]+)"></script>',
                  lambda m: f'<script src="../{rename.get(m.group(1), m.group(1))}"></script>', html)
    out = os.path.join(ROOT, "web", cfg["dir"], "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return out


def main():
    for code, cfg in LANGS.items():
        path = generate(cfg)
        print(f"generated {os.path.relpath(path, ROOT)} ({code})")


if __name__ == "__main__":
    main()
