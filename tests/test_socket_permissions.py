import errno
import unittest
from unittest.mock import patch

from breakout.cli import main
from breakout.daemon import request


class SocketPermissionTests(unittest.TestCase):
    def test_denied_connect_explains_access_and_preserves_errno(self):
        with patch('breakout.daemon.socket.socket') as socket:
            socket.return_value.__enter__.return_value.connect.side_effect = PermissionError(errno.EPERM, 'Operation not permitted')
            with self.assertRaises(PermissionError) as caught:
                request('ping', home='/tmp/breakout-permission-test')
        self.assertEqual(caught.exception.errno, errno.EPERM)
        self.assertIn('session sandbox permissions', str(caught.exception))
        self.assertIn('/tmp/breakout-permission-test/daemon.sock', str(caught.exception))

    def test_start_does_not_spawn_on_denied_access(self):
        with patch('breakout.cli.request', side_effect=PermissionError(errno.EPERM, 'Access denied')):
            with patch('breakout.cli.subprocess.Popen') as spawn:
                with patch('sys.stderr'):
                    with self.assertRaises(SystemExit) as caught:
                        main(['daemon', 'start'])
                self.assertEqual(caught.exception.code, 1)
                spawn.assert_not_called()
