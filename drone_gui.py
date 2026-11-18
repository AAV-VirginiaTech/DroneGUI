"""Simple GUI for running Linux commands on the drone over SSH.

Each button runs a command on the drone. The SSH connection is kept open in
the background and is re-established automatically if it drops.
"""

import json
import queue
import threading
import time
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import paramiko

# --- Drone connection settings ---------------------------------------------
HOST = "192.168.144.30"
USERNAME = "aav"
PASSWORD = "Aav"
PORT = 22

RECONNECT_CHECK_SECONDS = 2   # how often to check the connection
KEEPALIVE_SECONDS = 5         # SSH keepalive interval
CONNECT_TIMEOUT_SECONDS = 5

BUTTONS_FILE = Path(__file__).with_name("buttons.json")
DEFAULT_BUTTONS = [
    {"title": "Uptime", "command": "uptime"},
    {"title": "Disk Usage", "command": "df -h"},
    {"title": "Network Info", "command": "ip a"},
]


class DroneSSH:
    """Holds one SSH connection and reconnects whenever it is lost."""

    def __init__(self, on_status, on_log):
        self._on_status = on_status
        self._on_log = on_log
        self._client = None
        self._last_error = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def is_connected(self):
        client = self._client
        if client is None:
            return False
        transport = client.get_transport()
        return transport is not None and transport.is_active()

    def _connect(self):
        with self._lock:
            if self.is_connected():
                return True
            if self._client is not None:
                self._client.close()
                self._client = None
            self._on_status("connecting")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    HOST,
                    port=PORT,
                    username=USERNAME,
                    password=PASSWORD,
                    timeout=CONNECT_TIMEOUT_SECONDS,
                    banner_timeout=CONNECT_TIMEOUT_SECONDS,
                    auth_timeout=CONNECT_TIMEOUT_SECONDS,
                    look_for_keys=False,
                    allow_agent=False,
                )
            except Exception as e:
                client.close()
                self._on_status("disconnected")
                if str(e) != self._last_error:  # don't spam the same error every retry
                    self._last_error = str(e)
                    self._on_log(f"[connect failed: {e}, will keep retrying]\n")
                return False
            client.get_transport().set_keepalive(KEEPALIVE_SECONDS)
            self._client = client
            self._last_error = None
            self._on_status("connected")
            self._on_log(f"[connected to {USERNAME}@{HOST}]\n")
            return True

    def _watchdog(self):
        while not self._stop.is_set():
            if not self.is_connected():
                if self._client is not None:
                    self._on_log("[connection lost, reconnecting...]\n")
                self._connect()
            self._stop.wait(RECONNECT_CHECK_SECONDS)

    def run(self, command):
        """Run a command, streaming output to the log. Runs in a worker thread."""
        for attempt in range(2):
            if not self._connect():
                return
            try:
                _, stdout, _ = self._client.exec_command(command, get_pty=True)
                for line in iter(stdout.readline, ""):
                    self._on_log(line)
                code = stdout.channel.recv_exit_status()
                self._on_log(f"[exit code {code}]\n\n")
                return
            except Exception as e:
                self._on_log(f"[error: {e}]\n")
                if attempt == 0:
                    self._on_log("[retrying after reconnect...]\n")
                    time.sleep(1)

    def close(self):
        self._stop.set()
        if self._client is not None:
            self._client.close()


def load_buttons():
    if BUTTONS_FILE.exists():
        try:
            return json.loads(BUTTONS_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return list(DEFAULT_BUTTONS)


def save_buttons(buttons):
    BUTTONS_FILE.write_text(json.dumps(buttons, indent=2))


class DroneGUI:
    # Status pill: (text color, background color)
    STATUS_STYLE = {
        "connected": ("#3fb950", "#12261a"),
        "connecting": ("#d29922", "#2b2111"),
        "disconnected": ("#f85149", "#2d1517"),
    }
    COLUMNS = 3

    def __init__(self, root):
        self.root = root
        root.title("Drone Control")
        root.geometry("860x640")
        root.minsize(620, 480)

        self.events = queue.Queue()
        self.buttons = load_buttons()

        # --- Header: title, host, status, add button ---
        header = ctk.CTkFrame(root, corner_radius=12)
        header.pack(fill="x", padx=16, pady=(16, 8))

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=16, pady=12)
        ctk.CTkLabel(titles, text="Drone Control",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(titles, text=f"{USERNAME}@{HOST}:{PORT}", text_color="gray60",
                     font=ctk.CTkFont(size=13)).pack(anchor="w")

        ctk.CTkButton(header, text="+  Add Command", width=140, height=36,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self.add_button_dialog).pack(side="right", padx=16)
        self.status_label = ctk.CTkLabel(header, text="", corner_radius=14, height=30,
                                         font=ctk.CTkFont(size=13, weight="bold"))
        self.status_label.pack(side="right", padx=(0, 4))
        self.set_status("disconnected")

        # --- Command buttons ---
        section = ctk.CTkFrame(root, fg_color="transparent")
        section.pack(fill="x", padx=20, pady=(8, 0))
        ctk.CTkLabel(section, text="COMMANDS", text_color="gray60",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        ctk.CTkLabel(section, text="right-click a command to delete it", text_color="gray45",
                     font=ctk.CTkFont(size=12)).pack(side="right")

        self.button_frame = ctk.CTkFrame(root, fg_color="transparent")
        self.button_frame.pack(fill="x", padx=12, pady=(4, 8))

        # --- Output console ---
        out_card = ctk.CTkFrame(root, corner_radius=12)
        out_card.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        out_bar = ctk.CTkFrame(out_card, fg_color="transparent")
        out_bar.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(out_bar, text="OUTPUT", text_color="gray60",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        ctk.CTkButton(out_bar, text="Clear", width=70, height=26, fg_color="transparent",
                      border_width=1, border_color="gray40", text_color=("gray20", "gray80"),
                      hover_color=("gray80", "gray25"),
                      command=lambda: self.output.delete("1.0", "end")).pack(side="right")

        self.output = ctk.CTkTextbox(out_card, corner_radius=8, fg_color="#0d1117",
                                     text_color="#c9d1d9", wrap="word",
                                     font=ctk.CTkFont(family="Consolas", size=13))
        self.output.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.output.tag_config("cmd", foreground="#58a6ff")
        self.output.tag_config("ok", foreground="#3fb950")
        self.output.tag_config("err", foreground="#f85149")
        self.output.tag_config("meta", foreground="#8b949e")

        self.render_buttons()

        self.ssh = DroneSSH(
            on_status=lambda s: self.events.put(("status", s)),
            on_log=lambda t: self.events.put(("log", t)),
        )
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.process_events()

    # --- UI updates (main thread only) ---
    def set_status(self, status):
        fg, bg = self.STATUS_STYLE[status]
        self.status_label.configure(text=f"  ●  {status.capitalize()}  ",
                                    text_color=fg, fg_color=bg)

    @staticmethod
    def tag_for(line):
        if line.startswith("$ "):
            return "cmd"
        if line.startswith(("[connected", "[exit code 0]")):
            return "ok"
        if line.startswith(("[error", "[connect failed", "[connection lost", "[exit code")):
            return "err"
        if line.startswith("["):
            return "meta"
        return None

    def process_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.set_status(value)
                elif kind == "log":
                    tag = self.tag_for(value)
                    self.output.insert("end", value, tag)
                    self.output.see("end")
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)

    def render_buttons(self):
        for child in self.button_frame.winfo_children():
            child.destroy()
        for i, b in enumerate(self.buttons):
            btn = ctk.CTkButton(self.button_frame, text=b["title"], height=56,
                                corner_radius=10, fg_color=("gray85", "#21262d"),
                                hover_color=("gray75", "#30363d"),
                                border_width=1, border_color=("gray70", "#30363d"),
                                text_color=("gray10", "gray95"),
                                font=ctk.CTkFont(size=15, weight="bold"),
                                command=lambda b=b: self.run_button(b))
            btn.grid(row=i // self.COLUMNS, column=i % self.COLUMNS,
                     sticky="ew", padx=4, pady=4)
            btn.bind("<Button-3>", lambda e, i=i: self.delete_button(i))
        for c in range(self.COLUMNS):
            self.button_frame.columnconfigure(c, weight=1, uniform="cmd")

    # --- Actions ---
    def run_button(self, b):
        self.events.put(("log", f"$ {b['command']}   # {b['title']}\n"))
        threading.Thread(target=self.ssh.run, args=(b["command"],), daemon=True).start()

    def add_button_dialog(self):
        win = ctk.CTkToplevel(self.root)
        win.title("Add Command")
        win.geometry("440x270")
        win.resizable(False, False)
        win.transient(self.root)

        ctk.CTkLabel(win, text="New command button",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=20,
                                                                    pady=(18, 10))
        ctk.CTkLabel(win, text="Button title", text_color="gray60").pack(anchor="w", padx=20)
        title_entry = ctk.CTkEntry(win, placeholder_text="e.g. Hostname", height=34)
        title_entry.pack(fill="x", padx=20, pady=(2, 10))
        ctk.CTkLabel(win, text="Linux command", text_color="gray60").pack(anchor="w", padx=20)
        cmd_entry = ctk.CTkEntry(win, placeholder_text="e.g. hostname", height=34,
                                 font=ctk.CTkFont(family="Consolas", size=13))
        cmd_entry.pack(fill="x", padx=20, pady=(2, 14))

        def save():
            title = title_entry.get().strip()
            command = cmd_entry.get().strip()
            if not title or not command:
                messagebox.showwarning("Missing info", "Enter both a title and a command.",
                                       parent=win)
                return
            self.buttons.append({"title": title, "command": command})
            save_buttons(self.buttons)
            self.render_buttons()
            win.destroy()

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(fill="x", padx=20)
        ctk.CTkButton(actions, text="Save", width=90, command=save).pack(side="right")
        ctk.CTkButton(actions, text="Cancel", width=90, fg_color="transparent",
                      border_width=1, border_color="gray40", text_color=("gray20", "gray80"),
                      hover_color=("gray80", "gray25"),
                      command=win.destroy).pack(side="right", padx=8)
        win.bind("<Return>", lambda e: save())
        win.bind("<Escape>", lambda e: win.destroy())

        # CTkToplevel needs a moment to appear before it can grab focus
        def focus_dialog():
            win.lift()
            win.grab_set()
            title_entry.focus()
        win.after(150, focus_dialog)

    def delete_button(self, index):
        b = self.buttons[index]
        if messagebox.askyesno("Delete command", f"Delete the \"{b['title']}\" button?"):
            del self.buttons[index]
            save_buttons(self.buttons)
            self.render_buttons()

    def on_close(self):
        self.ssh.close()
        self.root.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    DroneGUI(root)
    root.mainloop()
