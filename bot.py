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
import hashlib
import msvcrt
from difflib import SequenceMatcher
from datetime import datetime

import cv2
import mss
import numpy as np

from device_backend import BackendCapture, BackendError, create_backend
from frame_stream import create_preview_capture
from runtime_events import emit_event
from runtime_recovery import RecoveryController
from runtime_watchdog import RuntimeHeartbeat
from tutorial_vision import BoundedActionPolicy, Box, TutorialPerception
from task_engine import SemanticTaskEngine
from resource_diagnostics import (
    collect_resource_network_diagnostics, save_resource_network_diagnostics,
)


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
LIVE_FRAME_FILE = os.path.join(DEBUG_DIR, "bot-live-frame.jpg")
LIVE_FRAME_META_FILE = os.path.join(DEBUG_DIR, "bot-live-frame.json")
INSTANCE_LOCK = None

PHONE_W = 1060
PHONE_H = 2376
INPUT_W = PHONE_W
INPUT_H = PHONE_H
INPUT_CONTENT_LEFT = 0.0
INPUT_CONTENT_TOP = 0.0
INPUT_CONTENT_W = float(PHONE_W)
INPUT_CONTENT_H = float(PHONE_H)
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
LAST_LEGACY_TUTORIAL_SCAN_AT = 0.0
WATCHDOG_SECONDS = 75.0
GOVERNOR_CONFIRM_SECONDS = 1.5
RUNTIME_PROBE_INTERVAL = 15.0

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
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
            time.sleep(0.20)
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


class WsaGameWindowCapture(ScrcpyCapture):
    """Capture the exact Kingshot WSA HWND through Win32 PrintWindow."""

    transport_name = "wsa-window"
    capture_method = "printwindow"
    TITLE_MARKERS = ("война за трон", "kingshot")

    @classmethod
    def _is_game_window_title(cls, title):
        """Accept only the app window title, never a browser/document mention."""
        folded = " ".join(str(title or "").split()).casefold()
        return folded in cls.TITLE_MARKERS

    def __init__(self):
        # This transport never samples desktop pixels, so it must not allocate
        # an MSS screen-grabber. Keeping it HWND-only makes overlap safety
        # explicit: another desktop window cannot become the source frame.
        self.hwnd = None
        self.rect = None

    def close(self):
        # Every GDI object is scoped to one grab() and released in its finally
        # block. There is no persistent native capture object to close.
        return None

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
            if WsaGameWindowCapture._is_game_window_title(title.value):
                found.append((hwnd, title.value))
            return True

        user32.EnumWindows(enum_proc(visit), 0)
        return found

    def _refresh_rect(self):
        candidates = self._visible_scrcpy_windows()
        if len(candidates) != 1:
            titles = ", ".join(title for _, title in candidates) or "нет"
            raise RuntimeError(
                "Ожидается одно видимое окно Kingshot в текущей Windows-сессии; "
                "найдено: " + titles
            )
        self.hwnd, title = candidates[0]
        # WSA can leave the exact game HWND minimized after a prior launcher
        # or diagnostics run. Restore that same HWND before PrintWindow; never
        # substitute desktop pixels or a different capture transport.
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
            time.sleep(0.20)
        rect = wintypes.RECT()
        point = wintypes.POINT(0, 0)
        if not user32.GetClientRect(self.hwnd, ctypes.byref(rect)):
            raise RuntimeError("Не удалось прочитать клиентскую область окна WSA.")
        if not user32.ClientToScreen(self.hwnd, ctypes.byref(point)):
            raise RuntimeError("Не удалось определить положение окна WSA.")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 100 or height < 100:
            raise RuntimeError("Окно Kingshot слишком мало или свёрнуто.")
        self.rect = {"left": point.x, "top": point.y, "width": width, "height": height}
        return title

    def _capture_client(self, width, height, *, user32_api=None, gdi32_api=None):
        """Return one BGR client frame and release every GDI handle on all paths."""
        user32_api = user32_api or user32
        gdi32_api = gdi32_api or ctypes.windll.gdi32
        window_dc = 0
        memory_dc = 0
        bitmap = 0
        old_bitmap = 0

        class BitmapInfoHeader(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        try:
            window_dc = user32_api.GetDC(self.hwnd)
            if not window_dc:
                raise RuntimeError("GetDC не смог открыть окно WSA.")
            memory_dc = gdi32_api.CreateCompatibleDC(window_dc)
            if not memory_dc:
                raise RuntimeError("CreateCompatibleDC не смог создать GDI context.")
            bitmap = gdi32_api.CreateCompatibleBitmap(window_dc, width, height)
            if not bitmap:
                raise RuntimeError("CreateCompatibleBitmap не смог создать кадр WSA.")
            old_bitmap = gdi32_api.SelectObject(memory_dc, bitmap)
            if not old_bitmap or int(old_bitmap) == -1:
                raise RuntimeError("SelectObject не смог выбрать bitmap WSA.")

            # PW_CLIENTONLY | PW_RENDERFULLCONTENT renders the target HWND
            # itself rather than the desktop rectangle above or below it.
            if not user32_api.PrintWindow(self.hwnd, memory_dc, 0x00000003):
                raise RuntimeError("PrintWindow не смог захватить окно WSA.")

            header = BitmapInfoHeader()
            header.biSize = ctypes.sizeof(BitmapInfoHeader)
            header.biWidth = width
            header.biHeight = -height
            header.biPlanes = 1
            header.biBitCount = 32
            pixels = (ctypes.c_ubyte * (width * height * 4))()
            if not gdi32_api.GetDIBits(
                memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0
            ):
                raise RuntimeError("GetDIBits не смог прочитать окно WSA.")
            bgra = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)
            return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        finally:
            if old_bitmap and memory_dc:
                try:
                    gdi32_api.SelectObject(memory_dc, old_bitmap)
                except Exception:
                    pass
            if bitmap:
                try:
                    gdi32_api.DeleteObject(bitmap)
                except Exception:
                    pass
            if memory_dc:
                try:
                    gdi32_api.DeleteDC(memory_dc)
                except Exception:
                    pass
            if window_dc:
                try:
                    user32_api.ReleaseDC(self.hwnd, window_dc)
                except Exception:
                    pass

    def grab(self):
        """Capture the WSA HWND even when another desktop window overlaps it."""
        title = self._refresh_rect()
        width = int(self.rect["width"])
        height = int(self.rect["height"])
        frame = self._capture_client(width, height)
        return frame, title, dict(self.rect)



class ActionGate:
    """Require visual evidence before the state machine may click again."""

    def __init__(self):
        self.before = None
        self.label = None
        self.started_at = 0.0
        self.change_threshold = ACTION_CHANGE_DIFF
        self.roi = None

    @property
    def pending(self):
        return self.before is not None

    def arm(self, phone, label, change_threshold=ACTION_CHANGE_DIFF, roi=None):
        self.roi = tuple(roi) if roi else None
        self.before = self._region(phone).copy()
        self.label = label
        self.started_at = time.monotonic()
        self.change_threshold = float(change_threshold)
        log(f"Действие отправлено: {label}; жду смену кадра.")

    def observe(self, phone):
        if not self.pending:
            return "ready"
        elapsed = time.monotonic() - self.started_at
        if elapsed < ACTION_MIN_SETTLE:
            return "waiting"
        delta = diff(self._region(phone), self.before)
        if delta >= self.change_threshold:
            log(f"Кадр изменился после {self.label}: diff={delta:.1f}, {elapsed:.2f} сек.")
            self.before = None
            return "changed"
        if elapsed >= ACTION_TIMEOUT:
            return "timeout"
        return "waiting"

    def _region(self, phone):
        if not self.roi:
            return phone
        x, y, width, height = self.roi
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1 = min(phone.shape[1], x0 + max(1, int(width)))
        y1 = min(phone.shape[0], y0 + max(1, int(height)))
        return phone[y0:y1, x0:x1]


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
    # Keep the resource retry budget across modal geometry changes/restarts.
    "resource_retry_attempts": 0,
    "resource_retry_last_at": 0.0,
}


TUTORIAL_PERCEPTION = TutorialPerception()
TUTORIAL_ACTION_POLICY = BoundedActionPolicy()
TUTORIAL_TASK_ENGINE = SemanticTaskEngine(TUTORIAL_ACTION_POLICY)
LAST_TUTORIAL_SCREEN_MODEL = None


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


def publish_live_frame(img, viewport=None):
    """Publish one replaceable GUI frame plus client-relative input geometry."""
    ok, encoded = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
    if not ok:
        return False
    frame_tmp = LIVE_FRAME_FILE + ".tmp"
    meta_tmp = LIVE_FRAME_META_FILE + ".tmp"
    metadata = {
        "schema": 1,
        "written_at": datetime.now().astimezone().isoformat(),
        "viewport": dict(viewport or {}),
        "frame_width": int(img.shape[1]),
        "frame_height": int(img.shape[0]),
        "coordinate_space": "wsa-client" if BACKEND_NAME == "wsa" else "android-frame",
    }
    try:
        with open(frame_tmp, "wb") as stream:
            stream.write(encoded.tobytes())
        with open(meta_tmp, "w", encoding="utf-8") as stream:
            json.dump(metadata, stream, ensure_ascii=False, indent=2)
        os.replace(frame_tmp, LIVE_FRAME_FILE)
        os.replace(meta_tmp, LIVE_FRAME_META_FILE)
        return True
    except OSError:
        for tmp in (frame_tmp, meta_tmp):
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
        return False


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
        with open(CONTROL_FILE, "r", encoding="utf-8-sig") as f:
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
        try:
            emit_event("tutorial_complete", origin="initial")
        except Exception:
            pass
        set_phase(state, "create_character", "home")
        return
    try:
        emit_event("tutorial_complete", origin="new_character")
    except Exception:
        pass
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
    runtime_permissions = backend.grant_runtime_permissions()
    if result.strip():
        log("pm clear: " + result.strip())
    log("Runtime permissions restored: " + ", ".join(runtime_permissions))
    state["current_cycle"] = int(state.get("current_cycle", 1)) + 1
    state["characters_created_cycle"] = 0
    state["last_stop_reason"] = ""
    save_state(state)
    begin_tutorial(state, "initial")
    backend.launch_app()
    try:
        emit_event(
            "cycle_reset",
            current_cycle=int(state["current_cycle"]),
            characters_created=int(state.get("characters_created", 0)),
        )
    except Exception:
        pass
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
    "создайтеперсонажавмобильнойверсии",
    "передвходомсэтойплатформы",
)


def stop_reason_from_ocr_lines(lines):
    """Pure terminal-stop policy shared by live and replay perception."""
    normalized = "".join(line.get("normalized", "") for line in lines)
    for phrase in STOP_OCR_PHRASES:
        if phrase in normalized:
            return f"Сервер/аккаунт сообщил ограничение: {phrase}"
    return ""


def ocr_available():
    """Whether the required local OCR executable is available for safety gates."""
    return bool(TESSERACT and os.path.isfile(TESSERACT))


def detect_stop_reason(phone):
    """Recognise only stop conditions; never use OCR here to bypass them."""
    return stop_reason_from_ocr_lines(ocr_lines(phone))


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
    """Return the production capture for the automation state machine.

    WSA automation is deliberately fail-closed on the exact Kingshot HWND.
    Diagnostic scrcpy/PNG fallbacks remain available to CLI preview tooling,
    but the bot must never continue vision against an unrelated desktop area.
    """
    if BACKEND_NAME == "scrcpy":
        return ScrcpyCapture()
    if BACKEND_NAME == "wsa":
        return WsaGameWindowCapture()
    return create_preview_capture(get_device_backend())


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


def handle_known_notification_permission():
    """Dismiss only Kingshot's explicit Android notification permission dialog."""
    backend = get_device_backend()
    try:
        hierarchy = backend.ui_dump()
    except Exception:
        return False
    allow_id = "com.android.permissioncontroller:id/permission_allow_button"
    if (
        'package="com.android.permissioncontroller"' not in hierarchy
        or allow_id not in hierarchy
        or "permission_message" not in hierarchy
    ):
        return False
    if not backend.ui_click_resource(allow_id, timeout=2.0):
        return False
    log("Android: подтверждён явный системный запрос уведомлений Kingshot.")
    emit_event("android_permission", permission="notifications", action="allow")
    time.sleep(1.0)
    return True


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


def map_phone_norm(nx, ny):
    """Map normalized portrait-game coordinates into the physical Android display."""
    nx = max(0.0, min(1.0, float(nx)))
    ny = max(0.0, min(1.0, float(ny)))
    return (
        INPUT_CONTENT_LEFT + nx * INPUT_CONTENT_W,
        INPUT_CONTENT_TOP + ny * INPUT_CONTENT_H,
    )


def tap_norm(nx, ny):
    tap(*map_phone_norm(nx, ny))


def tap_client(phone, nx, ny):
    """Tap a normalized point in the portrait game content, not letterbox bars."""
    tap_norm(nx, ny)


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
        "newbie_offer_context.png", "newbie_offer_close.png", "offline_confirm.png",
        "invasion_title.png", "tutorial_skip.png", "tutorial_skip_small.png", "tutorial_skip_core.png",
        "tutorial_summon_button.png", "tutorial_hand_target.png", "tutorial_hand_quarry_core.png",
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


def resource_dialog_ocr_lines(phone):
    """Return corroborating OCR only for the bounded resource-error dialog.

    The normal sparse OCR intentionally misses embossed dialog text.  These
    two fixed dialog regions are read only after a central tutorial dialog was
    independently detected; their text is evidence, never a click source.
    """
    if not os.path.isfile(TESSERACT):
        return []
    height, width = phone.shape[:2]
    regions = (
        ("message", 0.14, 0.42, 0.86, 0.59),
        ("retry", 0.49, 0.60, 0.85, 0.68),
    )
    values = {}
    for name, x0f, y0f, x1f, y1f in regions:
        x0, x1 = round(width * x0f), round(width * x1f)
        y0, y1 = round(height * y0f), round(height * y1f)
        crop = phone[y0:y1, x0:x1]
        ok, raw = cv2.imencode(".png", cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC))
        if not ok:
            return []
        env = os.environ.copy()
        env["TESSDATA_PREFIX"] = os.path.join(os.path.dirname(TESSERACT), "tessdata") + os.sep
        run = subprocess.run([TESSERACT, "stdin", "stdout", "-l", "rus+eng", "--psm", "6"], input=raw.tobytes(), capture_output=True, env=env, check=False, creationflags=WINDOWS_NO_WINDOW)
        values[name] = run.stdout.decode("utf-8", "ignore") if not run.returncode else ""
    message = norm_text(values["message"])
    retry = norm_text(values["retry"])
    if "неудалосьзагрузитьресурс" not in message or "попыт" not in retry:
        return []
    return [
        {"text": values["message"].strip(), "normalized": message, "score": 80.0, "loc": (round(width*.14), round(height*.42)), "w": round(width*.72), "h": round(height*.17)},
        {"text": values["retry"].strip(), "normalized": retry, "score": 80.0, "loc": (round(width*.49), round(height*.59)), "w": round(width*.36), "h": round(height*.10)},
    ]


def ocr_action_from_lines(lines):
    for source in lines:
        line = dict(source)
        for phrase, (name, action) in OCR_ACTIONS.items():
            similarity = SequenceMatcher(None, phrase, line["normalized"]).ratio()
            # OCR may lose the first glyph on a rounded button (for example,
            # "ернуться в город"). Accept only a near-exact whitelist match.
            if phrase in line["normalized"] or similarity >= 0.88:
                line["name"] = name
                line["action"] = action
                return line
    return None


def ocr_action(phone):
    return ocr_action_from_lines(ocr_lines(phone))


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
        if cv2.countNonZero(cyan) >= roi.shape[0] * roi.shape[1] * 0.10:
            return True
        # Housing upgrades use a grey hold-button rather than the turquoise
        # construction action. OCR is still limited to the exact whitelist and
        # right-side action zone; additionally require the verified parchment
        # construction panel before accepting this visual variant.
        return is_construction_panel(phone)
    # Other tutorial buttons use visual templates; do not trust OCR for them.
    return False


def handle_tutorial_ocr(phone, state, lines=None):
    global LAST_OCR_AT
    if lines is None:
        if time.monotonic() - LAST_OCR_AT < OCR_INTERVAL:
            return False
        LAST_OCR_AT = time.monotonic()
        target = ocr_action(phone)
    else:
        target = ocr_action_from_lines(lines)
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


def loading_screen_visible(phone):
    """Recognize both the legacy logo and the current progress-bar splash."""
    legacy = match(phone, tpl("loading_logo.png"), 0.82)
    if legacy:
        return True
    if phone is None or phone.size == 0:
        return False

    height, width = phone.shape[:2]
    hsv = cv2.cvtColor(phone, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(hsv, np.array([5, 120, 100]), np.array([35, 255, 255]))
    upper = orange[round(height * 0.12):round(height * 0.45), :]
    lower = orange[round(height * 0.75):round(height * 0.95), :]
    if int(np.count_nonzero(upper)) < max(500, round(phone.size / 600)):
        return False
    contours, _ = cv2.findContours(lower, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        _, _, bar_width, bar_height = cv2.boundingRect(contour)
        if (
            bar_width >= round(width * 0.20)
            and bar_height >= 6
            and bar_width / max(1, bar_height) >= 2.5
        ):
            return True
    return False


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
        ("tutorial_hand_assignment_task.png", (0.20, 0.95), 0.82),
        ("tutorial_hand_assign_quarry.png", (0.40, 0.72), 0.82),
        ("tutorial_hand_assign_quarry_day.png", (0.40, 0.79), 0.80),
        ("tutorial_hand_assign_quarry_after_upgrade.png", (0.40, 0.79), 0.80),
        ("tutorial_hand_assign_resident_slot.png", (0.36, 0.84), 0.82),
        ("tutorial_hand_quarry_core.png", (0.22, 0.91), 0.62),
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
            if hit and hit["loc"][1] < round(phone.shape[0] * 0.25):
                # Animated NPC speech/icons near the top can resemble the old
                # housing-hand crop. Tutorial pointer targets live in the city
                # or panels below the HUD, never inside the top status area.
                continue
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


def perceive_tutorial_screen(phone, *, include_ocr=False):
    """Build the state-machine-facing screen model from reusable primitives."""
    global LAST_TUTORIAL_SCREEN_MODEL, LAST_LEGACY_TUTORIAL_SCAN_AT, LAST_OCR_AT
    now_mono = time.monotonic()
    collect_ocr = include_ocr or now_mono - LAST_OCR_AT >= OCR_INTERVAL
    lines = ocr_lines(phone) if collect_ocr else []
    if collect_ocr:
        LAST_OCR_AT = now_mono
    LAST_TUTORIAL_SCREEN_MODEL = TUTORIAL_PERCEPTION.perceive(
        phone,
        ocr_lines=lines,
    )
    if collect_ocr and LAST_TUTORIAL_SCREEN_MODEL.panel.kind == "tutorial_dialog":
        resource_lines = resource_dialog_ocr_lines(phone)
        if resource_lines:
            LAST_TUTORIAL_SCREEN_MODEL = TUTORIAL_PERCEPTION.perceive(phone, ocr_lines=lines + resource_lines)
    # Animated glow/motion is the production path.  Two small, background-
    # independent crops remain as a throttled migration fallback; the sixteen
    # historical scene templates stay available for offline regression only.
    now = time.monotonic()
    if (
        LAST_TUTORIAL_SCREEN_MODEL.tutorial_target is None
        and now - LAST_LEGACY_TUTORIAL_SCAN_AT >= 1.5
    ):
        LAST_LEGACY_TUTORIAL_SCAN_AT = now
        legacy_hint = match_tutorial_hand_core(phone)
        if legacy_hint:
            LAST_TUTORIAL_SCREEN_MODEL = TUTORIAL_PERCEPTION.perceive(
                phone,
                ocr_lines=lines,
                legacy_hint=legacy_hint,
            )
    return LAST_TUTORIAL_SCREEN_MODEL


def match_tutorial_hand_core(phone):
    """Throttled fallback using only two background-independent pointer crops."""
    best = None
    variants = (
        ("tutorial_hand_target.png", (0.46, 0.76), 0.70),
        ("tutorial_hand_quarry_core.png", (0.22, 0.91), 0.62),
    )
    for name, target, threshold in variants:
        image = tpl(name)
        if image is None:
            continue
        for scale in (0.80, 0.94, 1.0, 1.12, 1.25):
            resized = cv2.resize(
                image, None, fx=scale, fy=scale,
                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
            )
            hit = match(phone, resized, threshold, allow_scale=False)
            if hit and hit["loc"][1] >= round(phone.shape[0] * 0.25) \
                    and tutorial_target_is_lit(phone, hit, target):
                if best is None or hit["score"] > best["score"]:
                    hit.update(target=target, variant=name)
                    best = hit
    return best


def _button_hit(button):
    return button.bbox.as_hit() if button is not None else None


def _expanded_action_roi(phone, hit, scale=2.0):
    x, y = hit["loc"]
    width, height = hit["w"], hit["h"]
    pad_x = round(width * (scale - 1.0) / 2.0)
    pad_y = round(height * (scale - 1.0) / 2.0)
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1 = min(phone.shape[1], x + width + pad_x)
    y1 = min(phone.shape[0], y + height + pad_y)
    return [x0, y0, x1 - x0, y1 - y0]


def find_tutorial_primary_button(phone):
    """Find the large turquoise primary action in a construction panel."""
    height, width = phone.shape[:2]
    # Upgrade panels may start directly below the city viewport.  Their
    # actionable button is around mid-screen, while later tutorial panels put
    # it near the bottom.
    top = round(height * 0.40)
    roi = phone[top:]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (75, 80, 80), (105, 255, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < width * 0.18 or h < 28 or w / h < 1.15:
            continue
        candidates.append({"loc": (x, y + top), "w": w, "h": h, "score": float(cv2.contourArea(contour))})
    return max(candidates, key=lambda item: item["score"], default=None)


def find_resident_assignment_plus(phone):
    """Find the explicit green add-resident button on the quarry panel."""
    height, width = phone.shape[:2]
    lower = phone[round(height * 0.38):round(height * 0.96)]
    hsv_lower = cv2.cvtColor(lower, cv2.COLOR_BGR2HSV)
    beige = cv2.inRange(hsv_lower, (8, 8, 90), (35, 150, 255))
    if float(np.count_nonzero(beige)) / max(1.0, float(beige.size)) < 0.48:
        return None

    # City Center benefit icons can be green at the same coordinates as the
    # resident add button.  Require the resident panel's dark-brown tab
    # footer as independent context before an action is allowed.
    footer = hsv_lower[round(height * 0.52):round(height * 0.60), round(width * 0.05):round(width * 0.95)]
    footer_brown = cv2.inRange(footer, (5, 30, 20), (30, 255, 200))
    if float(np.count_nonzero(footer_brown)) / max(1.0, float(footer_brown.size)) < 0.20:
        return None

    hsv = cv2.cvtColor(phone, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (35, 90, 70), (95, 255, 255))
    contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        cx, cy = x + w / 2, y + h / 2
        if not (
            width * 0.54 <= cx <= width * 0.72
            and height * 0.72 <= cy <= height * 0.90
            and w >= width * 0.055
            and h >= height * 0.035
            and 0.45 <= w / max(1.0, float(h)) <= 1.25
        ):
            continue
        candidates.append({"loc": (x, y), "w": w, "h": h, "score": float(cv2.contourArea(contour))})
    return max(candidates, key=lambda item: item["score"], default=None)


def find_completed_resident_assignment(phone):
    """Find the disabled add button after the quarry reaches its worker cap."""
    height, width = phone.shape[:2]
    lower = phone[round(height * 0.38):round(height * 0.96)]
    hsv_lower = cv2.cvtColor(lower, cv2.COLOR_BGR2HSV)
    beige = cv2.inRange(hsv_lower, (8, 8, 90), (35, 150, 255))
    if float(np.count_nonzero(beige)) / max(1.0, float(beige.size)) < 0.48:
        return None

    hsv = cv2.cvtColor(phone, cv2.COLOR_BGR2HSV)
    # Once the counter reaches 2/2 the same add button is disabled: its green
    # fill becomes neutral grey. The adjacent counter touches its border, so a
    # contour would merge both controls; measure only the exact button cell.
    x0, x1 = round(width * 0.58), round(width * 0.68)
    y0, y1 = round(height * 0.80), round(height * 0.87)
    cell = hsv[y0:y1, x0:x1]
    neutral = cv2.inRange(cell, (0, 0, 55), (179, 85, 190))
    green = cv2.inRange(cell, (35, 90, 70), (95, 255, 255))
    neutral_coverage = float(np.count_nonzero(neutral)) / max(1.0, float(neutral.size))
    green_coverage = float(np.count_nonzero(green)) / max(1.0, float(green.size))
    if neutral_coverage < 0.30 or green_coverage >= 0.05:
        return None
    return {"loc": (x0, y0), "w": x1 - x0, "h": y1 - y0, "score": neutral_coverage}


def find_resident_source_upgrade_button(phone):
    """Find the upper Forward button in the exact two-option resident source modal."""
    height, width = phone.shape[:2]
    modal = phone[round(height * 0.31):round(height * 0.64), round(width * 0.08):round(width * 0.92)]
    hsv_modal = cv2.cvtColor(modal, cv2.COLOR_BGR2HSV)
    beige = cv2.inRange(hsv_modal, (8, 8, 90), (35, 150, 255))
    if float(np.count_nonzero(beige)) / max(1.0, float(beige.size)) < 0.42:
        return None

    hsv = cv2.cvtColor(phone, cv2.COLOR_BGR2HSV)
    cyan = cv2.inRange(hsv, (75, 80, 90), (105, 255, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 5))
    cyan = cv2.morphologyEx(cyan, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(cyan, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        cx, cy = x + w / 2, y + h / 2
        if not (
            width * 0.62 <= cx <= width * 0.90
            and height * 0.38 <= cy <= height * 0.62
            and width * 0.12 <= w <= width * 0.26
            and height * 0.035 <= h <= height * 0.075
        ):
            continue
        candidates.append({"loc": (x, y), "w": w, "h": h, "score": float(cv2.contourArea(contour))})
    candidates.sort(key=lambda item: item["loc"][1])
    if len(candidates) != 2:
        return None
    separation = candidates[1]["loc"][1] - candidates[0]["loc"][1]
    if separation < height * 0.07 or separation > height * 0.16:
        return None
    return candidates[0]


def is_construction_panel(phone):
    kitchen = match(phone, tpl("tutorial_kitchen_title.png"), 0.93)
    if kitchen:
        return True
    lines = ocr_lines(phone)
    normalized = " ".join(line["normalized"] for line in lines)
    # Grey section headings are less legible to OCR than the building title.
    # The large cyan button is already required, and this rule runs only in
    # the tutorial state machine.
    if any(word in normalized for word in ("кухня", "требуется", "улучшить", "барак")):
        return True

    # OCR is unreliable on WSA's scaled Cyrillic text.  Construction and
    # upgrade panels have a stable parchment area covering most of the lower
    # screen; require that visual context in addition to the cyan primary
    # button found by the caller.
    height = phone.shape[0]
    panel = phone[round(height * 0.38):]
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    parchment = cv2.inRange(hsv, (8, 10, 100), (35, 180, 255))
    coverage = float(np.count_nonzero(parchment)) / max(1.0, float(parchment.size))
    return coverage >= 0.45


def tap_match(phone, hit):
    x, y = hit["loc"]
    cx = x + hit["w"] / 2
    cy = y + hit["h"] / 2
    tap_norm(cx / phone.shape[1], cy / phone.shape[0])


def hold_match(phone, hit, duration_ms):
    x, y = hit["loc"]
    cx = x + hit["w"] / 2
    cy = y + hit["h"] / 2
    px, py = map_phone_norm(cx / phone.shape[1], cy / phone.shape[0])
    hold(px, py, duration_ms)


def tap_match_relative(phone, hit, rel_x, rel_y):
    """Tap a known point inside a context template, not its visual centre."""
    x = hit["loc"][0] + hit["w"] * rel_x
    y = hit["loc"][1] + hit["h"] * rel_y
    tap_norm(x / phone.shape[1], y / phone.shape[0])


def debug(phone, hit, name):
    x, y = hit["loc"]
    out = phone.copy()
    cv2.rectangle(out, (x, y), (x+hit["w"], y+hit["h"]), (0,255,0), 3)
    score = float(hit.get("score", 0.0))
    save_img(os.path.join(DEBUG_DIR, f"{fs()}_{name}_{score:.3f}.png"), out)


def save_tutorial_perception_bundle(full_frame, phone, state, screenshot_path):
    """Persist one self-contained fail-closed diagnostic for an unknown UI."""
    screen = LAST_TUTORIAL_SCREEN_MODEL
    if screen is None:
        screen = perceive_tutorial_screen(phone, include_ocr=True)
    else:
        # OCR remains throttled during normal gameplay; run it only when a
        # diagnostic bundle is already being written.
        screen.ocr_lines = ocr_lines(phone)
    annotated = phone.copy()
    for button in screen.buttons:
        box = button.bbox
        color = (255, 180, 0) if button.enabled else (130, 130, 130)
        cv2.rectangle(
            annotated,
            (box.x, box.y),
            (box.x + box.width, box.y + box.height),
            color,
            2,
        )
    if screen.tutorial_target:
        box = screen.tutorial_target.bbox
        cv2.rectangle(
            annotated,
            (box.x, box.y),
            (box.x + box.width, box.y + box.height),
            (0, 255, 255),
            3,
        )
    stamp = fs()
    full_path = os.path.join(DEBUG_DIR, f"tutorial-perception-{stamp}-full.png")
    normalized_path = os.path.join(DEBUG_DIR, f"tutorial-perception-{stamp}-normalized.png")
    annotated_path = os.path.join(DEBUG_DIR, f"tutorial-perception-{stamp}-annotated.png")
    save_img(full_path, full_frame)
    save_img(normalized_path, phone)
    save_img(annotated_path, annotated)
    payload = {
        "schema": 1,
        "captured_at": datetime.now().astimezone().isoformat(),
        "phase": state.get("phase"),
        "step": state.get("step"),
        "reason": "no action met the fail-closed perception policy",
        "source_screenshot": screenshot_path,
        "full_frame": full_path,
        "normalized_frame": normalized_path,
        "annotated_frame": annotated_path,
        "screen": screen.to_dict(),
    }
    report_path = os.path.join(DEBUG_DIR, "tutorial-perception-failure.json")
    tmp_path = report_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    os.replace(tmp_path, report_path)
    return report_path


def sync_input_geometry(frame, rect=None):
    """Map portrait game coordinates onto the physical Android framebuffer.

    WSA may expose a 16:9 framebuffer while Kingshot is portrait and centered
    with side bars. Vision works on the cropped portrait content, therefore
    input must add the same crop offset instead of scaling to the full display.
    """
    global INPUT_W, INPUT_H
    global INPUT_CONTENT_LEFT, INPUT_CONTENT_TOP, INPUT_CONTENT_W, INPUT_CONTENT_H

    if BACKEND_NAME == "scrcpy":
        INPUT_W, INPUT_H = PHONE_W, PHONE_H
    else:
        height, width = frame.shape[:2]
        rect_width = int((rect or {}).get("width", 0) or 0)
        rect_height = int((rect or {}).get("height", 0) or 0)
        if rect_width >= 100 and rect_height >= 100:
            INPUT_W, INPUT_H = rect_width, rect_height
        elif width >= 100 and height >= 100:
            INPUT_W, INPUT_H = width, height

    frame_h, frame_w = frame.shape[:2]
    crop_left, crop_top, crop_w, crop_h = detect_content_rect(frame)
    scale_x = INPUT_W / max(1.0, float(frame_w))
    scale_y = INPUT_H / max(1.0, float(frame_h))
    left = crop_left * scale_x
    top = crop_top * scale_y
    content_w = crop_w * scale_x
    content_h = crop_h * scale_y

    INPUT_CONTENT_LEFT = left
    INPUT_CONTENT_TOP = top
    INPUT_CONTENT_W = content_w
    INPUT_CONTENT_H = content_h


def detect_content_rect(frame):
    """Return the real non-black Android/game viewport inside a WSA frame."""
    sh, sw = frame.shape[:2]
    if sh < 2 or sw < 2:
        return 0, 0, sw, sh
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    active = gray > 8
    cols = np.flatnonzero(active.sum(axis=0) >= max(8, round(sh * 0.05)))
    rows = np.flatnonzero(active.sum(axis=1) >= max(8, round(sw * 0.05)))
    if cols.size and rows.size:
        left, right = int(cols[0]), int(cols[-1] + 1)
        top, bottom = int(rows[0]), int(rows[-1] + 1)
        width, height = right - left, bottom - top
        if width >= 100 and height >= 100:
            return left, top, width, height

    target = PHONE_W / PHONE_H
    actual = sw / max(1.0, sh)
    if actual > target:
        width = round(sh * target)
        return max(0, (sw - width) // 2), 0, width, sh
    height = round(sw / target)
    return 0, max(0, (sh - height) // 2), sw, height


def crop_phone(frame):
    sh, sw = frame.shape[:2]
    left, top, content_w, content_h = detect_content_rect(frame)
    phone = frame[top:top + content_h, left:left + content_w]
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


def match_newbie_offer_close(phone):
    """Return the offer X only when the current offer panel is also present."""
    close = match(phone, tpl("newbie_offer_close.png"), 0.74)
    if not close:
        return None
    height, width = phone.shape[:2]
    x, y = close["loc"]
    if x < round(width * 0.65) or y > round(height * 0.20):
        return None

    hsv = cv2.cvtColor(phone, cv2.COLOR_BGR2HSV)
    panel = hsv[
        round(height * 0.25):round(height * 0.82),
        round(width * 0.10):round(width * 0.90),
    ]
    yellow = cv2.inRange(panel, np.array([10, 70, 100]), np.array([40, 255, 255]))
    coverage = float(np.count_nonzero(yellow)) / max(1.0, float(yellow.size))
    return close if coverage >= 0.18 else None


def match_offline_confirm(phone):
    """Match the explicit offline-income confirmation button on scaled WSA UI.

    WSA can stretch this modal independently on each axis.  The generic
    template matcher intentionally uses uniform scaling, so keep the
    anisotropic search local to this known button and require its documented
    lower-centre position before allowing an action.
    """
    image = tpl("offline_confirm.png")
    if image is None:
        return None
    height, width = phone.shape[:2]
    best = match(phone, image, 0.90)
    if not best:
        for scale_x in (0.65, 0.70, 0.75, 0.80, 0.85):
            for scale_y in (0.90, 1.00, 1.10, 1.20):
                resized = cv2.resize(
                    image, None, fx=scale_x, fy=scale_y,
                    interpolation=cv2.INTER_AREA if scale_x < 1 else cv2.INTER_CUBIC,
                )
                hit = match(phone, resized, 0.80, allow_scale=False)
                if hit and (best is None or hit["score"] > best["score"]):
                    best = hit
    if not best:
        return None
    x, y = best["loc"]
    center_x = x + best["w"] / 2
    center_y = y + best["h"] / 2
    if not (
        width * 0.25 <= center_x <= width * 0.75
        and height * 0.65 <= center_y <= height * 0.90
    ):
        return None
    return best


def handle_overlay(phone):
    hit = match(phone, tpl("newbie_offer_context.png"), 0.86)
    close = match_newbie_offer_close(phone)
    if hit or close:
        if not close:
            return False
        if hit:
            debug(phone, hit, "newbie_offer")
        debug(phone, close, "newbie_offer_close")
        log(
            "Контекстно найден «Ценный набор новичка»; "
            "закрываю только по его конкретному X-шаблону."
        )
        tap_match(phone, close)
        return True

    hit = match_offline_confirm(phone)
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
        state["state3_modal_score"] = round(float(modal["score"]), 4)
        state["state3_confirm_score"] = round(float(confirm["score"]), 4)
        set_step(state, "state_confirm_applied")
        return True

    if step == "state_confirm_applied":
        # Do not claim State #3 merely because the tap was sent.  Require the
        # confirmation modal to disappear and the new-character tutorial UI
        # to become visible.
        if match(phone, tpl("state3_modal.png"), 0.86):
            return False
        tutorial_visible = False
        for template_name, threshold in (
            ("tutorial_skip.png", 0.86),
            ("tutorial_skip_core.png", 0.86),
            ("tutorial_hand_target.png", 0.90),
        ):
            if match(phone, tpl(template_name), threshold):
                tutorial_visible = True
                break
        if not tutorial_visible:
            return False
        try:
            emit_event(
                "state3_confirmed",
                target_state=3,
                modal_score=state.get("state3_modal_score"),
                confirm_score=state.get("state3_confirm_score"),
                postcondition="new_character_tutorial_visible",
            )
        except Exception:
            pass
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
        blocking_target = perceive_tutorial_screen(phone).tutorial_target
        if blocking_target:
            log("Обучение закончено; убираю оставшуюся подтверждённую tutorial-цель.")
            tap_match(phone, blocking_target.bbox.as_hit())
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
            blocking_target = perceive_tutorial_screen(phone).tutorial_target
            if avatar or blocking_target:
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
        committed_number = int(state.get("pending_nickname", 1))
        committed_nickname = f"Тугарин{committed_number}"
        now_mono = time.monotonic()
        if now_mono - float(state.get("rename_ocr_checked_at", 0.0)) < 1.5:
            return False
        state["rename_ocr_checked_at"] = now_mono
        expected_text = norm_text(committed_nickname)
        confirmed_line = next(
            (line for line in ocr_lines(phone) if line.get("normalized") == expected_text),
            None,
        )
        if confirmed_line is None:
            log(f"Проверка имени: «{committed_nickname}» ещё не подтверждено OCR.")
            return False
        evidence_path = os.path.join(
            DEBUG_DIR,
            f"rename_committed_{committed_number}_{fs()}.png",
        )
        if not save_img(evidence_path, phone) or not os.path.isfile(evidence_path):
            log("Не удалось сохранить обязательный screenshot после переименования.")
            return False
        x, y = confirmed_line["loc"]
        crop = phone[y:y + confirmed_line["h"], x:x + confirmed_line["w"]]
        crop_path = os.path.join(
            DEBUG_DIR,
            f"rename_committed_{committed_number}_{fs()}_name.png",
        )
        if not save_img(crop_path, crop) or not os.path.isfile(crop_path):
            log("Не удалось сохранить обязательный crop имени после переименования.")
            return False
        with open(evidence_path, "rb") as stream:
            evidence_sha256 = hashlib.sha256(stream.read()).hexdigest()
        with open(crop_path, "rb") as stream:
            crop_sha256 = hashlib.sha256(stream.read()).hexdigest()
        state["next_nickname"] = committed_number + 1
        state["characters_created"] = int(state.get("characters_created", 0)) + 1
        try:
            emit_event(
                "nickname_committed",
                nickname=committed_nickname,
                characters_created=int(state["characters_created"]),
                evidence_screenshot=evidence_path,
                evidence_screenshot_sha256=evidence_sha256,
                evidence_name_crop=crop_path,
                evidence_name_crop_sha256=crop_sha256,
                nickname_ocr_text=confirmed_line["text"],
                nickname_ocr_confirmed=True,
            )
        except Exception:
            pass
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
    # Account-limit dialogs can visually resemble construction panels. Without
    # the local OCR safety channel there is no independent way to distinguish
    # them, so never let either semantic or legacy perception authorize input.
    if not ocr_available():
        state["last_stop_reason"] = (
            "OCR_UNAVAILABLE: terminal account/restriction checks cannot run; "
            "tutorial input is blocked fail-closed."
        )
        save_state(state)
        log("STOP: " + state["last_stop_reason"])
        return "ocr_unavailable"
    step = state["step"]
    screen = perceive_tutorial_screen(phone)

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
            ("governor_avatar_small.png", 0.94),            ("governor_avatar_large.png", 0.94),
        ):
            governor = match(phone, tpl(name), threshold)
            if governor:
                log("Туториал завершён: запускаю обязательное переименование перед новым персонажем.")
                finish_tutorial(state)
                return "wait"

    # A visible hand is the tutorial's exclusive input contract: the game
    # disables every other control. It must therefore outrank Skip, OCR, and
    # colour-based buttons; clicking anything else only wastes an action.
    guidance = screen.tutorial_target
    if guidance:
        hand_target = guidance.bbox.as_hit()
        decision = TUTORIAL_ACTION_POLICY.decide(
            state, "tutorial_target", guidance.bbox, phone.shape, retry_after=3.0
        )
        if decision in ("act", "retry"):
            debug(phone, hand_target, "tutorial_guidance_target")
            verb = "повторяю" if decision == "retry" else "нажимаю"
            log(
                f"Туториал: {verb} подтверждённую цель "
                f"source={guidance.source} confidence={guidance.confidence:.2f}."
            )
            tap_match(phone, hand_target)
            state["tutorial_hand_locked"] = True
            set_step(state, "tutorial_wait_hand_result")
            return "held"
        if decision == "exhausted":
            log("Туториал: два подтверждённых нажатия не убрали ту же цель; fail-closed.")
            return False
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

    if TUTORIAL_ACTION_POLICY.clear_kind(state, "tutorial_target"):
        state["tutorial_hand_locked"] = False
        save_state(state)
        log("Туториал: подтверждённая цель исчезла; action policy разблокирована.")
    elif state.get("tutorial_hand_locked", False):
        state["tutorial_hand_locked"] = False
        save_state(state)
        log("Туториал: рука-указатель исчезла; следующий маркер снова может быть обработан.")

    reward_claim = _button_hit(screen.button("battle_reward_claim"))
    if reward_claim:
        claim_box = Box(reward_claim["loc"][0], reward_claim["loc"][1], reward_claim["w"], reward_claim["h"])
        task = TUTORIAL_TASK_ENGINE.plan(state, screen, "battle_reward_claim", phone.shape)
        decision = task.status if task else "wait"
        if decision in ("act", "retry"):
            debug(phone, reward_claim, "tutorial_battle_reward_claim")
            log(f"Туториал: подтверждён reward «Получить»; action={decision}.")
            tap_match(phone, reward_claim)
            set_step(state, "tutorial_wait_hand_result")
            return "acted"
        if decision == "exhausted":
            log("Туториал: reward «Получить» не исчез после bounded retry; fail-closed.")
            return False
        return "wait"

    resource_retry = _button_hit(screen.button("resource_load_retry"))
    if resource_retry:
        retry_box = Box(resource_retry["loc"][0], resource_retry["loc"][1], resource_retry["w"], resource_retry["h"])
        attempts = int(state.get("resource_retry_attempts", 0))
        last_at = float(state.get("resource_retry_last_at", 0.0))
        # Signature buckets include geometry. Never allow a resized/reflowed
        # dialog to silently reset the overall resource retry budget.
        if attempts >= 2:
            if time.time() - last_at < 3.0:
                return "wait"
            decision = "exhausted"
        else:
            decision = TUTORIAL_ACTION_POLICY.decide(
                state, "resource_load_retry", retry_box, phone.shape, retry_after=3.0
            )
        if decision in ("act", "retry"):
            state["resource_retry_attempts"] = attempts + 1
            state["resource_retry_last_at"] = time.time()
            save_state(state)
            debug(phone, resource_retry, "tutorial_resource_load_retry")
            log(f"Туториал: подтверждена загрузочная ошибка; retry={decision} ({attempts + 1}/2).")
            tap_match(phone, resource_retry)
            set_step(state, "tutorial_wait_hand_result")
            return "acted"
        if decision == "exhausted":
            # A known external loading error is NOT an unknown screen.
            state["last_stop_reason"] = (
                "GAME_RESOURCE_LOADING_FAILED: Kingshot не загрузил ресурсы "
                f"после {attempts} подтверждённых попыток; дальнейшие нажатия запрещены."
            )
            save_state(state)
            log("STOP: " + state["last_stop_reason"])
            return "resource_blocked"
        return "wait"

    battle_conquer = _button_hit(screen.button("battle_conquer"))
    if battle_conquer:
        conquer_box = Box(battle_conquer["loc"][0], battle_conquer["loc"][1], battle_conquer["w"], battle_conquer["h"])
        task = TUTORIAL_TASK_ENGINE.plan(state, screen, "battle_conquer", phone.shape)
        decision = task.status if task else "wait"
        if decision in ("act", "retry"):
            debug(phone, battle_conquer, "tutorial_battle_conquer")
            log(f"Туториал: подтверждённое действие «Завоевать»; action={decision}.")
            tap_match(phone, battle_conquer)
            set_step(state, "tutorial_wait_hand_result")
            return "acted"
        if decision == "exhausted":
            log("Туториал: «Завоевать» не исчезло после bounded retry; fail-closed.")
            return False
        return "wait"

    resident_source = _button_hit(screen.button("source_upgrade"))
    if resident_source:
        task = TUTORIAL_TASK_ENGINE.plan(state, screen, "source_upgrade", phone.shape)
        decision = task.status if task else "wait"
        if decision in ("act", "retry"):
            debug(phone, resident_source, "tutorial_resident_source_upgrade")
            log(f"Туториал: подтверждено окно источников жителей; action={decision}.")
            tap_match(phone, resident_source)
            set_step(state, task.rule.next_step)
            return "acted"
        if decision == "exhausted":
            log("Туториал: источник жителей не изменился после bounded retry; fail-closed.")
            return False
        return "wait"
    resident_source = find_resident_source_upgrade_button(phone)
    if resident_source:
        debug(phone, resident_source, "tutorial_resident_source_upgrade")
        log("Туториал: подтверждено окно источников жителей; выбираю верхнее «Улучшить дом».")
        tap_match(phone, resident_source)
        set_step(state, "tutorial_wait_hand_result")
        return "acted"

    resident_plus = _button_hit(screen.button("resident_add"))
    if resident_plus:
        task = TUTORIAL_TASK_ENGINE.plan(state, screen, "resident_add", phone.shape)
        decision = task.status if task else "wait"
        if decision in ("act", "retry"):
            debug(phone, resident_plus, "tutorial_assign_resident_plus")
            log(f"Туториал: подтверждена панель жителей каменоломни; action={decision}.")
            tap_match(phone, resident_plus)
            state["action_change_threshold"] = 0.2
            state["action_change_roi"] = _expanded_action_roi(phone, resident_plus, 2.5)
            set_step(state, task.rule.next_step)
            return "acted"
        if decision == "exhausted":
            log("Туториал: назначение жителя не изменило экран после bounded retry; fail-closed.")
            return False
        return "wait"
    resident_plus = find_resident_assignment_plus(phone)
    if resident_plus:
        debug(phone, resident_plus, "tutorial_assign_resident_plus")
        log("Туториал: подтверждена панель жителей каменоломни; назначаю рабочего кнопкой +.")
        tap_match(phone, resident_plus)
        # Assigning one resident changes only a small portrait/counter region.
        # Keep the action fail-closed, but use a local-action threshold instead
        # of the scene-transition threshold used by full-screen tutorial steps.
        state["action_change_threshold"] = 0.2
        state["action_change_roi"] = _expanded_action_roi(phone, resident_plus, 2.5)
        set_step(state, "tutorial_wait_hand_result")
        return "acted"

    resident_complete = _button_hit(screen.button("resident_complete"))
    if resident_complete:
        task = TUTORIAL_TASK_ENGINE.plan(state, screen, "resident_complete", phone.shape)
        decision = task.status if task else "wait"
        if decision in ("act", "retry"):
            debug(phone, resident_complete, "tutorial_resident_assignment_complete")
            log(f"Туториал: панель жителей заполнена; action={decision}, закрываю Android Back.")
            key(4)
            set_step(state, task.rule.next_step)
            return "acted"
        if decision == "exhausted":
            log("Туториал: панель жителей не закрылась после bounded retry; fail-closed.")
            return False
        return "wait"
    resident_complete = find_completed_resident_assignment(phone)
    if resident_complete:
        debug(phone, resident_complete, "tutorial_resident_assignment_complete")
        log("Туториал: панель жителей заполнена; закрываю подтверждённую панель Android Back.")
        key(4)
        set_step(state, "tutorial_wait_hand_result")
        return "acted"

    upgrade_button = screen.button("construction_upgrade")
    upgrade = _button_hit(upgrade_button)
    if upgrade:
        task = TUTORIAL_TASK_ENGINE.plan(state, screen, "construction_upgrade", phone.shape)
        decision = task.status if task else "wait"
        if decision in ("act", "retry"):
            debug(phone, upgrade, "tutorial_construction_upgrade")
            log(f"Туториал: подтверждено «Улучшить»; hold {UPGRADE_HOLD_MS} ms action={decision}.")
            hold_match(phone, upgrade, UPGRADE_HOLD_MS)
            state["ocr_upgrade_hold_ms"] = UPGRADE_HOLD_MS
            set_step(state, task.rule.next_step)
            return "held"
        if decision == "exhausted":
            log("Туториал: upgrade-кнопка осталась после bounded hold retry; fail-closed.")
            return False
        return "wait"
    if TUTORIAL_ACTION_POLICY.clear_kind(state, "construction_upgrade"):
        state["ocr_upgrade_hold_ms"] = 0
        save_state(state)

    primary_button = screen.button("construction_primary")
    primary = _button_hit(primary_button)
    primary_from_semantic_model = primary is not None
    if not primary:
        primary = find_tutorial_primary_button(phone)
    if primary and (screen.panel.kind == "construction" or is_construction_panel(phone)):
        if primary_from_semantic_model:
            task = TUTORIAL_TASK_ENGINE.plan(state, screen, "construction_primary", phone.shape)
            decision = task.status if task else "wait"
            next_step = task.rule.next_step if task else "tutorial_wait_construction"
        else:
            primary_box = Box(primary["loc"][0], primary["loc"][1], primary["w"], primary["h"])
            decision = TUTORIAL_ACTION_POLICY.decide(
                state, "construction_primary", primary_box, phone.shape, retry_after=3.0
            )
            next_step = "tutorial_wait_construction"
        if decision in ("act", "retry"):
            debug(phone, primary, "tutorial_primary_button")
            log(f"Туториал: панель строительства подтверждена; action={decision}.")
            tap_match(phone, primary)
            state["tutorial_primary_locked"] = True
            set_step(state, next_step)
            return "acted"
        if decision == "exhausted":
            log("Туториал: кнопка строительства осталась после bounded retry; fail-closed.")
            return False
        return "wait"
    if TUTORIAL_ACTION_POLICY.clear_kind(state, "construction_primary") and state.get("tutorial_primary_locked", False):
        state["tutorial_primary_locked"] = False
        state.pop("tutorial_primary_signature", None)
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
        if loading_screen_visible(phone):
            loading_started = float(state.get("loading_started_at", 0.0)) or time.time()
            state["loading_started_at"] = loading_started
            save_state(state)
            if time.time() - loading_started > 240:
                state["last_stop_reason"] = "Загрузочный экран не завершился за 240 сек."
                save_state(state)
                return False
            log("Туториал: загрузочный экран, жду.")
            return "wait"
        if state.get("loading_started_at"):
            state["loading_started_at"] = 0.0
            save_state(state)

        # Dialogue screens can appear before/after Skip. We do not ship a
        # guessed generic dialogue image; the fallback below only accepts
        # explicit "Далее/Продолжить" OCR text in the safe lower dialogue zone.
        fallback = handle_tutorial_ocr(phone, state, screen.ocr_lines)
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
        # Tutorial hands are handled globally by TutorialPerception above; do
        # not reintroduce scene-specific pointer templates here.
        hit = match(phone, tpl("task_scroll.png"), 0.88)
        if not hit:
            fallback = handle_tutorial_ocr(phone, state, screen.ocr_lines)
            return fallback if fallback else False
        debug(phone, hit, "task_scroll")
        log("Туториал: найден свиток задания. Нажимаю свиток.")
        tap_match(phone, hit)
        set_step(state, "tutorial_building")
        return "acted"

    if step == "tutorial_building":
        # The construction panel is handled by ScreenModel before this branch.
        # OCR is the fail-closed semantic fallback; no dedicated upgrade PNG is
        # required for another colour/background variant.
        fallback = handle_tutorial_ocr(phone, state, screen.ocr_lines)
        return fallback if fallback else False

    fallback = handle_tutorial_ocr(phone, state, screen.ocr_lines)
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
    handle_known_notification_permission()

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
    last_stop_scan = 0.0
    capture = None
    pause_reported = False
    recovery = RecoveryController()
    heartbeat = RuntimeHeartbeat()
    backend = get_device_backend()
    last_runtime_probe = 0.0
    last_live_frame_at = 0.0
    heartbeat.write(
        state=state,
        backend=backend.backend_name,
        serial=backend.serial,
        force=True,
        status="starting",
    )

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

            now_mono = time.monotonic()
            if now_mono - last_runtime_probe >= RUNTIME_PROBE_INTERVAL:
                last_runtime_probe = now_mono
                try:
                    decision = recovery.probe_runtime(backend)
                except Exception as exc:
                    decision = None
                    state["last_stop_reason"] = f"Runtime health probe failed: {exc}"
                    save_state(state)
                    log("STOP: " + state["last_stop_reason"])
                    try:
                        emit_event(
                            "runtime_probe",
                            action="stop",
                            terminal=True,
                            detail=state["last_stop_reason"],
                        )
                    except Exception:
                        pass
                    break

                if decision.action != "healthy":
                    try:
                        emit_event(
                            "runtime_probe",
                            action=decision.action,
                            terminal=decision.terminal,
                            detail=decision.detail,
                        )
                    except Exception:
                        pass
                    log(f"Runtime probe: {decision.action}: {decision.detail}")
                    if decision.terminal:
                        state["last_stop_reason"] = decision.detail
                        save_state(state)
                        break
                    if decision.action in ("game_restarted", "wait_runtime"):
                        gate = ActionGate()
                        unknown_since = None
                        if capture is not None:
                            try:
                                capture.close()
                            except Exception:
                                pass
                            capture = None
                        time.sleep(2.0)
                        continue

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
                    sync_input_geometry(frame, rect)
                    log(
                        f"Захват Android: {title} {rect['width']}x{rect['height']} "
                        f"content={INPUT_CONTENT_W:.0f}x{INPUT_CONTENT_H:.0f}"
                    )
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
                frame, _, rect = capture.grab()
                recovery.capture_succeeded()
            except Exception as e:
                log(f"Захват Android: {e}. Пересоздаю захват.")
                capture.close()
                capture = None
                if not handle_capture_failure(recovery, state, e):
                    break
                time.sleep(2)
                continue
            heartbeat.mark_frame()
            heartbeat.write(
                state=state,
                backend=backend.backend_name,
                serial=backend.serial,
            )
            sync_input_geometry(frame, rect)
            phone, left, right = crop_phone(frame)
            if now_mono - last_live_frame_at >= 0.5:
                publish_live_frame(
                    phone,
                    viewport={
                        "left": round(INPUT_CONTENT_LEFT),
                        "top": round(INPUT_CONTENT_TOP),
                        "width": round(INPUT_CONTENT_W),
                        "height": round(INPUT_CONTENT_H),
                    },
                )
                last_live_frame_at = now_mono

            if not stream_ok(frame, left, right):
                if time.time() - warn_at > 5:
                    log("Android backend вернул пустой кадр — пауза.")
                    warn_at = time.time()
                unknown_since = None
                time.sleep(0.7)
                continue

            if now_mono - last_stop_scan >= 2.0:
                last_stop_scan = now_mono
                stop_reason = detect_stop_reason(phone)
                if stop_reason:
                    out = os.path.join(
                        DEBUG_DIR,
                        f"account_restriction_{fs()}_{state['phase']}_{state['step']}.png",
                    )
                    save_img(out, phone)
                    state["last_stop_reason"] = stop_reason
                    save_state(state)
                    emit_event(
                        "account_restriction",
                        terminal=True,
                        detail=stop_reason,
                        evidence_screenshot=out,
                    )
                    log("STOP: " + stop_reason + f" Evidence: {out}")
                    break

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
                elif tutorial_result == "resource_blocked":
                    # Preserve a distinct terminal outcome for this run; the
                    # 75-second unknown-screen watchdog must not overwrite it.
                    screenshot = os.path.join(
                        DEBUG_DIR,
                        f"game_resource_blocked_{fs()}.png",
                    )
                    save_img(screenshot, phone)
                    bundle = save_tutorial_perception_bundle(
                        frame, phone, state, screenshot,
                    )
                    diagnostic_path = os.path.join(
                        DEBUG_DIR, "game-resource-network-diagnostics.json",
                    )
                    attempts = int(state.get("resource_retry_attempts", 0))
                    try:
                        diagnostic = collect_resource_network_diagnostics(
                            backend,
                            run_id=os.environ.get("TUGARIN_ACCEPTANCE_RUN_ID", ""),
                            head=os.environ.get("TUGARIN_ACCEPTANCE_HEAD", ""),
                            phase=str(state.get("phase", "")),
                            step=str(state.get("step", "")),
                            attempts=attempts,
                        )
                        save_resource_network_diagnostics(diagnostic_path, diagnostic)
                    except Exception as exc:
                        # Diagnostics cannot obscure the original terminal cause.
                        log("Resource diagnostic unavailable: " + type(exc).__name__)
                    emit_event(
                        "game_resource_blocked",
                        terminal=True,
                        reason=state["last_stop_reason"],
                        confirmed_retries=attempts,
                        evidence_screenshot=screenshot,
                        perception_bundle=bundle,
                        network_diagnostics=diagnostic_path,
                    )
                    log(
                        f"FAIL-CLOSED external Kingshot resources: "
                        f"{state['last_stop_reason']} | {bundle}"
                    )
                    break
                elif tutorial_result == "ocr_unavailable":
                    screenshot = os.path.join(DEBUG_DIR, f"ocr_unavailable_{fs()}.png")
                    save_img(screenshot, phone)
                    emit_event(
                        "ocr_unavailable",
                        terminal=True,
                        reason=state["last_stop_reason"],
                        evidence_screenshot=screenshot,
                    )
                    log("FAIL-CLOSED tutorial OCR prerequisite unavailable.")
                    break

            if acted:
                heartbeat.mark_action()
                change_threshold = float(
                    state.pop("action_change_threshold", ACTION_CHANGE_DIFF)
                )
                change_roi = state.pop("action_change_roi", None)
                gate.arm(
                    phone,
                    f"{state['phase']}/{state['step']}",
                    change_threshold=change_threshold,
                    roi=change_roi,
                )
                save_state(state)
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
                        if state.get("phase") == "tutorial_new_character":
                            bundle = save_tutorial_perception_bundle(frame, phone, state, out)
                            log(f"Tutorial perception evidence: {bundle}")
                    last_unknown = phone.copy()
                    last_unknown_at = now

            if now - unknown_since >= WATCHDOG_SECONDS:
                out = os.path.join(
                    UNKNOWN_DIR,
                    f"watchdog_{fs()}_{state['phase']}_{state['step']}.png"
                )
                save_img(out, phone)
                if state.get("phase") == "tutorial_new_character":
                    bundle = save_tutorial_perception_bundle(frame, phone, state, out)
                    log(f"Tutorial perception evidence: {bundle}")
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
        try:
            heartbeat.write(
                state=state,
                backend=backend.backend_name,
                serial=backend.serial,
                force=True,
                status="stopped",
                detail=str(state.get("last_stop_reason", "") or ""),
            )
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
