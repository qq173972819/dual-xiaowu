// 设备选择页（Page 1）
const api = require('../../utils/api.js');

Page({
  data: {
    devices: []
  },

  onShow() {
    this.loadDevices();
  },

  loadDevices() {
    wx.showLoading({ title: '加载中' });
    api.getDevices()
      .then((r) => {
        wx.hideLoading();
        const devices = r.devices || [];
        getApp().globalData.devices = devices;
        wx.setStorageSync('devices', devices);
        this.setData({ devices });
      })
      .catch((e) => {
        wx.hideLoading();
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
      });
  },

  onSelect(e) {
    const idx = e.currentTarget.dataset.index;
    const dev = this.data.devices[idx];
    getApp().globalData.currentDevice = dev;
    wx.setStorageSync('currentDevice', dev);
    wx.navigateTo({ url: '/pages/home/home' });
  },

  onBack() {
    wx.navigateBack({ delta: 1 });
  }
});
