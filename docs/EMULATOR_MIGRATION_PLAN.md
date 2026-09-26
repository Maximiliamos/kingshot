# WAR BOT: переход к управляемому Android

Статус: утверждено 2026-09-26.

## Цель

Сделать state machine, vision и безопасные действия независимыми от места
запуска игры. Физический HONOR через scrcpy остаётся поддерживаемым backend,
пока совместимость официального Android Emulator не доказана.

## Подтверждённая игра

- Google Play: «Война за трон» от Echofun Interactive Limited.
- Package: `com.got.globalru`.
- Launcher: `com.unity3d.player.MyMainPlayerActivity`.
- Проверенный комплект: versionName `1.12.10`, versionCode `163`.
- minSdk `24`, targetSdk `35`, native ABI `arm64-v8a`.
- APKS состоит из `base.apk`, `split_config.arm64_v8a.apk` и
  `split_game_asset.apk`.

`games.war.regions` — другая игра (War Regions от SayGames) и не является
целью WAR BOT.

## Неизменяемое архитектурное решение

```text
State machine / tutorial / future modules
                    |
Vision, OCR and safe actions
                    |
              DeviceBackend
             /             \
physical_scrcpy        emulator
```

Интерфейс устройства должен предоставлять `get_frame`, `tap`, `swipe`,
`input_text`, `launch_game`, `stop_game`, `clear_game_data` и `health`.
Tutorial не должен знать, какой backend используется.

## Этапы и GO/NO-GO

1. Зафиксировать рабочую physical/scrcpy-версию тестами, commit, push и tag.
2. Исследовать APKS и установленный пакет.
3. Сделать изолированный Emulator PoC без переноса tutorial.
4. G1: APKS устанавливается.
5. G2: игра запускается и корректно рисуется.
6. G3: игра допускает требуемый сценарий без обхода её ограничений.
7. G4: кадры и ADB-действия достаточно быстрые.
8. После GO вынести `DeviceBackend`, сохранив physical backend.
9. Подключить существующую state machine к EmulatorBackend без переписывания.
10. Один ограниченный E2E, затем три последовательных цикла и тест 1–2 часа.
11. Только после стабильности развивать Android GUI и multi-instance.

При FAIL на G2 или G3 physical backend остаётся основным. Маскировка
эмулятора и обход серверных ограничений не выполняются.

## Источники кадров для benchmark

Проверяются в таком порядке: ADB `exec-out screencap -p`, scrcpy, захват окна.
Решение принимается по FPS, задержке tap-to-frame, CPU и числу чёрных кадров.

## Данные и безопасность

Счётчики, phase/step и device id хранятся вне Android. Snapshot не считается
откатом серверного состояния. `pm clear`, массовые циклы и создание персонажей
разрешаются только на обозначенном тестовом аккаунте и в заданных пределах.
