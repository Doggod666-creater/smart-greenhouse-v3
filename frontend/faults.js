/* ============================================================================
 * 智感云联 V3.0 · faults.js —— 故障码 → 文案（纯数据，零依赖、零 DOM）
 * ----------------------------------------------------------------------------
 * 为什么要单独一个文件：
 *   H5 用 ui.js 渲染，小程序用 .vue 渲染，两套渲染层完全不能共用；
 *   但"故障码该显示成什么字"这件事跟渲染技术无关，是业务文案。
 *   文案只写一份，两个端都从这里取，才不会出现"H5 说止回阀失效、
 *   小程序说回流异常"这种同一台设备两种说法的答辩事故。
 *
 * 两个端的用法：
 *   H5（浏览器）：<script src="faults.js"></script> 之后全局有 window.FAULT_DEFS
 *   小程序（uni-app）：import FAULT_DEFS from '@/common/faults.js'
 *
 * 字段说明：
 *   level    critical（红，需要立刻处理）/ warn（黄，需要关注）
 *   channel  'alert'  → 渲染成告警卡片，一码一卡
 *            'banner' → 不进告警列表，由链路状态横幅 + 整页置灰来表达
 *   title    卡片标题
 *   desc(s)  正文，接收当前传感器快照 s，返回字符串。
 *            s 里的字段可能缺失（离线时后端只给最后一条），所以每个取值
 *            都要先判 null —— 直接 s.temp.toFixed() 在掉线时会抛异常，
 *            而抛在渲染循环里会让整个页面白屏。
 * ==========================================================================*/
(function (root, factory) {
  var defs = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = defs;
    module.exports.default = defs;   // 兼容 ESM 的默认导入写法
  } else {
    root.FAULT_DEFS = defs;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function n(v, unit, digits) {
    if (v === null || v === undefined || isNaN(v)) return '—' + (unit || '');
    return Number(v).toFixed(digits === undefined ? 1 : digits) + (unit || '');
  }

  return {
    /* ---------------- 环境阈值：数值越界，处理方式是调环境 ---------------- */

    TEMP_HIGH: {
      level: 'critical', channel: 'alert', title: '棚内温度过高',
      desc: function (s) {
        return '当前 ' + n(s.temp, '℃') + '，超过 35℃ 上限。请立即通风降温，并暂停灌溉防止烫根。';
      }
    },

    TEMP_LOW: {
      level: 'warn', channel: 'alert', title: '棚内温度过低',
      desc: function (s) {
        return '当前 ' + n(s.temp, '℃') + '，低于 5℃ 下限，存在冻害风险。';
      }
    },

    HUMI_HIGH: {
      level: 'warn', channel: 'alert', title: '空气湿度过高',
      desc: function (s) {
        return '当前 ' + n(s.humi, '%RH') + '，超过 90%。高湿易诱发灰霉病，建议通风。';
      }
    },

    HUMI_LOW: {
      level: 'warn', channel: 'alert', title: '空气湿度过低',
      desc: function (s) {
        return '当前 ' + n(s.humi, '%RH') + '，低于 30%，蒸腾过强。';
      }
    },

    SOIL_DRY: {
      level: 'critical', channel: 'alert', title: '土壤缺水',
      desc: function (s) {
        return '土壤含水率 ' + n(s.soil, '%') + '，低于 20% 下限，根系已进入缺水胁迫。建议立即灌溉。';
      }
    },

    /* ---------------- 设备与执行：要动硬件或拦动作，处理方式不同 ---------------- */

    WATER_LOW: {
      level: 'critical', channel: 'alert', title: '水箱水位过低',
      desc: function (s) {
        return '水位 ' + n(s.water_level, '%') + '，低于 15% 安全下限。系统已禁止开泵，请先补水。';
      }
    },

    LOW_VOLTAGE: {
      level: 'critical', channel: 'alert', title: '电池电压过低',
      desc: function (s) {
        return '电压 ' + n(s.voltage, 'V', 2) + '，低于 10.5V。系统已禁止开泵，防止启动瞬间把电池拉崩。';
      }
    },

    SENSOR_INVALID: {
      level: 'warn', channel: 'alert', title: '传感器数据异常',
      desc: function () {
        return '探头读数超出物理可能范围（如 200℃），已标记为无效、不参与业务判定。请检查探头接线。';
      }
    },

    PUMP_BLOCKED: {
      level: 'critical', channel: 'alert', title: '水泵疑似堵塞',
      desc: function () {
        return '水泵运行超过 10 秒，水位几乎没有下降，管路可能堵塞或泵未出水。请检查 Y 型过滤器与进水管。';
      }
    },

    CHECK_VALVE_FAIL: {
      level: 'warn', channel: 'alert', title: '止回阀异常',
      desc: function () {
        return '水泵未运行，水位却持续下降，疑似止回阀失效导致回流。';
      }
    },

    /* ---------------- 掉线：不走告警卡，走横幅 + 置灰 ----------------
     * 后端在设备失联时返回的 fault 是 ["OFFLINE"]。如果把它也画成一张告警卡，
     * 用户会同时看到"链路中断"横幅和"设备掉线"卡片，像是两件事。
     * 掉线是唯一的"没有数据"型故障，它影响的是整页的可信度，
     * 所以由页面整体置灰来表达，不进列表。
     * ------------------------------------------------------------------- */

    OFFLINE: {
      level: 'warn', channel: 'banner', title: '设备掉线',
      desc: function (s) {
        return '超过 30 秒没有收到任何遥测。页面上显示的是最后一次的数据，不是当前值，请勿据此操作。';
      }
    }
  };
});
