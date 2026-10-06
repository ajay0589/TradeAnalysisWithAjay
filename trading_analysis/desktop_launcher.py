from __future__ import annotations

import argparse
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import webbrowser

from trading_analysis.server_control import ROOT, NO_WINDOW, ServerController, safe_text


def support_log(controller):
    lines = [f"Trading Desk support log | {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Project: {controller.root}", f"Python: {controller.python}", f"Port: {controller.port}"]
    for name in ("launcher.log", "launcher-bootstrap.log", f"web_{controller.port}.err.log", f"web_{controller.port}.out.log"):
        lines.append(f"\n--- {name} (last 64 KB) ---")
        path = controller.logs / name
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 65536))
                lines.append(handle.read().decode("utf-8", errors="replace"))
        except FileNotFoundError:
            lines.append("No log yet.")
    return safe_text("\n".join(lines))


class Launcher:
    def __init__(self, window, port=8766, root=ROOT):
        import tkinter as tk
        from tkinter import ttk

        self.window, self.root = window, root
        self.events = queue.Queue()
        self.busy = False
        self.checking = False
        self.status_generation = 0
        self.current = {"state": "unknown"}
        self.port = tk.StringVar(value=str(port))
        self.status = tk.StringVar(value="Checking server...")
        self.details = tk.StringVar(value="")
        window.title("Trading Desk Control")
        window.geometry("790x530")
        window.minsize(690, 450)
        style = ttk.Style(window)
        style.theme_use("clam")
        style.configure("TFrame", background="#f5f6f8")
        style.configure("TLabel", background="#f5f6f8", foreground="#20262d", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("State.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8))
        frame = ttk.Frame(window, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(6, weight=1)
        ttk.Label(frame, text="Trading Desk", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        project_label = ttk.Label(frame, text=str(root), wraplength=710)
        project_label.grid(row=1, column=0, sticky="w", pady=(4, 18))
        state_row = ttk.Frame(frame)
        state_row.grid(row=2, column=0, sticky="ew")
        state_row.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(state_row, textvariable=self.status, style="State.TLabel")
        self.status_label.grid(row=0, column=0, sticky="w")
        ttk.Label(state_row, text="Port").grid(row=0, column=1, padx=(12, 6))
        self.port_input = ttk.Spinbox(state_row, from_=1024, to=65535, width=7, textvariable=self.port)
        self.port_input.grid(row=0, column=2)
        details_label = ttk.Label(frame, textvariable=self.details, wraplength=710)
        details_label.grid(row=3, column=0, sticky="w", pady=(5, 16))
        frame.bind("<Configure>", lambda event: [label.configure(wraplength=max(240, event.width - 44))
                                                 for label in (project_label, details_label)])
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, sticky="ew")
        self.buttons = {}
        for col, (key, label, command) in enumerate((
            ("start", "Start Server", lambda: self.run("start")),
            ("stop", "Stop Server", lambda: self.confirm("stop")),
            ("open", "Open App", self.open_app),
            ("update", "Update App", lambda: self.confirm("update")),
        )):
            actions.columnconfigure(col, weight=1)
            button = ttk.Button(actions, text=label, command=command)
            button.grid(row=0, column=col, sticky="ew", padx=(0, 6 if col < 3 else 0))
            self.buttons[key] = button
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=5, column=0, sticky="ew", pady=(16, 10))
        log_frame = ttk.Frame(frame)
        log_frame.grid(row=6, column=0, sticky="nsew")
        self.output = tk.Text(log_frame, wrap="word", height=10, font=("Consolas", 10),
                              relief="solid", borderwidth=1, background="white", foreground="#20262d", state="disabled")
        self.output.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.output.yview)
        scroll.pack(side="right", fill="y")
        self.output.configure(yscrollcommand=scroll.set)
        footer = ttk.Frame(frame)
        footer.grid(row=7, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(footer, text="View Logs", command=self.view_logs).pack(side="left")
        ttk.Button(footer, text="Save Support Log", command=self.save_log).pack(side="left", padx=8)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(100, self.drain)
        self.window.after(100, self.refresh)

    def controller(self):
        return ServerController(root=self.root, port=self.port.get(), emit=lambda text: self.events.put(("log", text)))

    def append(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", safe_text(str(text)) + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def refresh(self):
        if not self.busy and not self.checking:
            try:
                controller = self.controller()
            except (ValueError, RuntimeError):
                self.status.set("Enter a valid port")
                for button in self.buttons.values():
                    button.configure(state="disabled")
            else:
                self.checking = True
                generation = self.status_generation
                def check():
                    try:
                        self.events.put(("status", (controller.port, generation, controller.status())))
                    except Exception as exc:
                        self.events.put(("log", str(exc)))
                    finally:
                        self.events.put(("checked", None))
                threading.Thread(target=check, daemon=True).start()
        self.window.after(3000, self.refresh)

    def show_status(self, result):
        self.current = result
        state = result["state"]
        self.status.set({"running": "Server running", "stopped": "Server stopped", "occupied": "Port needs attention"}.get(state, state))
        self.status_label.configure(foreground={"running": "#167347", "stopped": "#555c64", "occupied": "#ad3b30"}.get(state, "#20262d"))
        health = result.get("health", {})
        self.details.set(f"{result.get('url', '')}   |   PID {health.get('pid')}   |   Build {health.get('code_version', '-')}" if state == "running"
                         else result.get("message", result.get("url", "")))
        self.buttons["start"].configure(state="normal" if state == "stopped" else "disabled")
        for key in ("stop", "open"):
            self.buttons[key].configure(state="normal" if state == "running" else "disabled")
        self.buttons["update"].configure(state="disabled" if state == "occupied" else "normal")

    def drain(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self.append(data)
                elif kind == "checked":
                    self.checking = False
                elif kind == "status":
                    if not self.busy and str(data[0]) == self.port.get() and data[1] == self.status_generation:
                        self.show_status(data[2])
                elif kind == "done":
                    result, error, close_after = data
                    self.busy = False
                    self.progress.stop()
                    self.port_input.configure(state="normal")
                    self.show_status(result)
                    if error:
                        from tkinter import messagebox
                        messagebox.showerror("Action could not finish", error, parent=self.window)
                    elif close_after:
                        self.window.destroy()
                        return
        except queue.Empty:
            pass
        self.window.after(100, self.drain)

    def confirm(self, action):
        from tkinter import messagebox
        message = ("Stop the server and all its scanners? Stored data and trade history will be kept." if action == "stop"
                   else "Update from origin/scanner-audit-and-v4 and install dependencies?\n\nThe server will stop during the update, then restart if it was running. Scanners must be started again afterward. Local file changes will not be overwritten.")
        if messagebox.askyesno("Stop Server" if action == "stop" else "Update App", message, parent=self.window):
            self.run(action)

    def run(self, action, close_after=False):
        if self.busy:
            return
        from tkinter import messagebox
        try:
            controller = self.controller()
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Invalid port", str(exc), parent=self.window)
            return
        self.busy = True
        self.status_generation += 1
        self.status.set({"start": "Starting server...", "stop": "Stopping server...", "update": "Updating app..."}[action])
        for button in self.buttons.values():
            button.configure(state="disabled")
        self.port_input.configure(state="disabled")
        self.progress.start(12)
        def work():
            error = None
            try:
                result = getattr(controller, action)()
            except Exception as exc:
                error = safe_text(str(exc))
                controller.say(error)
                try:
                    result = controller.status()
                except Exception:
                    result = {"state": "unknown", "message": "Check View Logs for details."}
            self.events.put(("done", (result, error, close_after)))
        threading.Thread(target=work, daemon=True).start()

    def open_app(self):
        from tkinter import messagebox
        try:
            webbrowser.open(self.controller().url)
        except Exception as exc:
            messagebox.showerror("Cannot open app", str(exc), parent=self.window)

    def view_logs(self):
        try:
            self.append(support_log(self.controller()))
        except Exception as exc:
            self.append(str(exc))

    def save_log(self):
        from tkinter import filedialog, messagebox
        filename = filedialog.asksaveasfilename(parent=self.window, defaultextension=".txt", filetypes=[("Text log", "*.txt")],
                                               initialfile=f"trading-desk-support-{time.strftime('%Y-%m-%d')}.txt")
        if filename:
            try:
                Path(filename).write_text(support_log(self.controller()), encoding="utf-8")
                self.append(f"Support log saved: {filename}")
            except Exception as exc:
                messagebox.showerror("Save failed", str(exc), parent=self.window)

    def close(self):
        from tkinter import messagebox
        if self.busy:
            messagebox.showinfo("Action in progress", "Wait for the current action to finish before closing.", parent=self.window)
            return
        answer = messagebox.askyesnocancel("Close Trading Desk Control", "Stop the server before closing?\n\nYes: stop server and close.\nNo: leave the server running.", parent=self.window)
        if answer is True:
            self.run("stop", close_after=True)
        elif answer is False:
            self.window.destroy()


def main():
    parser = argparse.ArgumentParser(description="Trading Desk desktop launcher")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    if args.detach:
        logs = ROOT / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        python = str(pythonw if pythonw.exists() else sys.executable)
        with (logs / "launcher-bootstrap.log").open("ab") as handle:
            subprocess.Popen([python, "-m", "trading_analysis.desktop_launcher", "--port", str(args.port)],
                             cwd=ROOT, stdout=handle, stderr=handle, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return
    try:
        import tkinter as tk
        window = tk.Tk()
        if args.smoke_test:
            window.withdraw()
        launcher = Launcher(window, args.port)
        if args.smoke_test:
            window.update_idletasks()
            assert len(launcher.buttons) == 4
            window.destroy()
            print("Launcher window and controls initialized successfully.")
            return
        window.mainloop()
    except Exception as exc:
        if os.name == "nt" and not args.smoke_test:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"Trading Desk could not open.\n{exc}\n\nCheck logs/launcher-bootstrap.log. Python must include Tcl/Tk.", "Trading Desk", 16)
        raise


if __name__ == "__main__":
    main()
