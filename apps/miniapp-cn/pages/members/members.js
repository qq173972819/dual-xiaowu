// 共享成员（对齐 dual 后台「共享成员」）
// 所有者按用户名邀请协作成员，可移除；复用 dual 的设备绑定关系语义。
const api = require('../../utils/api.js');

Page({
  data: {
    mac: '',
    deviceName: '',
    loading: true,
    busy: false,
    members: [],
    username: ''
  },

  onLoad() {
    const dev = getApp().globalData.currentDevice;
    const mac = (dev && (dev.mac || dev.sn)) || '';
    if (!mac) {
      wx.showToast({ title: '请先选择设备', icon: 'none' });
      setTimeout(() => wx.navigateBack({ delta: 1 }), 800);
      return;
    }
    this.setData({ mac: mac, deviceName: (dev && dev.name) || '' });
    this.load();
  },

  load() {
    this.setData({ loading: true });
    api.getMembers(this.data.mac)
      .then((r) => {
        this.setData({ members: r.members || [], loading: false });
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
      });
  },

  onUsername(e) { this.setData({ username: e.detail.value }); },

  share() {
    const name = (this.data.username || '').trim();
    if (!name) { wx.showToast({ title: '请输入对方用户名', icon: 'none' }); return; }
    if (this.data.busy) return;
    this.setData({ busy: true });
    api.shareMember(this.data.mac, name)
      .then(() => {
        this.setData({ busy: false, username: '' });
        wx.showToast({ title: '已发送邀请', icon: 'success' });
        this.load();
      })
      .catch((e) => {
        this.setData({ busy: false });
        wx.showToast({ title: e.message || '邀请失败', icon: 'none' });
      });
  },

  // 老师邀请：仅学习场景使用，由家长在成员页发起，不作为独立菜单
  goTeacherAuth() {
    wx.navigateTo({ url: '/pages/teacher-auth/teacher-auth' });
  },

  remove(e) {
    const ds = e.currentTarget.dataset;
    if (ds.role === 'owner') { wx.showToast({ title: '所有者不可移除', icon: 'none' }); return; }
    const self = this;
    wx.showModal({
      title: '移除成员',
      content: '确认将「' + ds.name + '」移出该设备？',
      success(res) {
        if (!res.confirm) return;
        api.removeMember(self.data.mac, ds.uid)
          .then(() => { wx.showToast({ title: '已移除', icon: 'success' }); self.load(); })
          .catch((err) => { wx.showToast({ title: err.message || '移除失败', icon: 'none' }); });
      }
    });
  }
});
