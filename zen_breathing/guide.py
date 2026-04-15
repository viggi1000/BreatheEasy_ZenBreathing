"""
Interactive walkthrough overlay -- context-sensitive guidance
that appears during each session phase and fades gracefully.

Text is theme-aware: ocean, aurora, and orb modes have distinct language
so the instructions always match what the user sees and hears.

Includes:
  - Phase-specific guidance text (calibrating / training / flow)
  - Breathing technique panel (inhale/hold/exhale/hold)
  - Micro-message display (contextual feedback from biofeedback engine)
"""

from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout, QGraphicsOpacityEffect
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFont, QPainter, QColor

from zen_breathing.state import BreathingState


# ── Theme-specific guide text ────────────────────────────────────────────

GUIDE_STEPS = {
    "ocean": {
        "calibrating": {
            "title": "Calibrating",
            "body": "Breathe naturally.\nWe are measuring your resting breathing rate.",
            "icon": "\u25cb",
        },
        "training": {
            "title": "Follow the Wave",
            "body": "Watch and listen to the ocean wave.\n"
                    "Inhale as the wave RISES  \u2014  exhale as it FALLS.\n"
                    "Match the glowing GUIDE RING.\n"
                    "The ocean glows brighter as you synchronise.\n"
                    "Press E when you are ready to end the session.",
            "icon": "\u223f",
        },
        "flow": {
            "title": "Flow State",
            "body": "Beautiful. You are synchronised.\n"
                    "The ocean glows brighter as you deepen resonance.\n"
                    "Press E to end and see your session report.",
            "icon": "\u2726",
        },
    },
    "aurora": {
        "calibrating": {
            "title": "Calibrating",
            "body": "Breathe naturally.\nWe are measuring your resting breathing rate.",
            "icon": "\u25cb",
        },
        "training": {
            "title": "Follow the Aurora",
            "body": "Watch the aurora curtain ripple across the sky.\n"
                    "Breathe IN as it BRIGHTENS  \u2014  breathe OUT as it DIMS.\n"
                    "Match the glowing GUIDE BAND.\n"
                    "The aurora intensifies as you find your rhythm.\n"
                    "Press E when you are ready to end the session.",
            "icon": "\u223f",
        },
        "flow": {
            "title": "Flow State",
            "body": "Magnificent. The aurora dances with your breath.\n"
                    "You have found resonance.\n"
                    "Press E to end and see your session report.",
            "icon": "\u2726",
        },
    },
    "orb": {
        "calibrating": {
            "title": "Calibrating",
            "body": "Breathe naturally.\nWe are measuring your resting breathing rate.",
            "icon": "\u25cb",
        },
        "training": {
            "title": "Follow the Orb",
            "body": "Watch the glowing orb pulse with light.\n"
                    "Breathe IN as it EXPANDS  \u2014  breathe OUT as it CONTRACTS.\n"
                    "Match the orbiting GUIDE RING.\n"
                    "The orb shifts from blue to gold as you synchronise.\n"
                    "Press E when you are ready to end the session.",
            "icon": "\u223f",
        },
        "flow": {
            "title": "Flow State",
            "body": "Perfect. The orb radiates with your breath.\n"
                    "You have reached resonance.\n"
                    "Press E to end and see your session report.",
            "icon": "\u2726",
        },
    },
}

# ── Breathing technique text ─────────────────────────────────────────────

TECHNIQUE_TEXT = (
    "\u2191  INHALE — Fill your belly, then your chest\n"
    "\u23F8  PAUSE  — Rest at the top\n"
    "\u2193  EXHALE — Slow and steady, longer than inhale\n"
    "\u23F8  PAUSE  — Rest at the bottom"
)


class GuideOverlay(QWidget):
    """
    Semi-transparent overlay showing phase-appropriate guidance text.
    Auto-transitions and fades based on BreathingState.session_phase.
    Call ``update_theme(theme_name)`` when the visual theme changes.
    """

    def __init__(self, state: BreathingState, theme: str = "ocean", parent=None):
        super().__init__(parent)
        self.state = state
        self._theme = theme if theme in GUIDE_STEPS else "ocean"

        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Icon
        self._icon_label = QLabel("")
        self._icon_label.setAlignment(Qt.AlignCenter)
        self._icon_label.setStyleSheet(
            "color: rgba(100, 190, 240, 180); font-size: 32px; background: transparent;"
        )
        layout.addWidget(self._icon_label)

        # Title
        self._title_label = QLabel("")
        self._title_label.setAlignment(Qt.AlignCenter)
        self._title_label.setFont(QFont("Segoe UI", 20, QFont.Light))
        self._title_label.setStyleSheet(
            "color: rgba(120, 200, 250, 200); background: transparent; "
            "letter-spacing: 6px;"
        )
        layout.addWidget(self._title_label)

        # Body
        self._body_label = QLabel("")
        self._body_label.setAlignment(Qt.AlignCenter)
        self._body_label.setFont(QFont("Segoe UI", 13, QFont.Light))
        self._body_label.setStyleSheet(
            "color: rgba(140, 200, 230, 160); background: transparent; "
            "line-height: 160%;"
        )
        self._body_label.setWordWrap(True)
        layout.addWidget(self._body_label)

        # Breathing technique (shown during training only)
        self._technique_label = QLabel("")
        self._technique_label.setAlignment(Qt.AlignCenter)
        self._technique_label.setFont(QFont("Segoe UI", 11, QFont.Light))
        self._technique_label.setStyleSheet(
            "color: rgba(120, 180, 210, 130); background: transparent; "
            "line-height: 170%; margin-top: 12px;"
        )
        self._technique_label.setWordWrap(True)
        layout.addWidget(self._technique_label)

        # Opacity effect
        self._opacity_fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_fx)
        self._opacity_fx.setOpacity(0.0)
        self._target_opacity = 1.0

        # State tracking
        self._current_phase = None
        self._phase_enter_time = 0.0
        self._fade_out_after = 10.0
        self._hidden_for_phase = set()

        # Refresh timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(200)

    def update_theme(self, theme_name: str):
        """Switch to a different visual theme's guide text."""
        if theme_name not in GUIDE_STEPS:
            return
        self._theme = theme_name
        if self._current_phase:
            self._hidden_for_phase.discard(self._current_phase)
            self._phase_enter_time = self.state.session_time
            self._show_phase(self._current_phase)
            self._fade_to(1.0, 600)

    def _steps(self):
        return GUIDE_STEPS.get(self._theme, GUIDE_STEPS["ocean"])

    def _show_phase(self, phase: str):
        step = self._steps().get(phase)
        if step:
            self._icon_label.setText(step["icon"])
            self._title_label.setText(step["title"].upper())
            self._body_label.setText(step["body"])

            # Show technique text during training, hide otherwise
            if phase == "training":
                self._technique_label.setText(TECHNIQUE_TEXT)
            else:
                self._technique_label.setText("")

    def _refresh(self):
        phase = self.state.session_phase
        t = self.state.session_time

        if phase != self._current_phase and phase in self._steps():
            self._current_phase = phase
            self._phase_enter_time = t
            self._hidden_for_phase.discard(phase)
            self._show_phase(phase)
            self._fade_to(1.0, 800)

        # Auto-fade after timeout
        if (self._current_phase and
                self._current_phase not in self._hidden_for_phase and
                t - self._phase_enter_time > self._fade_out_after):
            self._hidden_for_phase.add(self._current_phase)
            self._fade_to(0.0, 2000)

    def _fade_to(self, target: float, duration_ms: int):
        self._target_opacity = target
        anim = QPropertyAnimation(self._opacity_fx, b"opacity")
        anim.setDuration(duration_ms)
        anim.setStartValue(self._opacity_fx.opacity())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._current_anim = anim
        anim.start()

    def show_again(self):
        """Re-show the current phase guidance (e.g. when user presses G)."""
        if self._current_phase:
            self._hidden_for_phase.discard(self._current_phase)
            self._phase_enter_time = self.state.session_time
            self._fade_to(1.0, 600)
