"""
Biofeedback engine — three-tier scoring, pacing, coherence, and gentle nudging.

Session flow:
  1. CALIBRATING (first ~15s): measure user's natural breathing rate
  2. TRAINING: pace starts at user's rate, nudges toward resonance
  3. FLOW: user is synchronized, visual/audio reward maximised

Three-tier scoring architecture (lag-immune):
  TIER 1 — INSTANT:   Direction match (inhale/exhale slope, ~30ms)
  TIER 2 — SHORT:     Rate match + Cross-correlation (15s window)
  TIER 3 — LONG:      Spectral coherence (HeartMath-style, 30s FFT)

The pacer never jumps — it glides at max ~1 BPM/min from wherever
the user is to wherever resonance is.
"""

import math
import time
import random
import numpy as np
from collections import deque


# ── Micro-messages for contextual feedback ───────────────────────────────
MICRO_MESSAGES = {
    "great_sync": [
        "Beautiful rhythm — you're in sync ✓",
        "Perfect. Stay with this feeling.",
        "You've found it. Keep this pace.",
        "Wonderful. Your breath and the guide are one.",
    ],
    "improving": [
        "Getting closer — slow down a little more.",
        "Nice. Extend your exhale slightly.",
        "Good progress — let it become effortless.",
    ],
    "struggling": [
        "Don't worry about matching perfectly.",
        "Focus on the SOUND of the wave.",
        "Try closing your eyes and just listening.",
        "Just breathe naturally and slowly.",
    ],
}


class BiofeedbackEngine:
    """
    Drives the breathing guidance loop.

    Call ``update()`` at ~60 Hz from the engine thread.
    Reads current breath state, writes pacing + coherence to BreathingState.
    """

    def __init__(self, state, target_rate_bpm: float = 6.0):
        self.state = state

        # Resonance target (can be personalised later)
        self._resonance_bpm = target_rate_bpm
        self.state.resonance_bpm = target_rate_bpm
        self.state.target_rate_bpm = target_rate_bpm

        # Pacing — will be set after calibration
        self._current_pace_bpm = 14.0
        self._max_nudge_bpm_per_sec = 1.0 / 60.0  # 1.0 BPM per minute

        # Calibration
        self._calibration_duration = 15.0
        self._calibration_rates = deque(maxlen=600)
        self._calibrated = False

        # Three-tier scoring state
        self._sync_ema = 0.0
        self._coherence_ema = 0.0
        self._spectral_coherence = 0.0
        self._spectral_dom_bpm = 0.0
        self._tick = 0

        # Exhale / inhale ratio tracking
        self._last_phase = 0.0
        self._phase_rising = True
        self._inhale_start_t = 0.0
        self._exhale_start_t = 0.0
        self._last_inhale_dur = 2.0
        self._last_exhale_dur = 2.0
        self._ei_ratio_ema = 1.0

        # Pacing waveform with asymmetric exhale/inhale
        self._pace_phase_acc = 0.0

        # Session timing
        self._session_t = 0.0

        # Micro-message cooldown
        self._last_msg_time = 0.0
        self._last_msg_category = ""
        self._msg_cooldown = 18.0  # seconds between messages

    @property
    def pace_bpm(self) -> float:
        return self._current_pace_bpm

    # ------------------------------------------------------------------ #
    #  Main update (call every frame, ~60 Hz)
    # ------------------------------------------------------------------ #

    def update(self, dt: float):
        self._session_t += dt
        t = self._session_t
        self.state.session_time = t
        self._tick += 1

        # --- Calibration phase ---
        if not self._calibrated:
            self._run_calibration(t)
        else:
            self._nudge(dt)

        # --- Generate pacing waveform ---
        self._generate_pace(dt, t)

        # =============================================================
        #  TIER 1: INSTANT — Direction match (~30ms latency)
        # =============================================================
        guide_inhaling = self.state.target_phase > 0.5
        dir_sync = 1.0 if (self.state.is_inhaling == guide_inhaling) else 0.0
        self.state.tier1_direction = dir_sync

        # =============================================================
        #  TIER 2: SHORT-TERM — Rate match + Cross-correlation
        # =============================================================
        # Rate component: ±5 BPM tolerance
        rate_err = abs(self.state.breath_rate_bpm - self._current_pace_bpm)
        rate_sync = max(0.0, 1.0 - rate_err / 5.0)
        self.state.tier2_rate = rate_sync

        # Cross-correlation (runs every 30 ticks = ~0.5s to save CPU)
        xcorr_sync = self.state.tier2_xcorr
        est_lag = self.state.estimated_lag
        if self._tick % 30 == 0:
            xcorr_sync, est_lag = self._compute_xcorr_sync(
                self.state.breath_phase_history,
                self.state.target_phase_history,
                fs=60
            )
            self.state.tier2_xcorr = xcorr_sync
            self.state.estimated_lag = est_lag

        # Visual sync: lag-immune components dominate
        raw_sync = 0.35 * dir_sync + 0.35 * rate_sync + 0.30 * xcorr_sync
        alpha_sync = 0.04
        self._sync_ema += alpha_sync * (raw_sync - self._sync_ema)
        self.state.sync_score = self._sync_ema

        # =============================================================
        #  TIER 3: LONG-TERM — Spectral Coherence (every 2s)
        # =============================================================
        if self._tick % 120 == 0:
            coh, dom_bpm = self._compute_spectral_coherence(
                self.state.raw_resp_signal_history, fs=60, window_sec=30
            )
            self._spectral_coherence = coh
            self._spectral_dom_bpm = dom_bpm
            self.state.tier3_spectral = coh
            self.state.dominant_bpm = dom_bpm

        # Coherence display: spectral coherence scaled to 0-100
        raw_coherence = self._spectral_coherence * 100.0
        # Blend in a portion of the sync score for responsiveness
        blended_coherence = 0.65 * raw_coherence + 0.35 * self._sync_ema * 100.0
        self._coherence_ema += 0.008 * (blended_coherence - self._coherence_ema)
        self.state.coherence = self._coherence_ema

        # --- ASI proxy ---
        time_factor = min(1.0, t / 180.0)
        self.state.asi = self._coherence_ema * 0.6 + time_factor * 40.0

        # --- Track exhale/inhale ratio ---
        self._track_ei_ratio(t)

        # --- Session phase ---
        if not self._calibrated:
            self.state.session_phase = "calibrating"
        elif self._coherence_ema < 40:
            self.state.session_phase = "training"
        else:
            self.state.session_phase = "flow"

        # --- Update end BPM for report ---
        self.state.end_rate_bpm = self.state.breath_rate_bpm

        # Accumulate time in each phase (for session report)
        phase_key = self.state.session_phase
        if phase_key in self.state.phase_times:
            self.state.phase_times[phase_key] += dt

        # --- Micro-messages ---
        self._update_micro_messages(t)

        # --- Push to history buffers ---
        self.state.push_history()

    # ------------------------------------------------------------------ #
    #  Cross-Correlation Sync (Tier 2)
    # ------------------------------------------------------------------ #

    def _compute_xcorr_sync(self, user_buf, guide_buf, fs=60):
        """
        Sliding-window cross-correlation between user phase and guide phase.
        Returns (sync_score 0-1, estimated_lag_seconds).

        The result is normalized against the user's autocorrelation zero-lag
        peak so that the score reflects shape similarity independent of
        the filter's amplitude response at the discovered lag.
        """
        window_sec = 15
        n = int(window_sec * fs)

        if len(user_buf) < n or len(guide_buf) < n:
            return 0.5, 0.0

        user = np.array(list(user_buf)[-n:], dtype=np.float64)
        guide = np.array(list(guide_buf)[-n:], dtype=np.float64)

        # Normalize
        u_std = np.std(user)
        g_std = np.std(guide)
        if u_std < 1e-6 or g_std < 1e-6:
            return 0.3, 0.0

        user = (user - np.mean(user)) / u_std
        guide = (guide - np.mean(guide)) / g_std

        corr = np.correlate(user, guide, mode='full')
        corr /= n

        # Autocorrelation zero-lag peak for normalization
        auto_user = np.sum(user * user) / n

        # Search in plausible lag range (0 to 5 seconds)
        center = n - 1
        max_lag_samples = int(5.0 * fs)
        search = corr[center:center + max_lag_samples]

        if len(search) == 0:
            return 0.3, 0.0

        best_idx = np.argmax(search)
        # Normalize against autocorrelation to compensate for filter amplitude
        raw_score = search[best_idx] / (auto_user + 1e-8)
        sync_score = float(np.clip(raw_score, 0, 1))
        lag_sec = best_idx / fs

        return sync_score, lag_sec

    # ------------------------------------------------------------------ #
    #  Spectral Coherence (Tier 3) — HeartMath-style
    # ------------------------------------------------------------------ #

    def _compute_spectral_coherence(self, resp_history, fs=60, window_sec=30):
        """
        HeartMath-style coherence ratio on the raw filtered breathing signal.

        Uses the **raw filtered signal** (not 0-1 phase) to avoid artificial
        spectral harmonics from phase wrapping.

        HeartMath formula (Frontiers in Public Health, 2017):
            coherence = P_peak / (P_total - P_peak)^2
        The denominator is squared per published specification.
        """
        n = int(window_sec * fs)
        if len(resp_history) < n:
            return 0.0, 0.0

        signal = np.array(list(resp_history)[-n:], dtype=np.float64)
        signal = signal - np.mean(signal)

        # Apply Hann window to reduce spectral leakage
        window = np.hanning(len(signal))
        signal = signal * window

        freqs = np.fft.rfftfreq(n, 1.0 / fs)
        psd = np.abs(np.fft.rfft(signal)) ** 2

        # Breathing band: 0.05 - 0.5 Hz (3 - 30 BPM)
        band = (freqs >= 0.05) & (freqs <= 0.5)
        band_power = psd[band]
        band_freqs = freqs[band]

        total_band_power = np.sum(band_power)
        if total_band_power < 1e-8:
            return 0.0, 0.0

        # Normalize PSD to sum-to-1 (required for HeartMath formula)
        # Without this, the squared denominator produces scale-dependent
        # near-zero values on raw FFT magnitudes.
        band_power_norm = band_power / total_band_power

        # Find dominant peak
        peak_idx = np.argmax(band_power_norm)
        peak_freq = band_freqs[peak_idx]

        # Power in \u00b10.015 Hz around peak (HeartMath uses \u00b10.015 Hz)
        peak_band = ((band_freqs >= peak_freq - 0.015) &
                     (band_freqs <= peak_freq + 0.015))
        peak_power = np.sum(band_power_norm[peak_band])

        # HeartMath coherence ratio: P_peak / (P_remainder)^2
        # Applied to normalized PSD so total = 1.0
        remainder = 1.0 - peak_power + 1e-8
        coherence = peak_power / (remainder ** 2)

        # Normalize to 0-1: perfect sine -> ~6.0, pure noise -> ~0.06
        normalized = float(np.clip(coherence / 6.0, 0, 1))

        return normalized, float(peak_freq * 60)

    # ------------------------------------------------------------------ #
    #  Micro-message system
    # ------------------------------------------------------------------ #

    def _update_micro_messages(self, t: float):
        """Fire contextual micro-messages based on sync state."""
        if t - self._last_msg_time < self._msg_cooldown:
            # Fade out existing message
            if self.state.micro_message_opacity > 0:
                self.state.micro_message_opacity = max(
                    0.0, self.state.micro_message_opacity - 0.008)
            return

        if not self._calibrated:
            return

        sync = self.state.sync_score
        if sync >= 0.65:
            cat = "great_sync"
        elif sync >= 0.40:
            cat = "improving"
        else:
            cat = "struggling"

        # Avoid repeating the same category consecutively
        if cat == self._last_msg_category and t - self._last_msg_time < 45:
            return

        msgs = MICRO_MESSAGES.get(cat, [])
        if msgs:
            self.state.micro_message = random.choice(msgs)
            self.state.micro_message_opacity = 1.0
            self._last_msg_time = t
            self._last_msg_category = cat

    # ------------------------------------------------------------------ #
    #  Calibration
    # ------------------------------------------------------------------ #

    def _run_calibration(self, t: float):
        """Collect breathing rate samples during calibration window."""
        rate = self.state.breath_rate_bpm
        if 3.0 <= rate <= 30.0:
            self._calibration_rates.append(rate)

        if t >= self._calibration_duration and len(self._calibration_rates) > 10:
            rates = sorted(self._calibration_rates)
            median_rate = rates[len(rates) // 2]

            self.state.calibrated_rate_bpm = median_rate
            self.state.is_calibrated = True
            self._calibrated = True

            self.state.start_rate_bpm = median_rate

            self._current_pace_bpm = median_rate
            self.state.current_pace_bpm = median_rate
        else:
            self._current_pace_bpm = max(6.0, self.state.breath_rate_bpm)
            self.state.current_pace_bpm = self._current_pace_bpm

    # ------------------------------------------------------------------ #
    #  Pacing waveform generation (asymmetric for exhale emphasis)
    # ------------------------------------------------------------------ #

    def _generate_pace(self, dt: float, t: float):
        """Generate target phase with optional asymmetric exhale/inhale."""
        period = 60.0 / self._current_pace_bpm

        ei = self.state.target_ei_ratio

        inhale_dur = period / (1.0 + ei)
        exhale_dur = period - inhale_dur

        self._pace_phase_acc += dt

        cycle_pos = self._pace_phase_acc % period

        if cycle_pos < inhale_dur:
            frac = cycle_pos / inhale_dur
            target = 0.5 * (1.0 - math.cos(math.pi * frac))
        else:
            frac = (cycle_pos - inhale_dur) / exhale_dur
            target = 0.5 * (1.0 + math.cos(math.pi * frac))

        self.state.target_phase = target
        self.state.target_rate_bpm = self._resonance_bpm
        self.state.current_pace_bpm = self._current_pace_bpm

    # ------------------------------------------------------------------ #
    #  Nudging toward resonance
    # ------------------------------------------------------------------ #

    def _nudge(self, dt: float):
        """Gently shift pacing rate toward resonance target."""
        delta = self._resonance_bpm - self._current_pace_bpm
        if abs(delta) < 0.05:
            return

        coh_factor = max(0.0, (self._coherence_ema - 20) / 80.0)
        max_step = self._max_nudge_bpm_per_sec * dt * (0.3 + 0.7 * coh_factor)

        step = max(-max_step, min(max_step, delta))
        self._current_pace_bpm += step

        self._current_pace_bpm = max(4.0, min(20.0, self._current_pace_bpm))

    # ------------------------------------------------------------------ #
    #  Exhale / Inhale ratio tracking
    # ------------------------------------------------------------------ #

    def _track_ei_ratio(self, t: float):
        """Track the user's exhale vs inhale duration."""
        phase = self.state.breath_phase
        rising = phase > self._last_phase

        if rising and not self._phase_rising:
            self._last_exhale_dur = max(0.5, t - self._exhale_start_t)
            self._inhale_start_t = t
        elif not rising and self._phase_rising:
            self._last_inhale_dur = max(0.5, t - self._inhale_start_t)
            self._exhale_start_t = t

        self._phase_rising = rising
        self._last_phase = phase

        if self._last_inhale_dur > 0.3:
            raw_ratio = self._last_exhale_dur / self._last_inhale_dur
            raw_ratio = max(0.5, min(3.0, raw_ratio))
            self._ei_ratio_ema += 0.02 * (raw_ratio - self._ei_ratio_ema)
            self.state.exhale_inhale_ratio = self._ei_ratio_ema

    # ------------------------------------------------------------------ #
    #  Configuration
    # ------------------------------------------------------------------ #

    def set_resonance_bpm(self, bpm: float):
        self._resonance_bpm = max(4.0, min(10.0, bpm))
        self.state.resonance_bpm = self._resonance_bpm

    def set_target_ei_ratio(self, ratio: float):
        self.state.target_ei_ratio = max(1.0, min(2.5, ratio))
