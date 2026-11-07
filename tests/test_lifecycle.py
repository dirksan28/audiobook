import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import tts_audiobook as tts


class ContainerCleanupTests(unittest.TestCase):
    def setUp(self):
        tts._CONTAINER_CLEANUP_NAME = None
        tts._CONTAINER_CLEANUP_DONE = False

    def tearDown(self):
        tts._CONTAINER_CLEANUP_NAME = None
        tts._CONTAINER_CLEANUP_DONE = False

    @mock.patch.object(tts, "_stop_container")
    def test_registered_container_is_stopped_once(self, stop_container):
        tts._register_container_cleanup("test-container")
        tts._cleanup_container()
        tts._cleanup_container()
        stop_container.assert_called_once_with("test-container")

    @mock.patch.object(tts, "_stop_container")
    def test_cleanup_without_container_does_nothing(self, stop_container):
        tts._cleanup_container()
        stop_container.assert_not_called()

    def test_termination_signal_exits_with_128_plus_signal(self):
        with self.assertRaises(SystemExit) as raised:
            tts._handle_termination(15, None)
        self.assertEqual(raised.exception.code, 143)

    @mock.patch.object(tts, "_docker_cmd")
    @mock.patch.object(tts, "_inspect_container", return_value={"State": {"Running": False}})
    def test_stop_skips_already_stopped_container(self, inspect_container, docker_cmd):
        tts._stop_container("stopped-container")
        inspect_container.assert_called_once_with("stopped-container")
        docker_cmd.assert_not_called()

    @mock.patch.object(tts, "_docker_cmd")
    @mock.patch.object(tts, "_inspect_container", return_value={"State": {"Running": True}})
    def test_stop_running_container(self, inspect_container, docker_cmd):
        with redirect_stdout(io.StringIO()):
            tts._stop_container("running-container")
        inspect_container.assert_called_once_with("running-container")
        docker_cmd.assert_called_once_with(["stop", "running-container"], timeout=60)


if __name__ == "__main__":
    unittest.main()
