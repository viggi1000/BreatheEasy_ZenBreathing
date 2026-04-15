"""
Mock Polar H10 sensor for UI development and testing without hardware.
Modulates HR based on realistic breathing phase (RSA) for testing the biofeedback algorithm.
Gradually reduces breathing rate to simulate a user following a rhythmic pacing guide.
"""

import time
import math
import random
import numpy as np

class MockPolarH10:
    def __init__(self):
        self._t0 = time.time()
        self._ecg_phase = 0.0
        self._acc_phase = 0.0
        
        # Patient simulation dynamics
        self._start_bpm = 15.0  # Anxious, fast breathing
        self._target_bpm = 5.5  # Deep coherent breathing
        self._reduction_time_s = 180.0 # 3 minutes to settle 
        
        self._hr_base = 75.0
        self._hr_bpm = 75.0
        
    def _get_current_breath_bpm(self) -> float:
        """Simulate the user gradually relaxing as time goes on."""
        t_elapsed = time.time() - self._t0
        progress = min(1.0, t_elapsed / self._reduction_time_s)
        
        # Non-linear relaxation curve
        bpm = self._start_bpm - (self._start_bpm - self._target_bpm) * (1.0 - math.exp(-3.0 * progress))
        
        # Add a tiny bit of erratic human noise
        noise = math.sin(t_elapsed * 0.1) * 0.5 + random.gauss(0, 0.2)
        return max(4.0, bpm + noise)

    def _get_current_breath_phase(self) -> float:
        """Returns 0.0 to 1.0 representing exhale-inhale cycle using current BPM."""
        t = time.time() - self._t0
        bpm = self._get_current_breath_bpm()
        period = 60.0 / bpm
        return (t % period) / period

    def _generate_ecg_beat(self, t: float, hr: float) -> float:
        period = 60.0 / hr
        phase = (t % period) / period
        value = 0.0
        
        if abs(phase - 0.23) < 0.024:
            value += 850.0 * math.exp(-((phase - 0.23) ** 2) / (2 * 0.008 ** 2))
            
        noise = random.gauss(0, 8)
        b_phase = self._get_current_breath_phase()
        baseline_wander = 150.0 * math.sin(2 * math.pi * b_phase)

        return value + noise + baseline_wander - 350.0

    def get_ecg_frame(self, n_samples: int = 73) -> tuple:
        dt = 1.0 / 130.0
        samples = []
        for _ in range(n_samples):
            # Smoothly lower base HR as they relax
            bpm = self._get_current_breath_bpm()
            base_hrv = 60.0 + ((bpm - 5.5) / 10.0) * 20.0
            self._hr_base += (base_hrv - self._hr_base) * 0.01

            breath_phase = self._get_current_breath_phase()
            
            # Respiratory Sinus Arrhythmia gets MUCH stronger at lower BPMs (Resonance effect!)
            rsa_strength = 5.0 + (15.0 - bpm) * 1.5 
            hr_shift = rsa_strength * math.sin(2 * math.pi * breath_phase) 
            
            self._hr_bpm = self._hr_base + hr_shift
            
            sample = self._generate_ecg_beat(self._ecg_phase, self._hr_bpm)
            samples.append(int(sample))
            self._ecg_phase += dt

        return ('ECG', time.time_ns(), samples)

    def get_acc_frame(self, n_samples: int = 16) -> tuple:
        dt = 1.0 / 100.0
        samples = []
        for _ in range(n_samples):
            breath_phase = self._get_current_breath_phase()
            # Deeper breaths (higher amplitude) as rate decreases
            bpm = self._get_current_breath_bpm()
            amp = 30 + (15.0 - bpm) * 3.0 
            
            breath_x = amp * math.sin(2 * math.pi * breath_phase)
            
            # Match the screenshot behavior exactly: X oscillates, Y rests at +0.2g, Z rests at -1g
            x = int(0 + breath_x + random.gauss(0, 3))
            y = int(200 + random.gauss(0, 3)) 
            z = int(-1000 + random.gauss(0, 3)) 
            
            samples.append((x, y, z))
            self._acc_phase += dt

        return ('ACC', time.time_ns(), samples)

    def get_hr_frame(self) -> tuple:
        hr = self._hr_bpm + random.gauss(0, 1.0)
        rr = 60000.0 / hr 
        return ('HR', time.time_ns(), (round(hr), round(rr)), None)
