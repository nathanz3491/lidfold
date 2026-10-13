"""Camera-driven fold-away effect for Windows laptops when the lid closes."""
import os

# NumPy's OpenBLAS reserves a buffer per CPU thread at import: ~480 MB of
# private memory on a 16-thread laptop, in every lidfold process. lidfold's
# matrices are tiny (single-threaded BLAS measured slightly faster), so one
# thread is enough. Must run before anything imports numpy.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

__version__ = "0.1.0"
