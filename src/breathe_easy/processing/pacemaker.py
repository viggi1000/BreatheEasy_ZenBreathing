import time
import math
from breathe_easy.data_bus import PolarDataBus
from breathe_easy.processing.resonance import ResonanceEstimator

class GuidedPacemaker:
    def __init__(self, data_bus: PolarDataBus):
        self.bus = data_bus
        self.estimator = ResonanceEstimator()
        
        self.start_time = time.time()
        self.state = "CALIBRATION" # CALIBRATION -> SWEEP -> LOCK
        
        self.current_target_bpm = 15.0
        # For asymmetric breathing. Inhale 40%, Exhale 60%
        self.inhale_ratio = 0.40  
        
    def tick(self, dt: float):
        t = time.time() - self.start_time
        
        # --- State Machine Logic ---
        if self.state == "CALIBRATION":
            # Match the user's natural breathing initially to hook them
            self.current_target_bpm = self.bus.current_breath_rate_bpm
            if t > 20.0:  # After 20 seconds, we begin the sweep
                self.state = "SWEEP"
                self.sweep_start_bpm = self.current_target_bpm
                self.sweep_start_t = t
                
        elif self.state == "SWEEP":
            # Gradually lower by 0.5 BPM every 10 seconds
            sweep_duration = t - self.sweep_start_t
            drop = (sweep_duration / 10.0) * 0.5
            new_target = max(4.5, self.sweep_start_bpm - drop)
            
            self.current_target_bpm = new_target
            
            # Feed the Resonance algorithm
            self.estimator.add_reading(self.current_target_bpm, self.bus.coherence_score)
            self.bus.estimated_resonant_bpm = self.estimator.peak_resonance_bpm
            
            # Stop sweeping if we hit bottom
            if new_target <= 4.5:
                self.state = "LOCK"
                self.lock_target = self.estimator.peak_resonance_bpm
                
        elif self.state == "LOCK":
            # Smoothly transition to their personalized ideal peak
            self.current_target_bpm += (self.lock_target - self.current_target_bpm) * 0.01

        # --- Phase Construction (Asymmetric 40/60 Exhale Focus) ---
        period_s = 60.0 / self.current_target_bpm
        phase = (t % period_s) / period_s  # Standard 0->1 linear progression
        
        # Distort the math
        # Standard: 0 to 0.5 is inhale. 0.5 to 1.0 is exhale.
        # Asymmetric target: 0 to 0.4 is inhale. 0.4 to 1.0 is exhale.
        if phase < self.inhale_ratio:
            # Map 0 -> 0.4 to 0.0 -> 1.0 (Inhale volume curve)
            val = phase / self.inhale_ratio
            mapped_phase = math.sin(val * (math.pi / 2)) # ease out
        else:
            # Map 0.4 -> 1.0 to 1.0 -> 0.0 (Exhale volume curve)
            val = (phase - self.inhale_ratio) / (1.0 - self.inhale_ratio)
            mapped_phase = 1.0 - math.sin(val * (math.pi / 2)) # ease in
            
        self.bus.target_bpm = self.current_target_bpm
        self.bus.target_breath_phase = mapped_phase
        
        # --- Synchronization Scoring ---
        # Did the user phase closely match target mapped phase?
        diff = abs(mapped_phase - self.bus.current_breath_phase)
        sync_strength = max(0.0, 1.0 - (diff * 2.5)) # tighter window
        
        # Smooth the sync score
        self.bus.sync_score += (sync_strength - self.bus.sync_score) * 0.05
