import os
import sys
import time
import json
import ctypes
import subprocess
from datetime import datetime

import cv2
import mss
import numpy as np


ADB = r"C:\platform-tools\adb.exe"
ROOT = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(ROOT, "templates")
LOG_DIR = os.path.join(ROOT, "logs")
UNKNOWN_DIR = os.path.join(ROOT, "unknown")
DEBUG_DIR = os.path.join(ROOT, "debug")
STATE_FILE = os.path.join(ROOT, "state.json")

PHONE_W = 1060
PHONE_H = 2376
TARGET_STATE = 3

START_DELAY = 10
LOOP_DELAY = 0.55
STREAM_LAG = 6.0
KINGDOM_WAIT = 6.0
UNKNOWN_DELAY = 2.5
UNKNOWN_SAVE_INTERVAL = 12.0
UNKNOWN_DIFF = 8.0
MONITOR_INDEX = 1
DRY_RUN = False
TEMPLATE_CACHE = {}
WATCHDOG_SECONDS = 75.0
GOVERNOR_CONFIRM_SECONDS = 1.5

user32 = ctypes.windll.user32
VK_F8 = 0x77


DEFAULT_STATE = {
    "phase": "create_character",
    "step": "home",
    "target_state": 3,
    "next_nickname": 1,
    "characters_created": 0,
    "current_cycle": 1,
    "step_started_at": 0.0,
}


def ensure_dirs():
    for p in [ROOT, TPL, LOG_DIR, UNKNOWN_DIR, DEBUG_DIR]:
        os.makedirs(p, exist_ok=True)


def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fs():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg):
    line = f"[{ts()}] {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(LOG_DIR, "bot.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def save_img(path, img):
    ok = cv2.imwrite(path, img)
    if not ok:
        log(f"Не удалось сохранить {path}")
    return ok


def load_state():
    if not os.path.isfile(STATE_FILE):
        s = dict(DEFAULT_STATE)
        save_state(s)
        return s
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            old = json.load(f)
        s = dict(DEFAULT_STATE)
        s.update(old)

        # Migration from the v4 runtime state captured before the intro logic
        # existed.  The real device was still inside the opening cinematic.
        if s.get("phase") == "tutorial_new_character" and s.get("step") == "tutorial_scroll":
            s["step"] = "tutorial_intro"
            s["step_started_at"] = 0.0
            save_state(s)
            log("Миграция состояния: tutorial_scroll -> tutorial_intro")

        return s
    except Exception:
        return dict(DEFAULT_STATE)


def save_state(s):
    ensure_dirs()
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def set_step(s, step):
    s["step"] = step
    s["step_started_at"] = time.time()
    save_state(s)
    log(f"STEP -> {step}")


def set_phase(s, phase, step):
    s["phase"] = phase
    s["step"] = step
    s["step_started_at"] = time.time()
    save_state(s)
    log(f"PHASE -> {phase}; STEP -> {step}")


def adb(args, capture=False):
    cmd = [ADB] + list(args)
    if capture:
        return subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="ignore", check=False
        )
    return subprocess.run(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    )


def adb_check():
    r = adb(["devices", "-l"], capture=True)
    lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    if any("unauthorized" in x for x in lines):
        raise RuntimeError("Телефон unauthorized. Подтверди USB-отладку на HONOR.")
    dev = [x for x in lines if "\tdevice" in x or " device " in f" {x} "]
    if not dev:
        raise RuntimeError("Телефон со статусом device не найден.")
    log(f"ADB OK: {dev[0]}")


def tap(x, y):
    x, y = int(round(x)), int(round(y))
    if DRY_RUN:
        log(f"DRY RUN tap ({x},{y})")
        return
    adb(["shell", "input", "tap", str(x), str(y)])
    log(f"ADB tap phone=({x},{y})")


def tap_norm(nx, ny):
    tap(nx * PHONE_W, ny * PHONE_H)


def key(code):
    if not DRY_RUN:
        adb(["shell", "input", "keyevent", str(code)])


def text(value):
    if not DRY_RUN:
        adb(["shell", "input", "text", str(value)])


def emergency():
    return bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)


def tpl(name):
    if name in TEMPLATE_CACHE:
        return TEMPLATE_CACHE[name]
    p = os.path.join(TPL, name)
    if not os.path.isfile(p):
        TEMPLATE_CACHE[name] = None
        return None
    img = cv2.imread(p)
    TEMPLATE_CACHE[name] = img
    return img


def validate_templates():
    names = [
        "governor_avatar.png", "profile_settings.png", "settings_characters.png",
        "create_plus.png", "select_kingdom_title.png", "state3_modal.png",
        "loading_logo.png", "task_scroll.png",
        "upgrade_button.png", "newbie_offer_context.png", "offline_confirm.png",
        "invasion_title.png",
    ]
    bad = []
    for name in names:
        p = os.path.join(TPL, name)
        if os.path.isfile(p) and tpl(name) is None:
            bad.append(name)
    if bad:
        raise RuntimeError("Повреждены обязательные PNG-шаблоны: " + ", ".join(bad))

    # tutorial_skip.png in the current repository history is known to be
    # corrupted. Treat it as optional until a clean real-device crop replaces
    # it; this keeps the bot fail-closed instead of preventing startup.
    skip_path = os.path.join(TPL, "tutorial_skip.png")
    if os.path.isfile(skip_path) and tpl("tutorial_skip.png") is None:
        log("ВНИМАНИЕ: tutorial_skip.png повреждён — Skip временно отключён.")
    log(f"Обязательные шаблоны проверены: {len(names)-len(bad)} позиций.")


def match(phone, image, threshold):
    if image is None:
        return None
    h, w = image.shape[:2]
    if h > phone.shape[0] or w > phone.shape[1]:
        return None
    result = cv2.matchTemplate(phone, image, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(result)
    if score < threshold:
        return None
    return {"score": float(score), "loc": loc, "w": w, "h": h}


def tap_match(phone, hit):
    x, y = hit["loc"]
    cx = x + hit["w"] / 2
    cy = y + hit["h"] / 2
    tap(cx * PHONE_W / phone.shape[1], cy * PHONE_H / phone.shape[0])


def debug(phone, hit, name):
    x, y = hit["loc"]
    out = phone.copy()
    cv2.rectangle(out, (x, y), (x+hit["w"], y+hit["h"]), (0,255,0), 3)
    save_img(os.path.join(DEBUG_DIR, f"{fs()}_{name}_{hit['score']:.3f}.png"), out)


def crop_phone(frame):
    sh, sw = frame.shape[:2]
    pw = round(sh * PHONE_W / PHONE_H)
    left = (sw - pw) // 2
    right = left + pw
    if left < 0 or right > sw:
        raise RuntimeError(f"Неверный crop: monitor={sw}x{sh}")
    return frame[:, left:right], left, right


def stream_ok(frame, left, right):
    sw = frame.shape[1]
    if left < 80 or sw-right < 80:
        return True
    def ratio(a):
        g = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
        return float(np.mean(g < 45))
    return ratio(frame[:, :left]) >= 0.66 and ratio(frame[:, right:]) >= 0.66


def diff(a, b):
    if a is None or b is None:
        return 999.0
    aa = cv2.cvtColor(cv2.resize(a, (120,240)), cv2.COLOR_BGR2GRAY)
    bb = cv2.cvtColor(cv2.resize(b, (120,240)), cv2.COLOR_BGR2GRAY)
    return float(np.mean(cv2.absdiff(aa, bb)))


OVERLAYS = [
    ("newbie_offer", "newbie_offer_context.png", 0.86),
    ("offline_confirm", "offline_confirm.png", 0.90),
    ("invasion", "invasion_title.png", 0.88),
]


def handle_overlay(phone):
    hit = match(phone, tpl("newbie_offer_context.png"), 0.86)
    if hit:
        debug(phone, hit, "newbie_offer")
        log(f"Контекстно найден «Ценный набор новичка» {hit['score']:.3f}")
        tap_norm(0.852, 0.193)
        return True

    hit = match(phone, tpl("offline_confirm.png"), 0.90)
    if hit:
        debug(phone, hit, "offline_confirm")
        log(f"Найден офлайн-доход {hit['score']:.3f}")
        tap_match(phone, hit)
        return True

    hit = match(phone, tpl("invasion_title.png"), 0.88)
    if hit:
        debug(phone, hit, "invasion")
        log(f"Найдено «Вторжение мятежников» {hit['score']:.3f}")
        tap_norm(0.50, 0.955)
        return True

    return False


CREATE_STEPS = {
    "home": ("governor_avatar.png", 0.91, "profile"),
    "profile": ("profile_settings.png", 0.93, "settings"),
    "settings": ("settings_characters.png", 0.93, "characters"),
    "characters": ("create_plus.png", 0.93, "kingdom_picker"),
}


def handle_create_step(phone, state):
    step = state["step"]

    if step in CREATE_STEPS:
        name, threshold, nxt = CREATE_STEPS[step]
        hit = match(phone, tpl(name), threshold)
        if not hit:
            return False
        debug(phone, hit, step)
        log(f"Ожидаемый экран {step}: confidence={hit['score']:.3f}")
        tap_match(phone, hit)
        set_step(state, nxt)
        return True

    if step == "kingdom_picker":
        hit = match(phone, tpl("select_kingdom_title.png"), 0.93)
        if not hit:
            return False

        debug(phone, hit, "kingdom_picker")
        log("Экран выбора королевства подтверждён. Ввожу ровно 3 один раз.")
        tap_norm(0.50, 0.282)
        time.sleep(0.4)
        key(123)
        for _ in range(12):
            key(67)
        text(str(int(state.get("target_state", TARGET_STATE))))
        set_step(state, "kingdom_results")
        return True

    if step == "kingdom_results":
        elapsed = time.time() - float(state.get("step_started_at", 0.0))
        if elapsed < KINGDOM_WAIT:
            return False

        # Never assume that the first search result is state #3: search can
        # contain 3, 13, 23, ... . Click only an explicitly recognised #3 row.
        exact = match(phone, tpl("state3_row.png"), 0.90)
        if not exact:
            return False
        debug(phone, exact, "state3_row")
        log("Найдена точная строка «Государство №3».")
        tap_match(phone, exact)
        set_step(state, "state_confirm")
        return True

    if step == "state_confirm":
        hit = match(phone, tpl("state3_modal.png"), 0.90)
        if not hit:
            return False

        debug(phone, hit, "state3_modal")
        log("Подтверждён диалог «создать в государстве #3». Нажимаю его Confirm.")
        tap_norm(0.710, 0.612)
        set_phase(state, "tutorial_new_character", "tutorial_intro")
        return True

    return False


def handle_tutorial(phone, state):
    step = state["step"]

    # Immediately after creating a character the game first shows its loading
    # screen and then a skippable intro/cinematic.  Do not look for the task
    # scroll before that intro is gone.
    if step == "tutorial_intro":
        loading = match(phone, tpl("loading_logo.png"), 0.82)
        if loading:
            log("Туториал: загрузочный экран, жду.")
            return "wait"

        skip = match(phone, tpl("tutorial_skip.png"), 0.88)
        if skip:
            debug(phone, skip, "tutorial_skip")
            log("Туториал: найдено «Пропустить». Пропускаю вступление.")
            tap_match(phone, skip)
            set_step(state, "tutorial_wait_scroll")
            return "acted"

        # Dialogue screens can appear before/after Skip.  Advance only when
        # the characteristic dialogue continuation marker is recognised.
        dialogue = match(phone, tpl("tutorial_dialogue_continue.png"), 0.88)
        if dialogue:
            debug(phone, dialogue, "tutorial_dialogue")
            log("Туториал: найден маркер продолжения диалога.")
            tap_match(phone, dialogue)
            return "acted"

        # If neither loading, Skip nor a dialogue marker is visible, save the
        # screen as unknown instead of guessing.
        return False

    if step in ("tutorial_scroll", "tutorial_wait_scroll"):
        # If the governor avatar becomes available, the mandatory tutorial has
        # reached the point required by the workflow.
        governor = match(phone, tpl("governor_avatar.png"), 0.91)
        if governor:
            log("Туториал: меню губернатора доступно — обязательная часть завершена.")
            set_phase(state, "tutorial_complete", "governor_available")
            return "wait"

        dialogue = match(phone, tpl("tutorial_dialogue_continue.png"), 0.88)
        if dialogue:
            debug(phone, dialogue, "tutorial_dialogue")
            log("Туториал: продолжаю подтверждённый диалог.")
            tap_match(phone, dialogue)
            return "acted"

        # Some cinematic pages can remain after the first skip request.
        # Retry only when the actual Skip template is still visible.
        skip = match(phone, tpl("tutorial_skip.png"), 0.88)
        if skip:
            debug(phone, skip, "tutorial_skip_retry")
            log("Туториал: «Пропустить» всё ещё видно. Нажимаю ещё раз.")
            tap_match(phone, skip)
            return "acted"

        hit = match(phone, tpl("task_scroll.png"), 0.88)
        if not hit:
            return False
        debug(phone, hit, "task_scroll")
        log("Туториал: найден свиток задания. Нажимаю свиток.")
        tap_match(phone, hit)
        set_step(state, "tutorial_building")
        return "acted"

    if step == "tutorial_building":
        hit = match(phone, tpl("upgrade_button.png"), 0.90)
        if not hit:
            return False
        debug(phone, hit, "upgrade_button")
        log("Туториал: найдено «Улучшить». Нажимаю один раз.")
        tap_match(phone, hit)
        set_step(state, "tutorial_wait_scroll")
        return "acted"

    return False


def create_capture():
    sct = mss.MSS()
    if MONITOR_INDEX >= len(sct.monitors):
        sct.close()
        raise RuntimeError("Монитор #1 не найден.")
    return sct, sct.monitors[MONITOR_INDEX]


def main():
    global DRY_RUN
    ensure_dirs()

    if "--reset-state" in sys.argv:
        s = dict(DEFAULT_STATE)
        save_state(s)
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return

    if "--dry-run" in sys.argv:
        DRY_RUN = True

    state = load_state()
    validate_templates()
    log("="*70)
    log(f"WAR BOT v4 | phase={state['phase']} step={state['step']}")
    adb_check()

    print("Через 10 секунд включи полноэкранную трансляцию HONOR Suite.")
    print("F8 — аварийная остановка.")
    for i in range(START_DELAY, 0, -1):
        print(f"Старт через {i}...")
        time.sleep(1)

    blocked_until = 0.0
    last_unknown = None
    last_unknown_at = 0.0
    unknown_since = None
    warn_at = 0.0
    sct = monitor = None

    try:
        while True:
            if emergency():
                log("F8: остановка.")
                break

            if sct is None:
                try:
                    sct, monitor = create_capture()
                    log(f"Захват монитора: {monitor['width']}x{monitor['height']}")
                except Exception as e:
                    log(f"Захват не открылся: {e}; повтор через 2 сек.")
                    time.sleep(2)
                    continue

            try:
                shot = np.array(sct.grab(monitor))
            except Exception as e:
                log(f"BitBlt/захват: {e}. Пересоздаю захват.")
                try:
                    sct.close()
                except Exception:
                    pass
                sct = monitor = None
                time.sleep(2)
                continue

            frame = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
            phone, left, right = crop_phone(frame)

            if not stream_ok(frame, left, right):
                if time.time() - warn_at > 5:
                    log("HONOR Suite не в полноэкранной трансляции — пауза.")
                    warn_at = time.time()
                unknown_since = None
                time.sleep(0.7)
                continue

            if time.time() < blocked_until:
                time.sleep(LOOP_DELAY)
                continue

            acted = False

            if handle_overlay(phone):
                acted = True
            elif state["phase"] == "create_character":
                acted = handle_create_step(phone, state)
            elif state["phase"] == "tutorial_new_character":
                tutorial_result = handle_tutorial(phone, state)
                if tutorial_result == "acted":
                    acted = True
                elif tutorial_result == "wait":
                    unknown_since = None
                    time.sleep(1.5)
                    continue

            if acted:
                blocked_until = time.time() + STREAM_LAG
                log(f"Жду {STREAM_LAG:.1f} сек обновления трансляции.")
                unknown_since = None
                continue

            now = time.time()
            if unknown_since is None:
                unknown_since = now

            if now - unknown_since >= UNKNOWN_DELAY and now - last_unknown_at >= UNKNOWN_SAVE_INTERVAL:
                d = diff(phone, last_unknown)
                if d >= UNKNOWN_DIFF:
                    out = os.path.join(
                        UNKNOWN_DIR,
                        f"unknown_{fs()}_{state['phase']}_{state['step']}.png"
                    )
                    if save_img(out, phone):
                        log(f"НЕИЗВЕСТНЫЙ ЭКРАН — ничего не нажимаю: {out} | diff={d:.1f}")
                    last_unknown = phone.copy()
                    last_unknown_at = now

            time.sleep(LOOP_DELAY)

    finally:
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass

    log("WAR BOT v4 остановлен.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        ensure_dirs()
        log("Ctrl+C: остановка.")
    except Exception as e:
        ensure_dirs()
        log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
        print("Бот остановлен.")
        print("Причина:", e)
        input("Нажми Enter для выхода...")
