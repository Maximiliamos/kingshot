# Kingshot / «Война за трон» automation

Локальный бот для автоматизации повторяемых действий в мобильной игре «Война за трон» на реальном Android-телефоне.

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

Базовая точка: **v4**.

Уже работает:
- подключение реального телефона по ADB;
- захват изображения через окно scrcpy;
- распознавание шаблонов OpenCV;
- пересчёт координат и ADB tap;
- защита от устаревших кадров трансляции;
- F8 как аварийная остановка;
- восстановление захвата после BitBlt/access errors;
- последовательность меню: город → профиль → настройки → персонажи → создание персонажа;
- начало автоматизации tutorial через «свиток задания → улучшить».

Известные проблемы:
- поиск государства по `3` в текущей реализации может выбрать **№23 вместо №3**; это приоритетный баг;
- tutorial нового персонажа пока не пройден полностью;
- нельзя использовать универсальные шаблоны крестика/кнопки вне контекста — это уже приводило к переходам в язык, события и магазин.

## Установка

```bat
python -m pip install -r requirements.txt
```

Ожидается Android Platform Tools в:

```text
C:\platform-tools
```

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

Очистка данных требует явного подтверждения:

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

## Следующие задачи

1. Исправить выбор точного государства №3.
2. Полностью пройти tutorial нового персонажа до появления меню губернатора.
3. Добавить переименование в `Тугарин<N>`.
4. Добавить создание следующего персонажа.
5. Добавить контролируемый reset данных приложения и продолжение счётчика.
