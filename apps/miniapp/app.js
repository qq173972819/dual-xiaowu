// app.js —— 森穹小悟墨看板小程序 全局状态
// 角色：parent（家长主账号） / teacher（授权老师，免注册）
App({
  globalData: {
    // 当前登录角色
    role: null,
    // 后端会话 token（家长微信登录获得；老师不持有，用授权码）
    token: null,
    // 家长名下设备列表
    devices: [],
    // 当前选中的设备（家长通道）
    currentDevice: null,
    // 老师授权信息（teacher 通道）：{ authCode, studentName, deviceSn, permission }
    teacher: null,
    // 是否启用本地模拟（无后端时立即可跑；接正式后端置 false）
    useMock: true,
    // 正式后端域名（须在微信公众平台「服务器域名」中配置 request 合法域名）
    // 本地联调：后端 uvicorn 默认 8100
    baseURL: 'http://127.0.0.1:8100'
  },

  onLaunch() {
    const g = this.globalData;
    g.token = wx.getStorageSync('token') || null;
    g.role = wx.getStorageSync('role') || null;
    g.currentDevice = wx.getStorageSync('currentDevice') || null;
    g.teacher = wx.getStorageSync('teacher') || null;
    g.devices = wx.getStorageSync('devices') || [];
  },

  // 退出登录 / 切换通道时清理
  logout() {
    const g = this.globalData;
    g.role = null;
    g.token = null;
    g.currentDevice = null;
    g.teacher = null;
    g.devices = [];
    wx.removeStorageSync('token');
    wx.removeStorageSync('role');
    wx.removeStorageSync('currentDevice');
    wx.removeStorageSync('teacher');
    wx.removeStorageSync('devices');
  }
});
