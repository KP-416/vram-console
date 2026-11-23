import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import psutil

import control as ctl
from gputaskman.dxgi import enumerate_adapters
from gputaskman.pdh import GpuCounters


BASE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
TITLE = "显存控制台"
GIB = 1024 ** 3


def memory(value):
    return "—" if value is None else f"{value / 1024 ** 2:,.0f} MiB"


class Console:
    def __init__(self, root, directory):
        self.root = root
        self.directory = directory
        self.store = directory / "profiles.json"
        self.rows = ctl.read(self.store) if self.store.exists() else []
        self.gpus = [a for a in enumerate_adapters() if not a.software and not a.root_display]
        if not self.gpus:
            raise RuntimeError("Windows 未返回可用的 GPU 适配器。")
        self.counters = GpuCounters()
        self.events = queue.Queue()
        self.live = {}
        self.services = {}
        self.sampling = self.acting = False
        root.title(TITLE)
        root.geometry("960x735")
        root.minsize(760, 590)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=3)
        root.rowconfigure(6, weight=2)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Treeview", rowheight=28, background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("TProgressbar", background="#297565")
        self.gpu = ttk.Combobox(root, state="readonly", values=[g.name for g in self.gpus], width=37)
        self.gpu.current(max(range(len(self.gpus)), key=lambda i: self.gpus[i].dedicated_vram))
        self.gpu.grid(row=0, column=0, sticky="w", padx=20, pady=(18, 4))
        self.usage = ttk.Label(root, font=("Segoe UI", 12))
        self.usage.grid(row=0, column=0, sticky="e", padx=20)
        self.bar = ttk.Progressbar(root, maximum=100)
        self.bar.grid(row=1, column=0, sticky="ew", padx=20, pady=5)

        heading = ttk.Frame(root)
        heading.grid(row=2, column=0, sticky="ew", padx=20, pady=(14, 7))
        ttk.Label(heading, text="实时显存占用", style="Heading.TLabel").pack(side="left")
        self.search = tk.StringVar()
        ttk.Entry(heading, textvariable=self.search, width=22).pack(side="right")
        ttk.Label(heading, text="筛选名称 / PID  ").pack(side="right")
        self.processes = self.table(3, [("name", "占用程序", 260), ("pid", "PID", 80),
                                        ("vram", "专用显存", 125), ("path", "程序位置", 350)])
        self.processes.bind("<Double-1>", self.open_process)
        actions = ttk.Frame(root)
        actions.grid(row=4, column=0, sticky="ew", padx=20, pady=(6, 10))
        self.button(actions, "记住启动方式…", lambda: self.capture(False))
        self.button(actions, "保存并停止…", lambda: self.capture(True))
        ttk.Label(root, text="已保存的启动项", style="Heading.TLabel").grid(row=5, column=0, sticky="w", padx=20, pady=5)
        self.saved = self.table(6, [("name", "名称", 260), ("state", "进程状态", 110),
                                   ("mem", "进程树显存", 140), ("pid", "启动进程 PID", 120)])
        actions = ttk.Frame(root)
        actions.grid(row=7, column=0, sticky="ew", padx=20, pady=8)
        self.button(actions, "启动", self.start_saved)
        self.button(actions, "停止…", self.stop_saved)
        self.button(actions, "编辑…", self.edit_saved)
        self.button(actions, "手动添加…", self.manual)
        self.button(actions, "移除记录…", self.forget)
        self.note = tk.StringVar(value="选择正在占用显存的程序，保存后即可从下方重新启动。")
        ttk.Label(root, textvariable=self.note, wraplength=860).grid(row=8, column=0, sticky="w", padx=20, pady=(2, 14))
        self.tick()

    def table(self, row, specs):
        frame = ttk.Frame(self.root)
        frame.grid(row=row, column=0, sticky="nsew", padx=20)
        tree = ttk.Treeview(frame, columns=[s[0] for s in specs], show="headings", selectmode="browse")
        for key, name, width in specs:
            tree.heading(key, text=name, anchor="w")
            tree.column(key, width=width, minwidth=55, anchor="w")
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        return tree

    def button(self, parent, text, action):
        def run():
            try:
                if not self.acting:
                    action()
            except Exception as exc:
                messagebox.showerror(TITLE, str(exc), parent=self.root)
        ttk.Button(parent, text=text, command=run).pack(side="left", padx=(0, 7))

    def sample(self, rows, adapter):
        try:
            pages, devices = self.counters.sample()
            metrics = pages.get(adapter.luid, {})
            usage = getattr(devices.get(adapter.luid), "dedicated", None)
            live = {}
            for pid, sample in metrics.items():
                size = sample.shown_vram
                if size is None:
                    size = sample.wddm_dedicated_usage
                if not size:
                    continue
                try:
                    p = psutil.Process(pid)
                    name = p.name()
                    try:
                        path = p.exe()
                        if name.lower() in ("python.exe", "pythonw.exe", "node.exe", "pwsh.exe", "powershell.exe"):
                            script = next((Path(a).name for a in p.cmdline()[1:] if a.lower().endswith((".py", ".js", ".ps1"))), "")
                            if script:
                                name += " · " + script
                    except psutil.AccessDenied:
                        path = "权限受限"
                    live[pid] = (p, name, size, path)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            services = {}
            for r, roots in zip(rows, ctl.matches(rows)):
                ids = {p.pid for root in roots for p in ctl.tree(root)}
                observed = [live[i][2] for i in ids if i in live]
                services[r["id"]] = (roots, sum(observed) if observed else None)
            self.events.put(("sample", (adapter, usage, live, services)))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def sync(self, widget, entries):
        keys = {k for k, _ in entries}
        for old in widget.get_children():
            if old not in keys:
                widget.delete(old)
        for pos, (key, values) in enumerate(entries):
            if widget.exists(key):
                widget.item(key, values=values)
                widget.move(key, "", pos)
            else:
                widget.insert("", pos, key, values=values)

    def tick(self):
        while not self.events.empty():
            kind, data = self.events.get()
            if kind == "sample":
                self.sampling = False
                adapter, used, self.live, self.services = data
                self.usage["text"] = f"{used / GIB:.2f} / {adapter.dedicated_vram / GIB:.2f} GiB" if used is not None else "显存计数暂不可用"
                self.bar["value"] = 100 * (used or 0) / max(1, adapter.dedicated_vram)
                keyword = self.search.get().lower()
                live = sorted(self.live.values(), key=lambda x: -x[2])
                self.sync(self.processes, [(self.process_key(p), (name, p.pid, memory(size), path))
                          for p, name, size, path in live if keyword in (name + " " + str(p.pid)).lower()])
                items = []
                for r in self.rows:
                    roots, size = self.services.get(r["id"], ([], None))
                    status = "运行中" if len(roots) == 1 else f"{len(roots)} 个实例" if roots else "未运行"
                    items.append((r["id"], (r["name"], status, memory(size), ", ".join(str(p.pid) for p in roots))))
                self.sync(self.saved, items)
            elif kind == "error":
                self.sampling = False
                self.note.set("采样失败 · " + data)
            else:
                self.acting = False
                self.note.set(data)
                if kind == "failed":
                    messagebox.showerror(TITLE, data, parent=self.root)
        if not self.sampling:
            self.sampling = True
            threading.Thread(target=self.sample, args=(list(self.rows), self.gpus[self.gpu.current()]), daemon=True).start()
        self.root.after(1000, self.tick)

    @staticmethod
    def process_key(p):
        return f"{p.pid}@{p.create_time()}"

    def persist(self, rows):
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf8")
        os.replace(tmp, self.store)
        self.rows = rows

    def selected(self):
        ids = self.saved.selection()
        for row in self.rows:
            if ids and row["id"] == ids[0]:
                return row
        raise ValueError("请先选择一个已保存的启动项。")

    def capture(self, stopping):
        ids = self.processes.selection()
        if not ids:
            raise ValueError("请先选择一个显存进程。")
        p = self.live[int(ids[0].split("@")[0])][0]
        if self.process_key(p) != ids[0] or not p.is_running():
            raise ValueError("所选进程已经退出。")
        launch = ctl.capture_root(p)
        self.editor(ctl.captured(launch), target=launch, stopping=stopping)

    def open_process(self, event):
        if self.acting or not self.processes.identify_row(event.y):
            return
        try:
            self.capture(False)
        except Exception as exc:
            messagebox.showerror(TITLE, str(exc), parent=self.root)

    def editor(self, row, target=None, stopping=False):
        window = tk.Toplevel(self.root)
        window.title("保存并停止" if stopping else "启动方式")
        window.transient(self.root)
        window.grab_set()
        window.columnconfigure(1, weight=1)
        fields = {}
        for i, (key, label, initial) in enumerate([("name", "显示名称", row["name"]),
                ("exe", "可执行程序", row["args"][0]), ("args", "启动参数", subprocess.list2cmdline(row["args"][1:])),
                ("cwd", "工作目录", row["cwd"])]):
            ttk.Label(window, text=label).grid(row=i, column=0, padx=14, pady=8, sticky="w")
            fields[key] = tk.StringVar(value=initial)
            ttk.Entry(window, width=72, textvariable=fields[key]).grid(row=i, column=1, sticky="ew", padx=4)
        def browse(key):
            result = filedialog.askopenfilename(parent=window, filetypes=[("Windows 程序", "*.exe")]) if key=="exe" else filedialog.askdirectory(parent=window)
            if result:
                fields[key].set(result)
        for i, key in [(1, "exe"), (3, "cwd")]:
            ttk.Button(window, text="浏览", width=5, command=lambda k=key: browse(k)).grid(row=i, column=2, padx=6)
        info = "请核对启动入口和目录。程序当前目录可能已改变，启动脚本设置的临时环境变量需由原脚本恢复。"
        if stopping:
            info += f" 保存后将结束 PID {target.pid} 和它的子进程；正在执行的任务会中断。"
        ttk.Label(window, text=info, wraplength=590).grid(row=4, column=0, columnspan=3, sticky="w", padx=14, pady=10)
        def save():
            try:
                raw = fields["args"].get()
                args = [fields["exe"].get().strip().strip('"'), *ctl.split_command("app.exe " + raw)[1:]]
                record = dict(row, name=fields["name"].get().strip(), args=args, cwd=fields["cwd"].get().strip().strip('"'))
                ctl.validate(record)
                duplicate = next((r for r in self.rows if ctl.signature(r) == ctl.signature(record)), None)
                if duplicate and duplicate["id"] != record["id"]:
                    if any(r["id"] == record["id"] for r in self.rows):
                        raise ValueError("相同命令已经登记。")
                    record["id"] = duplicate["id"]
                if stopping and target not in ctl.matches([record])[0]:
                    raise ValueError("修改后的命令与所选进程不同。请先单独保存启动项。")
                self.persist([r for r in self.rows if r["id"] != record["id"]] + [record])
                window.destroy()
                self.note.set("已保存 · " + record["name"])
                if stopping:
                    self.act(ctl.stop, record, target, self.directory)
            except Exception as exc:
                messagebox.showerror(TITLE, str(exc), parent=window)
        ttk.Button(window, text="保存并停止" if stopping else "保存", command=save).grid(row=5, column=1, pady=10, sticky="e")

    def act(self, fn, *args):
        self.acting = True
        self.note.set("正在处理 · " + args[0]["name"])
        def worker():
            try:
                self.events.put(("done", args[0]["name"] + " · " + fn(*args)))
            except Exception as exc:
                self.events.put(("failed", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def start_saved(self):
        self.act(ctl.start, self.selected(), self.directory)

    def stop_saved(self):
        row = self.selected()
        roots = ctl.matches([row])[0]
        if len(roots) != 1:
            raise ValueError("请选择实时列表中的具体实例。" if roots else "当前未找到该进程。")
        p = roots[0]
        if messagebox.askokcancel("停止进程", f'结束「{row["name"]}」及其子进程？\nPID {p.pid}，正在执行的任务会中断。'):
            self.act(ctl.stop, row, p, self.directory)

    def edit_saved(self):
        self.editor(self.selected())

    def manual(self):
        self.editor(ctl.profile("", [""], ""))

    def forget(self):
        row = self.selected()
        if messagebox.askyesno(TITLE, f'从列表移除「{row["name"]}」？运行中的程序会继续运行。'):
            self.persist([r for r in self.rows if r != row])


def main():
    # One writer per Windows login, including during manual stop/start.
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\GpuProcessConsole.c2a13")
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(0, "显存控制台已打开，可从任务栏切换。", TITLE, 64)
        return
    if getattr(sys, "frozen", False):
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    root = tk.Tk()
    try:
        app = Console(root, BASE / "显存控制台数据")
        root.mainloop()
    except Exception as exc:
        messagebox.showerror(TITLE, str(exc), parent=root)


if __name__ == "__main__":
    main()
