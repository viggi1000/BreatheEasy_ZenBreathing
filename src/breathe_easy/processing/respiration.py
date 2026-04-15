import numpy as np
import scipy.signal

class RespirationProcessor:
    def __init__(self, fps=100):
        self.fs = fps
        # 0.05 Hz to 0.8 Hz bounds for normal slow breathing (3 BPM to 48 BPM)
        self.b, self.a = scipy.signal.butter(2, [0.05/(self.fs/2), 0.8/(self.fs/2)], btype='band')
        
    def extract_phase_and_rate(self, acc_xyz: np.ndarray):
        """
        acc_xyz: np.ndarray of shape (N, 3) where N >= fs * 5 (5 seconds)
        Returns: (phase 0.0-1.0, breathing_rate_bpm)
        """
        if acc_xyz is None or len(acc_xyz) < self.fs * 5:
            return 0.5, 6.0
            
        # 1. PCA or Axis selection. Automatically grab the axis with the highest variance (chest excursion).
        variances = np.var(acc_xyz, axis=0)
        best_axis = np.argmax(variances)
        signal = acc_xyz[:, best_axis]
        
        # 2. Bandpass filter
        filtered = scipy.signal.filtfilt(self.b, self.a, signal)
        
        # 3. Rolling average (1s window) to smooth
        window = int(self.fs)
        trend = np.convolve(filtered, np.ones(window)/window, mode='same')
        
        # 4. Derivative -> determines inhale (+) vs exhale (-)
        slope = np.gradient(trend)
        
        # 5. Phase mapping
        # Peak of trend = max inhale (1.0). Trough of trend = max exhale (0.0).
        # To map linearly, we can normalize the last few seconds of the trend.
        recent_trend = trend[-self.fs*3:] # look at last 3 seconds
        t_max, t_min = np.max(recent_trend), np.min(recent_trend)
        if t_max - t_min > 0.001:
            # Normalize between 0 and 1
            phase = (trend[-1] - t_min) / (t_max - t_min)
        else:
            phase = 0.5
            
        # 6. Basic rate estimation via peak finding in the trend
        peaks, _ = scipy.signal.find_peaks(trend, distance=int(self.fs*1.5)) # min 1.5s between breaths
        rate_bpm = 6.0
        if len(peaks) >= 2:
            avg_peak_dist_s = np.mean(np.diff(peaks)) / self.fs
            if avg_peak_dist_s > 0:
                rate_bpm = 60.0 / avg_peak_dist_s
                
        return np.clip(phase, 0.0, 1.0), rate_bpm
