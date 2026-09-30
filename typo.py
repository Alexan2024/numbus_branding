"""NUMBUS Branding — типограф для заголовков и текста поста.

Правила русской вёрстки, которые видно на картинке:
  • короткие слова (в, на, и, не, для…) не висят в конце строки: после них неразрывный пробел;
  • частицы (ли, же, бы) не уходят в начало строки: перед ними неразрывный пробел;
  • кавычки «ёлочки», внутри — „лапки“; в английском тексте — “ ” и ‘ ’;
  • длинное тире с неразрывным пробелом перед ним; диапазон чисел — короткое тире «10–20»;
  • число не отрывается от единицы и от разрядов: «350 ₽», «4 000», «№ 5»;
  • три точки — многоточие; лишние пробелы убираются.

Та же функция есть в webapp.html (typograf) — результат совпадает символ в символ,
иначе превью в редакторе и итог бота разойдутся (это проверяет tests/test_typo.py).
Функция идемпотентна: повторный вызов ничего не меняет.
"""
import re

NBSP = "\u00a0"

# Слова, которые привязываются к следующему слову (кроме них — любое слово из 1–2 букв)
SHORT = frozenset(
    "для без над под при про изо обо ото подо надо вне или что как через перед между после около "
    "a an the of to in on at by or and".split()
)
# Частицы, которые привязываются к предыдущему слову
PARTICLES = frozenset("ли ль же ж бы б".split())
# Единицы и слова, которые не отрываются от числа
UNITS = frozenset(
    "₽ руб руб. р. рубль рубля рублей $ € ₸ ₴ £ % ‰ ° °C °С км м см мм кг г мг т л мл ч мин сек шт шт. "
    "тыс тыс. млн млрд трлн чел чел. лет год года году гг. г. км/ч".split()
)
_CYR = re.compile("[А-Яа-яЁё]")
_WORD = re.compile(r"^[A-Za-zА-Яа-яЁё]+$")
_LEAD = "«„“‘\"'([{"
_TRAIL = "»“”’\"'.,:;!?…)]}"
_STOP = ".,:;!?…"
_NUM = re.compile(r"^[0-9]+(?:[.,][0-9]+)?$")
_D13 = re.compile(r"^[0-9]{1,3}$")
_D3 = re.compile(r"^[0-9]{3}(?:[.,][0-9]+)?$")
_INITIAL = re.compile(r"^[A-ZА-ЯЁ]\.$")
_RANGE = re.compile(r"[0-9]+(?:[.,][0-9]+)?(?: ?[-–] ?[0-9]+(?:[.,][0-9]+)?)+")
_OPENERS = " \u00a0\t([{«„“‘—–-/"


def _quotes(s, ru):
    """Прямые двойные кавычки → «» (внутри „“) или “” (внутри ‘’)."""
    q = ("«", "»", "„", "“") if ru else ("“", "”", "‘", "’")
    opens = "«„" if ru else "“‘"
    closes = "»“" if ru else "”"
    out, depth = [], 0
    for i, ch in enumerate(s):
        if ch == '"':
            prev = s[i - 1] if i else " "
            if prev in _OPENERS:
                out.append(q[0] if depth == 0 else q[2])
                depth += 1
            else:
                depth = max(0, depth - 1)
                out.append(q[1] if depth == 0 else q[3])
            continue
        if ch in opens:
            depth += 1
        elif ch in closes and depth:
            depth -= 1
        out.append(ch)
    return "".join(out)


def _range(m):
    parts = re.split(r" ?[-–] ?", m.group(0))
    return "–".join(parts) if len(parts) == 2 else m.group(0)


def _strip(s, lead, trail):
    i, j = 0, len(s)
    while i < j and s[i] in lead:
        i += 1
    while j > i and s[j - 1] in trail:
        j -= 1
    return s[i:j]


def _glue(tok, nxt, ru):
    b, nb = _strip(tok, _LEAD, _TRAIL), _strip(nxt, _LEAD, _TRAIL)
    bl, nbl = b.lower(), nb.lower()
    if tok and tok[-1] not in _STOP and _WORD.match(b) and bl not in PARTICLES \
            and ((len(b) <= 2 and _CYR.search(b)) or bl in SHORT):
        return True
    if ru and nbl in PARTICLES and _WORD.match(nb):
        return True
    t, n = _strip(tok, "", _TRAIL), _strip(nxt, "", ".,:;!?)…")
    if _NUM.match(t) and (n in UNITS or n.lower() in UNITS):
        return True
    if _D13.match(tok) and _D3.match(_strip(nxt, "", _TRAIL)):
        return True
    return bool(_INITIAL.match(tok))


def _line(s, ru):
    s = re.sub(r"[ \t]+", " ", s).strip(" ")
    if not s:
        return s
    s = s.replace("...", "…")
    s = _quotes(s, ru)
    if not ru:
        s = re.sub(r"([A-Za-z])'(?=[A-Za-z])", "\\1’", s)
    s = _RANGE.sub(_range, s)                                          # 10-20 → 10–20
    s = re.sub(r"(\S) (?:--|-|–|—) (?=\S)", "\\1" + NBSP + "— ", s)    # слово - слово
    s = re.sub(r"(\S)\u00a0(?:--|-|–) ", "\\1" + NBSP + "— ", s)
    s = re.sub(r"^(?:--|-|–|—) ", "—" + NBSP, s)                       # прямая речь
    s = re.sub(r"№ ?(?=[0-9])", "№" + NBSP, s)
    s = re.sub(r"§ ?(?=[0-9])", "§" + NBSP, s)
    toks = s.split(" ")
    out = [toks[0]]
    for i in range(1, len(toks)):
        out.append(NBSP if _glue(toks[i - 1], toks[i], ru) else " ")
        out.append(toks[i])
    return "".join(out)


def typograf(s):
    """Строка → строка с правилами вёрстки. Переносы строк сохраняются."""
    if not s:
        return ""
    s = str(s).replace("\r\n", "\n")
    ru = bool(_CYR.search(s))
    return "\n".join(_line(x, ru) for x in s.split("\n"))
