// 双屏配置（对齐 dual 后台「双屏配置」）
// A 屏（FPC1 黑白快刷）/ B 屏（FPC2 三色四色）各自配置内容名、数据来源、刷新频率与模块。
const api = require('../../utils/api.js');
const dual = require('../../utils/dual.js');

const SOURCES = ['后台手动配置', '后台家庭待办', '后台作业/便签', '课程表/倒计时', '相册（后台上传）', '飞书日历', 'TF 卡', '行情数据源', '后台相册/TF 卡', '用户配置'];
const REFRESHES = ['每 5 分钟', '每 10 分钟', '每 15 分钟', '每 30 分钟', '每 12 小时', '每 24 小时', '自定义'];
const A_MODES = [
  { key: 'notes', label: '便签' },
  { key: 'homework', label: '今日作业' },
  { key: 'reading', label: '阅读任务' },
  { key: 'album', label: '相册' }
];

Page({
  data: {
    sources: SOURCES,
    refreshes: REFRESHES,
    aModes: A_MODES,
    aModeLabels: A_MODES.map((m) => m.label),
    loading: true,
    saving: false,
    mac: '',
    sceneKey: 'family',
    sceneName: '家庭',
    side: 'a',              // 当前编辑 a / b
    a: null,
    b: null,
    aModeIdx: 0,
    sideSourceIdx: 0,
    sideRefreshIdx: 0,
    screenSizeIdx: 0,
    colorsIdx: 2,
    newModule: ''
  },

  onLoad() {
    const dev = getApp().globalData.currentDevice;
    const mac = (dev && (dev.mac || dev.sn)) || '';
    if (!mac) {
      wx.showToast({ title: '请先选择设备', icon: 'none' });
      setTimeout(() => wx.navigateBack({ delta: 1 }), 800);
      return;
    }
    this.setData({ mac: mac });
    this.load();
  },

  load() {
    this.setData({ loading: true });
    api.getDualConfig(this.data.mac)
      .then((r) => {
        const cfg = r.config || {};
        const sceneKey = cfg.sceneKey || 'family';
        const screens = (cfg.config && cfg.config[sceneKey]) || dual.DEFAULT_SCREENS[sceneKey];
        const screen = cfg.screen || { size: '4.2', colors: 4 };
        const scene = dual.SCENES.filter((s) => s.key === sceneKey)[0] || dual.SCENES[0];
        this.setData({
          cfg: cfg,
          sceneKey: sceneKey,
          sceneName: scene.name,
          a: screens.a,
          b: screens.b,
          screen: screen,
          screenSizeIdx: screen.size === '5.83' ? 1 : 0,
          colorsIdx: screen.colors === 2 ? 0 : (screen.colors === 3 ? 1 : 2),
          aModeIdx: Math.max(0, A_MODES.map((m) => m.key).indexOf(screens.a.mode || 'notes')),
          sideSourceIdx: Math.max(0, SOURCES.indexOf(screens.a.source)),
          sideRefreshIdx: Math.max(0, REFRESHES.indexOf(screens.a.refresh)),
          loading: false
        });
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
      });
  },

  onSide(e) {
    const s = e.currentTarget.dataset.side;
    const cfg = this.data[s] || {};
    this.setData({
      side: s,
      sideSourceIdx: Math.max(0, SOURCES.indexOf(cfg.source)),
      sideRefreshIdx: Math.max(0, REFRESHES.indexOf(cfg.refresh))
    });
  },

  // A/B 屏字段（同一组回调，按 side 区分）
  onTitle(e) {
    const s = this.data.side;
    this.setData({ [s + '.title']: e.detail.value });
  },
  onSource(e) {
    const s = this.data.side;
    const idx = parseInt(e.detail.value, 10);
    this.setData({ [s + '.source']: SOURCES[idx], sideSourceIdx: idx });
  },
  onRefresh(e) {
    const s = this.data.side;
    const idx = parseInt(e.detail.value, 10);
    this.setData({ [s + '.refresh']: REFRESHES[idx], sideRefreshIdx: idx });
  },
  onAMode(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ 'a.mode': A_MODES[idx].key, aModeIdx: idx });
  },

  // 模块增删
  onModuleInput(e) {
    const ds = e.currentTarget.dataset;
    const s = this.data.side;
    const mods = this.data[s].modules.slice();
    mods[parseInt(ds.idx, 10)] = e.detail.value;
    this.setData({ [s + '.modules']: mods });
  },
  removeModule(e) {
    const ds = e.currentTarget.dataset;
    const s = this.data.side;
    const mods = this.data[s].modules.filter((_, i) => i !== parseInt(ds.idx, 10));
    this.setData({ [s + '.modules']: mods });
  },
  onNewModule(e) { this.setData({ newModule: e.detail.value }); },
  addModule() {
    const v = (this.data.newModule || '').trim();
    if (!v) { wx.showToast({ title: '请输入模块名', icon: 'none' }); return; }
    const s = this.data.side;
    this.setData({ [s + '.modules']: this.data[s].modules.concat([v]), newModule: '' });
  },

  // 屏幕规格
  onSize(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ screenSizeIdx: idx, 'screen.size': idx === 1 ? '5.83' : '4.2' });
  },
  onColors(e) {
    const idx = parseInt(e.detail.value, 10);
    this.setData({ colorsIdx: idx, 'screen.colors': idx === 0 ? 2 : (idx === 1 ? 3 : 4) });
  },

  onSave() {
    if (this.data.saving) return;
    this.setData({ saving: true });
    wx.showLoading({ title: '保存中' });
    const cfg = this.data.cfg || {};
    if (!cfg.config) cfg.config = {};
    cfg.config[this.data.sceneKey] = { a: this.data.a, b: this.data.b };
    cfg.screen = this.data.screen;
    cfg.sceneKey = this.data.sceneKey;
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
