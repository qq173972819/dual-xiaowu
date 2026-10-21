// 家长首页（Page 2）
const api = require('../../utils/api.js');

Page({
  data: {
    device: null,
    syncing: false
  },

  onShow() {
    this.setData({ device: getApp().globalData.currentDevice });
  },

  goHomework() {
    wx.navigateTo({ url: '/pages/push-homework/push-homework' });
  },
  goNote() {
    wx.navigateTo({ url: '/pages/push-note/push-note' });
  },
  goHistory() {
    wx.navigateTo({ url: '/pages/history/history' });
  },

  // —— dual 功能对齐：内容管理 / 场景 / 双屏 / 个人信息 / 系统配置 / 老师授权 ——
  goContent() { wx.navigateTo({ url: '/pages/content/content' }); },
  goScenes() { wx.navigateTo({ url: '/pages/scenes/scenes' }); },
  goScreens() { wx.navigateTo({ url: '/pages/screens/screens' }); },
  goProfile() { wx.navigateTo({ url: '/pages/profile/profile' }); },
  goSettings() { wx.navigateTo({ url: '/pages/settings/settings' }); },
  goTeacherAuth() { wx.navigateTo({ url: '/pages/teacher-auth/teacher-auth' }); },
  goMembers() { wx.navigateTo({ url: '/pages/members/members' }); },

  // 立即同步 A 屏（5 分钟限流）
  onSync() {
    if (this.data.syncing) return;
    const dev = getApp().globalData.currentDevice;
    if (!dev) {
      wx.showToast({ title: '请先选择设备', icon: 'none' });
      return;
    }
    this.setData({ syncing: true });
    wx.showLoading({ title: '同步中' });
    api.syncScreen(dev.sn)
      .then((r) => {
        wx.hideLoading();
        this.setData({ syncing: false });
        if (r.cooldown) {
          wx.showToast({ title: r.message, icon: 'none' });
        } else {
          wx.showToast({ title: r.message, icon: 'success' });
        }
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ syncing: false });
        wx.showToast({ title: e.message || '同步失败', icon: 'none' });
      });
  },

  onSwitchDevice() {
    wx.redirectTo({ url: '/pages/device/device' });
  },

  onLogout() {
    getApp().logout();
    wx.reLaunch({ url: '/pages/splash/splash' });
  }
});
