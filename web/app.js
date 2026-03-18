/**
 * AlgoInterview 前端逻辑
 * 单页应用 · 原生 JS · 无框架依赖
 */

const API = 'http://localhost:8000/api/v1';

const TAGS = [
  '数组','字符串','哈希表','链表','栈',
  '队列','二叉树','图','动态规划','回溯',
  '贪心','二分查找','双指针','滑动窗口','排序',
];

const TPL = {
  python: `class Solution:\n    def solve(self):\n        pass\n`,
  javascript: `/**\n * @return {any}\n */\nvar solve = function() {\n    \n};\n`,
};

// ── 状态 ───────────────────────────────────────────────
const S = {
  userId:    null,
  username:  null,
  sessionId: null,
  question:  null,
  swapLeft:  2,
  timeLeft:  0,
  timerId:   null,
  editor:    null,
  ratings:   Object.fromEntries(TAGS.map(t => [t, 3])),
};

// ── 工具 ───────────────────────────────────────────────
const $  = id => document.getElementById(id);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls)  e.className   = cls;
  if (html) e.innerHTML   = html;
  return e;
};

function show(name) {
  document.querySelectorAll('.pg').forEach(p => {
    p.classList.toggle('on', p.id === `pg-${name}`);
  });
  window.scrollTo(0, 0);
}

function toast(msg, ms = 3200) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.classList.remove('show'), ms);
}

async function req(method, path, body) {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// ── 持久化 ─────────────────────────────────────────────
const store = {
  save: (uid, uname) => { localStorage.setItem('uid', uid); localStorage.setItem('uname', uname); },
  load: () => ({ uid: localStorage.getItem('uid'), uname: localStorage.getItem('uname') }),
  clear: () => { localStorage.removeItem('uid'); localStorage.removeItem('uname'); },
};

// ── 计时器 ─────────────────────────────────────────────
function startTimer(secs) {
  S.timeLeft = secs;
  clearInterval(S.timerId);
  tick();
  S.timerId = setInterval(tick, 1000);
}

function tick() {
  S.timeLeft = Math.max(0, S.timeLeft - 1);
  const m = String(Math.floor(S.timeLeft / 60)).padStart(2, '0');
  const s = String(S.timeLeft % 60).padStart(2, '0');
  const el = $('timer');
  el.textContent = `${m}:${s}`;
  el.className = 'timer' + (S.timeLeft < 120 ? ' danger' : S.timeLeft < 300 ? ' warn' : '');
  if (S.timeLeft === 0) { clearInterval(S.timerId); App.submit(); }
}

// ── 问卷渲染 ───────────────────────────────────────────
function renderQuiz() {
  const grid = $('skills-grid');
  grid.innerHTML = '';
  TAGS.forEach(tag => {
    const row = el('div', 'skill-row');
    row.innerHTML = `<span class="skill-name">${tag}</span><div class="skill-btns" id="rb-${tag}">
      ${[1,2,3,4,5].map(n =>
        `<button class="rb${n === 3 ? ' on' : ''}" onclick="App.rate('${tag}',${n})">${n}</button>`
      ).join('')}
    </div>`;
    grid.appendChild(row);
  });
}

// ── 编辑器 ─────────────────────────────────────────────
function initEditor(lang = 'python') {
  if (S.editor) { S.editor.toTextArea(); S.editor = null; }
  $('editor').innerHTML = '<textarea id="_cm"></textarea>';
  S.editor = CodeMirror.fromTextArea($('_cm'), {
    mode:           lang === 'javascript' ? 'javascript' : 'python',
    theme:          'tomorrow-night-eighties',
    lineNumbers:    true,
    indentUnit:     4,
    tabSize:        4,
    indentWithTabs: false,
    autofocus:      true,
    extraKeys:      { Tab: cm => cm.execCommand('indentMore') },
  });
  S.editor.setValue(TPL[lang]);
}

// ── Markdown 轻量渲染 ──────────────────────────────────
function md(text) {
  return text
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>')
    .replace(/^(?!<[h2h3p])/, '<p>')
    + (text.endsWith('\n') ? '' : '</p>');
}

// ── 流式分析 ───────────────────────────────────────────
function streamAnalysis(sessionId) {
  const body   = $('analysis-body');
  let started  = false;
  let buf      = '';

  const es = new EventSource(`${API}/analysis/stream/${sessionId}`);

  es.onmessage = ({ data }) => {
    const d = JSON.parse(data);

    if (d.type === 'chunk') {
      if (!started) { body.innerHTML = ''; started = true; }
      buf += d.content;
      body.innerHTML = md(buf);
    }

    if (d.type === 'done') {
      es.close();
      App.complete(sessionId);
    }

    if (d.type === 'error') {
      es.close();
      if (!started) body.innerHTML = `<p style="color:var(--red)">${d.message}</p>`;
    }
  };

  es.onerror = () => {
    es.close();
    if (!started) body.innerHTML = '<p style="color:var(--ink-4)">分析服务暂时不可用</p>';
  };
}

// ── 推荐题单 ───────────────────────────────────────────
function renderRecs(recs) {
  if (!recs?.length) return;
  const typeLabel = { related: '相关练习', weakness: '薄弱点', new: '新知识点' };
  $('rec-grid').innerHTML = recs.map(r => `
    <div class="rec-item"
         onclick="window.open('https://leetcode.cn/problems/${r.title_slug}/','_blank')">
      <div class="rec-type">${typeLabel[r.recommend_type] || '推荐'}</div>
      <div class="rec-title">${r.title}</div>
      <div class="rec-why">${r.reason}</div>
      <span class="diff-pill ${r.difficulty}">${r.difficulty.toUpperCase()}</span>
    </div>`).join('');
  $('r-recs').style.display = 'block';
}

// ── 题目内容渲染 ───────────────────────────────────────
async function loadContent(sessionId) {
  try {
    const c = await req('GET', `/interview/session/${sessionId}/content`);
    $('q-content').innerHTML = c.content || '<p>暂无描述</p>';
    // 更新代码模板
    const lang    = $('lang').value;
    const snippet = c.code_snippets?.find(s => s.langSlug === lang);
    if (snippet && S.editor) S.editor.setValue(snippet.code);
  } catch {
    $('q-content').innerHTML = '<p style="color:var(--ink-4)">题目内容加载失败，请刷新</p>';
  }
}

// ── 渲染题目基本信息 ───────────────────────────────────
function renderQuestion(q, reason) {
  $('q-title').textContent = q.title;
  $('q-reason').textContent = reason ? `💡 ${reason}` : '';
  const tag = $('diff-tag');
  tag.textContent = q.difficulty.toUpperCase();
  tag.className   = `diff-tag ${q.difficulty}`;
  $('q-tags').innerHTML = (q.tags || [])
    .map(t => `<span class="tag">${t}</span>`).join('');
}

// ── App 主逻辑 ─────────────────────────────────────────
const App = {

  async init() {
    const { uid, uname } = store.load();

    if (uid) {
      try {
        const p = await req('GET', `/users/${uid}`);
        S.userId   = uid;
        S.username = uname;
        $('nav-user').textContent = uname;
        if (!p.calibration_done) { renderQuiz(); show('quiz'); }
        else this._interview();
        return;
      } catch {
        store.clear();
      }
    }

    try {
      const u = await req('POST', '/users/');
      S.userId   = u.user_id;
      S.username = u.username;
      store.save(u.user_id, u.username);
      $('nav-user').textContent = u.username;
      renderQuiz();
      show('quiz');
    } catch (e) {
      toast(`初始化失败：${e.message}`);
    }
  },

  rate(tag, n) {
    S.ratings[tag] = n;
    $(`rb-${tag}`).querySelectorAll('.rb').forEach((b, i) =>
      b.classList.toggle('on', i + 1 === n)
    );
  },

  async submitQuiz() {
    try {
      await req('POST', `/users/${S.userId}/questionnaire`, {
        items: TAGS.map(tag => ({ tag, rating: S.ratings[tag] })),
      });
      toast('评估完成，正在选题…');
      this._interview();
    } catch (e) {
      toast(`提交失败：${e.message}`);
    }
  },

  async _interview() {
    show('interview');
    // 重置题目面板
    $('q-content').innerHTML = `
      <div class="skel"></div><div class="skel sm"></div>
      <div class="skel"></div><div class="skel sm"></div>`;
    initEditor('python');

    try {
      const r     = await req('POST', `/interview/${S.userId}/start`, {});
      S.sessionId = r.session_id;
      S.question  = r.question;

      renderQuestion(r.question, r.select_reason);
      $('btn-swap').textContent = `换题 (${r.question.swap_remaining ?? 2})`;

      startTimer(r.question.time_limit || 1800);
      loadContent(r.session_id);
    } catch (e) {
      toast(`选题失败：${e.message}`);
    }
  },

  changeLang(lang) {
    initEditor(lang);
    if (S.sessionId) loadContent(S.sessionId);
  },

  async swap() {
    const reason = prompt(
      '换题原因（直接按确定跳过）\n① 太难了  ② 太简单了  ③ 做太多了  ④ 随便换'
    ) || '就是想换';

    try {
      const r = await req('POST', `/interview/${S.userId}/swap`, {
        session_id: S.sessionId,
        reason,
      });
      S.question = r.question;
      renderQuestion(r.question, r.select_reason);
      $('btn-swap').textContent = `换题 (${r.swap_remaining})`;
      // 换题后重新加载内容（注意 session 仍是旧的，后端需返回新 session_id）
      $('q-content').innerHTML = '<div class="skel"></div><div class="skel sm"></div>';
      toast('已换题');
    } catch (e) {
      toast(e.message);
    }
  },

  async submit() {
    clearInterval(S.timerId);

    const code = S.editor?.getValue() || '';
    const lang = $('lang').value;
    const used = (S.question?.time_limit || 1800) - S.timeLeft;

    if (!code.trim() || code.trim() === TPL[lang].trim()) {
      toast('请先写入你的解法');
      return;
    }

    show('result');
    $('r-verdict').innerHTML = '<span class="verdict-text">判题中…</span>';
    $('r-recs').style.display = 'none';
    $('analysis-body').innerHTML = `
      <div class="thinking">
        <span class="dot-pulse"></span>
        <span class="dot-pulse d2"></span>
        <span class="dot-pulse d3"></span>
        <span>AI 正在分析…</span>
      </div>`;

    try {
      const res = await req('POST', '/analysis/submit', {
        session_id: S.sessionId,
        code,
        language: lang,
        time_used: used,
      });

      const jr     = res.judge_result;
      const passed = jr.passed === jr.total && jr.status === 'Accepted';
      const mins   = Math.floor(used / 60);
      const secs   = used % 60;

      $('r-verdict').innerHTML = `
        <span class="verdict-text ${passed ? 'pass' : 'fail'}">
          ${passed ? '通过 ✓' : '未通过'}
        </span>
        <div class="verdict-stats">
          <div class="vs">
            <span class="vs-val">${jr.passed}/${jr.total}</span>
            <span class="vs-label">测试用例</span>
          </div>
          <div class="vs">
            <span class="vs-val">${mins}m ${secs}s</span>
            <span class="vs-label">用时</span>
          </div>
          <div class="vs">
            <span class="vs-val">${jr.status}</span>
            <span class="vs-label">状态</span>
          </div>
        </div>`;

      streamAnalysis(S.sessionId);

    } catch (e) {
      $('r-verdict').innerHTML = `<span class="verdict-text fail">提交失败</span>`;
      toast(`错误：${e.message}`);
    }
  },

  async complete(sessionId) {
    try {
      const r = await req('POST', `/analysis/complete/${sessionId}`);
      renderRecs(r.recommendations);
    } catch (e) {
      console.warn('推荐题单:', e.message);
    }
  },

  again() {
    clearInterval(S.timerId);
    S.sessionId = null;
    this._interview();
  },
};

// ── 启动 ───────────────────────────────────────────────
show('home');