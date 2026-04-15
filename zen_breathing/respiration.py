"""
Respiration extraction from Polar H10 signals.

Two independent methods -- both exposed separately:

  ACC-derived (chest wall motion):
    Gravity subtraction via causal 0.04 Hz LPF on all 3 axes, then
    the breathing signal is the NORM of the gravity-subtracted residual.
    This is posture-invariant (works standing, sitting, lying down).
    Breathing-band bandpass (0.07–0.7 Hz) applied incrementally with
    maintained filter state so there is NO phase discontinuity.

  ECG-derived (EDR -- QRS slope pair + R-peak amplitude envelope):
    The amplitude AND QRS morphology (slope pair) of each R-peak is
    modulated by chest expansion.  Features fused: 0.4*amp + 0.3*QR + 0.3*RS.
    Interpolated to 4 Hz then bandpassed 0.05-0.8 Hz.

The primary output (breath_phase, breath_rate_bpm) fuses the two
using SQI-weighted fusion:
  - ACC SQI: breathing-band power / total power
  - ECG SQI: RR-interval coefficient of variation
"""

import math
import numpy as np
from collections import deque
from scipy.signal import butter, lfilter, lfilter_zi, filtfilt, find_peaks
from scipy.interpolate import interp1d


class RespirationExtractor:
    """
    Real-time respiration from Polar H10 ACC and/or ECG.

    Usage:
        ext = RespirationExtractor()
        ext.add_acc_samples(acc_xyz_array)   # shape (N, 3) in mg
        ext.add_ecg_samples(list_of_uV)
        phase, rate, depth = ext.get_breath_state()

    Public attributes (separate signals):
        acc_signal, acc_phase, acc_rate_bpm
        ecg_signal, ecg_phase, ecg_rate_bpm, ecg_available
        is_inhaling, breath_depth
    """

    def __init__(self, fs_acc: float = 100, fs_ecg: float = 130,
                 buffer_seconds: int = 30):
        self.fs_acc = fs_acc
        self.fs_ecg = fs_ecg

        # ------ ACC raw buffers ------
        _n_acc = int(fs_acc * buffer_seconds)
        self._acc_z = deque(maxlen=_n_acc)
        self._acc_x = deque(maxlen=_n_acc)
        self._acc_y = deque(maxlen=_n_acc)

        # ---- Gravity extraction LPF (0.04 Hz, causal, per-axis) ----
        # Very slow cutoff → tracks only gravity/posture, not breathing
        b_grav, a_grav = butter(2, 0.04 / (fs_acc / 2), btype='low')
        self._grav_b, self._grav_a = b_grav, a_grav
        self._grav_zi_x = lfilter_zi(b_grav, a_grav) * 0.0
        self._grav_zi_y = lfilter_zi(b_grav, a_grav) * 0.0
        self._grav_zi_z = lfilter_zi(b_grav, a_grav) * 0.0
        self._grav_zi_ready = False

        # ---- Breathing bandpass (0.07-0.7 Hz) on the norm residual ----
        b, a = butter(2, [0.07 / (fs_acc / 2), 0.7 / (fs_acc / 2)], btype="band")
        self._acc_b, self._acc_a = b, a
        self._acc_zi = lfilter_zi(b, a) * 0.0
        self._acc_zi_ready = False

        # Causal-filtered respiration buffer (same rate as ACC, 100 Hz)
        self._acc_resp = deque(maxlen=_n_acc)

        # ------ ECG raw buffer ------
        _n_ecg = int(fs_ecg * buffer_seconds)
        self._ecg_buf = deque(maxlen=_n_ecg)
        self._ecg_total = 0            # total samples ever added
        self._ecg_last_edr = 0         # total at last EDR run
        self._edr_buf = deque(maxlen=500)   # EDR envelope values (4 Hz)

        # ------ Public outputs ------
        # ACC-derived
        self.acc_signal   = 0.0
        self.acc_phase    = 0.5
        self.acc_rate_bpm = 14.0

        # ECG-derived
        self.ecg_signal   = 0.0
        self.ecg_phase    = 0.5
        self.ecg_rate_bpm = 14.0
        self.ecg_available = False

        # SQI values (for debug / state)
        self.acc_sqi = 0.5
        self.ecg_sqi = 0.0

        # Primary fused
        self.breath_phase    = 0.5
        self.breath_rate_bpm = 14.0
        self.breath_depth    = 0.0
        self.is_inhaling     = False
        self.resp_signal     = 0.0

        # Peak-holder normalization (avoids jump when window extremes roll out)
        self._acc_pk_max =  1.0
        self._acc_pk_min = -1.0
        self._pk_decay   = 0.9995   # per-sample decay (faster adaptation)

        # Thresholds
        self._min_resp_samples = int(fs_acc * 4)   # 4 s to start phase
        self._min_rate_samples = int(fs_acc * 10)  # 10 s for reliable rate

        # ---- Timing-based phase (lag-free) ----
        self._total_acc_samples  = 0
        self._last_is_inh_timing = False
        self._inhale_start_s     = 0
        self._exhale_start_s     = 0
        self._half_inhale_s      = int(fs_acc * 2.5)
        self._half_exhale_s      = int(fs_acc * 2.5)
        self._timing_ready       = False

    # ================================================================
    #  Writers
    # ================================================================

    def add_acc_samples(self, acc_xyz):
        """
        acc_xyz: array-like (N, 3) in mg, or list of (x, y, z) tuples.
        Uses gravity subtraction + norm (posture-invariant).
        """
        arr = np.asarray(acc_xyz, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.shape[0] == 0:
            return

        self._total_acc_samples += arr.shape[0]

        # ---- Gravity subtraction (causal LPF on each axis) ----
        if not self._grav_zi_ready:
            self._grav_zi_x = lfilter_zi(self._grav_b, self._grav_a) * arr[0, 0]
            self._grav_zi_y = lfilter_zi(self._grav_b, self._grav_a) * arr[0, 1]
            self._grav_zi_z = lfilter_zi(self._grav_b, self._grav_a) * arr[0, 2]
            self._grav_zi_ready = True

        grav_x, self._grav_zi_x = lfilter(self._grav_b, self._grav_a,
                                           arr[:, 0], zi=self._grav_zi_x)
        grav_y, self._grav_zi_y = lfilter(self._grav_b, self._grav_a,
                                           arr[:, 1], zi=self._grav_zi_y)
        grav_z, self._grav_zi_z = lfilter(self._grav_b, self._grav_a,
                                           arr[:, 2], zi=self._grav_zi_z)

        # Subtract gravity → breathing-only residual
        res_x = arr[:, 0] - grav_x
        res_y = arr[:, 1] - grav_y
        res_z = arr[:, 2] - grav_z

        # Norm of residual (posture-invariant breathing signal)
        breathing_raw = np.sqrt(res_x**2 + res_y**2 + res_z**2)

        # ---- Bandpass in breathing range (0.07-0.7 Hz) ----
        if not self._acc_zi_ready:
            self._acc_zi = lfilter_zi(self._acc_b, self._acc_a) * breathing_raw[0]
            self._acc_zi_ready = True

        filtered, self._acc_zi = lfilter(self._acc_b, self._acc_a, breathing_raw,
                                         zi=self._acc_zi)
        self._acc_resp.extend(filtered.tolist())

        # Store raw axes for debug plots
        for row in arr:
            self._acc_x.append(row[0])
            self._acc_y.append(row[1])
            self._acc_z.append(row[2])

        self._update_acc()

    def add_ecg_samples(self, samples):
        """samples: list of uV values at fs_ecg Hz."""
        self._ecg_buf.extend(samples)
        self._ecg_total += len(samples)
        # Run EDR once per second of new data
        if self._ecg_total - self._ecg_last_edr >= int(self.fs_ecg):
            self._ecg_last_edr = self._ecg_total
            self._update_edr()

    # Backward-compat alias
    def add_samples(self, acc_xyz):
        self.add_acc_samples(acc_xyz)

    # ================================================================
    #  Primary output
    # ================================================================

    def get_breath_state(self):
        """Return (phase, rate_bpm, depth) primary fused state."""
        return self.breath_phase, self.breath_rate_bpm, self.breath_depth

    # ================================================================
    #  ACC update (causal, called on every new chunk)
    # ================================================================

    def _update_acc(self):
        n = len(self._acc_resp)
        if n < self._min_resp_samples:
            return

        resp = np.array(self._acc_resp)
        val = float(resp[-1])
        self.acc_signal  = val
        self.resp_signal = val

        # ---- Peak-holder normalization ----
        if val > self._acc_pk_max:
            self._acc_pk_max = val
        else:
            self._acc_pk_max = max(self._acc_pk_max * self._pk_decay, val, 0.1)

        if val < self._acc_pk_min:
            self._acc_pk_min = val
        else:
            self._acc_pk_min = min(self._acc_pk_min * self._pk_decay, val, -0.1)

        pk_rng = self._acc_pk_max - self._acc_pk_min

        if pk_rng > 0.2:
            self.breath_depth = float(pk_rng)

        # ---- Slope-based direction (fast, ~30ms latency) ----
        if n >= 5:
            self.is_inhaling = float(resp[-1]) > float(resp[-5])
        elif n >= 3:
            self.is_inhaling = float(resp[-1]) > float(resp[-3])

        # ---- Timing-based phase (lag-free) ----
        t_now = self._total_acc_samples

        if self.is_inhaling != self._last_is_inh_timing and n > int(self.fs_acc * 0.3):
            if self.is_inhaling:
                if self._exhale_start_s > 0:
                    dur = t_now - self._exhale_start_s
                    if int(self.fs_acc * 0.8) < dur < int(self.fs_acc * 20):
                        self._half_exhale_s = int(0.65 * self._half_exhale_s + 0.35 * dur)
                self._inhale_start_s = t_now
            else:
                if self._inhale_start_s > 0:
                    dur = t_now - self._inhale_start_s
                    if int(self.fs_acc * 0.8) < dur < int(self.fs_acc * 20):
                        self._half_inhale_s = int(0.65 * self._half_inhale_s + 0.35 * dur)
                self._exhale_start_s = t_now
            self._last_is_inh_timing = self.is_inhaling
            self._timing_ready = True

        if self._timing_ready:
            if self.is_inhaling and self._inhale_start_s > 0:
                elapsed = t_now - self._inhale_start_s
                frac = min(1.0, elapsed / max(self._half_inhale_s, 10))
                self.acc_phase = 0.5 * (1.0 - math.cos(math.pi * frac))
            elif not self.is_inhaling and self._exhale_start_s > 0:
                elapsed = t_now - self._exhale_start_s
                frac = min(1.0, elapsed / max(self._half_exhale_s, 10))
                self.acc_phase = 0.5 * (1.0 + math.cos(math.pi * frac))
        elif pk_rng > 0.2:
            self.acc_phase = float(np.clip(
                (val - self._acc_pk_min) / pk_rng, 0.0, 1.0))

        # ---- Rate from peaks ----
        if n >= self._min_rate_samples:
            try:
                min_dist = int(self.fs_acc * 2.0)
                analysis = resp[-min(n, int(self.fs_acc * 30)):]
                peaks, _ = find_peaks(analysis, distance=min_dist,
                                      prominence=max(pk_rng * 0.20, 0.1))
                if len(peaks) >= 2:
                    intervals = np.diff(peaks) / self.fs_acc
                    med = float(np.median(intervals[-5:] if len(intervals) >= 5
                                         else intervals))
                    rate = 60.0 / med
                    if 2.5 <= rate <= 30.0:
                        self.acc_rate_bpm = rate
            except Exception:
                pass

        # ---- ACC SQI: breathing-band power / total power ----
        if n >= int(self.fs_acc * 5):
            try:
                chunk = resp[-int(self.fs_acc * 5):]
                freqs = np.fft.rfftfreq(len(chunk), 1.0 / self.fs_acc)
                psd = np.abs(np.fft.rfft(chunk))**2
                breath_band = (freqs >= 0.05) & (freqs <= 0.6)
                total_band = (freqs >= 0.05) & (freqs <= 5.0)
                denom = np.sum(psd[total_band])
                if denom > 1e-8:
                    self.acc_sqi = float(np.clip(
                        np.sum(psd[breath_band]) / denom, 0, 1))
            except Exception:
                pass

        # ---- SQI-weighted fusion into primary ----
        self.breath_phase    = self.acc_phase
        self.breath_rate_bpm = self.acc_rate_bpm

        if self.ecg_available:
            w_ecg = self.ecg_sqi / (self.ecg_sqi + self.acc_sqi + 1e-8)
            w_acc = self.acc_sqi / (self.ecg_sqi + self.acc_sqi + 1e-8)
            self.breath_phase = w_acc * self.acc_phase + w_ecg * self.ecg_phase

    # ================================================================
    #  ECG-derived respiration  (runs once per second, batch mode)
    #  Uses QRS Slope Pair + R-peak amplitude fusion
    # ================================================================

    def _update_edr(self):
        n = len(self._ecg_buf)
        min_ecg = int(self.fs_ecg * 6)
        if n < min_ecg:
            return

        ecg = np.array(self._ecg_buf, dtype=np.float64)

        # 1. Bandpass QRS complex (5–30 Hz) to find R-peaks
        try:
            b_qrs, a_qrs = butter(2,
                                  [5.0 / (self.fs_ecg / 2),
                                   30.0 / (self.fs_ecg / 2)],
                                  btype="band")
            ecg_filt = filtfilt(b_qrs, a_qrs, ecg)
        except ValueError:
            return

        # 2. R-peak detection
        thr = np.std(ecg_filt) * 1.5
        try:
            peaks, _ = find_peaks(ecg_filt, height=thr,
                                  distance=int(self.fs_ecg * 0.4))
        except Exception:
            return

        if len(peaks) < 6:
            return

        # 3. QRS slope pair + amplitude feature extraction
        features = self._extract_qrs_features(ecg, peaks)
        peak_times = peaks / self.fs_ecg

        if peak_times[-1] - peak_times[0] < 5.0:
            return

        # 4. Interpolate to 4 Hz for smooth envelope
        t_reg = np.arange(peak_times[0], peak_times[-1], 0.25)
        if len(t_reg) < 8:
            return
        try:
            interp = interp1d(peak_times, features,
                              kind="cubic", fill_value="extrapolate")
            amp_reg = interp(t_reg)
        except Exception:
            return

        # 5. Bandpass envelope in breathing band (0.05–0.8 Hz)
        fs_edr = 4.0
        try:
            b_edr, a_edr = butter(2,
                                  [0.05 / (fs_edr / 2), 0.8 / (fs_edr / 2)],
                                  btype="band")
            edr = filtfilt(b_edr, a_edr, amp_reg)
        except ValueError:
            return

        if len(edr) < 4:
            return

        # 6. Phase from recent 5 s window
        win = min(len(edr), int(fs_edr * 5))
        recent = edr[-win:]
        mn, mx = np.min(recent), np.max(recent)
        rng = mx - mn

        self.ecg_signal = float(edr[-1])
        self._edr_buf.append(float(edr[-1]))

        if rng > 1e-4:
            self.ecg_phase = float(np.clip((edr[-1] - mn) / rng, 0.0, 1.0))

        # 7. Rate from EDR peaks
        try:
            edr_peaks, _ = find_peaks(edr,
                                      distance=int(fs_edr * 2.0),
                                      prominence=rng * 0.2)
            if len(edr_peaks) >= 2:
                intervals = np.diff(edr_peaks) / fs_edr
                med = float(np.median(intervals))
                rate = 60.0 / med
                if 2.5 <= rate <= 30.0:
                    self.ecg_rate_bpm = rate
                    self.ecg_available = True
        except Exception:
            pass

        # 8. ECG SQI: RR-interval coefficient of variation
        try:
            rr = np.diff(peaks) / self.fs_ecg
            if len(rr) >= 3:
                cv = float(np.std(rr) / (np.mean(rr) + 1e-8))
                self.ecg_sqi = float(np.clip(1.0 - cv * 3.0, 0, 1))
        except Exception:
            pass

    def _extract_qrs_features(self, ecg, peaks):
        """Extract QRS slope pair + amplitude → fused EDR features."""
        features = []
        for pk in peaks:
            win_start = max(0, pk - int(0.06 * self.fs_ecg))
            win_end = min(len(ecg), pk + int(0.06 * self.fs_ecg))
            segment = ecg[win_start:win_end]

            # R-peak amplitude
            r_amp = ecg[pk]

            # Q→R slope (rising)
            r_idx_local = pk - win_start
            q_segment = segment[:r_idx_local]
            if len(q_segment) > 2:
                q_val = np.min(q_segment)
                q_idx = np.argmin(q_segment)
                slope_qr = (ecg[pk] - q_val) / max(1, r_idx_local - q_idx) * self.fs_ecg
            else:
                slope_qr = 0.0

            # R→S slope (falling)
            s_segment = segment[r_idx_local:]
            if len(s_segment) > 2:
                s_val = np.min(s_segment)
                s_idx = np.argmin(s_segment)
                slope_rs = (s_val - ecg[pk]) / max(1, s_idx) * self.fs_ecg
            else:
                slope_rs = 0.0

            # Fuse: 0.4 * amplitude + 0.3 * QR_slope + 0.3 * RS_slope
            features.append(0.4 * r_amp + 0.3 * slope_qr + 0.3 * abs(slope_rs))

        return np.array(features)
