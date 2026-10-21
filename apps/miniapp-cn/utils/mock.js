// utils/mock.js —— 本地模拟数据层（无后端时驱动整个小程序）
// 仅用于演示 / 微信开发者工具内运行；接正式后端后由 api.js 走真实请求。

const DEVICES_KEY = 'mock_devices';
const HISTORY_KEY = 'mock_history';
const SYNC_KEY = 'mock_sync';
const MEMBERS_KEY = 'mock_members';

function seedDevices() {
  return [
    {
      sn: 'MQ20260001',
      name: '铠泽的墨看板',
      stage: '初中',
      online: true,
      firmware: 'v1.4.0',
      aInterval: 30,
      bTime: '08:00',
      mode: '初三备考模式'
    },
    {
      sn: 'MQ20260002',
      name: '妹妹的墨看板',
      stage: '小学',
      online: false,
      firmware: 'v1.4.0',
      aInterval: 60,
      bTime: '08:00',
      mode: '小学日常模式'
    }
  ];
}

function getDevices() {
  let list = wx.getStorageSync(DEVICES_KEY);
  if (!list || !list.length) {
    list = seedDevices();
    wx.setStorageSync(DEVICES_KEY, list);
  }
  return list;
}

function getHistory() {
  return wx.getStorageSync(HISTORY_KEY) || [];
}

function addHistory(rec) {
  const list = getHistory();
  list.unshift(rec);
  wx.setStorageSync(HISTORY_KEY, list);
  return rec;
}

function removeHistory(id) {
  const list = getHistory().filter(r => r.id !== id);
  wx.setStorageSync(HISTORY_KEY, list);
  return list;
}

// 老师授权码模拟：任意 6 位数字可用；示例码 123456 绑定铠泽设备
function verifyAuth(code) {
  if (!/^\d{6}$/.test(code)) {
    return { ok: false, message: '授权码须为 6 位数字' };
  }
  return {
    ok: true,
    authCode: code,
    studentName: '铠泽',
    deviceSn: 'MQ20260001',
    permission: code === '123456' ? 'note_append' : 'note_only'
  };
}

// 模拟一次强制同步 A 屏（5 分钟限流）
function syncScreen(sn) {
  const last = wx.getStorageSync(SYNC_KEY) || {};
  const now = Date.now();
  const gap = 5 * 60 * 1000;
  if (last[sn] && now - last[sn] < gap) {
    const waitSec = Math.ceil((gap - (now - last[sn])) / 1000);
    return { ok: false, cooldown: true, waitSec, message: `受墨水屏保护，${waitSec} 秒后可再次强制刷新` };
  }
  last[sn] = now;
  wx.setStorageSync(SYNC_KEY, last);
  return { ok: true, message: '已触发 A 屏刷新，设备将在数秒内更新' };
}

// —— 共享成员（按设备 sn 维护，复用 dual 的绑定关系语义）——
function getMembers(sn) {
  const all = wx.getStorageSync(MEMBERS_KEY) || {};
  if (!all[sn] || !all[sn].length) {
    all[sn] = [{ userId: 'owner_self', username: '我（所有者）', role: 'owner' }];
    wx.setStorageSync(MEMBERS_KEY, all);
  }
  return all[sn];
}
function shareMember(sn, username) {
  const all = wx.getStorageSync(MEMBERS_KEY) || {};
  const list = all[sn] || [{ userId: 'owner_self', username: '我（所有者）', role: 'owner' }];
  if (list.some(m => m.username === username)) return false;
  list.push({ userId: 'u_' + username + '_' + Date.now().toString(36), username: username, role: 'member' });
  all[sn] = list;
  wx.setStorageSync(MEMBERS_KEY, all);
  return true;
}
function removeMember(sn, userId) {
  const all = wx.getStorageSync(MEMBERS_KEY) || {};
  all[sn] = (all[sn] || []).filter(m => m.userId !== userId);
  wx.setStorageSync(MEMBERS_KEY, all);
}

module.exports = {
  getDevices,
  getHistory,
  addHistory,
  removeHistory,
  verifyAuth,
  syncScreen,
  getMembers,
  shareMember,
  removeMember
};
