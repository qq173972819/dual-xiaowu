// 推送历史归档（Page 5）
const api = require('../../utils/api.js');

function fmt(ts) {
  const d = new Date(ts);
  const p = (n) => (n < 10 ? '0' + n : '' + n);
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

Page({
  data: {
    filter: 'all',
    records: []
  },

  onShow() {
    this.load();
  },

  load() {
    wx.showLoading({ title: '加载中' });
    api.getHistory({ pushType: this.data.filter })
      .then((r) => {
        wx.hideLoading();
        const records = (r.records || []).map((x) => ({
          ...x,
          date: fmt(x.createTime),
          senderLabel: (x.senderType === 'teacher' ? '👩‍🏫老师' : '👨家长')
        }));
        this.setData({ records });
      })
      .catch((e) => {
        wx.hideLoading();
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
      });
  },

  onFilter(e) {
    this.setData({ filter: e.currentTarget.dataset.f });
    this.load();
  },

  onReuse(e) {
    const item = this.data.records[e.currentTarget.dataset.index];
    wx.setClipboardData({
      data: item.content,
      success: () => {
        // 写入复用缓存，跳转对应推送页自动填充
        getApp().globalData.reuse = { type: item.pushType, content: item.content };
        const url = item.pushType === 'homework'
          ? '/pages/push-homework/push-homework'
          : '/pages/push-note/push-note';
        wx.navigateTo({ url });
      }
    });
  },

  onDelete(e) {
    const idx = e.currentTarget.dataset.index;
    const item = this.data.records[idx];
    wx.showModal({
      title: '删除记录',
      content: '确定删除这条归档记录？',
      success: (res) => {
        if (!res.confirm) return;
        api.deleteHistory(item.id)
          .then(() => {
            const records = this.data.records.slice();
            records.splice(idx, 1);
            this.setData({ records });
            wx.showToast({ title: '已删除', icon: 'success' });
          })
          .catch((err) => wx.showToast({ title: err.message || '删除失败', icon: 'none' }));
      }
    });
  }
});
