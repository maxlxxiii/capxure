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

from capxure.github import GitHubClient, parse_github_url
from capxure.parser import extract_repo_urls
from capxure.processor import Severity, fetch_rate_limit, process_repo
from capxure.queue import Queue
from capxure.storage import DEFAULT_DATA_DIR, Storage


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
    """Top bar: app title, repo count, queue depth, rate limit."""

    total_repos: reactive[int] = reactive(0)
    queue_pending: reactive[int] = reactive(0)
    rate_limit_remaining: reactive[int] = reactive(-1)

    def render(self) -> Text:
        rl = str(self.rate_limit_remaining) if self.rate_limit_remaining >= 0 else "..."
        text = Text()
        text.append(" CAPXURE ", style="bold reverse green")
        text.append("  Repos: ", style="bold")
        text.append(str(self.total_repos), style="bold green")
        text.append("  \u2502  Queue: ", style="bold")
        text.append(f"{self.queue_pending} pending", style="bold cyan")
        text.append("  \u2502  Rate limit: ", style="bold")
        text.append(rl, style="bold yellow")
        return text

    def watch_total_repos(self) -> None:
        self.refresh()

    def watch_queue_pending(self) -> None:
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
        self._queue = Queue()
        self._github: GitHubClient | None = None
        self._processing = False

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
        self._update_queue_count()
        self._refresh_rate_limit()

    async def on_unmount(self) -> None:
        if self._github is not None:
            await self._github.__aexit__(None, None, None)

    @on(Input.Submitted, "#url-input")
    def handle_url_submit(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        if not raw:
            return
        event.input.value = ""

        # /queue is read-only — no token needed
        if raw == "/queue":
            self._handle_queue_cmd()
            return

        if self._github is None:
            self._log_message(
                "Cannot process: GITHUB_TOKEN not configured.",
                Severity.ERROR,
            )
            return

        if raw.startswith("/alist "):
            self._handle_alist(raw[7:].strip())
        elif raw == "/process":
            self._handle_process()
        else:
            self._process_url(raw)

    @work(exclusive=False)
    async def _process_url(self, url: str) -> None:
        assert self._github is not None

        result = await process_repo(
            url,
            github=self._github,
            storage=self._storage,
            on_status=self._log_message,
        )

        # Track in queue for history
        if result.error is None and result.owner:
            key = f"{result.owner}--{result.repo}"
            self._queue.add(
                url, result.owner, result.repo,
                priority="manual", source="manual",
            )
            self._queue.update_status(key, "done")

        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.total_repos = self._storage.count_repos()
        self._update_queue_count()
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

    @work(exclusive=False)
    async def _handle_alist(self, url: str) -> None:
        assert self._github is not None

        self._log_message(f"Processing awesome-list: {url}...", Severity.INFO)

        try:
            owner, repo = parse_github_url(url)
        except ValueError as exc:
            self._log_message(str(exc), Severity.ERROR)
            return

        alist_storage = Storage(data_dir=DEFAULT_DATA_DIR / "awesome-lists")
        alist_storage.ensure_directories()

        result = await process_repo(
            url,
            github=self._github,
            storage=alist_storage,
            on_status=self._log_message,
        )
        if result.error is not None:
            return

        # Read the downloaded README and parse repo URLs
        key = f"{owner}--{repo}"
        readme_path = DEFAULT_DATA_DIR / "awesome-lists" / "readmes" / f"{key}.md"
        try:
            readme_content = readme_path.read_text(encoding="utf-8")
        except OSError:
            self._log_message(
                f"Could not read README for {owner}/{repo}", Severity.ERROR,
            )
            return

        repos = extract_repo_urls(readme_content)
        added, skipped = self._queue.bulk_add(
            repos, source=f"awesome-list:{key}",
        )

        self._log_message(
            f"Parsed {len(repos)} repos from {owner}/{repo}, "
            f"added {added} new to queue ({skipped} already queued)",
            Severity.SUCCESS,
        )
        self._update_queue_count()

    @work(exclusive=False)
    async def _handle_process(self) -> None:
        assert self._github is not None

        if self._processing:
            self._log_message("Queue processing already running.", Severity.INFO)
            return

        self._processing = True
        try:
            stats = self._queue.get_stats()
            total = stats["pending"] + stats["failed"] + stats["rate_limited"]
            if total == 0:
                self._log_message("Queue is empty — nothing to process.", Severity.INFO)
                return

            self._log_message(f"Starting queue processing ({total} to go)...", Severity.INFO)
            processed = 0

            while True:
                entry = self._queue.pop_next()
                if entry is None:
                    break

                processed += 1
                self._log_message(
                    f"[{processed}/{total}] {entry['url']}",
                    Severity.INFO,
                )

                result = await process_repo(
                    entry["url"],
                    github=self._github,
                    storage=self._storage,
                    on_status=self._log_message,
                )

                if result.error and "rate limit" in result.error.lower():
                    self._queue.update_status(entry["key"], "rate_limited")
                    self._queue.mark_all_pending_as("rate_limited")
                    try:
                        info = await fetch_rate_limit(self._github)
                        reset_dt = datetime.fromtimestamp(info.reset_timestamp)
                        wait = reset_dt.strftime("%H:%M:%S")
                        self._log_message(
                            f"Rate limited. Resets at {wait}. "
                            f"Run /process again after that.",
                            Severity.ERROR,
                        )
                    except Exception:
                        self._log_message(
                            "Rate limited. Run /process again later.",
                            Severity.ERROR,
                        )
                    break
                elif result.error:
                    self._queue.update_status(
                        entry["key"], "failed", error=result.error,
                    )
                else:
                    self._queue.update_status(entry["key"], "done")

                # Update status bar after each repo
                status_bar = self.query_one("#status-bar", StatusBar)
                status_bar.total_repos = self._storage.count_repos()
                self._update_queue_count()
                self._refresh_rate_limit()

            self._log_message("Queue processing finished.", Severity.SUCCESS)
        finally:
            self._processing = False

    def _handle_queue_cmd(self) -> None:
        stats = self._queue.get_stats()
        self._log_message(
            f"Queue: {stats['total']} total — "
            f"{stats['pending']} pending, "
            f"{stats['done']} done, "
            f"{stats['failed']} failed, "
            f"{stats['rate_limited']} rate-limited",
            Severity.INFO,
        )
        if stats["pending"] > 0:
            preview = self._queue.get_pending_preview(5)
            for entry in preview:
                self._log_message(
                    f"  next: {entry['url']}  (source: {entry['source']})",
                    Severity.INFO,
                )

    def _update_queue_count(self) -> None:
        self.query_one("#status-bar", StatusBar).queue_pending = (
            self._queue.pending_count()
        )

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
