"""
BLE connection manager for Polar H10.

Uses PolarDataBus for thread-safe data sharing.
Avoids the "Cannot connect to nullptr" cross-thread signal bug by writing
data directly to PolarDataBus (plain Python deque) instead of emitting
Qt data signals to a non-QObject receiver.

Flow:
  1. scan()             -> emits device_found(name, address) for each Polar
  2. connect_device(addr) -> QThread runs _BLEWorker.run()
  3. Worker: HR first -> wait first packet -> MTU -> PMD -> ECG + ACC
  4. Worker writes to data_bus.*  (no Qt data signals at all)
  5. streams_ready() emitted when HR + ECG + ACC all confirmed flowing
"""

import asyncio

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from zen_breathing.polar_data_bus import PolarDataBus

HR_FIRST_PACKET_TIMEOUT_S = 25.0


class _BLEWorker(QObject):
    """
    Runs async BLE I/O in a dedicated QThread.
    Writes sensor data DIRECTLY to PolarDataBus -- no Qt data signals.
    Only control signals (status, connected, device_found, streams_ready).
    """

    status       = pyqtSignal(str)
    connected    = pyqtSignal(bool)
    device_found = pyqtSignal(str, str)
    streams_ready = pyqtSignal()   # emitted once when all 3 streams confirmed

    def __init__(self, data_bus: PolarDataBus):
        super().__init__()
        self.data_bus = data_bus
        self._running = False
        self._device_address = None

    def set_device_address(self, address: str):
        self._device_address = address

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    #  Scan  (called via QThread.started for scan thread)
    # ------------------------------------------------------------------

    def run_scan(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_scan())
        except Exception as e:
            self.status.emit(f"Scan error: {e}")
        finally:
            loop.close()

    async def _async_scan(self):
        try:
            from bleak import BleakScanner
        except ImportError:
            self.status.emit("bleak not installed -- pip install bleak bleakheart")
            return

        self.status.emit("Scanning for Polar devices...")
        devices = await BleakScanner.discover(timeout=8.0)
        found = False
        for d in devices:
            name = d.name or ""
            if "polar" in name.lower():
                self.device_found.emit(name, d.address)
                found = True
        if not found:
            self.status.emit("No Polar devices found.\nMake sure Polar H10 is turned on and charged.")

    # ------------------------------------------------------------------
    #  Stream  (called via QThread.started for stream thread)
    # ------------------------------------------------------------------

    def run(self):
        self._running = True
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_stream())
        except Exception as e:
            self.status.emit(f"BLE error: {e}")
            self.connected.emit(False)
        finally:
            loop.close()

    async def _async_stream(self):
        try:
            import bleakheart as bh
            from bleak import BleakClient
        except ImportError:
            self.status.emit("bleakheart not installed -- pip install bleakheart")
            self.connected.emit(False)
            return

        if not self._device_address:
            self.status.emit("No device address set")
            return

        disconnected_event = asyncio.Event()

        def on_disconnect(client):
            self.status.emit("Polar disconnected")
            self.connected.emit(False)
            disconnected_event.set()

        retry_count = 0
        streams_confirmed = False

        while self._running and retry_count <= 5:
            try:
                msg = f"Connecting to {self._device_address}"
                if retry_count:
                    msg += f"  (retry {retry_count})"
                self.status.emit(msg)

                async with BleakClient(
                    self._device_address,
                    disconnected_callback=on_disconnect,
                    timeout=15.0,
                ) as client:
                    self.status.emit("Connected -- starting HR notifications (required first)...")
                    self.connected.emit(True)
                    retry_count = 0
                    disconnected_event.clear()
                    streams_confirmed = False

                    ecg_q = asyncio.Queue()
                    acc_q = asyncio.Queue()
                    hr_q  = asyncio.Queue()

                    heartrate = bh.HeartRate(
                        client, queue=hr_q, unpack=True, instant_rate=True
                    )
                    pmd = bh.PolarMeasurementData(
                        client, ecg_queue=ecg_q, acc_queue=acc_q
                    )

                    # CRITICAL: Polar H10 requires HR notifications BEFORE PMD
                    await heartrate.start_notify()
                    self.status.emit(
                        f"Waiting for first HR packet (up to {HR_FIRST_PACKET_TIMEOUT_S:.0f}s)..."
                    )

                    try:
                        pkt = await asyncio.wait_for(
                            hr_q.get(), timeout=HR_FIRST_PACKET_TIMEOUT_S
                        )
                        _, _ts, (hr, _rr), _ = pkt
                        self.data_bus.add_hr(hr)
                        self.status.emit(
                            f"HR stream active: {hr:.0f} BPM  --  unlocking PMD..."
                        )
                    except asyncio.TimeoutError:
                        self.status.emit("HR timeout -- proceeding anyway (PMD may not unlock)")

                    # MTU negotiation
                    try:
                        if hasattr(client, "exchange_mtu"):
                            await client.exchange_mtu(247)
                    except Exception:
                        pass

                    meas = await pmd.available_measurements()
                    self.status.emit(f"PMD measurements available: {list(meas)}")

                    # Prime control channel (stabilises subsequent commands)
                    prime = "ECG" if "ECG" in meas else ("ACC" if "ACC" in meas else None)
                    if prime:
                        await pmd.available_settings(prime)

                    if "ECG" in meas:
                        await pmd.start_streaming("ECG")
                        self.status.emit("ECG stream starting...")
                    if "ACC" in meas:
                        await pmd.start_streaming("ACC", SAMPLE_RATE=100)
                        self.status.emit("ACC stream starting...")

                    # --- Drain loop ---
                    while self._running and not disconnected_event.is_set():

                        # ECG
                        while not ecg_q.empty():
                            try:
                                _, _, samples = ecg_q.get_nowait()
                                self.data_bus.add_ecg(samples)
                            except asyncio.QueueEmpty:
                                break

                        # ACC
                        while not acc_q.empty():
                            try:
                                _, _, samples = acc_q.get_nowait()
                                self.data_bus.add_acc(samples)
                            except asyncio.QueueEmpty:
                                break

                        # HR
                        while not hr_q.empty():
                            try:
                                _, _, (hr, _), _ = hr_q.get_nowait()
                                self.data_bus.add_hr(hr)
                            except asyncio.QueueEmpty:
                                break

                        # Confirm streams once
                        if not streams_confirmed and self.data_bus.all_streams_active:
                            streams_confirmed = True
                            self.status.emit("All streams confirmed and flowing -- ready!")
                            self.streams_ready.emit()

                        await asyncio.sleep(0.01)

                    # Cleanup on disconnect / stop
                    try:
                        await heartrate.stop_notify()
                        if "ECG" in meas:
                            await pmd.stop_streaming("ECG")
                        if "ACC" in meas:
                            await pmd.stop_streaming("ACC")
                    except Exception:
                        pass

            except Exception as e:
                retry_count += 1
                self.connected.emit(False)
                self.status.emit(f"Connection error: {e}")
                if self._running:
                    await asyncio.sleep(min(2 ** retry_count, 10))

        if retry_count > 5:
            self.status.emit("Max reconnection attempts reached. Please restart.")


class BLEManager(QObject):
    """
    High-level BLE manager for Polar H10.

    Data goes to PolarDataBus (not Qt signals -- no nullptr signal bug).
    Control signals: device_found, connected, status, streams_ready.
    """

    device_found  = pyqtSignal(str, str)
    connected     = pyqtSignal(bool)
    status        = pyqtSignal(str)
    streams_ready = pyqtSignal()

    def __init__(self, data_bus: PolarDataBus, parent=None):
        super().__init__(parent)
        self.data_bus      = data_bus
        self._worker       = None
        self._thread       = None
        self._scan_worker  = None
        self._scan_thread  = None

    def scan(self):
        """Scan for nearby Polar devices. Emits device_found(name, address)."""
        if self._scan_thread and self._scan_thread.isRunning():
            return
        self._scan_worker = _BLEWorker(self.data_bus)
        self._scan_worker.device_found.connect(self.device_found.emit)
        self._scan_worker.status.connect(self.status.emit)

        self._scan_thread = QThread()
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run_scan)
        self._scan_thread.start()

    def connect_device(self, address: str):
        """Connect to a specific device and start streaming data into data_bus."""
        if self._thread and self._thread.isRunning():
            self.status.emit("Already streaming")
            return

        # Stop scan thread first
        if self._scan_thread and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(2000)

        self._worker = _BLEWorker(self.data_bus)
        self._worker.set_device_address(address)
        self._worker.status.connect(self.status.emit)
        self._worker.connected.connect(self.connected.emit)
        self._worker.streams_ready.connect(self.streams_ready.emit)

        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def disconnect(self):
        """Stop streaming and disconnect."""
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
        self._worker = None
        self._thread = None
        if self._scan_thread:
            self._scan_thread.quit()
            self._scan_thread.wait(1000)
        self._scan_worker = None
        self._scan_thread = None

    @property
    def is_connected(self) -> bool:
        return self._thread is not None and self._thread.isRunning()
