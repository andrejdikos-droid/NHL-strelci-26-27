const $ = (s) => document.querySelector(s);
const fmtMoney = v => `${v >= 0 ? '+' : '−'}${Math.abs(Math.round(v))} €`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
}[c]));

let league, stats, history, trades, computed;

async function loadJSON(path) {
  const r = await fetch(`${path}?v=${Date.now()}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

function playerStat(name) {
  return stats.players?.[name] || {
    goals: 0,
    gamesPlayed: 0,
    team: '—',
    deltaGoals: 0,
    found: false
  };
}

function slotContribution(slot) {
  const current = playerStat(slot.player).goals || 0;
  return (slot.bankedGoals || 0) +
    Math.max(0, current - (slot.goalsAtAcquisition || 0));
}

function slotDaily(slot) {
  const p = playerStat(slot.player);
  const current = p.goals || 0;
  const acquisitionFloor = slot.goalsAtAcquisition || 0;
  const eligibleSeasonGoals = Math.max(0, current - acquisitionFloor);

  return Math.min(p.deltaGoals || 0, eligibleSeasonGoals);
}

function calculate() {
  const n = league.managers.length;
  const valuePerGoal = league.valuePerGoal || 1;

  const rows = league.managers.map(m => {
    const goals = m.roster.reduce((s, p) => s + slotContribution(p), 0);
    const today = m.roster.reduce((s, p) => s + slotDaily(p), 0);
    return { ...m, goals, today };
  });

  const total = rows.reduce((s, m) => s + m.goals, 0);
  const todayTotal = rows.reduce((s, m) => s + m.today, 0);

  rows.forEach(m => {
    m.gross = m.goals * (n - 1) * valuePerGoal;
    m.loss = (total - m.goals) * valuePerGoal;
    m.net = m.gross - m.loss;
    m.todayNet = (n * m.today - todayTotal) * valuePerGoal;
  });

  rows.sort((a, b) =>
    b.goals - a.goals ||
    b.net - a.net ||
    a.name.localeCompare(b.name)
  );

  return { rows, total, todayTotal };
}

function renderTop() {
  const leader = computed.rows[0];
  $('#leaderName').textContent = leader?.name || '—';
  $('#leaderMeta').textContent = leader
    ? `${leader.goals} gólov · ${fmtMoney(leader.net)}`
    : '—';

  $('#leagueGoals').textContent = computed.total;
  $('#nightGoals').textContent = `${computed.todayTotal} G`;
  $('#nightMeta').textContent = computed.todayTotal
    ? 'od posledného update'
    : 'bez zmeny';

  $('#updatedAt').textContent = stats.updatedAt
    ? new Date(stats.updatedAt).toLocaleString('sk-SK', {
        dateStyle: 'short',
        timeStyle: 'short'
      })
    : 'čaká sa na prvý update';
}

function renderLeaderboard() {
  $('#leaderboardBody').innerHTML = computed.rows.map((m, i) => `
    <tr>
      <td class="rank">${i + 1}</td>
      <td>
        <span class="manager">${esc(m.name)}</span>
        <span class="badge">${m.tradesUsed || 0}/4 T</span>
      </td>
      <td class="goals">${m.goals}</td>
      <td>${m.today ? `+${m.today}` : '—'}</td>
      <td>${Number(m.projection).toFixed(1)}</td>
      <td>${Math.round(m.gross)} €</td>
      <td>−${Math.round(m.loss)} €</td>
      <td class="money ${m.net >= 0 ? 'positive' : 'negative'}">
        ${fmtMoney(m.net)}
      </td>
    </tr>
  `).join('');
}

function renderTeams() {
  $('#teamGrid').innerHTML = computed.rows.map((m, i) => `
    <article class="team-card">
      <div class="team-head">
        <div>
          <div class="kicker">#${i + 1} · ${m.tradesUsed || 0}/4 trejdy</div>
          <div class="team-name">${esc(m.name)}</div>
          <div class="muted">Predsezónny model ${Number(m.projection).toFixed(1)} G</div>
        </div>
        <div class="team-score">
          <strong>${m.goals} G</strong>
          <span class="${m.net >= 0 ? 'plus' : 'minus'}">${fmtMoney(m.net)}</span>
        </div>
      </div>

      ${m.roster.map(slot => {
        const p = playerStat(slot.player);
        const credited = slotContribution(slot);

        return `
          <div class="player-row">
            <div>
              <div class="player-name">${esc(slot.player)}</div>
              <div class="player-sub">
                ${esc(p.team || '—')} · ${p.gamesPlayed || 0} GP
                ${p.found === false ? ' · čaká na NHL dáta' : ''}
              </div>
            </div>
            <div class="player-g">${credited} G</div>
            <div class="player-delta">${slotDaily(slot) ? `+${slotDaily(slot)}` : '—'}</div>
          </div>
        `;
      }).join('')}
    </article>
  `).join('');
}

function renderDaily() {
  const items = [];

  league.managers.forEach(m => {
    m.roster.forEach(slot => {
      const p = playerStat(slot.player);
      const creditedToday = slotDaily(slot);

      if (creditedToday > 0) {
        items.push({
          name: slot.player,
          manager: m.name,
          team: p.team,
          goals: creditedToday
        });
      }
    });
  });

  items.sort((a, b) =>
    b.goals - a.goals ||
    a.name.localeCompare(b.name)
  );

  $('#dailyList').innerHTML = items.length
    ? items.map(x => `
      <div class="daily-item">
        <div>
          <strong>${esc(x.name)}</strong>
          <div class="who">${esc(x.manager)} · ${esc(x.team || '—')}</div>
        </div>
        <div class="daily-goals">+${x.goals} G</div>
        <div class="money positive">+${x.goals * 10} € hrubé</div>
      </div>
    `).join('')
    : `<div class="empty">Od poslednej aktualizácie zatiaľ nemáme nový gól draftovaného hráča.</div>`;
}

function renderHistory() {
  const canvas = $('#historyChart');
  const ctx = canvas.getContext('2d');
  const snaps = history.snapshots || [];
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;

  canvas.width = Math.max(600, rect.width * dpr);
  canvas.height = 380 * dpr;
  ctx.scale(dpr, dpr);

  const W = canvas.width / dpr;
  const H = canvas.height / dpr;

  ctx.clearRect(0, 0, W, H);

  if (snaps.length < 2) {
    ctx.fillStyle = '#93a8bd';
    ctx.font = '14px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText('Graf sa zobrazí po aspoň dvoch denných snapshot-och.', W / 2, H / 2);
    return;
  }

  const names = league.managers.map(m => m.name);
  const colors = [
    '#67d7ff','#6ee7a8','#f5c451','#ff8da1','#a78bfa',
    '#60a5fa','#fb923c','#34d399','#f472b6','#c4b5fd','#94a3b8'
  ];

  const vals = snaps.flatMap(s =>
    names.map(n => s.managers?.[n]?.net ?? 0)
  );

  let min = Math.min(...vals, 0);
  let max = Math.max(...vals, 0);
  if (max === min) {
    max += 10;
    min -= 10;
  }

  const pad = { l: 50, r: 18, t: 18, b: 36 };
  const cw = W - pad.l - pad.r;
  const ch = H - pad.t - pad.b;

  const y = v => pad.t + (max - v) / (max - min) * ch;
  const x = i => pad.l + (i / (snaps.length - 1)) * cw;

  ctx.strokeStyle = '#213750';
  ctx.lineWidth = 1;

  for (let k = 0; k <= 4; k++) {
    const yy = pad.t + (k / 4) * ch;
    ctx.beginPath();
    ctx.moveTo(pad.l, yy);
    ctx.lineTo(W - pad.r, yy);
    ctx.stroke();

    const val = max - (k / 4) * (max - min);
    ctx.fillStyle = '#7890a5';
    ctx.font = '11px system-ui';
    ctx.textAlign = 'right';
    ctx.fillText(`${Math.round(val)}€`, pad.l - 8, yy + 4);
  }

  names.forEach((name, idx) => {
    ctx.strokeStyle = colors[idx % colors.length];
    ctx.lineWidth = name === 'DIKSO' ? 3 : 1.8;
    ctx.beginPath();

    snaps.forEach((s, i) => {
      const yy = y(s.managers?.[name]?.net ?? 0);
      if (i === 0) ctx.moveTo(x(i), yy);
      else ctx.lineTo(x(i), yy);
    });

    ctx.stroke();
  });
}

function ensureTradeUI() {
  if (document.querySelector('[data-view="trades"]')) return;

  const tabs = document.querySelector('.tabs');
  const rulesView = document.getElementById('rules');

  tabs.insertAdjacentHTML('beforeend',
    '<button class="tab" data-view="trades">Trejdy</button>'
  );

  rulesView.insertAdjacentHTML('beforebegin', `
    <section id="trades" class="view">
      <div class="trade-layout">
        <div class="panel">
          <div class="panel-head">
            <div>
              <div class="kicker">História výmien</div>
              <h2>Trejdy</h2>
            </div>
          </div>
          <div id="tradeHistory" class="daily-list"></div>
        </div>

        <div class="panel">
          <div class="kicker">Admin</div>
          <h2>Registrovať trejd</h2>
          <p class="formula" style="margin:8px 0 18px">
            Formulár pripraví GitHub požiadavku. Zmenu môže potvrdiť iba účet
            <strong>andrejdikos-droid</strong>. Po potvrdení sa zostava a bilancie
            automaticky prepočítajú.
          </p>

          <form id="tradeForm" class="trade-form">
            <label>
              Manažér
              <select id="tradeManager" required></select>
            </label>

            <label>
              OUT
              <select id="tradeOut" required></select>
            </label>

            <label>
              IN – presné meno NHL hráča
              <input id="tradeIn" type="text" placeholder="napr. Timo Meier" required />
            </label>

            <label>
              Poznámka
              <input id="tradeNote" type="text" placeholder="voliteľné" />
            </label>

            <label class="trade-check">
              <input id="tradeFree" type="checkbox" />
              <span>Nerátať medzi 4 voľné trejdy</span>
            </label>

            <button class="trade-submit" type="submit">Pripraviť registráciu trejdu</button>
          </form>

          <div class="trade-warning">
            Po kliknutí sa otvorí GitHub s pripravenou požiadavkou. Tam už iba klikneš
            <strong>Submit new issue</strong>. Automatizácia následne trejd zapíše.
          </div>
        </div>
      </div>
    </section>
  `);

  const style = document.createElement('style');
  style.textContent = `
    .trade-layout{display:grid;grid-template-columns:1.15fr .85fr;gap:13px}
    .trade-form{display:grid;gap:13px}
    .trade-form label{display:grid;gap:6px;color:#9fb2c4;font-size:12px;font-weight:700}
    .trade-form input,.trade-form select{
      width:100%;padding:12px 13px;border-radius:11px;border:1px solid #213750;
      background:#091725;color:#f4f8ff;font:inherit;outline:none
    }
    .trade-form input:focus,.trade-form select:focus{border-color:#67d7ff}
    .trade-check{display:flex!important;grid-template-columns:auto 1fr!important;align-items:center;gap:9px!important}
    .trade-check input{width:auto}
    .trade-submit{
      border:0;border-radius:11px;padding:13px 15px;background:#67d7ff;color:#06111d;
      font-weight:900;cursor:pointer
    }
    .trade-warning{
      margin-top:13px;padding:12px 13px;border-radius:11px;background:#0a1727;
      border:1px solid #213750;color:#93a8bd;font-size:12px;line-height:1.5
    }
    .trade-type{display:inline-flex;padding:3px 7px;border-radius:999px;font-size:10px;font-weight:900;
      background:#16334e;color:#9bdfff;margin-left:6px}
    .trade-type.free{background:#2a3140;color:#f5c451}
    @media(max-width:900px){.trade-layout{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);

  const managerSelect = document.getElementById('tradeManager');
  const outSelect = document.getElementById('tradeOut');

  managerSelect.innerHTML = league.managers
    .map(m => `<option value="${esc(m.name)}">${esc(m.name)} · ${m.tradesUsed || 0}/4</option>`)
    .join('');

  const refreshOut = () => {
    const manager = league.managers.find(m => m.name === managerSelect.value);
    outSelect.innerHTML = (manager?.roster || [])
      .map(s => `<option value="${esc(s.player)}">${esc(s.player)}</option>`)
      .join('');
  };

  managerSelect.addEventListener('change', refreshOut);
  refreshOut();

  document.getElementById('tradeForm').addEventListener('submit', e => {
    e.preventDefault();

    const manager = managerSelect.value;
    const outPlayer = outSelect.value;
    const inPlayer = document.getElementById('tradeIn').value.trim();
    const note = document.getElementById('tradeNote').value.trim();
    const counted = !document.getElementById('tradeFree').checked;

    if (!inPlayer) return;

    const title = `[TRADE] ${manager}: ${outPlayer} -> ${inPlayer}`;
    const body = [
      `MANAGER=${manager}`,
      `OUT=${outPlayer}`,
      `IN=${inPlayer}`,
      `COUNT_TRADE=${counted ? 'true' : 'false'}`,
      `NOTE=${note || '-'}`,
      '',
      'Vytvorené cez NHL Strelci 2026/27 dashboard.'
    ].join('\n');

    const url =
      'https://github.com/andrejdikos-droid/NHL-strelci-26-27/issues/new' +
      `?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;

    window.open(url, '_blank', 'noopener');
  });
}

function renderTrades() {
  const records = [...(trades?.records || [])].reverse();
  const target = document.getElementById('tradeHistory');
  if (!target) return;

  target.innerHTML = records.length
    ? records.map(t => `
      <div class="daily-item">
        <div>
          <strong>${esc(t.manager)} · ${esc(t.outPlayer)} → ${esc(t.inPlayer)}</strong>
          <div class="who">
            ${esc(t.date || '')}
            <span class="trade-type ${t.countedTrade ? '' : 'free'}">
              ${t.countedTrade ? `TREJD ${t.tradeNumber || ''}/4` : 'MIMO 4 TREJDOV'}
            </span>
          </div>
          ${t.note ? `<div class="who" style="margin-top:5px">${esc(t.note)}</div>` : ''}
        </div>
        <div class="daily-goals">${Number(t.bankedGoals || 0)} G</div>
        <div class="who">banked</div>
      </div>
    `).join('')
    : '<div class="empty">Zatiaľ nebol zaregistrovaný žiadny trejd.</div>';
}

function bindTabs() {
  document.querySelectorAll('.tab').forEach(b => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.view').forEach(x => x.classList.remove('active'));

      b.classList.add('active');
      document.getElementById(b.dataset.view).classList.add('active');

      if (b.dataset.view === 'history') {
        setTimeout(renderHistory, 20);
      }
    });
  });
}

(async () => {
  try {
    [league, stats, history, trades] = await Promise.all([
      loadJSON('data/league.json'),
      loadJSON('data/stats.json'),
      loadJSON('data/history.json'),
      loadJSON('data/trades.json')
    ]);

    computed = calculate();
    ensureTradeUI();
    renderTop();
    renderLeaderboard();
    renderTeams();
    renderDaily();
    renderTrades();
    bindTabs();

    window.addEventListener('resize', () => {
      if (document.getElementById('history').classList.contains('active')) {
        renderHistory();
      }
    });
  } catch (e) {
    console.error(e);
    $('#updatedAt').textContent = 'chyba dát';

    document.querySelector('main').insertAdjacentHTML(
      'afterbegin',
      `<div class="panel" style="border-color:#7d2e38;margin-bottom:15px">
        Nepodarilo sa načítať dáta aplikácie. Skontroluj súbory v priečinku data.
      </div>`
    );
  }
})();
