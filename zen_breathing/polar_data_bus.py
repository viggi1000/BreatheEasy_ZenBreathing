"""
Thread-safe data bus for Polar H10 sensor data.

The BLE worker (running in a QThread) writes raw data via add_* methods.
The UI-thread LiveProcessor reads via get_new_* methods.

No Qt signals for data -- direct deque access guarded by Python's GIL.
This avoids the classic "Cannot connect to nullptr" error that occurs when
a plain-Python class (non-QObject) tries to receive cross-thread Qt signals.
"""

import time
from collections import deque
import numpy as np


class PolarDataBus:
    """
    Thread-safe data sharing between BLE worker thread and UI thread.

    Uses Python GIL-protected deque writes.
    Tracks total sample counts so consumers can request only NEW samples.
    """

    ECG_HZ = 130
    ACC_HZ = 100
    BUFFER_SECONDS = 30

    def __init__(self):
        buf_acc = self.ACC_HZ * self.BUFFER_SECONDS
        buf_ecg = self.ECG_HZ * self.BUFFER_SECONDS

        self.acc_x_buffer = deque(maxlen=buf_acc)
        self.acc_y_buffer = deque(maxlen=buf_acc)
        self.acc_z_buffer = deque(maxlen=buf_acc)
        self.ecg_buffer   = deque(maxlen=buf_ecg)
        self.hr_buffer     = deque(maxlen=300)

        # Monotonically increasing counters (GIL makes int writes atomic)
        self._acc_total = 0
        self._ecg_total = 0
        self._hr_total  = 0

        # Timestamps for stream health monitoring
        self.last_hr_time  = 0.0
        self.last_acc_time = 0.0
        self.last_ecg_time = 0.0

    # ------------------------------------------------------------------
    #  Writers  (BLE worker thread)
    # ------------------------------------------------------------------

    def add_acc(self, samples):
        """samples: list/array of (x, y, z) tuples in mg."""
        for s in samples:
            self.acc_x_buffer.append(float(s[0]))
            self.acc_y_buffer.append(float(s[1]))
            self.acc_z_buffer.append(float(s[2]))
        self._acc_total += len(samples)
        self.last_acc_time = time.time()

    def add_ecg(self, samples):
        """samples: list of uV values at 130 Hz."""
        self.ecg_buffer.extend(samples)
        self._ecg_total += len(samples)
        self.last_ecg_time = time.time()

    def add_hr(self, hr_bpm: float):
        self.hr_buffer.append(float(hr_bpm))
        self._hr_total += 1
        self.last_hr_time = time.time()

    # ------------------------------------------------------------------
    #  Readers  (UI thread)
    # ------------------------------------------------------------------

    def get_new_acc(self, since_count: int):
        """
        Return (array shape (N,3) or None, new_total).
        Pass the previously returned new_total as since_count each call.
        """
        new_total = self._acc_total
        n_new = new_total - since_count
        if n_new <= 0:
            return None, new_total
        n_avail = len(self.acc_z_buffer)
        n_take  = min(n_new, n_avail)
        if n_take == 0:
            return None, new_total
        x = list(self.acc_x_buffer)[-n_take:]
        y = list(self.acc_y_buffer)[-n_take:]
        z = list(self.acc_z_buffer)[-n_take:]
        return np.column_stack([x, y, z]).astype(np.float64), new_total

    def get_new_ecg(self, since_count: int):
        """
        Return (array or None, new_total).
        """
        new_total = self._ecg_total
        n_new = new_total - since_count
        if n_new <= 0:
            return None, new_total
        n_avail = len(self.ecg_buffer)
        n_take  = min(n_new, n_avail)
        if n_take == 0:
            return None, new_total
        return np.array(list(self.ecg_buffer)[-n_take:], dtype=np.float64), new_total

    def get_latest_hr(self):
        """Most recent HR value, or None."""
        return float(self.hr_buffer[-1]) if self.hr_buffer else None

    # ------------------------------------------------------------------
    #  Stream health
    # ------------------------------------------------------------------

    @property
    def hr_active(self) -> bool:
        return self._hr_total > 0 and (time.time() - self.last_hr_time) < 3.0

    @property
    def acc_active(self) -> bool:
        return self._acc_total > 0 and (time.time() - self.last_acc_time) < 1.0

    @property
    def ecg_active(self) -> bool:
        return self._ecg_total > 0 and (time.time() - self.last_ecg_time) < 1.0

    @property
    def all_streams_active(self) -> bool:
        return self.hr_active and self.acc_active and self.ecg_active
