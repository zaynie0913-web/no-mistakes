const messages = require('./messages');

const category = process.argv[2];
const sendkey = process.env.SERVERCHAN_KEY;

if (!sendkey) {
  console.error('SERVERCHAN_KEY not set');
  process.exit(1);
}

function getDaysUntilExam() {
  const examDate = new Date('2026-12-26');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.ceil((examDate - today) / (1000 * 60 * 60 * 24));
}

function isWeekend() {
  const now = new Date(new Date().toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }));
  const day = now.getDay();
  return day === 0 || day === 6;
}

function pickCategory(category) {
  if (category === 'study_reminder') return 'study_reminder';
  if (isWeekend() && messages['weekend_' + category]) {
    return 'weekend_' + category;
  }
  return category;
}

const finalCategory = pickCategory(category);
const arr = messages[finalCategory];
if (!arr || arr.length === 0) {
  console.error(`Unknown category: ${finalCategory}`);
  process.exit(1);
}

const msg = arr[Math.floor(Math.random() * arr.length)];
const days = getDaysUntilExam();
const countdown = days > 0 ? `\n\n📅 距考研还有 ${days} 天` : '';
const body = msg + countdown;

fetch(`https://sctapi.ftqq.com/${sendkey}.send`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    title: '屠龙克克',
    desp: body,
  }),
}).then(res => res.json()).then(data => {
  if (data.code === 0) {
    console.log(`Sent [${finalCategory}]: ${msg.substring(0, 40)}...`);
  } else {
    console.error(`Failed: ${data.message}`);
    process.exit(1);
  }
}).catch(err => {
  console.error(err.message);
  process.exit(1);
});
