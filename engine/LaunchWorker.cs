using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.Web.Script.Serialization;

class LaunchWorker
{
    [STAThread]
    static int Main()
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        try
        {
            var spec = new JavaScriptSerializer().Deserialize<Dictionary<string, string>>(
                File.ReadAllText(Path.Combine(folder, "launch.json"), Encoding.UTF8));
            var info = new ProcessStartInfo(spec["exe"], spec["arguments"]);
            info.WorkingDirectory = spec["cwd"];
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            using (var child = Process.Start(info))
            {
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception ex)
        {
            File.WriteAllText(Path.Combine(folder, "launch-error.txt"), ex.Message, Encoding.UTF8);
            return 1;
        }
    }
}
