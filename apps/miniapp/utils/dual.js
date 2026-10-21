// utils/dual.js —— 与 dual 控制台共用的数据结构与默认值
// 字段严格对齐 dual/app/page.tsx 的 ContentState / ScreenConfig / CourseTable，
// 保证小程序写进 dual_config 的数据能被 Web 后台与固件正确解析，两端互不覆盖。

// 场景库（与 dual 的 scenes 一致）
const SCENES = [
  { key: 'family', name: '家庭', eyebrow: '默认场景', desc: '让每个人都看见今天的重要安排' },
  { key: 'study', name: '学习', eyebrow: '学生与家庭', desc: '课程、作业和目标始终在视线里' },
  { key: 'finance', name: '理财', eyebrow: '行情关注', desc: '快速价格与低频趋势各司其职' },
  { key: 'office', name: '办公', eyebrow: '专注工作', desc: '工作状态与氛围展示的双屏组合' },
  { key: 'custom', name: '自定义', eyebrow: '自由组合', desc: '从模块库搭建适合自己的双屏' }
];

// 各场景 A/B 屏默认配置（与 dual 的 scenes 常量一致）
const DEFAULT_SCREENS = {
  family: {
    a: { title: '今日家庭提醒', source: '后台家庭待办', refresh: '每 10 分钟', modules: ['天气', '快递与缴费', '家庭待办', '采购清单', '纪念日'] },
    b: { title: '共享日程', source: '飞书日历', refresh: '每 30 分钟', modules: ['月/周视图', '成员标记', '今日重点', '节假日', '下一项安排'] }
  },
  study: {
    a: { title: '作业与便签', source: '后台作业/便签', refresh: '每 5 分钟', modules: ['今日作业', '老师便签', '背词任务', '阅读任务', '完成进度'] },
    b: { title: '课程与目标', source: '课程表/倒计时', refresh: '每 15 分钟', modules: ['课程表', '考试倒计时', '当前课程', '下一节课', '自动切换', '相册'] }
  },
  finance: {
    a: { title: '实时行情', source: '行情数据源', refresh: '每 5 分钟', modules: ['股票自选', '黄金/期货', '当前价格', '涨跌幅', '交易状态'] },
    b: { title: '行情趋势', source: '趋势聚合', refresh: '每 10 分钟', modules: ['日内趋势', '日 K 简图', '涨跌排行', '资产概览', '市场摘要'] }
  },
  office: {
    a: { title: '工作信息', source: '后台模块组合', refresh: '每 10 分钟', modules: ['Token 余额', '今日调用', '本月费用', '服务状态', '每日资讯'] },
    b: { title: '办公展示', source: '后台相册/TF 卡', refresh: '每 30 分钟', modules: ['团队照片', '企业海报', '产品宣传', '二维码', '会议室状态'] }
  },
  custom: {
    a: { title: '自定义 A 屏', source: '用户配置', refresh: '自定义', modules: ['待办', '天气', '便签'] },
    b: { title: '自定义 B 屏', source: '用户配置', refresh: '自定义', modules: ['日程', '相册', '倒计时'] }
  }
};

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

// 课程表骨架：6 节课 × 7 天，默认只显示工作日
function emptyCourseTable() {
  const defs = [[8, 0, 8, 45], [8, 55, 9, 40], [10, 0, 10, 45], [10, 55, 11, 40], [14, 0, 14, 45], [14, 55, 15, 40]];
  const periods = defs.map(function (d) {
    return { start_hour: d[0], start_minute: d[1], end_hour: d[2], end_minute: d[3] };
  });
  const grid = [];
  for (let d = 0; d < 7; d++) {
    const row = [];
    for (let p = 0; p < periods.length; p++) row.push(null);
    grid.push(row);
  }
  return {
    enabled: true,
    period_count: periods.length,
    show_days: 5,
    semester_start: 1,
    periods: periods,
    grid: grid,
    school: '',
    class_name: '课程表',
    breaks: []
  };
}

// 新建一套内容时的默认值
function defaultContent() {
  return {
    courseTable: emptyCourseTable(),
    notes: [],
    homework: [],
    reading: []
  };
}

// 补齐缺失字段，避免后端/固件读到 undefined
function normalizeContent(raw) {
  const c = raw || {};
  const ct = c.courseTable && c.courseTable.grid ? c.courseTable : emptyCourseTable();
  if (!Array.isArray(ct.periods) || !ct.periods.length) ct.periods = emptyCourseTable().periods;
  if (!Array.isArray(ct.grid) || !ct.grid.length) ct.grid = emptyCourseTable().grid;
  return {
    courseTable: ct,
    notes: Array.isArray(c.notes) ? c.notes : [],
    homework: Array.isArray(c.homework) ? c.homework : [],
    reading: Array.isArray(c.reading) ? c.reading : [],
    // 以下模块小程序不编辑，原样透传，避免覆盖 dual 后台维护的数据
    album: c.album || { enabled: true, photos: [] },
    bills: Array.isArray(c.bills) ? c.bills : [],
    express: Array.isArray(c.express) ? c.express : [],
    familySchedule: Array.isArray(c.familySchedule) ? c.familySchedule : [],
    familyShopping: Array.isArray(c.familyShopping) ? c.familyShopping : [],
    familyAnniversaries: Array.isArray(c.familyAnniversaries) ? c.familyAnniversaries : []
  };
}

// 组装写入后端的完整 payload（与 dual 的 buildPayload 一致）
function buildPayload(cfg, mac) {
  const base = cfg || {};
  const sceneKey = base.sceneKey || 'family';
  const config = base.config || {};
  for (const k in DEFAULT_SCREENS) {
    if (!config[k]) config[k] = JSON.parse(JSON.stringify(DEFAULT_SCREENS[k]));
  }
  return {
    version: 1,
    sceneKey: sceneKey,
    config: config,
    content: normalizeContent(base.content),
    screen: base.screen || { size: '4.2', colors: 4 },
    deviceMac: mac || '',
    savedAt: new Date().toISOString()
  };
}

module.exports = {
  SCENES: SCENES,
  DEFAULT_SCREENS: DEFAULT_SCREENS,
  uid: uid,
  today: today,
  emptyCourseTable: emptyCourseTable,
  defaultContent: defaultContent,
  normalizeContent: normalizeContent,
  buildPayload: buildPayload
};
