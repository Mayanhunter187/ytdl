from __future__ import annotations

import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import customtkinter as ctk

from ytdl.search import (
    DATE_OPTIONS,
    DURATION_OPTIONS,
    SORT_OPTIONS,
    THUMB_SIZE,
    TYPE_OPTIONS,
    SearchFilters,
    SearchPage,
    SearchResult,
    fetch_thumbnail,
    is_url,
    search,
)
from ytdl.downloader import friendly_error
from ytdl.ui.common import CARD, MUTED, SECONDARY
from ytdl.ui.format_dialog import FormatDialog

if TYPE_CHECKING:
    from ytdl.ui.app import App

PAGE_SIZE = 20


class ResultRow(ctk.CTkFrame):
    """One search result: thumbnail, title/metadata, and action buttons."""

    def __init__(self, master, result: SearchResult, tab: SearchTab) -> None:
        super().__init__(master, fg_color=CARD)
        self.result = result
        self.grid_columnconfigure(1, weight=1)

        self.thumb = ctk.CTkLabel(
            self, text="", width=THUMB_SIZE[0], height=THUMB_SIZE[1], fg_color=("gray80", "gray25"), corner_radius=4
        )
        self.thumb.grid(row=0, column=0, rowspan=2, padx=8, pady=8)

        title = ctk.CTkLabel(
            self, text=result.title, anchor="w", justify="left", wraplength=300,
            font=ctk.CTkFont(weight="bold"), cursor="hand2",
        )
        title.grid(row=0, column=1, sticky="sw", pady=(8, 0))
        title.bind("<Button-1>", lambda _e: webbrowser.open(result.url))
        self.title_label = title
        ctk.CTkLabel(self, text=result.meta, anchor="w", text_color=MUTED).grid(row=1, column=1, sticky="nw", pady=(0, 8))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=0, column=2, rowspan=2, padx=8)
        self.actions = actions
        browse = lambda: tab.run_search(result.url)  # noqa: E731
        if result.is_channel:
            # One click shouldn't queue a channel's entire back catalogue; browse into a tab instead.
            ctk.CTkButton(actions, text="Browse", width=146, command=browse).pack(side="left")
            self.bind("<Configure>", self._fit_title)
            return
        if result.is_playlist:
            ctk.CTkButton(actions, text="Browse", width=70, command=browse, **SECONDARY).pack(side="left", padx=(0, 6))
        else:
            ctk.CTkButton(
                actions, text="Formats", width=70, **SECONDARY,
                command=lambda: FormatDialog(tab.app, result.url, result.title),
            ).pack(side="left", padx=(0, 6))
        self.video_btn = ctk.CTkButton(actions, text="Video", width=70, command=lambda: self._download(tab.app, False))
        self.video_btn.pack(side="left", padx=(0, 6))
        self.audio_btn = ctk.CTkButton(actions, text="Audio", width=70, command=lambda: self._download(tab.app, True))
        self.audio_btn.pack(side="left")
        self.bind("<Configure>", self._fit_title)

    def _fit_title(self, event) -> None:
        # Wrap the title to whatever room the thumbnail and buttons leave.
        scale = self._get_widget_scaling()  # event sizes are physical pixels; wraplength gets scaled again
        room = (event.width - self.actions.winfo_reqwidth()) / scale - THUMB_SIZE[0] - 48
        self.title_label.configure(wraplength=max(160, int(room)))

    def _download(self, app: App, audio_only: bool) -> None:
        opts = app.make_options(self.result.url, audio_only=audio_only, playlist=self.result.is_playlist)
        app.enqueue(opts, self.result.title)
        btn = self.audio_btn if audio_only else self.video_btn
        btn.configure(text="Added ✓", state="disabled")

    def set_thumbnail(self, image: ctk.CTkImage) -> None:
        self.thumb.configure(image=image, fg_color="transparent")


class SearchTab:
    def __init__(self, app: App, tab: ctk.CTkFrame) -> None:
        self.app = app
        self.thumb_pool = ThreadPoolExecutor(max_workers=6)
        self.token = 0
        self.generation = 0  # bumps when the result list is replaced
        self.empty_pages = 0
        self.rows: list[ResultRow] = []
        self.seen_urls: set[str] = set()
        self.query = ""
        self.filters = SearchFilters()
        self.next_start = 1
        self.loading = False

        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)
        s = app.settings

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        self.query_var = ctk.StringVar()
        self.query_entry = ctk.CTkEntry(
            bar, textvariable=self.query_var, placeholder_text="Search YouTube, or paste a playlist / channel link"
        )
        self.query_entry.grid(row=0, column=0, sticky="ew")
        self.query_entry.bind("<Return>", lambda _e: self.run_search())
        self.search_btn = ctk.CTkButton(bar, text="Search", width=100, command=self.run_search)
        self.search_btn.grid(row=0, column=1, padx=(8, 0))

        filters = ctk.CTkFrame(tab, fg_color="transparent")
        filters.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.type_var = ctk.StringVar(value=s.get("search_type", "Videos"))
        self.date_var = ctk.StringVar(value=s.get("search_date", "Any time"))
        self.duration_var = ctk.StringVar(value=s.get("search_duration", "Any length"))
        self.sort_var = ctk.StringVar(value=s.get("search_sort", "Relevance"))
        for label, var, values in (
            ("Type", self.type_var, TYPE_OPTIONS),
            ("Uploaded", self.date_var, DATE_OPTIONS),
            ("Length", self.duration_var, DURATION_OPTIONS),
            ("Sort by", self.sort_var, SORT_OPTIONS),
        ):
            ctk.CTkLabel(filters, text=label, text_color=MUTED).pack(side="left", padx=(0, 6))
            ctk.CTkOptionMenu(
                filters, values=list(values), variable=var, width=118, command=lambda _v: self._filters_changed()
            ).pack(side="left", padx=(0, 14))

        self.info = ctk.CTkLabel(
            tab, anchor="w", text_color=MUTED,
            text="Video and Audio buttons use the quality, format and folder from Settings.",
        )
        self.info.grid(row=2, column=0, sticky="ew", pady=(6, 2))

        self.results = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.results.grid(row=3, column=0, sticky="nsew")
        self.results.grid_columnconfigure(0, weight=1)
        self.more_btn = ctk.CTkButton(self.results, text="Load more", width=160, command=self.load_more, **SECONDARY)

        for var, key in (
            (self.type_var, "search_type"), (self.date_var, "search_date"),
            (self.duration_var, "search_duration"), (self.sort_var, "search_sort"),
        ):
            app.bind_setting(var, key)

        app.after(100, self.query_entry.focus_set)

    # -------------------------------------------------------------- searching

    def current_filters(self) -> SearchFilters:
        return SearchFilters(self.sort_var.get(), self.date_var.get(), self.type_var.get(), self.duration_var.get())

    def _filters_changed(self) -> None:
        if self.query and not is_url(self.query):
            self.run_search()

    def run_search(self, query: str | None = None) -> None:
        if query is not None:
            self.query_var.set(query)
        query = self.query_var.get().strip()
        if not query:
            return
        self.app.show_tab("Search")
        self.query, self.filters, self.next_start = query, self.current_filters(), 1
        self._fetch(reset=True)

    def load_more(self) -> None:
        if not self.loading:
            self._fetch(reset=False)

    def _fetch(self, reset: bool) -> None:
        self.token += 1
        token, query, filters, start = self.token, self.query, self.filters, self.next_start
        self.loading = True
        self.search_btn.configure(state="disabled", text="Searching…")
        if reset:
            self.info.configure(text=f"Searching for “{query}”…")
        else:
            self.more_btn.configure(state="disabled", text="Loading…")

        def work() -> None:
            try:
                page = search(query, filters, start=start, count=PAGE_SIZE)
                self.app.call_soon(self._on_page, token, reset, page)
            except Exception as exc:
                self.app.call_soon(self._on_error, token, exc)

        threading.Thread(target=work, daemon=True).start()

    def _on_page(self, token: int, reset: bool, page: SearchPage) -> None:
        if token != self.token:
            return
        self.loading = False
        self.search_btn.configure(state="normal", text="Search")
        if reset:
            for row in self.rows:
                row.destroy()
            self.rows, self.seen_urls = [], set()
            self.generation += 1
            self.results._parent_canvas.yview_moveto(0)
        self.next_start = page.next_start

        before = len(self.rows)
        for result in page.results:
            if result.url in self.seen_urls:
                continue
            self.seen_urls.add(result.url)
            row = ResultRow(self.results, result, self)
            row.grid(row=len(self.rows), column=0, sticky="ew", pady=(0, 6))
            self.rows.append(row)
            if result.thumbnail:
                self.thumb_pool.submit(self._load_thumbnail, self.generation, row, result.thumbnail)

        n = len(self.rows)
        text = f"{n} result{'s' if n != 1 else ''} for “{self.query}”"
        if is_url(self.query):
            text += "  ·  filters don't apply to links"
        self.info.configure(text=text)

        self.more_btn.grid_forget()
        if not page.has_more:
            return
        # YouTube sometimes repeats a page; quietly fetch further instead of adding nothing.
        self.empty_pages = 0 if len(self.rows) > before or reset else self.empty_pages + 1
        if not reset and len(self.rows) == before and self.empty_pages <= 2:
            self._fetch(reset=False)
            return
        self.more_btn.configure(state="normal", text="Load more")
        self.more_btn.grid(row=len(self.rows), column=0, pady=(4, 8))

    def _on_error(self, token: int, exc: Exception) -> None:
        if token != self.token:
            return
        self.loading = False
        self.search_btn.configure(state="normal", text="Search")
        self.more_btn.configure(state="normal", text="Load more")
        message = friendly_error(str(exc))
        self.info.configure(text=f"Search failed: {message or type(exc).__name__}")

    def _load_thumbnail(self, generation: int, row: ResultRow, url: str) -> None:
        if generation != self.generation:  # a newer search replaced these rows
            return
        try:
            image = fetch_thumbnail(url)
        except Exception:
            return  # a missing thumbnail isn't worth reporting
        self.app.call_soon(self._set_thumbnail, row, image)

    def _set_thumbnail(self, row: ResultRow, image) -> None:
        if row.winfo_exists():
            row.set_thumbnail(ctk.CTkImage(light_image=image, size=THUMB_SIZE))

    def shutdown(self) -> None:
        self.thumb_pool.shutdown(wait=False, cancel_futures=True)
