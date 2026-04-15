"""
Welcome screen -- intro, how-it-works, source + visual theme selection.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup, QFrame, QScrollArea,
    QGraphicsOpacityEffect,
)
from PyQt5.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer
from PyQt5.QtGui import QFont, QColor, QPainter, QLinearGradient


class WelcomeScreen(QWidget):
    """
    Full-screen welcome / intro page.

    Signals:
        start_session(source: str, theme: str)
            source -- "mock" or "polar"
            theme  -- "ocean", "aurora", or "orb"
    """

    start_session = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_source = "mock"
        self._selected_theme  = "ocean"
        self._build_ui()
        self._animate_in()

    def _build_ui(self):
        self.setStyleSheet("background: transparent;")

        # ---- Scroll area so content never clips on small screens ----
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: rgba(0,0,0,0); width: 6px; }"
            "QScrollBar::handle:vertical { background: rgba(60,130,190,80); border-radius: 3px; }"
        )

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        scroll.setWidget(inner)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(scroll)

        center = QVBoxLayout(inner)
        center.setAlignment(Qt.AlignHCenter)
        center.setSpacing(0)
        center.setContentsMargins(20, 30, 20, 30)

        # ---- Title ----
        title = QLabel("Z E N B R E A T H I N G")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "color: #68b8e8; font-size: 30px; font-weight: 300; "
            "letter-spacing: 8px; background: transparent;"
        )
        title.setFont(QFont("Segoe UI", 30, QFont.Light))
        center.addWidget(title)
        center.addSpacing(6)

        sub = QLabel("Breathing Biofeedback Art Experience")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(
            "color: rgba(100, 170, 220, 180); font-size: 14px; "
            "font-weight: 300; letter-spacing: 3px; background: transparent;"
        )
        center.addWidget(sub)
        center.addSpacing(22)

        # ---- How It Works ----
        how_frame = QFrame()
        how_frame.setStyleSheet(
            "QFrame { background: rgba(10, 25, 50, 180); "
            "border: 1px solid rgba(60, 130, 190, 60); border-radius: 12px; }"
        )
        how_frame.setMaximumWidth(660)
        how_layout = QVBoxLayout(how_frame)
        how_layout.setContentsMargins(28, 18, 28, 18)
        how_layout.setSpacing(10)

        how_title = QLabel("How It Works")
        how_title.setAlignment(Qt.AlignCenter)
        how_title.setStyleSheet(
            "color: #5aaddd; font-size: 15px; font-weight: 600; "
            "background: transparent; border: none;"
        )
        how_layout.addWidget(how_title)

        steps = [
            ("1.  Calibrate",
             "Breathe naturally for 15 seconds. We measure your resting breathing rate."),
            ("2.  Follow",
             "The visual and audio guide your breath. Match your inhale and exhale to the rising and falling guide."),
            ("3.  Synchronise",
             "As you sync, the guide slows gently toward your resonant breathing frequency (~6 BPM)."),
            ("4.  Flow",
             "The closer you synchronise, the more the visual responds and glows. Reach flow state."),
        ]

        for step_title, step_desc in steps:
            # Each step in its own sub-frame to prevent layout collapse
            row_widget = QWidget()
            row_widget.setMinimumHeight(44)
            row_widget.setStyleSheet("background: transparent; border: none;")
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(12)

            title_lbl = QLabel(step_title)
            title_lbl.setFixedWidth(130)
            title_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            title_lbl.setStyleSheet(
                "color: #7cc8e8; font-size: 13px; font-weight: 600; "
                "background: transparent; border: none;"
            )
            row.addWidget(title_lbl)

            desc_lbl = QLabel(step_desc)
            desc_lbl.setWordWrap(True)
            desc_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            desc_lbl.setStyleSheet(
                "color: rgba(170, 210, 235, 210); font-size: 13px; "
                "font-weight: 300; background: transparent; border: none;"
            )
            row.addWidget(desc_lbl, 1)
            how_layout.addWidget(row_widget)

        center.addWidget(how_frame, 0, Qt.AlignHCenter)
        center.addSpacing(18)

        # ---- Two-column config row: Source + Theme ----
        config_row = QHBoxLayout()
        config_row.setSpacing(14)
        config_row.setAlignment(Qt.AlignHCenter)

        # Data Source
        src_frame = self._make_config_frame("Data Source")
        src_layout = src_frame.layout()

        radio_style = self._radio_style()
        self._src_group = QButtonGroup(self)

        rb_mock = QRadioButton("Simulated Demo  --  no hardware needed")
        rb_mock.setStyleSheet(radio_style)
        rb_mock.setChecked(True)
        self._src_group.addButton(rb_mock, 0)
        src_layout.addWidget(rb_mock)

        rb_polar = QRadioButton("Polar H10  --  live Bluetooth chest strap")
        rb_polar.setStyleSheet(radio_style)
        self._src_group.addButton(rb_polar, 1)
        src_layout.addWidget(rb_polar)

        self._src_group.buttonClicked.connect(self._on_source_changed)
        config_row.addWidget(src_frame)

        # Visual Theme
        theme_frame = self._make_config_frame("Visual Theme")
        theme_layout = theme_frame.layout()

        self._theme_group = QButtonGroup(self)

        themes = [
            ("Ocean  --  underwater looking up at the waves",    "ocean"),
            ("Aurora  --  northern lights breathing curtain",     "aurora"),
            ("Orb  --  glowing sphere of light in the void",      "orb"),
        ]
        for i, (label, val) in enumerate(themes):
            rb = QRadioButton(label)
            rb.setStyleSheet(radio_style)
            if val == "ocean":
                rb.setChecked(True)
            self._theme_group.addButton(rb, i)
            theme_layout.addWidget(rb)

        self._theme_vals = [t[1] for t in themes]
        self._theme_group.buttonClicked.connect(self._on_theme_changed)
        config_row.addWidget(theme_frame)

        # Wrap config_row in a max-width container
        config_wrap = QWidget()
        config_wrap.setMaximumWidth(760)
        config_wrap.setStyleSheet("background: transparent;")
        config_wrap.setLayout(config_row)
        center.addWidget(config_wrap, 0, Qt.AlignHCenter)
        center.addSpacing(22)

        # ---- Start button ----
        self._start_btn = QPushButton("Begin Session")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setStyleSheet(
            "QPushButton { "
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "    stop:0 rgba(30, 100, 160, 200), stop:1 rgba(20, 140, 180, 200)); "
            "  color: #d0e8f8; font-size: 17px; font-weight: 400; "
            "  border: 1px solid rgba(80, 160, 220, 120); border-radius: 8px; "
            "  padding: 11px 50px; letter-spacing: 3px; "
            "} "
            "QPushButton:hover { "
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "    stop:0 rgba(40, 120, 180, 240), stop:1 rgba(30, 160, 200, 240)); "
            "  border: 1px solid rgba(100, 180, 240, 180); "
            "} "
            "QPushButton:pressed { background: rgba(20, 80, 140, 220); }"
        )
        self._start_btn.setFixedHeight(50)
        self._start_btn.setMaximumWidth(280)
        self._start_btn.clicked.connect(self._on_start)
        center.addWidget(self._start_btn, 0, Qt.AlignHCenter)
        center.addSpacing(14)

        hint = QLabel(
            "H = HUD    B = Breath Guide    D = Debug Signals    T = Cycle Theme    A = Audio    "
            "F11 = Fullscreen    Space = Pause    E = End Session    Esc = Quit"
        )
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            "color: rgba(100, 150, 180, 110); font-size: 11px; background: transparent;"
        )
        center.addWidget(hint)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_config_frame(title_text: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background: rgba(10, 25, 50, 150); "
            "border: 1px solid rgba(60, 130, 190, 55); border-radius: 10px; }"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 12, 20, 14)
        layout.setSpacing(7)
        title = QLabel(title_text)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "color: #5aaddd; font-size: 13px; font-weight: 600; "
            "background: transparent; border: none;"
        )
        layout.addWidget(title)
        return frame

    @staticmethod
    def _radio_style() -> str:
        return (
            "QRadioButton { color: rgba(160, 200, 230, 220); font-size: 12px; "
            "background: transparent; border: none; spacing: 8px; } "
            "QRadioButton::indicator { width: 13px; height: 13px; } "
            "QRadioButton::indicator:checked { background: #5aaddd; "
            "  border: 2px solid #3a8abd; border-radius: 6px; } "
            "QRadioButton::indicator:unchecked { background: rgba(40,60,80,180); "
            "  border: 2px solid rgba(60,130,190,80); border-radius: 6px; }"
        )

    def _on_source_changed(self, btn):
        self._selected_source = "mock" if self._src_group.id(btn) == 0 else "polar"

    def _on_theme_changed(self, btn):
        idx = self._theme_group.id(btn)
        self._selected_theme = self._theme_vals[idx]

    def _on_start(self):
        self.start_session.emit(self._selected_source, self._selected_theme)

    def _animate_in(self):
        self._opacity_fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_fx)
        self._opacity_fx.setOpacity(0.0)
        self._fade_anim = QPropertyAnimation(self._opacity_fx, b"opacity")
        self._fade_anim.setDuration(1200)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InOutQuad)
        QTimer.singleShot(200, self._fade_anim.start)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(2, 8, 20))
        grad.setColorAt(0.4, QColor(4, 14, 35))
        grad.setColorAt(1.0, QColor(2, 6, 18))
        painter.fillRect(self.rect(), grad)
