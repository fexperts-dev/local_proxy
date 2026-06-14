"""Shared Tkinter helpers for the local_proxy GUI."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk


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
        or (" -> " in msg and record.name == "local_proxy.proxy")
    )


def _is_payload_log(record: logging.LogRecord) -> bool:
    msg = record.getMessage()
    return msg.startswith("Payload model=") or msg.startswith("Payload request ")


def _is_response_log(record: logging.LogRecord) -> bool:
    return record.getMessage().startswith("Payload response ")


class _RequestLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return _is_request_log(record)


class _SystemLogFilter(logging.Filter):
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
