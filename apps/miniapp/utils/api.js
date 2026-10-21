// utils/api.js —— 小程序 <-> 后端 接口层
// 接口对齐 PRD《小程序后台.md》第六节「接口摘要」，并复用 dual 后台的设备模型
// 与 dual_config 存储 —— 小程序与 Web 后台读写同一份数据，互不覆盖。
// 无后端时（globalData.useMock=true）走本地模拟，保证微信开发者工具内可直接演示。

const mock = require('./mock.js');

// 本地模拟时的 dual_config 落盘 key
const MOCK_CFG_KEY = 'xiaowu_dual_config_mock';

function app() {
  return getApp();
}

// 当前生效设备：老师通道用授权绑定的设备，家长通道用选中的设备
function effectiveDevice() {
  const g = app().globalData;
  if (g.role === 'teacher' && g.teacher) {
    return { sn: g.teacher.deviceSn, name: g.teacher.studentName };
  }
  return g.currentDevice;
}

// 当前生效设备的 MAC（新页面统一用 mac 调后端）
function effectiveMac() {
  const d = effectiveDevice();
  if (!d) return '';
  return d.mac || d.sn || '';
}

// 统一请求封装
function request(path, method, data) {
  const g = app().globalData;
  return new Promise((resolve, reject) => {
    if (g.useMock) {
      // 延迟 200ms 模拟网络，提升真实感
      setTimeout(() => {
        try {
          resolve(handleMock(path, method, data));
        } catch (e) {
          reject({ message: e.message || '模拟处理失败' });
        }
      }, 200);
      return;
    }
    wx.request({
      url: g.baseURL + path,
      method,
      data,
      header: {
        'content-type': 'application/json',
        'Authorization': g.token ? 'Bearer ' + g.token : ''
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          reject((res.data && res.data.message) ? res.data : { message: '请求失败 (' + res.statusCode + ')' });
        }
      },
      fail(err) {
        reject({ message: err.errMsg || '网络异常' });
      }
    });
  });
}

// —— 模拟分发 ——
function handleMock(path, method, data) {
  // 家长微信一键登录（wx.login code 换 token）
  if (path === '/api/auth/wechat' && method === 'POST') {
    return { token: 'mock_token_' + Date.now(), openid: 'mock_openid_' + Date.now() };
  }
  // 设备列表（家长）
  if (path === '/api/device/list' && method === 'GET') {
    return { devices: mock.getDevices() };
  }
  // 双屏配置：读取
  if (path.indexOf('/dual-config') >= 0 && method === 'GET') {
    return { config: wx.getStorageSync(MOCK_CFG_KEY) || null };
  }
  // 双屏配置：保存
  if (path.indexOf('/dual-config') >= 0 && method === 'POST') {
    wx.setStorageSync(MOCK_CFG_KEY, data);
    return { ok: true, message: 'Dual config saved', updated_at: new Date().toISOString() };
  }
  // 个人信息
  if (path === '/api/user/profile' && method === 'GET') {
    return { llm_config: wx.getStorageSync('mock_llm') || { llm_access_mode: 'platform', provider: 'deepseek' } };
  }
  if (path === '/api/user/profile/llm' && method === 'PUT') {
    const prev = wx.getStorageSync('mock_llm') || {};
    const next = Object.assign({}, prev, data);
    if (data.api_key) next.llm_api_key_set = true;
    if (data.image_api_key) next.image_api_key_set = true;
    wx.setStorageSync('mock_llm', next);
    return { ok: true };
  }
  // 孩子信息
  if (path === '/api/user/child' && method === 'GET') {
    return { child: wx.getStorageSync('mock_child') || {} };
  }
  if (path === '/api/user/child' && method === 'PUT') {
    wx.setStorageSync('mock_child', data);
    return { ok: true };
  }
  // 系统配置（快递100 密钥等）
  if (path === '/api/settings/service' && method === 'GET') {
    const saved = wx.getStorageSync('mock_service_cfg') || {};
    return {
      items: [
        { key: 'KUAIDI100_KEY', label: '快递100 授权 KEY', secret: true, hint: '申请地址：kuaidi100.com', placeholder: '在 kuaidi100.com 申请后获得', masked: saved.KUAIDI100_KEY ? saved.KUAIDI100_KEY.slice(0, 2) + '****' + saved.KUAIDI100_KEY.slice(-4) : '', configured: !!saved.KUAIDI100_KEY, source: saved.KUAIDI100_KEY ? 'db' : 'none' },
        { key: 'KUAIDI100_CUSTOMER', label: '快递100 客户号 CUSTOMER', secret: false, hint: '与 KEY 配套的客户号', placeholder: '快递100 客户号', masked: saved.KUAIDI100_CUSTOMER || '', configured: !!saved.KUAIDI100_CUSTOMER, source: saved.KUAIDI100_CUSTOMER ? 'db' : 'none' }
      ],
      express_configured: !!(saved.KUAIDI100_KEY && saved.KUAIDI100_CUSTOMER)
    };
  }
  if (path === '/api/settings/service' && method === 'POST') {
    const saved = wx.getStorageSync('mock_service_cfg') || {};
    saved[data.key] = data.value;
    wx.setStorageSync('mock_service_cfg', saved);
    return { ok: true, key: data.key, configured: !!data.value };
  }
  // 老师授权：列表 / 生成 / 冻结
  if (path.indexOf('/api/teacher-auth/list') === 0 && method === 'GET') {
    return { items: wx.getStorageSync('mock_teacher_auth') || [] };
  }
  if (path === '/api/teacher-auth/generate' && method === 'POST') {
    const list = wx.getStorageSync('mock_teacher_auth') || [];
    const rec = {
      id: Date.now(),
      auth_code: String(Math.floor(100000 + Math.random() * 900000)),
      auth_name: data.auth_name || '老师',
      permission: data.permission || 'only_note',
      expire_time: null,
      is_frozen: false
    };
    list.unshift(rec);
    wx.setStorageSync('mock_teacher_auth', list);
    return { ok: true, auth_code: rec.auth_code, auth_name: rec.auth_name, permission: rec.permission };
  }
  if (path === '/api/teacher-auth/freeze' && method === 'POST') {
    const list = wx.getStorageSync('mock_teacher_auth') || [];
    const hit = list.filter((r) => r.auth_code === data.auth_code)[0];
    if (hit) hit.is_frozen = data.frozen !== false;
    wx.setStorageSync('mock_teacher_auth', list);
    return { ok: true };
  }
  // 推送今日作业
  if (path === '/api/push/homework' && method === 'POST') {
    const dev = effectiveDevice();
    const rec = mock.addHistory({
      id: 'h' + Date.now(),
      deviceSn: dev.sn,
      pushType: 'homework',
      senderType: app().globalData.role === 'teacher' ? 'teacher' : 'parent',
      senderName: app().globalData.role === 'teacher' ? '老师' : '家长',
      content: data.content,
      isAppend: data.isAppend !== false,
      expireTime: endOfDay(),
      createTime: Date.now()
    });
    return { ok: true, record: rec, message: '作业已推送至看板' };
  }
  // 推送通知便签
  if (path === '/api/push/note' && method === 'POST') {
    const dev = effectiveDevice();
    const rec = mock.addHistory({
      id: 'n' + Date.now(),
      deviceSn: dev.sn,
      pushType: 'note',
      senderType: data.senderType || (app().globalData.role === 'teacher' ? 'teacher' : 'parent'),
      senderName: data.senderName || (app().globalData.role === 'teacher' ? '老师' : '家长'),
      content: data.content,
      persistent: !!data.persistent,
      expireTime: data.persistent ? null : endOfDay(),
      createTime: Date.now()
    });
    return { ok: true, record: rec, message: '便签已推送至看板' };
  }
  // 推送历史
  if (path === '/api/push/history' && method === 'GET') {
    const dev = effectiveDevice();
    let list = mock.getHistory().filter(r => r.deviceSn === dev.sn);
    if (data && data.pushType && data.pushType !== 'all') {
      list = list.filter(r => r.pushType === data.pushType);
    }
    return { records: list };
  }
  // 老师授权码校验
  if (path === '/api/auth/verify' && method === 'POST') {
    const r = mock.verifyAuth(data.authCode);
    if (!r.ok) throw new Error(r.message);
    return r;
  }
  // 立即同步 A 屏
  if (path.indexOf('/sync') >= 0 && method === 'POST') {
    const sn = data.sn || (effectiveDevice() && effectiveDevice().sn);
    return mock.syncScreen(sn);
  }
  // 快照（设备侧，预览用）
  if (path.indexOf('/api/device/snapshot') === 0 && method === 'GET') {
    return { ok: true, url: '' };
  }
  throw new Error('未实现的模拟接口: ' + method + ' ' + path);
}

function endOfDay() {
  const d = new Date();
  d.setHours(23, 59, 59, 0);
  return d.getTime();
}

// 后端 /api/user/devices 返回 mac/nickname，小程序历史页面用 sn/name，这里做一次归一
function normalizeDevices(list) {
  return (list || []).map(function (d) {
    return {
      mac: d.mac || d.sn || '',
      sn: d.sn || d.mac || '',
      name: d.name || d.nickname || '墨看板',
      nickname: d.nickname || d.name || '',
      stage: d.stage || '',
      role: d.role || '',
      is_online: !!d.is_online,
      last_seen: d.last_seen || ''
    };
  });
}

// —— 业务 API ——
const api = {
  effectiveDevice: effectiveDevice,
  effectiveMac: effectiveMac,

  // 家长微信登录：拿到 code 后换 token
  wechatLogin(code) {
    return request('/api/auth/wechat', 'POST', { code });
  },

  // 设备列表（家长）
  getDevices() {
    return request('/api/device/list', 'GET').then(function (r) {
      const devices = normalizeDevices(r.devices);
      return { devices: devices };
    });
  },

  // —— 双屏配置（与 dual 控制台共用同一份 dual_config）——
  getDualConfig(mac) {
    return request('/api/device/' + encodeURIComponent(mac) + '/dual-config', 'GET');
  },
  saveDualConfig(mac, payload) {
    return request('/api/device/' + encodeURIComponent(mac) + '/dual-config', 'POST', payload);
  },

  // —— 个人信息 ——
  getProfile() {
    return request('/api/user/profile', 'GET');
  },
  saveLlm(payload) {
    return request('/api/user/profile/llm', 'PUT', payload);
  },
  getChild() {
    return request('/api/user/child', 'GET');
  },
  saveChild(payload) {
    return request('/api/user/child', 'PUT', payload);
  },

  // —— 系统配置（快递100 密钥等）——
  getServiceConfig() {
    return request('/api/settings/service', 'GET');
  },
  saveServiceConfig(key, value) {
    return request('/api/settings/service', 'POST', { key: key, value: value });
  },

  // —— 老师授权 ——
  getTeacherAuths(mac) {
    return request('/api/teacher-auth/list?mac=' + encodeURIComponent(mac), 'GET');
  },
  generateTeacherAuth(payload) {
    return request('/api/teacher-auth/generate', 'POST', payload);
  },
  freezeTeacherAuth(authCode, frozen) {
    return request('/api/teacher-auth/freeze', 'POST', { auth_code: authCode, frozen: frozen !== false });
  },

  // 推送今日作业 { content, isAppend }
  pushHomework(payload) {
    return request('/api/push/homework', 'POST', payload);
  },

  // 推送通知便签 { content, senderType, senderName, persistent }
  pushNote(payload) {
    return request('/api/push/note', 'POST', payload);
  },

  // 历史记录 { pushType: 'all'|'homework'|'note' }
  getHistory(params) {
    return request('/api/push/history', 'GET', params || {});
  },

  // 删除归档
  deleteHistory(id) {
    if (app().globalData.useMock) {
      mock.removeHistory(id);
      return Promise.resolve({ ok: true });
    }
    return request('/api/push/history/' + id, 'DELETE');
  },

  // 老师授权码校验
  verifyAuth(authCode) {
    return request('/api/auth/verify', 'POST', { authCode });
  },

  // 立即同步 A 屏（5 分钟限流）
  syncScreen(mac) {
    return request('/api/device/' + (mac || '') + '/sync', 'POST', {});
  },

  // 双屏快照预览地址
  getSnapshot(mac, screen) {
    return request('/api/device/snapshot?mac=' + encodeURIComponent(mac) + '&screen=' + (screen || 'a'), 'GET');
  }
};

module.exports = api;
