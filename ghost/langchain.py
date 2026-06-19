"""LangChain bindings for GHOST layer.

Provides a GhostTool wrapper that intercepts LangChain tool execution,
and logs the action to the Ghost Residue store.
"""

from __future__ import annotations

from typing import Any, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel

import ghost

__all__ = ["GhostTool"]


class GhostTool(BaseTool):
    """Wraps an existing LangChain BaseTool in a GHOST cryptographic session.

    When the tool is invoked, GhostTool intercepts the call, fires `ghost.act()`,
    records the parameters and output in the local SQLite residue store, and
    attaches an Ed25519 cryptographic signature.
    """

    name: str = "ghost_tool_wrapper"
    description: str = "A GHOST-wrapped tool that cryptographically signs actions."
    wrapped_tool: BaseTool
    ghost_store: Any
    ghost_session_id: str
    args_schema: Optional[Type[BaseModel]] = None

    def __init__(
        self,
        wrapped_tool: BaseTool,
        ghost_store: Any,
        ghost_session_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name=wrapped_tool.name,
            description=wrapped_tool.description,
            wrapped_tool=wrapped_tool,
            ghost_store=ghost_store,
            ghost_session_id=ghost_session_id,
            args_schema=wrapped_tool.args_schema,
            **kwargs,
        )

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """Run the tool and log the execution via ghost.act()."""
        # Convert args/kwargs into a dictionary for ghost.act payload
        params: dict[str, Any] = {"args": args, "kwargs": kwargs}

        try:
            # 1. Execute the actual underlying LangChain tool
            result = self.wrapped_tool._run(*args, **kwargs)
            params["result"] = str(result)

            # 2. Cryptographically sign the action
            ghost.act(
                store=self.ghost_store,
                session_id=self.ghost_session_id,
                tool=self.wrapped_tool.name,
                action="langchain_invoke",
                params=params,
                enforce_scope=True,
            )
            return result
        except Exception as exc:
            params["error"] = str(exc)
            ghost.act(
                store=self.ghost_store,
                session_id=self.ghost_session_id,
                tool=self.wrapped_tool.name,
                action="langchain_error",
                params=params,
                enforce_scope=True,
            )
            raise
