'use strict';
const $ = id => document.getElementById(id);
let csrf = '', claimed = false, authenticated = false, hasQr = false, pollingQr = false, refreshing = false, qrExpired = false;
const fragment = new URLSearchParams(location.hash.slice(1));
let ticket = fragment.get('setup') || '';
if (location.hash) history.replaceState(null, '', location.pathname);
$('ticket').value = ticket;
const error = message => { $('error').textContent = message || ''; $('error').hidden = !message; };
function screen(name, step) {
  for (const id of ['loading','account','model','qq','complete']) $(id).hidden = id !== name;
  for (let i=0;i<3;i++) $('step-'+i).className = i < step ? 'done' : i === step ? 'active' : '';
  $('logout').hidden = !authenticated;
}
async function api(path, body) {
  const response = await fetch('/setup/api/'+path, {
    method: body === undefined ? 'GET' : 'POST', credentials:'same-origin', cache:'no-store',
    headers: body === undefined ? {} : {'Content-Type':'application/json','X-Fish-CSRF':csrf},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  let data;
  try { data = await response.json(); } catch (_) { throw new Error('连接暂时中断，请稍后重试。'); }
  if (!response.ok) { const e = new Error(data.error || '操作未完成，请重试。'); e.status = response.status; throw e; }
  return data;
}
function accountScreen() {
  screen('account',0);
  $('ticket-label').hidden = claimed || !!ticket;
  $('confirm-label').hidden = claimed;
  $('account-title').textContent = claimed ? '欢迎回到你的鱼塘' : '给鱼塘设置一个密码';
  $('account-label').textContent = claimed ? '登录管理' : '第一步 / 安全开通';
  $('account-copy').textContent = claimed ? '输入之前设置的管理密码，继续开通或查看状态。' : '以后管理大肥鱼，就用这个密码登录。请妥善保存。';
  $('password').autocomplete = claimed ? 'current-password' : 'new-password';
  $('account-submit').textContent = claimed ? '登录，继续管理 →' : '保存密码，开始开通 →';
}
async function refresh() {
  if (refreshing || !authenticated) return;
  refreshing = true;
  try {
    const state = await api('status'); csrf = state.csrf;
    if (!state.configured) { screen('model',1); return; }
    if (state.reply_confirmed) { screen('complete',3); return; }
    screen('qq',2);
    $('qq-start').disabled = !state.bot_ready;
    if (state.qq_saved) {
      pollingQr = false;
      $('qr-image').hidden = true; $('qr-placeholder').hidden = false;
      $('qr-placeholder').textContent = state.qq_running ? '✓ QQ 已连接' : '绑定已保存，正在连接 QQ…';
      $('qq-title').textContent = '绑定已保存，试着聊一句';
      $('qq-copy').textContent = '我们已经自动保存绑定。收到 QQ 的实际回复后，才算完成最后一步。';
      $('qq-message').textContent = state.qq_running ? 'QQ 接入运行中' : '等待 QQ 接入启动';
      $('qq-start').hidden = true; $('qq-test').hidden = false;
      $('confirm-reply').disabled = !state.qq_running;
    } else if (!hasQr && !qrExpired) {
      $('qr-placeholder').textContent = state.bot_ready ? '准备好了，点击下方生成二维码' : '正在等待机器人启动…';
      $('qq-message').textContent = state.bot_ready ? 'DeepSeek 已激活' : '首次启动可能需要稍等片刻';
    }
  } catch(e) {
    if(e.status === 401) {authenticated = false; claimed = true; accountScreen();}
    else error(e.message);
  } finally {refreshing = false;}
}
$('account-form').addEventListener('submit', async event => {
  event.preventDefault(); error('');
  if (!claimed && $('password').value !== $('password-confirm').value) {error('两次输入的密码不一致，请检查。'); return;}
  $('account-submit').disabled = true;
  try {
    const result = await api('login',{password:$('password').value,ticket:ticket || $('ticket').value.trim()});
    csrf = result.csrf; authenticated = true; claimed = true; ticket = '';
    $('password').value = ''; $('password-confirm').value = ''; $('ticket').value = '';
    await refresh();
  } catch(e) {error(e.message);} finally {$('account-submit').disabled = false;}
});
$('model-form').addEventListener('submit', async event => {
  event.preventDefault(); error(''); $('model-submit').disabled = true;
  $('model-submit').textContent = '正在验证并配置，请稍等…';
  const key = $('model-key').value.trim(); $('model-key').value = '';
  try {await api('activate',{key}); await refresh();}
  catch(e) {error(e.message);}
  finally {$('model-submit').disabled = false; $('model-submit').textContent = '验证并激活 →';}
});
$('qq-start').addEventListener('click', async () => {
  error(''); $('qq-start').disabled = true;
  try {
    const qr = await api('qq/start',{});
    if (qr.status === 'saved') {await refresh(); return;}
    $('qr-image').src = qr.image; $('qr-image').hidden = false; $('qr-placeholder').hidden = true;
    hasQr = true; pollingQr = true; qrExpired = false; $('qq-start').textContent = '二维码过期了？换一个';
    $('qq-message').textContent = '等待手机 QQ 扫码并确认';
  } catch(e) {error(e.message);} finally {$('qq-start').disabled = false;}
});
$('confirm-reply').addEventListener('click', async () => {
  error(''); $('confirm-reply').disabled = true;
  try {await api('confirm',{}); await refresh();} catch(e) {error(e.message); $('confirm-reply').disabled = false;}
});
$('logout').addEventListener('click', async () => {
  try {await api('logout',{});} catch (_) {}
  authenticated = false; pollingQr = false; hasQr = false; qrExpired = false; csrf = '';
  $('qr-image').src = ''; $('qr-image').hidden = true; $('qr-placeholder').hidden = false;
  error(''); accountScreen();
});
async function tick() {
  if (authenticated && pollingQr) {
    try {
      const result = await api('qq/poll',{});
      if (result.status === 'expired') {
        pollingQr = false; hasQr = false; qrExpired = true; $('qr-image').hidden = true; $('qr-placeholder').hidden = false;
        $('qr-placeholder').textContent = '二维码已过期'; $('qq-message').textContent = '请点击下方按钮重新生成二维码';
      } else if (result.status === 'saved') {pollingQr = false; await refresh();}
    } catch(e) {error(e.message);}
  }
  await refresh(); setTimeout(tick, 4000);
}
(async () => {
  try {
    claimed = (await api('public')).claimed;
    try {const s = await api('status'); csrf=s.csrf; authenticated=true;} catch(e) {if(e.status !== 401) throw e;}
    if(authenticated) await refresh(); else accountScreen();
    setTimeout(tick,4000);
  } catch(e) {error(e.message); $('loading').querySelector('h2').textContent='暂时无法连接，请刷新页面重试';}
})();
