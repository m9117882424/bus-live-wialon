# Учётные записи водителей

В админке откройте «Настройки → Водители». Можно создать до 10 аккаунтов, включая отключённые. Укажите имя водителя или автобус, логин (3–40 латинских букв, цифр, _, . или -) и пароль (8–128 символов). Логин не зависит от регистра, пароль зависит.

При редактировании пустое поле пароля сохраняет текущий пароль. Смена пароля или логина, отключение и удаление отзывают все сессии аккаунта. Удаление освобождает место, история посадок сохраняет логин водителя. Новых пользователей по умолчанию нет.

Водитель входит на /bus/scanner/ по логину и паролю. Вход сохраняется до 30 дней. Есть имя текущего водителя, смена аккаунта и выход. Доступны номер пропуска и QR. После выпуска общий код перестаёт работать, поэтому администратор должен создать аккаунты перед началом работы водителей.

Миграция SQLite добавляет таблицы аккаунтов и сессий, поля driver_id/driver_login в boardings. Старые посадки остаются без водителя. Пароли хранятся как salted PBKDF2-SHA256; токены сессий — как SHA256, cookie HttpOnly/SameSite=Strict/Secure на HTTPS.

Проверки: python tests/test_driver_accounts_http.py; python tests/live_regressions/test_rules.py; python tests/live_regressions/test_horizon.py; python tests/live_regressions/test_lang_routes.py; python tests/live_regressions/test_chrome_pwa.py; node tests/live_regressions/test_flag_picker.js; node tests/live_regressions/test_lang_ui.js; node tests/live_regressions/test_lang_runtime.js; node tests/live_regressions/test_horizon_ui.js; node tests/live_regressions/test_chrome_pwa_ui.js.

Дополнительная проверка в Chrome: python tests/browser_acceptance.py (нужен puppeteer и браузер Chrome). Все тесты создают временную БД, не читают рабочие env-файлы и не меняют production.
