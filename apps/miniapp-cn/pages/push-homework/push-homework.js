// 推送今日作业（Page 3）
const api = require('../../utils/api.js');

Page({
  data: {
    role: 'parent',
    device: null,
    tab: 'text',
    content: '',
    lines: [],
    isAppend: true,
    pushing: false
  },

  onLoad() {
    const g = getApp().globalData;
    this.setData({
      role: g.role,
      device: g.role === 'teacher' && g.teacher ? { name: g.teacher.studentName, sn: g.teacher.deviceSn } : g.currentDevice,
      // 老师锁定为追加
      isAppend: g.role === 'teacher' ? true : true
    });
    // 复制复用：自动填充内容
    const reuse = g.reuse;
    if (reuse && reuse.type === 'homework') {
      this.updateLines(reuse.content);
      g.reuse = null;
    }
  },

  onTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab });
  },

  onInput(e) {
    this.updateLines(e.detail.value);
  },

  updateLines(text) {
    const lines = (text || '').split('\n').map(s => s.trim()).filter(Boolean);
    this.setData({ content: text, lines });
  },

  // 拍照 / 相册：调用 OCR（此处模拟识别结果）
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
          const sample = '数学：P42 1-12 题\n英语：背诵 Unit8 单词，听写\n物理：完成周测剩余大题';
          that.updateLines(sample);
          wx.showToast({ title: '识别完成，可校正后推送', icon: 'none' });
        }, 900);
      }
    });
  },

  onMode(e) {
    if (this.data.role === 'teacher') {
      wx.showToast({ title: '老师无覆盖权限', icon: 'none' });
      return;
    }
    this.setData({ isAppend: e.currentTarget.dataset.append === '1' });
  },

  onPush() {
    const content = this.data.content.trim();
    if (!content) {
      wx.showToast({ title: '请先填写作业内容', icon: 'none' });
      return;
    }
    this.setData({ pushing: true });
    wx.showLoading({ title: '推送中' });
    api.pushHomework({ content, isAppend: this.data.isAppend })
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
