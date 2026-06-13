"""Pretty terminal UI primitives for the SCHE-MA REPL.

Centralized so commands.py / repl.py stay free of styling concerns.
All output goes through the shared `console`; styles use rich's theme.
"""
from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = Theme({
    "brand":     "bold cyan",
    "brand.dim": "dim cyan",
    "ok":        "bold green",
    "warn":      "bold yellow",
    "err":       "bold red",
    "key":       "bold magenta",
    "val":       "white",
    "muted":     "dim white",
    "prompt":    "bold cyan",
    "user":      "bold white",
})

console = Console(theme=THEME, highlight=False)

# compact wordmark — block letters, no backslashes (rich-markup-safe)
LOGO = """[brand]   ██████╗  ██████╗██╗  ██╗███████╗      ███╗   ███╗ █████╗ [/brand]
[brand]   ██╔════╝ ██╔════╝██║  ██║██╔════╝      ████╗ ████║██╔══██╗[/brand]
[brand]   ╚█████╗  ██║     ███████║█████╗  █████╗██╔████╔██║███████║[/brand]
[brand]    ╚═══██╗ ██║     ██╔══██║██╔══╝  ╚════╝██║╚██╔╝██║██╔══██║[/brand]
[brand]   ██████╔╝ ╚██████╗██║  ██║███████╗      ██║ ╚═╝ ██║██║  ██║[/brand]
[brand]   ╚═════╝   ╚═════╝╚═╝  ╚═╝╚══════╝      ╚═╝     ╚═╝╚═╝  ╚═╝[/brand]
[brand.dim]   Security CHallenge Exploitation Multi-Agent[/brand.dim]"""


def print_banner(version: str = "0.1.0") -> None:
    body = f"{LOGO}\n\n[muted]v{version}  ·  type[/muted] [key]/help[/key] [muted]for commands,[/muted] [key]/exit[/key] [muted]to quit[/muted]"
    console.print(Panel(body, border_style="brand", padding=(1, 2)))


def print_status(backend: str, model: str, api_key_set: bool, cwd: str) -> None:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="key")
    t.add_column(style="val")
    t.add_row("backend", f"[{'ok' if backend else 'warn'}]{backend}[/]")
    t.add_row("model",   f"[ok]{model}[/]")
    t.add_row("api_key", "[ok]set[/]" if api_key_set else "[warn]unset[/]")
    t.add_row("cwd",     f"[muted]{cwd}[/]")
    console.print(t)


def prompt_text(backend: str, model: str) -> str:
    """Returns the colored input prompt string."""
    # Use rich markup via Text → ansi
    txt = Text()
    txt.append("\n", style="")
    txt.append(f"[{backend}·{model}]", style="muted")
    txt.append(" schema", style="brand")
    txt.append("› ",  style="prompt")
    return txt.markup if False else _to_ansi(txt)


def _to_ansi(txt: Text) -> str:
    # Render to ANSI so input() shows colors
    with console.capture() as cap:
        console.print(txt, end="")
    return cap.get()


def print_reply(text: str) -> None:
    """Render LLM reply as markdown (code blocks, lists, etc.)."""
    md = Markdown(text or "[muted](empty reply)[/muted]")
    console.print(Panel(md, border_style="brand.dim", padding=(0, 1)))


def print_info(msg: str) -> None:
    console.print(f"[muted]· {msg}[/muted]")


def print_ok(msg: str) -> None:
    console.print(f"[ok]✓[/ok] {msg}")


def print_err(msg: str) -> None:
    console.print(f"[err]✗[/err] {msg}")


def print_warn(msg: str) -> None:
    console.print(f"[warn]![/warn] {msg}")


def print_kv(rows: list[tuple[str, str]], title: str | None = None) -> None:
    t = Table(title=title, title_style="brand", border_style="brand.dim", show_header=False, box=None, pad_edge=False)
    t.add_column(style="key", no_wrap=True)
    t.add_column(style="val")
    for k, v in rows:
        t.add_row(k, v)
    console.print(t)


def print_help_table() -> None:
    t = Table(title="commands", title_style="brand", border_style="brand.dim",
              header_style="key", padding=(0, 1))
    t.add_column("command", style="key", no_wrap=True)
    t.add_column("description", style="val")
    rows = [
        ("/help",            "show this table"),
        ("/task <id>",       "run one CyberGym task (e.g. /task arvo:10400)"),
        ("/subset [N]",      "run first N from data/subset_tasks.txt"),
        ("/backend <name>",  "switch backend: claude_code | claude_api"),
        ("/model <alias>",   "switch chat model: haiku | sonnet | opus"),
        ("/config",          "print resolved settings"),
        ("/cost",            "session cost totals"),
        ("/clear",           "clear screen"),
        ("/exit",            "quit"),
    ]
    for k, v in rows:
        t.add_row(k, v)
    console.print(t)
    console.print("[muted]Anything not starting with `/` is sent as a free-form prompt to the active backend.[/muted]")


def print_task_result(task_id: str, success: bool, exit_code: int, poc_id: str,
                      cost: float, stages: list[str]) -> None:
    icon = "[ok]✓[/ok]" if success else "[err]✗[/err]"
    t = Table.grid(padding=(0, 2))
    t.add_column(style="key"); t.add_column(style="val")
    t.add_row("task",   f"[brand]{task_id}[/brand]")
    t.add_row("result", f"{icon}  exit={exit_code}  poc={poc_id}")
    t.add_row("cost",   f"${cost:.3f}")
    t.add_row("stages", "→".join(f"[brand.dim]{s}[/brand.dim]" for s in stages))
    console.print(t)


def status(msg: str):
    """Context manager: rich spinner with text. Use `with ui.status('asking…'):`."""
    return console.status(f"[brand.dim]{msg}[/brand.dim]", spinner="dots")
