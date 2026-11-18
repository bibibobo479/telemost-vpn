import tempfile
import unittest
from pathlib import Path
import supervisor


class RoomFileRegression(unittest.TestCase):
    def test_creator_append_does_not_become_one_url(self):
        first = 'https://telemost.yandex.ru/j/123456'
        second = 'https://telemost.yandex.ru/j/789012'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'call.txt'
            path.write_text(first + '\n' + second + '\n')
            self.assertFalse(supervisor.valid_link(path.read_text().strip()))
            self.assertEqual(supervisor.saved_link(path), second)

    def test_only_telemost_https_links_are_accepted(self):
        for value in ('http://telemost.yandex.ru/j/123', 'https://evil.example/j/123',
                      'https://telemost.yandex.ru/j/1\n2', None):
            self.assertFalse(supervisor.valid_link(value))


if __name__ == '__main__':
    unittest.main()
