import numpy as np
import scipy.signal

class HRVAnalyzer:
    def __init__(self, fs=130):
        self.fs = fs
        self.b, self.a = scipy.signal.butter(3, [0.5/(self.fs/2), 40.0/(self.fs/2)], btype='band')
        self.last_r_peaks = []
        
    def extract_metrics(self, ecg_signal: np.ndarray):
        """
        ecg_signal: np.ndarray, typically ~10 seconds of data.
        Returns: (rmssd_ms, coherence_0_to_1)
        """
        if ecg_signal is None or len(ecg_signal) < self.fs * 5:
            return 20.0, 0.5
            
        # 1. Clean ECG
        filtered = scipy.signal.filtfilt(self.b, self.a, ecg_signal)
        
        # 2. Simple QRS/R-peak detector (Pan-Tompkins heavily simplified)
        # Derivative -> Square -> Moving average
        derived = np.gradient(filtered)
        squared = derived ** 2
        window = int(0.12 * self.fs)
        integrated = np.convolve(squared, np.ones(window)/window, mode='same')
        
        # 3. Find Peaks
        peaks, _ = scipy.signal.find_peaks(integrated, distance=int(self.fs*0.4), height=np.mean(integrated)*1.5)
        
        if len(peaks) < 3:
            return 20.0, 0.5
            
        # 4. RR intervals in milliseconds
        rr_intervals = np.diff(peaks) / self.fs * 1000.0
        
        # Filter implausible beats
        valid_rr = rr_intervals[(rr_intervals > 300) & (rr_intervals < 2000)]
        if len(valid_rr) < 2:
            return 20.0, 0.5
            
        # 5. RMSSD calculation for parasympathetic tone proxy
        successive_diffs = np.diff(valid_rr)
        rmssd = float(np.sqrt(np.mean(successive_diffs**2)))
        
        # 6. Proxy for "Coherence" based on rhythmic SDNN/RMSSD ratios
        # Normal breathing creates resonant waves (high SDNN compared to RMSSD which measures high frequency noise)
        sdnn = float(np.std(valid_rr, ddof=1))
        
        # A crude but effective real-time proxy for respiratory sinus arrhythmia (RSA) coherence 
        # is the ratio of SDNN (total variability) to RMSSD (beat-to-beat chaotic variability).
        # High ratio = smooth sine wave breathing. Low ratio = erratic/choppy breathing.
        ratio = sdnn / (rmssd + 1.0) 
        
        # Map ratio (~0.5 to ~3.0) to 0.0 - 1.0 coherence score
        coherence = np.clip((ratio - 0.5) / 2.0, 0.0, 1.0)
        
        return rmssd, coherence
