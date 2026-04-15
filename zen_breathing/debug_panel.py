"""
Signal debug panel -- real-time plots of breathing signals.

Plots:
  1. Accelerometer Z (breath dominant axis)
  2. ECG raw waveform
  3. Respiration: ACC-derived (cyan) vs ECG-derived/EDR (yellow)
  4. Breath Phase (user vs target)
  5. Rate (cyan) & Pace (yellow)
  6. Coherence (green) & Sync (purple)
  7. Heart Rate

Toggle with 'D' key.
"""

import numpy as np

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import QTimer, Qt

try:
    import pyqtgraph as pg
    pg.setConfigOptions(antialias=True)
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False

from zen_breathing.state import BreathingState


PLOT_BG   = (4, 12, 28)
AXIS_COLOR = (80, 140, 180)


def _pen(r, g, b, w=1.5):
    return pg.mkPen(color=(r, g, b), width=w) if HAS_PYQTGRAPH else None


PEN_CYAN   = _pen(50,  190, 240)
PEN_GREEN  = _pen(50,  220, 120)
PEN_ORANGE = _pen(240, 160,  50)
PEN_RED    = _pen(240,  80,  80)
PEN_WHITE  = _pen(180, 200, 220, 1.0)
PEN_PURPLE = _pen(160, 100, 240)
PEN_YELLOW = _pen(240, 220,  80)
PEN_TEAL   = _pen(50,  200, 180)
PEN_LIME   = _pen(120, 240,  80, 1.2)


class DebugPanel(QWidget):
    """Collapsible panel with real-time signal plots."""

    def __init__(self, state: BreathingState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setMinimumWidth(380)
        self.setMaximumWidth(500)

        if not HAS_PYQTGRAPH:
            layout = QVBoxLayout(self)
            lbl = QLabel("pyqtgraph not installed\npip install pyqtgraph")
            lbl.setStyleSheet("color: #ff8888; font-size: 14px;")
            lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(lbl)
            return

        self._build_plots()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_plots)
        self._timer.start(100)   # 10 Hz refresh

    def _build_plots(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        self.setStyleSheet("QWidget { background: rgb(4, 12, 28); }")

        header = QLabel("Signal Debug")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet(
            "color: #5aaddd; font-size: 13px; font-weight: 500; "
            "padding: 3px; background: rgba(10, 25, 50, 200);"
        )
        layout.addWidget(header)

        def make_plot(title, height=65):
            pw = pg.PlotWidget(title=title)
            pw.setBackground(PLOT_BG)
            pw.showGrid(x=False, y=True, alpha=0.12)
            pw.setFixedHeight(height)
            pw.hideButtons()
            pw.setMenuEnabled(False)
            ax_l = pw.getAxis("left")
            ax_b = pw.getAxis("bottom")
            for ax in (ax_l, ax_b):
                ax.setPen(pg.mkPen(color=AXIS_COLOR))
                ax.setTextPen(pg.mkPen(color=AXIS_COLOR))
            layout.addWidget(pw)
            return pw

        # 1. ACC Z-axis (breathing dominant)
        self._acc_plot = make_plot("Accelerometer Z (breath)", height=72)
        self._acc_z_curve = self._acc_plot.plot(pen=PEN_ORANGE, name="Z")
        self._acc_x_curve = self._acc_plot.plot(pen=PEN_RED,    name="X", alpha=0.4)

        # 2. ECG raw
        self._ecg_plot = make_plot("ECG raw (µV)", height=65)
        self._ecg_curve = self._ecg_plot.plot(pen=PEN_LIME)

        # 3. Respiration: ACC-derived (cyan) vs ECG/EDR (yellow)
        self._resp_plot = make_plot(
            "Respiration  \u2014  CYAN = ACC (chest motion)  |  YELLOW = ECG-EDR (amplitude)", height=65)
        self._acc_resp_curve = self._resp_plot.plot(pen=PEN_CYAN,   name="ACC chest")
        self._ecg_resp_curve = self._resp_plot.plot(pen=PEN_YELLOW, name="ECG-EDR")

        # 4. Breath phase (user vs target/guide)
        self._phase_plot = make_plot(
            "Breath Phase  \u2014  CYAN = your breath  |  WHITE = guide (target)", height=65)
        self._phase_plot.setYRange(-0.05, 1.05)
        self._user_phase = self._phase_plot.plot(pen=PEN_CYAN,  name="Your Breath")
        self._tgt_phase  = self._phase_plot.plot(pen=PEN_WHITE, name="Guide")

        # 5. Rate + Pace
        self._rate_plot = make_plot("Rate BPM  \u2014  CYAN = your rate  |  YELLOW = guide pace", height=65)
        self._rate_plot.setYRange(3, 22)
        self._rate_curve = self._rate_plot.plot(pen=PEN_CYAN,   name="Rate")
        self._pace_curve = self._rate_plot.plot(pen=PEN_YELLOW, name="Pace")
        self._pace_history = []

        # 6. Coherence + Sync
        self._sync_plot = make_plot("Coherence (green) & Sync (purple)", height=65)
        self._sync_plot.setYRange(-5, 105)
        self._coh_curve  = self._sync_plot.plot(pen=PEN_GREEN,  name="Coh%")
        self._sync_curve = self._sync_plot.plot(pen=PEN_PURPLE, name="Sync")

        # 7. Heart Rate
        self._hr_plot = make_plot("Heart Rate (BPM)", height=60)
        self._hr_plot.setYRange(40, 110)
        self._hr_curve = self._hr_plot.plot(pen=PEN_RED, name="HR")

        # Metrics bar
        self._metrics = QLabel("")
        self._metrics.setAlignment(Qt.AlignCenter)
        self._metrics.setWordWrap(True)
        self._metrics.setStyleSheet(
            "color: rgba(140, 190, 220, 180); font-size: 10px; "
            "background: rgba(10, 25, 50, 200); padding: 3px;"
        )
        layout.addWidget(self._metrics)

    def _update_plots(self):
        if not HAS_PYQTGRAPH:
            return
        s = self.state

        # 1. ACC Z + X
        if len(s.acc_z_history) > 2:
            self._acc_z_curve.setData(list(s.acc_z_history))
        if len(s.acc_x_history) > 2:
            self._acc_x_curve.setData(list(s.acc_x_history))

        # 2. ECG raw
        if len(s.ecg_raw_history) > 2:
            self._ecg_curve.setData(list(s.ecg_raw_history))

        # 3. Respiration signals
        if len(s.acc_resp_history) > 2:
            self._acc_resp_curve.setData(list(s.acc_resp_history)[-500:])
        if len(s.ecg_resp_history) > 2:
            self._ecg_resp_curve.setData(list(s.ecg_resp_history)[-500:])

        # 4. Phase
        if len(s.breath_phase_history) > 2:
            self._user_phase.setData(list(s.breath_phase_history)[-600:])
        if len(s.target_phase_history) > 2:
            self._tgt_phase.setData(list(s.target_phase_history)[-600:])

        # 5. Rate + Pace
        if len(s.breath_rate_history) > 2:
            self._rate_curve.setData(list(s.breath_rate_history))
        self._pace_history.append(s.current_pace_bpm)
        if len(self._pace_history) > 300:
            self._pace_history = self._pace_history[-300:]
        self._pace_curve.setData(self._pace_history)

        # 6. Coherence + Sync
        if len(s.coherence_history) > 2:
            self._coh_curve.setData(list(s.coherence_history))
        if len(s.sync_score_history) > 2:
            self._sync_curve.setData(
                [v * 100 for v in list(s.sync_score_history)[-300:]]
            )

        # 7. Heart Rate
        if len(s.heart_rate_history) > 2:
            self._hr_curve.setData(list(s.heart_rate_history))

        # Metrics bar
        mins = int(s.session_time) // 60
        secs = int(s.session_time) % 60
        cal = f"{s.calibrated_rate_bpm:.1f}" if s.is_calibrated else "..."
        edr_tag = f"EDR:{s.ecg_resp_signal:.1f}" if s.ecg_resp_history else "EDR:--"
        self._metrics.setText(
            f"Rate:{s.breath_rate_bpm:.1f} | Pace:{s.current_pace_bpm:.1f}->{s.resonance_bpm:.1f} | "
            f"Coh:{s.coherence:.0f}% | Sync:{s.sync_score:.0%} | "
            f"E/I:{s.exhale_inhale_ratio:.2f} | HR:{s.heart_rate:.0f} | "
            f"Cal:{cal} | {edr_tag} | {mins}:{secs:02d}"
        )
