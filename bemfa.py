# -*- coding: utf-8 -*-
"""巴法云通道：下行推指令 / 上行拉遥测

为什么不用 MQTT：
    1. MQTT 规范规定同一个 clientId 同时只能有一个连接，新连接会把旧的踢掉。
       巴法云拿【私钥】当 clientId，后端要是连上去，很可能把陈昶瑞的 ESP32 踢下线。
    2. 后端在局域网、ESP32 在别处，两边都只能往外连，本来就需要一个公网中转。
    3. 巴法云官方提供 HTTP 推送接口，一次请求就走完，不用维护长连接、不用断线重连。

所以链路是：ESP32 →（往外连）巴法云 ←（往外连）后端。中间那跳由巴法云负责。

⚠️ 系统里设了 http_proxy，urllib 默认会把请求丢给代理，导致 502。
   这里统一禁用代理，和 fake_device.py 的处理保持一致。
"""

import json
import os
import urllib.request
import urllib.error

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

PUSH_URL = "https://apis.bemfa.com/va/postJsonMsg"
PULL_URL = "https://apis.bemfa.com/va/getmsg"

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print("[bemfa] 配置文件读不了：%s" % e)
        return {}


def _cfg():
    c = load_config().get("bemfa") or {}
    return {
        "enabled": bool(c.get("enabled")),
        "uid": (c.get("uid") or "").strip(),
        "topic": (c.get("topic") or "esp32room").strip(),
        "type": int(c.get("type", 1)),
        "interval": float(c.get("poll_interval", 2.0)),
        "poll_enabled": bool(c.get("poll_enabled")),
    }


def ready():
    """能不能往下发。返回 (bool, 原因)。"""
    c = _cfg()
    if not c["enabled"]:
        return False, "配置里 bemfa.enabled=false，未接入云端"
    if not c["uid"]:
        return False, "配置里 bemfa.uid 为空（需要填巴法云私钥）"
    if not c["topic"]:
        return False, "配置里 bemfa.topic 为空"
    return True, "ok"


def push(msg, timeout=4.0):
    """往主题推送一条消息，ESP32 订阅了该主题就能收到。

    返回 (ok, detail)。ok=False 时 detail 是给人看的原因，
    上层要把它原样带回去，不要假装发出去了。
    """
    okc, why = ready()
    if not okc:
        return False, why

    c = _cfg()
    body = json.dumps({
        "uid": c["uid"],
        "topic": c["topic"],
        "type": c["type"],
        "msg": msg,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        PUSH_URL, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return False, "连不上巴法云：%s" % e
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)

    if res.get("code") != 0:
        return False, "巴法云拒收 code=%s %s" % (res.get("code"), res.get("message") or res.get("msg") or "")
    return True, "已推送 %s → %s" % (msg, c["topic"])


def pull(timeout=4.0):
    """拉该主题最新一条消息。返回 (ok, 内容或原因)。

    巴法云返回的 data 是按时间倒序的数组，取 [0] 就是最新。
    """
    okc, why = ready()
    if not okc:
        return False, why

    c = _cfg()
    url = "%s?uid=%s&topic=%s&type=%d" % (PULL_URL, c["uid"], c["topic"], c["type"])
    try:
        with _OPENER.open(url, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return False, "连不上巴法云：%s" % e
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)

    if res.get("code") != 0:
        return False, "巴法云返回 code=%s" % res.get("code")

    data = res.get("data") or []
    if not data:
        return False, "该主题下还没有消息"
    return True, data[0].get("msg", "")


def build_pump_msg(action, duration):
    """开泵指令的报文。陈昶瑞的 ESP32 收到后按这个格式解析。

    刻意用 JSON 而不是 1.0 的 "on"/"off" 纯文本：
    以后要加风扇、补光灯，直接在对象里加字段就行，不用改协议。
    """
    return json.dumps({"pump": action, "duration": int(duration)}, ensure_ascii=False)


def parse_payload(raw):
    """把设备发来的报文解析成后端认的字典。

    两种格式都吃：
      V3.0（新）：{"temp":24.4,"humi":62.1,"soil":41.0,"water_level":72.0,"pump_on":false}
      1.0（旧）：Temp:24.4,Humi:19.0
    旧格式先留着，因为陈昶瑞那边还没改固件——他一天没改，这套就还能跑一天。
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    out = {}
    for part in raw.split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip().lower()
        try:
            out[k] = float(v)
        except ValueError:
            continue
    if "temp" in out:
        return {"temp": out.get("temp"), "humi": out.get("humi")}
    return None
