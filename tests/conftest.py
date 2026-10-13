"""Keep the test suite out of the user's real data folder (settings, log)."""
import os
import tempfile

os.environ["LIDFOLD_HOME"] = tempfile.mkdtemp(prefix="lidfold-tests-")
