import json
import os
import sys
from datetime import datetime

import cv2
from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

import bot
from device_backend import BackendCapture, create_backend


ROOT = os.path.dirname(os.path.abspath(__file__))
CONTROL_FILE = os.path.join(ROOT, "control.json")
GUI_CONFIG_FILE = os.path.join(ROOT, "gui_config.json")
LOG_FILE = os.path.join(ROOT, "logs", "bot.log")
PID_FILE = os.path.join(ROOT, "bot.pid")

PHASE_NAMES = {
    "rename_governor": "Переименование губернатора",
    "create_character": "Создание персонажа",
    "tutorial_new_character": "Обязательное обучение",
    "reset_cycle": "Сброс данных и новый цикл",
}


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


class Card(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("card")
        self.layout = QVBoxLayout(self)
        label = QLabel(title)
        label.setObjectName("cardTitle")
        self.layout.addWidget(label)


class WarBotWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WAR BOT — Центр управления")
        self.resize(1240, 780)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.runtime_process = QProcess(self)
        self.runtime_process.setProcessChannelMode(QProcess.MergedChannels)
        self.runtime_process.readyReadStandardOutput.connect(self.read_runtime_output)
        self.device_process = QProcess(self)
        self.device_process.setProcessChannelMode(QProcess.MergedChannels)
        self.device_process.readyReadStandardOutput.connect(self.read_device_output)
        self.process.readyReadStandardOutput.connect(self.read_process_output)
        self.process.finished.connect(self.process_finished)
        self.capture = None
        self.capture_signature = None
        self.last_log_size = 0
        self.paused = False
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
        brand = QLabel("WAR BOT")
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
        preview_card = Card("ЭКРАН ТЕЛЕФОНА")
        self.preview = QLabel("Ожидание Android backend")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(430, 600)
        self.preview.setObjectName("preview")
        preview_card.layout.addWidget(self.preview, 1)
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
        for row, (name, widget) in enumerate((
            ("Режим", self.phase_value), ("Шаг", self.step_value),
            ("Следующее имя", self.name_value), ("Создано всего", self.created_value),
            ("Цикл", self.cycle_value), ("В этом цикле", self.cycle_created_value),
            ("Последняя остановка", self.stop_reason_value),
        )):
            caption = QLabel(name)
            caption.setObjectName("muted")
            grid.addWidget(caption, row, 0)
            grid.addWidget(widget, row, 1)
        state_card.layout.addLayout(grid)
        side.addWidget(state_card)

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
        self.auto_tutorial = QCheckBox("Проходить обязательное обучение")
        self.auto_tutorial.setChecked(True)
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
        card = Card("ANDROID BACKEND")
        grid = QGridLayout()
        self.backend_mode = QComboBox()
        self.backend_mode.addItem("Native ARM64 emulator", "native_arm64")
        self.backend_mode.addItem("ADB device", "adb")
        self.backend_mode.addItem("Legacy scrcpy (диагностика)", "scrcpy")
        self.android_serial = QLineEdit("127.0.0.1:5561")
        self.adb_path = QLineEdit(os.environ.get("WAR_BOT_ADB", r"C:\Android\Sdk\platform-tools\adb.exe"))
        self.window_title = QLineEdit(bot.SCRCPY_VIDEO_TITLE)
        self.python_path = QLineEdit(sys.executable)
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
        self.start_android_button = QPushButton("▶ Запустить ARM64 Android")
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
            "Основной режим — Native ARM64. ADB device нужен для диагностики уже "
            "загруженного Android; scrcpy оставлен только как legacy-источник кадров."
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

    def write_control(self, paused=False, stop=False):
        atomic_json(CONTROL_FILE, {"paused": paused, "stop": stop})

    def start_bot(self):
        self.save_config(show_message=False)
        self.write_control(False, False)
        if self.process.state() != QProcess.NotRunning:
            self.paused = False
            self.pause_button.setText("Ⅱ  ПАУЗА")
            return
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("WAR_BOT_BACKEND", str(self.backend_mode.currentData() or "native_arm64"))
        env.insert("WAR_BOT_ANDROID_SERIAL", self.android_serial.text().strip() or "127.0.0.1:5561")
        env.insert("WAR_BOT_ADB", self.adb_path.text().strip())
        env.insert("WAR_BOT_SCRCPY_TITLE", self.window_title.text().strip())
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(ROOT)
        self.process.start(self.python_path.text().strip(), [os.path.join(ROOT, "bot.py")])
        self.start_button.setEnabled(False)

    def start_android_runtime(self):
        if self.runtime_process.state() != QProcess.NotRunning:
            return
        if (self.backend_mode.currentData() or "native_arm64") != "native_arm64":
            QMessageBox.information(self, "WAR BOT", "Выберите Native ARM64 emulator.")
            return
        self.save_config(show_message=False)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("WAR_BOT_ADB", self.adb_path.text().strip())
        env.insert("WAR_BOT_ANDROID_SERIAL", self.android_serial.text().strip() or "127.0.0.1:5561")
        self.runtime_process.setProcessEnvironment(env)
        self.runtime_process.setWorkingDirectory(ROOT)
        self.runtime_process.start(
            self.python_path.text().strip(),
            [os.path.join(ROOT, "native_arm64_poc.py"), "start"],
        )

    def stop_android_runtime(self):
        QProcess.startDetached(
            self.python_path.text().strip(),
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
            "--backend", str(self.backend_mode.currentData() or "native_arm64"),
            "--serial", self.android_serial.text().strip() or "127.0.0.1:5561",
        ]
        adb_path = self.adb_path.text().strip()
        if adb_path:
            args += ["--adb", adb_path]
        if extra:
            args += list(extra)
        return args

    def run_device_cli(self, action, extra=None):
        if self.device_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "WAR BOT", "Предыдущая Android-команда ещё выполняется.")
            return
        self.save_config(show_message=False)
        self.device_process.setWorkingDirectory(ROOT)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self.device_process.setProcessEnvironment(env)
        self.device_process.start(
            self.python_path.text().strip(),
            self._device_cli_args(action, extra),
        )

    def check_android_status(self):
        self.run_device_cli("status")

    def bootstrap_android_game(self):
        if (self.backend_mode.currentData() or "native_arm64") != "native_arm64":
            QMessageBox.information(
                self, "WAR BOT", "Подготовка доступна только для Native ARM64 emulator."
            )
            return
        self.run_device_cli(
            "bootstrap",
            ["--output", os.path.join(ROOT, "debug", "bootstrap-frame.png")],
        )

    def install_game(self):
        if (self.backend_mode.currentData() or "native_arm64") != "native_arm64":
            QMessageBox.information(
                self,
                "WAR BOT",
                "Установка игры через GUI разрешена только для Native ARM64, "
                "чтобы обязательный ARM64 gate нельзя было случайно обойти.",
            )
            return
        self.run_device_cli("install-game")

    def launch_game(self):
        self.run_device_cli("launch-game")

    def stop_game(self):
        self.run_device_cli("stop-game")

    def clear_game_data(self):
        answer = QMessageBox.question(
            self,
            "WAR BOT",
            "Очистить все данные com.got.globalru? Это сбросит локальное состояние игры.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.run_device_cli("clear-game-data", ["--yes"])

    def read_device_output(self):
        raw = bytes(self.device_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            self.append_log("[Device] " + line)

    def toggle_pause(self):
        control = read_json(CONTROL_FILE, {})
        self.paused = not bool(control.get("paused", False))
        self.write_control(self.paused, False)
        self.pause_button.setText("▶  ПРОДОЛЖИТЬ" if self.paused else "Ⅱ  ПАУЗА")

    def stop_bot(self):
        self.write_control(False, True)
        self.paused = False
        self.pause_button.setText("Ⅱ  ПАУЗА")
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
        self.refresh_log_file()

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
        running_pid = bot_pid()
        self.start_button.setEnabled(running_pid is None)
        if running_pid is not None:
            self.start_button.setText(f"●  БОТ РАБОТАЕТ  PID {running_pid}")
        else:
            self.start_button.setText("▶  ЗАПУСТИТЬ")

    def refresh_capture(self):
        mode = str(self.backend_mode.currentData() or "native_arm64")
        serial = self.android_serial.text().strip() or "127.0.0.1:5561"
        adb_path = self.adb_path.text().strip()
        signature = (mode, serial, adb_path, self.window_title.text().strip())
        try:
            if self.capture is None or signature != self.capture_signature:
                if self.capture is not None:
                    self.capture.close()
                if mode == "scrcpy":
                    bot.SCRCPY_VIDEO_TITLE = self.window_title.text().strip() or bot.SCRCPY_VIDEO_TITLE
                    self.capture = bot.ScrcpyCapture()
                else:
                    backend = create_backend(mode, serial=serial, adb_path=adb_path)
                    self.capture = BackendCapture(backend)
                self.capture_signature = signature
            frame, title, rect = self.capture.grab()
            phone, _, _ = bot.crop_phone(frame)
            rgb = cv2.cvtColor(phone, cv2.COLOR_BGR2RGB)
            image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(image).scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.preview.setPixmap(pixmap)
            self.device_status.setText(f"● {title} · {rect['width']}×{rect['height']}")
            self.device_status.setStyleSheet("color: #55d98b")
        except Exception as error:
            if self.capture is not None:
                self.capture.close()
                self.capture = None
            self.capture_signature = None
            self.device_status.setText(f"● Android недоступен: {error}")
            self.device_status.setStyleSheet("color: #ff7185")

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
        self.infinite_cycle.setChecked(bool(config.get("infinite_cycle", True)))
        self.auto_tutorial.setChecked(bool(config.get("auto_tutorial", True)))
        backend = config.get("backend", "native_arm64")
        index = self.backend_mode.findData(backend)
        self.backend_mode.setCurrentIndex(index if index >= 0 else 0)
        self.android_serial.setText(config.get("android_serial", "127.0.0.1:5561"))
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
            "backend": self.backend_mode.currentData() or "native_arm64",
            "android_serial": self.android_serial.text().strip() or "127.0.0.1:5561",
            "adb_path": self.adb_path.text().strip(),
            "window_title": self.window_title.text().strip() or bot.SCRCPY_VIDEO_TITLE,
        }
        atomic_json(GUI_CONFIG_FILE, config)
        state = read_json(bot.STATE_FILE, dict(bot.DEFAULT_STATE))
        state["target_state"] = config["target_state"]
        state["characters_per_cycle"] = config["characters_per_cycle"]
        state["auto_reset_data"] = config["auto_reset_data"]
        if int(state.get("characters_created", 0)) == 0:
            state["next_nickname"] = config["next_number"]
        atomic_json(bot.STATE_FILE, state)
        if show_message:
            QMessageBox.information(self, "WAR BOT", "Настройки сохранены.")

    def closeEvent(self, event):
        if self.capture is not None:
            self.capture.close()
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
    app.setApplicationName("WAR BOT")
    window = WarBotWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
