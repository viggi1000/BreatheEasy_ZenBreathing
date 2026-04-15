"""
Demo simulator -- generates realistic breathing data without hardware.

The simulated user starts at ~14-16 BPM with natural variability,
gradually begins following the pacer, and over 3-5 minutes reaches
coherent resonant breathing near 6 BPM.

ACC data has realistic 3-axis breathing distribution with gravity
baseline, matching real Polar H10 chest-strap orientation.
"""

import math
import random

from zen_breathing.state import BreathingState
from zen_breathing.biofeedback import BiofeedbackEngine


class DemoSimulator:
    """
    Simulates a user's breathing journey from irregular to resonant.

    With ``fast=True`` the whole arc happens in ~90s instead of ~5 min.
    """

    def __init__(self, state: BreathingState, fast: bool = False):
        self.state = state
        self.speed = 3.0 if fast else 1.0
        self._sim_time = 0.0
        self._running = True

        # The biofeedback engine drives pacing + coherence
        self.feedback = BiofeedbackEngine(state, target_rate_bpm=6.0)

        # Internal "user" breathing model
        self._user_rate = 14.0 + random.uniform(-1.0, 2.0)
        self._user_phase_acc = 0.0
        self._breath_noise_phase = random.uniform(0, 10)

        # Compliance (how well simulated user follows pacer)
        self._compliance = 0.0

        # Simulated heart rate
        self._hr_base = 74.0

        # Mock ECG phase for R-peak simulation
        self._ecg_phase = 0.0

    def update(self):
        if not self._running:
            return

        dt_real = 1.0 / 60.0
        self._sim_time += dt_real * self.speed
        t = self._sim_time

        # ============================================================
        #  Compliance ramp
        # ============================================================
        if t < 15:
            self._compliance = 0.0
        elif t < 30:
            self._compliance = 0.15 * ((t - 15) / 15.0)
        elif t < 90:
            frac = (t - 30) / 60.0
            self._compliance = 0.15 + 0.55 * (frac ** 0.7)
        elif t < 150:
            frac = (t - 90) / 60.0
            self._compliance = 0.70 + 0.18 * frac
        else:
            self._compliance = 0.88 + 0.08 * math.sin(t * 0.02)

        self._compliance = max(0.0, min(0.96, self._compliance))

        # ============================================================
        #  Breathing rate
        # ============================================================
        if t < 15:
            natural_rate = self._user_rate
        elif t < 120:
            frac = (t - 15) / 105.0
            natural_rate = self._user_rate - 2.0 * frac
        else:
            natural_rate = self._user_rate - 2.0

        pacer_rate = self.state.current_pace_bpm
        effective_rate = (1.0 - self._compliance) * natural_rate + \
                         self._compliance * pacer_rate

        # Breath-to-breath variability
        noise_amp = (1.0 - self._compliance * 0.8) * 1.2
        self._breath_noise_phase += dt_real * self.speed * 0.7
        rate_noise = noise_amp * (
            math.sin(self._breath_noise_phase * 2.3) * 0.6 +
            math.sin(self._breath_noise_phase * 5.7) * 0.3 +
            math.sin(self._breath_noise_phase * 0.4) * 0.4
        )
        effective_rate = max(4.0, effective_rate + rate_noise)

        # ============================================================
        #  Breath phase (asymmetric waveform)
        # ============================================================
        period = 60.0 / effective_rate
        self._user_phase_acc += dt_real * self.speed / period

        cycle_pos = self._user_phase_acc % 1.0
        ei_ratio = 1.0 + 0.3 * self._compliance

        inhale_frac = 1.0 / (1.0 + ei_ratio)
        if cycle_pos < inhale_frac:
            phase = 0.5 * (1.0 - math.cos(math.pi * cycle_pos / inhale_frac))
        else:
            exhale_frac = 1.0 - inhale_frac
            phase = 0.5 * (1.0 + math.cos(math.pi * (cycle_pos - inhale_frac) / exhale_frac))

        # Jitter + phase-lock to target
        jitter = (1.0 - self._compliance) * 0.06 * math.sin(t * 3.7)
        phase = max(0.0, min(1.0, phase + jitter))

        target = self.state.target_phase
        lock_strength = max(0.0, self._compliance - 0.2) * 0.7
        phase = (1.0 - lock_strength) * phase + lock_strength * target

        # ============================================================
        #  Write to state
        # ============================================================
        self.state.breath_phase = phase
        self.state.breath_rate_bpm = effective_rate
        self.state.is_inhaling = cycle_pos < inhale_frac

        # ============================================================
        #  Simulated Heart Rate with RSA
        # ============================================================
        rsa_amplitude = 2.0 + self._compliance * 6.0
        hr = self._hr_base + rsa_amplitude * math.sin(2 * math.pi * cycle_pos)
        hr += 2.0 * math.sin(t * 0.015)
        hr -= min(4.0, t * 0.015)
        self.state.heart_rate = max(55, min(90, hr))

        # ============================================================
        #  Simulated ACC data (3-axis with gravity baseline)
        #  Matches real Polar H10 chest-strap orientation:
        #    Z ~ 1000 mg (gravity) + breathing modulation (dominant)
        #    X ~ small lateral movement + breathing
        #    Y ~ small anterior/posterior
        #  The gravity subtraction in respiration.py will remove the
        #  ~1000 mg baseline, leaving the breathing residual.
        # ============================================================
        breath_signal = math.sin(2 * math.pi * cycle_pos)

        # Z-axis: gravity baseline + breathing expansion (dominant)
        acc_z = 1000.0 + 25.0 * breath_signal + random.gauss(0, 2.0)
        # X-axis: breathing component (cross-axis) + noise
        acc_x = 8.0 * breath_signal + random.gauss(0, 4.0)
        # Y-axis: smaller breathing component + noise
        acc_y = 5.0 * math.cos(2 * math.pi * cycle_pos * 1.02) + random.gauss(0, 3.0)

        self.state.acc_x_history.append(acc_x)
        self.state.acc_y_history.append(acc_y)
        self.state.acc_z_history.append(acc_z)
        self.state.resp_signal_history.append(phase * 100)
        self.state.acc_resp_signal = phase
        self.state.acc_resp_history.append(phase)

        # Raw continuous breathing signal for spectral coherence
        # (not 0-1 phase — phase wraps create artificial harmonics)
        self.state.raw_resp_signal = breath_signal

        # Simulate ECG trace (simple sine at HR frequency) for debug plot
        ecg_val = math.sin(2 * math.pi * self._ecg_phase) * 1000
        self.state.ecg_raw_history.append(ecg_val)

        # ============================================================
        #  Run biofeedback engine
        # ============================================================
        self.feedback.update(dt_real * self.speed)

    def stop(self):
        self._running = False
