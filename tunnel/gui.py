"""Desktop GUI for starting the tunnel client and viewing logs."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from .client import TunnelClient, log, setup_logging


class _GuiLogHandler(logging.Handler):
    """Thread-safe handler that appends formatted records to a text widget."""

    def __init__(self, root: tk.Tk, text: scrolledtext.ScrolledText) -> None:
        super().__init__()
        self._root = root
        self._text = text

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)

        def append() -> None:
            self._text.configure(state=tk.NORMAL)
            self._text.insert(tk.END, msg + "\n")
            self._text.see(tk.END)
            self._text.configure(state=tk.DISABLED)

        self._root.after(0, append)


def _is_request_log(record: logging.LogRecord) -> bool:
    msg = record.getMessage()
    return (
        msg.startswith("Forwarding ")
        or msg.startswith("Error forwarding ")
        or (" -> " in msg and record.name == "tunnel.client")
    )


def _is_payload_log(record: logging.LogRecord) -> bool:
    msg = record.getMessage()
    return msg.startswith("Payload model=") or msg.startswith("Payload request ")


def _is_response_log(record: logging.LogRecord) -> bool:
    return record.getMessage().startswith("Payload response ")


class _RequestLogFilter(logging.Filter):
    """Route LM Studio request lines to the request log panel."""

    def filter(self, record: logging.LogRecord) -> bool:
        return _is_request_log(record)


class _SystemLogFilter(logging.Filter):
    """Everything except request, payload and response lines goes to the system log panel."""

    def filter(self, record: logging.LogRecord) -> bool:
        return (
            not _is_request_log(record)
            and not _is_payload_log(record)
            and not _is_response_log(record)
        )


class _PayloadLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return _is_payload_log(record)


class _ResponseLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return _is_response_log(record)


class _LoggingToggleFilter(logging.Filter):
    def __init__(self, enabled: tk.BooleanVar) -> None:
        super().__init__()
        self._enabled = enabled

    def filter(self, record: logging.LogRecord) -> bool:
        return bool(self._enabled.get())


def copy_to_clipboard(root: tk.Misc, text: str, *, empty_message: str = "Not available yet.") -> None:
    """Copy *text* to the clipboard or show *empty_message* when empty."""
    value = (text or "").strip()
    if not value or value == "—":
        messagebox.showinfo("Copy", empty_message)
        return
    root.clipboard_clear()
    root.clipboard_append(value)
    root.update_idletasks()


def add_copyable_field(
    parent: ttk.Frame,
    row: int,
    label: str,
    variable: tk.StringVar,
    *,
    pad: dict,
    width: int = 52,
) -> tuple[ttk.Entry, ttk.Button]:
    """Add a read-only entry with a copy-to-clipboard button on *parent*."""
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.NW, **pad)
    field_frame = ttk.Frame(parent)
    field_frame.grid(row=row, column=1, sticky=tk.EW, **pad)
    entry = ttk.Entry(field_frame, textvariable=variable, width=width, state="readonly")
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
    btn = ttk.Button(
        field_frame,
        text="Copy",
        width=10,
        command=lambda: copy_to_clipboard(parent.winfo_toplevel(), variable.get()),
    )
    btn.pack(side=tk.LEFT, padx=(6, 0))
    return entry, btn


def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from *path* into os.environ (setdefault only)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


class TunnelClientApp:
    """Tkinter UI to start/stop the tunnel client and display session details."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Reverse HTTPS Tunnel Client")
        self.root.minsize(820, 640)

        _load_dotenv()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client_task: asyncio.Task | None = None

        self.logging_enabled = tk.BooleanVar(value=True)
        self.server_url = tk.StringVar(value=os.environ.get("TUNNEL_SERVER_URL", ""))
        self.token = tk.StringVar(value=os.environ.get("TUNNEL_TOKEN", ""))
        self.registration_secret = tk.StringVar(value=os.environ.get("TUNNEL_REGISTRATION_SECRET", ""))
        self.client_id = tk.StringVar(value=os.environ.get("CLIENT_ID", ""))
        self.target = tk.StringVar(value=os.environ.get("LMSTUDIO_URL", "http://localhost:1234"))
        self.cursor_api_key = tk.StringVar(value="—")
        self.cursor_base_url = tk.StringVar(value="—")
        self.status = tk.StringVar(value="Disconnected")

        self._build_ui()
        self._configure_logging()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}

        control_area = ttk.Frame(self.root, padding=10)
        control_area.pack(fill=tk.X, side=tk.TOP)

        config = ttk.LabelFrame(control_area, text="Connection", padding=10)
        config.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(config, text="Server URL").grid(row=0, column=0, sticky=tk.W, **pad)
        self.server_url_entry = ttk.Entry(config, textvariable=self.server_url, width=60)
        self.server_url_entry.grid(row=0, column=1, sticky=tk.EW, **pad)

        ttk.Label(config, text="Token (Legacy)").grid(row=1, column=0, sticky=tk.W, **pad)
        self.token_entry = ttk.Entry(config, textvariable=self.token, show="•", width=60)
        self.token_entry.grid(row=1, column=1, sticky=tk.EW, **pad)

        ttk.Label(config, text="Registration Secret").grid(row=2, column=0, sticky=tk.W, **pad)
        self.registration_secret_entry = ttk.Entry(
            config, textvariable=self.registration_secret, show="•", width=60
        )
        self.registration_secret_entry.grid(row=2, column=1, sticky=tk.EW, **pad)

        ttk.Label(config, text="Client-ID").grid(row=3, column=0, sticky=tk.W, **pad)
        self.client_id_entry = ttk.Entry(config, textvariable=self.client_id, width=60)
        self.client_id_entry.grid(row=3, column=1, sticky=tk.EW, **pad)

        ttk.Label(config, text="LM Studio URL").grid(row=4, column=0, sticky=tk.W, **pad)
        self.target_entry = ttk.Entry(config, textvariable=self.target, width=60)
        self.target_entry.grid(row=4, column=1, sticky=tk.EW, **pad)

        session = ttk.LabelFrame(control_area, text="Cursor (after start)", padding=8)
        session.pack(fill=tk.X, pady=(0, 8))
        session.columnconfigure(1, weight=1)
        add_copyable_field(session, 0, "Base URL", self.cursor_base_url, pad=pad)
        add_copyable_field(session, 1, "API Key", self.cursor_api_key, pad=pad)
        config.columnconfigure(1, weight=1)

        controls = ttk.Frame(control_area, padding=(0, 4))
        controls.pack(fill=tk.X)

        self.start_btn = ttk.Button(controls, text="Start", command=self._start_client)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.stop_btn = ttk.Button(
            controls, text="Stop", command=self._stop_client, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 16))

        ttk.Checkbutton(
            controls,
            text="Logging enabled",
            variable=self.logging_enabled,
        ).pack(side=tk.LEFT)

        ttk.Label(controls, textvariable=self.status).pack(side=tk.RIGHT)

        log_area = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        log_area.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        log_pane = ttk.PanedWindow(log_area, orient=tk.VERTICAL)
        log_pane.pack(fill=tk.BOTH, expand=True)

        system_frame = ttk.LabelFrame(log_pane, text="System log", padding=8)
        log_pane.add(system_frame)

        self.system_log_text = scrolledtext.ScrolledText(
            system_frame,
            state=tk.DISABLED,
            wrap=tk.WORD,
            height=6,
            font=("Menlo", 11),
        )
        self.system_log_text.pack(fill=tk.BOTH, expand=True)

        request_frame = ttk.LabelFrame(log_pane, text="Request log (LM Studio)", padding=8)
        log_pane.add(request_frame)

        self.request_log_text = scrolledtext.ScrolledText(
            request_frame,
            state=tk.DISABLED,
            wrap=tk.WORD,
            height=10,
            font=("Menlo", 11),
        )
        self.request_log_text.pack(fill=tk.BOTH, expand=True)

        payload_frame = ttk.LabelFrame(log_pane, text="Payload (Request)", padding=8)
        log_pane.add(payload_frame)

        self.payload_log_text = scrolledtext.ScrolledText(
            payload_frame,
            state=tk.DISABLED,
            wrap=tk.WORD,
            height=6,
            font=("Menlo", 10),
        )
        self.payload_log_text.pack(fill=tk.BOTH, expand=True)

        response_frame = ttk.LabelFrame(log_pane, text="Response", padding=8)
        log_pane.add(response_frame)

        self.response_log_text = scrolledtext.ScrolledText(
            response_frame,
            state=tk.DISABLED,
            wrap=tk.WORD,
            height=8,
            font=("Menlo", 10),
        )
        self.response_log_text.pack(fill=tk.BOTH, expand=True)

        self._log_pane = log_pane
        self.root.after_idle(self._init_log_pane_split)

    def _configure_logging(self) -> None:
        level = os.environ.get("TUNNEL_LOG_LEVEL", "INFO")
        log_file = os.environ.get("TUNNEL_CLIENT_LOG", "client.log")
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
            level,
            log_file,
            to_console=False,
            to_file=True,
            extra_handlers=[system_handler, request_handler, payload_handler, response_handler],
        )

    def _init_log_pane_split(self) -> None:
        """Balance log panels once the window is laid out."""
        try:
            height = self._log_pane.winfo_height()
            if height > 160:
                self._log_pane.sashpos(0, max(100, height // 5))
                self._log_pane.sashpos(1, max(180, height * 2 // 5))
                self._log_pane.sashpos(2, max(260, height * 3 // 5))
        except tk.TclError:
            pass

    def _set_config_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in (
            self.server_url_entry,
            self.token_entry,
            self.registration_secret_entry,
            self.client_id_entry,
            self.target_entry,
        ):
            widget.configure(state=state)

    def _validate_config(self) -> bool:
        if not self.server_url.get().strip():
            messagebox.showerror("Error", "Server URL is required.")
            return False
        if not self.registration_secret.get().strip() and not self.token.get().strip():
            messagebox.showerror(
                "Error",
                "Registration secret (multi-client) or token (legacy) is required.",
            )
            return False
        return True

    def _on_registered(self, payload: dict) -> None:
        def update() -> None:
            self.cursor_base_url.set(payload.get("api_base_url", "—"))
            self.cursor_api_key.set(payload.get("proxy_token", "—"))

        self.root.after(0, update)

    def _start_client(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self._validate_config():
            return

        self.status.set("Running…")
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self._set_config_enabled(False)

        client = TunnelClient(
            self.server_url.get().strip(),
            self.target.get().strip(),
            token=self.token.get().strip(),
            client_id=self.client_id.get().strip(),
            registration_secret=self.registration_secret.get().strip(),
            public_api_base=os.environ.get("TUNNEL_PUBLIC_API_BASE", ""),
            on_registered=self._on_registered,
        )

        def run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            task = loop.create_task(client.run_forever())
            self._client_task = task
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                log.info("Client stopped.")
            finally:
                loop.close()
                self._loop = None
                self._client_task = None
                self.root.after(0, self._on_client_stopped)

        self._thread = threading.Thread(target=run_loop, name="tunnel-client", daemon=True)
        self._thread.start()

    def _stop_client(self) -> None:
        if self._loop is None or self._client_task is None:
            return
        self.status.set("Stopping…")
        self._loop.call_soon_threadsafe(self._client_task.cancel)

    def _on_client_stopped(self) -> None:
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self._set_config_enabled(True)
        self.status.set("Disconnected")

    def _on_close(self) -> None:
        if self._loop is not None:
            self._stop_client()
            if self._thread is not None:
                self._thread.join(timeout=3)
        self.root.destroy()


def main() -> None:
    """Run the tunnel client desktop GUI."""
    root = tk.Tk()
    TunnelClientApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
