import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime

import cv2
from PySide6.QtCore import QLockFile, QProcess, QProcessEnvironment, QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

import bot
from device_backend import BackendCapture, create_backend
from frame_stream import ContinuousFrameStream, create_preview_capture


ROOT = os.path.dirname(os.path.abspath(__file__))
CONTROL_FILE = os.path.join(ROOT, "control.json")
GUI_CONFIG_FILE = os.path.join(ROOT, "gui_config.json")
LOG_FILE = os.path.join(ROOT, "logs", "bot.log")
PID_FILE = os.path.join(ROOT, "bot.pid")
HEARTBEAT_FILE = os.path.join(ROOT, "debug", "runtime-heartbeat.json")

PHASE_NAMES = {
    "rename_governor": "Переименование губернатора",
    "create_character": "Создание персонажа",
    "tutorial_new_character": "Обязательное обучение",
    "reset_cycle": "Сброс данных и новый цикл",
    "complete": "Цикл завершён",
}


def consoleless_python(path):
    """Prefer pythonw.exe so a GUI child never allocates a console window."""
    value = os.path.abspath(str(path or sys.executable))
    if os.name == "nt" and os.path.basename(value).lower() == "python.exe":
        candidate = os.path.join(os.path.dirname(value), "pythonw.exe")
        if os.path.isfile(candidate):
            return candidate
    return value


def atomic_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def bot_pid():
    try:
        with open(PID_FILE, "r", encoding="ascii") as stream:
            pid = int(stream.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


class InteractivePreview(QLabel):
    """Interactive Android framebuffer shown inside TUGARIN BOTS.

    Mouse clicks become taps, mouse drags become swipes, and focused keyboard
    events are forwarded to Android. The preview may be scaled/letterboxed, so
    pointer coordinates are mapped back to the real framebuffer size.
    """

    tap_requested = Signal(int, int)
    swipe_requested = Signal(int, int, int, int, int)
    hold_requested = Signal(int, int, int)
    key_requested = Signal(str)
    text_requested = Signal(str)

    def __init__(self, text=""):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._device_width = 0
        self._device_height = 0
        self._device_left = 0
        self._device_top = 0
        self._press_point = None
        self._press_at = 0.0

    def set_device_size(self, width, height, left=0, top=0):
        self._device_width = max(0, int(width or 0))
        self._device_height = max(0, int(height or 0))
        self._device_left = max(0, int(left or 0))
        self._device_top = max(0, int(top or 0))

    def _map_to_device(self, point):
        pixmap = self.pixmap()
        if (
            pixmap is None
            or pixmap.isNull()
            or self._device_width <= 0
            or self._device_height <= 0
        ):
            return None

        pw = pixmap.width()
        ph = pixmap.height()
        left = (self.width() - pw) / 2.0
        top = (self.height() - ph) / 2.0
        x = point.x() - left
        y = point.y() - top
        if x < 0 or y < 0 or x >= pw or y >= ph:
            return None

        dx = self._device_left + round(x * self._device_width / max(1, pw))
        dy = self._device_top + round(y * self._device_height / max(1, ph))
        dx = max(self._device_left, min(self._device_left + self._device_width - 1, dx))
        dy = max(self._device_top, min(self._device_top + self._device_height - 1, dy))
        return dx, dy

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.key_requested.emit("KEYCODE_BACK")
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            mapped = self._map_to_device(event.position())
            if mapped is not None:
                self._press_point = mapped
                self._press_at = time.monotonic()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_point is not None:
            start = self._press_point
            self._press_point = None
            end = self._map_to_device(event.position())
            if end is not None:
                elapsed_ms = max(50, min(5000, round((time.monotonic() - self._press_at) * 1000)))
                distance = abs(end[0] - start[0]) + abs(end[1] - start[1])
                if distance <= 12 and elapsed_ms >= 650:
                    self.hold_requested.emit(end[0], end[1], elapsed_ms)
                elif distance <= 12:
                    self.tap_requested.emit(*end)
                else:
                    self.swipe_requested.emit(start[0], start[1], end[0], end[1], elapsed_ms)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        special = {
            Qt.Key.Key_Backspace: "KEYCODE_DEL",
            Qt.Key.Key_Return: "KEYCODE_ENTER",
            Qt.Key.Key_Enter: "KEYCODE_ENTER",
            Qt.Key.Key_Escape: "KEYCODE_BACK",
            Qt.Key.Key_Tab: "KEYCODE_TAB",
            Qt.Key.Key_Left: "KEYCODE_DPAD_LEFT",
            Qt.Key.Key_Right: "KEYCODE_DPAD_RIGHT",
            Qt.Key.Key_Up: "KEYCODE_DPAD_UP",
            Qt.Key.Key_Down: "KEYCODE_DPAD_DOWN",
            Qt.Key.Key_Home: "KEYCODE_HOME",
        }
        code = special.get(event.key())
        if code:
            self.key_requested.emit(code)
            event.accept()
            return

        text = event.text()
        if text and text.isprintable():
            self.text_requested.emit(text)
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        mapped = self._map_to_device(event.position())
        if mapped is None:
            super().wheelEvent(event)
            return
        x, y = mapped
        span = max(120, self._device_height // 5)
        direction = 1 if event.angleDelta().y() > 0 else -1
        start_y = max(0, min(self._device_height - 1, y + direction * span // 2))
        end_y = max(0, min(self._device_height - 1, y - direction * span // 2))
        self.swipe_requested.emit(x, start_y, x, end_y, 250)
        event.accept()


class Card(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("card")
        self.layout = QVBoxLayout(self)
        label = QLabel(title)
        label.setObjectName("cardTitle")
        self.layout.addWidget(label)


class WarBotWindow(QMainWindow):
    capture_ready = Signal(object, str, object)
    capture_failed = Signal(str)
    stream_failed = Signal(str)
    stream_metrics_ready = Signal(object)
    manual_input_log = Signal(str)
    health_ready = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("TUGARIN BOTS — Центр управления")
        self.resize(1240, 780)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.runtime_process = QProcess(self)
        self.runtime_process.setProcessChannelMode(QProcess.MergedChannels)
        self.runtime_process.readyReadStandardOutput.connect(self.read_runtime_output)
        self.device_process = QProcess(self)
        self.device_process.setProcessChannelMode(QProcess.MergedChannels)
        self.device_process.readyReadStandardOutput.connect(self.read_device_output)
        self.device_process.finished.connect(self.device_process_finished)
        self.process.readyReadStandardOutput.connect(self.read_process_output)
        self.process.finished.connect(self.process_finished)
        self.capture = None
        self.capture_signature = None
        self.capture_busy = False
        self.frame_stream = None
        self.frame_stream_signature = None
        self.last_bot_frame_mtime = 0.0
        self.capture_ready.connect(self._render_capture)
        self.capture_failed.connect(self._capture_failed)
        self.stream_failed.connect(self._stream_failed)
        self.stream_metrics_ready.connect(self._render_stream_metrics)
        self.manual_input_log.connect(self.append_log)
        self.health_ready.connect(self._render_runtime_health)
        self.health_busy = False
        self.last_health_probe_at = 0.0
        self.last_log_size = 0
        self.paused = False
        self.pending_bot_start = False
        self.build_ui()
        self.load_config()
        self.apply_style()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        header = QHBoxLayout()
        brand = QLabel("TUGARIN BOTS")
        brand.setObjectName("brand")
        header.addWidget(brand)
        self.device_status = QLabel("● Проверка подключения…")
        self.device_status.setObjectName("muted")
        header.addWidget(self.device_status)
        header.addStretch()
        self.start_button = QPushButton("▶  ЗАПУСТИТЬ")
        self.pause_button = QPushButton("Ⅱ  ПАУЗА")
        self.stop_button = QPushButton("■  ОСТАНОВИТЬ")
        self.start_button.setObjectName("primary")
        self.stop_button.setObjectName("danger")
        self.start_button.clicked.connect(self.start_bot)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button.clicked.connect(self.stop_bot)
        header.addWidget(self.start_button)
        header.addWidget(self.pause_button)
        header.addWidget(self.stop_button)
        outer.addLayout(header)

        body = QHBoxLayout()
        self.menu = QListWidget()
        self.menu.setFixedWidth(190)
        self.menu.addItems([
            "Обзор", "Регистрация", "Строительство", "Исследования",
            "Тренировка войск", "Журнал", "Настройки",
        ])
        self.menu.currentRowChanged.connect(self.change_page)
        body.addWidget(self.menu)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        self.pages.addWidget(self.overview_page())
        self.pages.addWidget(self.registration_page())
        for title, text in (
            ("Строительство", "Очереди и приоритеты построек появятся в следующем модуле."),
            ("Исследования", "Здесь будут ветки университета, приоритеты и лимиты ресурсов."),
            ("Тренировка войск", "Здесь будут очереди пехоты, стрелков и кавалерии."),
        ):
            self.pages.addWidget(self.placeholder_page(title, text))
        self.pages.addWidget(self.log_page())
        self.pages.addWidget(self.settings_page())
        self.menu.setCurrentRow(0)

    def overview_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        preview_card = Card("ЭКРАН ANDROID · РУЧНОЕ УПРАВЛЕНИЕ")
        self.preview = InteractivePreview("Ожидание Android backend")
        self.preview.setMinimumSize(430, 600)
        self.preview.setObjectName("preview")
        self.preview.tap_requested.connect(self.preview_tap)
        self.preview.swipe_requested.connect(self.preview_swipe)
        self.preview.hold_requested.connect(self.preview_hold)
        self.preview.key_requested.connect(self.preview_key)
        self.preview.text_requested.connect(self.preview_text)
        preview_card.layout.addWidget(self.preview, 1)
        preview_help = QLabel(
            "Мышь: клик = касание, удержание = long-press, протяжка/колесо = свайп, "
            "правый клик = Назад. Клавиатура и Unicode-текст передаются в Android."
        )
        preview_help.setWordWrap(True)
        preview_help.setObjectName("muted")
        preview_card.layout.addWidget(preview_help)
        layout.addWidget(preview_card, 3)

        side = QVBoxLayout()
        state_card = Card("ТЕКУЩЕЕ СОСТОЯНИЕ")
        grid = QGridLayout()
        self.phase_value = QLabel("—")
        self.step_value = QLabel("—")
        self.name_value = QLabel("—")
        self.created_value = QLabel("0")
        self.cycle_value = QLabel("1")
        self.cycle_created_value = QLabel("0")
        self.stop_reason_value = QLabel("—")
        self.stop_reason_value.setWordWrap(True)
        self.network_value = QLabel("—")
        self.internet_value = QLabel("—")
        self.audio_value = QLabel("—")
        self.stream_value = QLabel("—")
        self.windows_user_value = QLabel(os.environ.get("USERNAME", "—"))
        self.wsa_flavor_value = QLabel("—")
        self.adb_auth_value = QLabel("—")
        self.google_services_value = QLabel("—")
        self.p0_value = QLabel("—")
        self.heartbeat_value = QLabel("—")
        for row, (name, widget) in enumerate((
            ("Режим", self.phase_value), ("Шаг", self.step_value),
            ("Следующее имя", self.name_value), ("Создано всего", self.created_value),
            ("Цикл", self.cycle_value), ("В этом цикле", self.cycle_created_value),
            ("Сеть Android", self.network_value), ("Интернет", self.internet_value),
            ("Аудиосервис", self.audio_value),
            ("Видео", self.stream_value),
            ("Windows SID/User", self.windows_user_value),
            ("WSA flavor", self.wsa_flavor_value),
            ("ADB authorization", self.adb_auth_value),
            ("Google Services", self.google_services_value),
            ("P0 manifest", self.p0_value),
            ("Heartbeat", self.heartbeat_value),
            ("Последняя остановка", self.stop_reason_value),
        )):
            caption = QLabel(name)
            caption.setObjectName("muted")
            grid.addWidget(caption, row, 0)
            grid.addWidget(widget, row, 1)
        state_card.layout.addLayout(grid)
        audio_row = QHBoxLayout()
        volume_down = QPushButton("🔉 −")
        volume_mute = QPushButton("🔇")
        volume_up = QPushButton("🔊 +")
        volume_down.clicked.connect(lambda: self._send_manual_input("volume_down"))
        volume_mute.clicked.connect(lambda: self._send_manual_input("volume_mute"))
        volume_up.clicked.connect(lambda: self._send_manual_input("volume_up"))
        audio_row.addWidget(volume_down)
        audio_row.addWidget(volume_mute)
        audio_row.addWidget(volume_up)
        state_card.layout.addLayout(audio_row)
        side.addWidget(state_card)

        recovery_card = Card("БЫСТРОЕ ВОССТАНОВЛЕНИЕ")
        recovery_row = QHBoxLayout()
        restart_game_button = QPushButton("↻ Игра")
        wake_wsa_button = QPushButton("↻ WSA")
        check_button = QPushButton("✓ Проверить")
        restart_game_button.clicked.connect(self.restart_game)
        wake_wsa_button.clicked.connect(self.start_android_runtime)
        check_button.clicked.connect(self.check_android_status)
        recovery_row.addWidget(restart_game_button)
        recovery_row.addWidget(wake_wsa_button)
        recovery_row.addWidget(check_button)
        recovery_card.layout.addLayout(recovery_row)
        side.addWidget(recovery_card)

        action_card = Card("ПОСЛЕДНИЕ ДЕЙСТВИЯ")
        self.mini_log = QListWidget()
        action_card.layout.addWidget(self.mini_log)
        side.addWidget(action_card, 1)
        layout.addLayout(side, 2)
        return page

    def registration_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        card = Card("ЦИКЛ РЕГИСТРАЦИИ")
        form = QGridLayout()
        self.name_prefix = QLineEdit("Тугарин")
        self.name_prefix.setReadOnly(True)
        self.next_number = QSpinBox()
        self.next_number.setRange(1, 999999)
        self.target_state = QSpinBox()
        self.target_state.setRange(3, 3)
        self.target_state.setValue(3)
        self.characters_per_cycle = QSpinBox()
        self.characters_per_cycle.setRange(1, 20)
        self.characters_per_cycle.setValue(4)
        self.auto_reset_data = QCheckBox(
            "После заданного числа персонажей очистить данные игры и начать новый чистый цикл"
        )
        self.auto_reset_data.setChecked(False)
        self.infinite_cycle = QCheckBox("Повторять цикл непрерывно")
        self.infinite_cycle.setChecked(True)
        self.auto_tutorial = QCheckBox("Обязательное обучение (всегда)")
        self.auto_tutorial.setChecked(True)
        self.auto_tutorial.setEnabled(False)
        fields = (
            ("Шаблон имени", self.name_prefix),
            ("Следующий номер", self.next_number),
            ("Государство", self.target_state),
            ("Персонажей до сброса", self.characters_per_cycle),
        )
        for row, (label, widget) in enumerate(fields):
            form.addWidget(QLabel(label), row, 0)
            form.addWidget(widget, row, 1)
        form.addWidget(self.auto_reset_data, 4, 0, 1, 2)
        form.addWidget(self.infinite_cycle, 5, 0, 1, 2)
        form.addWidget(self.auto_tutorial, 6, 0, 1, 2)
        card.layout.addLayout(form)
        save = QPushButton("Сохранить настройки")
        save.setObjectName("primary")
        save.clicked.connect(self.save_config)
        card.layout.addWidget(save)
        layout.addWidget(card)
        note = QLabel(
            "MVP работает только с государством №3. Автосброс выключен по умолчанию. "
            "Если игра или сервер сообщат о лимите/ограничении, бот остановится и не будет "
            "использовать очистку данных как способ обхода ограничения."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        layout.addStretch()
        return page

    def log_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        card = Card("ЖУРНАЛ ДВИЖКА")
        self.full_log = QListWidget()
        card.layout.addWidget(self.full_log)
        layout.addWidget(card)
        return page

    def settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.developer_mode = QCheckBox("Режим разработчика: показать backend/ADB/PoC настройки")
        self.developer_mode.setChecked(False)
        layout.addWidget(self.developer_mode)
        card = Card("ANDROID BACKEND · РАЗРАБОТЧИК")
        self.backend_card = card
        card.setVisible(False)
        self.developer_mode.toggled.connect(card.setVisible)
        grid = QGridLayout()
        self.backend_mode = QComboBox()
        self.backend_mode.addItem("Windows Subsystem for Android (WSA)", "wsa")
        self.backend_mode.addItem("Native ARM64 emulator", "native_arm64")
        self.backend_mode.addItem("ADB device", "adb")
        self.backend_mode.addItem("Legacy scrcpy (диагностика)", "scrcpy")
        self.android_serial = QLineEdit("127.0.0.1:58526")
        self.backend_mode.currentIndexChanged.connect(self.backend_changed)
        self.adb_path = QLineEdit(os.environ.get("WAR_BOT_ADB", r"C:\Android\Sdk\platform-tools\adb.exe"))
        self.window_title = QLineEdit(bot.SCRCPY_VIDEO_TITLE)
        self.python_path = QLineEdit(consoleless_python(sys.executable))
        grid.addWidget(QLabel("Backend"), 0, 0)
        grid.addWidget(self.backend_mode, 0, 1)
        grid.addWidget(QLabel("ADB serial"), 1, 0)
        grid.addWidget(self.android_serial, 1, 1)
        grid.addWidget(QLabel("adb.exe"), 2, 0)
        grid.addWidget(self.adb_path, 2, 1)
        grid.addWidget(QLabel("Заголовок scrcpy"), 3, 0)
        grid.addWidget(self.window_title, 3, 1)
        grid.addWidget(QLabel("Python"), 4, 0)
        grid.addWidget(self.python_path, 4, 1)
        card.layout.addLayout(grid)

        runtime_row = QHBoxLayout()
        self.start_android_button = QPushButton("▶ Запустить Android")
        self.stop_android_button = QPushButton("■ Остановить Android")
        self.check_android_button = QPushButton("Проверить Android")
        self.start_android_button.clicked.connect(self.start_android_runtime)
        self.stop_android_button.clicked.connect(self.stop_android_runtime)
        self.check_android_button.clicked.connect(self.check_android_status)
        runtime_row.addWidget(self.start_android_button)
        runtime_row.addWidget(self.stop_android_button)
        runtime_row.addWidget(self.check_android_button)
        card.layout.addLayout(runtime_row)

        bootstrap_row = QHBoxLayout()
        self.bootstrap_button = QPushButton("★ Подготовить Android + игру")
        self.bootstrap_button.setObjectName("primary")
        self.bootstrap_button.clicked.connect(self.bootstrap_android_game)
        bootstrap_row.addWidget(self.bootstrap_button)
        card.layout.addLayout(bootstrap_row)

        app_row = QHBoxLayout()
        self.install_game_button = QPushButton("Установить игру")
        self.launch_game_button = QPushButton("▶ Запустить игру")
        self.stop_game_button = QPushButton("■ Остановить игру")
        self.clear_game_button = QPushButton("Очистить данные")
        self.install_game_button.clicked.connect(self.install_game)
        self.launch_game_button.clicked.connect(self.launch_game)
        self.stop_game_button.clicked.connect(self.stop_game)
        self.clear_game_button.clicked.connect(self.clear_game_data)
        app_row.addWidget(self.install_game_button)
        app_row.addWidget(self.launch_game_button)
        app_row.addWidget(self.stop_game_button)
        app_row.addWidget(self.clear_game_button)
        card.layout.addLayout(app_row)

        layout.addWidget(card)
        note = QLabel(
            "Основной Windows-only режим — WSA. Native ARM64 сохранён как "
            "диагностический PoC; scrcpy оставлен только как legacy-источник кадров."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        layout.addStretch()
        return page

    def placeholder_page(self, title, text):
        page = QWidget()
        layout = QVBoxLayout(page)
        card = Card(title.upper())
        label = QLabel(text)
        label.setWordWrap(True)
        card.layout.addWidget(label)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def change_page(self, index):
        if index >= 0:
            self.pages.setCurrentIndex(index)

    def backend_changed(self):
        mode = str(self.backend_mode.currentData() or "wsa")
        current = self.android_serial.text().strip()
        if mode == "wsa" and current in ("", "127.0.0.1:5561"):
            self.android_serial.setText("127.0.0.1:58526")
        elif mode == "native_arm64" and current in ("", "127.0.0.1:58526"):
            self.android_serial.setText("127.0.0.1:5561")
        is_runtime = mode in ("wsa", "native_arm64")
        self.start_android_button.setEnabled(is_runtime)
        self.stop_android_button.setEnabled(mode == "native_arm64")

    def write_control(self, paused=False, stop=False):
        atomic_json(CONTROL_FILE, {"paused": paused, "stop": stop})

    def _launch_bot_process(self):
        self.write_control(False, False)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("WAR_BOT_BACKEND", str(self.backend_mode.currentData() or "wsa"))
        env.insert("WAR_BOT_ANDROID_SERIAL", self.android_serial.text().strip() or "127.0.0.1:58526")
        env.insert("WAR_BOT_ADB", self.adb_path.text().strip())
        env.insert("WAR_BOT_SCRCPY_TITLE", self.window_title.text().strip())
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(ROOT)
        self.process.start(consoleless_python(self.python_path.text().strip()), [os.path.join(ROOT, "bot.py")])
        self.start_button.setEnabled(False)

    def start_bot(self):
        self.save_config(show_message=False)
        if self.process.state() != QProcess.NotRunning:
            self.paused = False
            self.pause_button.setText("Ⅱ  ПАУЗА")
            return

        mode = str(self.backend_mode.currentData() or "wsa")
        if mode in ("native_arm64", "wsa"):
            self.pending_bot_start = True
            self.start_button.setEnabled(False)
            self.start_button.setText("●  ПОДГОТОВКА ANDROID + ИГРЫ…")
            started = self.run_device_cli(
                "bootstrap",
                ["--output", os.path.join(ROOT, "debug", "bootstrap-frame.png")],
                quiet_busy=True,
            )
            if not started:
                self.pending_bot_start = False
                self.start_button.setEnabled(True)
            return

        self._launch_bot_process()

    def start_android_runtime(self):
        if self.runtime_process.state() != QProcess.NotRunning:
            return
        mode = str(self.backend_mode.currentData() or "wsa")
        if mode not in ("native_arm64", "wsa"):
            QMessageBox.information(self, "TUGARIN BOTS", "Выберите WSA или Native ARM64 emulator.")
            return
        if mode == "wsa":
            self.run_device_cli("start-runtime")
            return
        self.save_config(show_message=False)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("WAR_BOT_ADB", self.adb_path.text().strip())
        env.insert("WAR_BOT_ANDROID_SERIAL", self.android_serial.text().strip() or "127.0.0.1:5561")
        self.runtime_process.setProcessEnvironment(env)
        self.runtime_process.setWorkingDirectory(ROOT)
        self.runtime_process.start(
            consoleless_python(self.python_path.text().strip()),
            [os.path.join(ROOT, "native_arm64_poc.py"), "start"],
        )

    def stop_android_runtime(self):
        if (self.backend_mode.currentData() or "wsa") == "wsa":
            QMessageBox.information(
                self, "TUGARIN BOTS", "Жизненным циклом WSA управляет Windows; остановка не требуется."
            )
            return
        QProcess.startDetached(
            consoleless_python(self.python_path.text().strip()),
            [os.path.join(ROOT, "native_arm64_poc.py"), "stop"],
            ROOT,
        )

    def read_runtime_output(self):
        raw = bytes(self.runtime_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            self.append_log("[Android] " + line)

    def _device_cli_args(self, action, extra=None):
        args = [
            os.path.join(ROOT, "warbot_cli.py"),
            action,
            "--backend", str(self.backend_mode.currentData() or "wsa"),
            "--serial", self.android_serial.text().strip() or "127.0.0.1:58526",
        ]
        adb_path = self.adb_path.text().strip()
        if adb_path:
            args += ["--adb", adb_path]
        if extra:
            args += list(extra)
        return args

    def run_device_cli(self, action, extra=None, quiet_busy=False):
        if self.device_process.state() != QProcess.NotRunning:
            if not quiet_busy:
                QMessageBox.information(self, "TUGARIN BOTS", "Предыдущая Android-команда ещё выполняется.")
            return False
        self.save_config(show_message=False)
        self.device_process.setWorkingDirectory(ROOT)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self.device_process.setProcessEnvironment(env)
        self.device_process.start(
            consoleless_python(self.python_path.text().strip()),
            self._device_cli_args(action, extra),
        )
        return True

    def check_android_status(self):
        self.run_device_cli("status")

    def bootstrap_android_game(self):
        if (self.backend_mode.currentData() or "wsa") not in ("native_arm64", "wsa"):
            QMessageBox.information(
                self, "TUGARIN BOTS", "Подготовка доступна для WSA или Native ARM64 emulator."
            )
            return
        self.run_device_cli(
            "bootstrap",
            ["--output", os.path.join(ROOT, "debug", "bootstrap-frame.png")],
        )

    def install_game(self):
        if (self.backend_mode.currentData() or "wsa") not in ("native_arm64", "wsa"):
            QMessageBox.information(
                self,
                "TUGARIN BOTS",
                "Установка игры через GUI разрешена только для управляемых "
                "runtime WSA и Native ARM64.",
            )
            return
        self.run_device_cli("install-game")

    def launch_game(self):
        self.run_device_cli("launch-game")

    def restart_game(self):
        self.run_device_cli("restart-game")

    def stop_game(self):
        self.run_device_cli("stop-game")

    def clear_game_data(self):
        answer = QMessageBox.question(
            self,
            "TUGARIN BOTS",
            "Очистить данные com.got.globalru и синхронно начать новый чистый цикл? "
            "Счётчик Тугарин<N> на ПК будет сохранён.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.run_device_cli("clean-start", ["--yes"])

    def _manual_backend(self):
        if isinstance(self.capture, BackendCapture):
            return self.capture.backend
        mode = str(self.backend_mode.currentData() or "wsa")
        if mode == "scrcpy":
            raise RuntimeError("Ручной ввод внутри GUI доступен для Android backend, а не legacy scrcpy.")
        return create_backend(
            mode,
            serial=self.android_serial.text().strip() or "127.0.0.1:58526",
            adb_path=self.adb_path.text().strip(),
        )

    def _pause_for_manual_control(self):
        if bot_pid() is None:
            return
        control = read_json(CONTROL_FILE, {})
        if not bool(control.get("paused", False)):
            self.paused = True
            self.write_control(True, False)
            self.pause_button.setText("▶  ПРОДОЛЖИТЬ")
            self.manual_input_log.emit(
                "[Manual] Автоматизация поставлена на паузу перед ручным управлением."
            )

    def _send_manual_input(self, kind, *args):
        self._pause_for_manual_control()

        def worker():
            try:
                backend = self._manual_backend()
                if kind == "tap":
                    backend.tap(*args)
                    detail = f"касание {args[0]},{args[1]}"
                elif kind == "swipe":
                    backend.swipe(*args)
                    detail = (
                        f"свайп {args[0]},{args[1]} → {args[2]},{args[3]} "
                        f"({args[4]} мс)"
                    )
                elif kind == "hold":
                    backend.hold(*args)
                    detail = f"удержание {args[0]},{args[1]} ({args[2]} мс)"
                elif kind == "key":
                    backend.keyevent(args[0])
                    detail = f"клавиша {args[0]}"
                elif kind == "text":
                    backend.input_text(args[0])
                    detail = f"текст {args[0]!r}"
                elif kind == "volume_up":
                    backend.volume_up()
                    detail = "громкость +"
                elif kind == "volume_down":
                    backend.volume_down()
                    detail = "громкость -"
                elif kind == "volume_mute":
                    backend.volume_mute()
                    detail = "mute"
                else:
                    raise RuntimeError(f"Неизвестный ручной ввод: {kind}")
                self.manual_input_log.emit("[Manual] " + detail)
            except Exception as error:
                self.manual_input_log.emit(f"[Manual] Ошибка ввода: {error}")

        threading.Thread(
            target=worker,
            name="tugarin-bots-manual-input",
            daemon=True,
        ).start()

    def preview_tap(self, x, y):
        self._send_manual_input("tap", int(x), int(y))

    def preview_swipe(self, x1, y1, x2, y2, duration_ms):
        self._send_manual_input(
            "swipe",
            int(x1), int(y1), int(x2), int(y2), int(duration_ms),
        )

    def preview_hold(self, x, y, duration_ms):
        self._send_manual_input("hold", int(x), int(y), int(duration_ms))

    def preview_key(self, code):
        self._send_manual_input("key", str(code))

    def preview_text(self, value):
        self._send_manual_input("text", str(value))

    def read_device_output(self):
        raw = bytes(self.device_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            self.append_log("[Device] " + line)

    def device_process_finished(self, exit_code, _exit_status):
        if not self.pending_bot_start:
            return
        self.pending_bot_start = False
        if exit_code == 0:
            self.append_log("[Device] Android и игра готовы — запускаю TUGARIN BOTS.")
            self._launch_bot_process()
        else:
            self.start_button.setEnabled(True)
            self.start_button.setText("▶  ЗАПУСТИТЬ")
            self.append_log(
                f"[Device] Подготовка завершилась ошибкой {exit_code}; бот не запущен."
            )

    def toggle_pause(self):
        control = read_json(CONTROL_FILE, {})
        self.paused = not bool(control.get("paused", False))
        self.write_control(self.paused, False)
        self.pause_button.setText("▶  ПРОДОЛЖИТЬ" if self.paused else "Ⅱ  ПАУЗА")

    def stop_bot(self):
        self.write_control(False, True)
        self.paused = False
        self.pause_button.setText("Ⅱ  ПАУЗА")

        if self.pending_bot_start:
            self.pending_bot_start = False
            if self.device_process.state() != QProcess.NotRunning:
                self.device_process.kill()
            # Stop only the TUGARIN BOTS runtime identified by native_arm64_poc.
            QProcess.startDetached(
                consoleless_python(self.python_path.text().strip()),
                [os.path.join(ROOT, "native_arm64_poc.py"), "stop"],
                ROOT,
            )
            self.start_button.setEnabled(True)
            self.start_button.setText("▶  ЗАПУСТИТЬ")
            self.append_log("[Device] Подготовка Android отменена пользователем.")

        if self.process.state() != QProcess.NotRunning:
            QTimer.singleShot(3000, self.force_stop_if_needed)

    def force_stop_if_needed(self):
        if self.process.state() != QProcess.NotRunning:
            self.process.terminate()

    def process_finished(self):
        self.start_button.setEnabled(True)

    def read_process_output(self):
        raw = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            self.append_log(line)

    def append_log(self, line):
        if not line.strip():
            return
        self.full_log.addItem(line)
        self.mini_log.addItem(line)
        while self.full_log.count() > 500:
            self.full_log.takeItem(0)
        while self.mini_log.count() > 8:
            self.mini_log.takeItem(0)
        self.full_log.scrollToBottom()
        self.mini_log.scrollToBottom()

    def refresh(self):
        self.refresh_state()
        self.refresh_capture()
        self.refresh_runtime_health()
        self.refresh_log_file()

    def refresh_runtime_health(self):
        if self.health_busy or (time.monotonic() - self.last_health_probe_at) < 60.0:
            return
        mode = str(self.backend_mode.currentData() or "wsa")
        if mode == "scrcpy":
            return

        self.health_busy = True
        self.last_health_probe_at = time.monotonic()

        def worker():
            try:
                backend = self._manual_backend()
                health = backend.health().to_dict()
                health["windows_user"] = os.environ.get("USERNAME", "—")
                package_pointer = r"C:\warbot_wsa\package-path.txt"
                try:
                    package_path = open(package_pointer, encoding="utf-8-sig").read().strip()
                except OSError:
                    package_path = ""
                health["wsa_flavor"] = "GApps" if "gapps" in package_path.lower() else "NoGApps"
                health["adb_authorized"] = bool(health.get("ready"))
                try:
                    packages = backend.shell(["pm", "list", "packages"], timeout=20)
                except Exception:
                    packages = ""
                required_google = (
                    "com.google.android.gms",
                    "com.google.android.gsf",
                    "com.android.vending",
                )
                health["google_services"] = all(
                    f"package:{name}" in packages for name in required_google
                )
                latest_p0 = read_json(r"C:\warbot_wsa\reports\LATEST-LOCAL.json", {})
                health["p0_state"] = str(latest_p0.get("state", "—"))
                self.health_ready.emit(health)
            except Exception as error:
                self.health_ready.emit({"error": str(error)})

        threading.Thread(
            target=worker,
            name="tugarin-bots-health",
            daemon=True,
        ).start()

    def _render_runtime_health(self, health):
        self.health_busy = False
        if health.get("error"):
            self.network_value.setText("недоступно")
            self.internet_value.setText("недоступно")
            self.audio_value.setText("недоступно")
            return

        ready = bool(health.get("ready"))
        self.network_value.setText("готово" if health.get("network_ready") else "нет")
        self.internet_value.setText("доступен" if health.get("internet_reachable") else "нет")
        self.audio_value.setText("готов" if health.get("audio_service_ready") else "нет")
        self.windows_user_value.setText(str(health.get("windows_user", "—")))
        self.wsa_flavor_value.setText(str(health.get("wsa_flavor", "—")))
        self.adb_auth_value.setText("авторизован" if health.get("adb_authorized") else "нет")
        self.google_services_value.setText("готовы" if health.get("google_services") else "нет")
        self.p0_value.setText(str(health.get("p0_state", "—")))
        if ready:
            self.device_status.setToolTip(
                "Android готов; сеть/интернет/аудио контролируются TUGARIN BOTS."
            )

    def refresh_state(self):
        state = read_json(bot.STATE_FILE, {})
        phase = state.get("phase", "—")
        self.phase_value.setText(PHASE_NAMES.get(phase, phase))
        self.step_value.setText(str(state.get("step", "—")))
        self.name_value.setText(f"{self.name_prefix.text()} {state.get('next_nickname', 1)}")
        self.created_value.setText(str(state.get("characters_created", 0)))
        self.cycle_value.setText(str(state.get("current_cycle", 1)))
        per_cycle = max(1, int(state.get("characters_per_cycle", 4)))
        cycle_created = int(state.get("characters_created_cycle", 0))
        self.cycle_created_value.setText(f"{cycle_created} / {per_cycle}")
        self.stop_reason_value.setText(str(state.get("last_stop_reason", "") or "—"))
        heartbeat = read_json(HEARTBEAT_FILE, {})
        written_at = str(heartbeat.get("written_at", "") or "")
        heartbeat_text = str(heartbeat.get("status", "—") or "—")
        if written_at:
            try:
                written = datetime.fromisoformat(written_at.replace("Z", "+00:00"))
                now = datetime.now(written.tzinfo) if written.tzinfo else datetime.now()
                age = max(0.0, (now - written).total_seconds())
                heartbeat_text += f" · {age:.1f} сек"
            except ValueError:
                pass
        self.heartbeat_value.setText(heartbeat_text)
        running_pid = bot_pid()
        if self.pending_bot_start:
            self.start_button.setEnabled(False)
            self.start_button.setText("●  ПОДГОТОВКА ANDROID + ИГРЫ…")
        elif running_pid is not None:
            self.start_button.setEnabled(False)
            self.start_button.setText(f"●  БОТ РАБОТАЕТ  PID {running_pid}")
        else:
            self.start_button.setEnabled(True)
            self.start_button.setText("▶  ЗАПУСТИТЬ")

    def _render_capture(self, phone, title, rect):
        self.capture_busy = False
        rgb = cv2.cvtColor(phone, cv2.COLOR_BGR2RGB)
        image = QImage(
            rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
            QImage.Format_RGB888,
        ).copy()
        pixmap = QPixmap.fromImage(image).scaled(
            self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.preview.setPixmap(pixmap)
        self.preview.set_device_size(
            rect["width"], rect["height"],
            rect.get("left", 0), rect.get("top", 0),
        )
        self.device_status.setText(
            f"● {title} · {rect['width']}×{rect['height']}"
        )
        self.device_status.setStyleSheet("color: #55d98b")

    def _capture_failed(self, message):
        self.capture_busy = False
        if self.capture is not None:
            try:
                self.capture.close()
            except Exception:
                pass
            self.capture = None
        self.capture_signature = None
        self.device_status.setText(f"● Android недоступен: {message}")
        self.device_status.setStyleSheet("color: #ff7185")

    def _stop_frame_stream(self):
        stream = self.frame_stream
        self.frame_stream = None
        self.frame_stream_signature = None
        if stream is not None:
            stream.stop()

    def _stream_failed(self, message):
        self.device_status.setText(f"● Видео: {message}")
        self.device_status.setStyleSheet("color: #ffb454")

    def _render_stream_metrics(self, metrics):
        self.stream_value.setText(
            f"{metrics.get('transport', 'video')} · "
            f"{metrics.get('fps', 0.0):.1f} FPS · "
            f"{metrics.get('latency_ms', 0.0):.0f} ms"
        )

    @staticmethod
    def _crop_for_render(frame, rect):
        left, top, width, height = bot.detect_content_rect(frame)
        phone, _, _ = bot.crop_phone(frame)
        scale_x = rect["width"] / max(1, frame.shape[1])
        scale_y = rect["height"] / max(1, frame.shape[0])
        viewport = dict(rect)
        viewport.update(
            left=round(left * scale_x),
            top=round(top * scale_y),
            width=round(width * scale_x),
            height=round(height * scale_y),
        )
        return phone, viewport

    def refresh_capture(self):
        mode = str(self.backend_mode.currentData() or "wsa")
        serial = self.android_serial.text().strip() or "127.0.0.1:58526"
        adb_path = self.adb_path.text().strip()
        signature = (mode, serial, adb_path, self.window_title.text().strip())

        # The bot owns the Android video transport while it is running.  The
        # GUI consumes its replaceable 2 FPS JPEG mailbox instead of starting
        # a second MediaCodec encoder and FFmpeg decoder inside WSA.
        if bot_pid() is not None and mode != "scrcpy":
            self._stop_frame_stream()
            live_path = getattr(bot, "LIVE_FRAME_FILE", "")
            try:
                modified = os.path.getmtime(live_path)
                if modified > self.last_bot_frame_mtime:
                    phone = cv2.imread(live_path)
                    if phone is not None:
                        self.last_bot_frame_mtime = modified
                        self._render_capture(
                            phone,
                            f"bot-shared:{serial}",
                            {"width": phone.shape[1], "height": phone.shape[0]},
                        )
                        self.stream_value.setText("bot-shared-jpeg · 2.0 FPS")
            except OSError:
                pass
            return

        # Legacy desktop-window capture remains diagnostics-only.
        if mode == "scrcpy":
            self._stop_frame_stream()
            try:
                if self.capture is None or signature != self.capture_signature:
                    if self.capture is not None:
                        self.capture.close()
                    bot.SCRCPY_VIDEO_TITLE = (
                        self.window_title.text().strip() or bot.SCRCPY_VIDEO_TITLE
                    )
                    self.capture = bot.ScrcpyCapture()
                    self.capture_signature = signature
                frame, title, rect = self.capture.grab()
                phone, viewport = self._crop_for_render(frame, rect)
                self._render_capture(phone, title, viewport)
                self.stream_value.setText("legacy scrcpy")
            except Exception as error:
                self._capture_failed(str(error))
            return

        if self.capture is not None:
            try:
                self.capture.close()
            except Exception:
                pass
            self.capture = None
            self.capture_signature = None

        if (
            self.frame_stream is not None
            and self.frame_stream_signature == signature
            and self.frame_stream.running
        ):
            active_transport = getattr(self.frame_stream.capture, "transport_name", "")
            window_ready = (
                mode == "wsa" and
                len(bot.WsaGameWindowCapture._visible_scrcpy_windows()) == 1
            )
            if active_transport == "wsa-window" or not window_ready:
                return

        self._stop_frame_stream()
        try:
            backend = create_backend(mode, serial=serial, adb_path=adb_path)
            capture = None
            if mode == "wsa":
                candidate = None
                try:
                    candidate = bot.WsaGameWindowCapture()
                    candidate.grab()
                    capture = candidate
                except Exception:
                    if candidate is not None:
                        candidate.close()
            if capture is None:
                capture = create_preview_capture(backend)

            def on_frame(frame, title, rect, metrics):
                phone, viewport = self._crop_for_render(frame, rect)
                self.capture_ready.emit(phone, title, viewport)
                self.stream_metrics_ready.emit(metrics.to_dict())

            stream = ContinuousFrameStream(
                capture,
                on_frame=on_frame,
                on_error=lambda error: self.stream_failed.emit(error),
                target_fps=30.0 if getattr(capture, "transport_name", "") == "wsa-window" else 15.0,
                transport="preview",
            )
            self.frame_stream = stream
            self.frame_stream_signature = signature
            stream.start()
        except Exception as error:
            self._stop_frame_stream()
            self._capture_failed(str(error))

    def refresh_log_file(self):
        try:
            size = os.path.getsize(LOG_FILE)
            if size == self.last_log_size:
                return
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as stream:
                lines = stream.readlines()[-12:]
            self.last_log_size = size
            existing = {self.mini_log.item(i).text() for i in range(self.mini_log.count())}
            for line in lines:
                line = line.rstrip()
                if line and line not in existing:
                    self.append_log(line)
        except OSError:
            pass

    def load_config(self):
        config = read_json(GUI_CONFIG_FILE, {})
        state = read_json(bot.STATE_FILE, {})
        self.name_prefix.setText(config.get("name_prefix", "Тугарин"))
        self.next_number.setValue(int(state.get("next_nickname", config.get("next_number", 1))))
        self.target_state.setValue(int(state.get("target_state", config.get("target_state", 3))))
        self.characters_per_cycle.setValue(
            int(state.get("characters_per_cycle", config.get("characters_per_cycle", 4)))
        )
        self.auto_reset_data.setChecked(
            bool(state.get("auto_reset_data", config.get("auto_reset_data", False)))
        )
        self.infinite_cycle.setChecked(
            bool(state.get("repeat_cycles", config.get("infinite_cycle", True)))
        )
        self.auto_tutorial.setChecked(bool(config.get("auto_tutorial", True)))
        backend = config.get("backend", "wsa")
        index = self.backend_mode.findData(backend)
        self.backend_mode.setCurrentIndex(index if index >= 0 else 0)
        self.android_serial.setText(config.get("android_serial", "127.0.0.1:58526"))
        self.adb_path.setText(config.get("adb_path", os.environ.get("WAR_BOT_ADB", r"C:\Android\Sdk\platform-tools\adb.exe")))
        self.window_title.setText(config.get("window_title", bot.SCRCPY_VIDEO_TITLE))

    def save_config(self, show_message=True):
        config = {
            "name_prefix": self.name_prefix.text().strip() or "Тугарин",
            "next_number": self.next_number.value(),
            "target_state": self.target_state.value(),
            "characters_per_cycle": self.characters_per_cycle.value(),
            "auto_reset_data": self.auto_reset_data.isChecked(),
            "infinite_cycle": self.infinite_cycle.isChecked(),
            "auto_tutorial": self.auto_tutorial.isChecked(),
            "backend": self.backend_mode.currentData() or "wsa",
            "android_serial": self.android_serial.text().strip() or "127.0.0.1:58526",
            "adb_path": self.adb_path.text().strip(),
            "window_title": self.window_title.text().strip() or bot.SCRCPY_VIDEO_TITLE,
        }
        atomic_json(GUI_CONFIG_FILE, config)
        try:
            state = bot.load_state()
        except RuntimeError as error:
            if show_message:
                QMessageBox.critical(
                    self,
                    "TUGARIN BOTS",
                    "state.json повреждён; настройки состояния не перезаписаны.\n" + str(error),
                )
            return
        state["target_state"] = config["target_state"]
        state["characters_per_cycle"] = config["characters_per_cycle"]
        state["auto_reset_data"] = config["auto_reset_data"]
        state["repeat_cycles"] = config["infinite_cycle"]
        if int(state.get("characters_created", 0)) == 0:
            state["next_nickname"] = config["next_number"]
        bot.save_state(state)
        if show_message:
            QMessageBox.information(self, "TUGARIN BOTS", "Настройки сохранены.")

    def closeEvent(self, event):
        self.timer.stop()
        self._stop_frame_stream()
        if self.capture is not None:
            self.capture.close()
            self.capture = None
        event.accept()

    def apply_style(self):
        self.setStyleSheet("""
            QWidget { background: #10141d; color: #e9edf6; font: 10pt 'Segoe UI'; }
            QMainWindow { background: #10141d; }
            #brand { font-size: 20pt; font-weight: 800; color: #74d6ff; padding: 8px; }
            #muted { color: #8994a8; }
            #card { background: #171d28; border: 1px solid #273043; border-radius: 10px; }
            #cardTitle { color: #94a2b8; font-size: 9pt; font-weight: 700; padding: 4px; }
            #preview { background: #080b11; border-radius: 7px; color: #667085; }
            QListWidget { background: #141a24; border: 1px solid #273043; border-radius: 8px; padding: 4px; }
            QListWidget::item { padding: 10px; border-radius: 6px; }
            QListWidget::item:selected { background: #25334b; color: #74d6ff; }
            QPushButton { background: #273043; border: 0; border-radius: 7px; padding: 9px 14px; font-weight: 700; }
            QPushButton:hover { background: #344057; }
            QPushButton:disabled { color: #657086; }
            #primary { background: #168fbd; color: white; }
            #primary:hover { background: #1aa6d9; }
            #danger { background: #743343; color: #ffdce3; }
            QLineEdit, QSpinBox, QComboBox { background: #0f141d; border: 1px solid #344057; border-radius: 6px; padding: 7px; }
            QCheckBox { padding: 5px; }
        """)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("TUGARIN BOTS")
    instance_lock = QLockFile(os.path.join(tempfile.gettempdir(), "tugarin-bots-gui.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(100):
        QMessageBox.information(
            None,
            "TUGARIN BOTS",
            "TUGARIN BOTS уже запущен. Второй экземпляр не будет открыт.",
        )
        return 0
    window = WarBotWindow()
    window.show()

    smoke_seconds = 0.0
    try:
        smoke_seconds = float(os.environ.get("TUGARIN_GUI_HOST_SMOKE_SECONDS", "0") or 0)
    except ValueError:
        smoke_seconds = 0.0

    if smoke_seconds > 0:
        smoke_seconds = max(3.0, min(60.0, smoke_seconds))
        report_path = os.environ.get(
            "TUGARIN_GUI_HOST_SMOKE_REPORT",
            os.path.join(ROOT, "debug", "gui-host-smoke.json"),
        )

        wsa_index = window.backend_mode.findData("wsa")
        if wsa_index >= 0:
            window.backend_mode.setCurrentIndex(wsa_index)
        window.android_serial.setText("127.0.0.1:58526")
        window.refresh_capture()

        def finish_host_smoke():
            pixmap = window.preview.pixmap()
            has_frame = bool(pixmap is not None and not pixmap.isNull())
            stream_text = window.stream_value.text()
            status_text = window.device_status.text()
            scrcpy_h264 = "scrcpy-h264" in stream_text
            report = {
                "pass": bool(has_frame and scrcpy_h264),
                "has_rendered_frame": has_frame,
                "stream": stream_text,
                "device_status": status_text,
                "frame_stream_running": bool(
                    window.frame_stream is not None and window.frame_stream.running
                ),
                "backend": "wsa",
                "serial": "127.0.0.1:58526",
            }
            os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
            atomic_json(report_path, report)
            window.close()
            app.exit(0 if report["pass"] else 8)

        QTimer.singleShot(round(smoke_seconds * 1000), finish_host_smoke)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
