"""Shared test fixtures."""
import sys
import os
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set required env vars before any imports that need them
os.environ.setdefault('DB_HOST', 'test-host')
os.environ.setdefault('DB_NAME', 'test-db')
os.environ.setdefault('DB_USER', 'test-user')
os.environ.setdefault('SSH_TUNNEL_DISABLED', 'true')
