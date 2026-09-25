"""NUMBUS Branding — тексты интерфейса (RU / EN).

Разметка — HTML (parse_mode="HTML"). Пользовательские данные (название
бренда, хештеги) экранируются в bot.py перед подстановкой.
"""

T = {
    # ---------- Общие кнопки ----------
    "b_back":     {"ru": "⬅️ Назад", "en": "⬅️ Back"},
    "b_menu":     {"ru": "⬅️ В меню", "en": "⬅️ Menu"},
    "b_next":     {"ru": "Далее ➡️", "en": "Next ➡️"},
    "b_save":     {"ru": "✅ Готово", "en": "✅ Done"},
    "b_skip":     {"ru": "Пропустить", "en": "Skip"},
    "b_done_ph":  {"ru": "✅ Готово — дальше", "en": "✅ Done — continue"},

    # ---------- Старт и доступ ----------
    "welcome_new": {
        "ru": "👋 Это <b>NUMBUS Branding</b> — фирменный стиль для ваших публикаций за секунды.\n\n"
              "Присылаете фото — получаете готовые посты с вашим логотипом и хештегом, "
              "обложки и сторис для Instagram и Telegram.\n\n"
              "Сейчас доступ по приглашениям. Пришлите инвайт-код:",
        "en": "👋 This is <b>NUMBUS Branding</b> — your brand identity on every post, in seconds.\n\n"
              "Send photos — get ready-to-publish posts with your logo and hashtag, "
              "plus covers and stories for Instagram and Telegram.\n\n"
              "Access is invite-only right now. Send your invite code:",
    },
    "ask_code":   {"ru": "Пришлите инвайт-код (формат <code>NB-XXXX-XXXX</code>):",
                   "en": "Send your invite code (format <code>NB-XXXX-XXXX</code>):"},
    "code_bad":   {"ru": "Код не подошёл или уже использован. Проверьте и пришлите ещё раз.",
                   "en": "This code is invalid or already used. Check it and try again."},
    "code_ok":    {"ru": "✅ Код активирован: тариф <b>{plan}</b> до {until}.\n\nСоберём бренд-кит — это пара минут.",
                   "en": "✅ Code activated: <b>{plan}</b> plan until {until}.\n\nLet's build your brand kit — takes a couple of minutes."},
    "join_ok":    {"ru": "✅ Вы в команде <b>{brand}</b>. Можно делать посты.",
                   "en": "✅ You've joined <b>{brand}</b>. You can start making posts."},
    "join_bad":   {"ru": "Ссылка-приглашение недействительна. Попросите владельца бренда прислать новую.",
                   "en": "This invite link is no longer valid. Ask the brand owner for a new one."},
    "join_full":  {"ru": "В команде <b>{brand}</b> уже максимум участников для текущего тарифа.",
                   "en": "<b>{brand}</b> has reached the member limit for its plan."},
    "join_notify": {"ru": "👥 {who} присоединился к команде <b>{brand}</b>.",
                    "en": "👥 {who} joined the <b>{brand}</b> team."},

    # ---------- Главное меню ----------
    "menu_head":  {"ru": "<b>NUMBUS Branding</b> · {brand}", "en": "<b>NUMBUS Branding</b> · {brand}"},
    "menu_plan":  {"ru": "Тариф: {plan} · до {until}\nФото в этом месяце: {used} из {limit}",
                   "en": "Plan: {plan} · until {until}\nPhotos this month: {used} of {limit}"},
    "menu_expired": {"ru": "⚠️ Срок тарифа истёк {until}. Напишите {support}, чтобы продлить.",
                     "en": "⚠️ Your plan expired on {until}. Contact {support} to renew."},
    "menu_no_kit": {"ru": "Бренд-кит ещё не собран — начните с него.",
                    "en": "Your brand kit isn't set up yet — start there."},
    "b_new":      {"ru": "✨ Новый пост", "en": "✨ New post"},
    "b_kit":      {"ru": "🎨 Бренд-кит", "en": "🎨 Brand kit"},
    "b_team":     {"ru": "👥 Команда", "en": "👥 Team"},
    "b_switch":   {"ru": "🔁 Сменить бренд", "en": "🔁 Switch brand"},
    "b_code":     {"ru": "➕ Ввести код", "en": "➕ Enter code"},
    "b_lang":     {"ru": "🌐 English", "en": "🌐 Русский"},
    "switch_head": {"ru": "Выберите бренд:", "en": "Choose a brand:"},

    # ---------- Мастер бренд-кита ----------
    "step":       {"ru": "<i>Шаг {n} из 6</i>\n", "en": "<i>Step {n} of 6</i>\n"},
    "ask_name":   {"ru": "<b>Название</b>\nКак называется бренд или канал?",
                   "en": "<b>Name</b>\nWhat's the name of your brand or channel?"},
    "name_bad":   {"ru": "Название должно быть от 1 до 40 символов.",
                   "en": "The name must be 1 to 40 characters."},
    "ask_logo":   {"ru": "<b>Логотип</b>\nПришлите логотип <b>файлом</b> (скрепка → Файл).\n\n"
                         "Лучше всего PNG без фона. Если фон есть — уберу его автоматически. "
                         "Цвет не важен: по умолчанию бот сам подбирает оттенок под каждое фото.",
                   "en": "<b>Logo</b>\nSend your logo <b>as a file</b> (paperclip → File).\n\n"
                         "A transparent PNG works best. If it has a background, I'll remove it automatically. "
                         "Colour doesn't matter: by default the bot adapts the tone to every photo."},
    "logo_bad":   {"ru": "Не смог открыть логотип. Пришлите PNG или JPG файлом (SVG пока не поддерживается).",
                   "en": "Couldn't open this logo. Send a PNG or JPG as a file (SVG isn't supported yet)."},
    "logo_empty": {"ru": "На картинке не нашёл логотип — он сливается с фоном. Пришлите версию с прозрачным фоном.",
                   "en": "Couldn't find a logo in this image — it blends into the background. Send a transparent version."},
    "logo_bg":    {"ru": "Фон убрал автоматически — проверьте на превью.",
                   "en": "Background removed automatically — check the preview."},
    "logo_photo": {"ru": "⚠️ Пришло как фото — Telegram сжал его и убрал прозрачность. Для лучшего качества пришлите файлом.",
                   "en": "⚠️ Sent as a photo — Telegram compressed it and dropped transparency. Send it as a file for best quality."},
    "ask_sample": {"ru": "<b>Фото для превью</b>\nПришлите типичное фото из вашей ленты — на нём я буду показывать, "
                         "как выглядит оформление. Можно пропустить.",
                   "en": "<b>Preview photo</b>\nSend a typical photo from your feed — I'll use it to preview your design. "
                         "You can skip this."},
    "layout_cap": {"ru": "<b>Расположение и цвет</b>\nЛоготип — в выбранном углу, хештег — в противоположном.\n\n"
                         "Цвет: <b>авто</b> подбирает оттенок под фон, "
                         "<b>оригинал</b> — цвета вашего файла.",
                   "en": "<b>Placement & colour</b>\nThe logo sits in the chosen corner, the hashtag in the opposite one.\n\n"
                         "Colour: <b>auto</b> adapts the tone to the background, "
                         "<b>original</b> keeps your file's colours."},
    "c_adaptive": {"ru": "🎯 Авто", "en": "🎯 Auto"},
    "c_white":    {"ru": "⚪ Белый", "en": "⚪ White"},
    "c_black":    {"ru": "⚫ Чёрный", "en": "⚫ Black"},
    "c_original": {"ru": "🎨 Оригинал", "en": "🎨 Original"},
    "font_cap":   {"ru": "<b>Шрифт</b>\nИм пишутся хештеги и заголовки обложек. Все шрифты поддерживают кириллицу.",
                   "en": "<b>Font</b>\nUsed for hashtags and cover headlines. All fonts support Cyrillic."},
    "b_font_up":  {"ru": "⬆️ Свой шрифт", "en": "⬆️ Upload font"},
    "font_custom": {"ru": "свой", "en": "custom"},
    "ask_font":   {"ru": "Пришлите файл шрифта <b>.ttf</b> или <b>.otf</b>.\n"
                         "<i>Используйте шрифты, на которые у вас есть лицензия.</i>",
                   "en": "Send a <b>.ttf</b> or <b>.otf</b> font file.\n"
                         "<i>Only use fonts you're licensed to use.</i>"},
    "font_bad":   {"ru": "Не смог открыть шрифт. Пришлите .ttf или .otf файлом.",
                   "en": "Couldn't open this font. Send a .ttf or .otf file."},
    "preview_title": {"ru": "Заголовок\nобложки", "en": "Cover\nheadline"},
    "ask_tags":   {"ru": "<b>Хештеги</b>\nПришлите рубрики вашего канала одним сообщением — через пробел или с новой строки. "
                         "Например: <code>#музыка #интервью #афиша</code>\n\nПотом их можно поменять.",
                   "en": "<b>Hashtags</b>\nSend your channel's sections in one message, separated by spaces or new lines. "
                         "E.g. <code>#music #interview #events</code>\n\nYou can change them later."},
    "tags_bad":   {"ru": "Не нашёл ни одного хештега. Пришлите ещё раз или пропустите.",
                   "en": "No hashtags found. Try again or skip."},
    "wiz_done":   {"ru": "🎉 <b>Бренд-кит готов.</b>\nВот так будут выглядеть ваши посты. Жмите «Новый пост» — и присылайте фото.",
                   "en": "🎉 <b>Brand kit ready.</b>\nThis is how your posts will look. Tap “New post” and send photos."},

    # ---------- Меню бренд-кита ----------
    "kit_head":   {"ru": "<b>Бренд-кит · {brand}</b>\n\nШрифт: {font}\nЛоготип: {pos}, размер {size}, цвет — {color}\n"
                         "Лого для обложек: {cover}\nХештеги: {tags}",
                   "en": "<b>Brand kit · {brand}</b>\n\nFont: {font}\nLogo: {pos}, size {size}, colour — {color}\n"
                         "Cover logo: {cover}\nHashtags: {tags}"},
    "kit_owner_only": {"ru": "Бренд-кит может менять только владелец бренда.",
                       "en": "Only the brand owner can edit the brand kit."},
    "pos_bl": {"ru": "снизу слева", "en": "bottom left"},
    "pos_br": {"ru": "снизу справа", "en": "bottom right"},
    "pos_tl": {"ru": "сверху слева", "en": "top left"},
    "pos_tr": {"ru": "сверху справа", "en": "top right"},
    "col_adaptive": {"ru": "авто", "en": "auto"},
    "col_white":    {"ru": "белый", "en": "white"},
    "col_black":    {"ru": "чёрный", "en": "black"},
    "col_original": {"ru": "оригинал", "en": "original"},
    "cover_same":   {"ru": "как основной", "en": "same as main"},
    "cover_own":    {"ru": "свой", "en": "custom"},
    "tags_none":    {"ru": "нет", "en": "none"},
    "k_name":   {"ru": "✏️ Название", "en": "✏️ Name"},
    "k_logo":   {"ru": "🖼 Логотип", "en": "🖼 Logo"},
    "k_cover":  {"ru": "🎬 Лого для обложек", "en": "🎬 Cover logo"},
    "k_sample": {"ru": "📷 Фото для превью", "en": "📷 Preview photo"},
    "k_layout": {"ru": "📐 Расположение", "en": "📐 Placement"},
    "k_font":   {"ru": "🔤 Шрифт", "en": "🔤 Font"},
    "k_tags":   {"ru": "#️⃣ Хештеги", "en": "#️⃣ Hashtags"},
    "ask_cover": {"ru": "<b>Логотип для обложек</b>\nНа обложках и в сторис логотип стоит по центру — сюда хорошо подходит "
                        "горизонтальная версия (вордмарк). Пришлите файлом или вернитесь к основному.",
                  "en": "<b>Cover logo</b>\nCovers and stories place the logo centred — a horizontal version (wordmark) "
                        "works well here. Send it as a file, or go back to the main logo."},
    "b_cover_reset": {"ru": "↩️ Использовать основной", "en": "↩️ Use main logo"},

    # ---------- Команда ----------
    "team_head": {"ru": "<b>Команда · {brand}</b>\nУчастников: {n} из {limit}\n\n"
                        "Отправьте эту ссылку коллегам — они смогут делать посты в вашем стиле:\n{link}",
                  "en": "<b>Team · {brand}</b>\nMembers: {n} of {limit}\n\n"
                        "Share this link with colleagues — they'll be able to make posts in your style:\n{link}"},
    "b_team_new": {"ru": "🔄 Новая ссылка (старая перестанет работать)",
                   "en": "🔄 New link (old one stops working)"},
    "team_owner_only": {"ru": "Приглашать в команду может только владелец бренда.",
                        "en": "Only the brand owner can invite team members."},

    # ---------- Создание поста ----------
    "no_access": {"ru": "Тариф неактивен — создание постов недоступно. Напишите {support}.",
                  "en": "Your plan is inactive — posting is unavailable. Contact {support}."},
    "no_logo":   {"ru": "Сначала загрузите логотип в бренд-ките.",
                  "en": "Upload a logo in your brand kit first."},
    "limit_hit": {"ru": "В этом месяце осталось {left} фото по тарифу, а в пакете {n}. Уберите лишние или напишите {support}.",
                  "en": "Your plan has {left} photos left this month, but the batch has {n}. Remove some or contact {support}."},
    "tpl_head":  {"ru": "Что делаем?", "en": "What are we making?"},
    "tpl_branding": {"ru": "🏷 Брендинг — лого + хештег", "en": "🏷 Branding — logo + hashtag"},
    "tpl_cover":    {"ru": "🖼 Обложка + сторис", "en": "🖼 Cover + stories"},
    "ask_photos": {"ru": "📎 Присылайте фото <b>файлами</b> (скрепка → Файл), чтобы не терять качество. "
                         "Можно несколько сразу. Потом нажмите «Готово».",
                   "en": "📎 Send photos <b>as files</b> (paperclip → File) to keep full quality. "
                         "Several at once is fine. Then tap “Done”."},
    "photos_n":  {"ru": "✅ Фото: {n}", "en": "✅ Photos: {n}"},
    "photos_max": {"ru": "Максимум {n} фото за раз — остальные не взял.",
                   "en": "Max {n} photos per batch — I skipped the rest."},
    "photos_none": {"ru": "Сначала пришлите хотя бы одно фото.",
                    "en": "Send at least one photo first."},
    "file_big":  {"ru": "Файл больше 20 МБ — это лимит Telegram для ботов. Пришлите файл поменьше.",
                  "en": "The file is over 20 MB — Telegram's limit for bots. Send a smaller file."},
    "photo_bad": {"ru": "Не смог открыть этот файл как изображение.",
                  "en": "Couldn't open this file as an image."},
    "ask_title": {"ru": "✍️ Пришлите <b>заголовок</b> обложки. Переносы строк можно ставить самостоятельно, "
                        "слишком длинные строки перенесу сам.",
                  "en": "✍️ Send the cover <b>headline</b>. Add line breaks yourself if you like — "
                        "I'll wrap long lines automatically."},
    "title_bad": {"ru": "Заголовок пустой — пришлите текст.", "en": "The headline is empty — send some text."},
    "ask_format": {"ru": "📐 Формат ({n} фото):", "en": "📐 Format ({n} photos):"},
    "ask_format_cover": {"ru": "📐 Формат ленты — сторис для IG и TG добавлю автоматически:",
                         "en": "📐 Feed format — IG and TG stories are added automatically:"},
    "fmt_orig":  {"ru": "Как в оригинале", "en": "Original ratio"},
    "ask_tag":   {"ru": "Хештег:", "en": "Hashtag:"},
    "tag_none":  {"ru": "— Без хештега —", "en": "— No hashtag —"},
    "tag_custom": {"ru": "✏️ Свой хештег", "en": "✏️ Custom hashtag"},
    "ask_custom_tag": {"ru": "Пришлите хештег одним словом — решётку добавлю сам.",
                       "en": "Send a one-word hashtag — I'll add the # myself."},
    "custom_tag_bad": {"ru": "Пустой хештег — пришлите ещё раз.", "en": "Empty hashtag — try again."},
    "working":   {"ru": "⚙️ Обрабатываю {n} фото…", "en": "⚙️ Processing {n} photos…"},
    "working_cover": {"ru": "⚙️ Рендерю обложки и сторис…", "en": "⚙️ Rendering covers and stories…"},
    "photo_err": {"ru": "❌ Фото {i}: не получилось обработать.", "en": "❌ Photo {i}: processing failed."},
    "done":      {"ru": "✅ Готово: {ok} из {n}.", "en": "✅ Done: {ok} of {n}."},
    "dark_cap":  {"ru": "🎚 <b>Затемнение</b> — подберите под кадр. Превью на первом фото.\n\nУровень: {meter}",
                  "en": "🎚 <b>Darkening</b> — tune it to the shot. Preview uses the first photo.\n\nLevel: {meter}"},
    "b_lighter": {"ru": "☀️ Светлее", "en": "☀️ Lighter"},
    "b_darker":  {"ru": "🌑 Темнее", "en": "🌑 Darker"},
    "b_render":  {"ru": "✅ Сгенерировать", "en": "✅ Generate"},
    "edge":      {"ru": "Дальше некуда 🙂", "en": "That's the limit 🙂"},
    "stale":     {"ru": "Сессия устарела — откройте меню: /start", "en": "Session expired — open the menu: /start"},
    "cancelled": {"ru": "Отменено.", "en": "Cancelled."},
}


def t(lang: str, key: str, **kw) -> str:
    entry = T.get(key)
    if entry is None:
        return key
    s = entry.get(lang) or entry.get("ru") or key
    return s.format(**kw) if kw else s
