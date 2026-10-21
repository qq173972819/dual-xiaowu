// 系统配置（对齐 dual 后台「系统配置」）
// 把原本要写在后端 .env 并重启才能生效的密钥，改为在后台直接填写；DB 值优先于 .env。
const api = require('../../utils/api.js');

Page({
  data: {
    loading: true,
    saving: false,
    items: [],              // 后端返回的可配置项（已脱敏）
    drafts: {},             // key -> 用户输入；留空表示不修改
    reveal: {},             // key -> 是否明文显示
    expressConfigured: false
  },

  onLoad() {
    this.load();
  },

  load() {
    this.setData({ loading: true });
    api.getServiceConfig()
      .then((r) => {
        this.setData({
          items: r.items || [],
          expressConfigured: !!r.express_configured,
          drafts: {},
          loading: false
        });
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
      });
  },

  onInput(e) {
    const key = e.currentTarget.dataset.key;
    const drafts = Object.assign({}, this.data.drafts);
    drafts[key] = e.detail.value;
    this.setData({ drafts: drafts });
  },

  onToggleReveal(e) {
    const key = e.currentTarget.dataset.key;
    const reveal = Object.assign({}, this.data.reveal);
    reveal[key] = !reveal[key];
    this.setData({ reveal: reveal });
  },

  onSave() {
    if (this.data.saving) return;
    const drafts = this.data.drafts || {};
    const changed = this.data.items.filter((it) => (drafts[it.key] || '') !== '');
    if (changed.length === 0) {
      wx.showToast({ title: '没有要保存的变更', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    wx.showLoading({ title: '保存中' });

    // 逐项提交（后端按 key 单项写入）
    let chain = Promise.resolve();
    changed.forEach((it) => {
      chain = chain.then(() => api.saveServiceConfig(it.key, drafts[it.key]));
    });

    chain
      .then(() => {
        wx.hideLoading();
        this.setData({ saving: false });
        wx.showToast({ title: '系统配置已保存', icon: 'success' });
        this.load();
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ saving: false });
        wx.showToast({ title: e.message || '保存失败', icon: 'none' });
      });
  }
});
