# -*- coding: utf-8 -*-
"""智感云联 V3.0 后端 M1 —— 最小可跑版本

启动（在 v3 目录下）：
    python app.py

然后浏览器打开：
    http://localhost:5000/api/latest

设计上只有三件事要记住：
  1. online 不由设备上报，由服务端按"多久没收到"推导
  2. ingest 缺字段就拒绝，不静默接受
  3. 规则是纯函数（rules.py），这里只负责收、存、取
"""

import os
import time
from collections import deque

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

import rules

app = Flask(__name__)
# supports_credentials=True：前端 core.js:186 发的是 credentials:'include'，
# 不开这个浏览器会直接拦掉响应（Access-Control-Allow-Origin 不能为 *）。
CORS(app, supports_credentials=True)

# 前端兼容层（/api/sensor/current 等），纯增量，不影响下面的原生路由
#
# ⚠️ 这两行不能省。`python app.py` 时本文件的模块名是 __main__，
#    compat.py 里 `import app` 会把它当普通模块再加载一遍，
#    于是 HISTORY / PUMP 各有一份，兼容层永远看到空数据
#    （现象：/api/latest 有数据，/api/sensor/current 一直 503「还没有收到任何遥测」）。
import sys as _sys
_sys.modules.setdefault("app", _sys.modules[__name__])

from compat import compat
app.register_blueprint(compat)

# ---------------- 存储：先内存，够用就先这样 ----------------
# 为什么用 deque(maxlen=)：写满自动丢最旧的，不需要自己管理内存。
# 以后要换 SQLite，只需要改这三个函数（_save/_all/_last），路由不用动。
MAX_POINTS = 500
HISTORY = deque(maxlen=MAX_POINTS)
LAST_TS = {"value": 0.0}      # 最后一次成功收到数据的时间

OFFLINE_AFTER = 30            # 超过 30 秒没收到 → 判离线


def _now():
    return time.time()


def _mark_online(rec):
    """online 是"状态"，不是"数据"。

    设备自己能报 online=True，但它死机的时候也会一直报 true，
    所以这个字段不能信设备，必须由服务端按"最后一次收到的时间"推。
    """
    rec["online"] = (_now() - LAST_TS["value"]) <= OFFLINE_AFTER
    return rec


# ---------------- 路由 ----------------

@app.route("/")
def index():
    """给手机试连通用的最小路由。能返回这个，说明网络这关过了。"""
    return jsonify({"ok": True, "service": "v3-backend", "ts": _now(), "ui": "/ui/"})


# ---------------- 前端托管 ----------------
# 为什么不另起一个 8000 端口的静态服务器：
#   1. 手机只需记一个地址，少一个"为什么打不开"的排查点
#   2. 同源访问，CORS / credentials 那一堆问题直接不存在
#   3. 换热点换 IP 时，前端页面和后端 API 永远在同一台机器上，地址不可能错配
# 注意末尾的斜杠：/ui 少了斜杠，页面里的相对路径 styles.css 会被解析到 /styles.css。
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")


@app.route("/ui/")
def ui_index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/ui/<path:name>")
def ui_assets(name):
    # send_from_directory 内部会挡掉 ../ 穿越
    return send_from_directory(FRONTEND_DIR, name)


@app.route("/api/ingest", methods=["POST"])
def ingest():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "reason": "body 不是 JSON"}), 400

    # 1) 校验：缺关键字段直接 400，不要静默接受
    #    设备发错字段是常态，静默接受会让你在"大屏为什么是空的"上耗一晚上
    missing = [k for k in ("dev", "temp", "humi") if k not in data]
    if missing:
        return jsonify({"ok": False, "reason": "缺字段", "missing": missing}), 400

    rec = dict(data)

    # 2) 补时间戳：设备不一定带，带了也可能是开机后的毫秒数
    rec["ts"] = _now()

    # 3) 更新"最后收到时间"，再算在线状态
    LAST_TS["value"] = rec["ts"]
    rec = _mark_online(rec)

    # 4) 算故障：先判这一条自己有没有毛病，再看它和前面一段的关系
    rec["fault"] = rules.evaluate(rec)

    # 堵塞、回流这两条必须看序列——单条记录里看不出"水位在怎么变"。
    # 取最近 60 条（2 秒一拍约两分钟）与当前这条拼起来判。
    rows = list(HISTORY)[-60:] + [rec]
    for code in rules.evaluate_series(rows):
        if code not in rec["fault"]:
            rec["fault"].append(code)

    HISTORY.append(rec)
    return jsonify({"ok": True, "ts": rec["ts"], "fault": rec["fault"]})


@app.route("/api/latest")
def latest():
    if not HISTORY:
        return jsonify({"ok": False, "reason": "还没有数据"}), 503

    # 注意：这里要重新算 online。
    # 存的最后一条可能还是 online=True，但设备可能已经死了 5 分钟——
    # 每次读取时重算，才能让"超时"在页面上真的变出来。
    rec = _mark_online(dict(HISTORY[-1]))
    if not rec["online"]:
        # 设备失联时，那条"湿度低"是几十秒前的读数，不能和 OFFLINE 并列展示。
        # 否则大屏会催人去处理一个已经无法确认、也可能早就不存在的状况。
        # 原来的判定留在 last_fault 里，排查时还看得到。
        rec["last_fault"] = list(rec.get("fault") or [])
        rec["fault"] = ["OFFLINE"]
    return jsonify(rec)


@app.route("/api/history")
def history():
    try:
        minutes = int(request.args.get("minutes", 60))
    except (TypeError, ValueError):
        minutes = 60
    since = _now() - minutes * 60
    rows = [dict(r) for r in HISTORY if r["ts"] >= since]
    return jsonify({"ok": True, "count": len(rows), "data": rows})


# 泵的最近一次指令，先用内存记着；接了 MQTT 之后这里只是"下发的记录"
PUMP = {"state": "off", "until": 0.0, "last_cmd_ts": 0.0}


@app.route("/api/cmd", methods=["POST"])
def cmd():
    data = request.get_json(silent=True) or {}
    action = data.get("pump")
    if action not in ("on", "off"):
        return jsonify({"ok": False, "reason": "pump 只能是 on 或 off"}), 400

    try:
        duration = int(data.get("duration", 30))
    except (TypeError, ValueError):
        duration = 30
    duration = max(1, min(duration, 600))   # 兜底：1 秒到 10 分钟

    # 开泵守卫。关泵（off）永远放行——停止动作不该有任何前置条件，
    # 哪怕设备离线、水位未知，也得让"停"的意图出去。
    if action == "on":
        last = HISTORY[-1] if HISTORY else None
        age = (_now() - LAST_TS["value"]) if LAST_TS["value"] else 1e9
        can, why = rules.can_start_pump(last, age)
        if not can:
            return jsonify({"ok": False, "pump": "on", "reason": why}), 409

    PUMP["state"] = action
    PUMP["until"] = _now() + duration if action == "on" else 0.0
    PUMP["last_cmd_ts"] = _now()

    # 真发一条到巴法云。发不出去要如实说，不能返回 ok:true 骗前端。
    # 注意 sent_mqtt=True 的含义是"巴法云收下了"，不等于"设备执行了"——
    # 设备到底动没动，由影子 /api/device/shadow 的 power 变化来证明。
    import bemfa
    sent, detail = bemfa.push(bemfa.build_pump_msg(action, duration))

    return jsonify({
        "ok": True,
        "pump": action,
        "duration": duration,
        "sent_mqtt": sent,
        "detail": detail,
    })


if __name__ == "__main__":
    # use_reloader=False 很关键：
    # debug 模式默认会起两个进程（一个监视器一个干活），
    # 接上 MQTT 之后就会连两遍、收到重复消息，很难查。
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
