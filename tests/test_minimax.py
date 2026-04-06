"""Unit tests for MiniMax provider support in agent.py."""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Stub out heavy third-party imports before agent.py is loaded
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)
    return mod


for _mod in [
    "agents",
    "agents.items",
    "agents.tool",
    "agents.usage",
    "harbor",
    "harbor.agents",
    "harbor.agents.base",
    "harbor.environments",
    "harbor.environments.base",
    "harbor.models",
    "harbor.models.agent",
    "harbor.models.agent.context",
]:
    _make_stub(_mod)

_mock_set_client = MagicMock(name="set_default_openai_client")

sys.modules["agents"].Agent = MagicMock()
sys.modules["agents"].Runner = MagicMock()
sys.modules["agents"].function_tool = lambda f: f
sys.modules["agents"].set_default_openai_client = _mock_set_client
sys.modules["agents.items"].ItemHelpers = MagicMock()
sys.modules["agents.items"].MessageOutputItem = MagicMock()
sys.modules["agents.items"].ReasoningItem = MagicMock()
sys.modules["agents.items"].ToolCallItem = MagicMock()
sys.modules["agents.items"].ToolCallOutputItem = MagicMock()
sys.modules["agents.tool"].FunctionTool = MagicMock()
sys.modules["agents.usage"].Usage = MagicMock()
sys.modules["harbor.agents.base"].BaseAgent = object
sys.modules["harbor.environments.base"].BaseEnvironment = MagicMock()
sys.modules["harbor.models.agent.context"].AgentContext = MagicMock()

# Import agent once (MODEL defaults to "gpt-5", so _configure_provider is a no-op)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "agent",
    os.path.join(os.path.dirname(__file__), "..", "agent.py"),
)
_agent_module = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["agent"] = _agent_module
_spec.loader.exec_module(_agent_module)  # type: ignore[union-attr]

import agent  # noqa: E402  (now safely loaded above)


class TestConfigureProvider(unittest.TestCase):
    """Tests for agent._configure_provider()."""

    def setUp(self):
        _mock_set_client.reset_mock()

    def _run(self, model: str, env: dict | None = None):
        """Patch agent.MODEL and env, then call _configure_provider()."""
        patch_env = {k: v for k, v in os.environ.items()}
        patch_env.pop("MINIMAX_API_KEY", None)
        patch_env.pop("MINIMAX_BASE_URL", None)
        if env:
            patch_env.update(env)
        with patch.object(agent, "MODEL", model):
            with patch.dict(os.environ, patch_env, clear=True):
                agent._configure_provider()

    # ------------------------------------------------------------------
    # Non-MiniMax models: no custom client should be configured
    # ------------------------------------------------------------------

    def test_openai_model_no_custom_client(self):
        self._run("gpt-4o")
        _mock_set_client.assert_not_called()

    def test_gpt5_no_custom_client(self):
        self._run("gpt-5")
        _mock_set_client.assert_not_called()

    # ------------------------------------------------------------------
    # MiniMax model with MINIMAX_API_KEY set
    # ------------------------------------------------------------------

    def test_minimax_m27_sets_client(self):
        self._run("MiniMax-M2.7", env={"MINIMAX_API_KEY": "test-key"})
        _mock_set_client.assert_called_once()

    def test_minimax_m27_highspeed_sets_client(self):
        self._run("MiniMax-M2.7-highspeed", env={"MINIMAX_API_KEY": "test-key"})
        _mock_set_client.assert_called_once()

    def test_minimax_case_insensitive(self):
        """Model matching is case-insensitive."""
        self._run("minimax-m2.7", env={"MINIMAX_API_KEY": "test-key"})
        _mock_set_client.assert_called_once()

    def test_minimax_default_base_url(self):
        """Client base_url should point to api.minimax.io by default."""
        self._run("MiniMax-M2.7", env={"MINIMAX_API_KEY": "test-key"})
        client = _mock_set_client.call_args[0][0]
        self.assertIn("api.minimax.io", str(client.base_url))

    def test_minimax_custom_base_url(self):
        """MINIMAX_BASE_URL env var overrides the default base URL."""
        custom = "https://custom.example.com/v1"
        self._run(
            "MiniMax-M2.7",
            env={"MINIMAX_API_KEY": "test-key", "MINIMAX_BASE_URL": custom},
        )
        client = _mock_set_client.call_args[0][0]
        self.assertIn("custom.example.com", str(client.base_url))

    # ------------------------------------------------------------------
    # MiniMax model WITHOUT MINIMAX_API_KEY must raise ValueError
    # ------------------------------------------------------------------

    def test_minimax_missing_api_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._run("MiniMax-M2.7")  # no env → no MINIMAX_API_KEY
        self.assertIn("MINIMAX_API_KEY", str(ctx.exception))


class TestMinimaxE2E(unittest.TestCase):
    """Integration test calling the real MiniMax API.

    Runs only when MINIMAX_API_KEY is present in the environment.
    """

    API_KEY = os.environ.get("MINIMAX_API_KEY")
    BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1")

    @unittest.skipUnless(
        os.environ.get("MINIMAX_API_KEY"), "MINIMAX_API_KEY not set"
    )
    def test_basic_chat_completion(self):
        """MiniMax-M2.7 should return a non-empty chat response."""
        import json as _json
        import urllib.request

        payload = _json.dumps(
            {
                "model": "MiniMax-M2.7",
                "messages": [{"role": "user", "content": 'Say "test passed"'}],
                "max_tokens": 20,
                "temperature": 1.0,
            }
        ).encode()

        req = urllib.request.Request(
            f"{self.BASE_URL}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.API_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())

        self.assertIn("choices", data)
        self.assertTrue(data["choices"][0]["message"]["content"])


if __name__ == "__main__":
    unittest.main()
