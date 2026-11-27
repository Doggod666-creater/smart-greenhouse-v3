# -*- coding: utf-8 -*-
"""
智感云联 V3.0 启动器

为什么逻辑不写在 start.bat 里：
  bat 文件里写中文有两个坑，都很难查——
    1. cmd 按系统代码页（中文 Windows 是 936）逐字节解析 bat，
       UTF-8 中文会被解错，每 3 字节吃掉后面 1 个 ASCII 字符，
       结果报 "efined 不是内部或外部命令" 这种莫名其妙的错。
    2. 行尾必须是 CRLF。纯 LF 时跨行括号块会崩。

  所以 bat 只保留最少的纯 ASCII 代码（找 Python），剩下全部搬到这里。
  Python 的编码是可控的，中文提示、菜单、错误处理都放在这。
"""

import ctypes
import os
import socket
import subprocess
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 5000
IS_WIN = sys.platform == "win32"

LINE = "=" * 58


# ---------------------------------------------------------------- 输出

def setup_console():
    """让中文在当前控制台正确显示。

    不强行 chcp 65001：那会换掉控制台代码页，某些字体下中文变方块。
    改成读出当前代码页，用对应编码输出，等于顺着环境来。
    """
    enc = "utf-8"
    if IS_WIN:
        try:
            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            enc = {65001: "utf-8", 936: "gbk", 950: "big5"}.get(cp, "utf-8")
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=enc, errors="replace")
        except Exception:
            pass


def say(msg=""):
    print(msg)


def ok(msg):
    print("  [OK]   " + msg)


def warn(msg):
    print("  [注意] " + msg)


def err(msg):
    print("  [错误] " + msg)


# ---------------------------------------------------------------- 环境

def check_deps():
    """只要 flask 和 flask_cors 能 import，服务就能跑。"""
    try:
        import flask          # noqa: F401
        import flask_cors     # noqa: F401
        return True
    except ImportError:
        return False


def install_deps():
    say("  正在安装依赖（首次运行需要联网）...")
    cmd = [
        sys.executable, "-m", "pip", "install",
        "-r", os.path.join(HERE, "requirements.txt"),
        "--index-url", "https://pypi.org/simple",
        "--disable-pip-version-check",
    ]
    try:
        r = subprocess.run(cmd, timeout=300)
        return r.returncode == 0
    except Exception as e:
        err("安装过程出错：%s" % e)
        return False


def listening_pids(port):
    """找出监听指定端口的进程。

    直接调 netstat 而不是用 psutil：多一个依赖，别人电脑上就多一个装不上的理由。
    """
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True,
            encoding="gbk", errors="replace", timeout=15,
        ).stdout
    except Exception:
        return []

    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(":%d" % port):
                try:
                    pids.add(int(parts[4]))
                except ValueError:
                    pass
    return sorted(pids)


def kill_pids(pids):
    for p in pids:
        subprocess.run(["taskkill", "/PID", str(p), "/F"], capture_output=True)


def lan_ip():
    """取本机在局域网里的地址。

    用 UDP connect 一个外网地址来问操作系统"你走哪块网卡"——
    不会真的发包，只是让路由表选一条路。比枚举网卡再猜哪块是 WiFi 可靠。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.6)
        s.connect(("114.114.114.114", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def firewall_rule_exists(name):
    """查入站规则在不在。返回 True / False / None（查不了）。"""
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=" + name],
            capture_output=True, text=True,
            encoding="gbk", errors="replace", timeout=15,
        )
        return r.returncode == 0 and name in r.stdout
    except Exception:
        return None


def show_ip():
    """--ip：只回答"今天的地址是什么、手机能不能连"，不启动任何服务。

    换热点后地址会变，这个入口就是为了不用每次回来问人。
    launcher.py 自己不做端口判断以外的猜测，查不到就直说查不到。
    """
    ip = lan_ip()
    say()
    say("  " + LINE)
    say("   手机要输的地址")
    say("     http://%s:%d/ui/" % (ip, PORT))
    say("  " + LINE)
    say()

    pids = listening_pids(PORT)
    if pids:
        ok("后端在跑 (PID %s)" % ",".join(map(str, pids)))
    else:
        warn("后端没在跑 —— 手机连不上是正常的")
        say("        先运行 start.bat 把服务起起来")
    say()

    fw = firewall_rule_exists("flask5000")
    if fw is True:
        ok("防火墙规则 flask5000 已存在")
    elif fw is False:
        warn("防火墙规则 flask5000 不存在")
        say("        本机访问永远正常、手机一定打不开，这个坑很难自己发现。")
        say("        用【管理员】PowerShell 跑一次，只需一次：")
        say('        netsh advfirewall firewall add rule name="flask5000" '
            'dir=in action=allow protocol=TCP localport=%d' % PORT)
    else:
        say("  [未知] 查不到防火墙规则")
    say()
    say("  换热点后地址会变，重新运行一次本脚本即可。")
    say()
    return 0


# ---------------------------------------------------------------- 启动

def spawn(title, args, dry_run=False):
    """在独立的控制台窗口里起一个进程。

    独立窗口而不是后台：演示时要能一眼看到两个窗口的日志，
    关窗口就是停服务，比拼 PID 直观得多。
    """
    full = [sys.executable] + args
    if dry_run:
        say("    [dry-run] 会开窗口 \"%s\" -> %s" % (title, " ".join(args)))
        return None

    if IS_WIN:
        flags = subprocess.CREATE_NEW_CONSOLE
        p = subprocess.Popen(
            full, cwd=HERE, creationflags=flags,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
    else:
        p = subprocess.Popen(full, cwd=HERE, start_new_session=True)
    ok("%s 已启动 (PID %d)" % (title, p.pid))
    return p


def wait_port(port, timeout=12.0):
    """等后端真的开始监听，再去开浏览器。

    不 sleep 死等固定秒数：慢机器上会打不开，快机器上会白等。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def main():
    setup_console()
    dry_run = "--dry-run" in sys.argv

    if "--ip" in sys.argv:
        return show_ip()

    say()
    say("  " + LINE)
    say("     智感云联 V3.0  —  一键启动")
    say("  " + LINE)
    say()

    # 1. 依赖
    if not check_deps():
        if not install_deps():
            err("依赖安装失败，服务起不来。")
            say()
            say("        手动安装：")
            say('        "%s" -m pip install flask flask-cors' % sys.executable)
            say('          --index-url https://pypi.org/simple')
            say()
            say("        常见原因：系统代理拦了 pip。先关代理再试。")
            say()
            return 1
    ok("依赖齐全")
    say("  解释器 %s" % sys.executable)
    say()

    # 2. 端口
    pids = listening_pids(PORT)
    if pids:
        warn("端口 %d 被占用（PID %s），先结束旧实例" % (PORT, ",".join(map(str, pids))))
        if not dry_run:
            kill_pids(pids)
            time.sleep(1.0)
        say()

    # 3. 选模式
    say("  请选择运行模式：")
    say()
    say("    1   真机模式    数据来自 ESP32（经巴法云中转）")
    say("    2   演示模式    本地假数据，不需要任何硬件")
    say()
    try:
        choice = input("    输入 1 或 2，直接回车默认演示模式： ").strip()
    except (EOFError, KeyboardInterrupt):
        choice = "2"
        say("")
    if choice not in ("1", "2"):
        choice = "2"
    say()

    # 4. 起进程
    apply_bemfa = (choice == "1")
    spawn("后端 :5000", ["app.py"], dry_run)

    if not dry_run:
        if not wait_port(PORT):
            warn("后端 12 秒内没有开始监听，可能有报错。看那个黑窗口。")
        else:
            ok("后端已就绪")
    if not apply_bemfa:
        spawn("假数据源 normal", ["fake_device.py", "normal"], dry_run)
        if not dry_run:
            time.sleep(1.2)
    say()

    # 5. 地址
    ip = lan_ip()
    say("  " + LINE)
    say("   本机大屏    http://127.0.0.1:%d/ui/" % PORT)
    say("   手机访问    http://%s:%d/ui/" % (ip, PORT))
    say("   接口自检    http://127.0.0.1:%d/api/sensor/current" % PORT)
    say()
    if choice == "1":
        say("   真机模式：页面要等 ESP32 上报才有数。")
        say("   没接设备时显示「链路中断」是正常的，不是故障。")
        say()
    say("   手机连同一个 WiFi 就能开。第一次用要先用管理员权限")
    say("   跑一次防火墙规则，否则本机正常、手机一定打不开：")
    say()
    say('     netsh advfirewall firewall add rule name="flask5000" '
        'dir=in action=allow protocol=TCP localport=%d' % PORT)
    say()
    say("   关闭方式   直接关掉那两个黑窗口")
    say("  " + LINE)
    say()

    if not dry_run:
        try:
            webbrowser.open("http://127.0.0.1:%d/ui/" % PORT)
        except Exception:
            pass

    if not dry_run:
        say("  按回车退出本窗口（服务继续在另外两个窗口里跑）")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
