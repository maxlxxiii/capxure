"""Textual TUI application."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.design import ColorSystem
from textual.reactive import reactive
from textual.widgets import Input, RichLog, Static

from capxure.github import GitHubClient
from capxure.processor import Severity, fetch_rate_limit, process_repo
from capxure.storage import Storage


# ── Custom Theme ──────────────────────────────────────────────

CAPXURE_COLORS = ColorSystem(
    primary="#00ff41",
    secondary="#ffb000",
    accent="#00ff41",
    foreground="#b0b0b0",
    background="#0a0a0a",
    surface="#111111",
    panel="#1a1a1a",
    success="#00ff41",
    warning="#ffb000",
    error="#ff3333",
    dark=True,
)


# ── Status Bar Widget ────────────────────────────────────────


class StatusBar(Static):
    """Top bar: app title, repo count, rate limit."""

    total_repos: reactive[int] = reactive(0)
    rate_limit_remaining: reactive[int] = reactive(-1)

    def render(self) -> Text:
        rl = str(self.rate_limit_remaining) if self.rate_limit_remaining >= 0 else "..."
        text = Text()
        text.append(" CAPXURE ", style="bold reverse green")
        text.append("  Repos: ", style="bold")
        text.append(str(self.total_repos), style="bold green")
        text.append("  \u2502  Rate limit: ", style="bold")
        text.append(rl, style="bold yellow")
        return text

    def watch_total_repos(self) -> None:
        self.refresh()

    def watch_rate_limit_remaining(self) -> None:
        self.refresh()


# ── Main App ──────────────────────────────────────────────────


class CapxureApp(App):
    """capxure TUI application."""

    CSS_PATH = "app.tcss"
    TITLE = "capxure"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()

        # Load .env from project root
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        load_dotenv(dotenv_path=env_path)

        self._token: str = os.environ.get("GITHUB_TOKEN", "")
        self._storage = Storage()
        self._github: GitHubClient | None = None

    def get_css_variables(self) -> dict[str, str]:
        """Override to inject our custom color system."""
        variables = super().get_css_variables()
        variables.update(CAPXURE_COLORS.generate())
        return variables

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        yield RichLog(
            id="log",
            highlight=False,
            markup=False,
            wrap=True,
            auto_scroll=True,
        )
        yield Input(
            placeholder="Paste a GitHub repo URL and press Enter...",
            id="url-input",
        )

    async def on_mount(self) -> None:
        self._storage.ensure_directories()

        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.total_repos = self._storage.count_repos()

        if not self._token:
            self._log_message(
                "GITHUB_TOKEN not found in .env file. Set it and restart.",
                Severity.ERROR,
            )
            return

        self._github = GitHubClient(self._token)
        await self._github.__aenter__()

        self._log_message("System online. Paste a GitHub URL below.", Severity.SUCCESS)
        self._refresh_rate_limit()

    async def on_unmount(self) -> None:
        if self._github is not None:
            await self._github.__aexit__(None, None, None)

    @on(Input.Submitted, "#url-input")
    def handle_url_submit(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if not url:
            return
        event.input.value = ""

        if self._github is None:
            self._log_message(
                "Cannot process: GITHUB_TOKEN not configured.",
                Severity.ERROR,
            )
            return

        self._process_url(url)

    @work(exclusive=False)
    async def _process_url(self, url: str) -> None:
        assert self._github is not None

        result = await process_repo(
            url,
            github=self._github,
            storage=self._storage,
            on_status=self._log_message,
        )

        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.total_repos = self._storage.count_repos()
        self._refresh_rate_limit()

    @work(exclusive=True)
    async def _refresh_rate_limit(self) -> None:
        if self._github is None:
            return
        try:
            info = await fetch_rate_limit(self._github)
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.rate_limit_remaining = info.remaining
        except Exception:
            pass

    def _log_message(self, message: str, severity: Severity) -> None:
        log_widget = self.query_one("#log", RichLog)
        timestamp = datetime.now().strftime("%H:%M:%S")

        style_map = {
            Severity.SUCCESS: "green",
            Severity.INFO: "yellow",
            Severity.ERROR: "bold red",
        }
        style = style_map.get(severity, "white")

        text = Text()
        text.append(f"[{timestamp}] ", style="dim")
        text.append(message, style=style)
        log_widget.write(text)
