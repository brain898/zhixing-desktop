import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

# 导入统一定义在 test_auth_and_permissions 中的测试套件
from test_auth_and_permissions import TestAuthAndPermissions

if __name__ == "__main__":
    unittest.main(verbosity=2)
