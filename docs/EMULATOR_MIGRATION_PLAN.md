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

## PoC evidence

- Официальный ARM64 system image API 34 установлен, но QEMU2 37.1.11 на
  x86-64 Windows отклонил запуск: архитектура AVD должна совпадать с host.
- Следующий G1-кандидат: официальный x86_64 Google Play image API 34 с
  поставляемым Google ARM DBT/native bridge. Совместимость считается
  подтверждённой только после реального `install-multiple` ARM64 split APK.
- G1 PASS: x86_64 Google Play AVD загрузился через WHPX, объявил ABI
  `x86_64,arm64-v8a` с `libndk_translation.so` и успешно установил все три APK.
- G2 FAIL: `com.got.globalru` завершается при старте. Android зафиксировал
  `UnsatisfiedLinkError`: ARM64-процесс пытается загрузить находящийся в split
  файл `libnesec-x86.so` формата `EM_X86_64` вместо `EM_AARCH64`. Это
  несовместимость защитного/упаковочного слоя игры с официальным ARM DBT.
  Библиотеки APK не модифицируются; основной backend остаётся physical/scrcpy.

## BlueStacks result

Stock BlueStacks 5 Android 11 Rvc64 was tested with the Google Play ARM64 split.
The game starts and reaches its loading flow, but the process exits
reproducibly with SIGSEGV in `/system/lib64/libhoudini.so`. Native tombstones
show an x86_64 process executing the ARM64 app through Houdini.

This closes BlueStacks as the primary path for the current build without
patching the game or its translation layer.

## Следующий Windows-only backend

The active PoC is now a full ARM64 guest under
`qemu-system-aarch64` software emulation (TCG). The key gate is not merely
that an ARM64 APK installs: Android itself must report `arm64-v8a` with no x86
ABI and no `ro.dalvik.vm.native.bridge`.

This path intentionally trades speed for compatibility and removes Houdini /
libndk_translation from the execution path. See
`docs/NATIVE_ARM64_EMULATOR_PLAN.md`.

## Данные и безопасность

Счётчики, phase/step и device id хранятся вне Android. Snapshot не считается
откатом серверного состояния. `pm clear`, массовые циклы и создание персонажей
разрешаются только на обозначенном тестовом аккаунте и в заданных пределах.
