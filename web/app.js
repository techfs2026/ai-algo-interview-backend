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
  python:     `class Solution:\n    def solve(self):\n        pass\n`,
  javascript: `/**\n * @return {any}\n */\nvar solve = function() {\n    \n};\n`,
};

const SKEL = `
  <div class="skel"></div><div class="skel sm"></div>
  <div class="skel"></div><div class="skel sm"></div>`;

// ── 状态 ─────────────────────────────────────────────
const S = {
  userId:    null,
  username:  null,
  sessionId: null,
  question:  null,
  swapLeft:  2,       // 本地记录剩余换题次数
  timeLeft:  0,
  timerId:   null,
  editor:    null,
  ratings:   Object.fromEntries(TAGS.map(t => [t, 3])),
};

// ── 工具 ─────────────────────────────────────────────
const $ = id => document.getElementById(id);

function show(name) {
  document.querySelectorAll('.pg').forEach(p =>
    p.classList.toggle('on', p.id === `pg-${name}`)
  );
  // 只有登录后才显示用户栏，面试页不显示（按钮已在 nav-center）
  const bar = $('user-bar');
  if (bar) bar.style.display = (S.userId && name !== 'interview') ? 'flex' : 'none';
  window.scrollTo(0, 0);
}

function toast(msg, ms = 3000) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove('show'), ms);
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

// ── 持久化 ───────────────────────────────────────────
const store = {
  save:  (uid, uname) => {
    localStorage.setItem('uid', uid);
    localStorage.setItem('uname', uname);
  },
  load:  () => ({
    uid:   localStorage.getItem('uid'),
    uname: localStorage.getItem('uname'),
  }),
  clear: () => {
    localStorage.removeItem('uid');
    localStorage.removeItem('uname');
  },
};

// ── 计时器 ───────────────────────────────────────────
function startTimer(secs) {
  S.timeLeft = secs;
  clearInterval(S.timerId);
  _tick();
  S.timerId = setInterval(_tick, 1000);
}

function _tick() {
  S.timeLeft = Math.max(0, S.timeLeft - 1);
  const m  = String(Math.floor(S.timeLeft / 60)).padStart(2, '0');
  const s  = String(S.timeLeft % 60).padStart(2, '0');
  const el = $('timer');
  el.textContent = `${m}:${s}`;
  el.className   = 'timer'
    + (S.timeLeft < 120 ? ' danger' : S.timeLeft < 300 ? ' warn' : '');
  if (S.timeLeft === 0) {
    clearInterval(S.timerId);
    App.submit();
  }
}

// ── 换题按钮状态 ──────────────────────────────────────
function updateSwapBtn(remaining) {
  S.swapLeft = remaining;
  const btn  = $('btn-swap');
  btn.textContent = remaining > 0 ? `换题 (${remaining})` : '换题 (0)';
  btn.disabled    = remaining <= 0;
  btn.style.opacity = remaining <= 0 ? '0.4' : '';
}

// ── 问卷渲染 ─────────────────────────────────────────
function renderQuiz() {
  $('skills-grid').innerHTML = '';
  TAGS.forEach(tag => {
    const row = document.createElement('div');
    row.className = 'skill-row';
    row.innerHTML = `
      <span class="skill-name">${tag}</span>
      <div class="skill-btns" id="rb-${tag}">
        ${[1,2,3,4,5].map(n =>
          `<button class="rb${n === 3 ? ' on' : ''}"
                   onclick="App.rate('${tag}',${n})">${n}</button>`
        ).join('')}
      </div>`;
    $('skills-grid').appendChild(row);
  });
}

// ── 编辑器 ───────────────────────────────────────────
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

// ── 题目面板重置（换题/新题前必须调用）──────────────
function resetQuestionPanel() {
  $('q-title').textContent  = '加载中…';
  $('q-tags').innerHTML     = '';
  $('q-reason').textContent = '';
  $('q-content').innerHTML  = SKEL;
}

// ── 还原旧题目（换题失败时调用）─────────────────────
function restoreQuestion() {
  if (!S.question) return;
  renderQuestion(S.question, null);
  loadContent(S.sessionId);
}

function renderQuestion(q, reason) {
  $('q-title').textContent  = q.title;
  $('q-reason').textContent = reason ? `💡 ${reason}` : '';
  const tag = $('diff-tag');
  tag.textContent = q.difficulty.toUpperCase();
  tag.className   = `diff-tag ${q.difficulty}`;
  $('q-tags').innerHTML = (q.tags || [])
    .map(t => `<span class="tag">${t}</span>`).join('');
  $('q-content').innerHTML = SKEL;
}

// ── 题目内容加载 ─────────────────────────────────────
async function loadContent(sessionId) {
  try {
    const c = await req('GET', `/interview/session/${sessionId}/content`);
    $('q-content').innerHTML = c.content || '<p>暂无描述</p>';
    const lang    = $('lang').value;
    const snippet = c.code_snippets?.find(s => s.langSlug === lang);
    if (snippet && S.editor) S.editor.setValue(snippet.code);
  } catch {
    $('q-content').innerHTML =
      '<p style="color:var(--ink-4)">题目内容加载失败，请刷新</p>';
  }
}

// ── Markdown + LaTeX 渲染 ─────────────────────────────
function md(text) {
  const blocks = [];
  text = text
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, m) => {
      blocks.push({ display: true, src: m });
      return `%%B${blocks.length - 1}%%`;
    })
    .replace(/\$([^\$\n]+?)\$/g, (_, m) => {
      blocks.push({ display: false, src: m });
      return `%%I${blocks.length - 1}%%`;
    });

  let html = text
    .replace(/^## (.+)$/gm,    '<h2>$1</h2>')
    .replace(/^### (.+)$/gm,   '<h3>$1</h3>')
    .replace(/^\d+\.\s(.+)$/gm,'<li>$1</li>')
    .replace(/^[-*]\s(.+)$/gm, '<li>$1</li>')
    .replace(/`([^`\n]+)`/g,   '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n\n/g,          '</p><p>')
    .replace(/\n(?!<)/g,       '<br>');

  if (!html.startsWith('<')) html = '<p>' + html;
  if (!html.endsWith('>'))   html += '</p>';

  html = html.replace(/%%([BI])(\d+)%%/g, (_, type, idx) => {
    const b = blocks[+idx];
    if (!b) return '';
    try {
      return katex.renderToString(b.src, {
        displayMode: type === 'B',
        throwOnError: false,
      });
    } catch {
      return type === 'B' ? `$$${b.src}$$` : `$${b.src}$`;
    }
  });

  return html;
}

// ── 流式分析：逐字符打出效果 ─────────────────────────
//
// 原理：
// SSE 推送的是按语义边界分批的 chunk（一句话/一段）
// 前端收到 chunk 后，用队列逐字符打出，速度约 30ms/字
// 这样就能实现"流水"效果，而不是一块一块出现
//
function streamAnalysis(sessionId) {
  const body = $('analysis-body');

  // 打字状态
  let rawBuf   = '';    // 已收到的全部原始文本（累积）
  let printBuf = '';    // 等待打出的字符队列
  let printing = false;
  let started  = false;
  let done     = false;

  const CHAR_INTERVAL = 18;  // ms/字符，越小越快

  // 光标
  const cursor = document.createElement('span');
  cursor.className = 'type-cursor';

  // 找到 body 内最后一个文本节点（递归，从右往左）
  function lastTextNode(node) {
    for (let i = node.childNodes.length - 1; i >= 0; i--) {
      const c = node.childNodes[i];
      if (c.nodeType === Node.TEXT_NODE && c.textContent.trim()) return c;
      const found = lastTextNode(c);
      if (found) return found;
    }
    return null;
  }

  // 把光标插到最后一个文本节点的正后方（行内，不换行）
  function placeCursor() {
    const t = lastTextNode(body);
    if (t && t.parentNode) {
      t.parentNode.insertBefore(cursor, t.nextSibling);
    } else {
      body.appendChild(cursor);
    }
  }

  // 逐字符打出函数
  function typeNext() {
    if (printBuf.length === 0) {
      printing = false;
      if (done) {
        cursor.remove();
        App.complete(sessionId);
      }
      return;
    }

    printing = true;
    const ch  = printBuf[0];
    printBuf  = printBuf.slice(1);
    rawBuf   += ch;

    const tmp = document.createElement('div');
    tmp.innerHTML = md(rawBuf);
    body.innerHTML = '';
    while (tmp.firstChild) body.appendChild(tmp.firstChild);
    placeCursor();

    const pause = '。！？,.!?'.includes(ch) ? CHAR_INTERVAL * 4 : CHAR_INTERVAL;
    setTimeout(typeNext, pause);
  }

  // SSE
  const es = new EventSource(`${API}/analysis/stream/${sessionId}`);

  es.onmessage = ({ data }) => {
    const d = JSON.parse(data);

    if (d.type === 'chunk') {
      if (!started) {
        body.innerHTML = '';
        body.appendChild(cursor);
        started = true;
      }
      // 把新 chunk 加入打字队列
      printBuf += d.content;
      if (!printing) typeNext();
    }

    if (d.type === 'done') {
      es.close();
      done = true;
      // 如果打字队列已空，立刻完成；否则等打完再触发
      if (!printing && printBuf.length === 0) {
        cursor.remove();
        App.complete(sessionId);
      }
    }

    if (d.type === 'error') {
      es.close();
      cursor.remove();
      printBuf = '';
      printing = false;
      if (!started)
        body.innerHTML = `<p style="color:var(--red)">${d.message}</p>`;
    }
  };

  es.onerror = () => {
    es.close();
    cursor.remove();
    printBuf = '';
    printing = false;
    if (!started)
      body.innerHTML = '<p style="color:var(--ink-4)">分析服务暂时不可用</p>';
  };
}

// ── 推荐题单 ─────────────────────────────────────────
function renderRecs(recs) {
  if (!recs?.length) return;
  const label = { related: '相关练习', weakness: '薄弱点', new: '新知识点' };
  $('rec-grid').innerHTML = recs.map(r => `
    <div class="rec-item"
         onclick="App.startWithQuestion(${r.id},'${r.title_slug}')">
      <div class="rec-type">${label[r.recommend_type] || '推荐'}</div>
      <div class="rec-title">${r.title}</div>
      <div class="rec-why">${r.reason}</div>
      <div class="rec-footer">
        <span class="diff-pill ${r.difficulty}">${r.difficulty.toUpperCase()}</span>
        <span class="rec-action">直接练习 →</span>
      </div>
    </div>`).join('');
  $('r-recs').style.display = 'block';
}

// ── 用户栏 ───────────────────────────────────────────
function _updateUserBar(username, profile) {
  // 全局用户栏（非面试页）
  const bar = $('user-bar');
  if (!username) {
    if (bar) bar.style.display = 'none';
    return;
  }
  if (bar) {
    bar.style.display = 'flex';
    $('user-chip-name').textContent = username;
    $('user-chip-stats').textContent = profile
      ? `${profile.total_questions} 题` : '';
  }
  // 面试页内嵌用户按钮
  const ib = $('interview-user-btn');
  if (ib) ib.textContent = username;
}

// ── App ───────────────────────────────────────────────
const App = {

  async init() {
    const { uid, uname } = store.load();
    if (uid) {
      try {
        const p    = await req('GET', `/users/${uid}`);
        S.userId   = uid;
        S.username = uname;
        S.swapLeft = p.swap_remaining ?? 2;
        _updateUserBar(uname, p);
        if (!p.calibration_done) { renderQuiz(); show('quiz'); }
        else this._interview();
        return;
      } catch { store.clear(); }
    }

    try {
      const u    = await req('POST', '/users/');
      S.userId   = u.user_id;
      S.username = u.username;
      store.save(u.user_id, u.username);
      _updateUserBar(u.username, null);
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
    resetQuestionPanel();
    initEditor($('lang')?.value || 'python');

    try {
      const r     = await req('POST', `/interview/${S.userId}/start`, {});
      S.sessionId = r.session_id;
      S.question  = r.question;

      renderQuestion(r.question, r.select_reason);
      updateSwapBtn(r.question.swap_remaining ?? S.swapLeft);
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
    if (S.swapLeft <= 0) {
      toast('今日换题次数已用完');
      return;
    }

    const _input = prompt(
      '换题原因（取消=不换题，直接确定=随便换）\n① 太难了  ② 太简单了  ③ 做太多了  ④ 随便换'
    );
    if (_input === null) return;   // 用户点了取消
    const reason = _input.trim() || '就是想换';

    // 先备份当前题目，失败时恢复
    const prevSession  = S.sessionId;
    const prevQuestion = S.question;

    resetQuestionPanel();

    try {
      const r     = await req('POST', `/interview/${S.userId}/swap`, {
        session_id: S.sessionId,
        reason,
      });
      S.sessionId = r.session_id;
      S.question  = r.question;

      renderQuestion(r.question, r.select_reason);
      updateSwapBtn(r.swap_remaining);
      initEditor($('lang').value);
      startTimer(r.question.time_limit || 1800);
      loadContent(r.session_id);
      toast('已换题');
    } catch (e) {
      // 失败：恢复旧题目
      S.sessionId = prevSession;
      S.question  = prevQuestion;
      if (prevQuestion) {
        renderQuestion(prevQuestion, null);
        loadContent(prevSession);
      }
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
    $('r-verdict').innerHTML    = '<span class="verdict-text">判题中…</span>';
    $('r-recs').style.display   = 'none';
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
      const m      = Math.floor(used / 60);
      const s      = used % 60;

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
            <span class="vs-val">${m}m ${s}s</span>
            <span class="vs-label">用时</span>
          </div>
          <div class="vs">
            <span class="vs-val">${jr.status}</span>
            <span class="vs-label">状态</span>
          </div>
        </div>`;

      streamAnalysis(S.sessionId);

    } catch (e) {
      $('r-verdict').innerHTML =
        `<span class="verdict-text fail">提交失败</span>`;
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

  async startWithQuestion(questionId, titleSlug) {
    clearInterval(S.timerId);
    show('interview');
    resetQuestionPanel();
    initEditor($('lang')?.value || 'python');
    toast('正在加载题目…');

    try {
      const r     = await req('POST', `/interview/${S.userId}/start`,
                              { preferred_question_id: questionId });
      S.sessionId = r.session_id;
      S.question  = r.question;

      renderQuestion(r.question, r.select_reason);
      updateSwapBtn(r.question.swap_remaining ?? S.swapLeft);
      startTimer(r.question.time_limit || 1800);
      loadContent(r.session_id);
    } catch (e) {
      toast(`加载失败：${e.message}`);
    }
  },

  again() {
    clearInterval(S.timerId);
    S.sessionId = null;
    this._interview();
  },

  async showStats() {
    if (!S.userId) return;
    $('stats-modal').style.display = 'flex';
    $('modal-username').textContent = S.username;
    $('modal-stats').innerHTML = '<div style="padding:20px;color:var(--ink-4);font-size:13px">加载中…</div>';
    $('modal-skills').innerHTML = '';

    try {
      // 调后端统计接口，数据来自数据库而不是前端状态
      const stats = await req('GET', `/users/${S.userId}/stats`);

      // 顶部统计数字
      const passRatePct = Math.round((stats.pass_rate || 0) * 100);
      const avgMins     = Math.floor((stats.avg_time_secs || 0) / 60);
      const avgSecs     = (stats.avg_time_secs || 0) % 60;
      $('modal-stats').innerHTML = `
        <div class="mstat">
          <span class="mstat-val">${stats.total_questions}</span>
          <span class="mstat-label">总题数</span>
        </div>
        <div class="mstat">
          <span class="mstat-val">${stats.solved_count}</span>
          <span class="mstat-label">已解决</span>
        </div>
        <div class="mstat">
          <span class="mstat-val">${passRatePct}%</span>
          <span class="mstat-label">通过率</span>
        </div>
        <div class="mstat">
          <span class="mstat-val">${avgMins}m${avgSecs}s</span>
          <span class="mstat-label">平均用时</span>
        </div>`;

      // 技能画像进度条（数据来自后端排序）
      const skills    = stats.skills || [];
      const filtered  = skills.filter(s => s.confidence > 0.2);
      const barsHtml  = filtered.map(s => {
        const pct = Math.round(s.level * 100);
        const cls = s.level < 0.4 ? 'low' : s.level < 0.7 ? 'mid' : 'high';
        const cnt = s.question_count > 0 ? `<span class="skill-bar-count">${s.question_count}题</span>` : '';
        return `
          <div class="skill-bar-row">
            <span class="skill-bar-name">${s.tag}</span>
            <div class="skill-bar-track">
              <div class="skill-bar-fill ${cls}" style="width:${pct}%"></div>
            </div>
            <span class="skill-bar-level">${pct}%</span>
            ${cnt}
          </div>`;
      }).join('');

      $('modal-skills').innerHTML = `
        <div class="modal-skills-title">技能画像</div>
        ${barsHtml || '<p style="color:var(--ink-4);font-size:13px">完成更多题目后显示</p>'}`;

    } catch (e) {
      $('modal-stats').innerHTML = `<p style="color:var(--red);padding:16px">${e.message}</p>`;
      toast(`加载统计失败：${e.message}`);
    }
  },

  closeStats() {
    $('stats-modal').style.display = 'none';
  },

  // 清除当前用户，重新开始（新用户身份）
  clearUser() {
    if (!confirm('清除当前用户画像？所有进度将丢失，将以新用户身份重新开始。')) return;
    clearInterval(S.timerId);
    store.clear();
    S.userId    = null;
    S.username  = null;
    S.sessionId = null;
    S.question  = null;
    S.swapLeft  = 2;
    _updateUserBar(null, null);
    show('home');
    toast('已清除，以新用户身份重新开始');
  },
};

// ── 启动 ─────────────────────────────────────────────
show('home');