require('dotenv').config();
const cron = require('node-cron');
const messages = require('./messages');

const TOPIC = process.env.NTFY_TOPIC;
if (!TOPIC) {
  console.error('请在 .env 文件中设置 NTFY_TOPIC');
  process.exit(1);
}

function getDaysUntilExam() {
  const examDate = new Date('2026-12-26');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.ceil((examDate - today) / (1000 * 60 * 60 * 24));
}

function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

async function sendMessage(message, { title = '屠龙克克', tags = ['crossed_swords'], priority = 3 } = {}) {
  const days = getDaysUntilExam();
  const countdown = days > 0 ? `\n\n📅 距考研还有 ${days} 天` : '';
  const body = message + countdown;

  try {
    const res = await fetch('https://ntfy.sh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic: TOPIC,
        title: title,
        message: body,
        tags: tags,
        priority: priority,
      }),
    });
    const now = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
    if (res.ok) {
      console.log(`[${now}] 已发送: ${message.substring(0, 30)}...`);
    } else {
      console.error(`[${now}] 发送失败: ${res.status} ${res.statusText}`);
    }
  } catch (err) {
    const now = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
    console.error(`[${now}] 发送出错:`, err.message);
  }
}

function sendRandom(category, opts = {}) {
  const msg = pickRandom(messages[category]);
  return sendMessage(msg, opts);
}

const cronOpts = { timezone: 'Asia/Shanghai' };

// 早上 7:30
cron.schedule('30 7 * * *', () => sendRandom('morning'), cronOpts);

// 中午 12:00
cron.schedule('0 12 * * *', () => sendRandom('lunch'), cronOpts);

// 下午 17:30
cron.schedule('30 17 * * *', () => sendRandom('afternoon'), cronOpts);

// 晚上 22:30 — 优先级更高，催睡
cron.schedule('30 22 * * *', () => sendRandom('night', { priority: 4, tags: ['crossed_swords', 'zzz'] }), cronOpts);

function scheduleRandomBonus() {
  const now = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }));
  const hour = 14 + Math.floor(Math.random() * 6);
  const minute = Math.floor(Math.random() * 60);

  const target = new Date(now);
  target.setHours(hour, minute, 0, 0);

  if (target > now) {
    const delay = target - now;
    const h = String(target.getHours()).padStart(2, '0');
    const m = String(target.getMinutes()).padStart(2, '0');
    console.log(`今日随机加餐时间: ${h}:${m}`);
    setTimeout(() => sendRandom('bonus'), delay);
  } else {
    console.log('今天的随机加餐时间已过，明天再来');
  }
}

// 每天午夜重新计算随机加餐时间
cron.schedule('0 0 * * *', () => scheduleRandomBonus(), cronOpts);

// 启动
console.log('=============================');
console.log('  屠龙克克推送系统已启动');
console.log(`  频道: ${TOPIC}`);
console.log(`  距考研还有 ${getDaysUntilExam()} 天`);
console.log('=============================');
console.log('推送时间 (北京时间):');
console.log('  07:30 早安');
console.log('  12:00 午饭');
console.log('  17:30 下午休息');
console.log('  22:30 晚安');
console.log('  14:00-20:00 随机加餐');
console.log('=============================');

scheduleRandomBonus();
