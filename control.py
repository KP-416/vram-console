import ctypes
import json
import os
import shutil
import uuid
import subprocess
import time
from pathlib import Path
import psutil


CURRENT_USER = psutil.Process().username()
ENGINE = Path(__file__).resolve().parent / "engine"


def split_command(value):
    from ctypes import wintypes as w
    if not value.strip():
        raise ValueError("请填写启动命令。")
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = (w.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    parse.restype = ctypes.POINTER(w.LPWSTR)
    count = ctypes.c_int()
    ptr = parse(value.strip(), ctypes.byref(count))
    if not ptr:
        raise ctypes.WinError()
    try:
        return ptr[:count.value]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(ptr, ctypes.c_void_p))


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def profile(name, args, cwd):
    return dict(id=uuid.uuid4().hex, name=name, args=list(map(str, args)), cwd=str(cwd))


def signature(row):
    return (os.path.normcase(os.path.normpath(row["args"][0])), tuple(row["args"][1:]),
            os.path.normcase(os.path.normpath(row["cwd"])))


def matches(rows):
    by_name = {}
    for i, row in enumerate(rows):
        by_name.setdefault(Path(row["args"][0]).name.lower(), []).append((i, signature(row)))
    groups = [[] for _ in rows]
    for p in psutil.process_iter(["name"]):
        choices = by_name.get((p.info["name"] or "").lower())
        if not choices:
            continue
        try:
            key = os.path.normcase(os.path.normpath(p.exe())), tuple(p.cmdline()[1:])
            for i, sig in choices:
                if key != sig[:2] or p.username() != CURRENT_USER:
                    continue
                cwd = os.path.normcase(os.path.normpath(p.cwd()))
                observed = rows[i].get("observed_cwd", rows[i]["cwd"])
                if cwd in (sig[2], os.path.normcase(os.path.normpath(observed))):
                    groups[i].append(p)
                    continue
                # Hosted applications may chdir after launch. The per-entry worker still identifies them.
                parent = p.parent()
                if parent and parent.name().lower() == "launchworker.exe" and Path(parent.exe()).parent.name == rows[i]["id"]:
                    groups[i].append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return groups


def tree(root):
    if not root.is_running():
        return []
    try:
        return [root] + root.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def validate(row):
    if not row["name"].strip() or not Path(row["args"][0]).is_absolute() or not Path(row["args"][0]).is_file():
        raise ValueError("请填写名称和可执行程序的完整路径。")
    if not Path(row["cwd"]).is_absolute() or not Path(row["cwd"]).is_dir():
        raise ValueError("工作目录不存在。")


def host_path(row, directory):
    ident = uuid.UUID(row["id"]).hex
    return directory / "hosts" / ident / "CommandTrayHost.exe"


def host_process(row, directory):
    target = host_path(row, directory)
    probe = profile("host", [target], target.parent)
    result = matches([probe])[0]
    return result[0] if len(result) == 1 else None


def end_processes(targets):
    for p in reversed(targets):
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(targets, timeout=4)
    if alive:
        raise RuntimeError("部分进程仍在运行，请刷新后核对。")


def start(row, directory):
    validate(row)
    if matches([row])[0]:
        return "已在运行"
    old = host_process(row, directory)
    if old:
        if any(p.name().lower() not in ("launchworker.exe", "conhost.exe") for p in tree(old)[1:]):
            raise RuntimeError("这个启动项仍有子进程，请先在托盘检查。")
        end_processes(tree(old))
    host = host_path(row, directory)
    folder = host.parent
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("CommandTrayHost.exe", "LaunchWorker.exe"):
        shutil.copyfile(ENGINE / name, folder / name)
    def write(name, data):
        (folder / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    write("launch.json", dict(exe=row["args"][0], arguments=subprocess.list2cmdline(row["args"][1:]), cwd=row["cwd"]))
    write("config.json", dict(configs=[dict(name=row["name"][:120], path=str(folder), cmd="LaunchWorker.exe",
          working_directory=str(folder), addition_env_path="", use_builtin_console=False, is_gui=True,
          enabled=True, start_show=False, kill_process_tree=True)], enable_cache=False, auto_update=False,
          enable_hotkey=False, enable_groups=False, lang="zh-CN"))
    error = folder / "launch-error.txt"
    error.unlink(missing_ok=True)
    subprocess.Popen([str(host)], cwd=folder, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(24):
        time.sleep(.25)
        if error.exists():
            raise RuntimeError(error.read_text(encoding="utf-8-sig"))
        if matches([row])[0]:
            return "已启动"
    raise RuntimeError("启动后未找到匹配的常驻进程。请检查启动参数、目录和托盘项目。")


def stop(row, expected, directory):
    if expected not in matches([row])[0] or not expected.is_running():
        raise RuntimeError("进程已变化，请刷新后重新选择。")
    host = host_process(row, directory)
    root = host if host and expected in tree(host) else expected
    end_processes(tree(root))
    return "已停止"


def capture_root(p):
    if not p.is_running() or p.username() != CURRENT_USER:
        raise RuntimeError("无法读取此进程的启动信息。")
    parent = p.parent()
    if (parent and p.name().lower() in ("python.exe", "pythonw.exe")
            and parent.name().lower() == p.name().lower()
            and parent.cmdline()[1:] == p.cmdline()[1:] and parent.username() == CURRENT_USER):
        return parent
    while parent and any(arg.startswith("--type=") for arg in p.cmdline()[1:]):
        if parent.exe().lower() != p.exe().lower() or parent.username() != CURRENT_USER:
            break
        p, parent = parent, parent.parent()
    return p


def captured(p):
    args = p.cmdline()
    if not args:
        raise RuntimeError("此进程没有可读取的启动参数。")
    script = next((Path(a).name for a in args[1:] if a.lower().endswith((".py", ".js", ".ps1"))), "")
    row = profile(script or p.name(), [p.exe(), *args[1:]], p.cwd())
    row["observed_cwd"] = row["cwd"]
    return row


