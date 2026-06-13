"""GHOST command-line interface.

Give your AI agent a body that vanishes. Thin Click layer over ghost.session.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import click

from . import __version__
from . import session as _session
from .gateway import GhostGateway
from .session import (
    ExpiredSessionError,
    ScopeError,
    SessionError,
)
from .store import ResidueStore

GREEN = "green"
GOLD = "yellow"
PINK = "magenta"
CYAN = "cyan"
RED = "red"


def _emit(obj: dict, color: str) -> None:
    click.secho(json.dumps(obj, indent=2), fg=color)


@click.group()
@click.version_option(version=__version__, prog_name="ghost")
@click.option("--db", type=click.Path(), default=None, help="Override residue DB path.")
@click.pass_context
def cli(ctx: click.Context, db: Optional[str]) -> None:
    """GHOST — Ephemeral Execution Layer for Autonomous Agents.

    Declare intent -> spawn -> execute -> evaporate -> leave signed residue.
    """
    ctx.ensure_object(dict)
    ctx.obj["store"] = ResidueStore(db)


@cli.command()
@click.option("--intent", required=True, help="Human-readable intent, e.g. 'deploy_staging'.")
@click.option("--ttl", default=300, show_default=True, help="Session lifetime in seconds.")
@click.option("--scope", multiple=True, help="Allowed tool scope (repeatable).")
@click.pass_context
def spawn(ctx: click.Context, intent: str, ttl: int, scope: tuple) -> None:
    """Spawn an ephemeral session with a fresh signing key."""
    store: ResidueStore = ctx.obj["store"]
    result = _session.spawn(store, intent=intent, ttl=ttl, scopes=list(scope))
    _emit(result, GREEN)
    click.secho(
        f"\nEphemeral session spawned: {result['session_id']}", fg=GREEN, bold=True
    )
    click.secho(
        f"  TTL {ttl}s  |  scopes: {', '.join(scope) if scope else '(none)'}", fg=CYAN
    )
    click.secho(
        f"\n  ⚠  bearer token issued once — store securely:\n"
        f"     {result['token']}",
        fg=GOLD,
        bold=True,
    )


@cli.command()
@click.option("--agent", required=True, help="Agent identifier, e.g. openai://gpt-4.")
@click.option("--session-id", required=True, help="Session from 'ghost spawn'.")
@click.option("--port", default=9999, show_default=True, help="Proxy listen port.")
@click.pass_context
def possess(ctx: click.Context, agent: str, session_id: str, port: int) -> None:
    """Bind an agent to the session via the local intercept proxy."""
    store: ResidueStore = ctx.obj["store"]
    row = store.get_session(session_id)
    if row is None:
        click.secho(f"Error: session {session_id} not found", fg=RED)
        sys.exit(1)
    if row["evaporated_at"]:
        click.secho(f"Error: session {session_id} already evaporated", fg=RED)
        sys.exit(1)
    click.secho(f"Proxy ready on localhost:{port}", fg=CYAN, bold=True)
    click.secho(f"  agent   : {agent}", fg=CYAN)
    click.secho(f"  session : {session_id}", fg=CYAN)
    click.secho(f"  scopes  : {row['scopes'] or '(none)'}", fg=CYAN)
    click.secho("\n  Route agent HTTP through GhostProxy (see examples/). ", fg=GOLD)


@cli.command()
@click.option("--tool", required=True, help="Tool name, e.g. aws_ec2.")
@click.option("--action", required=True, help="Action name, e.g. RunInstances.")
@click.option("--params", type=click.File("r"), default=None, help="JSON params file.")
@click.option("--session-id", required=True, help="Target session.")
@click.option("--no-scope", is_flag=True, help="Disable scope enforcement.")
@click.pass_context
def act(
    ctx: click.Context,
    tool: str,
    action: str,
    params: Optional[click.File],
    session_id: str,
    no_scope: bool,
) -> None:
    """Record a scoped, signed action against the session."""
    store: ResidueStore = ctx.obj["store"]
    payload = json.load(params) if params else {}
    try:
        result = _session.act(
            store,
            session_id,
            tool=tool,
            action=action,
            params=payload,
            enforce_scope=not no_scope,
        )
    except ScopeError as e:
        click.secho(f"DENIED (scope): {e}", fg=RED)
        sys.exit(2)
    except ExpiredSessionError as e:
        click.secho(f"DENIED (expired): {e}", fg=RED)
        sys.exit(3)
    except SessionError as e:
        click.secho(f"Error: {e}", fg=RED)
        sys.exit(1)
    _emit(result, GREEN)
    click.secho("\n  action signed + logged to residue", fg=GREEN)


@cli.command()
@click.option("--session-id", required=True, help="Session to evaporate.")
@click.pass_context
def evaporate(ctx: click.Context, session_id: str) -> None:
    """Destroy the key, sign the chain, finalize the residue."""
    store: ResidueStore = ctx.obj["store"]
    try:
        result = _session.evaporate(store, session_id)
    except SessionError as e:
        click.secho(f"Error: {e}", fg=RED)
        sys.exit(1)
    _emit(result, GOLD)
    click.secho("\n  Session evaporated. Ephemeral key shredded.", fg=PINK, bold=True)


@cli.command()
@click.option("--session-id", default=None, help="Session to replay.")
@click.option("--all", "all_", is_flag=True, help="List all sessions.")
@click.option(
    "--format", "fmt", type=click.Choice(["json", "csv"]), default="json", show_default=True
)
@click.pass_context
def replay(ctx: click.Context, session_id: Optional[str], all_: bool, fmt: str) -> None:
    """Retrieve and verify signed execution history."""
    store: ResidueStore = ctx.obj["store"]
    if all_:
        rows = store.list_sessions()
        if fmt == "csv":
            click.echo("session_id,intent,spawned_at,evaporated_at,actions")
            for r in rows:
                n = store.count_actions(r["session_id"])
                click.echo(
                    f"{r['session_id']},{r['intent']},{r['spawned_at']},"
                    f"{r['evaporated_at'] or ''},{n}"
                )
        else:
            for r in rows:
                _emit(
                    {
                        "session_id": r["session_id"],
                        "intent": r["intent"],
                        "spawned_at": r["spawned_at"],
                        "evaporated_at": r["evaporated_at"],
                        "actions": store.count_actions(r["session_id"]),
                    },
                    GREEN,
                )
        return

    if not session_id:
        click.secho("Error: provide --session-id or --all", fg=RED)
        sys.exit(1)
    try:
        result = _session.replay(store, session_id)
    except SessionError as e:
        click.secho(f"Error: {e}", fg=RED)
        sys.exit(1)

    if fmt == "csv":
        click.echo("seq,tool,action,timestamp,verified")
        for a in result["actions"]:
            click.echo(
                f"{a['seq']},{a['tool']},{a['action']},{a['timestamp']},{a['verified']}"
            )
        click.echo(f"# root_verified={result['root_verified']} verified={result['verified']}")
    else:
        _emit(result, GREEN if result["verified"] else RED)
    if result["verified"]:
        click.secho("\n  residue verified", fg=GREEN, bold=True)
    else:
        click.secho("\n  RESIDUE VERIFICATION FAILED", fg=RED, bold=True)
        sys.exit(4)


@cli.command()
@click.option("--upstream", required=True, help="Upstream base URL, e.g. https://api.stripe.com")
@click.option("--upstream-key", default=None, help="Real API key injected by gateway (broker mode). Never reaches the agent.")
@click.option("--port", default=7391, show_default=True, help="Gateway listen port.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--verbose", is_flag=True, help="Log every proxied request.")
@click.pass_context
def serve(
    ctx: click.Context,
    upstream: str,
    upstream_key: Optional[str],
    port: int,
    host: str,
    verbose: bool,
) -> None:
    """Start the enforcement gateway (reverse proxy / egress broker).

    Agents send requests to this gateway carrying X-Ghost-Token.
    The gateway validates the token, then forwards to --upstream.

    Sidecar mode (your own service):
        ghost serve --upstream http://localhost:8080

    Broker mode (third-party API — real key never reaches the agent):
        ghost serve --upstream https://api.stripe.com --upstream-key sk_live_...

    When 'ghost evaporate' is called, the gateway immediately rejects
    any request with that token — even if the caller cached it.
    """
    store: ResidueStore = ctx.obj["store"]
    mode = "broker" if upstream_key else "sidecar"
    click.secho(
        f"\n  GHOST Gateway — {mode} mode", fg=CYAN, bold=True
    )
    click.secho(f"  upstream : {upstream}", fg=CYAN)
    click.secho(f"  listen   : http://{host}:{port}", fg=CYAN)
    if upstream_key:
        click.secho(
            f"  upstream key injected (real credential hidden from agent)", fg=GOLD
        )
    click.secho("\n  Token enforcement is LIVE. Evaporate kills access instantly.\n", fg=GREEN)

    gw = GhostGateway(
        store=store,
        upstream_url=upstream,
        upstream_key=upstream_key,
        port=port,
        host=host,
        log_requests=verbose,
    )
    gw.start()
    try:
        click.secho("  Ctrl-C to stop.\n", fg=GOLD)
        gw._thread.join()  # type: ignore[union-attr]
    except KeyboardInterrupt:
        gw.stop()
        click.secho("\n  Gateway stopped.", fg=PINK)


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
