# -*- coding: utf-8 -*-
"""假数据源：不接 ESP32，也能让大屏曲线动起来。

用法（在 v3 目录下，另开一个终端）：
    python fake_device.py normal    # 正常
    python fake_device.py drift     # 缓慢劣化：湿度一路往下，3 分钟后越界
    python fake_device.py spike     # 突变：第 40 秒温度跳 8 度

为什么用剧本而不是随机数：
    答辩时你想演示"异常预警"，随机数恰好不触发，你就得说"稍等一下它自己会出现"——
    那句话一出口，这一页就废了。剧本能让异常在第 40 秒准时发生，讲一遍演一遍。
"""

import json
import math
import sys
import time
import urllib.request

URL = "http://127.0.0.1:5000/api/ingest"

# 关键：系统里设了 http_proxy，urllib 默认会走代理，
# 结果访问 127.0.0.1 也被代理转走，报 502。这里显式禁用代理。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def scenario(name, t):
    """t 是启动后经过的秒数。返回这一拍要上报的物理量。"""
    if name == "drift":
        # 湿度每秒掉 0.5%，100 秒后从 62% 掉到 12%，越过下限 30 必须报警
        return {
            "temp": 25.0 + 0.3 * math.sin(t / 30),
            "humi": max(5.0, 62.0 - t * 0.5),
            "soil": max(2.0, 41.0 - t * 0.35),
            "water_level": 72.0,
            "pump_on": False,
        }
    if name == "spike":
        # 第 40 秒温度直接跳 8 度，从 24 变成 32+，逼近上限 35
        return {
            "temp": 24.0 + (8.0 if t > 40 else 0.0),
            "humi": 60.0,
            "soil": 40.0,
            "water_level": 72.0,
            "pump_on": False,
        }
    # normal：正常波动，永远不报警
    return {
        "temp": 24.0 + 0.6 * math.sin(t / 20),
        "humi": 62.0 + 2.0 * math.sin(t / 35),
        "soil": 41.0 + 0.5 * math.sin(t / 50),
        "water_level": 72.0,
        "pump_on": False,
    }


def post(payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with _OPENER.open(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "normal"
    if name not in ("normal", "drift", "spike"):
        print("usage: python fake_device.py [normal|drift|spike]")
        return

    print(f"[fake_device] scenario={name} -> {URL}")
    print("[fake_device] Ctrl+C to stop")

    t0 = time.time()
    count = 0
    while True:
        t = time.time() - t0
        payload = scenario(name, t)
        payload["dev"] = "fake-01"
        payload["temp"] = round(payload["temp"], 1)
        payload["humi"] = round(payload["humi"], 1)
        payload["soil"] = round(payload["soil"], 1)

        try:
            r = post(payload)
            count += 1
            fault = r.get("fault") or []
            tag = ("FAULT:" + ",".join(fault)) if fault else "ok"
            print(f"[{count:03d}] t={t:5.1f}s T={payload['temp']:5.1f} "
                  f"H={payload['humi']:5.1f} S={payload['soil']:5.1f}  {tag}")
        except Exception as e:
            print(f"[xx] post failed: {type(e).__name__}: {e}")
            print("     检查后端是否在跑：浏览器打开 http://localhost:5000/")

        time.sleep(2)


if __name__ == "__main__":
    main()
