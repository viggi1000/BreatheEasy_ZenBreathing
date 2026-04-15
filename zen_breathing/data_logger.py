"""
Data logger -- records session data to timestamped JSON Lines file.

Logs every 1 second: breathing, HRV, coherence, sync, session phase.
Generates a summary report at session end.
"""

import json
import time
import os
from datetime import datetime
from pathlib import Path

from zen_breathing.state import BreathingState


class DataLogger:
    """
    Logs BreathingState parameters to a .jsonl file.

    Usage:
        logger = DataLogger(state)
        logger.start()
        # ... session runs ...
        logger.stop()  # writes summary report
    """

    def __init__(self, state: BreathingState, output_dir: str = "exports"):
        self.state = state
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._file = None
        self._filepath = None
        self._report_path = None
        self._running = False
        self._start_time = None
        self._last_log = 0.0

        # Accumulators for report
        self._coherence_samples = []
        self._sync_samples = []
        self._rate_samples = []
        self._hr_samples = []
        self._ei_ratio_samples = []
        self._n_records = 0

    def start(self):
        """Begin logging to a new timestamped file."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._filepath = self._output_dir / f"zen_session_{ts}.jsonl"
        self._report_path = self._output_dir / f"zen_report_{ts}.txt"

        self._file = open(self._filepath, "w", encoding="utf-8")
        self._running = True
        self._start_time = time.time()
        self._last_log = 0.0

        # Write header
        header = {
            "type": "session_start",
            "timestamp": datetime.now().isoformat(),
            "version": "1.0",
        }
        self._write(header)

    def update(self):
        """Call periodically (e.g. every engine tick). Logs at ~1 Hz."""
        if not self._running or self._file is None:
            return

        now = time.time()
        if now - self._last_log < 1.0:
            return
        self._last_log = now

        s = self.state
        record = {
            "type": "sample",
            "t": round(now - self._start_time, 1),
            "session_time": round(s.session_time, 1),
            "session_phase": s.session_phase,
            "breath_phase": round(s.breath_phase, 3),
            "breath_rate_bpm": round(s.breath_rate_bpm, 1),
            "current_pace_bpm": round(s.current_pace_bpm, 1),
            "target_rate_bpm": round(s.target_rate_bpm, 1),
            "coherence": round(s.coherence, 1),
            "sync_score": round(s.sync_score, 3),
            "asi": round(s.asi, 1),
            "heart_rate": round(s.heart_rate, 1),
            "exhale_inhale_ratio": round(s.exhale_inhale_ratio, 2),
            "is_calibrated": s.is_calibrated,
            "calibrated_rate_bpm": round(s.calibrated_rate_bpm, 1),
            "resonance_bpm": round(s.resonance_bpm, 1),
        }
        self._write(record)
        self._n_records += 1

        # Accumulate for report
        self._coherence_samples.append(s.coherence)
        self._sync_samples.append(s.sync_score)
        self._rate_samples.append(s.breath_rate_bpm)
        self._hr_samples.append(s.heart_rate)
        self._ei_ratio_samples.append(s.exhale_inhale_ratio)

    def stop(self):
        """Stop logging and write summary report."""
        if not self._running:
            return

        # Final record
        record = {
            "type": "session_end",
            "timestamp": datetime.now().isoformat(),
            "total_records": self._n_records,
        }
        self._write(record)

        if self._file:
            self._file.close()
            self._file = None

        self._running = False

        # Generate report
        self._write_report()

    def _write(self, record: dict):
        if self._file:
            self._file.write(json.dumps(record) + "\n")
            self._file.flush()

    def _write_report(self):
        """Generate a human-readable summary report."""
        if not self._coherence_samples:
            return

        import numpy as np
        coh = np.array(self._coherence_samples)
        sync = np.array(self._sync_samples)
        rate = np.array(self._rate_samples)
        hr = np.array(self._hr_samples)
        ei = np.array(self._ei_ratio_samples)

        duration = self.state.session_time

        lines = [
            "=" * 60,
            "  Z E N B R E A T H I N G  --  Session Report",
            "=" * 60,
            "",
            f"Date:     {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Duration: {int(duration // 60)}m {int(duration % 60)}s",
            f"Samples:  {self._n_records}",
            f"Data:     {self._filepath.name}",
            "",
            "--- Breathing ---",
            f"  Start rate:      {rate[0]:.1f} BPM",
            f"  End rate:        {rate[-1]:.1f} BPM",
            f"  Lowest rate:     {np.min(rate):.1f} BPM",
            f"  Calibrated at:   {self.state.calibrated_rate_bpm:.1f} BPM",
            f"  Target:          {self.state.resonance_bpm:.1f} BPM",
            "",
            "--- Coherence ---",
            f"  Mean:            {np.mean(coh):.1f}%",
            f"  Max:             {np.max(coh):.1f}%",
            f"  Time > 50%:      {np.sum(coh > 50) / len(coh) * 100:.0f}% of session",
            "",
            "--- Sync Score ---",
            f"  Mean:            {np.mean(sync):.2f}",
            f"  Max:             {np.max(sync):.2f}",
            "",
            "--- Heart Rate ---",
            f"  Start:           {hr[0]:.0f} BPM",
            f"  End:             {hr[-1]:.0f} BPM",
            f"  Mean:            {np.mean(hr):.0f} BPM",
            f"  Min:             {np.min(hr):.0f} BPM",
            "",
            "--- Exhale/Inhale Ratio ---",
            f"  Mean:            {np.mean(ei):.2f}",
            f"  End:             {ei[-1]:.2f}",
            f"  Target:          {self.state.target_ei_ratio:.2f}",
            "",
            "=" * 60,
        ]

        with open(self._report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"[Logger] Report saved: {self._report_path}")
        print(f"[Logger] Data saved:   {self._filepath}")

    @property
    def filepath(self):
        return self._filepath
