"""NUMBUS Branding — тексты интерфейса (RU / EN).

Тон: спокойный и короткий. В сообщениях — без эмодзи; в кнопках — только
функциональные значки одного набора: ‹ назад, ✓ выбрано, + добавить, ✕ отмена.
Разметка — HTML (parse_mode="HTML"). Пользовательские данные (название бренда,
хештеги) экранируются в bot.py перед подстановкой.
"""

T = {
    # ---------- Общие кнопки ----------
    "b_back":     {"ru": "‹ Назад", "en": "‹ Back"},
    "b_menu":     {"ru": "‹ Меню", "en": "‹ Menu"},
    "b_skip":     {"ru": "Пропустить", "en": "Skip"},
    "b_cancel":   {"ru": "✕ Отмена", "en": "✕ Cancel"},

    # ---------- Тарифы ----------
    "plan_pilot": {"ru": "Пилот", "en": "Pilot"},
    "plan_solo":  {"ru": "Solo", "en": "Solo"},
    "plan_media": {"ru": "Media", "en": "Media"},
    "plan_studio": {"ru": "Studio", "en": "Studio"},

    # ---------- Старт и доступ ----------
    "welcome_new": {
        "ru": "<b>NUMBUS</b> оформляет фото в стиле вашего бренда: логотип, шрифт заголовка, рубрика, "
              "затемнение. Присылаете фото с подписью — получаете готовые посты для ленты и сторис.\n\n"
              "Доступ пока по приглашениям. Пришлите код или запросите доступ.",
        "en": "<b>NUMBUS</b> styles your photos in your brand's look: logo, headline font, section tag, "
              "shading. Send photos with a caption — get ready posts for the feed and stories.\n\n"
              "Access is invite-only for now. Send your code or request access.",
    },
    "b_request":  {"ru": "Запросить доступ", "en": "Request access"},
    "b_have_code": {"ru": "У меня есть код", "en": "I have a code"},
    "ask_code":   {"ru": "Пришлите код доступа — он выглядит так: <code>NB-XXXX-XXXX</code>.",
                   "en": "Send your access code — it looks like <code>NB-XXXX-XXXX</code>."},
    "code_bad":   {"ru": "Этот код не подошёл или уже использован. Проверьте его и пришлите ещё раз.",
                   "en": "This code is invalid or already used. Check it and send it again."},
    "code_format": {"ru": "Чтобы начать, нужен код доступа. Он выглядит так: <code>NB-XXXX-XXXX</code>. "
                          "Кода нет — нажмите «Запросить доступ».",
                    "en": "To start, you need an access code. It looks like <code>NB-XXXX-XXXX</code>. "
                          "No code yet? Tap “Request access”."},
    "code_choose": {"ru": "Код действует: {plan} на {days} дн. Продлить бренд или создать новый?",
                    "en": "The code is valid: {plan} for {days} days. Renew a brand or create a new one?"},
    "b_code_extend": {"ru": "Продлить «{brand}»", "en": "Renew “{brand}”"},
    "b_code_new": {"ru": "+ Новый бренд", "en": "+ New brand"},
    "code_ok":    {"ru": "Код принят: {plan} до {until}.\n\nНастроим стиль — это пара минут.",
                   "en": "Code accepted: {plan} until {until}.\n\nLet's set up your style — it takes a couple of minutes."},
    "code_extended": {"ru": "Код принят: «{brand}» продлён до {until}.",
                      "en": "Code accepted: “{brand}” renewed until {until}."},
    "req_ask":    {"ru": "Пришлите ссылку на канал или аккаунт, для которого нужен NUMBUS. Можно добавить пару слов о себе.",
                   "en": "Send a link to the channel or account you need NUMBUS for. You can add a few words about yourself."},
    "req_sent":   {"ru": "Заявка отправлена. Ответ придёт сюда, обычно в течение дня.",
                   "en": "Request sent. The answer will come here, usually within a day."},
    "req_dup":    {"ru": "Ваша заявка уже у нас. Ответ придёт сюда.",
                   "en": "We already have your request. The answer will come here."},
    "req_has":    {"ru": "Доступ у вас уже есть — откройте меню: /start",
                   "en": "You already have access — open the menu: /start"},
    "req_ok_user": {"ru": "Доступ открыт: Пилот до {until}. Настроим стиль — это пара минут.",
                    "en": "Access granted: Pilot until {until}. Let's set up your style — it takes a couple of minutes."},
    "req_no_user": {"ru": "Спасибо за интерес к NUMBUS. Сейчас открыть доступ не получится. Если есть вопросы, напишите {support}.",
                    "en": "Thank you for your interest in NUMBUS. We can't open access right now. Questions: {support}."},
    "b_wiz_start": {"ru": "Начать настройку", "en": "Start setup"},
    "join_ok":    {"ru": "Вы в команде «{brand}». Присылайте фото с подписью — бот сделает посты.",
                   "en": "You're on the “{brand}” team. Send photos with a caption and the bot will make posts."},
    "join_bad":   {"ru": "Ссылка-приглашение больше не действует. Попросите владельца бренда прислать новую.",
                   "en": "This invite link no longer works. Ask the brand owner for a new one."},
    "join_full":  {"ru": "В команде «{brand}» уже максимум людей для текущего тарифа. "
                         "Попросите владельца освободить место.",
                   "en": "“{brand}” has reached the member limit for its plan. Ask the owner to free up a seat."},
    "join_notify": {"ru": "Новый участник в «{brand}»: {who}.", "en": "New member in “{brand}”: {who}."},

    # ---------- Главное меню ----------
    "menu_head":  {"ru": "<b>{brand}</b>", "en": "<b>{brand}</b>"},
    "menu_plan":  {"ru": "{plan} до {until}\nФото: {used} из {limit}, счётчик обновится {reset}",
                   "en": "{plan} until {until}\nPhotos: {used} of {limit}, resets on {reset}"},
    "menu_plan_noreset": {"ru": "{plan} до {until}\nФото: {used} из {limit}",
                          "en": "{plan} until {until}\nPhotos: {used} of {limit}"},
    "menu_expired": {"ru": "Доступ закончился {until}. Чтобы продлить, напишите {support}.",
                     "en": "Access ended on {until}. To renew, contact {support}."},
    "menu_no_kit": {"ru": "Стиль ещё не настроен — начните с него.",
                    "en": "Your style isn't set up yet — start there."},
    "menu_hint":  {"ru": "Чтобы сделать пост, пришлите фото с подписью: первая строка станет заголовком, #слово — рубрикой.",
                   "en": "To make a post, send photos with a caption: the first line becomes the headline, a #word the section tag."},
    "menu_locked": {"ru": "Бренд на паузе: несколько брендов доступны на тарифе Studio. Напишите {support}.",
                    "en": "This brand is paused: multiple brands are available on the Studio plan. Contact {support}."},
    "b_editor":   {"ru": "Редактор стиля", "en": "Style editor"},
    "b_desktop":  {"ru": "На компьютере", "en": "On a computer"},
    "k_setup":    {"ru": "Настроить стиль", "en": "Set up style"},
    "b_team":     {"ru": "Команда", "en": "Team"},
    "b_switch":   {"ru": "Сменить бренд", "en": "Switch brand"},
    "b_paused":   {"ru": " (на паузе)", "en": " (paused)"},
    "brand_unnamed": {"ru": "Новый бренд", "en": "New brand"},
    "b_code":     {"ru": "Ввести код", "en": "Enter code"},
    "b_add_brand": {"ru": "+ Добавить бренд", "en": "+ Add brand"},
    "b_settings": {"ru": "Настройки", "en": "Settings"},
    "b_help":     {"ru": "Помощь", "en": "Help"},
    "switch_head": {"ru": "Выберите бренд:", "en": "Choose a brand:"},
    "brands_count": {"ru": "Брендов в подписке: {n} из {limit}", "en": "Brands in the subscription: {n} of {limit}"},
    "brand_limit": {"ru": "В подписке уже максимум брендов.", "en": "The subscription already has the maximum number of brands."},
    "open_desktop": {"ru": "Редактор на компьютере: откройте ссылку в браузере. Ссылка одноразовая и действует "
                           "{min} минут. После входа браузер помнит вас 7 дней.",
                     "en": "The editor on a computer: open the link in a browser. The link works once and for "
                           "{min} minutes. After you sign in, the browser remembers you for 7 days."},
    "b_open_desktop": {"ru": "Открыть редактор", "en": "Open the editor"},
    "editor_off": {"ru": "Редактор пока недоступен. Напишите {support}.",
                   "en": "The editor isn't available yet. Contact {support}."},
    "kit_owner_only": {"ru": "Редактор стиля доступен владельцу и дизайнеру бренда.",
                       "en": "The style editor is available to the brand owner and designer."},

    # ---------- Настройки ----------
    "settings_head": {"ru": "<b>Настройки</b>", "en": "<b>Settings</b>"},
    "b_lang":     {"ru": "Language: English", "en": "Язык: русский"},
    "b_logout_all": {"ru": "Выйти на всех компьютерах ({n})", "en": "Sign out on all computers ({n})"},
    "logout_done": {"ru": "Вход с компьютеров сброшен.", "en": "Signed out on all computers."},
    "b_privacy":  {"ru": "Политика данных", "en": "Privacy policy"},
    "b_del_brand": {"ru": "Удалить бренд «{brand}»", "en": "Delete brand “{brand}”"},
    "del_brand_confirm": {"ru": "Удалить «{brand}»? Удалятся стили, логотипы, шрифты и статистика{sub}. Вернуть их будет нельзя.",
                          "en": "Delete “{brand}”? Its styles, logos, fonts and stats will be deleted{sub}. This can't be undone."},
    "del_brand_sub": {"ru": " — и остальные бренды подписки: {names}", "en": ", as well as the other brands in the subscription: {names}"},
    "b_del_yes":  {"ru": "Да, удалить", "en": "Yes, delete"},
    "del_brand_done": {"ru": "Бренд удалён.", "en": "Brand deleted."},
    "b_del_me":   {"ru": "Удалить мои данные", "en": "Delete my data"},
    "del_me_confirm": {"ru": "Удалить все ваши данные из NUMBUS? Удалятся ваши бренды со стилями и файлами, участие в командах "
                             "и статистика. Готовые файлы в этом чате останутся.",
                       "en": "Delete all your data from NUMBUS? Your brands with their styles and files, team memberships "
                             "and stats will be deleted. Finished files in this chat will stay."},
    "del_me_done": {"ru": "Данные удалены. Чтобы начать заново, отправьте /start.",
                    "en": "Your data has been deleted. To start again, send /start."},

    # ---------- Мастер настройки ----------
    "step":       {"ru": "<i>Шаг {n} из 4</i>\n", "en": "<i>Step {n} of 4</i>\n"},
    "ask_name":   {"ru": "<b>Название</b>\nКак называется бренд или канал?",
                   "en": "<b>Name</b>\nWhat's the name of your brand or channel?"},
    "name_bad":   {"ru": "Название должно быть от 1 до 40 знаков.", "en": "The name must be 1 to 40 characters."},
    "ask_logo":   {"ru": "<b>Логотип</b>\nПришлите логотип файлом: PNG без фона, SVG или PDF. Если фон есть, он уберётся автоматически.",
                   "en": "<b>Logo</b>\nSend your logo as a file: a transparent PNG, SVG or PDF. A background is removed automatically."},
    "logo_bad":   {"ru": "Не получилось открыть логотип. Пришлите PNG, JPG, SVG или PDF файлом.",
                   "en": "Couldn't open this logo. Send a PNG, JPG, SVG or PDF as a file."},
    "logo_empty": {"ru": "На картинке не видно логотипа — он сливается с фоном. Пришлите версию с прозрачным фоном.",
                   "en": "No logo visible in this image — it blends into the background. Send a transparent version."},
    "logo_bg":    {"ru": "Фон убран автоматически.", "en": "Background removed automatically."},
    "logo_photo": {"ru": "Логотип пришёл как фото: Telegram сжал его и убрал прозрачность. Для лучшего качества "
                         "пришлите его файлом прямо сейчас — он заменит этот. Или выберите цвет ниже.",
                   "en": "The logo came as a photo: Telegram compressed it and dropped transparency. For best quality "
                         "send it as a file now — it will replace this one. Or pick a colour below."},
    "ask_color_found": {"ru": "<b>Фирменный цвет</b>\nВ логотипе нашлись эти цвета. Какой взять акцентом — для плашек и рубрик?",
                        "en": "<b>Brand colour</b>\nI found these colours in your logo. Which one should be the accent for plates and tags?"},
    "ask_color_none": {"ru": "<b>Фирменный цвет</b>\nВ логотипе нет яркого цвета. Пришлите цвет в формате <code>#E4572E</code> "
                             "или пропустите — будет спокойный светлый.",
                       "en": "<b>Brand colour</b>\nThere's no bright colour in your logo. Send a colour like <code>#E4572E</code> "
                             "or skip — a calm light one will be used."},
    "ask_color_hex": {"ru": "Пришлите цвет в формате <code>#E4572E</code>.", "en": "Send a colour like <code>#E4572E</code>."},
    "color_bad":  {"ru": "Цвет не распознан. Нужен формат <code>#RRGGBB</code>, например <code>#E4572E</code>.",
                   "en": "Colour not recognised. Use the <code>#RRGGBB</code> format, e.g. <code>#E4572E</code>."},
    "b_color_n":  {"ru": "Цвет {n}", "en": "Colour {n}"},
    "b_color_own": {"ru": "Свой цвет", "en": "My own colour"},
    "ask_photo":  {"ru": "<b>Стиль</b>\nПришлите фото из вашей ленты — можно с подписью, как для поста. На нём будут показаны три стиля.",
                   "en": "<b>Style</b>\nSend a photo from your feed — with a caption, like a post. Three styles will be shown on it."},
    "styles_wait": {"ru": "Примеряем стили…", "en": "Trying on styles…"},
    "wiz_need_name": {"ru": "Сейчас нужно название — пришлите его текстом.",
                      "en": "A name is needed now — send it as text."},
    "wiz_need_logo": {"ru": "Сейчас нужен логотип — картинкой или файлом.",
                      "en": "A logo is needed now — as a picture or a file."},
    "wiz_need_color": {"ru": "Сейчас нужен фирменный цвет: выберите его кнопкой или пришлите в формате <code>#RRGGBB</code>.",
                       "en": "A brand colour is needed now: pick it with a button or send it as <code>#RRGGBB</code>."},
    "wiz_photo_is_post": {"ru": "Посты — после настройки. Сейчас выберите фирменный цвет. Чтобы заменить логотип, "
                                "пришлите его файлом.",
                          "en": "Posts come after the setup. Pick a brand colour now. To replace the logo, send it as a file."},
    "wiz_need_photo": {"ru": "Сейчас нужно фото — на нём будут показаны три стиля.",
                       "en": "A photo is needed now — three styles will be shown on it."},
    "wiz_pick_hint": {"ru": "Выберите стиль кнопкой под примерами — или пришлите другое фото.",
                      "en": "Pick a style with a button under the examples — or send another photo."},
    "styles_pick": {"ru": "Какой стиль взять за основу? Потом его можно донастроить в редакторе.",
                    "en": "Which style should be the base? You can fine-tune it in the editor later."},
    "style_sample_title": {"ru": "Так будет выглядеть заголовок вашего поста",
                           "en": "This is how your post headline will look"},
    "wiz_done":   {"ru": "Готово: стиль «{style}» — по умолчанию. Шрифт, цвета и место логотипа можно поменять "
                         "в редакторе стиля.",
                   "en": "Done: “{style}” is your default style. Fonts, colours and logo placement can be changed "
                         "in the style editor."},

    # ---------- Пост и пульт ----------
    "q_hint":     {"ru": "Чтобы сделать пост, пришлите фото. Подпись станет заголовком.",
                   "en": "To make a post, send photos. The caption becomes the headline."},
    "q_photos":   {"ru": "Фото: {n}", "en": "Photos: {n}"},
    "tpl_story":  {"ru": " + сторис", "en": " + stories"},
    "q_no_tag":   {"ru": "без рубрики", "en": "no tag"},
    "q_rubric":   {"ru": "Рубрика", "en": "Tag"},
    "q_no_title": {"ru": "Заголовка нет — нажмите «Текст» или пришлите фото с подписью.",
                   "en": "No headline — tap “Text” or send photos with a caption."},
    "q_title_cut": {"ru": "Текст длинный: на картинке — начало, в тексте поста — полностью.",
                    "en": "Long text: the image shows the beginning, the post text has it in full."},
    "q_title_small": {"ru": "Заголовок длинный — шрифт уменьшен.", "en": "The headline is long — the font is smaller."},
    "q_frame":    {"ru": "Кадр {i} из {n}", "en": "Photo {i} of {n}"},
    "q_crop":     {"ru": "Обрезается {pct}% фото. Выберите, какую часть оставить.",
                   "en": "{pct}% of the photo is cropped. Pick which part to keep."},
    "b_q_style":  {"ru": "Стиль: {name}", "en": "Style: {name}"},
    "b_q_fmt":    {"ru": "{fmt}", "en": "{fmt}"},
    "b_q_text":   {"ru": "Текст", "en": "Text"},
    "b_q_send":   {"ru": "Получить файлы ({n})", "en": "Get files ({n})"},
    "b_q_again":  {"ru": "Файлы ещё раз ({n})", "en": "Files again ({n})"},
    "b_q_done":   {"ru": "Готово", "en": "Done"},
    "b_q_cancel": {"ru": "✕ Отмена", "en": "✕ Cancel"},
    "b_q_channel": {"ru": "Альбом для канала", "en": "Album for a channel"},
    "b_lighter":  {"ru": "Светлее", "en": "Lighter"},
    "b_darker":   {"ru": "Темнее", "en": "Darker"},
    "b_focus_t":  {"ru": "Верх", "en": "Top"},
    "b_focus_c":  {"ru": "Центр", "en": "Centre"},
    "b_zoom":     {"ru": "Масштаб {z}×", "en": "Zoom {z}×"},
    "b_sample":   {"ru": "Стиль по образцу", "en": "Style from a sample"},
    "smp_ask":    {"ru": "Пришлите картинкой или файлом свой фирменный пост — тот, чей стиль нужно повторить. "
                         "Бот разберёт макет и соберёт по нему стиль.\n\nОтмена — /cancel",
                   "en": "Send your branded post as a picture or a file — the one whose style to repeat. "
                         "The bot will read the layout and build a style from it.\n\nCancel — /cancel"},
    "smp_wait":   {"ru": "Макет разбирается… Обычно это 20–40 секунд.", "en": "Reading the layout… Usually 20–40 seconds."},
    "smp_ready":  {"ru": "Слева образец, справа стиль, собранный по нему.{notes}\n\nСохранить как новый стиль?",
                   "en": "Left: your sample. Right: the style built from it.{notes}\n\nSave it as a new style?"},
    "smp_weak":   {"ru": "\nПроверьте в редакторе: {what}.", "en": "\nCheck in the editor: {what}."},
    "smp_fonts":  {"ru": "\nШрифты из каталога: {fonts}. Свой фирменный шрифт можно загрузить в редакторе — "
                         "тогда совпадение будет точным.",
                   "en": "\nCatalogue fonts used: {fonts}. Upload your brand font in the editor for an exact match."},
    "smp_name":   {"ru": "По образцу", "en": "From sample"},
    "smp_full":   {"ru": "Стилей уже {n} — это максимум. Удалите ненужный в редакторе и сохраните снова.",
                   "en": "You already have {n} styles, the maximum. Delete one in the editor and save again."},
    "smp_saved":  {"ru": "Стиль «{name}» сохранён. Пришлите фото с подписью — он уже в списке стилей.",
                   "en": "Style “{name}” saved. Send a photo with a caption — it is in your styles now."},
    "b_smp_save": {"ru": "Сохранить стиль", "en": "Save style"},
    "b_smp_again": {"ru": "Собрать ещё раз", "en": "Build again"},
    "b_smp_other": {"ru": "Другой образец", "en": "Another sample"},
    "smp_off":    {"ru": "Стиль по образцу пока недоступен. Напишите {support}.",
                   "en": "Style from a sample isn't available yet. Contact {support}."},
    "smp_fail":   {"ru": "Не получилось разобрать макет. Пришлите другой образец: чёткий скриншот поста целиком, "
                         "без интерфейса вокруг.\n\nОтмена — /cancel",
                   "en": "Could not read the layout. Send another sample: a sharp screenshot of the whole post, "
                         "no app interface around it.\n\nCancel — /cancel"},
    "smp_unavailable": {"ru": "Сервис разбора сейчас недоступен. Попробуйте позже: меню → «Стиль по образцу». Фото без образца бот оформляет как обычно.",
                        "en": "The layout service is unavailable right now. Please try later: menu → “Style from a sample”. Photos are styled as usual meanwhile."},
    "smp_limit_brand": {"ru": "Дневной лимит разборов по образцу ({n} в сутки) исчерпан. Завтра можно снова.",
                        "en": "{n} sample reads today — that's the daily limit. You can try again tomorrow."},
    "smp_limit_day": {"ru": "Разборы по образцу на сегодня закончились. Попробуйте завтра.",
                      "en": "Sample reads are used up for today. Please try tomorrow."},
    "smp_budget": {"ru": "Стиль по образцу временно недоступен: месячный бюджет сервиса исчерпан. Напишите {support}.",
                   "en": "Style from a sample is paused: the service's monthly budget is used up. Contact {support}."},
    "smp_role_title": {"ru": "заголовок", "en": "title"}, "smp_role_subtitle": {"ru": "подзаголовок", "en": "subtitle"},
    "smp_role_rubric": {"ru": "рубрика", "en": "rubric"}, "smp_role_label": {"ru": "надпись", "en": "label"},
    "smp_role_credit": {"ru": "подпись фото", "en": "photo credit"}, "smp_role_counter": {"ru": "счётчик", "en": "counter"},
    "b_focus_b":  {"ru": "Низ", "en": "Bottom"},
    "b_focus_l":  {"ru": "Лево", "en": "Left"},
    "b_focus_r":  {"ru": "Право", "en": "Right"},
    "q_ask_text": {"ru": "Пришлите текст. Первая строка — заголовок, после пустой строки — подзаголовок, #слово — рубрика.",
                   "en": "Send the text. First line is the headline, after a blank line the subheadline, a #word the tag."},
    "q_sending":  {"ru": "Файлы готовятся…", "en": "Preparing files…"},
    "q_progress": {"ru": "Файлы готовятся: {i} из {n}", "en": "Preparing files: {i} of {n}"},
    "q_channel_hint": {"ru": "Альбом для канала: перешлите его в канал — подпись уже внутри.",
                       "en": "Album for a channel: forward it to your channel — the caption is already inside."},
    "q_channel_long": {"ru": "Текст длиннее 1024 знаков — такую подпись Telegram под альбомом не разрешает. "
                             "Альбом пришёл без подписи, текст поста целиком — следующим сообщением. Перешлите оба.",
                       "en": "The text is over 1,024 characters — Telegram doesn't allow such a caption under an album. "
                             "The album came without a caption, the full post text is the next message. Forward both."},
    "no_access":  {"ru": "Доступ закончился — посты сейчас недоступны. Чтобы продлить, напишите {support}.",
                   "en": "Access has ended — posting is unavailable. To renew, contact {support}."},
    "no_access_short": {"ru": "Доступ закончился", "en": "Access has ended"},
    "no_logo":    {"ru": "Сначала загрузите логотип: меню → «Настроить стиль».",
                   "en": "Upload a logo first: menu → “Set up style”."},
    "no_tpl":     {"ru": "Стилей пока нет — выберите стиль в редакторе.", "en": "No styles yet — pick one in the editor."},
    "limit_hit":  {"ru": "На этот период осталось {left} фото, а в посте {n}. Лимит обновится {reset}.",
                   "en": "{left} photos are left for this period, but the post has {n}. The limit resets on {reset}."},
    "limit_zero": {"ru": "Фото на этот период закончились. Лимит обновится {reset}. Чтобы расширить тариф, напишите {support}.",
                   "en": "No photos left for this period. The limit resets on {reset}. To upgrade, contact {support}."},
    "b_part":     {"ru": "Сделать {left} из {n}", "en": "Make {left} of {n}"},
    "photos_max": {"ru": "В одном посте до {n} фото — остальные не добавлены.",
                   "en": "Up to {n} photos per post — the rest weren't added."},
    "file_big":   {"ru": "Файл больше 20 МБ — это предел Telegram для ботов. Пришлите файл поменьше.",
                   "en": "The file is over 20 MB — Telegram's limit for bots. Send a smaller file."},
    "photo_bad":  {"ru": "Не получилось открыть этот файл как изображение.", "en": "Couldn't open this file as an image."},
    "photo_err":  {"ru": "Фото {i} не обработалось. Пришлите его ещё раз файлом.",
                   "en": "Photo {i} failed. Send it again as a file."},
    "photo_dropped": {"ru": "Фото {i} не открылось — оно убрано из поста. Пришлите его ещё раз файлом.",
                      "en": "Photo {i} couldn't be opened — it was removed from the post. Send it again as a file."},
    "photo_too_big": {"ru": "Фото {i} больше 100 мегапикселей — оно убрано из поста. Уменьшите его и пришлите ещё раз.",
                      "en": "Photo {i} is over 100 megapixels — it was removed from the post. Downsize it and send it again."},
    "not_photo":  {"ru": "NUMBUS оформляет только фото. Пришлите фото или файл JPG, PNG, HEIC.",
                   "en": "I work with photos. Send a photo or a JPG, PNG or HEIC file."},
    "logo_big":   {"ru": "Файл слишком большой — больше 25 мегапикселей. Пришлите логотип поменьше.",
                   "en": "The file is too large — over 25 megapixels. Send a smaller logo."},
    "net_error":  {"ru": "Не получилось связаться с Telegram — сообщение дошло не целиком. Пришлите его ещё раз.",
                   "en": "Couldn't reach Telegram — your message didn't fully get through. Please send it again."},
    "fmt_orig":   {"ru": "Как в оригинале", "en": "Original ratio"},
    "tag_none":   {"ru": "Без рубрики", "en": "No tag"},
    "tag_custom": {"ru": "Своя рубрика", "en": "Custom tag"},
    "ask_custom_tag": {"ru": "Пришлите рубрику одним словом — решётка добавится сама.",
                       "en": "Send the tag as one word — the # is added automatically."},
    "custom_tag_bad": {"ru": "Пустая рубрика — пришлите ещё раз.", "en": "Empty tag — try again."},
    "edge":       {"ru": "Это предел", "en": "That's the limit"},
    "stale":      {"ru": "Эта кнопка устарела. Пришлите фото заново или откройте меню: /start",
                   "en": "This button is outdated. Send the photos again or open the menu: /start"},
    "cancelled":  {"ru": "Отменено.", "en": "Cancelled."},
    "error_user": {"ru": "Что-то пошло не так. Администратор уже знает об ошибке — попробуйте ещё раз через минуту.",
                   "en": "Something went wrong. The admin already knows — please try again in a minute."},

    # ---------- Команда ----------
    "team_head":  {"ru": "<b>Команда · {brand}</b>\nЛюдей в подписке: {n} из {limit}\n\n"
                         "По этой ссылке человек получит доступ только к этому бренду:\n{link}",
                   "en": "<b>Team · {brand}</b>\nPeople in the subscription: {n} of {limit}\n\n"
                         "This link gives access to this brand only:\n{link}"},
    "team_activity": {"ru": "\n\n<b>За текущий период</b>", "en": "\n\n<b>This period</b>"},
    "team_line":  {"ru": "{name} — {photos} фото, постов: {posts}{role}", "en": "{name} — {photos} photos, posts: {posts}{role}"},
    "mark_owner": {"ru": " · владелец", "en": " · owner"},
    "mark_designer": {"ru": " · дизайнер", "en": " · designer"},
    "team_empty": {"ru": "\nПока никого, кроме вас. Отправьте ссылку коллегам.",
                   "en": "\nNobody but you yet. Send the link to your colleagues."},
    "member_card": {"ru": "<b>{name}</b>{user}\nРоль: {role}\nВ бренде с {joined}\nЗа период: {pm} фото, постов: {po}\n"
                          "Всего фото: {pt}\nПоследний пост: {last}\nПоследний визит: {seen}",
                    "en": "<b>{name}</b>{user}\nRole: {role}\nIn this brand since {joined}\nThis period: {pm} photos, posts: {po}\n"
                          "Photos in total: {pt}\nLast post: {last}\nLast seen: {seen}"},
    "role_designer": {"ru": "дизайнер — меняет стили", "en": "designer — edits the styles"},
    "role_editor": {"ru": "участник — делает посты", "en": "member — makes posts"},
    "b_make_designer": {"ru": "Сделать дизайнером", "en": "Make designer"},
    "b_make_editor": {"ru": "Сделать участником", "en": "Make member"},
    "role_changed": {"ru": "Роль изменена", "en": "Role changed"},
    "designer_notify": {"ru": "Вам открыт редактор стиля бренда «{brand}»: меню → «Редактор стиля».",
                        "en": "You now have access to the style editor of “{brand}”: menu → “Style editor”."},
    "b_member_del": {"ru": "Убрать из бренда", "en": "Remove from brand"},
    "member_del_confirm": {"ru": "Убрать {name} из бренда «{brand}»? Ссылка-приглашение обновится, "
                                 "чтобы вернуться по старой было нельзя.",
                           "en": "Remove {name} from “{brand}”? The invite link will be renewed so the old one stops working."},
    "b_member_del_yes": {"ru": "Да, убрать", "en": "Yes, remove"},
    "member_removed": {"ru": "Участник убран, ссылка обновлена", "en": "Member removed, link renewed"},
    "removed_notify": {"ru": "Доступ к бренду «{brand}» закрыт.", "en": "Your access to “{brand}” has been removed."},
    "b_team_back": {"ru": "‹ Команда", "en": "‹ Team"},
    "never":      {"ru": "не было", "en": "never"},
    "b_team_new": {"ru": "Новая ссылка (старая перестанет работать)", "en": "New link (the old one stops working)"},
    "team_owner_only": {"ru": "Приглашать в команду может только владелец бренда.",
                        "en": "Only the brand owner can invite team members."},

    # ---------- Помощь ----------
    "help": {
        "ru": "<b>Как сделать пост</b>\n"
              "Пришлите фото с подписью — одно или несколько, до 30 в одном посте. Лучше файлом: так Telegram "
              "не сжимает снимки. Файл — до 20 МБ (предел Telegram для ботов), форматы JPG, PNG, HEIC. "
              "Telegram отправляет не больше 10 фото одним альбомом: если выбрать больше, они придут "
              "несколькими альбомами подряд — бот соберёт их в одну карусель с подписью первого.\n\n"
              "<b>Подпись</b>\n"
              "Первая строка — заголовок, после пустой строки — подзаголовок. #слово — рубрика; если хештегов "
              "несколько, рубрикой станет первый, остальные попадут в текст поста. Жирный текст Telegram "
              "на картинке выделяется цветом или начертанием стиля. Эмодзи остаются в тексте поста, на картинке "
              "их нет. Пока открыто превью, новый текст можно просто прислать — он заменит подпись.\n\n"
              "<b>Пульт под превью</b>\n"
              "Стиль, формат, рубрика, светлее или темнее, масштаб (− и +), текст. В карусели стрелки листают "
              "кадры. Если фото обрезается, кнопки «Верх · Центр · Низ» или «Лево · Центр · Право» выбирают, "
              "какую часть оставить.\n\n"
              "<b>Файлы</b>\n"
              "«Получить файлы» — альбомы по 10 файлов без сжатия, следом — текст поста для копирования. "
              "«Альбом для канала» — фото с подписью, его можно сразу переслать в канал; подпись длиннее "
              "1024 знаков придёт отдельным сообщением.\n\n"
              "<b>Форматы</b>\n"
              "4:5 и 3:4 — лента Instagram и Telegram, 1:1 — квадрат, 3:2 — горизонталь, 16:9 — широкий кадр, "
              "1.91:1 — превью ссылки, 9:16 — сторис, «Как в оригинале» — пропорции вашего фото.\n\n"
              "<b>Стиль</b>\n"
              "Меню → «Редактор стиля»: логотип, цвета, шрифт заголовка, затемнение. На компьютере — /desktop. "
              "«Стиль по образцу» собирает стиль по картинке вашего фирменного поста.\n\n"
              "<b>Команда и лимит</b>\n"
              "Меню → «Команда» (у владельца): ссылка-приглашение для коллег. Сколько фото осталось "
              "на период — в меню.\n\n"
              "Отменить текущий шаг — /cancel. Вопросы и идеи: {support}",
        "en": "<b>How to make a post</b>\n"
              "Send photos with a caption — one or several, up to 30 per post. Files are better: Telegram doesn't "
              "compress them. A file can be up to 20 MB (Telegram's limit for bots): JPG, PNG, HEIC. Telegram sends "
              "at most 10 photos per album: pick more and they arrive as several albums in a row — the bot joins "
              "them into one carousel with the first caption.\n\n"
              "<b>Caption</b>\n"
              "The first line is the headline, after a blank line — the subheadline. A #word is the section tag; "
              "with several hashtags the first one becomes the tag and the rest go into the post text. Telegram "
              "bold text is highlighted on the image with the style's colour or weight. Emoji stay in the post "
              "text but are left off the image. While a preview is open, just send new text to replace the caption.\n\n"
              "<b>Controls under the preview</b>\n"
              "Style, format, tag, lighter or darker, zoom (− and +), text. In a carousel the arrows switch photos. "
              "If the photo is cropped, “Top · Centre · Bottom” or “Left · Centre · Right” choose which part to keep.\n\n"
              "<b>Files</b>\n"
              "“Get files” sends uncompressed albums of 10, followed by the post text to copy. “Album for a channel” "
              "sends photos with the caption to forward straight to your channel; a caption over 1,024 characters "
              "comes as a separate message.\n\n"
              "<b>Formats</b>\n"
              "4:5 and 3:4 — Instagram and Telegram feed, 1:1 — square, 3:2 — landscape, 16:9 — wide, "
              "1.91:1 — link preview, 9:16 — stories, “Original ratio” — your photo's proportions.\n\n"
              "<b>Style</b>\n"
              "Menu → “Style editor”: logo, colours, headline font, shading. On a computer — /desktop. "
              "“Style from a sample” builds a style from a picture of your branded post.\n\n"
              "<b>Team and limit</b>\n"
              "Menu → “Team” (for the owner): an invite link for colleagues. Photos left for the period are shown "
              "in the menu.\n\n"
              "Cancel the current step — /cancel. Questions and ideas: {support}",
    },
}

MONTHS_GEN = {
    "ru": ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
}


def t(lang: str, key: str, **kw) -> str:
    entry = T.get(key)
    if entry is None:
        return key
    s = entry.get(lang) or entry.get("ru") or key
    return s.format(**kw) if kw else s
