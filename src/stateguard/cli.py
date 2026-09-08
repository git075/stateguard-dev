"""Rich Terminal CLI for StateGuard.

Commands:
    stateguard log       — Show recent audit events as a colored table
    stateguard sagas     — List all saga IDs with event counts
    stateguard inspect   — Full event history for one saga with state diffs
    stateguard export    — Export events to JSON
"""

import json
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text
from rich.columns import Columns

from stateguard.store import AuditStore, EventType

console = Console()

# ── Colour palette ────────────────────────────────────────────────────────────
_COLOURS = {
    EventType.COMMIT:          ("✅", "green"),
    EventType.ROLLBACK:        ("🔴", "red"),
    EventType.INVARIANT_WARN:  ("⚠️ ", "yellow"),
    EventType.INVARIANT_BLOCK: ("❌", "bold red"),
    EventType.COMPENSATION:    ("↩️ ", "blue"),
    EventType.SAGA_START:      ("▶️ ", "cyan"),
    EventType.SAGA_END:        ("⏹️ ", "cyan"),
}

def _style(event_type: EventType) -> tuple[str, str]:
    return _COLOURS.get(event_type, ("•", "white"))


def _store(db_path: str) -> AuditStore:
    p = Path(db_path)
    if not p.exists():
        console.print(f"[red]No audit database found at {db_path}[/red]")
        console.print("[dim]Run your agents with StateGuard first to generate events.[/dim]")
        sys.exit(1)
    return AuditStore(db_path)


def _diff_text(before: Optional[dict], after: Optional[dict]) -> str:
    """Produce a compact + / - diff string between two state dicts."""
    if before is None and after is None:
        return "—"
    before = before or {}
    after  = after  or {}
    all_keys = sorted(set(before) | set(after))
    lines = []
    for k in all_keys:
        b, a = before.get(k, "∅"), after.get(k, "∅")
        if b != a:
            lines.append(f"{k}: {b!r} → {a!r}")
    return " | ".join(lines) if lines else "(no changes)"


# ── CLI Group ─────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("0.1.0", prog_name="stateguard")
def cli() -> None:
    """StateGuard — Debug multi-agent AI workflows from your terminal."""


# ── stateguard log ────────────────────────────────────────────────────────────

@cli.command("log")
@click.option("--last",  "-n", default=20,    show_default=True, help="Number of events to show.")
@click.option("--saga",  "-s", default=None,  help="Filter by saga ID.")
@click.option("--type",  "-t", "etype", default=None,
              type=click.Choice([e.value for e in EventType], case_sensitive=False),
              help="Filter by event type.")
@click.option("--db",    default=".stateguard/audit.db", show_default=True,
              help="Path to the audit database.")
def log_cmd(last: int, saga: Optional[str], etype: Optional[str], db: str) -> None:
    """Show recent audit events as a coloured table."""
    store  = _store(db)
    events = store.read_last(n=last, event_type=etype, saga_id=saga)

    if not events:
        console.print("[yellow]No events found.[/yellow]")
        return

    title = f"StateGuard Audit Trail — last {len(events)} events"
    if saga:
        title += f"  [dim](saga: {saga})[/dim]"

    table = Table(title=title, box=box.ROUNDED, highlight=True, show_lines=False)
    table.add_column("Time",       style="dim",    width=10, no_wrap=True)
    table.add_column("Type",                       width=18)
    table.add_column("Saga",       style="cyan",   width=14, no_wrap=True)
    table.add_column("Step",       style="white",  width=18, no_wrap=True)
    table.add_column("Rule",       style="magenta",width=20, no_wrap=True)
    table.add_column("Changes / Message",           min_width=30)

    for e in events:
        icon, colour = _style(e.event_type)
        diff = _diff_text(e.state_before, e.state_after) if e.state_after else (e.message or "—")
        table.add_row(
            e.timestamp.strftime("%H:%M:%S"),
            Text(f"{icon} {e.event_type.value}", style=colour),
            e.saga_id[:13],
            e.step_name[:17],
            e.rule_name[:19],
            diff[:80],
        )

    console.print(table)


# ── stateguard sagas ──────────────────────────────────────────────────────────

@cli.command("sagas")
@click.option("--db", default=".stateguard/audit.db", show_default=True,
              help="Path to the audit database.")
def sagas_cmd(db: str) -> None:
    """List all saga IDs with their event counts and status."""
    store = _store(db)
    ids   = store.list_sagas()

    if not ids:
        console.print("[yellow]No sagas found.[/yellow]")
        return

    table = Table(title="StateGuard — Saga Summary", box=box.ROUNDED, highlight=True)
    table.add_column("Saga ID",     style="cyan",  width=20)
    table.add_column("Events",      style="white", width=8,  justify="right")
    table.add_column("Commits",     style="green", width=9,  justify="right")
    table.add_column("Rollbacks",   style="red",   width=10, justify="right")
    table.add_column("Warnings",    style="yellow",width=9,  justify="right")
    table.add_column("Status",                     width=12)

    for saga_id in ids:
        events    = store.read_saga(saga_id)
        commits   = sum(1 for e in events if e.event_type == EventType.COMMIT)
        rollbacks = sum(1 for e in events if e.event_type == EventType.ROLLBACK)
        warns     = sum(1 for e in events if e.event_type == EventType.INVARIANT_WARN)
        # Determine terminal status
        types = [e.event_type for e in events]
        if EventType.SAGA_END in types:
            last_end = next(e for e in reversed(events) if e.event_type == EventType.SAGA_END)
            status = Text("✅ done" if last_end.message == "completed" else "🔴 failed",
                          style="green" if last_end.message == "completed" else "red")
        elif rollbacks:
            status = Text("🔴 rolled back", style="red")
        else:
            status = Text("⏳ in progress", style="yellow")

        table.add_row(saga_id[:19], str(len(events)), str(commits),
                      str(rollbacks), str(warns), status)

    console.print(table)


# ── stateguard inspect ────────────────────────────────────────────────────────

@cli.command("inspect")
@click.argument("saga_id")
@click.option("--db", default=".stateguard/audit.db", show_default=True,
              help="Path to the audit database.")
def inspect_cmd(saga_id: str, db: str) -> None:
    """Show the full event history for a saga with state diffs."""
    store  = _store(db)
    events = store.read_saga(saga_id)

    if not events:
        console.print(f"[yellow]No events found for saga '{saga_id}'.[/yellow]")
        return

    console.print()
    console.rule(f"[bold cyan]Saga: {saga_id}[/bold cyan]")

    for i, e in enumerate(events, 1):
        icon, colour = _style(e.event_type)
        header = f"{icon} [{colour}]{e.event_type.value.upper()}[/{colour}]  " \
                 f"[dim]{e.timestamp.strftime('%H:%M:%S')}[/dim]"

        body_lines = []
        if e.step_name != "—":
            body_lines.append(f"[bold]Step:[/bold]  {e.step_name}")
        if e.rule_name != "—":
            body_lines.append(f"[bold]Rule:[/bold]  {e.rule_name}")
        if e.message:
            body_lines.append(f"[bold]Msg:[/bold]   {e.message}")
        if e.state_before or e.state_after:
            diff = _diff_text(e.state_before, e.state_after)
            body_lines.append(f"[bold]Diff:[/bold]  [dim]{diff}[/dim]")
            if e.state_before:
                body_lines.append(f"[bold]Before:[/bold] {json.dumps(e.state_before, default=str)}")
            if e.state_after:
                body_lines.append(f"[bold]After:[/bold]  {json.dumps(e.state_after, default=str)}")

        body = "\n".join(body_lines) if body_lines else "[dim](no details)[/dim]"
        console.print(Panel(body, title=header, border_style=colour, expand=False))

    console.print()


# ── stateguard export ─────────────────────────────────────────────────────────

@cli.command("export")
@click.option("--saga",   "-s", default=None, help="Export events for a specific saga.")
@click.option("--last",   "-n", default=100,  show_default=True, help="Export last N events.")
@click.option("--output", "-o", default=None, help="Output file path (default: stdout).")
@click.option("--db",     default=".stateguard/audit.db", show_default=True,
              help="Path to the audit database.")
def export_cmd(saga: Optional[str], last: int, output: Optional[str], db: str) -> None:
    """Export audit events to JSON."""
    store = _store(db)
    data  = store.export_json(saga_id=saga, last=last)

    if output:
        Path(output).write_text(data)
        console.print(f"[green]Exported to {output}[/green]")
    else:
        print(data)


if __name__ == "__main__":
    cli()
