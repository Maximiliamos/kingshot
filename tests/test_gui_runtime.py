import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import gui


class GuiRuntimeSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_constructs_and_closes_offscreen(self):
        window = gui.WarBotWindow()
        try:
            self.assertEqual(window.windowTitle(), "TUGARIN BOTS — Центр управления")
            self.assertIsNotNone(window.preview)
            self.assertEqual(window.backend_mode.currentData(), "wsa")
            self.assertTrue(window.timer.isActive())
            self.assertFalse(window.backend_card.isVisible())
            window.developer_mode.setChecked(True)
            self.app.processEvents()
            self.assertFalse(window.backend_card.isHidden())
        finally:
            window.timer.stop()
            window.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
