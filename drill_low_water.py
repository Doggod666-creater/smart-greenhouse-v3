# -*- coding: utf-8 -*-
"""饮水演练：把水位压到安全线下，验证「无水禁泵」真的会拒绝。

为什么需要这个脚本：
  fake_device.py 每 2 秒推一帧 water_level=72.0。
  你在手机上点「开泵」时，后端读的是"最后一条记录"——
  如果刚好是 fake 那帧，守卫看到 72% 就放行，返回 200 而不是 409。
  于是你以为"禁泵没生效"，其实是被数据源覆盖了。
  这个脚本用远高于 fake 的频率持续压水位，让"最后一条"稳定是低水位。

两种用法：

  1) 只跑校验，不碰手机（推荐先跑这个）
       python drill_low_water.py --check
     它会：推一帧 5% → 确认 latest 已是低水位 → 请求开泵 → 打印 HTTP 状态与后端原话。
     期望看到 409 和「水位 5.0% 低于安全下限 15.0%，拒绝开泵（防干转）」。
     注意：409 这条路径在发巴法云之前就 return 了，所以不会真的启动水泵。

  2) 开一个窗口常驻，供手机点测
       python drill_low_water.py
     持续每 0.5 秒推一次低水位，你在手机上点开泵应当弹出红字。
     按 Ctrl+C 结束；结束后 fake_device 的下一帧会把水位改回 72。

安全提醒：
  正常开泵（返回 200）那一步会真的往巴法云发一条 pump:on。
  如果陈昶瑞的板子此刻正通电、正在订阅 esp32room，泵会真的转 30 秒。
  所以"能开泵"那条点测，先问一句板子通没通电。
"""

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

# 不走系统代理：本机开着代理时 urllib 会绕到代理去，连 127.0.0.1 都会失败
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _req(path, body=None, timeout=5):
    """返回 (HTTP 状态码, 解析后的 JSON 或原文)。不抛异常，让调用方自己看。"""
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw
    except Exception as e:                      # 连不上、超时、DNS 失败
        return 0, "%s: %s" % (type(e).__name__, e)


def push_frame(water, voltage=12.2):
    """推一帧遥测。dev 用 drill-01，方便在日志里和 fake-01 区分开。"""
    rec = {
        "dev": "drill-01",
        "temp": round(22 + random.uniform(-0.5, 0.5), 2),
        "humi": round(62 + random.uniform(-1, 1), 2),
        "soil": 41.0,
        "water_level": water,
        "voltage": voltage,
        "pump_on": False,
    }
    return _req("/api/ingest", rec)


def check(water):
    print("=" * 62)
    print("1) 推一帧 water_level=%.1f%%" % water)
    code, body = push_frame(water)
    print("   -> HTTP %s  %s" % (code, body if code != 200 else body.get("fault")))

    print("2) 读回 latest，确认「最后一条」真的是低水位")
    code, latest = _req("/api/latest")
    if code != 200:
        print("   -> 读不到 latest（HTTP %s），后面不用测了" % code)
        return 2
    seen = latest.get("water_level")
    online = latest.get("online")
    print("   -> water_level=%s  online=%s  fault=%s" % (seen, online, latest.get("fault")))

    if seen is None or seen >= 15.0:
        print("   ✗ 最后一条不是低水位（%.1f%），大概率被数据源覆盖了。" % (seen or -1))
        print("     处理：把跑 fake_device 的那个窗口先关掉，再重跑 --check。")
        return 2
    if not online:
        print("   ✗ 设备被判离线。离线时守卫也会拒绝，但那句原因是「遥测已过期」，")
        print("     不是「水位低」——测出来的结论不对。先保证有数据在推。")
        return 2

    print("3) 请求开泵，期望 409 + 后端原话")
    code, body = _req("/api/device/pump", {"cmdId": "drill-no-water", "action": "on", "durationSec": 30})
    reason = (body or {}).get("reason") or (body or {}).get("msg") or body
    print("   -> HTTP %s" % code)
    print("   -> reason: %s" % reason)

    if code != 409:
        print("   ✗ 没拒绝。守卫没生效，这是真 bug，不要当成「演示技巧」绕过去。")
        return 2

    print("-" * 62)
    print("✓ 通过：后端在水位 %.1f%% 时拒绝开泵，且给了具体数字和原因。" % seen)
    print("  这条 reason 就是手机上应当显示的那行红字。")
    return 0


def loop(water, interval, secs):
    print("持续推送 water_level=%.1f%%，间隔 %.2f 秒。Ctrl+C 结束。" % (water, interval))
    print("期间在手机上点开泵，应当弹出后端原话的红字。\n")
    t0 = time.time()
    n = 0
    try:
        while True:
            if secs and time.time() - t0 > secs:
                break
            code, body = push_frame(water)
            n += 1
            fault = body.get("fault") if isinstance(body, dict) else body
            sys.stdout.write("\r  #%d  HTTP %s  fault=%s      " % (n, code, fault))
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    print("\n停止。共推 %d 帧。数据源若还在跑，下一帧会把水位改回去。" % n)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="无水禁泵演练")
    ap.add_argument("--base", default="http://127.0.0.1:5000", help="后端地址")
    ap.add_argument("--water", type=float, default=5.0, help="要压低到的水位（%%）")
    ap.add_argument("--interval", type=float, default=0.5, help="常驻模式的推送间隔（秒）")
    ap.add_argument("--secs", type=float, default=0, help="常驻模式跑多久，0=一直跑")
    ap.add_argument("--check", action="store_true", help="只做一次端到端校验后退出")
    a = ap.parse_args()

    BASE = a.base.rstrip("/")
    sys.exit(check(a.water) if a.check else loop(a.water, a.interval, a.secs))
