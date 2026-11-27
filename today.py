"""
今天手机该用哪个地址？—— 换热点后双击 ip.bat 跑一次，不用问任何人。

为什么需要这个：局域网 IP 是热点动态分配的，换一次热点就换一个号。
写死在文档里的地址过几天就失效，而失效时的症状是"手机打不开"，
看起来像代码坏了，实际只是地址变了。这个脚本把三件事一次问清：

  1. 今天的地址是什么
  2. 后端在不在跑
  3. 防火墙放没放行（这条不通过，手机一定打不开，本机却一切正常）

设计上刻意不依赖任何第三方库，只用标准库，免得哪天环境变了跑不起来。
"""

import socket
import subprocess
import sys
import urllib.request
from urllib.error import URLError

# Windows 控制台默认 GBK，中文会乱码，这里强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PORT = 5000
RULE = "flask5000"


def lan_ip():
    """拿默认路由出口的地址。

    不能用 socket.gethostbyname(gethostname())：那玩意儿在有 WSL 的机器上
    经常返回 172.x 的虚拟网卡地址，手机根本连不上。
    UDP connect 不发包，只是让系统选一条路由，选出来的就是真实出口地址。
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def backend_alive():
    """后端在不在监听 5000。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    alive = s.connect_ex(("127.0.0.1", PORT)) == 0
    s.close()
    return alive


def firewall_ok():
    """有没有 flask5000 这条入站规则。查规则不需要管理员权限。"""
    try:
        out = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=" + RULE],
            capture_output=True, text=True, errors="ignore", timeout=10,
        )
        text = (out.stdout or "") + (out.stderr or "")
        # 没有规则时 netsh 会说"没有与指定标准相匹配的规则"
        if RULE.lower() in text.lower():
            enabled = "是" in text or "Yes" in text
            return True, ("已启用" if enabled else "存在但未启用")
        return False, "规则不存在"
    except Exception as e:
        return False, "查询失败: %s" % e


def probe(ip):
    """真发一次请求，确认手机打开的那个 URL 现在返回什么。禁代理。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://%s:%d/api/latest" % (ip, PORT)
    try:
        with opener.open(url, timeout=3) as r:
            return True, "HTTP %d" % r.status
    except URLError as e:
        code = getattr(e, "code", None)
        if code:
            return True, "HTTP %d" % code
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)


def main():
    print("=" * 56)
    ip = lan_ip()
    if not ip:
        print("  拿不到本机地址：可能没连任何网络")
        return
    url = "http://%s:%d" % (ip, PORT)

    alive = backend_alive()
    fw_ok, fw_msg = firewall_ok()

    print("  今天的地址（手机要输这个）")
    print("  " + url)
    print("=" * 56)
    print(" ")

    print("  后端   : " + ("在跑" if alive else "没在跑 —— 先启动 app.py"))
    print("  防火墙 : " + fw_msg)

    if alive:
        ok, detail = probe(ip)
        print("  自检   : " + (("通，" + detail) if ok else ("不通，" + detail)))
    else:
        print("  自检   : 跳过（后端没跑）")

    print(" ")
    print("-" * 56)
    if not alive:
        print("  结论：后端没起，手机连不上是正常的。")
        print("        v3 目录下运行 python app.py 再来一次。")
    elif not fw_ok:
        print("  结论：代码没问题，是防火墙把手机挡在门外。")
        print("        本机访问永远正常，所以这个坑很难自己发现。")
        print(" ")
        print("        用【管理员】PowerShell 跑一次，只需一次：")
        print('        netsh advfirewall firewall add rule name="%s" ^' % RULE)
        print("          dir=in action=allow protocol=TCP localport=%d" % PORT)
    else:
        print("  结论：手机连上同一个 WiFi，浏览器输上面那个地址就能开。")
    print("-" * 56)


if __name__ == "__main__":
    main()
