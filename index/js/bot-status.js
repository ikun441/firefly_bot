/* ==========================================================================
   流萤 Firefly · 协议端（OneBot v11）状态面板
   --------------------------------------------------------------------------
   后端通过 OneBot 适配器连接 QQ 等协议端，报告与预警经由它推送。
   这里提供两个入口：
     1) 顶栏 🤖 按钮 → 弹出状态面板（随时查看连接情况）；
     2) 设置页内嵌卡片 → 与推送配置放在一起，便于排查「为什么没收到推送」。
   面板轮询 /api/push/status，实时展示连接状态、推送目标与最近一次发送结果。
   ========================================================================== */

(function () {
  'use strict';

  const FF = window.FF;
  const el = FF.el;
  const api = window.FFApi.api;
  const unwrap = window.FFApi.unwrap;

  let pollTimer = null;
  let lastStatus = null;

  /* ============================================================ 数据 */

  async function fetchStatus() {
    // 这里刻意不走 withFallback：协议端状态本身就是要看「真实」连接情况，
    // 演示模式下也不该显示 mock 的假连接状态，否则会误导排查。
    const envelope = await api.pushStatus();
    return unwrap(envelope) || {};
  }

  /* ============================================================ 渲染 */

  function statusDot(connected) {
    const color = connected ? '#4ade80' : '#94a3b8';
    return el('span', {
      style: {
        display: 'inline-block',
        width: '9px',
        height: '9px',
        borderRadius: '50%',
        background: color,
        boxShadow: connected ? '0 0 10px ' + color : 'none',
        marginRight: '7px',
        verticalAlign: 'middle',
      },
    });
  }

  function row(label, value, options) {
    const opts = options || {};
    return el('div', {
      style: {
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: '12px',
        padding: '9px 0',
        borderBottom: '1px solid rgba(148, 163, 184, 0.12)',
      },
    }, [
      el('span', { style: { color: 'var(--text-dim)', fontSize: '13px' }, text: label }),
      el('span', {
        style: {
          fontSize: '13px',
          fontWeight: '600',
          color: opts.color || 'var(--text)',
          textAlign: 'right',
          wordBreak: 'break-all',
        },
        text: value,
      }),
    ]);
  }

  function buildPanel(status) {
    const bot = status.bot || {};
    const connected = !!bot.connected;
    const target = status.target_id
      ? status.target_type + ' · ' + status.target_id
      : '未配置';

    const children = [
      el('div', {
        style: {
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '6px',
        },
      }, [
        el('div', {
          style: { display: 'flex', alignItems: 'center', fontSize: '15px', fontWeight: '700' },
        }, [
          statusDot(connected),
          el('span', { text: connected ? '协议端已连接' : '协议端未连接' }),
        ]),
        el('span.badge', {
          style: {
            padding: '3px 10px',
            borderRadius: '999px',
            fontSize: '12px',
            background: connected ? 'rgba(74, 222, 128, 0.16)' : 'rgba(148, 163, 184, 0.16)',
            color: connected ? '#4ade80' : 'var(--text-dim)',
          },
          text: status.enabled ? '推送已开启' : '推送已关闭',
        }),
      ]),
      el('div', {
        style: { fontSize: '12px', color: 'var(--text-dim)', marginBottom: '10px' },
        text: 'OneBot v11 · 报告与预警经此通道推送到 QQ 等平台',
      }),
      row('机器人账号', bot.self_id || '—'),
      row('适配器', (bot.adapters && bot.adapters.length ? bot.adapters.join(', ') : '—')),
      row('推送目标', target),
      row('推送周期', status.report_period || '—'),
      row('推送时间', (status.schedule_times || []).join('、') || '—'),
      row('发送图片', status.send_image ? '是' : '否'),
      row('发送文字摘要', status.send_summary_text ? '是' : '否'),
      row('最近发送', bot.last_sent_at || '尚未发送'),
    ];

    if (bot.last_error) {
      children.push(
        el('div', {
          style: {
            marginTop: '12px',
            padding: '10px 12px',
            borderRadius: '10px',
            fontSize: '12px',
            lineHeight: '1.6',
            background: 'rgba(248, 113, 113, 0.12)',
            color: '#f87171',
            wordBreak: 'break-all',
          },
        }, [
          el('div', { style: { fontWeight: '700', marginBottom: '3px' }, text: '最近错误' }),
          el('div', { text: bot.last_error }),
        ])
      );
    }

    if (!connected) {
      children.push(
        el('div', {
          style: {
            marginTop: '12px',
            padding: '10px 12px',
            borderRadius: '10px',
            fontSize: '12px',
            lineHeight: '1.7',
            background: 'rgba(94, 234, 212, 0.08)',
            color: 'var(--text-dim)',
          },
        }, [
          el('div', { style: { fontWeight: '700', color: 'var(--text)', marginBottom: '4px' }, text: '如何连接协议端' }),
          el('div', { text: '1. 启动 NapCat / Lagrange 等 OneBot v11 实现；' }),
          el('div', { text: '2. 反向 WS 地址填 ws://127.0.0.1:2525/onebot/v11/ws；' }),
          el('div', { text: '3. 在「设置」页填写 push.target_id（接收报告的 QQ 号或群号）。' }),
        ])
      );
    }

    return children;
  }

  /* ============================================================ 弹窗 */

  function openPanel() {
    const root = FF.$('#modal-root');
    if (!root) return;

    const body = el('div', { style: { minHeight: '180px' } }, [
      el('div', {
        style: { color: 'var(--text-dim)', fontSize: '13px', padding: '20px 0', textAlign: 'center' },
        text: '正在获取协议端状态…',
      }),
    ]);

    const close = function () {
      stopPoll();
      overlay.remove();
    };

    const overlay = el('div', {
      style: {
        position: 'fixed',
        inset: '0',
        zIndex: '9998',
        display: 'grid',
        placeItems: 'center',
        background: 'rgba(5, 8, 16, 0.66)',
        backdropFilter: 'blur(6px)',
        padding: '20px',
      },
      on: {
        click: function (event) {
          if (event.target === overlay) close();
        },
      },
    }, [
      el('div.card', {
        style: {
          maxWidth: '480px',
          width: '100%',
          maxHeight: '86vh',
          overflowY: 'auto',
          animation: 'toastIn 280ms cubic-bezier(0.34, 1.56, 0.64, 1)',
        },
      }, [
        el('div', {
          style: {
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '14px',
          },
        }, [
          el('h3', { style: { margin: '0', fontSize: '17px' }, text: '🤖 协议端状态' }),
          el('button.btn.btn-ghost', {
            text: '关闭',
            style: { padding: '4px 12px', fontSize: '13px' },
            on: { click: close },
          }),
        ]),
        body,
      ]),
    ]);

    root.appendChild(overlay);

    const refresh = async function () {
      try {
        const status = await fetchStatus();
        lastStatus = status;
        FF.clear(body);
        buildPanel(status).forEach(function (node) {
          body.appendChild(node);
        });
        updateTopbarButton(status);
      } catch (err) {
        FF.clear(body);
        body.appendChild(
          el('div', {
            style: { padding: '18px 0', color: '#f87171', fontSize: '13px', lineHeight: '1.7' },
          }, [
            el('div', { style: { fontWeight: '700', marginBottom: '4px' }, text: '无法获取协议端状态' }),
            el('div', { text: (err && err.message) || '请求失败，请确认后端服务已启动。' }),
          ])
        );
      }
    };

    refresh();
    stopPoll();
    pollTimer = window.setInterval(refresh, 5000);
  }

  /* ============================================================ 顶栏按钮 */

  function updateTopbarButton(status) {
    const btn = FF.$('#bot-toggle');
    if (!btn) return;
    const connected = status && status.bot && status.bot.connected;
    btn.title = connected ? '协议端已连接 · 点击查看详情' : '协议端未连接 · 点击查看详情';
    btn.style.opacity = connected ? '1' : '0.55';
  }

  async function refreshTopbar() {
    try {
      const status = await fetchStatus();
      lastStatus = status;
      updateTopbarButton(status);
      return status;
    } catch (err) {
      updateTopbarButton({ bot: { connected: false } });
      return null;
    }
  }

  function stopPoll() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  /* ============================================================ 内嵌卡片 */

  /** 供设置页内嵌使用的卡片节点（不轮询，随视图生命周期销毁）。 */
  function buildEmbeddedCard() {
    const body = el('div', {
      style: { color: 'var(--text-dim)', fontSize: '13px', padding: '14px 0' },
      text: '正在获取协议端状态…',
    });

    const card = el('div.card', {}, [
      el('div.card-header', {}, [
        el('div.card-title', { text: '🤖 协议端连接' }),
        el('button.btn.btn-ghost', {
          text: '刷新',
          style: { padding: '4px 12px', fontSize: '13px' },
          on: {
            click: async function () {
              await load();
              FF.toast('协议端状态已刷新', 'info', 1400);
            },
          },
        }),
      ]),
      body,
    ]);

    async function load() {
      try {
        const status = await fetchStatus();
        updateTopbarButton(status);
        FF.clear(body);
        buildPanel(status).forEach(function (node) {
          body.appendChild(node);
        });
      } catch (err) {
        FF.clear(body);
        body.appendChild(
          el('div', {
            style: { color: '#f87171', fontSize: '13px', lineHeight: '1.7' },
            text: '获取失败：' + ((err && err.message) || '后端未连接'),
          })
        );
      }
    }

    load();
    return { node: card, reload: load };
  }

  /* ============================================================ 初始化 */

  function init() {
    const btn = FF.$('#bot-toggle');
    if (btn) {
      btn.addEventListener('click', openPanel);
    }
    // 启动时拉一次，让顶栏按钮的明暗反映真实连接状态
    window.setTimeout(refreshTopbar, 1500);
  }

  window.FFBotStatus = {
    init: init,
    refresh: refreshTopbar,
    open: openPanel,
    buildEmbeddedCard: buildEmbeddedCard,
    getLast: function () {
      return lastStatus;
    },
  };
})();
