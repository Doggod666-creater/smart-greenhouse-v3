# -*- coding: utf-8 -*-
"""接口层测试。

重点不是"接口能不能通"，而是几条安全语义：
  · 没数据时不能假装有数据
  · 设备失联时不能拿旧读数当现状
  · 水位不够时不能开泵
  · 关泵永远放行
"""

import time

import pytest

import app as svc
import bemfa


@pytest.fixture
def client(monkeypatch):
    # 把巴法云推送换成假的，测试不碰外网、不往他账号发消息
    monkeypatch.setattr(bemfa, "push", lambda msg, timeout=4.0: (True, "stub: 已忽略"))
    svc.app.config["TESTING"] = True
    svc.HISTORY.clear()
    svc.LAST_TS["value"] = 0.0
    svc.PUMP.update({"state": "off", "until": 0.0, "last_cmd_ts": 0.0})
    with svc.app.test_client() as c:
        yield c


def _ingest(client, **kw):
    body = {"dev": "test", "temp": 25.0, "humi": 60.0, "soil": 40.0, "water_level": 72.0}
    body.update(kw)
    return client.post("/api/ingest", json=body)


# ------------------------------------------------ 基础

def test_index_ok(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_latest_without_data_is_503(client):
    r = client.get("/api/latest")
    assert r.status_code == 503


def test_ingest_rejects_missing_field(client):
    r = client.post("/api/ingest", json={"dev": "x", "temp": 25.0})
    assert r.status_code == 400
    assert "humi" in r.get_json()["missing"]


def test_ingest_then_latest_online(client):
    assert _ingest(client).status_code == 200
    j = client.get("/api/latest").get_json()
    assert j["online"] is True
    assert j["fault"] == []


# ------------------------------------------------ 掉线语义

def test_latest_offline_after_timeout(client):
    _ingest(client)
    svc.LAST_TS["value"] = time.time() - 100      # 伪造"100 秒没收到"
    j = client.get("/api/latest").get_json()
    assert j["online"] is False
    assert j["fault"] == ["OFFLINE"]


def test_offline_hides_stale_fault_codes(client):
    """设备失联时只剩 OFFLINE，旧判定挪到 last_fault。

    否则大屏会一边显示"设备离线"，一边催你"土壤干旱"——
    那个读数可能是几分钟前的，既没法确认，也没法处理。
    """
    _ingest(client, soil=5.0)                     # 触发 SOIL_DRY
    svc.LAST_TS["value"] = time.time() - 100
    j = client.get("/api/latest").get_json()
    assert j["fault"] == ["OFFLINE"]
    assert "SOIL_DRY" in j["last_fault"]


# ------------------------------------------------ 开泵守卫

def test_cannot_start_pump_without_data(client):
    r = client.post("/api/cmd", json={"pump": "on", "duration": 10})
    assert r.status_code == 409
    assert "还没有收到" in r.get_json()["reason"]


def test_cannot_start_pump_with_low_water(client):
    _ingest(client, water_level=5.0)
    r = client.post("/api/cmd", json={"pump": "on", "duration": 10})
    assert r.status_code == 409
    assert "水位" in r.get_json()["reason"]


def test_cannot_start_pump_when_offline(client):
    _ingest(client)
    svc.LAST_TS["value"] = time.time() - 100
    r = client.post("/api/cmd", json={"pump": "on", "duration": 10})
    assert r.status_code == 409
    assert "过期" in r.get_json()["reason"]


def test_can_start_pump_normally(client):
    _ingest(client)
    r = client.post("/api/cmd", json={"pump": "on", "duration": 10})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and j["sent_mqtt"] is True


def test_stop_pump_always_allowed(client):
    """关泵不设前置条件——哪怕没数据、离线、水位低，也得让"停"出去。"""
    r = client.post("/api/cmd", json={"pump": "off"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


# ------------------------------------------------ 前端兼容接口

def test_compat_sensor_current_shape(client):
    _ingest(client)
    j = client.get("/api/sensor/current").get_json()
    assert j["code"] == 0
    d = j["data"]
    assert set(["ts", "temp", "humi", "soil", "light"]).issubset(d.keys())
    assert len(str(d["ts"])) == 13          # 毫秒，前端 new Date() 直接用


def test_compat_sensor_503_when_stale(client):
    _ingest(client)
    svc.LAST_TS["value"] = time.time() - 60
    assert client.get("/api/sensor/current").status_code == 503


def test_compat_pump_rejects_low_water(client):
    _ingest(client, water_level=3.0)
    r = client.post("/api/device/pump", json={"cmdId": "c1", "action": "on", "durationSec": 10})
    assert r.status_code == 409
    assert r.get_json()["code"] == 409


def test_compat_pump_ok(client):
    _ingest(client)
    r = client.post("/api/device/pump", json={"cmdId": "c2", "action": "on", "durationSec": 10})
    j = r.get_json()
    assert j["code"] == 0
    assert j["data"]["accepted"] is True
    assert j["data"]["sent_mqtt"] is True
