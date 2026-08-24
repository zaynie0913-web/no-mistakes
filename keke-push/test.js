require('dotenv').config();

const TOPIC = process.env.NTFY_TOPIC;

async function testSend() {
  const examDate = new Date('2026-12-26');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.ceil((examDate - today) / (1000 * 60 * 60 * 24));

  const msg = `这是一条测试消息。如果你看到了，说明推送系统搭好了！\n\n📅 距考研还有 ${days} 天`;

  console.log(`正在发送测试消息到频道: ${TOPIC}`);

  try {
    const res = await fetch('https://ntfy.sh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic: TOPIC,
        title: '屠龙克克',
        message: msg,
        tags: ['crossed_swords'],
        priority: 3,
      }),
    });

    if (res.ok) {
      console.log('发送成功！去手机上检查 ntfy APP');
    } else {
      const text = await res.text();
      console.error(`发送失败: ${res.status} ${text}`);
    }
  } catch (err) {
    console.error('发送出错:', err.message);
  }
}

testSend();
