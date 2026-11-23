# 显存控制台

Windows 桌面上的 GPU 显存监视器和进程启动器。可以从显存列表选中一个本地模型服务，记住它的程序、参数和工作目录，用显卡做其他任务后再启动它。

[下载 Windows x64 便携版](https://github.com/KP-416/vram-console/releases/latest) · [上游项目与许可证](#上游项目与许可证)

双击 Windows x64 版 `显存控制台.exe`。上半区按专用显存用量排序，可以按名称、脚本名或 PID 搜索。下半区显示你保存过的启动项。

1. 选中实时列表里的进程，点 **记住启动方式**，核对程序、参数和工作目录，然后保存。要立即腾出显存，选 **保存并停止**。
2. 需要恢复时，选中下半区的记录，点 **启动**。同一启动项已经运行时，控制台会提示。停止会结束选中进程及其子进程，正在运行的任务会中断。
3. 关闭显存控制台后，由它启动的程序仍由各自的 CommandTrayHost 托管。重新打开控制台即可继续管理它们，也可使用对应的 Windows 托盘菜单。

**启动方式**。首次登记读取运行中程序的命令行和当前工作目录。可在保存前修改。Windows 下常见的 Python 虚拟环境和 Chromium GPU 子进程会向上找到相应的启动进程。若程序依赖整合包脚本、临时环境变量或交互登录，请用原来的启动入口。例如批处理可用 `C:\Windows\System32\cmd.exe`，参数填 `/d /c call "D:\路径\启动.bat"`。脚本需要在后台服务运行期间保持前台等待，才能通过该启动项管理整个进程树。将启动参数中包含凭据的记录保留在个人目录。

**读数**。进程列表使用所选显卡的 Windows WDDM 计数器。已保存项的显存为其可观测子进程树的合计。横杠表示当前未读到该项的显存。多个模型共用一个进程时，这里的停止按钮作用于整个进程。服务的“运行中”指进程存在，模型加载可能仍在进行。

**保存位置**。程序旁的 `显存控制台数据/profiles.json` 保存启动记录，`hosts` 保存各项的托管配置。启动路径和参数以明文存储。多个相同进程可以从实时列表指定具体实例。权限受限的系统进程可能只能查看显存。

## 上游项目与许可证

- **[shanselman/gputaskman](https://github.com/shanselman/gputaskman)**，Scott Hanselman。这里使用 [0.4.5 发布包](https://pypi.org/project/gputaskman/0.4.5/) 的 `pdh.py` 和 `dxgi.py`，分别读取 Windows GPU 性能计数器和显卡信息。[上游 MIT 许可证](gputaskman/LICENSE)
- **[rexdf/CommandTrayHost](https://github.com/rexdf/CommandTrayHost)**，rexdf。这里随附 [2.3-b450 发布包](https://github.com/rexdf/CommandTrayHost/releases/tag/2.3-b450) 的官方 x64 程序，用独立托盘宿主管理每个已保存的启动项。[上游 MIT 许可证](engine/LICENSE-CommandTrayHost.txt)
- `engine/LaunchWorker.cs` 将保存的参数和工作目录传递给 Windows 进程。运行时使用 Windows 自带的 .NET Framework。中文界面和进程识别在 `console.py` 与 `control.py` 中。

## 从源码运行

在 Windows x64 上使用带 Tk 的 Python 3.11 或更新版本。安装 `psutil` 后执行 `python console.py`。源码包附有托管组件和启动辅助程序。安装 `pyinstaller` 后可执行 `build.ps1` 生成单文件程序。
