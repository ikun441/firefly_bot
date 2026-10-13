/**
 * 推送选项弹窗。
 *
 * 把「点推送」从一次性动作改成一次可配置的操作：
 *   · 推送类型：文字 / 图片 / 两者都发
 *   · 是否启用 AI 总结（启用后展开温度与字数上限）
 *
 * 为什么用弹窗而不是常驻表单：
 *   报告页的主线是「看报告」，推送是偶尔为之的动作。
 *   常驻表单会长期占据版面，而用户真正需要选择时又常常忘记它的存在。
 *
 * 默认值来自上一次的本地选择（localStorage），
 * 避免每次都从「both + 不开 AI」重新选一遍。
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'firefly-push-options';

  var DEFAULTS = {
    content_type: 'both',
    ai_summary: false,
    ai_temperature: 0.5,
    ai_max_tokens: 800,
  };

  function loadSaved() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return Object.assign({}, DEFAULTS);
      var parsed = JSON.parse(raw);
      return {
        content_type: parsed.content_type || DEFAULTS.content_type,
        ai_summary: !!parsed.ai_summary,
        ai_temperature:
          typeof parsed.ai_temperature === 'number'
            ? parsed.ai_temperature
            : DEFAULTS.ai_temperature,
        ai_max_tokens:
          typeof parsed.ai_max_tokens === 'number'
            ? parsed.ai_max_tokens
            : DEFAULTS.ai_max_tokens,
      };
    } catch (err) {
      return Object.assign({}, DEFAULTS);
    }
  }

  function save(opts) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(opts));
    } catch (err) {
      /* 隐私模式下 localStorage 会抛异常，忽略即可 —— 只是记不住偏好 */
    }
  }

  /**
   * 打开选项弹窗。
   * @returns {Promise<Object|null>} 用户确认则返回选项对象，取消返回 null
   */
  function openPushOptions() {
    var el = window.FF.el;
    var state = loadSaved();

    return new Promise(function (resolve) {
      var overlay = el('div', {
        style: {
          position: 'fixed',
          inset: '0',
          zIndex: '9998',
          display: 'grid',
          placeItems: 'center',
          background: 'rgba(5, 8, 16, 0.66)',
          backdropFilter: 'blur(6px)',
          padding: '20px',
          overflowY: 'auto',
        },
      });

      // ---------- 内容类型 ----------
      var typeButtons = [];
      var TYPE_OPTIONS = [
        { value: 'text', label: '📝 纯文字', hint: '信息密度高，加载快' },
        { value: 'image', label: '🖼️ 图片', hint: 'htmlrender 渲染的长图' },
        { value: 'both', label: '📦 文字 + 图片', hint: '两者都发，推荐' },
      ];

      function syncTypeButtons() {
        typeButtons.forEach(function (btn) {
          var active = btn.dataset.value === state.content_type;
          btn.classList.toggle('push-type-btn', true);
          btn.classList.toggle('is-active', active);
        });
      }

      var typeRow = el(
        'div',
        { className: 'push-type-row' },
        TYPE_OPTIONS.map(function (opt) {
          var btn = el('button', {
            type: 'button',
            className: 'push-type-btn',
            html:
              '<span class="push-type-label">' +
              opt.label +
              '</span><span class="push-type-hint">' +
              opt.hint +
              '</span>',
            on: {
              click: function () {
                state.content_type = opt.value;
                syncTypeButtons();
              },
            },
          });
          btn.dataset.value = opt.value;
          typeButtons.push(btn);
          return btn;
        })
      );

      // ---------- AI 总结 ----------
      var aiToggle = el('input', {
        type: 'checkbox',
        className: 'push-switch',
        on: {
          change: function () {
            state.ai_summary = aiToggle.checked;
            syncAiPanel();
          },
        },
      });
      aiToggle.checked = state.ai_summary;

      var tempInput = el('input', {
        type: 'range',
        min: '0',
        max: '1.5',
        step: '0.1',
        value: String(state.ai_temperature),
        className: 'push-range',
        on: {
          input: function () {
            state.ai_temperature = parseFloat(tempInput.value);
            tempValue.textContent = state.ai_temperature.toFixed(1);
          },
        },
      });

      var tempValue = el('span', {
        className: 'push-range-value',
        text: state.ai_temperature.toFixed(1),
      });

      var tokensInput = el('input', {
        type: 'number',
        min: '64',
        max: '4000',
        step: '64',
        value: String(state.ai_max_tokens),
        className: 'push-number',
        on: {
          input: function () {
            var v = parseInt(tokensInput.value, 10);
            if (!isNaN(v)) state.ai_max_tokens = Math.max(64, Math.min(4000, v));
          },
        },
      });

      var aiPanel = el('div', { className: 'push-ai-panel' }, [
        el('div', { className: 'push-field' }, [
          el('div', { className: 'push-field-head' }, [
            el('label', { className: 'push-field-label', text: '温度（temperature）' }),
            tempValue,
          ]),
          tempInput,
          el('p', {
            className: 'push-field-hint',
            text: '越低措辞越克制稳定，越高越灵活有主见。总结类任务建议 0.4 ~ 0.8。',
          }),
        ]),
        el('div', { className: 'push-field' }, [
          el('div', { className: 'push-field-head' }, [
            el('label', { className: 'push-field-label', text: '字数上限（max_tokens）' }),
          ]),
          tokensInput,
          el('p', {
            className: 'push-field-hint',
            text: '限制 AI 总结的长度，不影响上方的规则摘要。建议 300 ~ 1200。',
          }),
        ]),
        el('p', {
          className: 'push-note',
          text: '开启后会在规则摘要后面追加一段 AI 解读。规则摘要保证数字准确，AI 负责解释「为什么」与「怎么办」。',
        }),
      ]);

      function syncAiPanel() {
        aiToggle.checked = state.ai_summary;
        aiPanel.classList.toggle('is-disabled', !state.ai_summary);
        tempInput.disabled = !state.ai_summary;
        tokensInput.disabled = !state.ai_summary;
      }

      // ---------- 组装 ----------
      var box = el('div.card', { className: 'push-dialog' }, [
        el('h3', {
          text: '📤 推送到协议端',
          style: { fontSize: '17px', marginBottom: '6px' },
        }),
        el('p', {
          className: 'push-dialog-sub',
          text: '选择这次要推送的内容形态与是否附带 AI 解读。',
        }),

        el('div', { className: 'push-section' }, [
          el('label', { className: 'push-section-title', text: '推送类型' }),
          typeRow,
        ]),

        el('div', { className: 'push-section' }, [
          el('div', { className: 'push-section-head' }, [
            el('label', { className: 'push-section-title', text: 'AI 总结' }),
            el('label', { className: 'push-switch-wrap' }, [aiToggle, el('span', { className: 'push-switch-text', text: state.ai_summary ? '已启用' : '未启用' })]),
          ]),
          aiPanel,
        ]),

        el('div', { className: 'btn-group', style: { justifyContent: 'flex-end', marginTop: '6px' } }, [
          el('button.btn', {
            text: '取消',
            on: {
              click: function () {
                close(null);
              },
            },
          }),
          el('button.btn.btn-primary', {
            text: '确认推送',
            on: {
              click: function () {
                close(
                  Object.assign({}, state, {
                    ai_summary: aiToggle.checked,
                    ai_temperature: state.ai_temperature,
                    ai_max_tokens: state.ai_max_tokens,
                  })
                );
              },
            },
          }),
        ]),
      ]);

      overlay.appendChild(box);

      function onKey(event) {
        if (event.key === 'Escape') close(null);
      }

      function close(value) {
        document.removeEventListener('keydown', onKey);
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        resolve(value);
      }

      document.addEventListener('keydown', onKey);
      overlay.addEventListener('click', function (event) {
        if (event.target === overlay) close(null);
      });

      var root = document.getElementById('modal-root') || document.body;
      root.appendChild(overlay);

      syncTypeButtons();
      syncAiPanel();

      // 切开关时同步那个「已启用/未启用」文案
      aiToggle.addEventListener('change', function () {
        var textNode = box.querySelector('.push-switch-text');
        if (textNode) textNode.textContent = aiToggle.checked ? '已启用' : '未启用';
      });
    });
  }

  window.FFPushOptions = {
    open: openPushOptions,
    save: save,
    load: loadSaved,
  };
})();
