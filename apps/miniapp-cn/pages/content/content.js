// 内容管理（对齐 dual 后台「内容管理」）
// tabs 按场景联动：学习/家庭场景显示课程表、作业等；理财场景显示自选股、推荐股、资金管理。
// 读写同一份 dual_config，与 Web 后台互不覆盖。
const api = require('../../utils/api.js');
const dual = require('../../utils/dual.js');

const DAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

// 场景 → 内容 tabs（理财场景与行情/持仓配置吻合，学习场景保持原样）
const SCENE_TABS = {
  finance: [
    { key: 'watchlist', label: '自选股' },
    { key: 'reco', label: '推荐股' },
    { key: 'portfolio', label: '资金管理' },
    { key: 'gold', label: '黄金/期货' },
    { key: 'notes', label: '便签' }
  ],
  study: [
    { key: 'course', label: '课程表' },
    { key: 'notes', label: '便签' },
    { key: 'homework', label: '今日作业' },
    { key: 'reading', label: '阅读任务' },
    { key: 'album', label: '相册' }
  ],
  family: [
    { key: 'notes', label: '便签' },
    { key: 'bills', label: '缴费台账' },
    { key: 'express', label: '快递/取件码' },
    { key: 'schedule', label: '家庭日程' },
    { key: 'shopping', label: '采购清单' },
    { key: 'anniversaries', label: '纪念日' }
  ],
  office: [
    { key: 'notes', label: '便签' },
    { key: 'album', label: '相册' }
  ],
  custom: [
    { key: 'notes', label: '便签' },
    { key: 'album', label: '相册' }
  ]
};

const BILL_CATEGORIES = ['电费', '水费', '燃气费', '伙食费', '宽带费', '大模型费用', '其他'];
const EXPRESS_COMPANIES = ['顺丰', '京东', '中通', '圆通', '韵达', '申通', '邮政EMS', '极兔', '德邦', '其他'];
const EXPRESS_STATUS = [
  { key: 'ordered', label: '已下单' },
  { key: 'transit', label: '运输中' },
  { key: 'delivering', label: '派送中' },
  { key: 'signed', label: '已签收' },
  { key: 'exception', label: '问题件' }
];
const FAMILY_CATEGORIES = ['家庭', '就医', '课程', '出行', '纪念日', '其他'];
const REPEAT_OPTIONS = ['不重复', '每天', '每周', '每月', '每年'];
const REMINDER_OPTIONS = ['不提醒', '准时提醒', '提前5分钟', '提前15分钟', '提前30分钟', '提前1小时', '提前1天'];

function todayKey() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
function advanceDate(key, cycle) {
  const d = new Date(key + 'T00:00:00');
  if (cycle === 'yearly') d.setFullYear(d.getFullYear() + 1);
  else d.setMonth(d.getMonth() + 1);
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

Page({
  data: {
    tab: 'notes',
    tabs: SCENE_TABS.family,
    sceneKey: 'family',
    sceneName: '家庭',
    deviceName: '',
    mac: '',
    loading: true,
    saving: false,

    // 课程表
    ct: null,
    dayLabels: DAY_LABELS,
    visibleDays: [],
    curDay: 0,
    periodCount: 6,

    // 便签 / 作业 / 阅读
    notes: [],
    homework: [],
    reading: [],
    noteText: '',
    noteSender: '家长',
    notePersistent: false,
    hwText: '',
    readTitle: '',
    readProgress: '',

    // 相册
    albumPhotos: [],

    // 缴费台账
    bills: [],
    billView: [],
    billCategories: BILL_CATEGORIES,
    billName: '',
    billAmount: '',
    billCycleKey: 'monthly',
    billNextDue: todayKey(),
    billRemindDays: '3',

    // 快递 / 取件码
    express: [],
    expressView: [],
    expressCompanies: EXPRESS_COMPANIES,
    expressStatusLabels: EXPRESS_STATUS.map((s) => s.label),
    expCompanyIdx: 0,
    expPickupCode: '',
    expPickupPoint: '',
    expItem: '',
    expTrackingNo: '',

    // 家庭日程 / 纪念日（共用家庭事件编辑器）
    familySchedule: [],
    familyAnniversaries: [],
    memberOptions: ['全家'],
    familyCategories: FAMILY_CATEGORIES,
    repeatOptions: REPEAT_OPTIONS,
    reminderOptions: REMINDER_OPTIONS,
    evTitle: '',
    evDate: todayKey(),
    evTime: '09:00',
    evMemberIdx: 0,
    evCategoryIdx: 0,
    evRepeatIdx: 0,
    evReminderIdx: 2,
    evNote: '',

    // 采购清单
    familyShopping: [],
    shopText: '',

    // 理财场景：自选股 / 推荐股 / 资金管理
    finWatchlist: [],
    finQuoteView: [],
    finCodesInput: '',
    finBusy: false,
    finReco: [],
    finRecoDisclaimer: '',
    finGold: [],
    finPortfolio: null,
    pfCash: '',
    pfCode: '',
    pfShares: '',
    pfCost: ''
  },

  onLoad() {
    const dev = getApp().globalData.currentDevice;
    const mac = (dev && (dev.mac || dev.sn)) || '';
    if (!mac) {
      wx.showToast({ title: '请先选择设备', icon: 'none' });
      setTimeout(() => wx.navigateBack({ delta: 1 }), 800);
      return;
    }
    this.setData({ mac: mac, deviceName: (dev && dev.name) || '' });
    this.load();
  },

  // 从场景中心返回时重新读取 sceneKey，让内容配置跟随场景
  onShow() {
    if (!this._firstShowDone) { this._firstShowDone = true; return; }
    if (this.data.mac) this.load();
  },

  load() {
    this.setData({ loading: true });
    api.getDualConfig(this.data.mac)
      .then((r) => {
        const cfg = r.config || {};
        const content = dual.normalizeContent(cfg.content);
        const ct = content.courseTable;
        const members = (content.familyMembers || []).map((m) => m.name).filter(Boolean);
        const sceneKey = cfg.sceneKey || 'family';
        const scene = dual.SCENES.filter((s) => s.key === sceneKey)[0] || dual.SCENES[0];
        const tabs = SCENE_TABS[sceneKey] || SCENE_TABS.family;
        const tab = tabs.some((t) => t.key === this.data.tab) ? this.data.tab : tabs[0].key;
        this.setData({
          cfg: cfg,
          sceneKey: sceneKey,
          sceneName: scene.name,
          tabs: tabs,
          tab: tab,
          ct: ct,
          notes: content.notes,
          homework: content.homework,
          reading: content.reading,
          albumPhotos: (content.album && content.album.photos) || [],
          bills: content.bills,
          express: content.express,
          familySchedule: content.familySchedule,
          familyAnniversaries: content.familyAnniversaries,
          familyShopping: content.familyShopping,
          memberOptions: ['全家'].concat(members),
          periodCount: ct.periods.length,
          loading: false
        });
        this.refreshDays(ct.show_days);
        this.syncBills();
        this.syncExpress();
        this.loadFinanceTab(tab);
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
      });
  },

  refreshDays(showDays) {
    const n = showDays === 7 ? 7 : 5;
    const days = [];
    for (let i = 0; i < n; i++) days.push(i);
    const curDay = this.data.curDay >= n ? 0 : this.data.curDay;
    this.setData({ visibleDays: days, curDay: curDay });
  },

  onTab(e) {
    const key = e.currentTarget.dataset.key;
    this.setData({ tab: key });
    this.loadFinanceTab(key);
  },

  // —— 理财场景：按需加载自选 / 推荐 / 持仓 ——
  loadFinanceTab(key) {
    if (key === 'watchlist') this.refreshWatchlist();
    else if (key === 'reco' && !this.data.finReco.length) this.refreshReco();
    else if (key === 'gold' && !this.data.finGold.length) this.refreshGold();
    else if (key === 'portfolio' && !this.data.finPortfolio) this.refreshPortfolio();
  },

  refreshGold() {
    this.setData({ finBusy: true });
    api.getFinanceGold()
      .then((r) => this.setData({ finGold: r.items || [], finBusy: false }))
      .catch((e) => {
        this.setData({ finBusy: false });
        wx.showToast({ title: e.message || '行情加载失败', icon: 'none' });
      });
  },

  refreshWatchlist() {
    if (this.data.finBusy) return;
    this.setData({ finBusy: true });
    api.getFinanceWatchlist()
      .then((r) => {
        const list = r.watchlist || [];
        this.setData({ finWatchlist: list, finBusy: false });
        return list.length ? api.getFinanceQuotes(list.map((x) => x.code)) : Promise.resolve({ quotes: [] });
      })
      .then((r) => {
        const quotes = (r && r.quotes) || [];
        const by = {};
        quotes.forEach((q) => { by[q.code] = q; });
        const view = this.data.finWatchlist.map((x) => {
          const q = by[x.code] || {};
          const pct = q.pct;
          return Object.assign({}, x, {
            price: q.price, pct: pct,
            dir: pct == null ? '' : (pct > 0 ? 'up' : (pct < 0 ? 'down' : ''))
          });
        });
        this.setData({ finQuoteView: view, finBusy: false });
      })
      .catch((e) => {
        this.setData({ finBusy: false });
        wx.showToast({ title: e.message || '行情加载失败', icon: 'none' });
      });
  },

  onFinCodes(e) { this.setData({ finCodesInput: e.detail.value }); },
  addWatch() {
    const codes = (this.data.finCodesInput || '').trim();
    if (!codes) { wx.showToast({ title: '请输入证券代码', icon: 'none' }); return; }
    if (this.data.finBusy) return;
    this.setData({ finBusy: true });
    api.saveFinanceWatchlist(codes)
      .then(() => {
        this.setData({ finCodesInput: '', finBusy: false });
        wx.showToast({ title: '已加入自选', icon: 'success' });
        this.refreshWatchlist();
      })
      .catch((e) => {
        this.setData({ finBusy: false });
        wx.showToast({ title: e.message || '添加失败', icon: 'none' });
      });
  },
  removeWatch(e) {
    const code = e.currentTarget.dataset.code;
    api.deleteFinanceWatchlist(code)
      .then(() => this.refreshWatchlist())
      .catch((e2) => wx.showToast({ title: e2.message || '删除失败', icon: 'none' }));
  },
  applyWatch() {
    if (!this.data.finWatchlist.length) { wx.showToast({ title: '请先添加自选股', icon: 'none' }); return; }
    wx.showLoading({ title: '下发中' });
    api.applyFinanceToDevice(this.data.mac)
      .then((r) => {
        wx.hideLoading();
        wx.showToast({ title: r.message || '已写入设备', icon: 'success' });
      })
      .catch((e) => {
        wx.hideLoading();
        wx.showToast({ title: e.message || '下发失败', icon: 'none' });
      });
  },

  refreshReco() {
    this.setData({ finBusy: true });
    api.getFinanceRecommend()
      .then((r) => this.setData({ finReco: r.items || [], finRecoDisclaimer: r.disclaimer || '', finBusy: false }))
      .catch((e) => {
        this.setData({ finBusy: false });
        wx.showToast({ title: e.message || '推荐加载失败', icon: 'none' });
      });
  },
  recoToWatch(e) {
    const code = e.currentTarget.dataset.code;
    const codes = this.data.finWatchlist.map((x) => x.code).concat([code]).join(',');
    if (this.data.finBusy) return;
    this.setData({ finBusy: true });
    api.saveFinanceWatchlist(codes)
      .then(() => {
        this.setData({ finBusy: false });
        wx.showToast({ title: '已加入自选', icon: 'success' });
      })
      .catch((err) => {
        this.setData({ finBusy: false });
        wx.showToast({ title: err.message || '添加失败', icon: 'none' });
      });
  },

  refreshPortfolio() {
    this.setData({ finBusy: true });
    api.getFinancePortfolio()
      .then((r) => {
        this.setData({
          finPortfolio: r,
          finBusy: false,
          pfCash: r.cash ? String(r.cash) : ''
        });
      })
      .catch((e) => {
        this.setData({ finBusy: false });
        wx.showToast({ title: e.message || '持仓加载失败', icon: 'none' });
      });
  },
  onPfCash(e) { this.setData({ pfCash: e.detail.value }); },
  onPfCode(e) { this.setData({ pfCode: e.detail.value }); },
  onPfShares(e) { this.setData({ pfShares: e.detail.value }); },
  onPfCost(e) { this.setData({ pfCost: e.detail.value }); },
  savePortfolio() {
    if (this.data.finBusy) return;
    const p = this.data.finPortfolio;
    const positions = ((p && p.positions) || []).map((x) => ({ code: x.code, name: x.name, shares: x.shares, cost: x.cost }));
    const cash = parseFloat(this.data.pfCash) || 0;
    this.setData({ finBusy: true });
    api.saveFinancePortfolio(cash, positions)
      .then(() => {
        this.setData({ finBusy: false });
        wx.showToast({ title: '已保存', icon: 'success' });
        this.refreshPortfolio();
      })
      .catch((e) => {
        this.setData({ finBusy: false });
        wx.showToast({ title: e.message || '保存失败', icon: 'none' });
      });
  },
  addPosition() {
    const raw = (this.data.pfCode || '').trim();
    const shares = parseFloat(this.data.pfShares);
    const cost = parseFloat(this.data.pfCost);
    if (!raw) { wx.showToast({ title: '请输入股票名称或代码', icon: 'none' }); return; }
    if (!(shares > 0) || !(cost > 0)) { wx.showToast({ title: '请填写股数与成本价', icon: 'none' }); return; }
    if (this.data.finBusy) return;
    const doAdd = (code, name) => {
      const existing = ((this.data.finPortfolio && this.data.finPortfolio.positions) || [])
        .map((x) => ({ code: x.code, name: x.name, shares: x.shares, cost: x.cost }));
      if (existing.some((x) => x.code === code)) { this.setData({ finBusy: false }); wx.showToast({ title: '该股票已在持仓中', icon: 'none' }); return; }
      existing.push({ code: code, name: name || '', shares: shares, cost: cost });
      this.setData({ finBusy: true });
      api.saveFinancePortfolio(parseFloat(this.data.pfCash) || 0, existing)
        .then(() => {
          this.setData({ pfCode: '', pfShares: '', pfCost: '', finBusy: false });
          wx.showToast({ title: '已录入 ' + (name || code), icon: 'success' });
          this.refreshPortfolio();
        })
        .catch((e) => {
          this.setData({ finBusy: false });
          wx.showToast({ title: e.message || '保存失败', icon: 'none' });
        });
    };
    if (/^\d{6}$/.test(raw)) { doAdd(raw, ''); return; }
    // 名称/拼音 → 自动反查代码
    api.financeSearch(raw)
      .then((r) => {
        const items = (r && r.items) || [];
        if (!items.length) { wx.showToast({ title: '未找到「' + raw + '」，请输入 6 位代码', icon: 'none' }); return; }
        doAdd(items[0].code, items[0].name || '');
      })
      .catch(() => wx.showToast({ title: '查询失败，请输入 6 位代码', icon: 'none' }));
  },
  removePosition(e) {
    const code = e.currentTarget.dataset.code;
    const p = this.data.finPortfolio;
    const rest = ((p && p.positions) || []).filter((x) => x.code !== code)
      .map((x) => ({ code: x.code, name: x.name, shares: x.shares, cost: x.cost }));
    if (this.data.finBusy) return;
    this.setData({ finBusy: true });
    api.saveFinancePortfolio(parseFloat(this.data.pfCash) || 0, rest)
      .then(() => {
        this.setData({ finBusy: false });
        this.refreshPortfolio();
      })
      .catch((err) => {
        this.setData({ finBusy: false });
        wx.showToast({ title: err.message || '删除失败', icon: 'none' });
      });
  },
  applyPortfolio() {
    if (!this.data.finPortfolio || !this.data.finPortfolio.summary || !this.data.finPortfolio.summary.count) {
      wx.showToast({ title: '请先录入持仓', icon: 'none' });
      return;
    }
    wx.showLoading({ title: '下发中' });
    api.applyFinancePortfolioToDevice(this.data.mac)
      .then((r) => {
        wx.hideLoading();
        wx.showToast({ title: r.message || '已写入设备', icon: 'success' });
      })
      .catch((e) => {
        wx.hideLoading();
        wx.showToast({ title: e.message || '下发失败', icon: 'none' });
      });
  },

  // —— 课程表 ——
  onClassInput(e) { this.setData({ 'ct.class_name': e.detail.value }); },
  onSchoolInput(e) { this.setData({ 'ct.school': e.detail.value }); },
  onShowDays(e) {
    const v = parseInt(e.detail.value, 10);
    const n = v === 1 ? 7 : 5;
    this.setData({ 'ct.show_days': n });
    this.refreshDays(n);
  },
  onDay(e) { this.setData({ curDay: parseInt(e.currentTarget.dataset.idx, 10) }); },
  onCellInput(e) {
    const ds = e.currentTarget.dataset;
    const day = parseInt(ds.day, 10);
    const period = parseInt(ds.period, 10);
    const field = ds.field;
    const grid = this.data.ct.grid;
    const row = grid[day] || [];
    const cell = row[period] || { name: '', teacher: '', room: '', weeks: '' };
    cell[field] = e.detail.value;
    row[period] = cell;
    grid[day] = row;
    this.setData({ 'ct.grid': grid });
  },
  clearCell(e) {
    const ds = e.currentTarget.dataset;
    const grid = this.data.ct.grid;
    grid[parseInt(ds.day, 10)][parseInt(ds.period, 10)] = null;
    this.setData({ 'ct.grid': grid });
  },

  // —— 便签 ——
  onNoteInput(e) { this.setData({ noteText: e.detail.value }); },
  onNoteSender(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ noteSender: idx === 1 ? '老师' : '家长' });
  },
  onNotePersistent(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ notePersistent: idx === 1 });
  },
  addNote() {
    const text = (this.data.noteText || '').trim();
    if (!text) { wx.showToast({ title: '请输入便签内容', icon: 'none' }); return; }
    const note = {
      id: dual.uid(), text: text, color: 'yellow', sender: this.data.noteSender || '家长',
      sender_type: this.data.noteSender === '老师' ? 'teacher' : 'parent',
      persistent: this.data.notePersistent, date: dual.today()
    };
    this.setData({ notes: [note].concat(this.data.notes), noteText: '' });
  },
  removeNote(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ notes: this.data.notes.filter((n) => n.id !== id) });
  },

  // —— 今日作业 ——
  onHwInput(e) { this.setData({ hwText: e.detail.value }); },
  addHomework() {
    const text = (this.data.hwText || '').trim();
    if (!text) { wx.showToast({ title: '请输入作业内容', icon: 'none' }); return; }
    const item = { id: dual.uid(), text: text, done: false, sender: '家长', sender_type: 'parent', date: dual.today() };
    this.setData({ homework: [item].concat(this.data.homework), hwText: '' });
  },
  toggleHomework(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ homework: this.data.homework.map((h) => (h.id === id ? Object.assign({}, h, { done: !h.done }) : h)) });
  },
  removeHomework(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ homework: this.data.homework.filter((h) => h.id !== id) });
  },

  // —— 阅读任务 ——
  onReadTitle(e) { this.setData({ readTitle: e.detail.value }); },
  onReadProgress(e) { this.setData({ readProgress: e.detail.value }); },
  addReading() {
    const title = (this.data.readTitle || '').trim();
    if (!title) { wx.showToast({ title: '请输入书名', icon: 'none' }); return; }
    const item = { id: dual.uid(), title: title, progress: this.data.readProgress || '' };
    this.setData({ reading: [item].concat(this.data.reading), readTitle: '', readProgress: '' });
  },
  onReadProgressInput(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ reading: this.data.reading.map((r) => (r.id === id ? Object.assign({}, r, { progress: e.detail.value }) : r)) });
  },
  removeReading(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ reading: this.data.reading.filter((r) => r.id !== id) });
  },

  // —— 相册 ——
  addPhoto() {
    const self = this;
    wx.chooseMedia({
      count: 9, mediaType: ['image'], sourceType: ['album', 'camera'],
      success(res) {
        const files = (res.tempFiles || []).map((f) => f.tempFilePath);
        wx.showLoading({ title: '上传中' });
        Promise.all(files.map((p) => api.uploadImage(p)))
          .then((results) => {
            const photos = self.data.albumPhotos.concat(results.map((r) => ({ id: r.id || dual.uid(), url: r.url, caption: '' })));
            self.setData({ albumPhotos: photos });
            wx.hideLoading();
            wx.showToast({ title: '已添加 ' + results.length + ' 张', icon: 'success' });
          })
          .catch((e) => { wx.hideLoading(); wx.showToast({ title: e.message || '上传失败', icon: 'none' }); });
      }
    });
  },
  onCaption(e) {
    const id = e.currentTarget.dataset.id;
    const caption = e.detail.value;
    this.setData({ albumPhotos: this.data.albumPhotos.map((p) => (p.id === id ? Object.assign({}, p, { caption: caption }) : p)) });
  },
  removePhoto(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ albumPhotos: this.data.albumPhotos.filter((p) => p.id !== id) });
  },

  // —— 缴费台账 ——
  onBillName(e) { this.setData({ billName: e.detail.value }); },
  onBillAmount(e) { this.setData({ billAmount: e.detail.value }); },
  onBillCycle(e) { this.setData({ billCycleKey: e.currentTarget.dataset.cycle }); },
  onBillNextDue(e) { this.setData({ billNextDue: e.detail.value }); },
  onBillRemind(e) { this.setData({ billRemindDays: e.detail.value }); },
  syncBills() {
    const view = this.data.bills.map((b) => Object.assign({}, b, {
      cycleLabel: b.cycle === 'yearly' ? '年付' : '月付',
      paidLabel: b.paid ? '已缴' : '待缴'
    }));
    this.setData({ billView: view });
  },
  addBill() {
    const name = (this.data.billName || '').trim();
    const amt = parseFloat(this.data.billAmount);
    if (!name) { wx.showToast({ title: '请填写账单名称', icon: 'none' }); return; }
    if (!(amt >= 0)) { wx.showToast({ title: '请填写正确金额', icon: 'none' }); return; }
    if (!this.data.billNextDue) { wx.showToast({ title: '请选择下次缴费日期', icon: 'none' }); return; }
    const item = {
      id: dual.uid(), category: '宽带费', name: name, amount: amt,
      cycle: this.data.billCycleKey, nextDue: this.data.billNextDue,
      remindDays: Math.max(0, parseInt(this.data.billRemindDays) || 0), paid: false, history: []
    };
    this.setData({ bills: this.data.bills.concat([item]), billName: '', billAmount: '', billRemindDays: '3' });
    this.syncBills();
  },
  payBill(e) {
    const id = e.currentTarget.dataset.id;
    const b = this.data.bills.filter((x) => x.id === id)[0];
    if (!b) return;
    const history = (b.history || []).concat([{ date: todayKey(), amount: b.amount }]);
    const next = advanceDate(b.nextDue, b.cycle);
    this.setData({ bills: this.data.bills.map((x) => (x.id === id ? Object.assign({}, x, { history: history, nextDue: next, paid: true }) : x)) });
    this.syncBills();
  },
  unpayBill(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ bills: this.data.bills.map((x) => (x.id === id ? Object.assign({}, x, { paid: false }) : x)) });
    this.syncBills();
  },
  removeBill(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ bills: this.data.bills.filter((x) => x.id !== id) });
    this.syncBills();
  },

  // —— 快递 / 取件码 ——
  onExpCompany(e) { this.setData({ expCompanyIdx: parseInt(e.detail.value, 10) }); },
  onExpPickupCode(e) { this.setData({ expPickupCode: e.detail.value }); },
  onExpPickupPoint(e) { this.setData({ expPickupPoint: e.detail.value }); },
  onExpItem(e) { this.setData({ expItem: e.detail.value }); },
  onExpTrackingNo(e) { this.setData({ expTrackingNo: e.detail.value }); },
  syncExpress() {
    const view = this.data.express.map((x) => Object.assign({}, x, {
      statusIdx: Math.max(0, EXPRESS_STATUS.findIndex((s) => s.key === x.status)),
      statusLabel: (EXPRESS_STATUS.filter((s) => s.key === x.status)[0] || {}).label || x.status
    }));
    this.setData({ expressView: view });
  },
  addExpress() {
    const code = (this.data.expPickupCode || '').trim();
    const no = (this.data.expTrackingNo || '').trim();
    if (!no && !code) { wx.showToast({ title: '请填写取件码或运单号', icon: 'none' }); return; }
    if (no && this.data.express.some((x) => x.trackingNo === no)) { wx.showToast({ title: '该运单号已存在', icon: 'none' }); return; }
    const it = {
      id: dual.uid(), company: EXPRESS_COMPANIES[this.data.expCompanyIdx], trackingNo: no,
      pickupCode: code, pickupPoint: (this.data.expPickupPoint || '').trim(), item: (this.data.expItem || '').trim(),
      status: 'ordered', trail: [], updatedAt: todayKey()
    };
    this.setData({
      express: this.data.express.concat([it]),
      expPickupCode: '', expPickupPoint: '', expItem: '', expTrackingNo: ''
    });
    this.syncExpress();
  },
  setExpStatus(e) {
    const ds = e.currentTarget.dataset;
    const idx = parseInt(e.detail.value, 10);
    const key = EXPRESS_STATUS[idx].key;
    this.setData({ express: this.data.express.map((x) => (x.id === ds.id ? Object.assign({}, x, { status: key, updatedAt: todayKey() }) : x)) });
    this.syncExpress();
  },
  removeExpress(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ express: this.data.express.filter((x) => x.id !== id) });
    this.syncExpress();
  },

  // —— 家庭日程 / 纪念日（共用家庭事件编辑器）——
  onEvTitle(e) { this.setData({ evTitle: e.detail.value }); },
  onEvDate(e) { this.setData({ evDate: e.detail.value }); },
  onEvTime(e) { this.setData({ evTime: e.detail.value }); },
  onEvMember(e) { this.setData({ evMemberIdx: parseInt(e.detail.value, 10) }); },
  onEvCategory(e) { this.setData({ evCategoryIdx: parseInt(e.detail.value, 10) }); },
  onEvRepeat(e) { this.setData({ evRepeatIdx: parseInt(e.detail.value, 10) }); },
  onEvReminder(e) { this.setData({ evReminderIdx: parseInt(e.detail.value, 10) }); },
  onEvNote(e) { this.setData({ evNote: e.detail.value }); },
  addFamilyEvent() {
    const kind = this.data.tab === 'anniversaries' ? 'anniversaries' : 'schedule';
    const name = (this.data.evTitle || '').trim();
    if (!name) { wx.showToast({ title: '请输入标题', icon: 'none' }); return; }
    const ev = {
      id: dual.uid(), title: name, date: this.data.evDate, time: this.data.evTime,
      member: this.data.memberOptions[this.data.evMemberIdx] || '全家',
      category: FAMILY_CATEGORIES[this.data.eCategoryIdx] || '家庭',
      repeat: REPEAT_OPTIONS[this.data.evRepeatIdx] || '不重复',
      reminder: REMINDER_OPTIONS[this.data.evReminderIdx] || '提前30分钟',
      note: (this.data.evNote || '').trim()
    };
    if (kind === 'anniversaries') {
      this.setData({ familyAnniversaries: this.data.familyAnniversaries.concat([ev]), evTitle: '', evNote: '' });
    } else {
      this.setData({ familySchedule: this.data.familySchedule.concat([ev]), evTitle: '', evNote: '' });
    }
  },
  removeFamilyEvent(e) {
    const ds = e.currentTarget.dataset;
    if (ds.kind === 'anniversaries') {
      this.setData({ familyAnniversaries: this.data.familyAnniversaries.filter((x) => x.id !== ds.id) });
    } else {
      this.setData({ familySchedule: this.data.familySchedule.filter((x) => x.id !== ds.id) });
    }
  },

  // —— 采购清单 ——
  onShopInput(e) { this.setData({ shopText: e.detail.value }); },
  addShop() {
    const text = (this.data.shopText || '').trim();
    if (!text) { wx.showToast({ title: '请输入采购项目', icon: 'none' }); return; }
    this.setData({ familyShopping: this.data.familyShopping.concat([{ id: dual.uid(), text: text }]), shopText: '' });
  },
  toggleShop(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ familyShopping: this.data.familyShopping.map((x) => (x.id === id ? Object.assign({}, x, { done: !x.done }) : x)) });
  },
  removeShop(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ familyShopping: this.data.familyShopping.filter((x) => x.id !== id) });
  },

  // —— 保存：整体回写 dual_config ——
  onSave() {
    if (this.data.saving) return;
    this.setData({ saving: true });
    wx.showLoading({ title: '保存中' });
    const cfg = this.data.cfg || {};
    const content = Object.assign({}, cfg.content || {}, {
      courseTable: this.data.ct,
      notes: this.data.notes,
      homework: this.data.homework,
      reading: this.data.reading,
      album: { enabled: true, photos: this.data.albumPhotos },
      bills: this.data.bills,
      express: this.data.express,
      familySchedule: this.data.familySchedule,
      familyShopping: this.data.familyShopping,
      familyAnniversaries: this.data.familyAnniversaries
    });
    cfg.content = content;
    const payload = dual.buildPayload(cfg, this.data.mac);
    api.saveDualConfig(this.data.mac, payload)
      .then(() => {
        wx.hideLoading();
        this.setData({ saving: false, cfg: cfg });
        wx.showToast({ title: '已保存并下发', icon: 'success' });
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ saving: false });
        wx.showToast({ title: e.message || '保存失败', icon: 'none' });
      });
  }
});
