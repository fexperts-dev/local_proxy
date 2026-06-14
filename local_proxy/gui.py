"""Desktop GUI for the local combined proxy."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import tkinter as tk
import urllib.request
from tkinter import messagebox, scrolledtext, ttk

from tunnel.client import setup_logging
from tunnel.gui import (
    _GuiLogHandler,
    _LoggingToggleFilter,
    _PayloadLogFilter,
    _RequestLogFilter,
    _ResponseLogFilter,
    _SystemLogFilter,
    add_copyable_field,
)

from .config import load_config
from .service import LocalProxy

log = logging.getLogger("local_proxy.gui")


def _fetch_model_ids(lmstudio_url: str) -> list[str]:
    """Return chat model IDs from LM Studio, excluding embedding models."""
    try:
        with urllib.request.urlopen(f"{lmstudio_url.rstrip('/')}/v1/models", timeout=5) as resp:
            data = json.load(resp)
        return [
            m["id"]
            for m in data.get("data", [])
            if m.get("id") and "embed" not in m["id"].lower()
        ]
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fetch models: %s", exc)
        return []


class LocalProxyApp:
    """Tkinter UI to start/stop local_proxy and display session details."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("local_proxy")
        self.root.minsize(820, 720)

        self._config = load_config()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._proxy_task: asyncio.Task | None = None
        self._proxy: LocalProxy | None = None

        self.logging_enabled = tk.BooleanVar(value=True)
        self.domain = tk.StringVar(value=self._config.domain)
        self.port = tk.StringVar(value=str(self._config.port))
        self.lmstudio_url = tk.StringVar(value=self._config.lmstudio_url)
        self.use_tls = tk.BooleanVar(value=self._config.use_tls)
        self.cursor_base_url = tk.StringVar(value="—")
        self.cursor_api_key = tk.StringVar(value="—")
        self.available_models = tk.StringVar(value="—")
        self.status = tk.StringVar(value="Disconnected")

        self._build_ui()
        self._configure_logging()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}

        control = ttk.Frame(self.root, padding=10)
        control.pack(fill=tk.X, side=tk.TOP)

        config = ttk.LabelFrame(control, text="Local", padding=10)
        config.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(config, text="Domain (/etc/hosts)").grid(row=0, column=0, sticky=tk.W, **pad)
        self.domain_entry = ttk.Entry(config, textvariable=self.domain, width=50)
        self.domain_entry.grid(row=0, column=1, sticky=tk.EW, **pad)

        ttk.Label(config, text="Port").grid(row=1, column=0, sticky=tk.W, **pad)
        self.port_entry = ttk.Entry(config, textvariable=self.port, width=12)
        self.port_entry.grid(row=1, column=1, sticky=tk.W, **pad)

        ttk.Label(config, text="LM Studio URL").grid(row=2, column=0, sticky=tk.W, **pad)
        self.lmstudio_entry = ttk.Entry(config, textvariable=self.lmstudio_url, width=50)
        self.lmstudio_entry.grid(row=2, column=1, sticky=tk.EW, **pad)

        ttk.Checkbutton(config, text="TLS (HTTPS)", variable=self.use_tls).grid(
            row=3, column=1, sticky=tk.W, **pad
        )
        config.columnconfigure(1, weight=1)

        ttk.Label(
            control,
            text="Hosts: 127.0.0.1 → domain (e.g. api.lmstudio.local)",
            wraplength=760,
        ).pack(anchor=tk.W, pady=(0, 8))

        session = ttk.LabelFrame(control, text="IDE (Cursor, Claude, …)", padding=8)
        session.pack(fill=tk.X, pady=(0, 8))
        session.columnconfigure(1, weight=1)
        add_copyable_field(session, 0, "Base URL", self.cursor_base_url, pad=pad)
        add_copyable_field(session, 1, "API Key", self.cursor_api_key, pad=pad)
        add_copyable_field(session, 2, "Model IDs (exact match in Cursor)", self.available_models, pad=pad)

        buttons = ttk.Frame(control)
        buttons.pack(fill=tk.X)
        self.start_btn = ttk.Button(buttons, text="Start", command=self._start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 16))
        ttk.Checkbutton(buttons, text="Logging enabled", variable=self.logging_enabled).pack(side=tk.LEFT)
        ttk.Label(buttons, textvariable=self.status).pack(side=tk.RIGHT)

        log_area = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        log_area.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        log_pane = ttk.PanedWindow(log_area, orient=tk.VERTICAL)
        log_pane.pack(fill=tk.BOTH, expand=True)

        system_frame = ttk.LabelFrame(log_pane, text="System log", padding=8)
        log_pane.add(system_frame)
        self.system_log_text = scrolledtext.ScrolledText(
            system_frame, state=tk.DISABLED, wrap=tk.WORD, height=5, font=("Menlo", 11)
        )
        self.system_log_text.pack(fill=tk.BOTH, expand=True)

        request_frame = ttk.LabelFrame(log_pane, text="Requests", padding=8)
        log_pane.add(request_frame)
        self.request_log_text = scrolledtext.ScrolledText(
            request_frame, state=tk.DISABLED, wrap=tk.WORD, height=6, font=("Menlo", 11)
        )
        self.request_log_text.pack(fill=tk.BOTH, expand=True)

        payload_frame = ttk.LabelFrame(log_pane, text="Payload (Request)", padding=8)
        log_pane.add(payload_frame)
        self.payload_log_text = scrolledtext.ScrolledText(
            payload_frame, state=tk.DISABLED, wrap=tk.WORD, height=6, font=("Menlo", 10)
        )
        self.payload_log_text.pack(fill=tk.BOTH, expand=True)

        response_frame = ttk.LabelFrame(log_pane, text="Response", padding=8)
        log_pane.add(response_frame)
        self.response_log_text = scrolledtext.ScrolledText(
            response_frame, state=tk.DISABLED, wrap=tk.WORD, height=8, font=("Menlo", 10)
        )
        self.response_log_text.pack(fill=tk.BOTH, expand=True)

        self._log_pane = log_pane
        self.root.after_idle(self._init_log_pane_split)

    def _configure_logging(self) -> None:
        toggle_filter = _LoggingToggleFilter(self.logging_enabled)

        system_handler = _GuiLogHandler(self.root, self.system_log_text)
        system_handler.addFilter(_SystemLogFilter())
        system_handler.addFilter(toggle_filter)

        request_handler = _GuiLogHandler(self.root, self.request_log_text)
        request_handler.addFilter(_RequestLogFilter())
        request_handler.addFilter(toggle_filter)

        payload_handler = _GuiLogHandler(self.root, self.payload_log_text)
        payload_handler.addFilter(_PayloadLogFilter())
        payload_handler.addFilter(toggle_filter)

        response_handler = _GuiLogHandler(self.root, self.response_log_text)
        response_handler.addFilter(_ResponseLogFilter())
        response_handler.addFilter(toggle_filter)

        setup_logging(
            self._config.log_level,
            str(self._config.log_file),
            to_console=False,
            to_file=True,
            extra_handlers=[system_handler, request_handler, payload_handler, response_handler],
        )

    def _init_log_pane_split(self) -> None:
        try:
            height = self._log_pane.winfo_height()
            if height > 160:
                self._log_pane.sashpos(0, max(100, height // 5))
                self._log_pane.sashpos(1, max(180, height * 2 // 5))
                self._log_pane.sashpos(2, max(260, height * 3 // 5))
        except tk.TclError:
            pass

    def _apply_config(self) -> None:
        self._config.domain = self.domain.get().strip()
        self._config.port = int(self.port.get().strip())
        self._config.lmstudio_url = self.lmstudio_url.get().strip()
        self._config.use_tls = bool(self.use_tls.get())

    def _set_fields_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for w in (self.domain_entry, self.port_entry, self.lmstudio_entry):
            w.configure(state=state)

    def _on_registered(self, payload: dict) -> None:
        models = _fetch_model_ids(self._config.lmstudio_url)
        models_text = ", ".join(models) if models else "(none — is LM Studio running?)"

        def update() -> None:
            self.cursor_base_url.set(payload.get("api_base_url", "—"))
            self.cursor_api_key.set(payload.get("proxy_token", "—"))
            self.available_models.set(models_text)
            self.status.set("Running")

        self.root.after(0, update)
        if models:
            log.info("LM Studio model IDs for Cursor: %s", models_text)

    def _start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.domain.get().strip():
            messagebox.showerror("Error", "Domain is required.")
            return

        self._apply_config()
        self.status.set("Starting…")
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self._set_fields_enabled(False)

        def run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._proxy = LocalProxy(self._config, on_registered=self._on_registered)
            self._proxy_task = loop.create_task(self._proxy.run_forever())
            try:
                loop.run_until_complete(self._proxy_task)
            except asyncio.CancelledError:
                log.info("Stopped.")
            finally:
                loop.close()
                self._loop = None
                self._proxy_task = None
                self.root.after(0, self._on_stopped)

        self._thread = threading.Thread(target=run_loop, name="local-proxy", daemon=True)
        self._thread.start()

    def _stop(self) -> None:
        if self._loop is None or self._proxy_task is None:
            return
        self.status.set("Stopping…")
        self._loop.call_soon_threadsafe(self._proxy_task.cancel)

    def _on_stopped(self) -> None:
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self._set_fields_enabled(True)
        self.status.set("Disconnected")

    def _on_close(self) -> None:
        if self._loop is not None:
            self._stop()
            if self._thread is not None:
                self._thread.join(timeout=3)
        self.root.destroy()


def main() -> None:
    """Run the local_proxy desktop GUI."""
    root = tk.Tk()
    LocalProxyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
