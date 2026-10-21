// 老师专属推送面板（Page 7）
const api = require('../../utils/api.js');

Page({
  data: {
    teacher: null,
    syncing: false
  },

  onLoad() {
    this.setData({ teacher: getApp().globalData.teacher });
  },

  goHomework() {
    wx.navigateTo({ url: '/pages/push-homework/push-homework' });
  },
  goNote() {
    wx.navigateTo({ url: '/pages/push-note/push-note' });
  },

  onSync() {
    if (this.data.syncing) return;
    const t = this.data.teacher;
    if (!t) return;
    this.setData({ syncing: true });
    wx.showLoading({ title: '同步中' });
    api.syncScreen(t.deviceSn)
      .then((r) => {
        wx.hideLoading();
        this.setData({ syncing: false });
        wx.showToast({ title: r.cooldown ? r.message : (r.message || '已同步'), icon: r.cooldown ? 'none' : 'success' });
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ syncing: false });
        wx.showToast({ title: e.message || '同步失败', icon: 'none' });
      });
  },

  onExit() {
    wx.showModal({
      title: '退出授权登录',
      content: '退出后需重新输入授权码，确定退出？',
      success: (res) => {
        if (res.confirm) {
          getApp().logout();
          wx.reLaunch({ url: '/pages/splash/splash' });
        }
      }
    });
  }
});
