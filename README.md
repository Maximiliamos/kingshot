# Kingshot / «Война за трон» automation

Локальное Windows-приложение для автоматизации повторяемых действий в мобильной игре «Война за трон» через собственный ARM64 Android runtime или другой ADB Android.

## Текущая архитектура

WAR BOT теперь отделяет логику игры от способа подключения к Android.

```text
Native ARM64 QEMU / любой готовый ADB Android
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

По умолчанию используется `native_arm64`: собственный ARM64 Android runtime
под QEMU TCG и ADB `127.0.0.1:5561`. После прохождения системного ARM64 gate
тот же backend будет использоваться для игры.

Доступны режимы:

- **native_arm64** — целевой production backend;
- **adb** — уже запущенный Android по конкретному ADB serial;
- **scrcpy** — только legacy/диагностический источник кадров.

Из зрелых open-source проектов взяты архитектурные идеи, а не скопированный
код: device-scoped transport как в adbutils, опциональный системный UI-канал
как в uiautomator2, независимый video/control transport как в scrcpy и
image-first подход для Unity-интерфейса как в Airtest.


## Текущая версия

Интеграционная ветка: **feature/unified-android-backend** / Draft PR #6.

На уровне приложения уже реализованы:

- единый Android backend и строгий ARM64 gate;
- GUI + CLI;
- автоматический bootstrap Android → игра;
- прямой screenshot/input через ADB;
- точный выбор строки и Confirm государства №3;
- обязательный initial/character tutorial state machine;
- переименование без пробела: `Тугарин1`, `Тугарин2`, ...;
- продолжение счётчика между циклами;
- настраиваемый цикл и синхронный `pm clear`;
- fail-closed при неизвестном экране или сообщении о лимите/ограничении;
- тесты полного цикла, backend, runtime и vision.

Последний real-host прогон доказал, что legacy Google `ranchu` под TCG
доходит до adbd, но не завершает Android boot: zygote/HAL-процессы продолжают
падать, а ядро поднимает только CPU0 из-за отсутствия PSCI для TCG. Поэтому
production runtime переключён на Android-модифицированную Google машину
`virt`, которую AOSP специально расширил ranchu/goldfish-устройствами и
которая сохраняет PSCI/multicore. Legacy `ranchu` оставлен только как
диагностический fallback.

Следующий real-host gate должен подтвердить `virt` до
`ADB=device` + `sys.boot_completed=1`. При ошибке автоматически
сохраняются serial log, crash buffer, tombstones (если доступны),
`zygote-crash.txt` и `boot-diagnostic.json`.


## Установка

```bat
python -m pip install -r requirements.txt
```

По умолчанию используется Android SDK в `C:\Android\Sdk`. Путь можно
переопределить через `WAR_BOT_ANDROID_SDK`. Bootstrap проверяет Android
Emulator/QEMU, Platform Tools и ARM64 Android 11 system image; отсутствующий
system image устанавливается через `sdkmanager`.

Для резервного распознавания подписей кнопок установи Tesseract с русским языком:

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact
```

Затем помести официальный `rus.traineddata` в каталог `tessdata` установленного Tesseract.

Шаблоны должны находиться в:

```text
C:\warbot\templates
```

## Запуск

Запустить MVP GUI:

```bat
run_gui.bat
```

Или напрямую:

```bat
python gui.py
```

GUI показывает кадр выбранного Android backend, текущую фазу `state.json`,
журнал и статистику цикла. В настройках можно выбрать Native ARM64, обычный
ADB или legacy scrcpy, а также запустить/остановить ARM64 runtime. Кнопки
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

Для целевого режима scrcpy больше не требуется. После загрузки ARM64 Android
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

Опциональный системный UI-канал uiautomator2 устанавливается отдельно:

```powershell
python -m pip install -r requirements-android-optional.txt
```

Он нужен только для Android permission/settings dialogs. Интерфейс самой Unity
игры по-прежнему обрабатывается OpenCV-шаблонами.

## Полный цикл

Для чистого запуска состояние по умолчанию начинается с обязательного
обучения. После его завершения WAR BOT:

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

`bootstrap` — рекомендуемый первый запуск: он поднимает Native ARM64 Android,
проверяет строгий ARM64 gate, при необходимости устанавливает ARM64 splits
игры, запускает игру и сохраняет контрольный screenshot.

## Что не коммитим

Локальные runtime-данные исключены через `.gitignore`:
- `state.json`
- `logs/`
- `unknown/`
- `debug/`
- временные скриншоты.

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

PASS означает: Android полностью загрузился, native ARM64 gate пройден, игра
установлена/запущена, процесс жив и получен реальный PNG. После этого GUI
кнопкой «ЗАПУСТИТЬ» использует тот же bootstrap автоматически и запускает
state machine.

Если boot не проходит, runtime автоматически формирует
`C:\warbot_arm64_runtime\zygote-crash.txt` и
`boot-diagnostic.json`, поэтому следующий blocker определяется по фактам,
а не перебором параметров.
