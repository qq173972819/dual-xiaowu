// 个人信息（对齐 dual 后台「个人信息」）
// 大模型配置是 OCR / AI 内容生成的统一 Key 来源；孩子信息用于推算年级。
// 家庭与设备位置（家庭成员 + 城市）供家庭共享日程、天气看板使用，数据随双屏配置下发。
const api = require('../../utils/api.js');
const dual = require('../../utils/dual.js');

const PROVIDERS = ['deepseek', 'aliyun', 'moonshot', 'siliconflow', 'openai'];
const PROVIDER_LABELS = ['DeepSeek', '阿里云通义', 'Kimi（月之暗面）', '硅基流动 SiliconFlow', 'OpenAI'];
const ACCESS = ['platform', 'preset', 'custom_openai'];
const ACCESS_LABELS = ['平台额度（免费，无需 Key）', '自带 Key（预设厂商）', '自定义 OpenAI 兼容端点'];
const IMG_PROVIDERS = ['aliyun', 'siliconflow', 'openai'];
const IMG_LABELS = ['阿里云通义', '硅基流动 SiliconFlow', 'OpenAI'];

Page({
  data: {
    loading: true,
    saving: false,
    providers: PROVIDERS,
    providerLabels: PROVIDER_LABELS,
    accessModes: ACCESS,
    accessLabels: ACCESS_LABELS,
    imgProviders: IMG_PROVIDERS,
    imgLabels: IMG_LABELS,

    llm: {},
    accessIdx: 0,
    providerIdx: 0,
    imgIdx: 0,
    apiKey: '',
    imgApiKey: '',

    child: {},

    // 家庭与设备位置
    mac: '',
    cfg: null,
    familyMembers: [],
    weatherCity: '',
    newMember: '',
    familySaving: false
  },

  onLoad() {
    const dev = getApp().globalData.currentDevice;
    const mac = (dev && (dev.mac || dev.sn)) || '';
    this.setData({ mac: mac });
    this.load();
  },

  load() {
    this.setData({ loading: true });
    const proms = [api.getProfile(), api.getChild()];
    if (this.data.mac) proms.push(api.getDualConfig(this.data.mac));
    Promise.all(proms)
      .then((res) => {
        const llm = (res[0] && res[0].llm_config) || {};
        const child = (res[1] && res[1].child) || {};
        const cfg = res[2] ? (res[2].config || {}) : {};
        const content = dual.normalizeContent(cfg.content);
        this.setData({
          llm: llm,
          child: child,
          accessIdx: Math.max(0, ACCESS.indexOf(llm.llm_access_mode || 'preset')),
          providerIdx: Math.max(0, PROVIDERS.indexOf(llm.provider || 'deepseek')),
          imgIdx: Math.max(0, IMG_PROVIDERS.indexOf(llm.image_provider || 'aliyun')),
          cfg: cfg,
          familyMembers: content.familyMembers || [],
          weatherCity: content.weatherCity || '',
          loading: false
        });
      })
      .catch((e) => {
        this.setData({ loading: false });
        wx.showToast({ title: e.message || '加载失败', icon: 'none' });
      });
  },

  onAccess(e) { this.setData({ accessIdx: parseInt(e.detail.value, 10) }); },
  onProvider(e) { this.setData({ providerIdx: parseInt(e.detail.value, 10) }); },
  onImgProvider(e) { this.setData({ imgIdx: parseInt(e.detail.value, 10) }); },
  onModel(e) { this.setData({ 'llm.model': e.detail.value }); },
  onBaseUrl(e) { this.setData({ 'llm.base_url': e.detail.value }); },
  onImgModel(e) { this.setData({ 'llm.image_model': e.detail.value }); },
  onApiKey(e) { this.setData({ apiKey: e.detail.value }); },
  onImgApiKey(e) { this.setData({ imgApiKey: e.detail.value }); },

  onChildName(e) { this.setData({ 'child.name': e.detail.value }); },
  onChildBirth(e) { this.setData({ 'child.birth': e.detail.value }); },
  onChildGender(e) { this.setData({ 'child.gender': e.detail.value }); },
  onChildSchool(e) { this.setData({ 'child.school': e.detail.value }); },
  onChildGrade(e) { this.setData({ 'child.grade_override': e.detail.value }); },

  onSaveLlm() {
    if (this.data.saving) return;
    this.setData({ saving: true });
    wx.showLoading({ title: '保存中' });
    const body = {
      llm_access_mode: this.data.accessModes[this.data.accessIdx],
      provider: this.data.providers[this.data.providerIdx],
      model: this.data.llm.model || '',
      base_url: this.data.llm.base_url || '',
      image_provider: this.data.imgProviders[this.data.imgIdx],
      image_model: this.data.llm.image_model || '',
      image_base_url: this.data.llm.image_base_url || ''
    };
    // 空 = 保留已有 Key
    if (this.data.apiKey) body.api_key = this.data.apiKey;
    if (this.data.imgApiKey) body.image_api_key = this.data.imgApiKey;

    api.saveLlm(body)
      .then(() => {
        wx.hideLoading();
        this.setData({ saving: false, apiKey: '', imgApiKey: '' });
        wx.showToast({ title: '大模型配置已保存', icon: 'success' });
        this.load();
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ saving: false });
        wx.showToast({ title: e.message || '保存失败', icon: 'none' });
      });
  },

  onSaveChild() {
    if (this.data.saving) return;
    this.setData({ saving: true });
    wx.showLoading({ title: '保存中' });
    const c = this.data.child;
    api.saveChild({
      name: c.name || '', birth: c.birth || '', gender: c.gender || '',
      school: c.school || '', grade_override: c.grade_override || ''
    })
      .then(() => {
        wx.hideLoading();
        this.setData({ saving: false });
        wx.showToast({ title: '孩子信息已保存', icon: 'success' });
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ saving: false });
        wx.showToast({ title: e.message || '保存失败', icon: 'none' });
      });
  },

  // —— 家庭与设备位置 ——
  onCity(e) { this.setData({ weatherCity: e.detail.value }); },
  onNewMember(e) { this.setData({ newMember: e.detail.value }); },
  addMember() {
    const name = (this.data.newMember || '').trim();
    if (!name) { wx.showToast({ title: '请输入成员姓名 / 称呼', icon: 'none' }); return; }
    if (this.data.familyMembers.some((m) => m.name === name)) {
      wx.showToast({ title: '该成员已存在', icon: 'none' }); return;
    }
    const role = this.data.familyMembers.length === 0 ? 'admin' : 'member';
    this.setData({
      familyMembers: this.data.familyMembers.concat([{ name: name, role: role }]),
      newMember: ''
    });
  },
  setAdmin(e) {
    const name = e.currentTarget.dataset.name;
    this.setData({
      familyMembers: this.data.familyMembers.map((m) => Object.assign({}, m, { role: m.name === name ? 'admin' : 'member' }))
    });
  },
  removeMember(e) {
    const name = e.currentTarget.dataset.name;
    this.setData({ familyMembers: this.data.familyMembers.filter((m) => m.name !== name) });
  },
  saveFamily() {
    if (this.data.familySaving) return;
    if (!this.data.mac) { wx.showToast({ title: '请先选择设备', icon: 'none' }); return; }
    this.setData({ familySaving: true });
    wx.showLoading({ title: '保存中' });
    api.getDualConfig(this.data.mac)
      .then((r) => {
        const cfg = r.config || this.data.cfg || {};
        const content = Object.assign({}, cfg.content || {}, {
          familyMembers: this.data.familyMembers,
          weatherCity: this.data.weatherCity
        });
        cfg.content = content;
        const payload = dual.buildPayload(cfg, this.data.mac);
        return api.saveDualConfig(this.data.mac, payload);
      })
      .then(() => {
        wx.hideLoading();
        this.setData({ familySaving: false });
        wx.showToast({ title: '家庭设置已保存', icon: 'success' });
      })
      .catch((e) => {
        wx.hideLoading();
        this.setData({ familySaving: false });
        wx.showToast({ title: e.message || '保存失败', icon: 'none' });
      });
  }
});
