import time
from collections import deque
import numpy as np

class PolarDataBus:
    """Thread-safe data bus for sharing raw Polar metrics and derived biofeedback states."""
    def __init__(self, buffer_seconds=30):
        # Hardware native frequencies
        self.ECG_HZ = 130
        self.ACC_HZ = 100
        
        self.ecg_buffer = deque(maxlen=self.ECG_HZ * buffer_seconds)
        self.acc_x_buffer = deque(maxlen=self.ACC_HZ * buffer_seconds)
        self.acc_y_buffer = deque(maxlen=self.ACC_HZ * buffer_seconds)
        self.acc_z_buffer = deque(maxlen=self.ACC_HZ * buffer_seconds)
        
        # We track HR values separately (~1 Hz update rate)
        self.hr_buffer = deque(maxlen=int(buffer_seconds * 1.5))
        
        # Real-time Hardware Extracted State (The Patient)
        self.current_breath_phase = 0.5  # 0.0 -> Exhale, 1.0 -> Peak Inhale
        self.current_breath_rate_bpm = 15.0
        self.coherence_score = 0.5  # 0.0 to 1.0 (smooth vs chaotic breathing)
        self.asi_score = 0.5

        # Real-time Algorithm State (The Guide)
        self.target_breath_phase = 0.5
        self.target_bpm = 15.0
        self.estimated_resonant_bpm = 6.0 
        self.sync_score = 0.0  # 1.0 if patient perfectly matches the guide

    def add_ecg(self, samples):
        self.ecg_buffer.extend(samples)

    def add_acc(self, samples):
        for s in samples:
            self.acc_x_buffer.append(float(s[0]))
            self.acc_y_buffer.append(float(s[1]))
            self.acc_z_buffer.append(float(s[2]))
            
    def add_hr(self, hr_bpm):
        self.hr_buffer.append(float(hr_bpm))

    def get_recent_acc(self, seconds=5.0):
        n_samples = int(self.ACC_HZ * seconds)
        if len(self.acc_x_buffer) < n_samples:
            return None
        x = np.array(list(self.acc_x_buffer)[-n_samples:])
        y = np.array(list(self.acc_y_buffer)[-n_samples:])
        z = np.array(list(self.acc_z_buffer)[-n_samples:])
        return np.column_stack([x, y, z])
    
    def get_recent_ecg(self, seconds=5.0):
        n_samples = int(self.ECG_HZ * seconds)
        if len(self.ecg_buffer) < n_samples:
            return None
        return np.array(list(self.ecg_buffer)[-n_samples:])

    def get_recent_hr(self):
        if len(self.hr_buffer) == 0:
            return None
        return np.array(list(self.hr_buffer))
