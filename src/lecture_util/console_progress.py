"""A live stage board with a plain-text fallback for redirected output."""
from __future__ import annotations

from time import monotonic
from types import TracebackType

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from lecture_util.progress import ProgressEvent, format_duration


class ConsoleProgressReporter:
    def __init__(
        self, stages: tuple[str, ...], *, console: Console,
        lecture_label: str | None = None, source_url: str | None = None,
    ) -> None:
        self.stages = stages
        self.console = console
        self.lecture_label = lecture_label
        self.source_url = source_url
        self.events: dict[str, ProgressEvent] = {}
        self.warnings: list[str] = []
        self.latest = "Preparing lecture processing"
        self.started = monotonic()
        self.live: Live | None = None
        self.last_update: dict[str, float] = {}
        self.spinner = Spinner("dots", style="cyan")

    def __enter__(self) -> ConsoleProgressReporter:
        self.started = monotonic()
        if self.console.is_terminal and not self.console.is_dumb_terminal:
            self.live = Live(
                console=self.console, get_renderable=self.render,
                refresh_per_second=4, transient=True,
            )
            self.live.start()
        else:
            for line in self._lecture_details():
                self.console.print(line)
        return self

    def _lecture_details(self) -> list[Text]:
        details = []
        if self.lecture_label:
            details.append(Text(self.lecture_label, style="bold", overflow="fold"))
        if self.source_url:
            details.append(Text(self.source_url, style="dim", overflow="fold"))
        return details

    def __exit__(
        self, exc_type: type[BaseException] | None,
        exc: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        if self.live is not None:
            self.live.stop()
            for warning in self.warnings:
                self.console.print(Text(f"! {warning}", style="yellow"))
            if exc is not None:
                active = next((stage for stage, event in self.events.items()
                               if event.status in {"start", "update", "failed"}), None)
                status = "Interrupted" if isinstance(exc, KeyboardInterrupt) else "Failed"
                self.console.print(Text(f"{status}: {active or 'processing'}", style="red"))
            self.live = None

    def __call__(self, event: ProgressEvent) -> None:
        now = monotonic()
        previous = self.events.get(event.stage)
        interval = 0.5 if self.live is not None else 10.0
        if (event.status == "update" and previous is not None
                and previous.status == "update" and previous.phase == event.phase
                and now - self.last_update.get(event.stage, 0) < interval):
            return
        self.last_update[event.stage] = now
        self.latest = event.message
        if event.processed_seconds is not None and event.total_seconds:
            percent = min(100, 100 * event.processed_seconds / event.total_seconds)
            self.latest += (f" · ~{percent:.0f}% "
                            f"({format_duration(event.processed_seconds)}/{format_duration(event.total_seconds)})")
            if event.elapsed_seconds:
                self.latest += f" · {event.processed_seconds / event.elapsed_seconds:.1f}× audio/wall time"
        if event.status == "warning":
            if event.message not in self.warnings:
                self.warnings.append(event.message)
        else:
            self.events[event.stage] = event
        if self.live is not None:
            self.live.refresh()
            return
        marker, style = {
            "start": ("→", "cyan"), "update": ("·", "blue"),
            "complete": ("✓", "green"), "cached": ("↻", "dim"),
            "warning": ("!", "yellow"), "failed": ("✗", "red"),
        }[event.status]
        prefix = (f"[{self.stages.index(event.stage) + 1}/{len(self.stages)}] "
                  if event.stage in self.stages else "")
        self.console.print(Text(f"{marker} {prefix}{self.latest}", style=style))

    def render(self) -> Panel:
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(width=2)
        table.add_column(ratio=1)
        table.add_column(justify="right")
        statuses = {"start": ("", "Running", "cyan"),
                    "update": ("", "Running", "cyan"),
                    "complete": ("✓", "Complete", "green"),
                    "cached": ("↻", "Cached", "dim"),
                    "failed": ("✗", "Failed", "red")}
        for stage in self.stages:
            event = self.events.get(stage)
            marker, label, style = statuses[event.status] if event else ("·", "Waiting", "dim")
            icon = self.spinner if label == "Running" else Text(marker, style=style)
            table.add_row(icon, Text(stage.capitalize(), style=style), Text(label, style=style))
        header = self._lecture_details()
        if header:
            header.append(Text(""))
        details = [*header, table, Text(""), Text(self.latest)]
        details.extend(Text(f"! {warning}", style="yellow") for warning in self.warnings)
        return Panel(Group(*details), title="Lecture processing",
                     subtitle=f"Elapsed {format_duration(monotonic() - self.started)}",
                     border_style="cyan")
