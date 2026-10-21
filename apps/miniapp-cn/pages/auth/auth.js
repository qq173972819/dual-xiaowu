// 老师授权码校验（Page 6）
const api = require('../../utils/api.js');

Page({
  data: {
    code: '',
    codeArr: ['', '', '', '', '', ''],
    boxes: [0, 1, 2, 3, 4, 5],
    verifying: false
  },

  onInput(e) {
    const code = e.detail.value;
    const codeArr = [];
    for (let i = 0; i < 6; i++) codeArr.push(code[i] || '');
    this.setData({ code, codeArr });
  },

  onVerify() {
    if (this.data.code.length < 6) {
      wx.showToast({ title: '请输入完整 6 位授权码', icon: 'none' });
      return;
    }
    this.setData({ verifying: true });
    wx.showLoading({ title: '校验中' });
    api.verifyAuth(this.data.code)
      .then((r) => {
        wx.hideLoading();
        this.setData({ verifying: false });
        const teacher = {
          authCode: r.authCode,
          studentName: r.studentName,
          deviceSn: r.deviceSn,
          permission: r.permission
        };
        const g = getApp().globalData;
        g.teacher = teacher;
        g.role = 'teacher';
        wx.setStorageSync('teacher', teacher);
        wx.setStorageSync('role', 'teacher');
        wx.showToast({ title: '校验成功', icon: 'success' });
        setTimeout(() => {
          wx.reLaunch({ url: '/pages/teacher/teacher' });
        }, 600);
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ verifying: false, code: '', codeArr: ['', '', '', '', '', ''] });
        wx.showToast({ title: e.message || '授权码无效', icon: 'none' });
      });
  },

  onBack() {
    getApp().logout();
    wx.reLaunch({ url: '/pages/splash/splash' });
  }
});
