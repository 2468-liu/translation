"""Unit tests for Vulkan GPU discovery and Nemotron state reset."""

import ctypes
import unittest
from unittest.mock import MagicMock, patch

from server.asr.nemotron import NemotronAsrEngine, _detect_best_vulkan_gpu


class NemotronGpuTests(unittest.TestCase):
    def test_vulkan_gpu_prioritizes_discrete_nvidia_over_integrated(self):
        # Mock vkCreateInstance, vkEnumeratePhysicalDevices, vkGetPhysicalDeviceProperties
        class MockVkPhysicalDeviceProperties(ctypes.Structure):
            _fields_ = [
                ("apiVersion", ctypes.c_uint32),
                ("driverVersion", ctypes.c_uint32),
                ("vendorID", ctypes.c_uint32),
                ("deviceID", ctypes.c_uint32),
                ("deviceType", ctypes.c_uint32),
                ("deviceName", ctypes.c_char * 256),
                ("pipelineCacheUUID", ctypes.c_uint8 * 16),
            ]

        # Device 0: Intel Iris Xe (Integrated, deviceType=1, vendor=0x8086)
        # Device 1: NVIDIA RTX 3050 Ti (Discrete, deviceType=2, vendor=0x10DE)
        props0 = MockVkPhysicalDeviceProperties(
            vendorID=0x8086,
            deviceType=1,
            deviceName=b"Intel(R) Iris(R) Xe Graphics",
        )
        props1 = MockVkPhysicalDeviceProperties(
            vendorID=0x10DE,
            deviceType=2,
            deviceName=b"NVIDIA GeForce RTX 3050 Ti Laptop GPU",
        )

        mock_vulkan = MagicMock()
        mock_vulkan.vkCreateInstance.return_value = 0
        mock_vulkan.vkDestroyInstance.return_value = None

        def mock_enum_devices(instance, count_ptr, dev_array):
            if not dev_array:
                count_ptr._obj.value = 2
            else:
                dev_array[0] = ctypes.c_void_p(100)
                dev_array[1] = ctypes.c_void_p(200)
            return 0

        mock_vulkan.vkEnumeratePhysicalDevices.side_effect = mock_enum_devices

        def mock_get_props(dev, props_ptr):
            if dev == 100:
                ctypes.memmove(props_ptr, ctypes.byref(props0), ctypes.sizeof(props0))
            else:
                ctypes.memmove(props_ptr, ctypes.byref(props1), ctypes.sizeof(props1))

        mock_vulkan.vkGetPhysicalDeviceProperties.side_effect = mock_get_props

        with patch("ctypes.CDLL", return_value=mock_vulkan):
            best_idx = _detect_best_vulkan_gpu()
            self.assertEqual(best_idx, 1, "Discrete NVIDIA GPU must be selected over integrated Intel GPU")

    def test_nemotron_engine_reset_state(self):
        engine = NemotronAsrEngine.__new__(NemotronAsrEngine)
        engine._is_init = False
        engine._c_engine = None
        # reset_state should succeed without exception
        engine.reset_state()
        self.assertFalse(engine._is_init)


if __name__ == "__main__":
    unittest.main()
