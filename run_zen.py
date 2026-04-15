#!/usr/bin/env python3
"""
ZenBreathing -- Biofeedback breathing art experience.

Usage:
    python run_zen.py                    # Welcome screen (choose mock/live)
    python run_zen.py --demo             # Skip welcome, start demo immediately
    python run_zen.py --demo --fast      # Fast demo  (3x speed, ~90 s arc)
    python run_zen.py --live             # Skip welcome, connect to Polar H10
    python run_zen.py --theme aurora     # Aurora borealis theme
    python run_zen.py --no-audio         # Launch without audio
    python run_zen.py --windowed         # Start in windowed mode
    python run_zen.py --debug            # Start with debug panel visible

Controls:
    F11     Toggle fullscreen
    H       Toggle HUD overlay
    D       Toggle debug signal panel
    A       Toggle audio
    T       Switch theme (ocean / aurora)
    G       Re-show guide message
    Space   Pause / resume
    Esc     Quit
"""

import sys
import argparse

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QSurfaceFormat


def main():
    parser = argparse.ArgumentParser(description="ZenBreathing")
    parser.add_argument("--live", action="store_true", help="Connect to real Polar H10")
    parser.add_argument("--demo", action="store_true", help="Skip welcome, start demo")
    parser.add_argument("--fast", action="store_true", help="3x demo speed")
    parser.add_argument("--theme", default="ocean", choices=["ocean", "aurora"])
    parser.add_argument("--no-audio", action="store_true", help="Disable audio")
    parser.add_argument("--windowed", action="store_true", help="Start windowed")
    parser.add_argument("--debug", action="store_true", help="Show debug panel")
    args = parser.parse_args()

    # Request OpenGL 3.3 Core (required for moderngl shaders)
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setSwapInterval(1)  # vsync
    fmt.setSamples(4)       # MSAA
    QSurfaceFormat.setDefaultFormat(fmt)

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("ZenBreathing")

    from zen_breathing.app import ZenBreathingApp

    # Determine mode
    mode = None
    if args.live:
        mode = "live"
    elif args.demo:
        mode = "demo"
    # else: None = show welcome screen

    window = ZenBreathingApp(
        mode=mode,
        fast=args.fast,
        theme=args.theme,
        audio_on=not args.no_audio,
    )

    if args.windowed:
        window.showMaximized()
    else:
        window.showFullScreen()
        # Cursor stays visible on the welcome/connect screen.
        # It is hidden automatically when the session starts (in _start_session_active).

    # Auto-show debug panel if requested
    if args.debug:
        window._debug_visible = True
        if window._debug:
            window._debug.setVisible(True)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
