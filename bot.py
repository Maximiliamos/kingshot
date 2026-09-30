import os
import sys
import time
import json
import ctypes
from ctypes import wintypes
import subprocess
import csv
import io
import atexit
import shutil
import msvcrt
from difflib import SequenceMatcher
from datetime import datetime

import cv2
import mss
import numpy as np

from device_backend import BackendCapture, BackendError, create_backend
from runtime_events import emit_event
from runtime_recovery import RecoveryController


WINDOWS_NO_WINDOW = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
)


ADB = os.environ.get("WAR_BOT_ADB", "")
BACKEND_NAME = os.environ.get("WAR_BOT_BACKEND", "wsa").strip().lower()
ANDROID_SERIAL = os.environ.get("WAR_BOT_ANDROID_SERIAL", "127.0.0.1:58526")
DEVICE_BACKEND = None
TESSERACT = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"
)
ROOT = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(ROOT, "templates")
LOG_DIR = os.path.join(ROOT, "logs")
UNKNOWN_DIR = os.path.join(ROOT, "unknown")
DEBUG_DIR = os.path.join(ROOT, "debug")
STATE_FILE = os.path.join(ROOT, "state.json")
STATE_PREVIOUS_FILE = os.path.join(ROOT, "state.previous.json")
CONTROL_FILE = os.path.join(ROOT, "control.json")
PID_FILE = os.path.join(ROOT, "bot.pid")
LOCK_FILE = os.path.join(ROOT, "bot.lock")
INSTANCE_LOCK = None

PHONE_W = 1060
PHONE_H = 2376
INPUT_W = PHONE_W
INPUT_H = PHONE_H
VISION_W = 421
VISION_H = 944
TARGET_STATE = 3

START_DELAY = 2
LOOP_DELAY = 0.12
ACTION_MIN_SETTLE = 0.30
ACTION_TIMEOUT = 8.0
ACTION_CHANGE_DIFF = 3.0
UPGRADE_HOLD_MS = 8000
OCR_INTERVAL = 1.5
OCR_LOCK_SECONDS = 4.0
OCR_ABSENCE_SECONDS = 2.0
KINGDOM_WAIT = 0.8
UNKNOWN_DELAY = 2.5
UNKNOWN_SAVE_INTERVAL = 12.0
UNKNOWN_DIFF = 8.0
DRY_RUN = False
TEMPLATE_CACHE = {}
LAST_OCR_AT = 0.0
WATCHDOG_SECONDS = 75.0
GOVERNOR_CONFIRM_SECONDS = 1.5

user32 = ctypes.windll.user32
# MSS uses physical pixels. Ask Windows for client-window coordinates in the
# same coordinate space before converting them into an MSS rectangle.
try:
    user32.SetProcessDPIAware()
except Exception:
    pass
VK_F8 = 0x77
SCRCPY_VIDEO_TITLE = os.environ.get("WAR_BOT_SCRCPY_TITLE", "FCP-AN10")


class ScrcpyCapture:
    """Capture only the client pixels of the live scrcpy window.

    Capturing the whole monitor worked around HONOR Suite fullscreen mode, but
    it also made coordinates depend on unrelated windows.  scrcpy has a native
    low-latency video window, so its client rectangle is the only trusted input.
    """

    def __init__(self):
        self.sct = mss.MSS()
        self.hwnd = None
        self.rect = None

    def close(self):
        self.sct.close()

    @staticmethod
    def _visible_scrcpy_windows():
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        found = []

        def visit(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            size = user32.GetWindowTextLengthW(hwnd)
            if not size:
                return True
            title = ctypes.create_unicode_buffer(size + 1)
            user32.GetWindowTextW(hwnd, title, size + 1)
            # scrcpy opens a terminal titled "scrcpy - DEVICE" plus an SDL
            # video window titled just "DEVICE".  The latter is the source of
            # pixels; capturing the terminal would silently feed log text to
            # OpenCV.  The title may be overridden for another device.
            if title.value.casefold() == SCRCPY_VIDEO_TITLE.casefold():
                found.append((hwnd, title.value))
            return True

        user32.EnumWindows(enum_proc(visit), 0)
        return found

    def _refresh_rect(self):
        candidates = self._visible_scrcpy_windows()
        if len(candidates) != 1:
            titles = ", ".join(title for _, title in candidates) or "нет"
            raise RuntimeError(
                "Ожидается ровно одно видимое окно scrcpy; найдено: " + titles
            )
        self.hwnd, title = candidates[0]
        rect = wintypes.RECT()
        point = wintypes.POINT(0, 0)
        if not user32.GetClientRect(self.hwnd, ctypes.byref(rect)):
            raise RuntimeError("Не удалось прочитать клиентскую область scrcpy.")
        if not user32.ClientToScreen(self.hwnd, ctypes.byref(point)):
            raise RuntimeError("Не удалось определить положение окна scrcpy.")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 100 or height < 100:
            raise RuntimeError("Окно scrcpy слишком мало или свёрнуто.")
        self.rect = {"left": point.x, "top": point.y, "width": width, "height": height}
        return title

    def grab(self):
        title = self._refresh_rect()
        shot = np.array(self.sct.grab(self.rect))
        return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR), title, dict(self.rect)


class ActionGate:
    """Require visual evidence before the state machine may click again."""

    def __init__(self):
        self.before = None
        self.label = None
        self.started_at = 0.0

    @property
    def pending(self):
        return self.before is not None

    def arm(self, phone, label):
        self.before = phone.copy()
        self.label = label
        self.started_at = time.monotonic()
        log(f"Действие отправлено: {label}; жду смену кадра.")

    def observe(self, phone):
        if not self.pending:
            return "ready"
        elapsed = time.monotonic() - self.started_at
        if elapsed < ACTION_MIN_SETTLE:
            return "waiting"
        delta = diff(phone, self.before)
        if delta >= ACTION_CHANGE_DIFF:
            log(f"Кадр изменился после {self.label}: diff={delta:.1f}, {elapsed:.2f} сек.")
            self.before = None
            return "changed"
        if elapsed >= ACTION_TIMEOUT:
            return "timeout"
        return "waiting"


DEFAULT_STATE = {
    # A freshly installed/cleared game starts in the mandatory intro tutorial.
    "phase": "tutorial_new_character",
    "step": "tutorial_intro",
    "target_state": 3,
    "next_nickname": 1,
    "pending_nickname": 1,
    "characters_created": 0,
    "characters_created_cycle": 0,
    "characters_per_cycle": 4,
    "auto_reset_data": False,
    "repeat_cycles": True,
    "current_cycle": 1,
    "tutorial_origin": "initial",
    "last_stop_reason": "",
    "step_started_at": 0.0,
    "skip_locked": False,
    "skip_lock_version": 0,
    "tutorial_hand_locked": False,
    "tutorial_primary_locked": False,
    "ocr_locked_action": "",
    "ocr_locked_until": 0.0,
    "ocr_absent_since": 0.0,
    "ocr_upgrade_hold_ms": 0,
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
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # Windows CI/legacy consoles may still expose cp1252. Logging must
        # never crash the automation because a Russian status line cannot be
        # encoded by the host terminal.
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = (line + "\n").encode(encoding, errors="replace")
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(safe)
            sys.stdout.buffer.flush()
    try:
        with open(os.path.join(LOG_DIR, "bot.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        emit_event("log", message=str(msg))
    except Exception:
        # Event telemetry must never become a control-path dependency.
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
    except Exception as exc:
        # A corrupt state file is not equivalent to a fresh install. Starting
        # again from DEFAULT_STATE could duplicate destructive/game actions.
        # Preserve the evidence and fail closed for operator review.
        corrupt = os.path.join(ROOT, f"state.corrupt.{fs()}.json")
        try:
            shutil.copy2(STATE_FILE, corrupt)
        except OSError:
            corrupt = STATE_FILE
        raise RuntimeError(
            f"state.json is unreadable; preserved as {corrupt}: {exc}"
        ) from exc


def save_state(s):
    ensure_dirs()
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    if os.path.isfile(STATE_FILE):
        try:
            shutil.copy2(STATE_FILE, STATE_PREVIOUS_FILE)
        except OSError:
            pass
    os.replace(tmp, STATE_FILE)


def load_control():
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            value = json.load(f)
        return {"paused": bool(value.get("paused", False)), "stop": bool(value.get("stop", False))}
    except (OSError, ValueError, TypeError):
        return {"paused": False, "stop": False}


def acquire_instance_lock():
    """Prevent two GUI/console launches from controlling one phone."""
    global INSTANCE_LOCK
    handle = open(LOCK_FILE, "a+b")
    if os.path.getsize(LOCK_FILE) == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    INSTANCE_LOCK = handle
    with open(PID_FILE, "w", encoding="ascii") as stream:
        stream.write(str(os.getpid()))

    def release():
        global INSTANCE_LOCK
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        if INSTANCE_LOCK is not None:
            try:
                INSTANCE_LOCK.seek(0)
                msvcrt.locking(INSTANCE_LOCK.fileno(), msvcrt.LK_UNLCK, 1)
                INSTANCE_LOCK.close()
            except OSError:
                pass
            INSTANCE_LOCK = None

    atexit.register(release)
    return True


def set_step(s, step):
    previous = s.get("step", "")
    s["step"] = step
    s["step_started_at"] = time.time()
    save_state(s)
    try:
        emit_event(
            "state_transition",
            phase=s.get("phase", ""),
            from_step=previous,
            to_step=step,
        )
    except Exception:
        pass
    log(f"STEP -> {step}")


def set_phase(s, phase, step):
    previous_phase = s.get("phase", "")
    previous_step = s.get("step", "")
    s["phase"] = phase
    s["step"] = step
    s["step_started_at"] = time.time()
    save_state(s)
    try:
        emit_event(
            "state_transition",
            from_phase=previous_phase,
            from_step=previous_step,
            to_phase=phase,
            to_step=step,
        )
    except Exception:
        pass
    log(f"PHASE -> {phase}; STEP -> {step}")


def _reset_tutorial_locks(state):
    state["skip_locked"] = False
    state["tutorial_hand_locked"] = False
    state["tutorial_primary_locked"] = False
    state["ocr_locked_action"] = ""
    state["ocr_locked_until"] = 0.0
    state["ocr_absent_since"] = 0.0
    state["ocr_upgrade_hold_ms"] = 0


def begin_tutorial(state, origin):
    """Enter tutorial with an explicit reason so completion is deterministic."""
    _reset_tutorial_locks(state)
    state["tutorial_origin"] = origin
    set_phase(state, "tutorial_new_character", "tutorial_intro")


def begin_next_character_cycle(state):
    """Enter the mandatory rename gate after a created character tutorial."""
    state["pending_nickname"] = int(state.get("next_nickname", 1))
    _reset_tutorial_locks(state)
    set_phase(state, "rename_governor", "governor_home")


def finish_tutorial(state):
    """Route tutorial completion according to the flow that started it."""
    origin = state.get("tutorial_origin", "new_character")
    if origin == "initial":
        log("Начальное обучение завершено: перехожу к созданию персонажа в государстве №3.")
        set_phase(state, "create_character", "home")
        return
    begin_next_character_cycle(state)


def perform_cycle_reset(state):
    """Clear only game app data and begin the next initial tutorial.

    PC-side state.json is intentionally preserved, so the nickname counter is
    never reset by pm clear.
    """
    backend = get_device_backend()
    log(
        "Цикл завершён: очищаю данные игры перед новым чистым запуском. "
        "Счётчик имён на ПК сохраняется."
    )
    backend.stop_app()
    result = backend.clear_app_data()
    if result.strip():
        log("pm clear: " + result.strip())
    state["current_cycle"] = int(state.get("current_cycle", 1)) + 1
    state["characters_created_cycle"] = 0
    state["last_stop_reason"] = ""
    save_state(state)
    begin_tutorial(state, "initial")
    backend.launch_app()
    log(f"Новый цикл #{state['current_cycle']}: игра запущена после очистки данных.")


def ensure_game_running():
    backend = get_device_backend()
    if hasattr(backend, "package_installed") and not backend.package_installed():
        raise RuntimeError(
            "Игра com.got.globalru не установлена. Сначала установите её через "
            "GUI или warbot_cli.py install-game."
        )
    health = backend.health()
    if not health.package_running:
        backend.launch_app()
        log("Игра была остановлена — запустил com.got.globalru.")


STOP_OCR_PHRASES = (
    "лимитперсонажей",
    "достигнутлимит",
    "нельзясоздатьперсонажа",
    "невозможносоздатьперсонажа",
    "слишкоммногоперсонажей",
    "попробуйтепозже",
    "слишкомчасто",
    "ограничениеаккаунта",
)


def detect_stop_reason(phone):
    """Recognise only stop conditions; never use OCR here to bypass them."""
    lines = ocr_lines(phone)
    normalized = "".join(line.get("normalized", "") for line in lines)
    for phrase in STOP_OCR_PHRASES:
        if phrase in normalized:
            return f"Сервер/аккаунт сообщил ограничение: {phrase}"
    return ""


def get_device_backend():
    """Return one device-scoped backend for the whole bot process."""
    global DEVICE_BACKEND
    if DEVICE_BACKEND is None:
        backend_name = "adb" if BACKEND_NAME == "scrcpy" else BACKEND_NAME
        serial = ANDROID_SERIAL or None
        DEVICE_BACKEND = create_backend(
            backend_name,
            serial=serial,
            adb_path=ADB or None,
        )
    return DEVICE_BACKEND


def create_capture():
    """Use direct Android frames by default; keep scrcpy only as diagnostics."""
    if BACKEND_NAME == "scrcpy":
        return ScrcpyCapture()
    return BackendCapture(get_device_backend())


def adb(args, capture=False):
    """Compatibility wrapper; all commands are scoped to the selected serial."""
    backend = get_device_backend()
    if not hasattr(backend, "run_adb"):
        raise BackendError(f"Backend {backend.backend_name} has no raw ADB channel")
    return backend.run_adb(args, check=False)


def adb_check():
    backend = get_device_backend()
    health = backend.health()
    if health.state != "device":
        raise RuntimeError(
            f"Android backend {health.backend} is not ready: "
            f"serial={health.serial} state={health.state}"
        )
    if health.boot_completed != "1":
        raise RuntimeError(
            f"Android {health.serial} is connected but boot is incomplete "
            f"(sys.boot_completed={health.boot_completed!r})."
        )
    if BACKEND_NAME in ("native", "native_arm64", "emulator") and not health.native_arm64:
        raise RuntimeError(
            "Native ARM64 gate failed: "
            f"abi={health.abi!r} abilist={health.abilist!r} "
            f"native_bridge={health.native_bridge!r}"
        )
    log(
        f"ANDROID OK: backend={health.backend} serial={health.serial} "
        f"android={health.android} abi={health.abi}"
    )


def tap(x, y):
    x, y = int(round(x)), int(round(y))
    if DRY_RUN:
        log(f"DRY RUN tap ({x},{y})")
        return
    get_device_backend().tap(x, y)
    log(f"Android tap phone=({x},{y})")


def hold(x, y, duration_ms):
    """Keep one UI control pressed; Android implements this as a stationary swipe."""
    x, y = int(round(x)), int(round(y))
    if DRY_RUN:
        log(f"DRY RUN hold ({x},{y}) for {duration_ms} ms")
        return
    get_device_backend().hold(x, y, duration_ms)
    log(f"Android hold phone=({x},{y}) duration={duration_ms} ms")


def tap_norm(nx, ny):
    tap(nx * INPUT_W, ny * INPUT_H)


def tap_client(phone, nx, ny):
    """Tap a normalized point in the current Android frame."""
    tap(nx * INPUT_W, ny * INPUT_H)


def type_tugarin_on_russian_keyboard(phone, number):
    """Type the required nickname on the visible Russian system keyboard."""
    for _ in range(16):
        tap_client(phone, 0.94, 0.897)
    tap_client(phone, 0.06, 0.897)
    keys = {
        "т": (0.57, 0.897), "у": (0.22, 0.781), "г": (0.57, 0.781),
        "а": (0.31, 0.843), "р": (0.48, 0.843), "и": (0.48, 0.897),
        "н": (0.48, 0.781),
    }
    for char in "тугарин":
        tap_client(phone, *keys[char])
    # Switch straight to the numeric layer. Do not press the space bar:
    # the required nickname is "Тугарин1", not "Тугарин 1".
    tap_client(phone, 0.06, 0.953)
    digit_x = {
        "1": 0.055, "2": 0.154, "3": 0.254, "4": 0.352, "5": 0.451,
        "6": 0.551, "7": 0.649, "8": 0.748, "9": 0.846, "0": 0.945,
    }
    for digit in str(int(number)):
        tap_client(phone, digit_x[digit], 0.781)


def key(code):
    if not DRY_RUN:
        get_device_backend().keyevent(code)


def text(value):
    if not DRY_RUN:
        get_device_backend().input_text(str(value))


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
        "governor_avatar.png", "governor_avatar_small.png", "governor_avatar_large.png", "profile_settings.png", "profile_settings_small.png", "profile_settings_large.png", "settings_characters.png", "settings_characters_small.png", "settings_characters_large.png",
        "governor_rename_button.png", "governor_rename_button_large.png",
        "governor_rename_dialog.png", "governor_rename_dialog_large.png",
        "governor_back.png",
        "create_plus.png", "select_kingdom_title.png", "state3_row.png", "state3_modal.png", "state3_confirm.png",
        "loading_logo.png", "task_scroll.png",
        "upgrade_button.png", "newbie_offer_context.png", "newbie_offer_close.png", "offline_confirm.png",
        "invasion_title.png", "tutorial_skip.png", "tutorial_skip_small.png", "tutorial_skip_core.png", "tutorial_hand_building.png",
        "tutorial_summon_button.png", "tutorial_hand_target.png", "tutorial_hand_roof.png",
        "tutorial_hand_housing.png", "tutorial_hand_residents.png",
        "tutorial_hand_bell.png",
        "tutorial_hand_recommend.png",
        "tutorial_hand_save_residents.png",
        "tutorial_hand_chest.png",
        "tutorial_hand_task_center.png",
        "tutorial_kitchen_title.png",
        "tutorial_return_city.png", "tutorial_return_city_small.png", "tutorial_cook_button.png",
        "tutorial_build_tower_button.png",
        "tutorial_claim_button.png", "tutorial_next_button.png",
        "tutorial_task_kitchen.png",
        "tutorial_battle_pause.png",
    ]
    missing = []
    bad = []
    for name in names:
        p = os.path.join(TPL, name)
        if not os.path.isfile(p):
            missing.append(name)
        elif tpl(name) is None:
            bad.append(name)
    if missing:
        raise RuntimeError("Отсутствуют обязательные PNG-шаблоны: " + ", ".join(missing))
    if bad:
        raise RuntimeError("Повреждены обязательные PNG-шаблоны: " + ", ".join(bad))

    log(f"Обязательные шаблоны проверены: {len(names)} позиций.")


OCR_ACTIONS = {
    "пропустить": ("tutorial_skip", "skip"),
    "далее": ("tutorial_continue", "continue"),
    "продолжить": ("tutorial_continue", "continue"),
    "нажмитечтобыпродолжить": ("tutorial_continue", "continue"),
    "призвать": ("tutorial_summon", "summon"),
    "вернутьсявгород": ("tutorial_return_city", "return_city"),
    "улучшить": ("tutorial_upgrade", "upgrade"),
}


def norm_text(value):
    return "".join(ch for ch in value.lower() if ch.isalnum())


def ocr_lines(phone):
    """Return confident OCR text lines with a bounding rectangle.

    The OCR fallback is deliberately narrow: it only supplies geometry for a
    whitelisted tutorial button. It never turns arbitrary on-screen text into
    a click target.
    """
    if not os.path.isfile(TESSERACT):
        return []
    enlarged = cv2.resize(phone, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    ok, raw = cv2.imencode(".png", enlarged)
    if not ok:
        return []
    env = os.environ.copy()
    env["TESSDATA_PREFIX"] = os.path.join(os.path.dirname(TESSERACT), "tessdata") + os.sep
    run = subprocess.run(
        [TESSERACT, "stdin", "stdout", "-l", "rus+eng", "--psm", "11", "tsv"],
        input=raw.tobytes(), capture_output=True, env=env, check=False,
        creationflags=WINDOWS_NO_WINDOW,
    )
    if run.returncode:
        log("OCR недоступен: " + run.stderr.decode("utf-8", "ignore").strip())
        return []
    groups = {}
    for row in csv.DictReader(io.StringIO(run.stdout.decode("utf-8", "ignore")), delimiter="\t"):
        text_value = (row.get("text") or "").strip()
        try:
            confidence = float(row.get("conf") or -1)
        except ValueError:
            confidence = -1
        if not text_value or confidence < 50:
            continue
        key = tuple(row.get(k, "") for k in ("block_num", "par_num", "line_num"))
        x, y, w, h = (int(row[k]) // 2 for k in ("left", "top", "width", "height"))
        groups.setdefault(key, []).append((text_value, x, y, w, h, confidence))
    lines = []
    for words in groups.values():
        text_value = " ".join(word[0] for word in words)
        left = min(word[1] for word in words)
        top = min(word[2] for word in words)
        right = max(word[1] + word[3] for word in words)
        bottom = max(word[2] + word[4] for word in words)
        lines.append({
            "text": text_value,
            "normalized": norm_text(text_value),
            "score": min(word[5] for word in words),
            "loc": (left, top), "w": right - left, "h": bottom - top,
        })
    return lines


def ocr_action(phone):
    for line in ocr_lines(phone):
        for phrase, (name, action) in OCR_ACTIONS.items():
            similarity = SequenceMatcher(None, phrase, line["normalized"]).ratio()
            # OCR may lose the first glyph on a rounded button (for example,
            # "ернуться в город"). Accept only a near-exact whitelist match.
            if phrase in line["normalized"] or similarity >= 0.88:
                line["name"] = name
                line["action"] = action
                return line
    return None


def ocr_action_is_safe(phone, target):
    """Accept OCR only in the fixed UI zone that belongs to that action."""
    height, width = phone.shape[:2]
    x, y = target["loc"]
    cx, cy = x + target["w"] / 2, y + target["h"] / 2
    action = target["action"]
    if action == "skip":
        return cx >= width * 0.52 and cy <= height * 0.26
    if action == "continue":
        # Only trust explicit continuation text in the lower dialogue region.
        # This avoids turning arbitrary OCR text into a blind Unity tap.
        return width * 0.12 <= cx <= width * 0.88 and cy >= height * 0.52
    if action == "upgrade":
        if not (width * 0.55 <= cx <= width * 0.97 and height * 0.42 <= cy <= height * 0.82):
            return False
        roi = phone[max(0, y - 8):min(height, y + target["h"] + 8),
                    max(0, x - 8):min(width, x + target["w"] + 8)]
        if roi.size == 0:
            return False
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        cyan = cv2.inRange(hsv, (75, 80, 80), (105, 255, 255))
        return cv2.countNonZero(cyan) >= roi.shape[0] * roi.shape[1] * 0.10
    # Other tutorial buttons use visual templates; do not trust OCR for them.
    return False


def handle_tutorial_ocr(phone, state):
    global LAST_OCR_AT
    if time.monotonic() - LAST_OCR_AT < OCR_INTERVAL:
        return False
    LAST_OCR_AT = time.monotonic()
    target = ocr_action(phone)
    now = time.time()
    if not target:
        if state.get("ocr_locked_action"):
            absent_since = float(state.get("ocr_absent_since", 0.0)) or now
            state["ocr_absent_since"] = absent_since
            if now >= float(state.get("ocr_locked_until", 0.0)) and now - absent_since >= OCR_ABSENCE_SECONDS:
                state["ocr_locked_action"] = ""
                state["ocr_locked_until"] = 0.0
                state["ocr_absent_since"] = 0.0
                state["ocr_upgrade_hold_ms"] = 0
            save_state(state)
            return "wait"
        return False
    action = target["action"]
    if not ocr_action_is_safe(phone, target):
        log(f"OCR tutorial отклонён вне безопасной зоны: действие={action}.")
        return False
    state["ocr_absent_since"] = 0.0
    if action == "skip" and state.get("skip_locked", False):
        return "wait"
    if state.get("ocr_locked_action") == action:
        # A prior bot version used a short tap for upgrades. Let the first run
        # after this change replace that persisted legacy lock with one hold,
        # then record the hold duration so this can never repeat on the same UI.
        if action != "upgrade" or state.get("ocr_upgrade_hold_ms") == UPGRADE_HOLD_MS:
            return "wait"
    debug(phone, target, "ocr_" + target["name"])
    log(f"OCR tutorial: «{target['text']}» ({target['score']:.0f}); действие={action}.")
    if action == "upgrade":
        hold_match(phone, target, UPGRADE_HOLD_MS)
    else:
        tap_match(phone, target)
    if action == "skip":
        state["skip_locked"] = True
    elif action == "continue":
        pass
    elif action == "summon":
        set_step(state, "tutorial_wait_summon")
    elif action == "return_city":
        set_step(state, "tutorial_wait_city")
    elif action == "upgrade":
        set_step(state, "tutorial_wait_scroll")
    state["ocr_locked_action"] = action
    state["ocr_locked_until"] = now + OCR_LOCK_SECONDS
    state["ocr_absent_since"] = 0.0
    if action == "upgrade":
        state["ocr_upgrade_hold_ms"] = UPGRADE_HOLD_MS
    save_state(state)
    return "held" if action == "upgrade" else "acted"


MATCH_FALLBACK_SCALES = (0.72, 0.80, 0.88, 0.94, 1.06, 1.12, 1.18, 1.25, 1.32)


def match(phone, image, threshold, allow_scale=True):
    if image is None:
        return None
    h, w = image.shape[:2]
    fits_native = h <= phone.shape[0] and w <= phone.shape[1]
    if not fits_native and not allow_scale:
        return None
    if fits_native:
        result = cv2.matchTemplate(phone, image, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        best = {"score": float(score), "loc": loc, "w": w, "h": h}
    else:
        score = -1.0
        best = {"score": -1.0, "loc": (0, 0), "w": w, "h": h}
    if score >= threshold or not allow_scale:
        return best if score >= threshold else None

    # Templates are captured from scrcpy. The phone coordinate system stays
    # fixed while the desktop window can be resized, so retry only after the
    # native-size match misses. This keeps the common path fast.
    for scale in MATCH_FALLBACK_SCALES:
        resized = cv2.resize(
            image, None, fx=scale, fy=scale,
            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
        )
        rh, rw = resized.shape[:2]
        if rh > phone.shape[0] or rw > phone.shape[1]:
            continue
        candidate = cv2.matchTemplate(phone, resized, cv2.TM_CCOEFF_NORMED)
        _, candidate_score, _, candidate_loc = cv2.minMaxLoc(candidate)
        if candidate_score > best["score"]:
            best = {"score": float(candidate_score), "loc": candidate_loc, "w": rw, "h": rh}
    return best if best["score"] >= threshold else None


def match_tutorial_skip(phone):
    """Find the common Skip control only where the game draws it.

    Restricting the search to the upper-right dialog area makes this both
    faster and safer than scanning the whole scene. The video window may be
    resized, so test a small range of template scales.
    """
    height, width = phone.shape[:2]
    left = round(width * 0.52)
    bottom = round(height * 0.26)
    roi = phone[:bottom, left:]
    best = None
    for name in ("tutorial_skip.png", "tutorial_skip_small.png", "tutorial_skip_core.png"):
        image = tpl(name)
        if image is None:
            continue
        for scale in (0.76, 0.80, 0.82, 0.88, 0.94, 1.0, 1.06, 1.12):
            resized = cv2.resize(
                image, None, fx=scale, fy=scale,
                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
            )
            hit = match(roi, resized, 0.72, allow_scale=False)
            if hit and (best is None or hit["score"] > best["score"]):
                hit["loc"] = (hit["loc"][0] + left, hit["loc"][1])
                best = hit
    return best


def match_tutorial_hand(phone):
    """Locate the shared animated tutorial hand anywhere in the game area."""
    best = None
    variants = (
        ("tutorial_hand_building.png", (0.25, 0.93), 0.68),
        ("tutorial_hand_target.png", (0.46, 0.76), 0.70),
        ("tutorial_hand_roof.png", (0.43, 0.53), 0.70),
        ("tutorial_hand_housing.png", (0.57, 0.74), 0.70),
        ("tutorial_hand_residents.png", (0.40, 0.73), 0.70),
        ("tutorial_hand_bell.png", (0.50, 0.37), 0.70),
        ("tutorial_hand_recommend.png", (0.39, 0.80), 0.70),
        ("tutorial_hand_save_residents.png", (0.25, 0.76), 0.70),
        ("tutorial_hand_chest.png", (0.45, 0.35), 0.70),
        ("tutorial_hand_task_center.png", (0.18, 0.48), 0.62),
    )
    for name, target, threshold in variants:
        image = tpl(name)
        if image is None:
            continue
        for scale in (0.72, 0.80, 0.88, 0.94, 1.0, 1.06, 1.12, 1.18, 1.25, 1.32):
            resized = cv2.resize(
                image, None, fx=scale, fy=scale,
                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
            )
            hit = match(phone, resized, threshold, allow_scale=False)
            if hit and not tutorial_target_is_lit(phone, hit, target):
                continue
            if hit and (best is None or hit["score"] > best["score"]):
                hit["target"] = target
                hit["variant"] = name
                best = hit
    return best


def tutorial_target_is_lit(phone, hit, target):
    """Reject a hand-shaped false match unless its target circle is glowing."""
    cx = round(hit["loc"][0] + hit["w"] * target[0])
    cy = round(hit["loc"][1] + hit["h"] * target[1])
    radius = max(8, round(min(hit["w"], hit["h"]) * 0.11))
    y0, y1 = max(0, cy - radius), min(phone.shape[0], cy + radius + 1)
    x0, x1 = max(0, cx - radius), min(phone.shape[1], cx + radius + 1)
    if y0 >= y1 or x0 >= x1:
        return False
    hsv = cv2.cvtColor(phone[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    lit = cv2.inRange(hsv, (15, 60, 100), (100, 255, 255))
    return float(np.count_nonzero(lit)) / float(lit.size) >= 0.12


def find_tutorial_primary_button(phone):
    """Find the large turquoise primary action in a construction panel."""
    height, width = phone.shape[:2]
    top = round(height * 0.65)
    roi = phone[top:]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (75, 80, 80), (105, 255, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < width * 0.25 or h < 28 or w / h < 2.0:
            continue
        candidates.append({"loc": (x, y + top), "w": w, "h": h, "score": float(cv2.contourArea(contour))})
    return max(candidates, key=lambda item: item["score"], default=None)


def is_construction_panel(phone):
    kitchen = match(phone, tpl("tutorial_kitchen_title.png"), 0.93)
    if kitchen:
        return True
    lines = ocr_lines(phone)
    normalized = " ".join(line["normalized"] for line in lines)
    # Grey section headings are less legible to OCR than the building title.
    # The large cyan button is already required, and this rule runs only in
    # the tutorial state machine.
    return "кухня" in normalized or "требуется" in normalized


def tap_match(phone, hit):
    x, y = hit["loc"]
    cx = x + hit["w"] / 2
    cy = y + hit["h"] / 2
    tap(cx * INPUT_W / phone.shape[1], cy * INPUT_H / phone.shape[0])


def hold_match(phone, hit, duration_ms):
    x, y = hit["loc"]
    cx = x + hit["w"] / 2
    cy = y + hit["h"] / 2
    hold(cx * INPUT_W / phone.shape[1], cy * INPUT_H / phone.shape[0], duration_ms)


def tap_match_relative(phone, hit, rel_x, rel_y):
    """Tap a known point inside a context template, not its visual centre."""
    x = hit["loc"][0] + hit["w"] * rel_x
    y = hit["loc"][1] + hit["h"] * rel_y
    tap(x * INPUT_W / phone.shape[1], y * INPUT_H / phone.shape[0])


def debug(phone, hit, name):
    x, y = hit["loc"]
    out = phone.copy()
    cv2.rectangle(out, (x, y), (x+hit["w"], y+hit["h"]), (0,255,0), 3)
    save_img(os.path.join(DEBUG_DIR, f"{fs()}_{name}_{hit['score']:.3f}.png"), out)


def sync_input_geometry(frame):
    """Use the real Android framebuffer size for input coordinates.

    Template matching always runs at VISION_W x VISION_H, but ADB input must
    target the guest's actual framebuffer. Legacy scrcpy still uses the
    physical device constants because its desktop window can be arbitrarily
    resized/letterboxed.
    """
    global INPUT_W, INPUT_H
    if BACKEND_NAME == "scrcpy":
        INPUT_W, INPUT_H = PHONE_W, PHONE_H
        return
    height, width = frame.shape[:2]
    if width >= 100 and height >= 100:
        INPUT_W, INPUT_H = width, height


def crop_phone(frame):
    sh, sw = frame.shape[:2]
    # SDL may letterbox the stream when scrcpy is resized freely. Crop to the
    # device aspect ratio, then normalize vision to one stable resolution.
    target = PHONE_W / PHONE_H
    actual = sw / sh
    if actual > target:
        content_w = round(sh * target)
        left = max(0, (sw - content_w) // 2)
        phone = frame[:, left:left + content_w]
    else:
        content_h = round(sw / target)
        top = max(0, (sh - content_h) // 2)
        phone = frame[top:top + content_h, :]
        left = 0
    if phone.size == 0:
        raise RuntimeError(f"Неверный crop Android frame: {sw}x{sh}")
    normalized = cv2.resize(phone, (VISION_W, VISION_H), interpolation=cv2.INTER_AREA)
    return normalized, left, left + phone.shape[1]


def stream_ok(frame, left, right):
    # scrcpy client pixels have no HONOR Suite side panels. A mostly black
    # frame is still a valid game scene, so only reject an empty capture.
    return frame.size > 0 and float(np.std(frame)) > 1.0


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
        close = match(phone, tpl("newbie_offer_close.png"), 0.90)
        if not close:
            return False
        debug(phone, hit, "newbie_offer")
        debug(phone, close, "newbie_offer_close")
        log(
            f"Контекстно найден «Ценный набор новичка» {hit['score']:.3f}; "
            "закрываю только по его конкретному X-шаблону."
        )
        tap_match(phone, close)
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
        if not hit and step == "home":
            hit = match(phone, tpl("governor_avatar_small.png"), 0.94)
        if not hit and step == "profile":
            hit = match(phone, tpl("profile_settings_small.png"), 0.94)
        if not hit and step == "profile":
            hit = match(phone, tpl("profile_settings_large.png"), 0.94)
        if not hit and step == "settings":
            hit = match(phone, tpl("settings_characters_small.png"), 0.94)
        if not hit and step == "settings":
            hit = match(phone, tpl("settings_characters_large.png"), 0.94)
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
        log("Найдена точная строка «Государство №3»; подтверждаю ввод клавиатуры.")
        tap_norm(0.90, 0.657)
        set_step(state, "kingdom_results_ready")
        return True

    if step == "kingdom_results_ready":
        exact = match(phone, tpl("state3_row.png"), 0.88)
        if not exact:
            return False
        debug(phone, exact, "state3_row_ready")
        log("Клавиатура закрыта; выбираю точно «Государство №3».")
        tap_match(phone, exact)
        set_step(state, "state_confirm")
        return True

    if step == "state_confirm":
        modal = match(phone, tpl("state3_modal.png"), 0.90)
        if not modal:
            return False
        confirm = match(phone, tpl("state3_confirm.png"), 0.91)
        if not confirm:
            return False

        debug(phone, modal, "state3_modal")
        debug(phone, confirm, "state3_confirm")
        log(
            "Подтверждены и диалог «Государство №3», и его конкретная кнопка "
            "подтверждения. Создаю персонажа."
        )
        tap_match(phone, confirm)
        begin_tutorial(state, "new_character")
        return True

    return False


def handle_rename_governor(phone, state):
    """Rename before opening the character-management branch of a cycle."""
    if state["step"] == "governor_home":
        back = match(phone, tpl("governor_back.png"), 0.90)
        if back:
            log("Обязательное обучение уже закончено; закрываю необязательную панель.")
            tap_match(phone, back)
            return True
        blocking_hand = match_tutorial_hand(phone)
        if blocking_hand:
            log("Обучение закончено; убираю оставшийся указатель, который блокирует меню.")
            tap_match_relative(phone, blocking_hand, *blocking_hand["target"])
            return True
        hit = match(phone, tpl("governor_avatar_large.png"), 0.94)
        if not hit:
            return False
        debug(phone, hit, "governor_avatar_large")
        log("Возвращаюсь в профиль губернатора для обязательного переименования.")
        tap_match(phone, hit)
        set_step(state, "governor_profile")
        return True

    if state["step"] == "governor_profile":
        hit = match(phone, tpl("governor_rename_button.png"), 0.94)
        if not hit:
            hit = match(phone, tpl("governor_rename_button_large.png"), 0.94)
        if not hit:
            # A camera movement is not proof that the avatar opened. Recover
            # to the verified home step and retry after clearing UI blockers.
            avatar = match(phone, tpl("governor_avatar_large.png"), 0.90)
            blocking_hand = match_tutorial_hand(phone)
            if avatar or blocking_hand:
                log("Профиль не открылся; возвращаюсь к проверенному шагу меню губернатора.")
                set_step(state, "governor_home")
            return False
        debug(phone, hit, "governor_rename_button")
        log(f"Цикл {state.get('next_nickname', 1)}: открываю переименование губернатора.")
        tap_match(phone, hit)
        set_step(state, "rename_dialog")
        return True

    if state["step"] == "rename_dialog":
        hit = match(phone, tpl("governor_rename_dialog.png"), 0.94)
        if not hit:
            hit = match(phone, tpl("governor_rename_dialog_large.png"), 0.94)
        if not hit:
            return False
        nickname = f"Тугарин{int(state.get('pending_nickname', state.get('next_nickname', 1)))}"
        debug(phone, hit, "governor_rename_dialog")
        log(f"Переименовываю губернатора в «{nickname}».")
        tap_match_relative(phone, hit, 0.50, 0.30)
        time.sleep(0.25)
        key(123)
        for _ in range(16):
            key(67)
        type_tugarin_on_russian_keyboard(phone, int(state.get("pending_nickname", state.get("next_nickname", 1))))
        time.sleep(0.4)
        # Commit the active keyboard composition first, then press Apply.
        tap_norm(0.90, 0.657)
        time.sleep(0.35)
        tap_match_relative(phone, hit, 0.50, 0.78)
        set_step(state, "rename_verify")
        return True

    if state["step"] == "rename_verify":
        # The modal must be gone before progressing to character creation.
        modal = match(phone, tpl("governor_rename_dialog.png"), 0.94)
        if not modal:
            modal = match(phone, tpl("governor_rename_dialog_large.png"), 0.94)
        if modal:
            return False
        state["next_nickname"] = int(state.get("pending_nickname", 1)) + 1
        state["characters_created"] = int(state.get("characters_created", 0)) + 1
        state["characters_created_cycle"] = int(state.get("characters_created_cycle", 0)) + 1
        limit = max(1, int(state.get("characters_per_cycle", 4)))
        if state.get("auto_reset_data", False) and state["characters_created_cycle"] >= limit:
            if state.get("repeat_cycles", True):
                log(
                    f"В текущем цикле создано {state['characters_created_cycle']} из {limit}; "
                    "планирую безопасный reset данных игры."
                )
                set_phase(state, "reset_cycle", "clear_data")
            else:
                log(
                    f"Цель цикла достигнута: {state['characters_created_cycle']} из {limit}. "
                    "Повтор циклов выключен — останавливаюсь."
                )
                set_phase(state, "complete", "done")
        else:
            set_phase(state, "create_character", "profile")
        return False

    return False


def handle_tutorial(phone, state):
    step = state["step"]

    # Battles after summoning run automatically. The pause badge is a stable
    # scene marker; no tutorial control is actionable until it disappears.
    if step == "tutorial_wait_summon":
        battle = match(phone, tpl("tutorial_battle_pause.png"), 0.90)
        if battle:
            log("Туториал: идёт автоматическая боевая сцена, жду её окончания.")
            return "wait"

    # Completion is a state transition, not a terminal stop: it starts the
    # rename/create/tutorial cycle for the next character.
    if step in ("tutorial_wait_city", "tutorial_wait_hand_result", "tutorial_wait_scroll"):
        for name, threshold in (
            ("governor_avatar.png", 0.91),
            ("governor_avatar_small.png", 0.94),
            ("governor_avatar_large.png", 0.94),
        ):
            governor = match(phone, tpl(name), threshold)
            if governor:
                log("Туториал завершён: запускаю обязательное переименование перед новым персонажем.")
                finish_tutorial(state)
                return "wait"

    # A visible hand is the tutorial's exclusive input contract: the game
    # disables every other control. It must therefore outrank Skip, OCR, and
    # colour-based buttons; clicking anything else only wastes an action.
    hand_target = match_tutorial_hand(phone)
    if hand_target:
        if not state.get("tutorial_hand_locked", False):
            debug(phone, hand_target, "tutorial_hand_target")
            log(f"Туториал: рука-указатель ({hand_target['variant']}) — нажимаю только указанную цель.")
            tap_match_relative(phone, hand_target, *hand_target["target"])
            state["tutorial_hand_locked"] = True
            set_step(state, "tutorial_wait_hand_result")
            return "acted"
        if time.time() - float(state.get("step_started_at", 0.0)) >= 3.0:
            log("Туториал: указатель остался после действия; повторяю точную светящуюся цель.")
            state["tutorial_hand_locked"] = False
            save_state(state)
        return "wait"

    # Skip is common to tutorial dialogs, so it takes precedence over every
    # scene-specific state. A continuously visible instance is clicked once.
    skip = match_tutorial_skip(phone)
    if skip:
        if not state.get("skip_locked", False) or state.get("skip_lock_version") != 2:
            debug(phone, skip, "tutorial_skip")
            log("Туториал: найден новый эпизод «Пропустить». Пропускаю один раз.")
            tap_match(phone, skip)
            state["skip_locked"] = True
            state["skip_lock_version"] = 2
            save_state(state)
            return "acted"
        return "wait"
    if state.get("skip_locked", False):
        state["skip_locked"] = False
        save_state(state)
        log("Туториал: «Пропустить» исчезло; следующий эпизод снова может быть обработан.")

    if state.get("tutorial_hand_locked", False):
        state["tutorial_hand_locked"] = False
        save_state(state)
        log("Туториал: рука-указатель исчезла; следующий маркер снова может быть обработан.")

    primary = find_tutorial_primary_button(phone)
    if primary and is_construction_panel(phone):
        if not state.get("tutorial_primary_locked", False):
            debug(phone, primary, "tutorial_primary_button")
            log("Туториал: подтверждена панель строительства; нажимаю её основную кнопку.")
            tap_match(phone, primary)
            state["tutorial_primary_locked"] = True
            set_step(state, "tutorial_wait_construction")
            return "acted"
        return "wait"
    if state.get("tutorial_primary_locked", False):
        state["tutorial_primary_locked"] = False
        save_state(state)
        log("Туториал: основная кнопка строительства исчезла; действие снова разблокировано.")

    cook = match(phone, tpl("tutorial_cook_button.png"), 0.94)
    if cook:
        debug(phone, cook, "tutorial_cook_button")
        log("Туториал: выбор еды подтверждён; нажимаю «Приготовить».")
        tap_match(phone, cook)
        set_step(state, "tutorial_wait_cooking")
        return "acted"

    claim = match(phone, tpl("tutorial_claim_button.png"), 0.94)
    if claim:
        debug(phone, claim, "tutorial_claim_button")
        log("Туториал: забираю подтверждённую награду главы.")
        tap_match(phone, claim)
        set_step(state, "tutorial_wait_claim")
        return "acted"

    next_button = match(phone, tpl("tutorial_next_button.png"), 0.94)
    if next_button:
        debug(phone, next_button, "tutorial_next_button")
        log("Туториал: все доступные награды обработаны; нажимаю «Вперёд».")
        tap_match(phone, next_button)
        set_step(state, "tutorial_wait_next_mission")
        return "acted"

    tower = match(phone, tpl("tutorial_build_tower_button.png"), 0.94)
    if tower:
        debug(phone, tower, "tutorial_build_tower_button")
        log("Туториал: выбран рекомендованный тип башни; нажимаю «Построить».")
        tap_match(phone, tower)
        set_step(state, "tutorial_wait_tower")
        return "acted"

    kitchen_task = match(phone, tpl("tutorial_task_kitchen.png"), 0.93)
    if kitchen_task:
        debug(phone, kitchen_task, "tutorial_task_kitchen")
        log("Туториал: открываю подтверждённую задачу «Постройте: Кухня».")
        tap_match(phone, kitchen_task)
        set_step(state, "tutorial_wait_kitchen_task")
        return "acted"

    summon = match(phone, tpl("tutorial_summon_button.png"), 0.94)
    if summon:
        debug(phone, summon, "tutorial_summon_button")
        log("Туториал: роль уже выбрана, нажимаю подтверждённое «Призвать».")
        tap_match(phone, summon)
        set_step(state, "tutorial_wait_summon")
        return "acted"

    return_city = match(phone, tpl("tutorial_return_city.png"), 0.94)
    if not return_city:
        return_city = match(phone, tpl("tutorial_return_city_small.png"), 0.94)
    if return_city:
        debug(phone, return_city, "tutorial_return_city")
        log("Туториал: победа подтверждена, возвращаюсь в город.")
        tap_match(phone, return_city)
        set_step(state, "tutorial_wait_city")
        return "acted"

    # Immediately after creating a character the game first shows its loading
    # screen and then a skippable intro/cinematic.  Do not look for the task
    # scroll before that intro is gone.
    if step == "tutorial_intro":
        loading = match(phone, tpl("loading_logo.png"), 0.82)
        if loading:
            log("Туториал: загрузочный экран, жду.")
            return "wait"

        # Dialogue screens can appear before/after Skip. We do not ship a
        # guessed generic dialogue image; the fallback below only accepts
        # explicit "Далее/Продолжить" OCR text in the safe lower dialogue zone.
        fallback = handle_tutorial_ocr(phone, state)
        return fallback if fallback else False

    if step in ("tutorial_scroll", "tutorial_wait_scroll"):
        # If the governor avatar becomes available, the mandatory tutorial has
        # reached the point required by the workflow.
        governor = match(phone, tpl("governor_avatar.png"), 0.91)
        if governor:
            log("Туториал: меню губернатора доступно — обязательная часть завершена.")
            finish_tutorial(state)
            return "wait"

        # Text-based dialogue continuation is handled by the safe OCR
        # fallback below if none of the stronger tutorial markers match.
        # The first real tutorial task is a hand pointing to the shelter, not
        # the later task scroll.  Its crop comes from the live scrcpy frame.
        hand = match(phone, tpl("tutorial_hand_building.png"), 0.94)
        if hand:
            debug(phone, hand, "tutorial_hand_building")
            log("Туториал: подтверждён указатель на первое здание; нажимаю круг подсказки.")
            # The actionable circle is at the bottom-left of this contextual
            # hand crop. The centre is only the hand, not the target.
            tap_match_relative(phone, hand, 0.25, 0.93)
            set_step(state, "tutorial_building")
            return "acted"

        hit = match(phone, tpl("task_scroll.png"), 0.88)
        if not hit:
            fallback = handle_tutorial_ocr(phone, state)
            return fallback if fallback else False
        debug(phone, hit, "task_scroll")
        log("Туториал: найден свиток задания. Нажимаю свиток.")
        tap_match(phone, hit)
        set_step(state, "tutorial_building")
        return "acted"

    if step == "tutorial_building":
        hit = match(phone, tpl("upgrade_button.png"), 0.90)
        if not hit:
            fallback = handle_tutorial_ocr(phone, state)
            return fallback if fallback else False
        debug(phone, hit, "upgrade_button")
        log("Туториал: найдено «Улучшить». Удерживаю до завершения улучшений.")
        hold_match(phone, hit, UPGRADE_HOLD_MS)
        set_step(state, "tutorial_wait_scroll")
        return "held"

    fallback = handle_tutorial_ocr(phone, state)
    return fallback if fallback else False


def save_scrcpy_capture():
    capture = ScrcpyCapture()
    try:
        frame, title, rect = capture.grab()
        out = os.path.join(DEBUG_DIR, f"scrcpy_capture_{fs()}.png")
        save_img(out, frame)
        print(out)
        log(f"scrcpy capture: {title} {rect['width']}x{rect['height']} -> {out}")
    finally:
        capture.close()


def handle_capture_failure(recovery, state, error):
    """Apply one bounded recovery decision after a capture/ADB failure."""
    try:
        decision = recovery.capture_failed(get_device_backend(), str(error))
    except Exception as recovery_error:
        decision_detail = f"Recovery probe failed: {recovery_error}"
        state["last_stop_reason"] = decision_detail
        save_state(state)
        log("STOP: " + decision_detail)
        try:
            emit_event("recovery", action="stop", terminal=True, detail=decision_detail)
        except Exception:
            pass
        return False

    try:
        emit_event(
            "recovery",
            action=decision.action,
            terminal=decision.terminal,
            detail=decision.detail,
        )
    except Exception:
        pass
    log(f"Recovery: {decision.action}: {decision.detail}")
    if decision.terminal:
        state["last_stop_reason"] = decision.detail
        save_state(state)
        return False
    return True


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

    if "--ocr-file" in sys.argv:
        index = sys.argv.index("--ocr-file") + 1
        if index >= len(sys.argv):
            raise RuntimeError("После --ocr-file укажи PNG-файл.")
        image = cv2.imread(sys.argv[index])
        if image is None:
            raise RuntimeError("Не удалось прочитать OCR PNG: " + sys.argv[index])
        print(json.dumps(ocr_lines(image), ensure_ascii=False, indent=2))
        return

    if "--capture-scrcpy" in sys.argv:
        save_scrcpy_capture()
        return

    if not acquire_instance_lock():
        log("Бот уже запущен; второй экземпляр не создан.")
        return

    state = load_state()
    validate_templates()
    log("="*70)
    log(f"TUGARIN BOTS v4 | phase={state['phase']} step={state['step']}")
    adb_check()
    ensure_game_running()

    if BACKEND_NAME == "scrcpy":
        print("Legacy diagnostics: откройте ровно одно окно scrcpy.")
    else:
        print(f"Android backend: {BACKEND_NAME} / {get_device_backend().serial}")
    print("F8 — аварийная остановка.")
    for i in range(START_DELAY, 0, -1):
        print(f"Старт через {i}...")
        time.sleep(1)

    gate = ActionGate()
    last_unknown = None
    last_unknown_at = 0.0
    unknown_since = None
    warn_at = 0.0
    capture = None
    pause_reported = False
    recovery = RecoveryController()

    try:
        while True:
            if emergency():
                log("F8: остановка.")
                break

            control = load_control()
            if control["stop"]:
                log("Получена команда остановки из GUI.")
                break
            if control["paused"]:
                if not pause_reported:
                    log("GUI: бот поставлен на паузу.")
                    pause_reported = True
                time.sleep(0.5)
                continue
            if pause_reported:
                log("GUI: работа продолжена.")
                pause_reported = False

            if state.get("phase") == "complete":
                log("Работа завершена по настройке цикла.")
                break

            if state.get("phase") == "reset_cycle":
                if capture is not None:
                    try:
                        capture.close()
                    except Exception:
                        pass
                    capture = None
                perform_cycle_reset(state)
                gate = ActionGate()
                unknown_since = None
                time.sleep(2.0)
                continue

            if capture is None:
                try:
                    capture = create_capture()
                    frame, title, rect = capture.grab()
                    log(f"Захват Android: {title} {rect['width']}x{rect['height']}")
                except Exception as e:
                    log(f"Захват Android не открылся: {e}.")
                    if capture is not None:
                        capture.close()
                    capture = None
                    if not handle_capture_failure(recovery, state, e):
                        break
                    time.sleep(2)
                    continue

            try:
                frame, _, _ = capture.grab()
                recovery.capture_succeeded()
            except Exception as e:
                log(f"Захват Android: {e}. Пересоздаю захват.")
                capture.close()
                capture = None
                if not handle_capture_failure(recovery, state, e):
                    break
                time.sleep(2)
                continue
            sync_input_geometry(frame)
            phone, left, right = crop_phone(frame)

            if not stream_ok(frame, left, right):
                if time.time() - warn_at > 5:
                    log("Android backend вернул пустой кадр — пауза.")
                    warn_at = time.time()
                unknown_since = None
                time.sleep(0.7)
                continue

            gate_status = gate.observe(phone)
            if gate_status == "changed" and state.get("skip_locked", False):
                state["skip_locked"] = False
                state["skip_lock_version"] = 2
                save_state(state)
                log("Туториал: кадр сменился после «Пропустить»; новый эпизод снова может быть обработан.")
                gate_status = "ready"
            if gate_status == "timeout":
                out = os.path.join(DEBUG_DIR, f"action_timeout_{fs()}_{state['phase']}_{state['step']}.png")
                save_img(out, phone)
                log(f"ТАЙМАУТ: после {gate.label} кадр не изменился за {ACTION_TIMEOUT:.1f} сек. Остановка: {out}")
                break
            if gate_status == "waiting":
                time.sleep(LOOP_DELAY)
                continue

            acted = False

            if handle_overlay(phone):
                acted = True
            elif state["phase"] == "rename_governor":
                acted = handle_rename_governor(phone, state)
            elif state["phase"] == "create_character":
                acted = handle_create_step(phone, state)
            elif state["phase"] == "tutorial_new_character":
                tutorial_result = handle_tutorial(phone, state)
                if tutorial_result == "acted":
                    acted = True
                elif tutorial_result == "held":
                    # A hold is already a complete action. Do not require a
                    # visual transition after it: the game may leave the same
                    # construction panel open while the next hand appears.
                    unknown_since = None
                    continue
                elif tutorial_result == "wait":
                    unknown_since = None
                    time.sleep(1.5)
                    continue

            if acted:
                gate.arm(phone, f"{state['phase']}/{state['step']}")
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

            if now - unknown_since >= WATCHDOG_SECONDS:
                out = os.path.join(
                    UNKNOWN_DIR,
                    f"watchdog_{fs()}_{state['phase']}_{state['step']}.png"
                )
                save_img(out, phone)
                reason = detect_stop_reason(phone)
                if not reason:
                    reason = (
                        f"Экран не распознан {WATCHDOG_SECONDS:.0f} сек.; "
                        "остановка без слепых нажатий."
                    )
                state["last_stop_reason"] = reason
                save_state(state)
                log(f"STOP: {reason} Screenshot: {out}")
                break

            time.sleep(LOOP_DELAY)

    finally:
        if capture is not None:
            try:
                capture.close()
            except Exception:
                pass

    log("TUGARIN BOTS остановлен.")


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
