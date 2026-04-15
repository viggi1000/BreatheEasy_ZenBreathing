"""
Thread-safe shared breathing state with EMA smoothing.

All modules read from / write to this single object.
Python's GIL makes single-float attribute assignments atomic enough
for our purposes (one writer thread, one reader thread).
"""

import time
import threading
from collections import deque


class BreathingState:
    """Central state object shared between engine, visual, and audio."""

    def __init__(self):
        self._lock = threading.Lock()

        # --- Breath ---
        self.breath_phase = 0.0        # 0.0 = exhale trough, 1.0 = inhale peak
        self.breath_rate_bpm = 14.0    # current estimated breath rate
        self.is_inhaling = False
        self.breath_depth = 0.0        # normalised breath amplitude

        # --- Exhale / Inhale ratio ---
        self.exhale_inhale_ratio = 1.0  # >1 means longer exhale (calming)
        self.target_ei_ratio = 1.5      # target: slightly longer exhale

        # --- Biofeedback ---
        self.coherence = 0.0           # 0-100, how well user matches pacer
        self.sync_score = 0.0          # 0-1, instantaneous sync (visual reward)
        self.asi = 0.0                 # 0-100, Autonomic State Index
        self.rsa_amplitude = 0.0       # RSA power (arbitrary units)

        # --- Three-tier scoring outputs ---
        self.tier1_direction = 0.0     # 0-1, instant direction match
        self.tier2_xcorr = 0.0         # 0-1, cross-correlation sync
        self.tier2_rate = 0.0          # 0-1, rate match
        self.tier3_spectral = 0.0      # 0-1, spectral coherence
        self.dominant_bpm = 0.0        # dominant breathing frequency in BPM
        self.estimated_lag = 0.0       # cross-correlation estimated lag (seconds)

        # --- Micro-message system ---
        self.micro_message = ""        # current micro-feedback text
        self.micro_message_opacity = 0.0  # 0-1 for fade animation

        # --- Pacing guide ---
        self.target_rate_bpm = 6.0     # ultimate resonance target
        self.current_pace_bpm = 14.0   # current pacing rate (starts at user's rate)
        self.target_phase = 0.0        # 0-1, pacing guide phase

        # --- Calibration ---
        self.is_calibrated = False
        self.calibrated_rate_bpm = 0.0  # user's natural resting rate
        self.resonance_bpm = 6.0        # estimated resonance frequency

        # --- HRV snapshot ---
        self.heart_rate = 72.0
        self.rmssd = 0.0
        self.lf_hf = 0.0

        # --- Session ---
        self.session_time = 0.0        # seconds since start
        self.session_phase = "idle"    # idle | calibrating | training | flow

        # Session statistics (accumulated, for end-of-session report)
        self.phase_times = {"calibrating": 0.0, "training": 0.0, "flow": 0.0}
        self.start_rate_bpm = 14.0     # user's calibrated resting rate
        self.end_rate_bpm = 14.0       # breathing rate at session end

        # Coherence time tracking (for session report)
        self.time_low_coherence = 0.0    # coherence < 33
        self.time_mid_coherence = 0.0    # coherence 33-66
        self.time_high_coherence = 0.0   # coherence >= 66
        self.peak_coherence = 0.0        # max coherence reached
        self.avg_coherence_sum = 0.0     # running sum for average
        self.avg_coherence_count = 0     # sample count

        # --- Data buffers for debug plots (ring buffers, ~30s at update rate) ---
        self._history_len = 1800       # 30s at 60Hz
        self.breath_phase_history = deque(maxlen=self._history_len)
        self.target_phase_history = deque(maxlen=self._history_len)
        self.breath_rate_history = deque(maxlen=300)   # 5s at 60Hz (downsampled)
        self.coherence_history = deque(maxlen=300)
        self.heart_rate_history = deque(maxlen=300)
        self.sync_score_history = deque(maxlen=self._history_len)

        # Raw filtered breathing signal for spectral coherence (not 0-1 phase)
        # Phase wraps at 0→1 create artificial spectral harmonics that
        # contaminate coherence estimates, so Tier 3 uses this instead.
        self.raw_resp_signal_history = deque(maxlen=self._history_len)
        self.raw_resp_signal = 0.0     # current raw filtered ACC/fused value

        # Raw ACC data for debug plot (last 10s at 100Hz)
        self.acc_x_history = deque(maxlen=1000)
        self.acc_y_history = deque(maxlen=1000)
        self.acc_z_history = deque(maxlen=1000)
        self.resp_signal_history = deque(maxlen=1000)

        # Separate ACC-derived and ECG-derived respiration signals for debug
        self.acc_resp_history = deque(maxlen=1000)   # causal-filtered ACC signal
        self.ecg_resp_history = deque(maxlen=500)    # EDR envelope values
        self.ecg_raw_history  = deque(maxlen=650)    # raw ECG uV (last ~5s at 130Hz)
        self.acc_resp_signal  = 0.0   # current ACC-derived resp value
        self.ecg_resp_signal  = 0.0   # current ECG-derived resp value

        # --- Smoothed copies for the visual (EMA-filtered) ---
        self._smooth_breath = 0.0
        self._smooth_coherence = 0.0
        self._smooth_asi = 0.0
        self._smooth_target = 0.0
        self._smooth_sync = 0.0

        self._last_smooth_t = time.perf_counter()

        # Downsample counter for history
        self._history_tick = 0

    def push_history(self):
        """Call once per engine tick to record history for debug plots."""
        self.breath_phase_history.append(self.breath_phase)
        self.target_phase_history.append(self.target_phase)
        self.sync_score_history.append(self.sync_score)
        self.raw_resp_signal_history.append(self.raw_resp_signal)

        self._history_tick += 1
        if self._history_tick % 6 == 0:  # ~10 Hz for slower signals
            self.breath_rate_history.append(self.breath_rate_bpm)
            self.coherence_history.append(self.coherence)
            self.heart_rate_history.append(self.heart_rate)

        # Coherence time tracking
        dt = 1.0 / 60.0  # approximate per-tick dt
        if self.coherence < 33:
            self.time_low_coherence += dt
        elif self.coherence < 66:
            self.time_mid_coherence += dt
        else:
            self.time_high_coherence += dt
        self.peak_coherence = max(self.peak_coherence, self.coherence)
        self.avg_coherence_sum += self.coherence
        self.avg_coherence_count += 1

    # ------------------------------------------------------------------ #
    #  EMA-smoothed getters (call from visual/audio thread)
    # ------------------------------------------------------------------ #

    def get_smooth(self, alpha: float = 0.12):
        """Return EMA-smoothed values. Call once per frame."""
        now = time.perf_counter()
        dt = now - self._last_smooth_t
        self._last_smooth_t = now

        # Adaptive alpha: faster response at high dt (lag recovery)
        a = min(1.0, alpha * dt * 60.0)  # tuned for 60 fps

        self._smooth_breath += a * (self.breath_phase - self._smooth_breath)
        self._smooth_coherence += a * (self.coherence / 100.0 - self._smooth_coherence)
        self._smooth_asi += a * (self.asi / 100.0 - self._smooth_asi)
        # Target phase: pass through unsmoothed — it's already mathematically smooth
        self._smooth_target = self.target_phase
        self._smooth_sync += a * (self.sync_score - self._smooth_sync)

        return {
            "breath": self._smooth_breath,
            "coherence": self._smooth_coherence,
            "energy": self._smooth_asi,
            "target": self._smooth_target,
            "sync": self._smooth_sync,
            "rate_bpm": self.breath_rate_bpm,
            "pace_bpm": self.current_pace_bpm,
            "session_time": self.session_time,
            "phase": self.session_phase,
            "heart_rate": self.heart_rate,
        }
