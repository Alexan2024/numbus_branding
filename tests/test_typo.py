"""Типограф: правила и совпадение с копией в редакторе (webapp.html) символ в символ."""
import os
import re
import json
import random
import shutil
import subprocess

import pytest

from typo import typograf

NB = "\u00a0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("src,want", [
    ("Кофе в центре", f"Кофе в{NB}центре"),
    ('"Мы вернёмся" - директор', f"«Мы{NB}вернёмся»{NB}— директор"),
    ("Цена 350 ₽", f"Цена 350{NB}₽"),
    ("Итого 12 500 000 руб.", f"Итого 12{NB}500{NB}000{NB}руб."),
    ("В 2025 году", f"В{NB}2025{NB}году"),                      # год не дробится на разряды
    ("С 10-20 октября", f"С{NB}10–20 октября"),
    ("Телефон 555-35-35", "Телефон 555-35-35"),              # номер не превращается в диапазон
    ("А. С. Пушкин", f"А.{NB}С.{NB}Пушкин"),
    ("Неужели ли он", f"Неужели{NB}ли он"),                    # частица — к предыдущему слову
    ("Зал № 5", f"Зал №{NB}5"),
    ("Ждём...", "Ждём…"),
    ("- Привет", f"—{NB}Привет"),
    ('Книга "Сказка о "золотой" рыбке"', f"Книга «Сказка о{NB}„золотой“ рыбке»"),
    ('He said "fine"', "He said “fine”"),
    ("it's", "it’s"),
])
def test_rules(src, want):
    assert typograf(src) == want


def test_idempotent_and_lines():
    s = 'Первая строка\nВторая - с тире "и кавычками"'
    once = typograf(s)
    assert typograf(once) == once
    assert once.count("\n") == 1


def _js_source():
    html = open(os.path.join(ROOT, "webapp.html"), encoding="utf-8").read()
    m = re.search(r"/\* <typograf>.*?\*/(.*?)/\* </typograf> \*/", html, re.S)
    assert m, "в webapp.html нет блока типографа"
    return m.group(1)


@pytest.mark.skipif(not shutil.which("node"), reason="нужен node")
def test_editor_copy_matches(tmp_path):
    cases = ["В Москве открылась выставка о модернизме 1960-х", "Кофе подорожал до 350 ₽",
             '"Мы вернёмся к старой программе" - директор театра', "- Привет, как дела?",
             "С 10-20 октября, 1941 - 1945 и телефон 555-35-35", "А. С. Пушкин родился в 1799 году",
             "Цена 12 500 000 руб. и 25 %...", "He said \"it's fine\" - really", "Зал № 5 и §3",
             "Москва — Санкт-Петербург", "температура -5 °C", "100 000 ₽ за 2 часа", "Т. е. всё и т. д.",
             "Emoji 🎉 и \"кавычки\"", "...и многоточие в начале", "Первая строка\nВторая - с тире", ""]
    words = ["в", "на", "и", "не", "для", "кто", "же", "ли", "500", "000", "₽", "—", "-", '"', "«", "»",
             "Москва", "дом", "a", "the", "2025", "10-20", "№", "5", "%", ".", ",", "...", "\n"]
    rng = random.Random(7)
    cases += [" ".join(rng.choice(words) for _ in range(rng.randint(1, 10))) for _ in range(500)]
    js = tmp_path / "run.js"
    js.write_text(_js_source() + "\nconst S = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
                  "process.stdout.write(JSON.stringify(S.map(typograf)));", encoding="utf-8")
    out = subprocess.run(["node", str(js)], input=json.dumps(cases), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    bad = [(s, typograf(s), j) for s, j in zip(cases, got) if typograf(s) != j]
    assert not bad, bad[:3]
