"""
ZenBreathing main application.

UI flow:
  Welcome screen (intro + source selection)
    -> [Polar only] Connection overlay (scan / connect / confirm streams)
    -> Session view (underwater visual + breath guide bar + optional overlays)
    -> [End session] Session report overlay

Controls:
  F11       fullscreen toggle
  H         HUD overlay toggle (numeric stats)
  B         breath guide bar toggle (default: on)
  D         debug signal panel toggle
  A         audio toggle
  T         theme toggle (ocean -> aurora -> orb -> ocean)
  G         re-show guide message
  Space     pause / resume
  E         end session early (shows report)
  Esc       quit  (or fall back to demo if connection overlay visible)
"""

import sys
import numpy as np

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QPushButton, QSplitter, QStackedWidget, QGraphicsOpacityEffect,
    QFrame,
)
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal, QRect
from PyQt5.QtGui import QFont, QCursor, QPainter, QColor, QPen, QLinearGradient

from zen_breathing.state import BreathingState
from zen_breathing.visual import ZenVisualWidget
from zen_breathing.audio import AudioEngine
from zen_breathing.welcome import WelcomeScreen
from zen_breathing.guide import GuideOverlay
from zen_breathing.debug_panel import DebugPanel
from zen_breathing.simulator import DemoSimulator
from zen_breathing.data_logger import DataLogger


# ===================================================================
#  LIVE PROCESSOR  --  polls PolarDataBus, no cross-thread signals
# ===================================================================

class LiveProcessor:
    """
    Polls PolarDataBus for new Polar H10 data and feeds it into the
    biofeedback engine.  Called from the main UI thread via _tick().
    """

    def __init__(self, state: BreathingState, data_bus):
        self.state    = state
        self.data_bus = data_bus
        self._running = True

        from zen_breathing.respiration import RespirationExtractor
        from zen_breathing.biofeedback import BiofeedbackEngine

        self.resp     = RespirationExtractor(fs_acc=100, fs_ecg=130)
        self.feedback = BiofeedbackEngine(state, target_rate_bpm=6.0)

        self._acc_read = 0
        self._ecg_read = 0

    def update(self, dt: float = 1 / 60):
        if not self._running:
            return

        bus = self.data_bus

        new_acc, self._acc_read = bus.get_new_acc(self._acc_read)
        if new_acc is not None and len(new_acc) > 0:
            self.resp.add_acc_samples(new_acc)
            phase, rate, depth = self.resp.get_breath_state()
            self.state.breath_phase     = phase
            self.state.breath_rate_bpm  = rate
            self.state.breath_depth     = depth
            self.state.is_inhaling      = self.resp.is_inhaling

            for row in new_acc[-20:]:
                self.state.acc_x_history.append(row[0])
                self.state.acc_y_history.append(row[1])
                self.state.acc_z_history.append(row[2])
            self.state.acc_resp_signal = self.resp.acc_signal
            self.state.acc_resp_history.append(self.resp.acc_signal)
            self.state.resp_signal_history.append(self.resp.resp_signal)
            self.state.raw_resp_signal = self.resp.resp_signal

        new_ecg, self._ecg_read = bus.get_new_ecg(self._ecg_read)
        if new_ecg is not None and len(new_ecg) > 0:
            self.resp.add_ecg_samples(new_ecg.tolist())
            for s in new_ecg[-130:]:
                self.state.ecg_raw_history.append(float(s))
            self.state.ecg_resp_signal = self.resp.ecg_signal
            if self.resp.ecg_available:
                self.state.ecg_resp_history.append(self.resp.ecg_signal)

        hr = bus.get_latest_hr()
        if hr is not None:
            self.state.heart_rate = hr

        self.feedback.update(dt)

    def stop(self):
        self._running = False


# ===================================================================
#  POLAR CONNECTION OVERLAY
# ===================================================================

class PolarConnectOverlay(QWidget):
    """Full-screen overlay while connecting to Polar H10."""

    fallback_to_demo = pyqtSignal()

    def __init__(self, data_bus, parent=None):
        super().__init__(parent)
        self.data_bus = data_bus
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: rgba(0, 5, 15, 220);")

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        card = QWidget()
        card.setMaximumWidth(520)
        card.setStyleSheet(
            "QWidget { background: rgba(8, 20, 45, 200); "
            "border: 1px solid rgba(60, 130, 190, 80); border-radius: 14px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(45, 40, 45, 40)
        card_layout.setSpacing(18)

        title = QLabel("Connecting to Polar H10")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "color: rgba(100, 190, 255, 220); font: 22px 'Segoe UI'; "
            "font-weight: 300; letter-spacing: 4px; background: transparent; border: none;"
        )
        card_layout.addWidget(title)

        self._status_lbl = QLabel("Scanning...")
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            "color: rgba(190, 215, 240, 200); font: 13px 'Segoe UI'; "
            "background: transparent; border: none;"
        )
        card_layout.addWidget(self._status_lbl)

        card_layout.addSpacing(8)

        self._INACTIVE = (
            "color: rgba(130, 130, 140, 180); font: 13px 'Segoe UI'; "
            "background: transparent; border: none;"
        )
        self._ACTIVE = (
            "color: rgba(80, 230, 130, 230); font: 13px 'Segoe UI'; "
            "background: transparent; border: none;"
        )

        self._hr_lbl  = QLabel("Heart Rate   --  waiting")
        self._ecg_lbl = QLabel("ECG          --  waiting")
        self._acc_lbl = QLabel("Accelerometer  --  waiting")
        for lbl in (self._hr_lbl, self._ecg_lbl, self._acc_lbl):
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(self._INACTIVE)
            card_layout.addWidget(lbl)

        card_layout.addSpacing(8)

        hint = QLabel("Press Esc to switch to Demo mode")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            "color: rgba(110, 110, 120, 150); font: 11px 'Segoe UI'; "
            "background: transparent; border: none;"
        )
        card_layout.addWidget(hint)

        outer.addWidget(card, 0, Qt.AlignCenter)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh_streams)
        self._poll.start(400)

    def update_status(self, msg: str):
        self._status_lbl.setText(msg)

    def _set_stream(self, lbl, label, active, detail=""):
        if active:
            text = f"{label}   \u2713   {detail}" if detail else f"{label}   \u2713"
            lbl.setStyleSheet(self._ACTIVE)
        else:
            text = f"{label}   --   waiting"
            lbl.setStyleSheet(self._INACTIVE)
        lbl.setText(text)

    def _refresh_streams(self):
        bus = self.data_bus
        hr  = bus.get_latest_hr()
        self._set_stream(self._hr_lbl,  "Heart Rate",     bus.hr_active,
                         f"{hr:.0f} BPM" if hr else "")
        self._set_stream(self._ecg_lbl, "ECG",            bus.ecg_active,
                         f"{bus._ecg_total} samples" if bus.ecg_active else "")
        self._set_stream(self._acc_lbl, "Accelerometer",  bus.acc_active,
                         f"{bus._acc_total} samples" if bus.acc_active else "")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.fallback_to_demo.emit()
        else:
            super().keyPressEvent(event)


# ===================================================================
#  BREATHING RING OVERLAY
# ===================================================================

class BreathingRingOverlay(QWidget):
    """
    Concentric breathing ring indicator — the primary real-time sync guide.

    Outer ring (white/cream, thickens with sync): the GUIDE.
      Expands as the pacer inhales, contracts as it exhales.

    Inner fill (teal/cyan): YOU.
      Grows to match the ring as you synchronise.

    When the fill edge touches the outer ring you are perfectly in sync.
    Text below shows the cue (INHALE / EXHALE), BPM, and sync %.

    Toggle with B key (default: visible).
    """

    RING_W = 200
    RING_H = 230
    R_MIN  = 22     # radius at full exhale
    R_MAX  = 78     # radius at full inhale

    def __init__(self, state: BreathingState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.RING_W, self.RING_H)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(33)   # ~30 Hz

    def paintEvent(self, event):
        s = self.state
        target   = float(s.target_phase)
        breath   = float(s.breath_phase)
        sync     = float(s.sync_score)
        pace_bpm = float(s.current_pace_bpm)
        user_bpm = float(s.breath_rate_bpm)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        W, H  = self.RING_W, self.RING_H
        R_MIN = self.R_MIN
        R_MAX = self.R_MAX
        cx    = W // 2
        cy    = R_MAX + 18          # ring centre

        guide_r = int(R_MIN + (R_MAX - R_MIN) * target)
        user_r  = int(R_MIN + (R_MAX - R_MIN) * breath)

        # ── Dark circular background ──────────────────────────────────
        bg_r = R_MAX + 14
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(2, 8, 20, 170))
        p.drawEllipse(cx - bg_r, cy - bg_r, bg_r * 2, bg_r * 2)

        # ── Exhale baseline (dashed, shows minimum size reference) ────
        p.setPen(QPen(QColor(50, 90, 130, 55), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(cx - R_MIN, cy - R_MIN, R_MIN * 2, R_MIN * 2)

        # ── User fill (teal) ──────────────────────────────────────────
        fill_alpha = 130 + int(80 * sync)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(38, 185, 172, fill_alpha))
        if user_r > 0:
            p.drawEllipse(cx - user_r, cy - user_r, user_r * 2, user_r * 2)

        # ── Guide ring (white/cream, thickens + brightens with sync) ──
        ring_w   = 2.0 + 3.5 * sync
        bright   = int(200 + 55 * sync)
        guide_col = QColor(bright, bright, 255, bright)
        p.setPen(QPen(guide_col, ring_w))
        p.setBrush(Qt.NoBrush)
        if guide_r > 0:
            p.drawEllipse(cx - guide_r, cy - guide_r, guide_r * 2, guide_r * 2)

        # ── Centre anchor dot ─────────────────────────────────────────
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(200, 220, 255, 80))
        p.drawEllipse(cx - 3, cy - 3, 6, 6)

        # ── In-ring legend ─────────────────────────────────────────────
        p.setPen(QColor(200, 228, 255, 90))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(cx - 24, cy - 8, 48, 12, Qt.AlignCenter, "\u25ef GUIDE")
        p.setPen(QColor(55, 210, 200, 110))
        p.drawText(cx - 18, cy + 4, 36, 11, Qt.AlignCenter, "\u25cf YOU")

        # ── INHALE / EXHALE cue ───────────────────────────────────────
        is_inhaling = target > 0.5
        cue_text  = "\u2191 INHALE" if is_inhaling else "\u2193 EXHALE"
        cue_color = QColor(90, 220, 155, 230) if is_inhaling else QColor(130, 160, 220, 195)
        p.setPen(cue_color)
        p.setFont(QFont("Segoe UI", 11, QFont.Bold))
        cue_y = cy + R_MAX + 8
        p.drawText(0, cue_y, W, 20, Qt.AlignCenter, cue_text)

        # ── BPM info ──────────────────────────────────────────────────
        p.setPen(QColor(120, 170, 210, 145))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(0, cue_y + 22, W, 15, Qt.AlignCenter,
                   f"guide {pace_bpm:.1f}  \u00b7  you {user_bpm:.1f} bpm")

        # ── Sync quality ──────────────────────────────────────────────
        sync_pct = int(sync * 100)
        sc_r = int(215 * (1 - sync))
        sc_g = int(75  + 145 * sync)
        sc_b = int(125 + 65  * sync)
        p.setPen(QColor(sc_r, sc_g, sc_b, 165))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(0, cue_y + 38, W, 14, Qt.AlignCenter, f"sync {sync_pct}%")

        p.end()


# ===================================================================
#  HUD OVERLAY — Elegant, minimal, with micro-messages
# ===================================================================

class HUDOverlay(QWidget):
    """Transparent overlay showing key metrics and micro-feedback messages.

    Top strip:   ♡ HR  ·  ∿ BPM → target  ·  Session time
    Second row:  Coherence bar (gradient fill)
    Below:       Micro-message (contextual feedback, fades)
    """

    def __init__(self, state: BreathingState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self.update)  # triggers paintEvent
        self._update_timer.start(100)

        self._micro_opacity = 0.0
        self._last_micro_text = ""

    def paintEvent(self, event):
        s = self.state
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        W = self.width()

        # ── Semi-transparent top strip background ──────────────────────
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(2, 8, 22, 120))
        p.drawRoundedRect(8, 6, W - 16, 60, 10, 10)

        # ── Row 1: HR · BPM → target · Time ───────────────────────────
        mins = int(s.session_time) // 60
        secs = int(s.session_time) % 60

        p.setFont(QFont("Segoe UI", 12, QFont.Light))

        # Heart icon + HR
        p.setPen(QColor(235, 100, 120, 200))
        hr_text = f"\u2665 {s.heart_rate:.0f}"
        p.drawText(22, 18, 100, 22, Qt.AlignLeft | Qt.AlignVCenter, hr_text)

        # Breath BPM → target
        p.setPen(QColor(130, 200, 235, 200))
        bpm_text = f"\u223f {s.breath_rate_bpm:.1f} \u2192 {s.resonance_bpm:.0f} bpm"
        p.drawText(130, 18, 220, 22, Qt.AlignLeft | Qt.AlignVCenter, bpm_text)

        # Session time
        p.setPen(QColor(160, 190, 220, 160))
        time_text = f"{mins}:{secs:02d}"
        p.drawText(W - 90, 18, 70, 22, Qt.AlignRight | Qt.AlignVCenter, time_text)

        # ── Row 2: Coherence bar ───────────────────────────────────────
        bar_x = 22
        bar_y = 44
        bar_w = W - 44
        bar_h = 12

        # Bar background
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 40, 70, 140))
        p.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 4, 4)

        # Bar fill — gradient from blue → green → gold
        coh = max(0.0, min(1.0, s.coherence / 100.0))
        fill_w = max(1, int(bar_w * coh))

        grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
        grad.setColorAt(0.0,  QColor(40, 120, 220, 220))
        grad.setColorAt(0.45, QColor(50, 200, 150, 220))
        grad.setColorAt(1.0,  QColor(230, 200, 80, 220))
        p.setBrush(grad)
        p.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, 4, 4)

        # Coherence % label
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor(200, 225, 245, 180))
        coh_label = f"Coherence {s.coherence:.0f}%"
        p.drawText(bar_x, bar_y - 1, bar_w, bar_h, Qt.AlignCenter, coh_label)

        # ── Micro-message (below the HUD strip) ───────────────────────
        msg = s.micro_message
        msg_alpha = s.micro_message_opacity

        if msg and msg_alpha > 0.02:
            p.setFont(QFont("Segoe UI", 11, QFont.Light))
            a = int(msg_alpha * 180)
            p.setPen(QColor(140, 210, 230, a))
            p.drawText(22, 72, W - 44, 24, Qt.AlignCenter, msg)

        p.end()


# ===================================================================
#  SESSION REPORT OVERLAY
# ===================================================================

class SessionReportOverlay(QWidget):
    """
    End-of-session summary screen.  Shown when the session timer expires
    or the user presses E to end early.

    Signals:
        new_session: user clicked "New Session"
        quit_app:    user clicked "Quit"
    """

    new_session = pyqtSignal()
    quit_app    = pyqtSignal()

    def __init__(self, state: BreathingState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: rgba(0, 5, 15, 210);")
        self._build_ui()

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _stat_row(layout, label: str, value: str,
                  label_col="#7ab8d4", value_col="#c8e4f4"):
        row = QHBoxLayout()
        row.setSpacing(12)
        lbl = QLabel(label)
        lbl.setFixedWidth(200)
        lbl.setStyleSheet(f"color: {label_col}; font: 13px 'Segoe UI'; "
                          "background: transparent; border: none;")
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val = QLabel(value)
        val.setStyleSheet(f"color: {value_col}; font: 13px 'Segoe UI'; font-weight: 500;"
                          " background: transparent; border: none;")
        row.addWidget(lbl)
        row.addWidget(val)
        layout.addLayout(row)

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m}:{s:02d}"

    # ── build ─────────────────────────────────────────────────────────

    def _build_ui(self):
        s = self.state

        # Compute stats from history deques
        coh_list  = list(s.coherence_history)
        sync_list = list(s.sync_score_history)
        hr_list   = list(s.heart_rate_history)
        rate_list = list(s.breath_rate_history)

        avg_coh  = float(np.mean(coh_list))  if coh_list  else 0.0
        peak_coh = float(np.max(coh_list))   if coh_list  else 0.0
        avg_sync = float(np.mean(sync_list)) if sync_list else 0.0
        avg_hr   = float(np.mean(hr_list))   if hr_list   else 0.0
        end_rate = float(rate_list[-1])      if rate_list else s.breath_rate_bpm

        flow_t    = s.phase_times.get("flow", 0.0)
        train_t   = s.phase_times.get("training", 0.0)
        calib_t   = s.phase_times.get("calibrating", 0.0)
        total_t   = s.session_time or (flow_t + train_t + calib_t + 1)
        flow_pct  = int(100 * flow_t / max(total_t, 1))

        # Coherence time breakdown
        coh_total = s.time_low_coherence + s.time_mid_coherence + s.time_high_coherence
        if coh_total > 0:
            low_pct  = int(100 * s.time_low_coherence  / coh_total)
            mid_pct  = int(100 * s.time_mid_coherence  / coh_total)
            high_pct = int(100 * s.time_high_coherence / coh_total)
        else:
            low_pct, mid_pct, high_pct = 100, 0, 0

        # Session score: 40% avg coherence + 30% flow time + 30% rate improvement
        rate_improvement = max(0.0, s.start_rate_bpm - end_rate)
        rate_score = min(1.0, rate_improvement / 8.0)  # 8 BPM drop = 100%
        session_score = int(
            40 * min(1.0, avg_coh / 80.0) +
            30 * min(1.0, flow_pct / 60.0) +
            30 * rate_score
        )

        # Outer layout
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        # Card
        card = QFrame()
        card.setMaximumWidth(560)
        card.setStyleSheet(
            "QFrame { background: rgba(6, 18, 42, 230); "
            "border: 1px solid rgba(60, 130, 190, 100); border-radius: 16px; }"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(50, 36, 50, 36)
        card_lay.setSpacing(8)

        # Title
        title = QLabel("SESSION COMPLETE")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "color: rgba(100, 190, 255, 230); font: 22px 'Segoe UI'; "
            "font-weight: 300; letter-spacing: 6px; background: transparent; border: none;"
        )
        card_lay.addWidget(title)

        # Duration
        dur_lbl = QLabel(self._fmt_time(total_t))
        dur_lbl.setAlignment(Qt.AlignCenter)
        dur_lbl.setStyleSheet(
            "color: rgba(160, 210, 245, 200); font: 38px 'Segoe UI'; "
            "font-weight: 200; background: transparent; border: none;"
        )
        card_lay.addWidget(dur_lbl)

        # Session score
        score_color = "#80c8a8" if session_score >= 60 else "#c8a060" if session_score >= 30 else "#c87070"
        score_lbl = QLabel(f"\u2605 Session Score: {session_score}/100")
        score_lbl.setAlignment(Qt.AlignCenter)
        score_lbl.setStyleSheet(
            f"color: {score_color}; font: 16px 'Segoe UI'; font-weight: 500; "
            "background: transparent; border: none; letter-spacing: 2px;"
        )
        card_lay.addWidget(score_lbl)

        # Divider
        card_lay.addSpacing(4)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: rgba(60, 110, 160, 80); background: rgba(60,110,160,50); border: none;")
        line.setFixedHeight(1)
        card_lay.addWidget(line)
        card_lay.addSpacing(4)

        # Primary stats
        self._stat_row(card_lay, "Average Coherence",
                       f"{avg_coh:.0f}%  (peak {peak_coh:.0f}%)")
        self._stat_row(card_lay, "Average Sync Score",
                       f"{avg_sync:.0%}")
        self._stat_row(card_lay, "Flow State",
                       f"{self._fmt_time(flow_t)}  ({flow_pct}% of session)")
        self._stat_row(card_lay, "Starting Breath Rate",
                       f"{s.start_rate_bpm:.1f} BPM",
                       value_col="#a8d0e8")
        self._stat_row(card_lay, "Ending Breath Rate",
                       f"{end_rate:.1f} BPM",
                       value_col="#80c8a8")
        if avg_hr > 0:
            self._stat_row(card_lay, "Average Heart Rate",
                           f"{avg_hr:.0f} BPM")

        # Divider
        card_lay.addSpacing(4)
        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setStyleSheet("color: rgba(60, 110, 160, 80); background: rgba(60,110,160,50); border: none;")
        line2.setFixedHeight(1)
        card_lay.addWidget(line2)
        card_lay.addSpacing(4)

        # Coherence time breakdown
        coh_header = QLabel("COHERENCE BREAKDOWN")
        coh_header.setAlignment(Qt.AlignCenter)
        coh_header.setStyleSheet(
            "color: rgba(110, 170, 210, 170); font: 11px 'Segoe UI'; "
            "letter-spacing: 3px; background: transparent; border: none;"
        )
        card_lay.addWidget(coh_header)

        coh_bar_widget = QWidget()
        coh_bar_widget.setFixedHeight(32)
        coh_bar_widget.setStyleSheet("background: transparent; border: none;")
        card_lay.addWidget(coh_bar_widget)
        self._coh_bar_data = (low_pct, mid_pct, high_pct)
        self._coh_bar_widget = coh_bar_widget

        # Legend for coherence bar
        legend_text = f"Low {low_pct}%   \u00b7   Medium {mid_pct}%   \u00b7   High {high_pct}%"
        legend = QLabel(legend_text)
        legend.setAlignment(Qt.AlignCenter)
        legend.setStyleSheet(
            "color: rgba(150, 190, 220, 160); font: 10px 'Segoe UI'; "
            "background: transparent; border: none;"
        )
        card_lay.addWidget(legend)

        # Divider
        card_lay.addSpacing(4)
        line3 = QFrame()
        line3.setFrameShape(QFrame.HLine)
        line3.setStyleSheet("color: rgba(60, 110, 160, 80); background: rgba(60,110,160,50); border: none;")
        line3.setFixedHeight(1)
        card_lay.addWidget(line3)
        card_lay.addSpacing(4)

        # Tips for next session
        tip = self._generate_tip(avg_coh, avg_sync, end_rate, flow_pct)
        tip_lbl = QLabel(tip)
        tip_lbl.setAlignment(Qt.AlignCenter)
        tip_lbl.setWordWrap(True)
        tip_lbl.setStyleSheet(
            "color: rgba(170, 210, 235, 180); font: 12px 'Segoe UI'; "
            "font-style: italic; background: transparent; border: none;"
        )
        card_lay.addWidget(tip_lbl)

        card_lay.addSpacing(10)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        btn_style = (
            "QPushButton { background: rgba(20, 70, 130, 180); color: #c0dff8; "
            "font: 14px 'Segoe UI'; border: 1px solid rgba(60, 130, 200, 120); "
            "border-radius: 7px; padding: 9px 28px; letter-spacing: 2px; } "
            "QPushButton:hover { background: rgba(30, 90, 160, 220); "
            "border: 1px solid rgba(80, 160, 240, 180); } "
            "QPushButton:pressed { background: rgba(15, 55, 110, 220); }"
        )

        btn_new = QPushButton("New Session")
        btn_new.setStyleSheet(btn_style)
        btn_new.setCursor(Qt.PointingHandCursor)
        btn_new.clicked.connect(self.new_session)

        btn_quit = QPushButton("Quit")
        btn_quit.setStyleSheet(btn_style)
        btn_quit.setCursor(Qt.PointingHandCursor)
        btn_quit.clicked.connect(self.quit_app)

        btn_row.addWidget(btn_new)
        btn_row.addWidget(btn_quit)
        card_lay.addLayout(btn_row)

        outer.addWidget(card, 0, Qt.AlignCenter)

    @staticmethod
    def _generate_tip(avg_coh, avg_sync, end_rate, flow_pct):
        """Generate a personalized tip based on session performance."""
        if avg_coh < 25:
            return ("\U0001f4a1 Tip: Focus on the SOUND of the waves rather than "
                    "the visuals. Try closing your eyes and breathing with the audio guide.")
        if avg_sync < 0.35:
            return ("\U0001f4a1 Tip: Don't try to match the guide exactly. "
                    "Just slow your breathing gradually and let sync grow naturally.")
        if end_rate > 10:
            return ("\U0001f4a1 Tip: Your breathing rate is still fast. "
                    "Try a longer session (5+ minutes) to give your body more time to slow down.")
        if flow_pct < 20:
            return ("\U0001f4a1 Tip: You're close! Try extending your exhale — "
                    "make it 1.5\u00d7 longer than your inhale for deeper coherence.")
        if avg_coh > 60 and flow_pct > 40:
            return ("\u2728 Excellent session! You achieved strong coherence. "
                    "Try maintaining this for longer in your next session.")
        return ("\U0001f4a1 Tip: Consistency matters. Regular 5-minute sessions "
                "build the neural pathways for faster coherence in future.")

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 5, 15, 210))

        # Draw coherence bar if data is available
        if hasattr(self, '_coh_bar_widget') and hasattr(self, '_coh_bar_data'):
            w = self._coh_bar_widget
            geo = w.geometry()
            # Get position relative to this widget (the overlay)
            bar_x = geo.x() + 50  # Match card margins
            bar_y = geo.y() + geo.height() // 2 - 6
            bar_w = geo.width() - 10
            bar_h = 12

            low_pct, mid_pct, high_pct = self._coh_bar_data

            # Low (blue)
            low_w = max(1, int(bar_w * low_pct / 100))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(50, 100, 180, 180))
            p.drawRoundedRect(bar_x, bar_y, low_w, bar_h, 4, 4)

            # Medium (teal)
            mid_w = max(1, int(bar_w * mid_pct / 100))
            p.setBrush(QColor(50, 190, 150, 180))
            p.drawRect(bar_x + low_w, bar_y, mid_w, bar_h)

            # High (gold)
            high_w = max(1, int(bar_w * high_pct / 100))
            p.setBrush(QColor(220, 190, 70, 180))
            p.drawRoundedRect(bar_x + low_w + mid_w, bar_y, high_w, bar_h, 4, 4)

        p.end()


# ===================================================================
#  MAIN WINDOW
# ===================================================================

class ZenBreathingApp(QMainWindow):
    """
    QStackedWidget:
      page 0 = WelcomeScreen
      page 1 = Session (visual + debug + overlays)
    """

    def __init__(self, mode=None, fast=False, theme="ocean", audio_on=True):
        super().__init__()
        self.setWindowTitle("ZenBreathing")
        self.setStyleSheet("background-color: black;")
        self.setMinimumSize(800, 600)

        self.state = BreathingState()

        self._theme    = theme
        self._fast     = fast
        self._audio_on = audio_on
        self._forced_mode = mode

        # ---- Stack ----
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # Page 0: Welcome
        self._welcome = WelcomeScreen()
        self._welcome.start_session.connect(self._on_start_session)
        self._stack.addWidget(self._welcome)

        # Page 1: built on demand
        self._session_widget = None
        self._visual   = None
        self._hud      = None
        self._guide    = None
        self._debug    = None
        self._guide_bar = None

        # Engines
        self._simulator    = None
        self._live_proc    = None
        self._ble_mgr      = None
        self._data_bus     = None
        self._connect_overlay = None
        self._report_overlay  = None

        self._paused         = False
        self._hud_visible    = False
        self._guide_bar_visible = True   # on by default
        self._debug_visible  = False
        self._tick_timer     = None

        # Audio + Logger
        self.audio  = AudioEngine(self.state)
        self.logger = DataLogger(self.state)

        # CLI shortcut: skip welcome screen
        if self._forced_mode:
            _src = "mock" if self._forced_mode in ("demo", "mock") else "polar"
            QTimer.singleShot(100, lambda: self._on_start_session(_src, self._theme))

    # ------------------------------------------------------------------ #
    #  Session startup
    # ------------------------------------------------------------------ #

    def _on_start_session(self, source: str, theme: str = None):
        if theme and theme != self._theme:
            self._theme = theme
            if self._visual is not None:
                self._visual.set_theme(theme)
            if self._guide is not None:
                self._guide.update_theme(theme)

        if self._session_widget is None:
            self._build_session_page()

        self._stack.setCurrentIndex(1)

        if source == "mock":
            self._simulator = DemoSimulator(self.state, fast=self._fast)
            self._live_proc = None
            self._start_session_active()
        elif source == "polar":
            from zen_breathing.polar_data_bus import PolarDataBus
            from zen_breathing.ble_manager import BLEManager
            self._data_bus  = PolarDataBus()
            self._live_proc = LiveProcessor(self.state, self._data_bus)
            self._simulator = None
            self._show_polar_connect()

    def _build_session_page(self):
        self._session_widget = QWidget()
        session_layout = QHBoxLayout(self._session_widget)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(0)

        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setStyleSheet(
            "QSplitter::handle { background: rgba(30, 60, 90, 100); width: 2px; }"
        )

        self._visual = ZenVisualWidget(self.state, theme=self._theme)
        self._splitter.addWidget(self._visual)

        self._debug = DebugPanel(self.state)
        self._debug.setVisible(self._debug_visible)
        self._splitter.addWidget(self._debug)

        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)

        session_layout.addWidget(self._splitter)

        # Overlays (all children of visual, so they float on top)
        self._hud = HUDOverlay(self.state, parent=self._visual)
        self._hud.setVisible(True)  # HUD visible by default now
        self._hud_visible = True

        self._guide = GuideOverlay(self.state, theme=self._theme, parent=self._visual)
        self._guide.setGeometry(0, 0, 500, 260)

        self._guide_bar = BreathingRingOverlay(self.state, parent=self._visual)
        # Positioned at bottom-center in resizeEvent

        self._stack.addWidget(self._session_widget)

    # ------------------------------------------------------------------ #
    #  Polar connection overlay
    # ------------------------------------------------------------------ #

    def _show_polar_connect(self):
        self._connect_overlay = PolarConnectOverlay(self._data_bus, parent=self)
        self._connect_overlay.setGeometry(0, 0, self.width(), self.height())
        self._connect_overlay.fallback_to_demo.connect(self._fallback_to_demo)
        self._connect_overlay.show()
        self._connect_overlay.raise_()

        from zen_breathing.ble_manager import BLEManager
        self._ble_mgr = BLEManager(self._data_bus)
        self._ble_mgr.status.connect(self._connect_overlay.update_status)
        self._ble_mgr.connected.connect(self._on_ble_connected)
        self._ble_mgr.streams_ready.connect(self._on_streams_ready)
        self._ble_mgr.device_found.connect(self._on_device_found)
        self._ble_mgr.scan()

    def _on_device_found(self, name: str, address: str):
        print(f"[App] Found {name} [{address}] -- connecting...")
        if self._connect_overlay:
            self._connect_overlay.update_status(f"Found {name} -- connecting...")
        try:
            self._ble_mgr.device_found.disconnect(self._on_device_found)
        except TypeError:
            pass
        if self._ble_mgr:
            self._ble_mgr.connect_device(address)

    def _on_ble_connected(self, connected: bool):
        status = "Polar H10 connected" if connected else "Polar H10 disconnected"
        print(f"[App] {status}")
        if connected and self._connect_overlay:
            self._connect_overlay.update_status(
                "Connected! Waiting for HR + ECG + ACC streams..."
            )

    def _on_streams_ready(self):
        print("[App] All Polar streams active -- starting session")
        if self._connect_overlay:
            self._connect_overlay._poll.stop()
            self._connect_overlay.hide()
            self._connect_overlay.deleteLater()
            self._connect_overlay = None
        self._start_session_active()

    def _fallback_to_demo(self):
        print("[App] Falling back to demo mode")
        if self._ble_mgr:
            self._ble_mgr.disconnect()
            self._ble_mgr = None
        if self._connect_overlay:
            self._connect_overlay._poll.stop()
            self._connect_overlay.hide()
            self._connect_overlay.deleteLater()
            self._connect_overlay = None
        self._live_proc = None
        self._data_bus  = None
        self._simulator = DemoSimulator(self.state, fast=self._fast)
        self._start_session_active()

    # ------------------------------------------------------------------ #
    #  Session active (both mock and live)
    # ------------------------------------------------------------------ #

    def _start_session_active(self):
        if self._audio_on:
            self.audio.start()
        self.logger.start()

        if self._tick_timer is None:
            self._tick_timer = QTimer(self)
            self._tick_timer.timeout.connect(self._tick)
            self._tick_timer.start(16)   # ~60 Hz

        if self.isFullScreen():
            self.setCursor(QCursor(Qt.BlankCursor))

        if self._guide_bar:
            self._guide_bar.setVisible(self._guide_bar_visible)

        self._show_session_title()

    def _show_session_title(self):
        self._title_label = QLabel("Z E N B R E A T H I N G", self._visual)
        self._title_label.setAlignment(Qt.AlignCenter)
        self._title_label.setStyleSheet(
            "color: rgba(100, 180, 230, 200); background: transparent; "
            "font: 28px 'Segoe UI'; font-weight: 300; letter-spacing: 12px;"
        )
        self._title_label.setGeometry(0, 0, self._visual.width(), self._visual.height())
        self._title_label.show()
        self._title_opacity = QGraphicsOpacityEffect(self._title_label)
        self._title_label.setGraphicsEffect(self._title_opacity)
        self._title_opacity.setOpacity(1.0)
        QTimer.singleShot(3500, self._fade_title)

    # ------------------------------------------------------------------ #
    #  End session (early or natural)
    # ------------------------------------------------------------------ #

    def _end_session(self):
        """Stop the session and show the report overlay."""
        if self._tick_timer:
            self._tick_timer.stop()
            self._tick_timer = None

        self.audio.stop()
        self.logger.stop()

        if self._simulator:
            self._simulator.stop()
        if self._live_proc:
            self._live_proc.stop()

        # Hide breath guide bar during report
        if self._guide_bar:
            self._guide_bar.setVisible(False)
        if self._hud:
            self._hud.setVisible(False)

        # Show cursor for button interaction
        self.setCursor(QCursor(Qt.ArrowCursor))

        # Build and show report overlay
        self._report_overlay = SessionReportOverlay(self.state, parent=self)
        self._report_overlay.setGeometry(0, 0, self.width(), self.height())
        self._report_overlay.new_session.connect(self._restart_session)
        self._report_overlay.quit_app.connect(self.close)
        self._report_overlay.show()
        self._report_overlay.raise_()

    def _restart_session(self):
        """Return to welcome screen for a new session."""
        if self._report_overlay:
            self._report_overlay.hide()
            self._report_overlay.deleteLater()
            self._report_overlay = None

        # Reset state
        self.state.__init__()

        # Reset session widget so it gets rebuilt
        if self._session_widget:
            self._splitter = None
            self._visual   = None
            self._hud      = None
            self._guide    = None
            self._guide_bar = None
            self._debug    = None
            self._stack.removeWidget(self._session_widget)
            self._session_widget.deleteLater()
            self._session_widget = None

        self._simulator   = None
        self._live_proc   = None
        self._paused      = False

        # Go back to welcome
        self._stack.setCurrentIndex(0)
        self.setCursor(QCursor(Qt.ArrowCursor))

    # ------------------------------------------------------------------ #
    #  Engine tick
    # ------------------------------------------------------------------ #

    def _tick(self):
        if self._paused:
            return
        if self._simulator:
            self._simulator.update()
        elif self._live_proc:
            self._live_proc.update()
        self.logger.update()

    # ------------------------------------------------------------------ #
    #  Title fade
    # ------------------------------------------------------------------ #

    def _fade_title(self):
        self._title_anim = QPropertyAnimation(self._title_opacity, b"opacity")
        self._title_anim.setDuration(3000)
        self._title_anim.setStartValue(1.0)
        self._title_anim.setEndValue(0.0)
        self._title_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._title_anim.finished.connect(self._title_label.hide)
        self._title_anim.start()

    # ------------------------------------------------------------------ #
    #  Keyboard
    # ------------------------------------------------------------------ #

    def keyPressEvent(self, event):
        key = event.key()

        # Welcome screen
        if self._stack.currentIndex() == 0:
            if key == Qt.Key_Escape:
                self.close()
            elif key == Qt.Key_F11:
                self._toggle_fullscreen()
            else:
                super().keyPressEvent(event)
            return

        # Connect overlay
        if self._connect_overlay and self._connect_overlay.isVisible():
            if key == Qt.Key_Escape:
                self._fallback_to_demo()
            return

        # Report overlay visible -- only Esc closes
        if self._report_overlay and self._report_overlay.isVisible():
            if key == Qt.Key_Escape:
                self.close()
            return

        # Session screen
        if key == Qt.Key_F11:
            self._toggle_fullscreen()
        elif key == Qt.Key_H:
            self._hud_visible = not self._hud_visible
            if self._hud:
                self._hud.setVisible(self._hud_visible)
        elif key == Qt.Key_B:
            self._guide_bar_visible = not self._guide_bar_visible
            if self._guide_bar:
                self._guide_bar.setVisible(self._guide_bar_visible)
        elif key == Qt.Key_D:
            self._debug_visible = not self._debug_visible
            if self._debug:
                self._debug.setVisible(self._debug_visible)
        elif key == Qt.Key_A:
            if self._audio_on:
                self.audio.stop()
                self._audio_on = False
            else:
                self.audio.start()
                self._audio_on = True
        elif key == Qt.Key_T:
            if self._visual:
                cycle = {"ocean": "aurora", "aurora": "orb", "orb": "ocean"}
                nxt = cycle.get(self._visual._theme_name, "ocean")
                self._visual.set_theme(nxt)
                self._theme = nxt
                if self._guide:
                    self._guide.update_theme(nxt)   # update guide language
        elif key == Qt.Key_G:
            if self._guide:
                self._guide.show_again()
        elif key == Qt.Key_Space:
            self._paused = not self._paused
        elif key == Qt.Key_E:
            # End session early -- show report
            if self._tick_timer and self._tick_timer.isActive():
                self._end_session()
        elif key == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.setCursor(QCursor(Qt.ArrowCursor))
        else:
            self.showFullScreen()
            if self._tick_timer and self._tick_timer.isActive():
                self.setCursor(QCursor(Qt.BlankCursor))

    # ------------------------------------------------------------------ #
    #  Resize
    # ------------------------------------------------------------------ #

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._hud:
            w = self._visual.width() if self._visual else self.width()
            self._hud.setGeometry(0, 0, min(w, 500), 110)
        if self._guide and self._visual:
            w, h = self._visual.width(), self._visual.height()
            gw, gh = min(520, w - 40), 280
            self._guide.setGeometry((w - gw) // 2, (h - gh) // 2 - 30, gw, gh)
        if self._guide_bar and self._visual:
            vw = self._visual.width()
            vh = self._visual.height()
            rw, rh = BreathingRingOverlay.RING_W, BreathingRingOverlay.RING_H
            self._guide_bar.setGeometry((vw - rw) // 2, vh - rh - 8, rw, rh)
        if hasattr(self, "_title_label") and self._title_label.isVisible() and self._visual:
            self._title_label.setGeometry(0, 0, self._visual.width(), self._visual.height())
        if self._connect_overlay and self._connect_overlay.isVisible():
            self._connect_overlay.setGeometry(0, 0, self.width(), self.height())
        if self._report_overlay and self._report_overlay.isVisible():
            self._report_overlay.setGeometry(0, 0, self.width(), self.height())

    # ------------------------------------------------------------------ #
    #  Cleanup
    # ------------------------------------------------------------------ #

    def closeEvent(self, event):
        self.audio.stop()
        self.logger.stop()
        if self._simulator:
            self._simulator.stop()
        if self._live_proc:
            self._live_proc.stop()
        if self._ble_mgr:
            self._ble_mgr.disconnect()
        event.accept()
