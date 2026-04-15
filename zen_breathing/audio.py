"""
Audio engine -- ocean wave sounds synced with breathing pacing.

All continuous sound layers use band-limited noise (Butterworth IIR bandpass
with causal ``lfilter`` and maintained zi state between callbacks).
No pure sine tones are used for sustained layers.

Layers:
  1. BASS UNDERTOW    (30–180 Hz)   deep ocean rumble, constant
  2. WAVE BODY        (200–1200 Hz) main wave wash -- amplitude = target_phase
  3. WAVE SPRAY       (1000–5000 Hz) cresting shimmer -- only at wave peaks
  4. DARK SURGE       (20–60 Hz)   aperiodic deep surge via two-LFO product
  5. HARMONIC SHIMMER (600–2500 Hz) reward layer -- fades in when sync > 0.5
  6. WHALE SONG       (50–200 Hz)  deep coherence reward -- coherence > 0.5
  7. SINGING BOWL     pre-generated detuned-harmonic transient; triggered
                      when coherence crosses 40% or 70%.

Variety mechanisms:
  - Multi-LFO swell: three sinusoids at prime-ish periods create organic
    amplitude variation that never repeats.
  - Dark surge: product of two odd-frequency LFOs fires aperiodically.
  - Harmonic shimmer: crystalline tones that only appear at sustained
    good sync -- a clear audio reward the user can learn to chase.
"""

import numpy as np
from scipy.signal import butter, lfilter, lfilter_zi

SAMPLE_RATE = 44100
BLOCK_SIZE  = 512

# ────────────────────────────────────────────────────────────────────────
#  Singing bowl partial tuning
# ────────────────────────────────────────────────────────────────────────
_BOWL_PARTIALS = [
    (109.8, 1.00, 0.00),
    (110.3, 0.95, 0.70),
    (219.6, 0.55, 0.30),
    (220.5, 0.52, 1.10),
    (329.7, 0.30, 0.50),
    (440.1, 0.18, 0.90),
    (550.0, 0.10, 1.40),
    (660.2, 0.06, 0.20),
]
_BOWL_DECAY_S    = 4.5
_BOWL_DURATION_S = 7.0
_BOWL_COOLDOWN_S = 30.0

# ────────────────────────────────────────────────────────────────────────
#  Whale song partials (low, warm tones with slow beating)
# ────────────────────────────────────────────────────────────────────────
_WHALE_PARTIALS = [
    (62.0,  1.00, 0.0),    # fundamental
    (62.4,  0.90, 0.3),    # slow beat
    (93.0,  0.55, 0.8),    # 3/2 partial
    (124.0, 0.30, 1.2),    # octave
    (155.0, 0.15, 1.8),    # 5/2
]
_WHALE_DECAY_S    = 6.0
_WHALE_DURATION_S = 8.0
_WHALE_COOLDOWN_S = 45.0


# ════════════════════════════════════════════════════════════════════════
#  Module-level helpers
# ════════════════════════════════════════════════════════════════════════

def _bp_filter(lo: float, hi: float, order: int = 2,
               sr: int = SAMPLE_RATE):
    """Return (b, a, zi_zeros) for a Butterworth bandpass filter."""
    b, a = butter(order, [lo / (sr / 2.0), hi / (sr / 2.0)], btype="band")
    zi   = lfilter_zi(b, a) * 0.0
    return b, a, zi


def _make_transient(partials, decay_s, duration_s, sr=SAMPLE_RATE):
    """Pre-generate a detuned-harmonic transient."""
    n   = int(sr * duration_s)
    t   = np.arange(n, dtype=np.float64) / sr
    env = np.exp(-t / decay_s).astype(np.float32)
    sig = np.zeros(n, dtype=np.float32)
    for freq, amp, phase in partials:
        sig += (amp * np.sin(2 * np.pi * freq * t + phase)).astype(np.float32)
    sig *= env
    pk  = np.max(np.abs(sig))
    if pk > 1e-8:
        sig /= pk
    return sig


# ════════════════════════════════════════════════════════════════════════
#  AudioEngine
# ════════════════════════════════════════════════════════════════════════

class AudioEngine:
    """
    Real-time ocean wave audio that guides breathing.

    Usage::
        audio = AudioEngine(state)
        audio.start()
        ...
        audio.stop()
    """

    def __init__(self, state):
        self.state    = state
        self._stream  = None
        self._t       = 0.0
        self._running = False
        self._volume  = 0.35

        # ── Independent noise sources ────────────────────────────────────
        self._rng_bass   = np.random.RandomState(11)
        self._rng_mid    = np.random.RandomState(22)
        self._rng_bright = np.random.RandomState(33)
        self._rng_surge  = np.random.RandomState(44)
        self._rng_shimmer = np.random.RandomState(55)

        # ── IIR bandpass filters with maintained state ───────────────────
        self._bb,  self._ba,  self._bzi  = _bp_filter(30,   180)
        self._mb,  self._ma,  self._mzi  = _bp_filter(200, 1200)
        self._brb, self._bra, self._brzi = _bp_filter(1000, 5000)
        self._sb,  self._sa,  self._szi  = _bp_filter(20,   60)
        # Shimmer: narrower, more crystalline (600-2500 Hz)
        self._shb, self._sha, self._shzi = _bp_filter(600, 2500)

        # ── Per-band amplitude normalisation ────────────────────────────
        self._gain_bass    = 3.7
        self._gain_mid     = 1.4
        self._gain_bright  = 0.7
        self._gain_surge   = 7.0
        self._gain_shimmer = 1.2

        # ── Singing bowl ─────────────────────────────────────────────────
        self._bowl          = _make_transient(_BOWL_PARTIALS, _BOWL_DECAY_S,
                                              _BOWL_DURATION_S)
        self._bowl_pos      = len(self._bowl)
        self._prev_coh      = 0.0
        self._bowl_cooldown = 0.0

        # ── Whale song ───────────────────────────────────────────────────
        self._whale           = _make_transient(_WHALE_PARTIALS, _WHALE_DECAY_S,
                                                _WHALE_DURATION_S)
        self._whale_pos       = len(self._whale)
        self._whale_cooldown  = 0.0
        self._whale_last_coh  = 0.0

        # ── Shimmer envelope state (for smooth fade) ─────────────────────
        self._shimmer_env = 0.0

        # ── sounddevice ──────────────────────────────────────────────────
        try:
            import sounddevice as sd
            self._sd        = sd
            self._available = True
        except ImportError:
            self._available = False
            print("[Audio] sounddevice not installed -- audio disabled")

    # ────────────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    def start(self):
        if not self._available or self._running:
            return
        try:
            self._stream = self._sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=self._callback,
            )
            self._stream.start()
            self._running = True
        except Exception as e:
            print(f"[Audio] Failed to start: {e}")
            self._running = False

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._running = False

    def set_volume(self, v: float):
        self._volume = max(0.0, min(1.0, v))

    # ────────────────────────────────────────────────────────────────────
    #  Audio callback
    # ────────────────────────────────────────────────────────────────────

    def _callback(self, outdata, frames, time_info, status):
        """Called by sounddevice ~86 ×/s.  Must stay fast."""
        dt      = frames / SAMPLE_RATE
        self._t += dt
        T = self._t

        target_phase = float(self.state.target_phase)
        sync         = float(self.state.sync_score)
        coh          = float(self.state.coherence) / 100.0

        sig = np.zeros(frames, dtype=np.float32)

        # ════════════════════════════════════════════════════════════════
        #  SWELL VARIATION (multi-LFO for organic irregularity)
        # ════════════════════════════════════════════════════════════════
        swell = float(
            1.00
            + 0.20 * np.sin(2 * np.pi * 0.0303 * T)
            + 0.12 * np.sin(2 * np.pi * 0.0526 * T + 1.20)
            + 0.08 * np.sin(2 * np.pi * 0.0189 * T + 2.70)
        )
        swell = max(0.55, min(1.55, swell))

        # ════════════════════════════════════════════════════════════════
        #  Generate band-limited noise
        # ════════════════════════════════════════════════════════════════
        raw_b,  self._bzi  = lfilter(self._bb,  self._ba,
                                     self._rng_bass.randn(frames).astype(np.float32),
                                     zi=self._bzi)
        raw_m,  self._mzi  = lfilter(self._mb,  self._ma,
                                     self._rng_mid.randn(frames).astype(np.float32),
                                     zi=self._mzi)
        raw_br, self._brzi = lfilter(self._brb, self._bra,
                                     self._rng_bright.randn(frames).astype(np.float32),
                                     zi=self._brzi)
        raw_s,  self._szi  = lfilter(self._sb,  self._sa,
                                     self._rng_surge.randn(frames).astype(np.float32),
                                     zi=self._szi)
        raw_sh, self._shzi = lfilter(self._shb, self._sha,
                                     self._rng_shimmer.randn(frames).astype(np.float32),
                                     zi=self._shzi)

        bass_n    = (raw_b  * self._gain_bass   ).astype(np.float32)
        mid_n     = (raw_m  * self._gain_mid    ).astype(np.float32)
        bright_n  = (raw_br * self._gain_bright ).astype(np.float32)
        surge_n   = (raw_s  * self._gain_surge  ).astype(np.float32)
        shimmer_n = (raw_sh * self._gain_shimmer).astype(np.float32)

        # ════════════════════════════════════════════════════════════════
        #  Layer 1: DEEP BASS UNDERTOW  (30–180 Hz)
        # ════════════════════════════════════════════════════════════════
        sig += 0.22 * swell * bass_n

        # ════════════════════════════════════════════════════════════════
        #  Layer 2: WAVE BODY  (200–1200 Hz) — THE BREATHING GUIDE
        # ════════════════════════════════════════════════════════════════
        wave_env = (0.08 + 0.92 * target_phase) * swell
        sig += 0.60 * wave_env * mid_n

        # ════════════════════════════════════════════════════════════════
        #  Layer 3: WAVE SPRAY  (1000–5000 Hz)
        # ════════════════════════════════════════════════════════════════
        spray_env = (target_phase ** 2.0) * (0.30 + 0.70 * sync) * swell
        sig += 0.18 * spray_env * bright_n

        # ════════════════════════════════════════════════════════════════
        #  Layer 4: DARK SURGE  (20–60 Hz, aperiodic)
        # ════════════════════════════════════════════════════════════════
        surge_val = float(
            np.sin(2 * np.pi * 0.0230 * T) *
            np.sin(2 * np.pi * 0.0310 * T)
        )
        surge_active = max(0.0, surge_val)
        if surge_active > 0.35:
            r = (surge_active - 0.35) / 0.65
            sig += 0.22 * r * surge_n

        # ════════════════════════════════════════════════════════════════
        #  Layer 5: HARMONIC SHIMMER  (600–2500 Hz) — REWARD LAYER
        #  Fades in when sync > 0.5, giving clear audio feedback
        #  that the user is doing well. Crystalline, ethereal quality.
        # ════════════════════════════════════════════════════════════════
        shimmer_target = max(0.0, (sync - 0.4) / 0.6)  # 0 below 0.4, ramps to 1
        shimmer_target *= coh  # modulate by coherence too
        # Smooth envelope (avoid pops)
        self._shimmer_env += 0.15 * (shimmer_target - self._shimmer_env)
        if self._shimmer_env > 0.01:
            # Pitch-modulate slightly with breath for organic feel
            pitch_mod = 1.0 + 0.05 * np.sin(2 * np.pi * 0.07 * T)
            sig += 0.14 * self._shimmer_env * swell * shimmer_n * float(pitch_mod)

        # ════════════════════════════════════════════════════════════════
        #  Layer 6: WHALE SONG  (50–200 Hz) — deep coherence reward
        #  A warm, resonant transient triggered when coherence
        #  crosses 50% upward. 45s cooldown.
        # ════════════════════════════════════════════════════════════════
        self._whale_cooldown = max(0.0, self._whale_cooldown - dt)
        if self._whale_cooldown <= 0.0:
            if self._whale_last_coh < 0.50 <= coh:
                self._whale_pos      = 0
                self._whale_cooldown = _WHALE_COOLDOWN_S
        self._whale_last_coh = coh

        if self._whale_pos < len(self._whale):
            end_pos = min(self._whale_pos + frames, len(self._whale))
            chunk   = self._whale[self._whale_pos:end_pos]
            if len(chunk) < frames:
                chunk = np.pad(chunk, (0, frames - len(chunk)))
            sig += 0.16 * coh * chunk.astype(np.float32)
            self._whale_pos = end_pos

        # ════════════════════════════════════════════════════════════════
        #  Layer 7: SINGING BOWL  (detuned harmonic transient)
        #  Triggered when coherence crosses 40% or 70% upward.
        # ════════════════════════════════════════════════════════════════
        self._bowl_cooldown = max(0.0, self._bowl_cooldown - dt)
        if self._bowl_cooldown <= 0.0:
            for threshold in (0.40, 0.70):
                if self._prev_coh < threshold <= coh:
                    self._bowl_pos      = 0
                    self._bowl_cooldown = _BOWL_COOLDOWN_S
                    break
        self._prev_coh = coh

        if self._bowl_pos < len(self._bowl):
            end_pos = min(self._bowl_pos + frames, len(self._bowl))
            chunk   = self._bowl[self._bowl_pos:end_pos]
            if len(chunk) < frames:
                chunk = np.pad(chunk, (0, frames - len(chunk)))
            sig += 0.22 * coh * chunk.astype(np.float32)
            self._bowl_pos = end_pos

        # ════════════════════════════════════════════════════════════════
        #  MASTER: volume, fade-in, soft clip
        # ════════════════════════════════════════════════════════════════
        sig *= self._volume

        if self._t < 3.0:
            sig *= float(np.clip(self._t / 3.0, 0.0, 1.0))

        sig = np.tanh(sig * 1.4) / 1.4

        outdata[:, 0] = sig
