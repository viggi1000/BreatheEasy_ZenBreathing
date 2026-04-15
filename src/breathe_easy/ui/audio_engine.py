import numpy as np
import scipy.signal
import sounddevice as sd
from PyQt5.QtCore import QObject

class OceanAudioEngine(QObject):
    def __init__(self, sample_rate=44100):
        super().__init__()
        self.fs = sample_rate
        self.stream = None
        self.running = False
        
        # Audio State parameters mapped from biofeedback
        self.target_breath = 0.5
        self.current_breath = 0.5
        
        # Lowpass filter state
        self.zi = None
        self.b, self.a = scipy.signal.butter(2, 0.1) # Default cutoff, updated dynamically

    def set_biofeedback(self, breath: float):
        self.target_breath = np.clip(breath, 0.0, 1.0)

    def _audio_callback(self, outdata, frames, time, status):
        if status:
            print(status)
            
        # Smooth modulation of the breath parameter to avoid clicking/popping
        self.current_breath += (self.target_breath - self.current_breath) * 0.05
        
        # Base audio: White noise
        noise = np.random.randn(frames).astype(np.float32)
        
        # Sound design: Ocean waves
        # Volume swells violently on inhale (1.0) and softens on exhale (0.0)
        # Cutoff frequency also opens up (brighter sound) on inhale.
        vol = 0.02 + (self.current_breath * 0.15)
        
        cutoff = 400 + (self.current_breath * 2000)
        norm_cutoff = cutoff / (self.fs / 2.0)
        
        # Recompute butterworth for new cutoff
        b, a = scipy.signal.butter(2, np.clip(norm_cutoff, 0.01, 0.99))
        
        if self.zi is None:
            self.zi = scipy.signal.lfilter_zi(b, a) * noise[0]
            
        filtered_noise, self.zi = scipy.signal.lfilter(b, a, noise, zi=self.zi)
        
        # Apply volume and output
        output = filtered_noise * vol
        
        # Duplicate to stereo if output has 2 channels
        if outdata.shape[1] == 2:
            outdata[:, 0] = output
            outdata[:, 1] = output
        else:
            outdata[:, 0] = output

    def start(self):
        if not self.running:
            try:
                self.stream = sd.OutputStream(
                    samplerate=self.fs, 
                    channels=2, 
                    callback=self._audio_callback
                )
                self.stream.start()
                self.running = True
            except Exception as e:
                print(f"Error starting audio stream: {e}")

    def stop(self):
        if self.running and self.stream:
            self.stream.stop()
            self.stream.close()
            self.running = False
