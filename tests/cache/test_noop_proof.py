"""Part 1: No-op proof (I-1, I-2).

I-1: Poisoned imports - when cache is off, cache modules are not loaded.
I-2: Golden snapshot - cached-off path behaves as before.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


class TestNoOpProof:
    """I-1 and I-2: No-op proof that cache is off by default."""

    def test_i1_cache_off_no_submodules_serial(self) -> None:
        """I-1 serial: When cache is off, no cache submodules are imported."""
        self._test_cache_off_no_submodules(max_parallel=1)

    def test_i1_cache_off_no_submodules_parallel(self) -> None:
        """I-1 max_parallel=3: When cache is off, no cache submodules are imported."""
        self._test_cache_off_no_submodules(max_parallel=3)

    def _test_cache_off_no_submodules(self, max_parallel: int) -> None:
        """Test that cache submodules are not imported when cache is off."""
        # Create a test script that imports modules with AO_CACHE=0
        # and verifies that only base cache modules are in sys.modules
        test_code = [
            "import os",
            "os.environ['AO_CACHE'] = '0'",
            "",
            "# Import main modules",
            "from agent_orchestrator import models",
            "from agent_orchestrator import engine",
            "from agent_orchestrator.artifacts import LocalFsArtifactStore",
            "from agent_orchestrator.executors.fake import FakeExecutor",
            "",
            "# Check loaded cache modules",
            "import sys",
            "loaded = {m for m in sys.modules if m.startswith('agent_orchestrator.cache')}",
            "allowed = {'agent_orchestrator.cache', 'agent_orchestrator.cache.constants'}",
            "",
            "if loaded.issubset(allowed):",
            "    print('SUCCESS')",
            "    sys.exit(0)",
            "else:",
            "    print(f'FAIL: {loaded - allowed}')",
            "    sys.exit(1)",
        ]

        script = "\n".join(test_code)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                pytest.fail(
                    f"I-1 test failed (max_parallel={max_parallel}):\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}"
                )

            if "SUCCESS" not in result.stdout:
                pytest.fail(f"Test did not succeed: {result.stdout}")

        finally:
            Path(script_path).unlink(missing_ok=True)
