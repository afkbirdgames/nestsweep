"""Read-only smoke tests: every scan runs against the real machine and every bridge result
serialises to JSON. Nothing here changes the system. Windows Update searches are stubbed
because they can take minutes on a fresh machine."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nestsweep import api, driver_updater as du, windows_update  # noqa: E402


class ScanSmokeTest(unittest.TestCase):
    def setUp(self):
        self.api = api.Api()
        patcher = mock.patch.object(windows_update, "search", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, name, *args):
        res = getattr(self.api, name)(*args)
        json.dumps(res)
        self.assertTrue(res["ok"], f"{name}: {res.get('kind')}: {res.get('error')}")
        return res["data"]

    def test_read_only_endpoints(self):
        self.call("app_info")
        self.call("overview")
        self.assertTrue(self.call("cc_rules"))
        self.assertIsInstance(self.call("un_scan"), list)
        self.assertIsInstance(self.call("sm_scan"), list)
        self.assertIsInstance(self.call("po_scan"), list)
        self.assertIsInstance(self.call("du_scan"), list)
        self.assertIn("items", self.call("su_scan", True))

    def test_device_listing_builds_devices(self):
        devs = du.scan_devices()
        self.assertTrue(devs, "expected at least one device with a driver")
        self.assertIsInstance(devs[0], du.Device)


class DriverOfferTest(unittest.TestCase):
    def _dev(self, date):
        # The installed driver's hardware ID first, then every hardware ID Windows lists for the device.
        return du.Device("PCI\\X\\1", "GPU", "Display", "Vendor", "1.0", date, "oem1.inf",
                         ["PCI\\VEN_1&DEV_2&SUBSYS_3", "PCI\\VEN_1&DEV_2"])

    def test_only_newer_drivers_are_offered(self):
        dev = self._dev("2025-06-01")
        older = {"Id": "a", "DriverHardwareID": "PCI\\VEN_1&DEV_2", "DriverVerDate": "2024-01-01"}
        newer = {"Id": "b", "DriverHardwareID": "PCI\\VEN_1&DEV_2", "DriverVerDate": "2026-01-01"}
        other = {"Id": "c", "DriverHardwareID": "PCI\\VEN_1&DEV_22", "DriverVerDate": "2027-01-01"}
        with mock.patch.object(du, "scan_devices", return_value=[dev]), \
                mock.patch.object(windows_update, "search", return_value=[older, other, newer]):
            got = du.scan()
        self.assertEqual(got[0].update["Id"], "b")

    def test_downgrade_is_not_offered(self):
        dev = self._dev("2026-06-01")
        older = {"Id": "a", "DriverHardwareID": "PCI\\VEN_1&DEV_2", "DriverVerDate": "2025-01-01"}
        with mock.patch.object(du, "scan_devices", return_value=[dev]), \
                mock.patch.object(windows_update, "search", return_value=[older]):
            self.assertIsNone(du.scan()[0].update)


if __name__ == "__main__":
    unittest.main()
