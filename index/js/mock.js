/* ==========================================================================
   流萤 Firefly · 模拟数据
   --------------------------------------------------------------------------
   用途：**仅在演示模式（config.yaml: app.debug = true）下使用**。

   ⚠️ 正式模式下这份数据永远不会出现在界面上：
      `FFApi.withFallback` 在 debug=false 时失败即报错，不做 mock 降级。

   数据每次页面加载随机生成（见下方 seed 的说明），避免演示时
   反复看到同一组写死的数字。
   ========================================================================== */

(function () {
  'use strict';

  /* ============================================================ 类别 */
  const CATEGORIES = [
    { id: 'c-food', name: '餐饮', icon: '🍜', direction: 'expense', color: '#f472b6', tax_account: '业务招待费', deductible: true },
    { id: 'c-transport', name: '交通', icon: '🚕', direction: 'expense', color: '#38bdf8', tax_account: '差旅费', deductible: true },
    { id: 'c-office', name: '办公', icon: '📎', direction: 'expense', color: '#5eead4', tax_account: '办公费', deductible: true },
    { id: 'c-equip', name: '设备', icon: '💻', direction: 'expense', color: '#a78bfa', tax_account: '固定资产', deductible: true },
    { id: 'c-rent', name: '房租', icon: '🏠', direction: 'expense', color: '#fbbf24', tax_account: '租赁费', deductible: true },
    { id: 'c-salary', name: '工资', icon: '👥', direction: 'expense', color: '#f87171', tax_account: '工资薪金', deductible: true },
    { id: 'c-marketing', name: '推广', icon: '📣', direction: 'expense', color: '#fb923c', tax_account: '广告宣传费', deductible: true },
    { id: 'c-service', name: '服务', icon: '🛠️', direction: 'expense', color: '#2dd4bf', tax_account: '服务费', deductible: true },
    { id: 'c-software', name: '订阅', icon: '☁️', direction: 'expense', color: '#60a5fa', tax_account: '办公费', deductible: true },
    { id: 'c-tax', name: '税费', icon: '🧾', direction: 'expense', color: '#c084fc', tax_account: '税金及附加', deductible: true },
    { id: 'c-misc', name: '其他', icon: '📦', direction: 'expense', color: '#94a3b8', tax_account: '其他', deductible: false },
    { id: 'c-sales', name: '营业收入', icon: '💰', direction: 'income', color: '#4ade80', tax_account: '主营业务收入', deductible: false },
    { id: 'c-other-in', name: '其他收入', icon: '🎁', direction: 'income', color: '#86efac', tax_account: '其他业务收入', deductible: false },
  ];

  /* ============================================================ 交易生成 */
  /**
   * 伪随机数发生器。
   *
   * 种子的选择分两种情况，这是刻意的设计：
   *
   * - **演示模式（app.debug=true）**：种子取自当前时间戳，因此每次
   *   刷新页面都会得到一套全新的数据。目的是让答辩演示看起来像
   *   真实系统在持续产生数据，而不是每次都是同一组写死的数字。
   *
   * - **正式模式**：这份 mock 根本不会被用到（正式模式下所有请求
   *   都走真实后端，失败就报错、不降级），所以种子取什么都无所谓。
   *
   * 之所以用自实现 LCG 而不是 Math.random()，是为了保证**同一次会话内
   * 数据自洽**：一次页面加载里 buildTransactions 可能被调用多次
   * （总览、报告、预算各算一遍），必须得到相同结果，否则会出现
   * "总览说花了 2 万、报告说花了 3 万"的诡异现象。
   * 所以只在脚本加载时定一次种子，之后连续取数。
   */
  let seed = window.FF_MOCK_SEED || (Date.now() % 2147483647) || 20260310;
  function rnd() {
    seed = (seed * 9301 + 49297) % 233280;
    return seed / 233280;
  }

  function pick(list) {
    return list[Math.floor(rnd() * list.length)];
  }

  function between(min, max) {
    return min + rnd() * (max - min);
  }

  /** 交易模板：[类别, 描述, 金额区间, 权重] */
  const TEMPLATES = {
    'c-food': [
      ['团队工作餐', [45, 180], 5],
      ['客户招待餐', [260, 880], 2],
      ['咖啡下午茶', [18, 68], 4],
      ['加班外卖', [25, 75], 3],
    ],
    'c-transport': [
      ['市内打车', [18, 86], 6],
      ['高铁出行', [180, 620], 2],
      ['停车费', [5, 30], 3],
      ['机票', [680, 1680], 1],
    ],
    'c-office': [
      ['耗材采购', [30, 220], 3],
      ['打印复印', [10, 60], 2],
      ['办公用品', [50, 350], 3],
    ],
    'c-equip': [
      ['显示器采购', [880, 2400], 1],
      ['键盘鼠标', [120, 480], 2],
      ['服务器续费', [1200, 3600], 1],
    ],
    'c-rent': [['办公场地租金', [6800, 6800], 1]],
    'c-salary': [['员工工资发放', [18000, 24000], 1]],
    'c-marketing': [
      ['信息流投放', [1200, 5600], 2],
      ['KOL 合作', [2000, 8800], 1],
      ['线下物料', [300, 1500], 2],
    ],
    'c-service': [
      ['法律咨询', [600, 2200], 1],
      ['代理记账', [300, 900], 2],
      ['设计外包', [800, 3200], 2],
    ],
    'c-software': [
      ['云服务器', [128, 460], 3],
      ['协作工具订阅', [45, 180], 3],
      ['域名解析', [60, 120], 2],
    ],
    'c-tax': [
      ['增值税及附加', [800, 3200], 2],
      ['印花税', [50, 300], 2],
    ],
    'c-misc': [
      ['快递物流', [15, 90], 3],
      ['零星支出', [20, 160], 3],
    ],
    'c-sales': [
      ['项目回款 A 期', [28000, 52000], 3],
      ['产品销售收入', [12000, 38000], 4],
      ['服务费收入', [6800, 18000], 3],
    ],
    'c-other-in': [
      ['补贴收入', [1000, 5000], 1],
      ['利息收入', [80, 420], 2],
    ],
  };

  function buildTransactions() {
    const list = [];
    const today = new Date();
    const spanDays = 120; // 覆盖约 4 个月，便于月度对比

    for (let dayOffset = spanDays; dayOffset >= 0; dayOffset--) {
      const date = new Date(today);
      date.setDate(today.getDate() - dayOffset);

      // 月初固定项：房租、工资
      const isMonthStart = date.getDate() === 1;
      if (isMonthStart) {
        date.setHours(9, 30, 0, 0);
        list.push(make(date, 'c-rent', '办公场地租金', 6800, '每月固定租金'));
        list.push(make(date, 'c-salary', '员工工资发放', 18000 + Math.round(between(0, 6000) / 100) * 100, '本月工资'));
        list.push(make(date, 'c-software', '云服务器续费', 460, '生产环境主机'));
      }

      // 每日随机 0-4 笔
      const count = Math.floor(rnd() * 5);
      for (let i = 0; i < count; i++) {
        const categoryId = pick(Object.keys(TEMPLATES));
        const candidates = TEMPLATES[categoryId];
        const totalWeight = candidates.reduce(function (acc, item) {
          return acc + item[2];
        }, 0);
        let roll = rnd() * totalWeight;
        let chosen = candidates[0];
        for (let j = 0; j < candidates.length; j++) {
          roll -= candidates[j][2];
          if (roll <= 0) {
            chosen = candidates[j];
            break;
          }
        }

        const amount = Math.round(between(chosen[1][0], chosen[1][1]) * 100) / 100;
        const tradeDate = new Date(date);
        tradeDate.setHours(
          Math.floor(between(8, 22)),
          Math.floor(rnd() * 60),
          Math.floor(rnd() * 60),
          0
        );
        list.push(make(tradeDate, categoryId, chosen[0], amount, chosen[0]));
      }
    }

    // 注入若干异常样本（用于异常检测演示）
    const anomalySpecs = [
      { daysAgo: 8, categoryId: 'c-misc', memo: '零星支出', amount: 16800, reason: '单笔金额显著高于该类别历史水平' },
      { daysAgo: 21, categoryId: 'c-food', memo: '餐饮小结', amount: 9860, reason: '疑似个人消费混入，建议核实业务关联性' },
      { daysAgo: 34, categoryId: 'c-marketing', memo: '线下物料', amount: 26400, reason: '广告费单笔超阈值，关注税前扣除限额' },
    ];

    anomalySpecs.forEach(function (spec) {
      const date = new Date(today);
      date.setDate(today.getDate() - spec.daysAgo);
      date.setHours(14, 20, 0, 0);
      const txn = make(date, spec.categoryId, spec.memo, spec.amount, spec.memo);
      txn.is_anomaly = true;
      txn.anomaly_reason = spec.reason;
      txn.is_personal_mixed = spec.categoryId === 'c-food';
      list.push(txn);
    });

    return list.sort(function (a, b) {
      return new Date(b.trade_time) - new Date(a.trade_time);
    });
  }

  let txnIndex = 0;
  function make(tradeTime, categoryId, memo, amount, note) {
    txnIndex += 1;
    const category = CATEGORIES.filter(function (c) {
      return c.id === categoryId;
    })[0] || CATEGORIES[0];
    return {
      id: 'txn-' + String(txnIndex).padStart(4, '0'),
      category_id: categoryId,
      category_name: category.name,
      category_icon: category.icon,
      direction: category.direction,
      amount: amount,
      currency: 'CNY',
      trade_time: tradeTime.toISOString(),
      memo: memo,
      note: note || '',
      account_id: 'acc-main',
      account_name: '基本户',
      counterparty: memo,
      source: 'manual',
      confidence: 0.94,
      is_confirmed: true,
      is_duplicate: false,
      is_anomaly: !!false,
      anomaly_reason: '',
      is_personal_mixed: false,
      has_invoice: rnd() > 0.35,
    };
  }

  const TRANSACTIONS = buildTransactions();

  /* ============================================================ 预算 */
  const BUDGETS = [
    { id: 'b1', category_id: 'c-food', category_name: '餐饮', category_icon: '🍜', amount: 8000, period: 'monthly', alert_enabled: true },
    { id: 'b2', category_id: 'c-transport', category_name: '交通', category_icon: '🚕', amount: 4000, period: 'monthly', alert_enabled: true },
    { id: 'b3', category_id: 'c-marketing', category_name: '推广', category_icon: '📣', amount: 15000, period: 'monthly', alert_enabled: true },
    { id: 'b4', category_id: 'c-office', category_name: '办公', category_icon: '📎', amount: 2000, period: 'monthly', alert_enabled: true },
    { id: 'b5', category_id: 'c-service', category_name: '服务', category_icon: '🛠️', amount: 6000, period: 'monthly', alert_enabled: false },
    { id: 'b6', category_id: 'c-equip', category_name: '设备', category_icon: '💻', amount: 3000, period: 'monthly', alert_enabled: true },
  ];

  /* ============================================================ 账户 */
  const ACCOUNTS = [
    { id: 'acc-main', name: '基本户', kind: 'bank', initial_balance: 186000, include_in_net: true },
    { id: 'acc-pay', name: '支付账户', kind: 'wallet', initial_balance: 12400, include_in_net: true },
    { id: 'acc-cash', name: '备用金', kind: 'cash', initial_balance: 3000, include_in_net: true },
  ];

  /* ============================================================ 税务自检 */
  const TAX_CHECKS = [
    {
      check_key: 'tax_burden',
      title: '增值税税负率偏离',
      level: 'medium',
      actual_value: 0.86,
      threshold_value: 1.0,
      unit: '%',
      description:
        '本周期增值税税负率 0.86%，低于小规模纳税人行业参考下限 1.0%。税负率长期偏低易触发税务系统预警。',
      suggestion: '核对收入确认时点是否完整，确认无未开票收入遗漏；若属正常经营波动，留存说明材料备查。',
      policy_basis: '《增值税暂行条例》及其实施细则关于纳税义务发生时间的规定',
    },
    {
      check_key: 'vat_threshold',
      title: '小规模纳税人免征额临界',
      level: 'low',
      actual_value: 286400,
      threshold_value: 300000,
      unit: '元/季',
      description:
        '本季度销售额 28.64 万元，距 30 万元免征额仅剩 1.36 万元。单笔大额开票即可能突破免征线。',
      suggestion: '评估剩余合同的开票节奏，必要时与客户协商跨季度开票，避免临界点被动转为一般纳税人。',
      policy_basis: '财政部 税务总局关于增值税小规模纳税人减免增值税政策的公告',
    },
    {
      check_key: 'invoice_mismatch',
      title: '进销项匹配度',
      level: 'low',
      actual_value: 92.4,
      threshold_value: 85.0,
      unit: '%',
      description: '本期有票支出占全部支出的 92.4%，进销项匹配良好，无倒挂风险。',
      suggestion: '保持现有凭证管理习惯，继续要求员工报销时上传发票。',
      policy_basis: '《企业所得税税前扣除凭证管理办法》',
    },
    {
      check_key: 'deduction_limit',
      title: '业务招待费扣除限额',
      level: 'high',
      actual_value: 18760,
      threshold_value: 12480,
      unit: '元',
      description:
        '本期业务招待费 1.876 万元，按发生额 60% 与营业收入 5‰ 孰低原则，可扣除上限约 1.248 万元，超出部分 6280 元需纳税调增。',
      suggestion: '将部分客户招待转为会议费或差旅费口径（需有会议通知、签到表等佐证），可降低调增金额。',
      policy_basis: '《企业所得税法实施条例》第四十三条：业务招待费按发生额 60% 扣除，最高不得超过当年销售收入的 5‰',
    },
    {
      check_key: 'personal_mixed',
      title: '个人消费混入风险',
      level: 'high',
      actual_value: 9860,
      threshold_value: 0,
      unit: '元',
      description: '检测到 1 笔金额 9860 元的餐饮支出，时间与地点特征接近个人消费，存在被认定为与经营无关支出的风险。',
      suggestion: '补充该笔支出的业务关联说明与参与人清单；若确属个人消费，及时做纳税调增或从公司账户抽回。',
      policy_basis: '《企业所得税法》第八条：与取得收入有关的、合理的支出准予扣除',
    },
    {
      check_key: 'missing_invoice',
      title: '无票支出占比',
      level: 'medium',
      actual_value: 7.6,
      threshold_value: 5.0,
      unit: '%',
      description: '本期无票支出占比 7.6%，高于 5% 的参考线，合计约 4.2 万元无法税前扣除。',
      suggestion: '针对小额零星支出，可要求对方开具电子发票；500 元以下个人零星支出可用收款凭证入账。',
      policy_basis: '国家税务总局关于小额零星经营业务税前扣除凭证的公告',
    },
  ];

  /* ============================================================ 优化建议 */
  const ACTIONS = [
    {
      priority: 'high',
      title: '将业务招待费转为会议费口径',
      detail: '本期招待费超限 6280 元需纳税调增，按 25% 税率计约增加税负 1570 元。',
      action: '对带有明确会议性质的支出补充会议通知与签到表，单独归集到「会议费」明细科目。',
      impact: '预计减少应纳税所得额 6280 元',
    },
    {
      priority: 'high',
      title: '核查 1 笔疑似个人消费',
      detail: '金额 9860 元的餐饮支出缺业务关联佐证，若被认定为个人消费需全额调增。',
      action: '调取该笔支出的小票与参与人信息，补齐业务说明；无法佐证的做纳税调增处理。',
      impact: '规避潜在调增 9860 元',
    },
    {
      priority: 'medium',
      title: '控制推广费税前扣除限额',
      detail: '本期广告宣传费 3.86 万元，未超营业收入 15% 限额，但增速较快（环比 +42%）。',
      action: '将部分投放费用归集为「业务宣传费」，并留存投放合同与效果报告作为佐证。',
      impact: '保持扣除限额安全边际',
    },
    {
      priority: 'medium',
      title: '补齐无票支出凭证',
      detail: '无票支出 4.2 万元无法税前扣除，估算多缴企业所得税约 1.05 万元。',
      action: '对单笔 500 元以下零星支出采用收款凭证 + 内部凭证入账；其余联系供应商补开发票。',
      impact: '预计可增加税前扣除 3.5 万元',
    },
    {
      priority: 'low',
      title: '评估纳税人身份临界点',
      detail: '本季度销售额 28.64 万元，接近 30 万元免征额。',
      action: '跟踪剩余合同开票计划，必要时跨季度安排开票节奏。',
      impact: '避免被动转为一般纳税人',
    },
    {
      priority: 'low',
      title: '建立月度预算复核机制',
      detail: '餐饮与交通两类连续 2 个月超预算 80%，缺少事前控制。',
      action: '在流萤中为超支类别设置 80% 预警阈值，触发时通过 Bot 推送到负责人。',
      impact: '预计月度可压缩支出 8%-12%',
    },
  ];

  /* ============================================================ 报告 payload */
  function buildReportPayload() {
    const now = new Date();
    const range = window.FF.periodRange('monthly', now);

    const inRange = function (list) {
      return list.filter(function (t) {
        const time = new Date(t.trade_time).getTime();
        return time >= range.start.getTime() && time <= range.end.getTime();
      });
    };

    const current = inRange(TRANSACTIONS);

    const previousRange = window.FF.periodRange('monthly', new Date(range.start.getTime() - 86400000));
    const previous = TRANSACTIONS.filter(function (t) {
      const time = new Date(t.trade_time).getTime();
      return time >= previousRange.start.getTime() && time <= previousRange.end.getTime();
    });

    const expense = function (list) {
      return window.FF.sum(
        list.filter(function (t) {
          return t.direction === 'expense';
        }),
        function (t) {
          return t.amount;
        }
      );
    };
    const income = function (list) {
      return window.FF.sum(
        list.filter(function (t) {
          return t.direction === 'income';
        }),
        function (t) {
          return t.amount;
        }
      );
    };

    const curIncome = income(current);
    const curExpense = expense(current);
    const prevIncome = income(previous);
    const prevExpense = expense(previous);

    // 类别结构
    const expenseList = current.filter(function (t) {
      return t.direction === 'expense';
    });
    const grouped = window.FF.groupBy(expenseList, function (t) {
      return t.category_id;
    });
    const catMeta = {};
    CATEGORIES.forEach(function (c) {
      catMeta[c.id] = c;
    });

    const categories = window.FF
      .sortDesc(
        Object.keys(grouped).map(function (key) {
          const total = window.FF.sum(grouped[key], function (t) {
            return t.amount;
          });
          const meta = catMeta[key] || { name: key, icon: '📦', color: '#94a3b8' };
          return {
            category_id: key,
            name: meta.name,
            icon: meta.icon,
            color: meta.color,
            amount: total,
            count: grouped[key].length,
          };
        }),
        function (c) {
          return c.amount;
        }
      )
      .map(function (c) {
        c.ratio = curExpense > 0 ? (c.amount / curExpense) * 100 : 0;
        return c;
      });

    // 预算执行
    const budgets = BUDGETS.map(function (b) {
      const used = grouped[b.category_id]
        ? window.FF.sum(grouped[b.category_id], function (t) {
            return t.amount;
          })
        : 0;
      return {
        category_id: b.category_id,
        category_name: b.category_name,
        icon: b.category_icon,
        budget: b.amount,
        used: used,
        ratio: b.amount > 0 ? (used / b.amount) * 100 : 0,
        status: used > b.amount ? 'over' : used > b.amount * 0.8 ? 'warn' : 'ok',
      };
    });

    // 归因（瀑布）：三大类环比变化
    const prevGrouped = window.FF.groupBy(
      previous.filter(function (t) {
        return t.direction === 'expense';
      }),
      function (t) {
        return t.category_id;
      }
    );
    const attribution = categories.slice(0, 3).map(function (c) {
      const prevAmount = prevGrouped[c.category_id]
        ? window.FF.sum(prevGrouped[c.category_id], function (t) {
            return t.amount;
          })
        : 0;
      return {
        label: c.name,
        delta: c.amount - prevAmount,
        current: c.amount,
        previous: prevAmount,
      };
    });
    const restCurrent = curExpense - window.FF.sum(attribution, function (a) {
      return a.current;
    });
    const restPrevious = prevExpense - window.FF.sum(attribution, function (a) {
      return a.previous;
    });
    attribution.push({ label: '其他', delta: restCurrent - restPrevious, current: restCurrent, previous: restPrevious });

    // 日趋势
    const trend = [];
    const dayCount = Math.round((range.end - range.start) / 86400000) + 1;
    for (let i = 0; i < dayCount; i++) {
      const day = new Date(range.start.getTime() + i * 86400000);
      const dayEnd = new Date(day.getTime() + 86400000);
      const dayExpense = window.FF.sum(
        expenseList.filter(function (t) {
          const time = new Date(t.trade_time).getTime();
          return time >= day.getTime() && time < dayEnd.getTime();
        }),
        function (t) {
          return t.amount;
        }
      );
      trend.push({ date: day.toISOString(), expense: dayExpense });
    }

    const taxBurden = curIncome > 0 ? ((curIncome * 0.01) / curIncome) * 100 : 0;
    const grossMargin = curIncome > 0 ? ((curIncome - curExpense) / curIncome) * 100 : 0;

    return {
      period: 'monthly',
      period_start: range.start.toISOString(),
      period_end: range.end.toISOString(),
      title: window.FF.formatDate(range.start, 'YYYY年MM月') + '财务税务分析报告',
      generated_at: now.toISOString(),
      is_sample: true,

      overview: {
        income: curIncome,
        expense: curExpense,
        net: curIncome - curExpense,
        prev_income: prevIncome,
        prev_expense: prevExpense,
        prev_net: prevIncome - prevExpense,
        gross_margin: grossMargin,
        tax_burden: taxBurden,
        transaction_count: current.length,
        avg_expense: current.length > 0 ? curExpense / current.length : 0,
        invoice_ratio: 92.4,
        balance: window.FF.sum(ACCOUNTS, function (a) {
          return a.initial_balance;
        }),
      },

      highlights: [
        {
          label: '本期净利',
          value: (curIncome - curExpense).toFixed(0),
          text: '收入 ' + curIncome.toFixed(0) + ' 元，支出 ' + curExpense.toFixed(0) + ' 元',
          tone: curIncome - curExpense >= 0 ? 'positive' : 'danger',
        },
        {
          label: '毛利率',
          value: grossMargin.toFixed(1) + '%',
          text: '较上期' + (grossMargin > 30 ? '处于健康区间' : '偏低，需关注成本结构'),
          tone: grossMargin > 30 ? 'positive' : 'warning',
        },
        {
          label: '无票支出占比',
          value: '7.6%',
          text: '高于 5% 参考线，约 4.2 万元无法税前扣除',
          tone: 'warning',
        },
        {
          label: '业务招待费超限',
          value: '6,280 元',
          text: '需纳税调增，预计增加税负 1,570 元',
          tone: 'danger',
        },
        {
          label: '预算执行',
          value: budgets.filter(function (b) { return b.status === 'over'; }).length + ' 类超支',
          text: '餐饮、交通接近预算上限',
          tone: 'warning',
        },
      ],

      categories: categories,
      budgets: budgets,
      attribution: attribution,
      trend: trend,
      tax_checks: TAX_CHECKS,
      actions: ACTIONS,

      summary_text:
        '本期（' +
        window.FF.formatDate(range.start, 'YYYY-MM-DD') +
        ' 至 ' +
        window.FF.formatDate(range.end, 'YYYY-MM-DD') +
        '）共记录 ' +
        current.length +
        ' 笔交易，实现营业收入 ' +
        curIncome.toFixed(2) +
        ' 元，发生支出 ' +
        curExpense.toFixed(2) +
        ' 元，净利 ' +
        (curIncome - curExpense).toFixed(2) +
        ' 元，毛利率 ' +
        grossMargin.toFixed(1) +
        '%。\n\n' +
        '财务层面：支出结构中' +
        (categories[0] ? categories[0].name : '—') +
        '占比最高（' +
        (categories[0] ? categories[0].ratio.toFixed(1) : '0') +
        '%，' +
        (categories[0] ? categories[0].amount.toFixed(0) : '0') +
        ' 元），' +
        budgets.filter(function (b) { return b.status !== 'ok'; }).length +
        ' 个类别预算执行率超过 80%，其中' +
        budgets.filter(function (b) { return b.status === 'over'; }).map(function (b) { return b.category_name; }).join('、') +
        '已超支。\n\n' +
        '税务层面：识别到 2 项高风险事项——业务招待费超税前扣除限额 6,280 元、1 笔 9,860 元支出存在个人消费混入嫌疑；另有 6 项中低风险提示。\n\n' +
        '优化方向：优先处理招待费口径重分类与疑似个人消费核查，两项合计可规避约 4,035 元的潜在税负增加；同步补齐无票支出凭证，预计可增加税前扣除 3.5 万元。',
    };
  }

  /* ============================================================ 报告列表 */
  const REPORTS = [
    {
      id: 'rpt-sample-1',
      title: window.FF.formatDate(new Date(), 'YYYY年MM月') + '财务税务分析报告',
      period: 'monthly',
      period_start: window.FF.periodRange('monthly').start.toISOString(),
      period_end: window.FF.periodRange('monthly').end.toISOString(),
      status: 'completed',
      created_at: new Date().toISOString(),
      elapsed_ms: 3840,
      is_pushed: false,
    },
    {
      id: 'rpt-sample-2',
      title: window.FF.formatDate(new Date(Date.now() - 30 * 86400000), 'YYYY年MM月') + '财务税务分析报告',
      period: 'monthly',
      period_start: window.FF.periodRange('monthly', new Date(Date.now() - 30 * 86400000)).start.toISOString(),
      period_end: window.FF.periodRange('monthly', new Date(Date.now() - 30 * 86400000)).end.toISOString(),
      status: 'completed',
      created_at: new Date(Date.now() - 30 * 86400000).toISOString(),
      elapsed_ms: 4120,
      is_pushed: true,
    },
  ];

  /* ============================================================ 审计统计 */
  const AUDIT_STATS = {
    total_suggestions: 46,
    adopted: 24,
    rejected: 8,
    pending: 14,
    adoption_rate: 52.2,
    with_ai_rate: 61.5,
    without_ai_rate: 38.0,
    by_kind: [
      { kind: 'category', label: '分类建议', total: 18, adopted: 12, rate: 66.7 },
      { kind: 'anomaly', label: '异常提示', total: 11, adopted: 7, rate: 63.6 },
      { kind: 'tax_optimize', label: '税务优化', total: 9, adopted: 3, rate: 33.3 },
      { kind: 'budget', label: '预算控制', total: 8, adopted: 2, rate: 25.0 },
    ],
    logs: [
      { id: 'log-1', action: 'generate_report', target_type: 'report', target_id: 'rpt-sample-1', operator: 'user', channel: 'web', detail: '生成月度报告', created_at: new Date(Date.now() - 3600000).toISOString() },
      { id: 'log-2', action: 'adopt_suggestion', target_type: 'suggestion', target_id: 'sg-12', operator: 'user', channel: 'web', detail: '采纳「招待费口径重分类」建议', created_at: new Date(Date.now() - 7200000).toISOString() },
      { id: 'log-3', action: 'nl_parse', target_type: 'transaction', target_id: 'txn-0031', operator: 'ai', channel: 'web', detail: '自然语言解析成功，置信度 0.94', created_at: new Date(Date.now() - 10800000).toISOString() },
      { id: 'log-4', action: 'push_report', target_type: 'report', target_id: 'rpt-sample-2', operator: 'scheduler', channel: 'bot', detail: '定时推送报告图片至 QQ 群', created_at: new Date(Date.now() - 86400000).toISOString() },
      { id: 'log-5', action: 'create_transaction', target_type: 'transaction', target_id: 'txn-0044', operator: 'user', channel: 'bot', detail: '通过对话记录一笔支出', created_at: new Date(Date.now() - 90000000).toISOString() },
    ],
  };

  /* ============================================================ AI 解析示例 */
  const NL_RESPONSE = {
    items: [
      {
        direction: 'expense',
        amount: 38,
        category_name: '交通',
        category_id: 'c-transport',
        memo: '打车',
        trade_time: new Date(Date.now() - 86400000).toISOString(),
        counterparty: '打车',
        confidence: 0.93,
        raw_text: '昨天打车 38',
      },
      {
        direction: 'expense',
        amount: 260,
        category_name: '餐饮',
        category_id: 'c-food',
        memo: '请客户吃饭',
        trade_time: new Date(Date.now() - 86400000).toISOString(),
        counterparty: '请客户吃饭',
        confidence: 0.88,
        raw_text: '请客户吃饭 260',
        remark: '该笔属于业务招待费，注意税前扣除限额',
      },
    ],
    model: 'mock',
    elapsed_ms: 620,
  };

  /* ============================================================ 对话式查询示例 */
  const ASK_RESPONSE = {
    question: '上月餐饮花了多少？',
    answer: '上月餐饮支出合计 7,842.50 元，共 26 笔，占当月总支出的 18.2%。较前月下降 6.4%，主要减少在客户招待餐一项（环比 -1,180 元）。',
    data: {
      amount: 7842.5,
      count: 26,
      ratio: 18.2,
      delta: -6.4,
    },
    intent: 'query_category_expense',
    params: { category: '餐饮', period: 'last_month' },
    chart: {
      labels: ['第1周', '第2周', '第3周', '第4周'],
      values: [1820, 2240, 1980, 1802.5],
    },
  };

  window.FFMock = {
    CATEGORIES: CATEGORIES,
    TRANSACTIONS: TRANSACTIONS,
    BUDGETS: BUDGETS,
    ACCOUNTS: ACCOUNTS,
    TAX_CHECKS: TAX_CHECKS,
    ACTIONS: ACTIONS,
    REPORTS: REPORTS,
    AUDIT_STATS: AUDIT_STATS,
    NL_RESPONSE: NL_RESPONSE,
    ASK_RESPONSE: ASK_RESPONSE,
    buildReportPayload: buildReportPayload,
  };
})();
