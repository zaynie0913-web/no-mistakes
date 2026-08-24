const messages = require('./messages');

const category = process.argv[2];
const priority = parseInt(process.argv[3] || '3');
const tags = (process.argv[4] || 'crossed_swords').split(',');
const topic = process.env.NTFY_TOPIC;

if (!topic) {
  console.error('NTFY_TOPIC not set');
  process.exit(1);
}

function getDaysUntilExam() {
  const examDate = new Date('2026-12-26');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.ceil((examDate - today) / (1000 * 60 * 60 * 24));
}

const arr = messages[category];
if (!arr || arr.length === 0) {
  console.error(`Unknown category: ${category}`);
  process.exit(1);
}

const msg = arr[Math.floor(Math.random() * arr.length)];
const days = getDaysUntilExam();
const countdown = days > 0 ? `\n\n📅 距考研还有 ${days} 天` : '';
const body = msg + countdown;

fetch('https://ntfy.sh', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    topic: topic,
    title: '屠龙克克',
    message: body,
    tags: tags,
    priority: priority,
  }),
}).then(res => {
  if (res.ok) {
    console.log(`Sent [${category}]: ${msg.substring(0, 40)}...`);
  } else {
    res.text().then(t => console.error(`Failed: ${res.status} ${t}`));
    process.exit(1);
  }
}).catch(err => {
  console.error(err.message);
  process.exit(1);
});
