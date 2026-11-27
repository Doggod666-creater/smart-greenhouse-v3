# -*- coding: utf-8 -*-
"""规则层测试。

这一批测试一秒钟跑完，不启后端、不连网络、不接硬件——
这就是"规则是纯函数"最实际的好处：改一个阈值，立刻知道有没有踩坏别的判定。
"""

import rules


def _rec(**kw):
    """造一条正常的记录，要改哪个字段就传哪个。"""
    base = {
        "dev": "test", "ts": 1000.0,
        "temp": 25.0, "humi": 60.0, "soil": 40.0,
        "water_level": 72.0, "pump_on": False,
    }
    base.update(kw)
    return base


# ------------------------------------------------ 单条判定

def test_normal_has_no_fault():
    assert rules.evaluate(_rec()) == []


def test_temp_high():
    assert "TEMP_HIGH" in rules.evaluate(_rec(temp=36.0))


def test_temp_low():
    assert "TEMP_LOW" in rules.evaluate(_rec(temp=4.0))


def test_humi_high():
    assert "HUMI_HIGH" in rules.evaluate(_rec(humi=95.0))


def test_humi_low():
    assert "HUMI_LOW" in rules.evaluate(_rec(humi=29.0))


def test_soil_dry():
    assert "SOIL_DRY" in rules.evaluate(_rec(soil=19.9))


def test_water_low():
    assert "WATER_LOW" in rules.evaluate(_rec(water_level=14.9))


def test_low_voltage():
    assert "LOW_VOLTAGE" in rules.evaluate(_rec(voltage=10.4))


def test_voltage_normal():
    assert "LOW_VOLTAGE" not in rules.evaluate(_rec(voltage=12.2))


def test_sensor_invalid_suppresses_business_rule():
    """探头断了报 200℃，该说的是"传感器失效"，不是"高温告警"。

    这两句话对农户意味着完全不同的动作：
    前者应该去检查探头，后者会让人白跑去开风口。
    """
    f = rules.evaluate(_rec(temp=200.0))
    assert "SENSOR_INVALID" in f
    assert "TEMP_HIGH" not in f


def test_sensor_invalid_on_garbage_value():
    f = rules.evaluate(_rec(temp="NaN"))
    assert "SENSOR_INVALID" in f
    assert "TEMP_HIGH" not in f


def test_missing_field_does_not_crash():
    """设备少发一个字段不能把整个判定炸掉。"""
    r = _rec()
    r.pop("humi")
    assert isinstance(rules.evaluate(r), list)


# ------------------------------------------------ 序列判定

def _series(levels, pumps, step=2.0):
    return [
        {"ts": 1000.0 + i * step, "water_level": w, "pump_on": p}
        for i, (w, p) in enumerate(zip(levels, pumps))
    ]


# 序列判定有最少 10 秒的观察期，所以下面每组都造 6 条 × 2 秒 = 10 秒跨度


def test_pump_blocked_when_running_but_level_flat():
    rows = _series([72.0] * 6, [True] * 6)
    assert "PUMP_BLOCKED" in rules.evaluate_series(rows)


def test_pump_not_blocked_when_level_drops():
    rows = _series([72.0, 70.0, 68.0, 66.0, 64.0, 62.0], [True] * 6)
    assert "PUMP_BLOCKED" not in rules.evaluate_series(rows)


def test_check_valve_fail_when_idle_but_level_drops():
    rows = _series([72.0, 70.0, 68.0, 66.0, 64.0, 62.0], [False] * 6)
    assert "CHECK_VALVE_FAIL" in rules.evaluate_series(rows)


def test_no_reflux_on_single_frame_jump():
    """一帧之内从 72% 掉到 5% 不是回流，是读数跳变。

    这条是真踩出来的：拿 5% 水位做"无水禁泵"演练时，
    序列判定看见"极差 67、泵没转"，直接报了止回阀失效。
    真漏水不可能两秒漏掉 67 个百分点。
    """
    rows = _series([72.0, 72.0, 72.0, 72.0, 72.0, 5.0], [False] * 6)
    assert "CHECK_VALVE_FAIL" not in rules.evaluate_series(rows)


def test_no_reflux_when_tank_refilled():
    """手动补水 15%→72%，泵没转，也不能报止回阀失效。"""
    rows = _series([15.0, 15.0, 15.0, 15.0, 15.0, 72.0], [False] * 6)
    assert rules.evaluate_series(rows) == []


def test_jump_suppresses_all_series_judgement():
    """有跳变的序列不足以支撑任何趋势结论，堵塞也不该判。"""
    rows = _series([72.0, 72.0, 72.0, 72.0, 72.0, 5.0], [True] * 6)
    assert rules.evaluate_series(rows) == []


def test_no_fault_when_idle_and_stable():
    rows = _series([72.0] * 6, [False] * 6)
    assert rules.evaluate_series(rows) == []


def test_no_conclusion_before_min_span():
    """泵刚启动那几秒水位不动是正常的，不能立刻喊堵塞。"""
    rows = _series([72.0, 72.0], [True, True])       # 只有 2 秒
    assert rules.evaluate_series(rows) == []


def test_series_needs_at_least_two_points():
    assert rules.evaluate_series([]) == []
    assert rules.evaluate_series(_series([72.0], [True])) == []


def test_series_ignores_missing_water_level():
    rows = _series([72.0] * 6, [True] * 6)
    rows[3]["water_level"] = None
    assert rules.evaluate_series(rows) == []


# ------------------------------------------------ 开泵守卫

def test_can_start_when_all_good():
    ok, why = rules.can_start_pump(_rec(), 0.0)
    assert ok, why


def test_cannot_start_without_any_data():
    ok, why = rules.can_start_pump(None, 0.0)
    assert not ok and "还没有收到" in why


def test_cannot_start_with_stale_data():
    ok, why = rules.can_start_pump(_rec(), 99.0)
    assert not ok and "过期" in why


def test_cannot_start_without_water_field():
    r = _rec()
    r.pop("water_level")
    ok, why = rules.can_start_pump(r, 0.0)
    assert not ok and "water_level" in why


def test_cannot_start_with_low_water():
    ok, why = rules.can_start_pump(_rec(water_level=5.0), 0.0)
    assert not ok and "5.0%" in why


def test_cannot_start_with_low_voltage():
    ok, why = rules.can_start_pump(_rec(voltage=10.0), 0.0)
    assert not ok and "电压" in why
