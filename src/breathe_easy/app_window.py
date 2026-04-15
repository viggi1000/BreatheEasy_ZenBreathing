import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTabWidget
)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont

# Core Data & Signals
from breathe_easy.data_bus import PolarDataBus
from breathe_easy.workers.ble_worker import BLEWorker
from breathe_easy.processing.respiration import RespirationProcessor
from breathe_easy.processing.hrv import HRVAnalyzer
from breathe_easy.processing.pacemaker import GuidedPacemaker

# UI / Aesthetics Components
from breathe_easy.ui.visual_engine import VisualEngineWidget
from breathe_easy.ui.intro_guide import IntroGuideWidget
from breathe_easy.ui.debug_dashboard import DebugDashboardWidget
from breathe_easy.ui.audio_engine import OceanAudioEngine

class ZenBreathingApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BreatheEasy - Zen Journey")
        self.resize(1200, 800)
        self.setStyleSheet("background-color: #050510; color: #aaaaaa;")
        
        # Central routing
        self.data_bus = PolarDataBus()
        self.resp_proc = RespirationProcessor()
        self.hrv_proc = HRVAnalyzer()
        self.audio_engine = OceanAudioEngine()
        self.pacemaker = GuidedPacemaker(self.data_bus)
        
        self.ble_worker = None
        self.ble_thread = None
        
        # Exponential moving averages for smooth animations
        self.smoothed_breath = 0.5
        self.smoothed_coherence = 0.5
        self.alpha_breath = 0.05
        self.alpha_coherence = 0.01

        self._build_ui()
        self._start_processing_loop()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Build Tab Widget
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabBar::tab { background: #111122; color: #aaa; padding: 10px 20px; border-top-left-radius: 4px; border-top-right-radius: 4px;}
            QTabBar::tab:selected { background: #333344; color: #fff; font-weight: bold;}
            QTabWidget::pane { border: 0; }
        """)
        
        # 1. Guide Tab
        self.guide_view = IntroGuideWidget()
        self.tabs.addTab(self.guide_view, "Guide & Intro")
        
        # 2. Immersive Visual Art Tab
        self.visual_engine = VisualEngineWidget()
        self.tabs.addTab(self.visual_engine, "Oceanic Biofeedback")
        
        # 3. Debug Dashboard Tab
        self.debug_dashboard = DebugDashboardWidget(self.data_bus)
        self.tabs.addTab(self.debug_dashboard, "Signal Debugger")
        
        # Switch immediately to the visual view if desired, or let them read the guide
        self.tabs.setCurrentIndex(0)
        layout.addWidget(self.tabs, stretch=1)
        
        # Minimalist control overlay at the bottom
        controls = QHBoxLayout()
        controls.setContentsMargins(20, 10, 20, 10)
        
        self.lbl_status = QLabel("Ready. Please read the Guide, then select a mode.")
        self.lbl_status.setFont(QFont("Arial", 10))
        controls.addWidget(self.lbl_status)
        
        controls.addStretch()
        
        self.btn_mock = QPushButton("Start Demo (Mock)")
        self.btn_mock.setStyleSheet(self._btn_style())
        self.btn_mock.clicked.connect(lambda: self._start_sensor(use_mock=True))
        controls.addWidget(self.btn_mock)
        
        self.btn_live = QPushButton("Start Live (BLE)")
        self.btn_live.setStyleSheet(self._btn_style())
        self.btn_live.clicked.connect(self._scan_and_connect)
        controls.addWidget(self.btn_live)
        
        layout.addLayout(controls)

    def _btn_style(self):
        return """
            QPushButton {
                background-color: #111122;
                border: 1px solid #333344;
                border-radius: 4px;
                padding: 6px 16px;
                color: #dddddd;
            }
            QPushButton:hover {
                background-color: #222244;
            }
        """

    def _scan_and_connect(self):
        self.btn_live.setEnabled(False)
        self.btn_mock.setEnabled(False)
        self.lbl_status.setText("Scanning for Polar...")
        self.ble_worker = BLEWorker(self.data_bus, use_mock=False)
        self.ble_worker.status.connect(self.lbl_status.setText)
        self.ble_worker.device_found.connect(self._on_device_found)
        
        from PyQt5.QtCore import QThread
        self.scan_thread = QThread()
        self.ble_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.ble_worker.run_scan)
        self.scan_thread.start()
        
    def _on_device_found(self, name, address):
        self.lbl_status.setText(f"Found {name}. Connecting...")
        self._start_sensor(use_mock=False, address=address)

    def _start_sensor(self, use_mock: bool, address: str = None):
        self.btn_mock.setEnabled(False)
        self.btn_live.setEnabled(False)
        
        from PyQt5.QtCore import QThread
        self.ble_worker = BLEWorker(self.data_bus, use_mock=use_mock)
        if address:
            self.ble_worker.set_device_address(address)
            
        self.ble_worker.status.connect(self.lbl_status.setText)
        
        self.ble_thread = QThread()
        self.ble_worker.moveToThread(self.ble_thread)
        self.ble_thread.started.connect(self.ble_worker.run)
        self.ble_thread.start()
        
        self.audio_engine.start()
        self.tabs.setCurrentIndex(1)

    def _start_processing_loop(self):
        self.proc_timer = QTimer()
        self.proc_timer.timeout.connect(self._process_data)
        self.proc_timer.start(50) 
        
        self.analysis_timer = QTimer()
        self.analysis_timer.timeout.connect(self._run_heavy_analysis)
        self.analysis_timer.start(500) 

    def _run_heavy_analysis(self):
        acc = self.data_bus.get_recent_acc(seconds=5.0)
        if acc is not None:
            raw_phase, act_bpm = self.resp_proc.extract_phase_and_rate(acc)
            self.data_bus.current_breath_phase = raw_phase
            self.data_bus.current_breath_rate_bpm = act_bpm
            
        ecg = self.data_bus.get_recent_ecg(seconds=10.0)
        if ecg is not None:
            _, raw_coherence = self.hrv_proc.extract_metrics(ecg)
            self.data_bus.coherence_score = raw_coherence

    def _process_data(self):
        # Tick the guide
        self.pacemaker.tick(0.05)
        
        target_breath = self.data_bus.current_breath_phase
        self.smoothed_breath += (target_breath - self.smoothed_breath) * self.alpha_breath
        
        target_coh = self.data_bus.coherence_score
        self.smoothed_coherence += (target_coh - self.smoothed_coherence) * self.alpha_coherence
        
        # Audio Engine gets the TARGET phase (guiding sound)
        self.audio_engine.set_biofeedback(self.data_bus.target_breath_phase)
        
        # Visual Engine gets the PATIENT ACTUAl phase + Sync Score
        self.visual_engine.set_biofeedback(self.smoothed_breath, self.smoothed_coherence, self.data_bus.sync_score)

    def closeEvent(self, event):
        self.audio_engine.stop()
        if self.ble_worker:
            self.ble_worker.stop()
        super().closeEvent(event)

def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    window = ZenBreathingApp()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
