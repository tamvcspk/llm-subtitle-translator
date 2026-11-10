import importlib.metadata
import unittest

import subcore


class TestPackage(unittest.TestCase):
    def test_installed_as_workspace_member(self):
        self.assertEqual(subcore.__version__, importlib.metadata.version("subcore"))


if __name__ == "__main__":
    unittest.main()
