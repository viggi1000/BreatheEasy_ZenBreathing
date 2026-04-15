"""
BLE data acquisition worker.
Runs in a dedicated QThread and streams ECG/ACC/HR data from Polar H10
via bleakheart, or from a MockPolarH10 for testing.
"""
import asyncio
import time

from PyQt5.QtCore import QObject, pyqtSignal

from breathe_easy.utils.constants import ECG_NATIVE_HZ, ACC_HZ
from breathe_easy.utils.mock_sensor import MockPolarH10
from breathe_easy.data_bus import PolarDataBus

HR_FIRST_PACKET_TIMEOUT_S = 25.0
PMD_FIRST_FRAME_TIMEOUT_ECG_S = 8.0
PMD_FIRST_FRAME_TIMEOUT_ACC_S = 7.0
PMD_STREAM_ATTEMPTS = 4

class BLEWorker(QObject):
    status = pyqtSignal(str)
    connected = pyqtSignal(bool)
    device_found = pyqtSignal(str, str)

    def __init__(self, data_bus: PolarDataBus, use_mock: bool = False):
        super().__init__()
        self.data_bus = data_bus
        self._use_mock = use_mock
        self._running = False
        self._device_address = None
        self._loop = None

    def set_device_address(self, address: str):
        self._device_address = address

    def stop(self):
        self._running = False

    def run_scan(self):
        if self._use_mock:
            self.device_found.emit("Mock Polar H10", "00:00:00:00:00:00")
            return
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._async_scan())
            loop.close()
        except Exception as e:
            self.status.emit(f"Scan error: {e}")

    async def _async_scan(self):
        from bleak import BleakScanner
        self.status.emit("Scanning for Polar devices...")
        devices = await BleakScanner.discover(timeout=8.0)
        found = False
        for d in devices:
            name = d.name or ""
            if "polar" in name.lower():
                self.device_found.emit(name, d.address)
                found = True
        if not found:
            self.status.emit("No Polar devices found.")

    def run(self):
        self._running = True
        if self._use_mock:
            self._run_mock()
        else:
            self._run_ble()

    def _run_mock(self):
        self.status.emit("Mock sensor started")
        self.connected.emit(True)
        sensor = MockPolarH10()

        ecg_interval = 73.0 / ECG_NATIVE_HZ
        acc_interval = 16.0 / ACC_HZ
        hr_interval = 1.0

        last_ecg = time.time()
        last_acc = time.time()
        last_hr = time.time()

        while self._running:
            now = time.time()
            if now - last_ecg >= ecg_interval:
                _, _, samples = sensor.get_ecg_frame()
                self.data_bus.add_ecg(samples)
                last_ecg = now

            if now - last_acc >= acc_interval:
                _, _, samples = sensor.get_acc_frame()
                self.data_bus.add_acc(samples)
                last_acc = now
                
            if now - last_hr >= hr_interval:
                _, _, (hr, rr), _ = sensor.get_hr_frame()
                self.data_bus.add_hr(hr)
                last_hr = now

            time.sleep(0.005)

        self.connected.emit(False)
        self.status.emit("Mock sensor stopped")

    def _run_ble(self):
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._async_stream())
        except Exception as e:
            self.status.emit(f"BLE error: {e}")
            self.connected.emit(False)
        finally:
            if self._loop and not self._loop.is_closed():
                self._loop.close()

    async def _wait_first_hr_packet(self, hr_queue: asyncio.Queue, timeout: float) -> bool:
        """Block until the first HR notification or timeout. Essential for PMD unlocking on Polar!"""
        try:
            pkt = await asyncio.wait_for(hr_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        _tag, ts, hr_rr, _energy = pkt
        hr, rr = hr_rr
        self.data_bus.add_hr(hr)
        return True

    async def _async_stream(self):
        import bleakheart as bh
        from bleak import BleakClient

        if not self._device_address:
            self.status.emit("No device address set")
            return

        disconnected_event = asyncio.Event()
        def on_disconnect(client):
            self.status.emit("Device disconnected")
            self.connected.emit(False)
            disconnected_event.set()

        retry_count = 0
        max_retries = 5

        while self._running and retry_count <= max_retries:
            try:
                self.status.emit(f"Connecting to {self._device_address}..." + (f" (retry {retry_count})" if retry_count > 0 else ""))
                
                async with BleakClient(self._device_address, disconnected_callback=on_disconnect, timeout=15.0) as client:
                    self.status.emit("Connected. Starting HR to unblock streams...")
                    self.connected.emit(True)
                    retry_count = 0
                    disconnected_event.clear()

                    ecg_q, acc_q, hr_q = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
                    
                    heartrate = bh.HeartRate(client, queue=hr_q, unpack=True, instant_rate=True)
                    pmd = bh.PolarMeasurementData(client, ecg_queue=ecg_q, acc_queue=acc_q)

                    # Start notifications on heartrate characteristic BEFORE querying PMD capabilities.
                    await heartrate.start_notify()
                    got_hr = await self._wait_first_hr_packet(hr_q, HR_FIRST_PACKET_TIMEOUT_S)
                    
                    if not got_hr:
                        self.status.emit("No HR packet yet. Proceeding with PMD anyway...")

                    try:
                        exch = getattr(client, "exchange_mtu", None)
                        if callable(exch):
                            await exch(247)
                    except:
                        pass

                    meas = await pmd.available_measurements()
                    self.status.emit(f"Hardware measurements unlocked: {meas}")

                    # Prime the control channel
                    prime = "ECG" if "ECG" in meas else ("ACC" if "ACC" in meas else None)
                    if prime:
                        await pmd.available_settings(prime)

                    # START Streams safely
                    if "ECG" in meas:
                        await pmd.start_streaming("ECG")
                    if "ACC" in meas:
                        await pmd.start_streaming("ACC", SAMPLE_RATE=100)

                    self.status.emit("Live Streams established.")

                    while self._running and not disconnected_event.is_set():
                        # Drain ECG Queue
                        while not ecg_q.empty():
                            try:
                                _, _, samples = ecg_q.get_nowait()
                                self.data_bus.add_ecg(samples)
                            except asyncio.QueueEmpty:
                                break
                        # Drain ACC Queue
                        while not acc_q.empty():
                            try:
                                _, _, samples = acc_q.get_nowait()
                                self.data_bus.add_acc(samples)
                            except asyncio.QueueEmpty:
                                break
                        # Drain HR Queue
                        while not hr_q.empty():
                            try:
                                _, _, (hr, _), _ = hr_q.get_nowait()
                                self.data_bus.add_hr(hr)
                            except asyncio.QueueEmpty:
                                break
                                
                        await asyncio.sleep(0.01)

                    try:
                        await heartrate.stop_notify()
                        await pmd.stop_streaming("ECG")
                        await pmd.stop_streaming("ACC")
                    except Exception:
                        pass

            except Exception as e:
                retry_count += 1
                self.connected.emit(False)
                if self._running:
                    await asyncio.sleep(min(2**retry_count, 10))

        if retry_count > max_retries:
            self.status.emit("Max reconnection attempts reached")
