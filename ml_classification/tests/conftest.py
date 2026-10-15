import os
import sys
from pathlib import Path

# enhanced_analytics refuses to import without DATABASE_URL. The tests below
# never open a connection, so a placeholder value is enough.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

# Make the service modules importable as top-level modules, as in the Dockerfile.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
