// 连接器绑定页：第三方账号一键绑定
// 平台级连接器 → 「无需绑定，已开通」；用户级 → 「去授权」/「已绑定」。
// 授权引导：点击「去授权」后弹出引导层，用户复制授权链接到浏览器完成授权，
// 返回本页下拉刷新即可看到「已绑定」。
const api = require('../../utils/api.js');

const TYPE_LABELS = { platform: '平台级', user: '用户级' };

Page({
  data: {
    loading: true,
    items: [],
    // 当前引导中的连接器
    guideFor: null,
    authorizeUrl: '',
    authorizing: false
  },

  onLoad() {
    this.load();
  },

  onPullDownRefresh() {
    this.load(() => wx.stopPullDownRefresh());
  },

  load(done) {
    this.setData({ loading: true });
    api.listConnectors()
      .then((items) => {
        this.setData({
          loading: false,
          items: (items || []).map(function (it) {
            return Object.assign({}, it, { typeLabel: TYPE_LABELS[it.type] || it.type });
          })
        });
        if (done) done();
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
        if (done) done();
      });
  },

  // 点「去授权」：请求授权链接并展开引导层
  onTapAuthorize(e) {
    const id = e.currentTarget.dataset.id;
    const item = this.data.items.find(function (it) { return it.id === id; });
    if (!item || this.data.authorizing) return;
    this.setData({ authorizing: true });
    api.authorizeConnector(id)
      .then((r) => {
        this.setData({ authorizing: false, guideFor: id, authorizeUrl: r.authorize_url || '' });
      })
      .catch((err) => {
        this.setData({ authorizing: false });
        wx.showToast({ title: err.message || '发起授权失败', icon: 'none' });
      });
  },

  // 复制授权链接，引导用户去浏览器打开
  onCopyLink() {
    if (!this.data.authorizeUrl) return;
    wx.setClipboardData({
      data: this.data.authorizeUrl,
      success: () => wx.showToast({ title: '链接已复制，请在浏览器打开完成授权', icon: 'none', duration: 2500 })
    });
  },

  onCloseGuide() {
    this.setData({ guideFor: null, authorizeUrl: '' });
    this.load();
  },

  onUnbind(e) {
    const id = e.currentTarget.dataset.id;
    const item = this.data.items.find(function (it) { return it.id === id; });
    if (!item) return;
    wx.showModal({
      title: '解除绑定',
      content: '解除后平台将立即删除已保存的「' + item.name + '」授权凭据，确定继续？',
      confirmText: '解除',
      confirmColor: '#a32d2d',
      success: (res) => {
        if (!res.confirm) return;
        api.unbindConnector(id)
          .then(() => {
            wx.showToast({ title: '已解除绑定', icon: 'success' });
            this.load();
          })
          .catch((err) => wx.showToast({ title: err.message || '解绑失败', icon: 'none' }));
      }
    });
  }
});
