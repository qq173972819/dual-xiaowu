// 老师授权管理（对齐 dual 后台「老师授权」）
// 生成 / 查看 / 冻结授权码；老师凭 6 位授权码免注册进入小程序推送作业与便签。
const api = require('../../utils/api.js');

const VALIDITY = [7, 30, 0];
const VALIDITY_LABELS = ['7 天', '30 天', '永久'];
const PERMS = ['only_note', 'note_and_homework'];
const PERM_LABELS = ['仅通知便签', '便签 + 追加作业'];

Page({
  data: {
    loading: true,
    busy: false,
    mac: '',
    items: [],
    authName: '老师',
    validityIdx: 1,
    validity: VALIDITY,
    validityLabels: VALIDITY_LABELS,
    permIdx: 0,
    perms: PERMS,
    permLabels: PERM_LABELS,
    generated: null
  },

  onLoad() {
    const dev = getApp().globalData.currentDevice;
    const mac = (dev && (dev.mac || dev.sn)) || '';
    if (!mac) {
      wx.showToast({ title: '请先选择设备', icon: 'none' });
      setTimeout(() => wx.navigateBack({ delta: 1 }), 800);
      return;
    }
    this.setData({ mac: mac });
    this.load();
  },

  load() {
    this.setData({ loading: true });
    api.getTeacherAuths(this.data.mac)
      .then((r) => {
        this.setData({ items: r.items || [], loading: false });
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
      });
  },

  onAuthName(e) { this.setData({ authName: e.detail.value }); },
  onValidity(e) { this.setData({ validityIdx: parseInt(e.detail.value, 10) }); },
  onPerm(e) { this.setData({ permIdx: parseInt(e.detail.value, 10) }); },

  onGenerate() {
    if (this.data.busy) return;
    this.setData({ busy: true });
    wx.showLoading({ title: '生成中' });
    api.generateTeacherAuth({
      device_mac: this.data.mac,
      auth_name: this.data.authName || '老师',
      validity_days: this.data.validity[this.data.validityIdx],
      permission: this.data.perms[this.data.permIdx]
    })
      .then((r) => {
        wx.hideLoading();
        this.setData({ busy: false, generated: r });
        this.load();
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ busy: false });
        wx.showToast({ title: e.message || '生成失败', icon: 'none' });
      });
  },

  onCopy() {
    const code = this.data.generated && this.data.generated.auth_code;
    if (!code) return;
    wx.setClipboardData({ data: code });
  },

  onFreeze(e) {
    const ds = e.currentTarget.dataset;
    const frozen = ds.frozen !== 'false';
    const title = frozen ? '确认冻结该授权码？' : '确认恢复该授权码？';
    wx.showModal({
      title: title,
      content: '授权码：' + ds.code,
      success: (res) => {
        if (!res.confirm) return;
        wx.showLoading({ title: '处理中' });
        api.freezeTeacherAuth(ds.code, frozen)
          .then(() => {
            wx.hideLoading();
            wx.showToast({ title: frozen ? '已冻结' : '已恢复', icon: 'success' });
            this.load();
          })
          .catch((err) => {
            wx.hideLoading();
            wx.showToast({ title: err.message || '操作失败', icon: 'none' });
          });
      }
    });
  }
});
