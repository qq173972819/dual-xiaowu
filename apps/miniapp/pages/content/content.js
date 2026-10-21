// 内容管理（对齐 dual 后台「内容管理」）
// 课程表 / 便签 / 今日作业 / 阅读任务 —— 读写同一份 dual_config，与 Web 后台互不覆盖。
const api = require('../../utils/api.js');
const dual = require('../../utils/dual.js');

const DAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

Page({
  data: {
    tab: 'course',          // course | notes | homework | reading
    tabs: [
      { key: 'course', label: '课程表' },
      { key: 'notes', label: '便签' },
      { key: 'homework', label: '今日作业' },
      { key: 'reading', label: '阅读任务' }
    ],
    deviceName: '',
    mac: '',
    loading: true,
    saving: false,

    // 课程表
    ct: null,
    dayLabels: DAY_LABELS,
    visibleDays: [],        // 依 show_days 过滤后的天索引
    curDay: 0,
    periodCount: 6,

    // 便签 / 作业 / 阅读
    notes: [],
    homework: [],
    reading: [],

    // 新建草稿
    noteText: '',
    noteSender: '家长',
    notePersistent: false,
    hwText: '',
    readTitle: '',
    readProgress: ''
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

  load() {
    this.setData({ loading: true });
    api.getDualConfig(this.data.mac)
      .then((r) => {
        const cfg = r.config || {};
        const content = dual.normalizeContent(cfg.content);
        const ct = content.courseTable;
        this.setData({
          cfg: cfg,
          ct: ct,
          notes: content.notes,
          homework: content.homework,
          reading: content.reading,
          periodCount: ct.periods.length,
          loading: false
        });
        this.refreshDays(ct.show_days);
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
    this.setData({ tab: e.currentTarget.dataset.key });
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

  cellLabel(day, period) {
    const c = this.data.ct.grid[day] && this.data.ct.grid[day][period];
    return c ? c.name : '';
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
      id: dual.uid(),
      text: text,
      color: 'yellow',
      sender: this.data.noteSender || '家长',
      sender_type: this.data.noteSender === '老师' ? 'teacher' : 'parent',
      persistent: this.data.notePersistent,
      date: dual.today()
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
    this.setData({
      homework: this.data.homework.map((h) => (h.id === id ? Object.assign({}, h, { done: !h.done }) : h))
    });
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
    this.setData({
      reading: this.data.reading.map((r) => (r.id === id ? Object.assign({}, r, { progress: e.detail.value }) : r))
    });
  },
  removeReading(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ reading: this.data.reading.filter((r) => r.id !== id) });
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
      reading: this.data.reading
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
