"""Test configuration.

Point the store at a throwaway DB and force offline AI so the suite never
touches AWS or the network. Must run before any ``gpumon`` import.
"""

import os
import tempfile

os.environ.setdefault("ARGUS_MODE", "local")
os.environ.setdefault("ARGUS_OFFLINE_AI", "1")
os.environ.setdefault("ARGUS_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
# Lower the warm-up so tests don't need hundreds of samples.
os.environ.setdefault("ARGUS_MIN_SAMPLES", "10")
