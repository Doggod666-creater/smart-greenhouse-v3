# -*- coding: utf-8 -*-
"""v3 后端 ↔ 前端 core.js 的兼容层

为什么要有这一层：
    司景行那套 core.js 的读接口全部要求 {code:0, data:...} 信封，
    路径也是 /api/sensor/current 这种，跟 v3 原生的扁平 JSON + /api/latest 对不上。
    与其让前端改七处，不如在这里一次性翻译。原生的 /api/latest 等照旧可用，
    所以这层是纯增量，删掉它后端也照跑。

三个隐性差异，任何一个没处理前端都会挂：
    1. 信封      core.js:191 校验 json.code，扁平对象直接抛"业务异常"
    2. ts 单位   前端 new Date(ts)，必须是毫秒；后端 time.time() 是秒
    3. 离线语义  前端靠"请求失败→不刷新 lastSeenAt→心跳判离线"，
                 所以数据陈旧时必须返回 503，不能返回 200 + 旧数据
"""

from flask import Blueprint, request, jsonify

compat = Blueprint("compat", __name__)

# 前端 core.js:36 的 OFFLINE_GAP_MS 是 5000ms。
# 这里跟它对齐：超过 5 秒没新数据就回 503，前端心跳会自然判离线。
# ⚠️ 如果哪天 ESP32 的上报周期超过 5 秒，这个值必须跟着放大，否则会误判掉线。
FRESH_MS = 5000


def _core():
    """延迟导入，避免 app.py 导入本模块时的循环引用。"""
    import app as _a
    return _a


def ok(data, msg="ok"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=500, extra=None):
    body = {"code": code, "msg": msg, "data": None}
    if extra:
        body.update(extra)
    return jsonify(body), code


def _frame(r):
    """把内部记录翻成前端要的字段，单位换成毫秒。"""
    return {
        "ts": int(r["ts"] * 1000),          # 毫秒，前端 new Date() 直接用
        "temp": r.get("temp"),
        "humi": r.get("humi"),
        "soil": r.get("soil"),
        "light": r.get("light"),            # ESP32 还没装光照，先给 null 不报错
        "water_level": r.get("water_level"),
        "pump_on": bool(r.get("pump_on")),
        "fault": r.get("fault") or [],
    }


def _recent(a, n=80):
    """最近 n 条，给离线时的图表兜底用。

    页面刷新时如果设备已经失联，HISTORY 里明明有数据，前端却是空图。
    把最近这段一起带过去，离线状态下也能看到失联前的曲线，
    比一张白板有用得多。
    """
    rows = list(a.HISTORY)[-n:]
    return [_frame(r) for r in rows]


# ---------------------------------------------------------------- 遥测

@compat.route("/api/sensor/current")
def sensor_current():
    """前端轮询这一个接口拿最新一帧。

    数据陈旧时返回 503 是刻意的：前端 request() 会抛错，
    轮询的 catch 里什么都不做，lastSeenAt 就不会更新，
    5 秒后 heartbeat 自动把链路判为断开。整条链路不用改一行判活逻辑。
    """
    a = _core()
    if not a.HISTORY:
        return fail("还没有收到任何遥测", 503)

    age_ms = (a._now() - a.LAST_TS["value"]) * 1000
    r = a.HISTORY[-1]

    if age_ms > FRESH_MS:
        # 仍然是 503（前端的判活链路依赖这个状态码），但把最后一条数据一起带出去。
        # 只回一句"数据已陈旧"的话，前端只能显示初始值 0℃/0%，
        # 农户半夜打开手机看到 0℃，会以为棚里冻死了，实际只是设备失联。
        return fail(
            "数据已陈旧 %.1f 秒" % (age_ms / 1000.0),
            503,
            {
                "stale": True,
                "age_sec": round(age_ms / 1000.0, 1),
                "last": _frame(r),
                "recent": _recent(a),
            },
        )

    return ok(_frame(r))


# ---------------------------------------------------------------- 设备影子

@compat.route("/api/device/shadow")
def device_shadow():
    """前端 waitAck() 等的就是这个 power 字段。

    注意：前端目前没有任何地方轮询这个接口，所以就算这里对了，
    Device.command() 也会在 7 秒后走 ACK_TIMEOUT 分支。
    前端必须补一个 setInterval 拉这里并调 App.setShadow()。
    """
    a = _core()
    p = a.PUMP
    power = p["state"] == "on" and (p["until"] == 0 or a._now() < p["until"])
    return ok({
        "power": power,
        "ts": int(p["last_cmd_ts"] * 1000),
        "source": "device",
    })


# ---------------------------------------------------------------- 下发指令

@compat.route("/api/device/pump", methods=["POST"])
def device_pump():
    """接收前端 {cmdId, action, durationSec}，落到 PUMP 状态上。

    sent_mqtt 仍然是 false —— paho 那一步还没接，主题名也没跟陈昶瑞确认。
    这里如实上报，不假装发出去了。
    """
    a = _core()
    d = request.get_json(silent=True) or {}

    action = d.get("action")
    if action not in ("on", "off"):
        return fail("action 只能是 on 或 off", 400)

    dur = d.get("durationSec") or d.get("duration") or 30
    try:
        dur = int(dur)
    except (TypeError, ValueError):
        dur = 30
    dur = max(1, min(dur, 600))     # 1 秒到 10 分钟

    # 开泵守卫：关泵永远放行，开泵必须能证明水位安全
    if action == "on":
        last = a.HISTORY[-1] if a.HISTORY else None
        age = (a._now() - a.LAST_TS["value"]) if a.LAST_TS["value"] else 1e9
        can, why = a.rules.can_start_pump(last, age)
        if not can:
            # why 本身已经是一句完整的话（"水位 5.0% 低于安全下限 15.0%，拒绝开泵（防干转）"），
            # 再加前缀会变成"拒绝开泵：水位…，拒绝开泵（防干转）"，手机上看着像结巴。
            return fail(why, 409)

    a.PUMP["state"] = action
    a.PUMP["until"] = a._now() + dur if action == "on" else 0.0
    a.PUMP["last_cmd_ts"] = a._now()

    # 真发到巴法云。失败就把原因带走，让前端走 failed 分支，
    # 不能返回 code:0 骗它"已下发"。
    import bemfa
    sent, detail = bemfa.push(bemfa.build_pump_msg(action, dur))
    if not sent:
        return fail("指令未送达：%s" % detail, 502)

    return ok({
        "accepted": True,
        "cmdId": d.get("cmdId"),
        "duration": dur,
        "sent_mqtt": True,
        "detail": detail,
    })
