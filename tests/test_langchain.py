"""Tests for the GhostTool LangChain wrapper."""

import pytest
try:
    from langchain_core.tools import BaseTool
    from ghost.langchain import GhostTool
    import ghost
    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False

from ghost.store import ResidueStore

class DummyTool(BaseTool):
    name: str = "dummy_tool"
    description: str = "A dummy tool for testing."
    
    def _run(self, text: str) -> str:
        if text == "fail":
            raise ValueError("Intentional failure")
        return f"Echo: {text}"

@pytest.mark.skipif(not HAS_LANGCHAIN, reason="langchain-core not installed")
def test_ghost_tool_wrapper(tmp_path):
    # Set up Ghost session
    db_path = tmp_path / "residue.db"
    store = ResidueStore(str(db_path))
    session = ghost.spawn(store, intent="langchain_test", ttl=300, scopes=["dummy_tool"])
    
    # Create Tool
    base_tool = DummyTool()
    ghost_tool = GhostTool(
        wrapped_tool=base_tool,
        ghost_store=store,
        ghost_session_id=session["session_id"]
    )
    
    # Run Tool successfully
    res = ghost_tool.run("hello")
    assert res == "Echo: hello"
    
    # Verify residue exists
    actions = store.actions_for(session["session_id"])
    assert len(actions) == 1
    assert actions[0]["tool"] == "dummy_tool"
    assert actions[0]["action"] == "langchain_invoke"
    
    # Run Tool failure
    with pytest.raises(ValueError):
        ghost_tool.run("fail")
        
    actions = store.actions_for(session["session_id"])
    assert len(actions) == 2
    assert actions[1]["action"] == "langchain_error"
    
    ghost.evaporate(store, session["session_id"])
    replay = ghost.replay(store, session["session_id"])
    assert replay["verified"] is True
