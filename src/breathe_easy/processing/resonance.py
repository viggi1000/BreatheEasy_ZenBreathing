import numpy as np

class ResonanceEstimator:
    def __init__(self):
        # We sweep from 15 BPM down to 4.5 BPM normally.
        # We'll map scores to buckets of 0.5 BPM.
        self.bpm_scores = {}
        self.peak_resonance_bpm = 6.0 # Default fallback
        self._min_samples_per_bucket = 3
        
    def add_reading(self, bpm: float, coherence: float):
        """Register a coherence score while the patient was at a specific BPM."""
        # Clean bracket formatting (e.g., 6.34 -> 6.5)
        bucket = round(bpm * 2.0) / 2.0
        bucket = max(4.5, min(bucket, 18.0))
        
        if bucket not in self.bpm_scores:
            self.bpm_scores[bucket] = []
            
        self.bpm_scores[bucket].append(coherence)
        self._recalculate_peak()

    def _recalculate_peak(self):
        best_score = -1.0
        best_bpm = 6.0
        
        for bpm, scores in self.bpm_scores.items():
            if len(scores) >= self._min_samples_per_bucket:
                # Average coherence at this BPM
                avg_coherence = sum(scores) / len(scores)
                if avg_coherence > best_score:
                    best_score = avg_coherence
                    best_bpm = bpm
                    
        self.peak_resonance_bpm = best_bpm
