# -*- coding: utf-8 -*-
"""智感云联 V3.0 —— 故障判定规则（纯函数）

为什么单独一个文件：
  答辩时要说的一句话是"规则是纯函数，可以不启硬件、不连网络单独测"。
  塞进 app.py 就讲不出这句话，而且七条规则会变成一堆 if 嵌在路由里。

为什么超时那条不在这里：
  七条规则里有六条判断"数据本身"，一条判断"没有数据"。
  后者不属于单条记录，必须放在读取时判——见 app.py 的 _mark_online()。
"""

TEMP_MIN, TEMP_MAX = 5.0, 35.0      # 大棚温度合理区间（℃）
HUMI_MIN, HUMI_MAX = 30.0, 90.0     # 相对湿度合理区间（%）
SOIL_MIN = 20.0                     # 土壤含水率下限（%）
WATER_MIN = 15.0                    # 水箱水位下限（%）
VOLTAGE_MIN = 10.5                  # 3S 锂电组低压阈值（V），低于它禁泵

# 物理量程：超出这个范围不是"超标"，是"传感器坏了"。
# 必须和业务阈值分开判——SHT30 报 200℃ 不能当成"高温告警"，
# 那会让人跑去通风，而真正的问题是探头断了。
PHYS_RANGE = {
    "temp": (-40.0, 125.0),         # SHT30 量程
    "humi": (0.0, 100.0),
    "soil": (0.0, 100.0),
    "water_level": (0.0, 100.0),
    "voltage": (0.0, 30.0),
}


def evaluate(rec):
    """传入一条记录，返回故障码列表。空列表代表正常。

    只读 rec，不写 rec，不访问任何外部状态 —— 这就是"纯函数"的意思：
    同样的输入永远给同样的输出，所以可以拿一条假数据直接测。
    """
    fault = []

    # ---------- 第一层：数据本身可不可信 ----------
    invalid = []
    for k, (lo, hi) in PHYS_RANGE.items():
        v = rec.get(k)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            invalid.append(k)
            continue
        if not (lo <= v <= hi):
            invalid.append(k)

    if invalid:
        fault.append("SENSOR_INVALID")
    # 不可信的字段不再参与下面的业务判据：
    # 探头坏了报 200℃，你不该收到"高温告警"，该收到"探头失效"。

    def good(k):
        return k not in invalid

    # ---------- 第二层：业务判据 ----------
    t = rec.get("temp")
    if t is not None and good("temp"):
        if t > TEMP_MAX:
            fault.append("TEMP_HIGH")
        elif t < TEMP_MIN:
            fault.append("TEMP_LOW")

    h = rec.get("humi")
    if h is not None and good("humi"):
        if h > HUMI_MAX:
            fault.append("HUMI_HIGH")
        elif h < HUMI_MIN:
            fault.append("HUMI_LOW")

    s = rec.get("soil")
    if s is not None and good("soil") and s < SOIL_MIN:
        fault.append("SOIL_DRY")

    w = rec.get("water_level")
    if w is not None and good("water_level") and w < WATER_MIN:
        fault.append("WATER_LOW")

    v = rec.get("voltage")
    if v is not None and good("voltage") and v < VOLTAGE_MIN:
        fault.append("LOW_VOLTAGE")

    return fault


def evaluate_series(rows, window_sec=30.0, min_span_sec=10.0, blocked_drop=0.5,
                    reflux_drop=2.0, jump_limit=15.0):
    """看一段历史，判两条单条数据判不出来的规则。

    为什么必须单独一个函数：
      堵塞和回流都是"水位随时间怎么变"的问题，
      任何一条孤立记录里都看不出来，必须拿一段序列比。

    min_span_sec 是刻意的：
      泵启动后头几秒水位本来就不会动（水还在管子里走），
      这时候判堵塞一定是误报。撑够 10 秒再说话。

    jump_limit 也是刻意的（实测踩出来的）：
      把水位从 72% 直接写到 5% 做禁泵演练时，这段序列的极差是 67、
      泵又没转，于是被判成"止回阀失效"——可实际上没有任何阀门失效，
      是读数一帧之内跳了 67 个百分点。真漏水不可能这么跳。
      同理，手动给水箱补水（15%→72%）也会反向触发同一条误报。
      所以只要窗口内相邻两帧的跳变超过 jump_limit，就认定这段数据
      不足以支撑任何"趋势"结论，直接不下判断，等下一段干净的序列。

    rows 要按时间升序，每条含 ts / water_level / pump_on。
    返回故障码列表。
    """
    if not rows or len(rows) < 2:
        return []

    t_end = rows[-1].get("ts") or 0
    win = [r for r in rows if (r.get("ts") or 0) >= t_end - window_sec]
    if len(win) < 2:
        return []

    if (win[-1].get("ts") or 0) - (win[0].get("ts") or 0) < min_span_sec:
        return []       # 观察时间不够，先不下结论

    levels = [r.get("water_level") for r in win]
    if any(x is None for x in levels):
        return []

    # 单帧跳变 → 这段序列不能用来判趋势
    max_step = max(abs(levels[i + 1] - levels[i]) for i in range(len(levels) - 1))
    if max_step > jump_limit:
        return []

    pumps = [bool(r.get("pump_on")) for r in win]
    drop = max(levels) - min(levels)     # 用极差而不是首尾差，能容忍单点抖动

    fault = []

    # 整段都在抽水，水位却几乎没动 → 抽不上来
    # 可能是 Y 型过滤器堵了、进水管漏气，或者泵本身空转
    if all(pumps) and drop < blocked_drop:
        fault.append("PUMP_BLOCKED")

    # 整段都没抽水，水位却在往下走 → 水在往回漏
    # 典型原因是单向止回阀失效，或者管路有渗漏
    elif not any(pumps) and drop > reflux_drop:
        fault.append("CHECK_VALVE_FAIL")

    return fault


def can_start_pump(last_rec, age_sec, stale_after=10.0):
    """开泵前的守卫。返回 (能不能开, 原因)。

    为什么单独一个函数、不塞进 evaluate()：
      evaluate() 判的是"这条数据本身有没有毛病"，
      而这个判的是"该不该执行一个动作"。前者纯读数，后者要担责任。

    宁可拒绝也不冒险，五条都是 fail-safe：
      1. 压根没收到过数据 → 不知道水位，不开
      2. 数据太旧（设备离线）→ 那是过期读数，不开
      3. 报文里没有水位字段 → 传感器没接或坏了，不开
      4. 水位低于下限 → 会干转，不开
      5. 电压低于 10.5V → 泵一启动就把电池拉崩，不开

    关泵永远放行，不走这个函数——停止动作不该有任何前置条件。
    """
    if last_rec is None:
        return False, "还没有收到任何遥测，无法确认水位，拒绝开泵"

    if age_sec > stale_after:
        return False, "遥测已过期 %.0f 秒（设备可能离线），拒绝开泵" % age_sec

    w = last_rec.get("water_level")
    if w is None:
        return False, "遥测里没有 water_level 字段，无法判断水位，拒绝开泵"

    if w < WATER_MIN:
        return False, "水位 %.1f%% 低于安全下限 %.1f%%，拒绝开泵（防干转）" % (w, WATER_MIN)

    v = last_rec.get("voltage")
    if v is not None and v < VOLTAGE_MIN:
        return False, "电池电压 %.2fV 低于 %.1fV，拒绝开泵（泵启动会把电池拉崩）" % (v, VOLTAGE_MIN)

    return True, "水位 %.1f%%，允许开泵" % w

