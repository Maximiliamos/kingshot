# TUGARIN BOTS

Локальное Windows-приложение для автоматизации повторяемых действий в мобильной игре «Война за трон». Основной Windows-only runtime — Windows Subsystem for Android (WSA); физический телефон и окно scrcpy для штатной работы не нужны.

## Текущая архитектура

TUGARIN BOTS теперь отделяет логику игры от способа подключения к Android.

```text
WSA / Native ARM64 PoC / любой готовый ADB Android
                  │
                  ▼
           DeviceBackend
      ┌───────────┼────────────┐
      │           │            │
 screenshot     input      app lifecycle
      │           │            │
      └───────────┴────────────┘
                  │
                  ▼
          OpenCV vision layer
                  │
                  ▼
       safe state machine / GUI
```

По умолчанию используется `wsa` с ADB `127.0.0.1:58526`. WSA может выполнять
ARM64-библиотеки через штатный Android native bridge; поэтому его критерий
приёмки — стабильный запуск игры, прямой screenshot и ADB input, а не отсутствие
трансляции. Собственный ARM64 QEMU сохранён как исследовательский fallback.

Доступны режимы:

- **wsa** — основной Windows-only backend;
- **native_arm64** — диагностический PoC без трансляции, пока не прошедший boot gate;
- **adb** — уже запущенный Android по конкретному ADB serial;
- **scrcpy** — только legacy/диагностический источник кадров.

Из зрелых open-source проектов взяты архитектурные идеи, а не скопированный
код: device-scoped transport как в adbutils, опциональный системный UI-канал
как в uiautomator2, независимый video/control transport как в scrcpy и
image-first подход для Unity-интерфейса как в Airtest.


## Текущая версия

Текущий hardening-релиз: **feature/tugarin-bots-v1-hardening** поверх интеграционного Draft PR #6. После зелёного CI и real-host gate изменения переходят в основную интеграционную ветку.

На уровне приложения уже реализованы:

- пользовательское имя продукта **TUGARIN BOTS** (старые `WAR_BOT_*` переменные временно сохранены для совместимости);

- единый Android backend, отдельные WSA и native ARM64 gates;
- GUI + CLI;
- автоматический bootstrap Android → игра;
- прямой screenshot/input через Android transport;
- low-latency H.264 `screenrecord → FFmpeg` preview с автоматическим fallback на PNG `screencap`;
- один долгоживущий preview-worker вместо создания нового потока на каждый кадр;
- FPS/latency preview telemetry;
- интерактивный экран Android внутри GUI: tap/swipe/hold/wheel/right-click Back;
- ASCII и Unicode clipboard/paste input (через закреплённый uiautomator2);
- Android volume up/down/mute;
- ручной ввод автоматически ставит автоматизацию на паузу, чтобы действия не конфликтовали;
- read-only health probes сети, доступности Интернета и Android-аудиосервиса;
- bounded recovery при потере capture/ADB или падении Kingshot;
- structured events + runtime heartbeat;
- atomic state, previous snapshot и fail-closed при повреждённом state.json;
- точный выбор строки и Confirm государства №3;
- обязательный initial/character tutorial state machine;
- переименование без пробела: `Тугарин1`, `Тугарин2`, ...;
- продолжение счётчика между циклами;
- настраиваемый цикл и синхронный `pm clear`;
- fail-closed при неизвестном экране или сообщении о лимите/ограничении;
- тесты полного цикла, backend, runtime и vision.

Native ARM64 QEMU пока не является рабочим runtime: Google ranchu падает в
guest userspace, а upstream QEMU не предоставляет нужную ranchu/goldfish
графику. Этот путь не удалён, но не блокирует WSA MVP.

WSA installer выбирает пакет под фактическую версию Windows, проверяет SHA-256
архива и разделяет установку на две фазы: административная фаза меняет только
машинные компоненты Windows, затем регистрация AppX и запуск выполняются под
интерактивной учётной записью пользователя.


## Установка

Для обычного пользователя рекомендуемый вход один:

```bat
install_tugarin_bots.cmd
```

Он проверяет Git/Python 3.12+, передаёт управление безопасному dedicated-user
WSA setup и сохраняет существующие WSA-данные перед миграцией (если не указан
явный skip).

Для разработчика зависимости закреплены:

```bat
python -m pip install -r requirements.txt
python -m pip install -r requirements-android-optional.txt
```

Основной runtime — WSA. Android Emulator/QEMU SDK больше не является
обязательной частью production-установки; эти компоненты нужны только для
архивных диагностических PoC.

Для резервного OCR подписей можно отдельно установить Tesseract с русским
языком. OpenCV/template recognition остаётся основным путём для Unity UI.

## Чистая переустановка WSA в выделенный Windows-профиль

Если WSA зарегистрирован под другим Windows SID или AppX сообщает `0x80073CFB`,
используй единый recovery/bootstrap-скрипт:

```powershell
cd C:\warbot_git
powershell -ExecutionPolicy Bypass -File .\install_tugarin_bots.ps1 -Branch feature/tugarin-bots-v1-hardening
```

Скрипт:

- сохраняет найденные `userdata.vhdx` и WSA Settings;
- удаляет WSA-регистрацию у всех Windows-пользователей;
- создаёт локальный профиль `TugarinBots` и временно даёт ему права локального администратора для установки;
- переносит старое дерево WSA в backup, сохраняя проверяемый download-cache;
- ставит продолжение через Task Scheduler на вход `TugarinBots`;
- после первого входа автоматически обновляет репозиторий, создаёт отдельный Python venv,
  заново устанавливает/регистрирует WSA, запускает строгий P0;
- ярлык GUI создаётся только после `WSA_GAME_PASS`; GUI намеренно не запускается автоматически.

Пароль нового пользователя вводится через SecureString и не сохраняется скриптом.
Автоматический Windows logon намеренно не включается.

## Запуск

Запустить MVP GUI:

```bat
run_gui.vbs
```

`run_gui.vbs` и созданный установщиком ярлык используют `pythonw.exe` и не
создают консольное окно. `run_gui.bat` оставлен только как совместимый wrapper.

Или напрямую без консоли:

```bat
pythonw gui.py
```

GUI показывает непрерывный preview выбранного Android backend, FPS/latency,
runtime heartbeat, текущую фазу `state.json`, health и статистику цикла.
WSA является production-режимом; Native ARM64/ADB/scrcpy оставлены для
диагностики и совместимости. Кнопки
паузы и остановки передают команды движку через `control.json`, поэтому GUI
не нажимает кнопки игры самостоятельно.

Сбросить локальное состояние:

```bat
python C:\warbot\bot.py --reset-state
```

Обычный запуск:

```bat
python C:\warbot\bot.py
```

Тест без реальных нажатий:

```bat
python C:\warbot\bot.py --dry-run
```

Для целевого режима scrcpy больше не требуется. После загрузки WSA Android
кадры берутся напрямую через `adb exec-out screencap -p`, а input отправляется
в тот же device-scoped ADB serial.

Legacy scrcpy оставлен только для диагностики. Для него можно задать
`WAR_BOT_BACKEND=scrcpy` и `WAR_BOT_SCRCPY_TITLE`.

**F8** — аварийная остановка.

## Backend CLI

Для локальных агентов и диагностики добавлен единый CLI:

```powershell
python .\warbot_cli.py status
python .\warbot_cli.py bootstrap --output bootstrap-frame.png
python .\warbot_cli.py screenshot --output frame.png
python .\warbot_cli.py install-game
python .\warbot_cli.py launch-game
python .\warbot_cli.py stop-game
python .\warbot_cli.py ui-dump
```

Установка игры в Native ARM64 режиме разрешается только после строгого gate:
ADB=`device`, `sys.boot_completed=1`, ABI=`arm64-v8a`, без x86/native bridge.
Для WSA обязательны ADB=`device`, `sys.boot_completed=1`, стабильный процесс
игры и корректный PNG; наличие штатного native bridge допустимо.

Очистка данных требует явного подтверждения. Для обычной работы предпочтителен
синхронизированный clean-start, который сохраняет PC-side счётчик и возвращает
state machine в initial tutorial:

```powershell
python .\warbot_cli.py clean-start --yes
```

Низкоуровневый maintenance-вариант также доступен:

```powershell
python .\warbot_cli.py clear-game-data --yes
```

Управление ARM64 runtime:

```powershell
python .\warbot_cli.py start-runtime
python .\warbot_cli.py stop-runtime
```

Системный UI-канал uiautomator2 закреплён в
`requirements-android-optional.txt` и production installer ставит его в
dedicated venv. Он используется для Android permission/settings dialogs и
Unicode clipboard input. Интерфейс самой Unity-игры по-прежнему
обрабатывается OpenCV-шаблонами.

## Полный цикл

Для чистого запуска состояние по умолчанию начинается с обязательного
обучения. После его завершения TUGARIN BOTS:

```text
initial tutorial
→ create exact State #3
→ character tutorial
→ rename to Тугарин<N>
→ create next character
```

Счётчик `Тугарин<N>` хранится на ПК и не сбрасывается при `pm clear`.

В GUI можно задать число персонажей в одном цикле и отдельно включить
автоматическую очистку данных игры. Автосброс по умолчанию выключен. Если
OCR видит сообщение о лимите/ограничении аккаунта или сервера, бот
останавливается; очистка данных не используется как обход такого ограничения.

`bootstrap` — рекомендуемый первый запуск: он будит WSA,
проверяет готовность Android, при необходимости устанавливает ARM64 splits
игры, запускает игру и сохраняет контрольный screenshot.

Первичная установка WSA (один UAC-запрос):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_wsa_poc.ps1
```

## Что не коммитим

Локальные runtime-данные исключены через `.gitignore`:
- `state.json`
- `logs/`
- `unknown/`
- `debug/`
- временные скриншоты.

## Рабочий цикл ChatGPT ↔ Windows host

Для дальнейшей разработки используется один повторяемый цикл:

```text
ChatGPT пишет код в feature/unified-android-backend
        ↓
пользователь запускает одну команду
        ↓
скрипт сам делает git pull
        ↓
запускает real-host verifier
        ↓
собирает console/runtime/crash/tombstone отчёты
        ↓
публикует их в GitHub branch runtime-reports
        ↓
ChatGPT читает отчёт из GitHub и делает следующий фикс
```

Команда для обычного цикла:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_and_report.ps1
```

Скрипт автоматически обновляет `feature/unified-android-backend`, запускает
`verify_mvp.ps1`, формирует manifest, сохраняет консольный вывод и доступные
runtime-диагностики, после чего коммитит их в отдельную ветку
`runtime-reports`. Исходная рабочая ветка не загрязняется отчётами.

На GitHub последний отчёт определяется файлом:

```text
runtime-reports/LATEST.json
```

Если автоматический push не авторизован на конкретном Windows-ПК, отчёт не
теряется: скрипт оставляет staging-каталог в `%TEMP%` и сообщает его путь.
После однократной настройки Git credentials следующие циклы полностью
автоматические.

## Финальный real-host gate

После зелёного CI остаётся один аппаратно-зависимый прогон на Windows-машине.
Рекомендуемый вариант выполняет bootstrap, строгий native gate, status и
проверку PNG одной командой:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_mvp.ps1
```

Низкоуровневый эквивалент:

```powershell
python .\warbot_cli.py bootstrap --output bootstrap-frame.png
```

PASS означает: Android полностью загрузился, WSA доступен по ADB, игра
установлена/запущена, процесс жив и получен реальный PNG. После этого GUI
кнопкой «ЗАПУСТИТЬ» использует тот же bootstrap автоматически и запускает
state machine.

WSA installer и verifier сохраняют отчёты в `runtime-reports`; native ARM64
fallback дополнительно формирует `C:\warbot_arm64_runtime\zygote-crash.txt`
и `boot-diagnostic.json`.

## Проверка релиза

Для финальной проверки конкретного commit на целевом Windows/WSA ПК после
закрытия GUI используется единый gate:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_release.ps1
```

Он повторяет test suite, выполняет реальный WSA + Kingshot 120-second gate и
проверяет отсутствие оставшихся dedicated-user Python/CMD/conhost процессов.
Полные критерии игрового flow/soak описаны в
`docs/RELEASE_ACCEPTANCE.md`.

## Дальнейший план

Актуальный план разработки, включая низколатентное видео, передачу игрового звука, клавиатуру и сетевую готовность, находится в `docs/TUGARIN_BOTS_ROADMAP.md`.
