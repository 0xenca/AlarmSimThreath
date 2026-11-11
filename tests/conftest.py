import os
import sys
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SECRET_KEY"] = "test"
os.environ["TEST_RECEIVER_PORT"] = "0"
os.environ["ALARMSIM_ENV_FILE"] = os.path.join(_tmp, "none.env")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
