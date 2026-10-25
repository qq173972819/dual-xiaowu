// utils/api.js —— 小程序 <-> 后端 接口层
// 接口对齐 PRD《小程序后台.md》第六节「接口摘要」，并复用 dual 后台的设备模型
// 与 dual_config 存储 —— 小程序与 Web 后台读写同一份数据，互不覆盖。
// 无后端时（globalData.useMock=true）走本地模拟，保证微信开发者工具内可直接演示。

const mock = require('./mock.js');

// 本地模拟时的 dual_config 落盘 key
const MOCK_CFG_KEY = 'INKSIGHT_dual_config_mock';

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
  // 连接器（第三方账号绑定）：本地模拟，保证演示模式可用
  if (path === '/api/v1/mobile/connectors' && method === 'GET') {
    const bound = wx.getStorageSync('mock_connectors') || [];
    return [
      { id: 'market-data', name: '行情数据（平台级）', type: 'platform', category: 'finance', description: '平台统一采购的数据源，所有用户零绑定直接可用。', auth_kind: 'none', guide: [], status: 'included', status_label: '无需绑定，已开通', bind_url: '' },
      { id: 'demo-oauth', name: '示例 OAuth 连接器', type: 'user', category: 'demo', description: '演示用户级 OAuth 授权绑定流程。', auth_kind: 'oauth2', guide: ['点击「去授权」并在授权页确认', '授权完成后返回本页，状态变为「已绑定」'], status: bound.indexOf('demo-oauth') >= 0 ? 'bound' : 'unbound', status_label: bound.indexOf('demo-oauth') >= 0 ? '已绑定' : '去授权', external_account: bound.indexOf('demo-oauth') >= 0 ? 'demo-user@demo-oauth' : '', bind_url: '' }
    ];
  }
  if (path === '/api/v1/mobile/connectors/demo-oauth/authorize' && method === 'POST') {
    const bound = wx.getStorageSync('mock_connectors') || [];
    if (bound.indexOf('demo-oauth') < 0) bound.push('demo-oauth');
    wx.setStorageSync('mock_connectors', bound);
    return { authorize_url: 'mock://authorize?demo=1', state: 'mock_state' };
  }
  if (path === '/api/v1/mobile/connectors/demo-oauth/unbind' && method === 'POST') {
    const bound = (wx.getStorageSync('mock_connectors') || []).filter(function (id) { return id !== 'demo-oauth'; });
    wx.setStorageSync('mock_connectors', bound);
    return { status: 'unbound' };
  }
  // 理财：自选 / 推荐 / 持仓（本地演示数据，涨红跌绿）
  if (path === '/api/finance/watchlist' && method === 'GET') {
    return { watchlist: wx.getStorageSync('mock_watchlist') || [{ code: '600519', name: '贵州茅台' }, { code: '600036', name: '招商银行' }] };
  }
  if (path === '/api/finance/watchlist' && method === 'POST') {
    const parts = String(data.codes || '').split(/[\s,，;；]+/).filter(Boolean).slice(0, 8);
    const list = parts.map((c) => ({ code: c.replace(/^(sh|sz|bj)/, ''), name: '股票' + c.slice(-4) }));
    wx.setStorageSync('mock_watchlist', list);
    return { ok: true, watchlist: list };
  }
  if (path.indexOf('/api/finance/watchlist/') === 0 && method === 'DELETE') {
    const code = path.split('/').pop().toLowerCase();
    const list = (wx.getStorageSync('mock_watchlist') || []).filter((x) => String(x.code).toLowerCase() !== code);
    wx.setStorageSync('mock_watchlist', list);
    return { ok: true, watchlist: list };
  }
  if (path.indexOf('/api/finance/search') === 0 && method === 'GET') {
    const q = decodeURIComponent((path.split('q=')[1] || ''));
    const dict = { '茅台': ['600519', '贵州茅台'], '招行': ['600036', '招商银行'], '招商银行': ['600036', '招商银行'], '宁德': ['300750', '宁德时代'], '乐鑫': ['688018', '乐鑫科技'] };
    const hit = dict[q];
    if (/^\d{6}$/.test(q)) return { items: [{ code: q, name: '' }] };
    return { items: hit ? [{ code: hit[0], name: hit[1] }] : [] };
  }
  if (path === '/api/finance/gold') {
    return {
      items: [
        { code: 'aum', name: '沪金主连', price: 886.4, pct: -0.41 },
        { code: 'agm', name: '沪银主连', price: 13780.0, pct: -1.73 },
        { code: 'GC00Y', name: 'COMEX黄金', price: 4130.1, pct: -0.48 },
        { code: 'AUTD', name: '黄金T+D', price: 884.91, pct: -0.14 },
        { code: 'jm', name: '焦炭主连', price: 1926.5, pct: -0.95 }
      ],
      updated_at: ''
    };
  }
  if (path === '/api/finance/recommend') {
    return {
      items: [
        { code: '300750', name: '宁德时代', price: 212.6, pct: 3.2, reason: '日内动量走强' },
        { code: '600519', name: '贵州茅台', price: 1502.0, pct: 1.1, reason: '温和上行' },
        { code: '601899', name: '紫金矿业', price: 18.4, pct: 0.6, reason: '温和上行' }
      ],
      disclaimer: '推荐结果为公开行情的规则化排序，仅供参考，不构成投资建议'
    };
  }
  if (path === '/api/finance/portfolio' && method === 'GET') {
    const p = wx.getStorageSync('mock_portfolio') || { cash: 20000, positions: [{ code: '600519', name: '贵州茅台', shares: 100, cost: 1400 }, { code: '600036', name: '招商银行', shares: 1000, cost: 40 }] };
    const px = { 600519: 1502.0, 600036: 33.2 };
    let mv = 0, cost = 0;
    const positions = p.positions.map((x) => {
      const price = px[x.code] || x.cost;
      const value = price * x.shares;
      mv += value; cost += x.cost * x.shares;
      return Object.assign({}, x, { price: price, value: value, pnl: (price - x.cost) * x.shares, pnl_pct: (price / x.cost - 1) * 100, weight: null, advice: '持有观察' });
    });
    positions.forEach((x) => { x.weight = mv ? x.value / mv * 100 : 0; });
    return { positions: positions, summary: { market_value: mv, cost_value: cost, pnl: mv - cost, pnl_pct: cost ? (mv / cost - 1) * 100 : null, cash: p.cash, count: positions.length }, notes: [], disclaimer: '调仓参考为规则化提示，不构成投资建议' };
  }
  if (path === '/api/finance/portfolio' && method === 'POST') {
    wx.setStorageSync('mock_portfolio', { cash: data.cash || 0, positions: data.positions || [] });
    return { ok: true, count: (data.positions || []).length };
  }
  if (path === '/api/finance/watchlist/apply' || path === '/api/finance/portfolio/apply') {
    return { ok: true, message: '已写入设备，下次唤醒刷新生效' };
  }
  // 老师授权：列表 / 生成 / 冻结
  if (path.indexOf('/api/teacher-auth/list') === 0 && method === 'GET') {
    return { items: wx.getStorageSync('mock_teacher_auth') || [] };
  }  if (path === '/api/teacher-auth/generate' && method === 'POST') {
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
  // 共享成员：列表 / 邀请 / 移除
  if (path.indexOf('/members') >= 0 || path.indexOf('/share') >= 0) {
    const mac = decodeURIComponent((path.split('/')[4] || '').trim());
    if (method === 'GET') {
      return { members: mock.getMembers(mac) };
    }
    if (method === 'POST' && path.indexOf('/share') >= 0) {
      const ok = mock.shareMember(mac, (data && data.username || '').trim());
      if (!ok) throw new Error('该成员已在列表中');
      return { ok: true };
    }
    if (method === 'DELETE') {
      const uid = decodeURIComponent(path.split('/').pop());
      mock.removeMember(mac, uid);
      return { ok: true };
    }
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

  // —— 共享成员（协作账号：邀请 / 列出 / 移除）——
  getMembers(mac) {
    return request('/api/user/devices/' + encodeURIComponent(mac) + '/members', 'GET');
  },
  shareMember(mac, username) {
    return request('/api/user/devices/' + encodeURIComponent(mac) + '/share', 'POST', { username: username });
  },
  removeMember(mac, userId) {
    return request('/api/user/devices/' + encodeURIComponent(mac) + '/members/' + encodeURIComponent(userId), 'DELETE');
  },

  // 相册照片上传（真实后端走 /api/uploads；本地模拟直接返回临时路径）
  uploadImage(filePath) {
    const g = app().globalData;
    if (g.useMock) {
      return Promise.resolve({ id: 'p' + Date.now().toString(36), url: filePath });
    }
    return new Promise(function (resolve, reject) {
      wx.uploadFile({
        url: g.baseURL + '/api/uploads',
        filePath: filePath,
        name: 'file',
        header: g.token ? { Authorization: 'Bearer ' + g.token } : {},
        success(res) {
          if (res.statusCode >= 200 && res.statusCode < 300) {
            try { resolve(JSON.parse(res.data)); } catch (e) { reject({ message: '上传返回解析失败' }); }
          } else {
            reject({ message: '上传失败 (' + res.statusCode + ')' });
          }
        },
        fail(err) { reject({ message: err.errMsg || '上传失败' }); }
      });
    });
  },

  // 立即同步 A 屏（5 分钟限流）
  syncScreen(mac) {
    return request('/api/device/' + (mac || '') + '/sync', 'POST', {});
  },

  // —— 理财场景：自选股 / 行情 / 推荐股 / 资金管理 ——
  getFinanceWatchlist() {
    return request('/api/finance/watchlist', 'GET');
  },
  saveFinanceWatchlist(codes) {
    return request('/api/finance/watchlist', 'POST', { codes: codes });
  },
  deleteFinanceWatchlist(code) {
    return request('/api/finance/watchlist/' + encodeURIComponent(code), 'DELETE');
  },
  getFinanceQuotes(codes) {
    return request('/api/finance/quote?codes=' + encodeURIComponent((codes || []).join(',')), 'GET');
  },
  getFinanceRecommend() {
    return request('/api/finance/recommend', 'GET');
  },
  getFinanceGold() {
    return request('/api/finance/gold', 'GET');
  },
  financeSearch(q) {
    return request('/api/finance/search?q=' + encodeURIComponent(q), 'GET');
  },
  getFinancePortfolio() {
    return request('/api/finance/portfolio', 'GET');
  },
  saveFinancePortfolio(cash, positions) {
    return request('/api/finance/portfolio', 'POST', { cash: cash || 0, positions: positions });
  },
  applyFinanceToDevice(mac) {
    return request('/api/finance/watchlist/apply', 'POST', { mac: mac });
  },
  applyFinancePortfolioToDevice(mac) {
    return request('/api/finance/portfolio/apply', 'POST', { mac: mac });
  },

  // 双屏快照预览地址
  getSnapshot(mac, screen) {
    return request('/api/device/snapshot?mac=' + encodeURIComponent(mac) + '&screen=' + (screen || 'a'), 'GET');
  },

  // ——— 连接器（第三方账号绑定）———
  // 列表：platform → 无需绑定已开通；user → 已绑定 / 去授权
  listConnectors() {
    return request('/api/v1/mobile/connectors', 'GET');
  },

  // 发起授权：返回第三方授权页地址（页面引导用户打开或复制）
  authorizeConnector(connectorId) {
    return request('/api/v1/mobile/connectors/' + connectorId + '/authorize', 'POST', {});
  },

  // 解除绑定：平台立即删除该用户此连接器的全部凭据
  unbindConnector(connectorId) {
    return request('/api/v1/mobile/connectors/' + connectorId + '/unbind', 'POST', {});
  }
};

module.exports = api;
