"""Safety layer tests — tools/safety.py and the mirrored policy in
iris_node/main.py must agree: a command blocked on the server must also be
blocked at the node, or remote nodes become a bypass."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.safety import SafetyChecker, is_safe

NODE_BLOCKED = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+/\*",
    r"del\s+/f\s+/q\s+[A-Z]:",
    r"format\s+[A-Z]:",
    r"shutdown",
    r"rmdir\s+/s",
    r"dd\s+if=.*of=/dev/",
    r":()\{\s*:\|:&\s*\};:",
    r"mkfs\.",
    r"reg\s+delete\s+/f\s+HKLM",
    r"diskpart",
    r"cipher\s+/w",
]


class TestServerSafety:
    def test_blocks_rm_rf_root(self):
        assert not is_safe("rm -rf /")

    def test_blocks_format(self):
        assert not is_safe("format C:")

    def test_blocks_shutdown(self):
        assert not is_safe("shutdown /s /t 0")

    def test_blocks_fork_bomb(self):
        assert not is_safe(":(){ :|:& };:")

    def test_allows_harmless(self):
        assert is_safe("dir C:\\Users")
        assert is_safe("echo hello")

    def test_returns_reason(self):
        safe, reason = SafetyChecker.is_command_safe("mkfs.ext4 /dev/sda1")
        assert not safe
        assert "mkfs" in reason


class TestNodeSafetyParity:
    """The node policy is a superset copy — verify parity by importing the
    node module's pattern list directly (fastapi is a dependency, so skip
    gracefully if the node env isn't installed here)."""

    def _node_patterns(self):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "iris_node_main",
                Path(__file__).resolve().parent.parent / "iris_node" / "main.py",
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return list(mod.BLOCKED_PATTERNS)
        except ImportError:
            pytest.skip("fastapi not installed — node module can't be imported")

    def test_node_patterns_cover_server_patterns(self):
        from tools.safety import BLOCKED_PATTERNS
        node_patterns = set(self._node_patterns())
        for server_pattern in BLOCKED_PATTERNS:
            assert server_pattern in node_patterns, (
                f"Server blocks '{server_pattern}' but iris_node does NOT — "
                "remote nodes can be used to bypass server-side safety!"
            )

    def test_node_blocks_fork_bomb(self):
        import re
        for pattern in self._node_patterns():
            if re.search(pattern, ":(){ :|:& };:"):
                return
        pytest.fail("No node pattern matches the fork bomb — the old "
                    "unescaped-parens bug pattern must not return.")
