// 启动页（Page 0）
const api = require('../../utils/api.js');

Page({
  data: {},

  // 微信一键登录：微信授权 + 换取会话，再进入设备选择
  onLogin() {
    wx.showLoading({ title: '登录中', mask: true });
    wx.login({
      success: (res) => {
        api.wechatLogin(res.code)
          .then((r) => {
            const g = getApp().globalData;
            g.role = 'parent';
            g.token = r.token;
            wx.setStorageSync('token', r.token);
            wx.setStorageSync('role', 'parent');
            wx.hideLoading();
            wx.navigateTo({ url: '/pages/device/device' });
          })
          .catch((e) => {
            wx.hideLoading();
            wx.showToast({ title: e.message || '登录失败', icon: 'none' });
          });
      },
      fail: () => {
        wx.hideLoading();
        wx.showToast({ title: '微信登录失败', icon: 'none' });
      }
    });
  },

  // 老师通道：凭家长提供的授权码进入，直接到授权码校验页
  onTeacher() {
    const g = getApp().globalData;
    g.role = 'teacher';
    wx.setStorageSync('role', 'teacher');
    wx.navigateTo({ url: '/pages/auth/auth' });
  }
});
