// 场景中心（对齐 dual 后台「场景中心」）
// 切换家庭 / 学习 / 理财 / 办公 / 自定义，写入同一份 dual_config。
const api = require('../../utils/api.js');
const dual = require('../../utils/dual.js');

Page({
  data: {
    scenes: dual.SCENES,
    sceneKey: 'family',
    loading: true,
    saving: false,
    mac: ''
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
        this.setData({ cfg: cfg, sceneKey: cfg.sceneKey || 'family', loading: false });
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
      });
  },

  onSelect(e) {
    this.setData({ sceneKey: e.currentTarget.dataset.key });
  },

  onSave() {
    if (this.data.saving) return;
    this.setData({ saving: true });
    wx.showLoading({ title: '保存中' });
    const cfg = this.data.cfg || {};
    cfg.sceneKey = this.data.sceneKey;
    const payload = dual.buildPayload(cfg, this.data.mac);
    api.saveDualConfig(this.data.mac, payload)
      .then(() => {
        wx.hideLoading();
        this.setData({ saving: false, cfg: cfg });
        wx.showToast({ title: '场景已保存', icon: 'success' });
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ saving: false });
        wx.showToast({ title: e.message || '保存失败', icon: 'none' });
      });
  },

  goScreens() {
    wx.navigateTo({ url: '/pages/screens/screens' });
  }
});
