import pyqtgraph as pg
import numpy as np
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from PyQt5.QtCore import QTimer

from breathe_easy.data_bus import PolarDataBus
from breathe_easy.utils.constants import DARK_THEME

class DebugDashboardWidget(QWidget):
    def __init__(self, data_bus: PolarDataBus, parent=None):
        super().__init__(parent)
        self.data_bus = data_bus
        
        self.setStyleSheet(f"background-color: {DARK_THEME['background']};")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        pg.setConfigOption('background', DARK_THEME['plot_bg'])
        pg.setConfigOption('foreground', DARK_THEME['text'])
        
        self._build_plots()
        
        # History buffers for the 3rd chart (Sync tracker)
        self.phase_history = np.zeros(200)
        self.target_history = np.zeros(200)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_plots)
        self.timer.start(50) 
        
    def _build_plots(self):
        # 1. ECG Plot
        self.ecg_plot = pg.PlotWidget(title="ECG (130 Hz)")
        self.ecg_plot.showGrid(x=True, y=True, alpha=0.3)
        self.ecg_plot.setLabel('left', 'Amplitude', units='uV')
        self.ecg_plot.setYRange(-600, 600)
        self.curve_ecg = self.ecg_plot.plot(pen=pg.mkPen(DARK_THEME['primary'], width=1.0))

        # 2. Accelerometer
        self.acc_plot = pg.PlotWidget(title="Accelerometer (100 Hz)")
        self.acc_plot.showGrid(x=True, y=True, alpha=0.3)
        self.acc_plot.setLabel('left', 'Acceleration', units='kmg')
        self.acc_plot.setYRange(-1.3, 0.5)
        
        # Match user's legend precisely
        self.curve_x = self.acc_plot.plot(pen=pg.mkPen('#FFAA00', width=1.0), name="X") # Orange
        self.curve_y = self.acc_plot.plot(pen=pg.mkPen('#00FF00', width=1.0), name="Y") # Green
        self.curve_z = self.acc_plot.plot(pen=pg.mkPen('#FF0055', width=1.0), name="Z") # Pink/Red
        self.acc_plot.addLegend()

        # 3. Sync Tracker
        self.sync_plot = pg.PlotWidget(title="Respiration Target vs Actual")
        self.sync_plot.showGrid(x=True, y=True, alpha=0.3)
        self.sync_plot.setLabel('left', 'Phase', units='0..1')
        self.sync_plot.setYRange(0, 1)
        self.curve_target = self.sync_plot.plot(pen=pg.mkPen('#FFFFFF', width=2.0, style=pg.QtCore.Qt.DashLine), name="Guided Target")
        self.curve_actual = self.sync_plot.plot(pen=pg.mkPen(DARK_THEME['secondary'], width=2.5), name="Actual Phase")
        self.sync_plot.addLegend()
        
        # 4. Heart Rate Plot (Native from Sensor)
        self.hr_plot = pg.PlotWidget(title="Heart Rate")
        self.hr_plot.showGrid(x=True, y=True, alpha=0.3)
        self.hr_plot.setLabel('left', 'BPM')
        # We will dynamically range this, but typical HRV range is 40-100
        self.curve_hr = self.hr_plot.plot(pen=pg.mkPen('#FF88DD', width=2.0), symbol='o', symbolSize=4)

        # Build Layout
        self.layout().addWidget(self.ecg_plot)
        self.layout().addWidget(self.acc_plot)
        self.layout().addWidget(self.sync_plot)
        self.layout().addWidget(self.hr_plot)

    def update_plots(self):
        # 1. Update ECG
        ecg = self.data_bus.get_recent_ecg(seconds=10.0)
        if ecg is not None and len(ecg) > 0:
            self.curve_ecg.setData(ecg)
            
        # 2. Update ACC X, Y, Z
        acc = self.data_bus.get_recent_acc(seconds=10.0)
        if acc is not None and len(acc) > 0:
            self.curve_x.setData(acc[:, 0] / 1000.0)
            self.curve_y.setData(acc[:, 1] / 1000.0)
            self.curve_z.setData(acc[:, 2] / 1000.0)
                
        # 3. Scroll the Sync phase histories
        self.phase_history = np.roll(self.phase_history, -1)
        self.target_history = np.roll(self.target_history, -1)
        
        self.phase_history[-1] = self.data_bus.current_breath_phase
        self.target_history[-1] = self.data_bus.target_breath_phase
        
        self.curve_actual.setData(self.phase_history)
        self.curve_target.setData(self.target_history)
        
        # 4. Update HR
        hr_data = self.data_bus.get_recent_hr()
        if hr_data is not None and len(hr_data) > 0:
            self.curve_hr.setData(hr_data)
