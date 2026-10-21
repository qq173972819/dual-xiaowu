// 推送通知便签（Page 4）
const api = require('../../utils/api.js');

Page({
  data: {
    role: 'parent',
    device: null,
    tab: 'text',
    content: '',
    source: 'parent',     // parent | teacher
    persistent: false,
    pushing: false
  },

  onLoad() {
    const g = getApp().globalData;
    this.setData({
      role: g.role,
      device: g.role === 'teacher' && g.teacher ? { name: g.teacher.studentName, sn: g.teacher.deviceSn } : g.currentDevice,
      // 老师推送固定为「老师通知」，且不可持续展示
      source: g.role === 'teacher' ? 'teacher' : 'parent',
      persistent: g.role === 'teacher' ? false : false
    });
    // 复制复用：自动填充内容
    const reuse = g.reuse;
    if (reuse && reuse.type === 'note') {
      this.setData({ content: reuse.content });
      g.reuse = null;
    }
  },

  onTab(e) { this.setData({ tab: e.currentTarget.dataset.tab }); },
  onInput(e) { this.setData({ content: e.detail.value }); },

  onCamera() { this.doOcr('camera'); },
  onAlbum() { this.doOcr('album'); },
  doOcr(source) {
    const that = this;
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: source === 'camera' ? ['camera'] : ['album'],
      success() {
        wx.showLoading({ title: 'OCR 识别中' });
        setTimeout(() => {
          wx.hideLoading();
          that.setData({ content: '明天数学小测，请带草稿本和计算器' });
          wx.showToast({ title: '识别完成，可校正后推送', icon: 'none' });
        }, 900);
      }
    });
  },

  onSource(e) {
    if (this.data.role === 'teacher') {
      wx.showToast({ title: '老师推送固定为老师通知', icon: 'none' });
      return;
    }
    this.setData({ source: e.currentTarget.dataset.src });
  },

  onExpire(e) {
    if (this.data.role === 'teacher' && e.currentTarget.dataset.p === '1') {
      wx.showToast({ title: '老师不可设置持续便签', icon: 'none' });
      return;
    }
    this.setData({ persistent: e.currentTarget.dataset.p === '1' });
  },

  onPush() {
    const content = this.data.content.trim();
    if (!content) {
      wx.showToast({ title: '请先填写通知内容', icon: 'none' });
      return;
    }
    const isTeacher = this.data.role === 'teacher';
    const payload = {
      content,
      senderType: isTeacher ? 'teacher' : (this.data.source === 'teacher' ? 'teacher' : 'parent'),
      senderName: isTeacher ? '老师' : (this.data.source === 'teacher' ? '老师' : '家长'),
      persistent: isTeacher ? false : this.data.persistent
    };
    this.setData({ pushing: true });
    wx.showLoading({ title: '推送中' });
    api.pushNote(payload)
      .then((r) => {
        wx.hideLoading();
        this.setData({ pushing: false });
        wx.showToast({ title: r.message || '已推送', icon: 'success' });
        setTimeout(() => wx.navigateBack(), 800);
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ pushing: false });
        wx.showToast({ title: e.message || '推送失败', icon: 'none' });
      });
  }
});
