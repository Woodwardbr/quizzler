/* Quizzler guide engine.
 *
 * Turns a static guide document (see docs/onboarding/README.md for the
 * contract) into an interactive page: progressive modules, confidence-rated
 * questions, exercises, a source pane with literal anchors, and, in review
 * mode, a Changes view, finding responses, and a final review summary.
 * No dependencies and no build step. All guide text is trusted local HTML;
 * everything fetched from the server is inserted as text.
 */
(() => {
  'use strict';

  const guideRoot = document.querySelector('main[data-guide]');
  if (!guideRoot) return;

  const scriptUrl = document.currentScript ? document.currentScript.src : '';
  const appBase = new URL('.', scriptUrl || location.href);
  const hasServer = /^https?:$/.test(location.protocol);
  const apiBase = new URL('../api/', appBase);
  const params = new URLSearchParams(location.search);

  const meta = {
    mode: guideRoot.dataset.mode === 'review' ? 'review' : 'onboard',
    id: guideRoot.dataset.guideId || 'guide',
    version: Number(guideRoot.dataset.version || 1),
    repo: guideRoot.dataset.repo || '',
    pr: guideRoot.dataset.pr || '',
    head: guideRoot.dataset.headSha || '',
  };
  const isReview = meta.mode === 'review';
  const PREFIX = isReview ? 'QUIZZLER-REVIEW' : 'QUIZZLER-ONBOARD';
  const CONFIDENCE = [['sure', 'Sure'], ['unsure', 'Unsure'], ['guess', 'Guessing']];
  const RESPONSES = [['agree', 'Agree'], ['unsure', 'Unsure'], ['disagree', 'Disagree']];
  const STATES = [['ready', 'Ready'], ['needs-another-look', 'Needs another look'], ['not-posting', 'Not posting']];
  const CLASS_LABEL = {
    mastered: 'Mastered: right and sure',
    fragile: 'Fragile: right, but not sure',
    misconception: 'Misconception: wrong but sure',
    gap: 'Gap: wrong and not sure',
  };

  const state = {
    kind: meta.mode, guide: meta.id, version: meta.version, repo: meta.repo,
    pr: meta.pr, head: meta.head,
    answers: {}, exercises: {}, findings: {}, comments: [], revealed: {}, plan: {}, refresh: {}, prefs: {}, current: 0,
  };
  let serverMeta = null;

  // ------------------------------------------------------------------ utils
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  let uid = 0;
  const nextId = (prefix) => `${prefix}-${++uid}`;

  async function api(path, query, body) {
    if (!hasServer) throw new Error('This needs the local guide server (serve.py).');
    const url = new URL(path, apiBase);
    for (const [key, value] of Object.entries(query || {})) {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
    }
    const init = body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    };
    const response = await fetch(url, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  const normalize = (text) => String(text || '').replace(/\r\n?/g, '\n')
    .split('\n').map((line) => line.trimEnd()).join('\n').replace(/\n+$/, '');

  function radioGroup(name, legend, options, current, onChange) {
    const fieldset = el('fieldset', { class: 'qz-radios' }, el('legend', { text: legend }));
    for (const [value, label] of options) {
      const input = el('input', { type: 'radio', name, value });
      input.checked = current === value;
      if (onChange) input.addEventListener('change', () => onChange(value));
      fieldset.append(el('label', {}, input, ' ', label));
    }
    return fieldset;
  }

  function selectedValue(container, name) {
    const checked = container.querySelector(`input[name="${CSS.escape(name)}"]:checked`);
    return checked ? checked.value : '';
  }

  // ---------------------------------------------------------- persistence
  let saveTimer = null;
  const saveStatus = el('span', { class: 'qz-save', role: 'status', 'aria-live': 'polite' });

  function saveNow() {
    if (!hasServer) return Promise.resolve();
    clearTimeout(saveTimer);
    return api('results', null, { results: resultsPayload() }).catch(() => {});
  }

  function scheduleSave() {
    if (!hasServer) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        await api('results', null, { results: resultsPayload() });
        saveStatus.textContent = 'Saved locally';
      } catch (error) {
        saveStatus.textContent = `Not saved: ${error.message}`;
      }
    }, 400);
  }

  function resultsPayload() {
    const payload = JSON.parse(JSON.stringify(state));
    // Report the effective plan (recommendation unless the reader changed it).
    if (overview) {
      payload.plan = Object.fromEntries(studyModules().map((s) => [s.dataset.module, intensityOf(s.dataset.module)]));
      payload.pending = pendingModules();
      payload.round = round;
      if (Object.keys(refreshRecommended).length) {
        payload.refresh = Object.fromEntries(Object.keys(refreshRecommended).map((id) => [id, refreshOf(id)]));
      }
    }
    return payload;
  }

  async function loadSaved() {
    if (!hasServer) return;
    try {
      const data = await api('results');
      const saved = data.results;
      if (saved && saved.guide === meta.id) {
        for (const key of ['answers', 'exercises', 'findings', 'revealed', 'plan', 'refresh', 'prefs']) {
          if (saved[key] && typeof saved[key] === 'object') state[key] = saved[key];
        }
        if (Array.isArray(saved.comments)) state.comments = saved.comments;
        // Answers recorded against an older version keep their grading; the
        // guide marks those questions retired or rewords them.
        if (Number.isInteger(saved.current)) state.current = saved.current;
      }
    } catch (error) {
      saveStatus.textContent = `Could not load saved progress: ${error.message}`;
    }
  }

  // ---------------------------------------------------------------- theme
  // Light/dark switch. Starts from the system setting; an explicit choice is
  // saved with the guide's progress (the port changes every run, so browser
  // storage alone would forget it) and mirrored to localStorage for the
  // first paint.
  const systemDark = () => window.matchMedia('(prefers-color-scheme: dark)').matches;
  const currentTheme = () => document.documentElement.dataset.theme || (systemDark() ? 'dark' : 'light');
  const themeButton = el('button', { class: 'qz-btn qz-theme-toggle', type: 'button' });

  function applyTheme(theme) {
    if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    const dark = currentTheme() === 'dark';
    themeButton.replaceChildren(
      el('span', { class: 'qz-theme-icon', 'aria-hidden': 'true', text: dark ? '☀' : '☾' }),
      el('span', { class: 'qz-theme-label', text: dark ? 'Light mode' : 'Dark mode' }));
    themeButton.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
    themeButton.setAttribute('aria-pressed', String(!dark));
  }

  themeButton.addEventListener('click', () => {
    const next = currentTheme() === 'dark' ? 'light' : 'dark';
    state.prefs.theme = next;
    try { localStorage.setItem('quizzler-theme', next); } catch { /* storage unavailable */ }
    applyTheme(next);
    scheduleSave();
  });
  try { applyTheme(localStorage.getItem('quizzler-theme')); } catch { applyTheme(null); }
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => applyTheme(document.documentElement.dataset.theme));

  // ---------------------------------------------------------------- shell
  const modules = [...guideRoot.querySelectorAll('section.module[data-module]')];
  const shell = el('div', { class: 'qz-shell' });
  const topbar = el('header', { class: 'qz-top' });
  const side = el('nav', { class: 'qz-side', id: 'qz-side', 'aria-label': 'Guide contents' });
  const mainArea = el('div', { class: 'qz-main' });
  const codePane = el('aside', { class: 'qz-code', id: 'qz-code', 'aria-label': 'Source', hidden: true, tabindex: '-1' });
  const backdrop = el('div', { class: 'qz-backdrop', hidden: true });
  const views = {};

  function buildShell() {
    const title = guideRoot.querySelector('h1');
    const sideToggle = el('button', {
      class: 'qz-btn qz-side-toggle', type: 'button', 'aria-controls': 'qz-side', 'aria-expanded': 'false',
      onclick: () => {
        const open = !shell.classList.contains('qz-side-open');
        shell.classList.toggle('qz-side-open', open);
        sideToggle.setAttribute('aria-expanded', String(open));
      },
    }, 'Contents');
    const tabs = el('div', { class: 'qz-tabs', role: 'tablist', 'aria-label': 'Views' });
    const tabNames = [['guide', 'Guide']];
    if (isReview) tabNames.push(['changes', 'Changes'], ['review', 'Review']);
    for (const [key, label] of tabNames) {
      const tab = el('button', {
        class: 'qz-tab', type: 'button', role: 'tab', id: `qz-tab-${key}`,
        'aria-controls': `qz-view-${key}`, 'aria-selected': key === 'guide' ? 'true' : 'false',
        onclick: () => showView(key),
      }, label);
      tabs.append(tab);
    }
    tabs.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      const all = [...tabs.querySelectorAll('[role=tab]')];
      const index = all.indexOf(document.activeElement);
      const next = all[(index + (event.key === 'ArrowRight' ? 1 : all.length - 1)) % all.length];
      next.focus();
      next.click();
    });
    const badge = el('span', { class: `qz-badge qz-badge-${meta.mode}` },
      isReview ? `Review${meta.pr ? ` · PR #${meta.pr}` : ''}` : 'Onboarding', ` · v${meta.version}`);
    topbar.append(
      sideToggle,
      el('span', { class: 'qz-title' }, title ? title.textContent : document.title),
      badge, tabs,
      el('span', { class: 'qz-top-actions' }, saveStatus,
        el('button', { class: 'qz-btn', type: 'button', onclick: () => openSource(null) }, 'Source'),
        el('button', { class: 'qz-btn qz-primary', type: 'button', onclick: openExport }, 'Export results'),
        themeButton),
    );

    views.guide = el('div', { class: 'qz-view', id: 'qz-view-guide', role: 'tabpanel', 'aria-labelledby': 'qz-tab-guide' });
    guideRoot.replaceWith(shell);
    views.guide.append(guideRoot);
    mainArea.append(views.guide);
    if (isReview) {
      views.changes = el('div', { class: 'qz-view', id: 'qz-view-changes', role: 'tabpanel', 'aria-labelledby': 'qz-tab-changes', hidden: true });
      views.review = el('div', { class: 'qz-view', id: 'qz-view-review', role: 'tabpanel', 'aria-labelledby': 'qz-tab-review', hidden: true });
      mainArea.append(views.changes, views.review);
    }
    shell.append(topbar, side, mainArea, codePane, backdrop);
    backdrop.addEventListener('click', closeSource);
  }

  function showView(key) {
    for (const [name, view] of Object.entries(views)) {
      view.hidden = name !== key;
      const tab = document.getElementById(`qz-tab-${name}`);
      if (tab) tab.setAttribute('aria-selected', String(name === key));
    }
    if (key === 'changes') renderChanges();
    if (key === 'review') renderReview();
  }

  // -------------------------------------------------------------- modules
  const moduleNav = el('ol', { class: 'qz-modules' });
  const pager = el('div', { class: 'qz-pager' });

  // ------------------------------------------------------- study plan
  // An overview module (data-overview) opens the guide with a plan table:
  // one <tr data-plan="module" data-importance data-complexity
  // data-intensity> per module. The reader picks an intensity per module:
  //   skip      the module drops out of navigation and progress
  //   skim      content marked data-depth="standard" or "deep" is hidden
  //   standard  content marked data-depth="deep" is hidden
  //   deep      everything is shown
  const overview = modules.find((s) => 'overview' in s.dataset) || null;
  const INTENSITY = [['skip', 'Skip'], ['skim', 'Skim'], ['standard', 'Standard'], ['deep', 'Deep']];
  const DEPTH_RANK = { skim: 0, standard: 1, deep: 2 };
  const recommended = {};
  // The end of an onboarding guide: a final review (authored by Claude once
  // requested; until then the engine shows a live scoreboard and a button to
  // request it) and, when the reader is done, a summary.
  const isFinal = (s) => s && s.dataset.final !== undefined;
  const isSummary = (s) => s && s.dataset.summary !== undefined;
  const isSpecial = (s) => s === overview || isFinal(s) || isSummary(s);
  if (overview && !isReview && !modules.some(isFinal) && !modules.some(isSummary)) {
    const stub = el('section', {
      class: 'module', 'data-module': 'final-review', 'data-final': '', 'data-stub': '', 'data-title': 'Final review',
    }, el('h2', { text: 'Final review' }));
    guideRoot.append(stub);
    modules.push(stub);
  }
  const round = Number(guideRoot.dataset.round || 1);
  const studyModules = () => modules.filter((s) => !isSpecial(s));
  const intensityOf = (id) => state.plan[id] || recommended[id] || 'standard';
  const isSkipped = (section) => !isSpecial(section) && intensityOf(section.dataset.module) === 'skip';
  // data-written: how far a module has been written (missing = fully written).
  // Modules are generated on demand: a skeleton first, then the depth chosen.
  const WRITTEN_RANK = { skeleton: -1, skim: 0, standard: 1, deep: 2 };
  const writtenOf = (section) => section.dataset.written || 'deep';
  const isSkeleton = (section) => !isSpecial(section) && writtenOf(section) === 'skeleton';
  // Modules whose chosen intensity is deeper than what has been written.
  function pendingModules() {
    const pending = {};
    if (!overview) return pending;
    for (const section of studyModules()) {
      const wanted = intensityOf(section.dataset.module);
      if (wanted === 'skip') continue;
      const written = writtenOf(section);
      if ((WRITTEN_RANK[written] ?? 2) < (WRITTEN_RANK[wanted] ?? 1)) pending[section.dataset.module] = { written, wanted };
    }
    return pending;
  }

  function buildPlan() {
    if (!overview) return;
    const table = overview.querySelector('[data-plan]')?.closest('table') || null;
    if (table) table.classList.add('qz-plan');
    const headRow = table && table.querySelector('thead tr');
    if (headRow) {
      // Module and description share one column so the plan stays compact
      // beside the docked code pane.
      headRow.querySelectorAll('th:not(:first-child)').forEach((th) => th.remove());
      headRow.append(el('th', { text: 'Importance' }), el('th', { text: 'Complexity' }), el('th', { text: 'Your intensity' }));
    }
    overview.querySelectorAll('[data-plan]').forEach((row) => {
      const id = row.dataset.plan;
      const section = modules.find((s) => s.dataset.module === id);
      recommended[id] = row.dataset.intensity || 'standard';
      const first = row.querySelector('td, th');
      if (first && section) {
        const link = el('a', { href: `#${section.id}`, onclick: (e) => { e.preventDefault(); goTo(modules.indexOf(section), true); } });
        while (first.firstChild) link.append(first.firstChild);
        first.append(link);
      }
      [...row.children].slice(1).forEach((cell) => {
        if (cell.textContent.trim()) first.append(el('div', { class: 'qz-plan-desc' }, ...cell.childNodes));
        cell.remove();
      });
      const select = el('select', { class: 'qz-intensity', 'aria-label': `Intensity for ${section ? section.dataset.title || id : id}` },
        INTENSITY.map(([value, label]) => el('option', { value }, label)));
      select.value = intensityOf(id);
      select.addEventListener('change', () => {
        state.plan[id] = select.value;
        applyPlan();
        scheduleSave();
      });
      const rating = (value) => el('td', {}, el('span', { class: `qz-rating qz-rating-${value || 'none'}` }, value || '–'));
      const recLabel = (INTENSITY.find(([v]) => v === recommended[id]) || ['', recommended[id]])[1];
      row.append(rating(row.dataset.importance), rating(row.dataset.complexity),
        el('td', {}, select, el('div', { class: 'qz-plan-rec', text: `Recommended: ${recLabel}` })));
    });
    const start = el('div', { class: 'qz-plan-actions' });
    (table ? table.closest('.table-wrap') || table : overview.lastElementChild).after(start);
  }

  // Under the plan table: Reset. At the very end of the overview: one big
  // Generate modules button. Unwritten modules stay outlines until it is
  // pressed; nothing else writes them.
  function renderPlanActions() {
    const box = overview?.querySelector('.qz-plan-actions');
    if (!box) return;
    box.replaceChildren(el('div', { class: 'qz-row' },
      el('button', { class: 'qz-btn', type: 'button', onclick: () => { state.plan = {}; syncPlanSelects(); applyPlan(); scheduleSave(); } }, 'Reset to recommended')));
    let gen = overview.querySelector(':scope > .qz-generate');
    if (!gen) {
      gen = el('div', { class: 'qz-generate' });
      overview.append(gen);
    }
    const pending = Object.keys(pendingModules());
    const generating = work.has('write-modules');
    if (!pending.length && !generating) { gen.replaceChildren(); gen.hidden = true; return; }
    gen.hidden = false;
    gen.replaceChildren(
      el('button', {
        class: 'qz-btn qz-primary qz-btn-big', type: 'button', disabled: generating || !hasServer, onclick: generateModules,
      }, generating ? 'Generating modules…' : 'Generate modules'),
      el('p', { class: 'qz-dim', text: !hasServer ? 'Generating needs the local guide server (serve.py).'
        : generating ? 'Claude is writing them now. This page reloads when they are ready.'
          : `Writes ${pending.length} module${pending.length === 1 ? '' : 's'} at the intensities in your plan. Skipped modules stay as outlines.` }));
  }

  // ------------------------------------------------------- final review
  const refreshRecommended = {};
  const refreshOf = (id) => state.refresh[id] || refreshRecommended[id] || 'standard';

  function scoreboard() {
    const rows = studyModules().map((section) => {
      const id = section.dataset.module;
      const counts = { mastered: 0, fragile: 0, gap: 0, misconception: 0 };
      let answered = 0;
      section.querySelectorAll('[data-q]').forEach((q) => {
        const a = state.answers[q.dataset.q];
        if (!a) return;
        answered += 1;
        counts[classify(a.choice === q.dataset.answer, a.confidence)] += 1;
      });
      const { total } = moduleProgress(section);
      return { section, id, counts, answered, total };
    });
    const CHIP = [['mastered', 'mastered', 'low'], ['fragile', 'fragile', 'medium'], ['gap', 'gap', 'high'], ['misconception', 'misconception', 'high']];
    const table = el('table', { class: 'qz-scoreboard' },
      el('thead', {}, el('tr', {}, ['Module', 'Answered', 'Results'].map((h) => el('th', { text: h })))),
      el('tbody', {}, rows.map((r) => el('tr', {},
        el('td', {}, el('a', { href: `#${r.section.id}`, onclick: (e) => { e.preventDefault(); goTo(modules.indexOf(r.section), true); } },
          r.section.dataset.title || r.id), el('div', { class: 'qz-plan-desc', text: intensityOf(r.id) })),
        el('td', { text: isSkipped(r.section) ? 'skipped' : `${r.answered} of ${r.total}` }),
        el('td', {}, CHIP.filter(([key]) => r.counts[key]).map(([key, label, tone]) =>
          el('span', { class: `qz-rating qz-rating-${tone}`, 'data-result': key },
            `${r.counts[key]} ${label}${r.counts[key] > 1 && ['gap', 'misconception'].includes(key) ? 's' : ''}`)),
        r.answered ? '' : el('span', { class: 'qz-dim', text: '–' }))))));
    return { table: el('div', { class: 'table-wrap' }, table), rows };
  }

  function renderFinalStub(section) {
    const { table, rows } = scoreboard();
    const unanswered = rows.reduce((n, r) => n + Math.max(0, r.total - r.answered), 0);
    const unwritten = Object.keys(pendingModules()).length;
    section.querySelectorAll(':scope > :not(h2):not(.qz-eyebrow):not(.qz-work)').forEach((n) => n.remove());
    section.append(
      el('div', { class: 'key-points' }, el('h3', { text: 'Where you are' }),
        el('ul', {},
          el('li', {}, `Round ${round}. ${rows.filter((r) => !isSkipped(r.section)).length} of ${rows.length} modules in your plan.`),
          el('li', {}, unanswered ? `${unanswered} question${unanswered === 1 ? '' : 's'} still unanswered.` : 'Every question in your plan is answered.'),
          unwritten ? el('li', {}, `${unwritten} module${unwritten === 1 ? ' is' : 's are'} not written to your chosen depth yet.`) : '')),
      el('h3', { text: 'Your answers so far' }), table,
      el('div', { class: 'qz-generate qz-generate-review' },
        el('button', { class: 'qz-btn qz-primary qz-btn-big', type: 'button', disabled: !hasServer || work.has('final-review'), onclick: () => requestWork('final-review') }, 'Generate review'),
        el('ol', { class: 'qz-next-steps' },
          el('li', {}, 'Claude reviews what you got right and wrong in each module, and explains the right idea for anything you missed.'),
          el('li', {}, 'It suggests how hard to revisit each module. You can change any of them.'),
          el('li', {}, el('strong', { text: 'Then you choose: ' }), 'keep learning (a new round focused on what you missed) or call it a day (a final report on how well you know each part of the codebase).')),
        unanswered ? el('p', { class: 'qz-dim', text: `${unanswered} unanswered question${unanswered === 1 ? '' : 's'} will count as not covered. You can answer them first, or generate now.` }) : ''));
    if (!hasServer) section.append(el('p', { class: 'qz-dim', text: 'Generating needs the local guide server (serve.py).' }));
  }

  // Every question in the plan, per module, colour-coded by result.
  const RESULT = {
    mastered: ['✓', 'Mastered', 'got'], fragile: ['~', 'Right, but not sure', 'shaky'],
    gap: ['✗', 'Missed', 'missed'], misconception: ['✗', 'Missed, and you were sure', 'missed'],
    unanswered: ['–', 'Not answered', 'none'],
  };

  function choiceText(question, value) {
    const label = question.querySelector(`.qz-choice[data-choice="${CSS.escape(value)}"] .qz-choice-body`);
    return label ? label.textContent.trim() : value;
  }

  function questionBreakdown() {
    const wrap = el('div', { class: 'qz-breakdown' });
    for (const section of studyModules()) {
      if (isSkipped(section)) continue;
      const questions = [...section.querySelectorAll('[data-q]')].filter((q) => !q.dataset.retired && !q.closest('.qz-depth-hidden'));
      if (!questions.length) continue;
      const rows = questions.map((q) => {
        const a = state.answers[q.dataset.q];
        const level = a ? classify(a.choice === q.dataset.answer, a.confidence) : 'unanswered';
        const [icon, label, tone] = RESULT[level];
        const prompt = q.querySelector('.prompt')?.textContent.trim() || q.dataset.q;
        const detail = !a ? 'No answer yet.'
          : a.choice === q.dataset.answer ? `You answered: ${choiceText(q, a.choice)}`
            : `You answered: ${choiceText(q, a.choice)} · Correct: ${choiceText(q, q.dataset.answer)}`;
        return el('li', { class: `qz-result qz-result-${tone}`, 'data-result': level },
          el('span', { class: 'qz-result-icon', 'aria-hidden': 'true', text: icon }),
          el('div', {}, el('div', { class: 'qz-result-prompt', text: prompt }),
            el('div', { class: 'qz-result-detail' }, el('span', { class: 'qz-result-label', text: label }), ` · ${detail}`)));
      });
      const got = rows.filter((r) => r.dataset.result === 'mastered').length;
      wrap.append(el('div', { class: 'qz-review-card qz-tone-neutral' },
        el('div', { class: 'qz-review-card-head' },
          el('a', { href: `#${section.id}`, onclick: (e) => { e.preventDefault(); goTo(modules.indexOf(section), true); } }, section.dataset.title || section.dataset.module),
          el('span', { class: 'qz-dim', text: `${got} of ${rows.length} mastered` })),
        el('ul', { class: 'qz-results' }, rows)));
    }
    if (!wrap.childElementCount) wrap.append(el('p', { class: 'qz-dim', text: 'No questions in your plan yet.' }));
    return wrap;
  }

  // Claude's review and report: every <h3> sub-section becomes a boxed card
  // whose colour says what it is (h3 class got/missed/shaky/plan, or a
  // keyword in the heading as a fallback).
  function toneOf(h3) {
    for (const tone of ['got', 'missed', 'shaky', 'plan']) if (h3.classList.contains(tone)) return tone;
    const text = h3.textContent.toLowerCase();
    if (/missed|wrong|gap|misconception/.test(text)) return 'missed';
    if (/shaky|fragile|improv|still|later/.test(text)) return 'shaky';
    if (/got|right|know|can now|strength|solid/.test(text)) return 'got';
    if (/plan|revisit|next/.test(text)) return 'plan';
    return 'neutral';
  }

  function cardify(section) {
    const heads = [...section.children].filter((n) => n.tagName === 'H3');
    for (const h3 of heads) {
      const card = el('section', { class: `qz-review-card qz-tone-${toneOf(h3)}` });
      const head = el('div', { class: 'qz-review-card-head' });
      h3.before(card);
      head.append(h3);
      card.append(head);
      let next = card.nextSibling;
      while (next && !(next.nodeType === 1 && (next.tagName === 'H3' || next.matches('.qz-final-actions, .qz-review-card')))) {
        const move = next;
        next = next.nextSibling;
        card.append(move);
      }
    }
  }

  function buildFinal() {
    const final = modules.find(isFinal);
    if (!final) return;
    for (const report of modules.filter(isSummary)) cardify(report);
    if (final.dataset.stub !== undefined) { renderFinalStub(final); return; }
    cardify(final);
    // The objective per-question breakdown, after Claude's verdict.
    const glance = el('section', { class: 'qz-review-card qz-tone-neutral' },
      el('div', { class: 'qz-review-card-head' }, el('h3', { text: 'Question by question' })),
      questionBreakdown());
    const verdict = final.querySelector(':scope > .key-points');
    (verdict || final.querySelector(':scope > h2')).after(glance);
    // Authored final review: refresh plan dropdowns and the next-step buttons.
    const table = final.querySelector('[data-refresh]')?.closest('table');
    if (table) {
      table.classList.add('qz-plan');
      table.querySelector('thead tr')?.append(el('th', { text: 'Revisit' }));
    }
    final.querySelectorAll('[data-refresh]').forEach((row) => {
      const id = row.dataset.refresh;
      refreshRecommended[id] = row.dataset.intensity || 'standard';
      const select = el('select', { class: 'qz-intensity', 'aria-label': `How hard to revisit ${id}` },
        INTENSITY.map(([value, label]) => el('option', { value }, label)));
      select.value = refreshOf(id);
      select.addEventListener('change', () => { state.refresh[id] = select.value; scheduleSave(); });
      const recLabel = (INTENSITY.find(([v]) => v === refreshRecommended[id]) || ['', refreshRecommended[id]])[1];
      row.append(el('td', {}, select, el('div', { class: 'qz-plan-rec', text: `Recommended: ${recLabel}` })));
    });
    const done = modules.some(isSummary);
    final.append(el('div', { class: 'qz-final-actions' },
      el('h3', { text: done ? 'Want another pass?' : 'Two ways to finish' }),
      el('div', { class: 'qz-choice-cards' },
        done ? '' : el('div', { class: 'qz-choice-card' },
          el('h4', { text: 'Call it a day' }),
          el('p', {}, 'Get a final report: how well you know each part of the codebase, what you can do now, and what to look at later.'),
          el('button', { class: 'qz-btn qz-primary', type: 'button', disabled: !hasServer, onclick: () => requestWork('summary') }, 'Write my final report')),
        el('div', { class: 'qz-choice-card' },
          el('h4', { text: 'Go back over what I missed' }),
          el('p', {}, `Start round ${round + 1}: the modules above are rewritten to focus on what you missed, at the intensities you set in the table. What you mastered is not repeated.`),
          el('button', { class: `qz-btn${done ? ' qz-primary' : ''}`, type: 'button', disabled: !hasServer, onclick: () => requestWork('new-round') }, `Start round ${round + 1}`)))));
  }

  function syncPlanSelects() {
    overview?.querySelectorAll('[data-plan]').forEach((row) => {
      const select = row.querySelector('.qz-intensity');
      if (select) select.value = intensityOf(row.dataset.plan);
    });
  }

  function applyPlan() {
    for (const section of studyModules()) {
      const intensity = intensityOf(section.dataset.module);
      section.dataset.intensity = intensity;
      const rank = DEPTH_RANK[intensity] ?? 1;
      section.querySelectorAll('[data-depth]').forEach((node) => {
        node.classList.toggle('qz-depth-hidden', (DEPTH_RANK[node.dataset.depth] ?? 1) > rank);
      });
      let banner = section.querySelector(':scope > .qz-skip-banner');
      if (intensity === 'skip') {
        if (!banner) {
          banner = el('div', { class: 'warning qz-skip-banner' },
            el('p', {}, 'You chose to skip this module. Its questions do not count toward your progress.'),
            el('button', {
              class: 'qz-btn', type: 'button',
              onclick: () => {
                const id = section.dataset.module;
                state.plan[id] = recommended[id] && recommended[id] !== 'skip' ? recommended[id] : 'standard';
                syncPlanSelects(); applyPlan(); scheduleSave();
              },
            }, 'Include this module'));
          section.querySelector('h2')?.after(banner);
        }
      } else if (banner) {
        banner.remove();
      }
      section.querySelector(':scope > .qz-pending-banner')?.remove();
      const written = writtenOf(section);
      if (intensity !== 'skip' && (WRITTEN_RANK[written] ?? 2) < (WRITTEN_RANK[intensity] ?? 1)) {
        const text = written === 'skeleton'
          ? `Outline only: this module has not been written yet. It will be written at ${intensity} depth.`
          : `Written to ${written} depth so far. You chose ${intensity}; the extra material has not been written yet.`;
        const notice = el('div', { class: 'note qz-pending-banner', role: 'note' },
          el('p', {}, el('strong', { text: written === 'skeleton' ? 'Coming up. ' : 'More to come. ' }), text),
          el('p', {}, el('button', {
            class: 'qz-btn', type: 'button', onclick: () => goTo(modules.indexOf(overview), true),
          }, 'Go to the overview to generate modules')));
        (section.querySelector(':scope > .qz-skip-banner') || section.querySelector('h2'))?.after(notice);
      }
    }
    updateModuleNav();
    if (modules[state.current]) renderPager(state.current);
    renderPlanActions();
  }

  // ----------------------------------------------------- relay to Claude
  // Only a few buttons ask Claude for work: Generate modules (overview),
  // Send (help with a module), Generate my final review, and the two end
  // options. Each queues a request; the session's background
  // `relay.py wait` wakes up, marks it "working", writes, and marks it
  // "done". The affected sections show a live generating panel, and the
  // page reloads once, when the request is done (never mid-write).
  const work = new Map();  // key -> { id, action, module, modules, status, since }
  const relay = { baseline: null, timer: null, listening: null };
  const RELAY_SLOW_MS = 120000;

  const workKey = (action, module) => (module ? `${action}:${module}` : action);
  const isPending = (section) => section && !isSpecial(section) && Boolean(pendingModules()[section.dataset.module]);
  const WORK_TITLE = {
    'write-modules': 'Writing your modules',
    'help-module': 'Rewriting this module for you',
    'next-module': 'Writing the next module',
    'final-review': 'Writing your final review',
    'new-round': 'Preparing a round on what you missed',
    summary: 'Writing your final report',
  };

  async function requestWork(action, extra = {}) {
    if (!hasServer) return;
    const key = workKey(action, extra.module);
    if (work.has(key)) return;
    const item = { action, module: extra.module || null, modules: extra.modules ? Object.keys(extra.modules) : null, status: 'sending', since: Date.now() };
    work.set(key, item);
    renderWork();
    try {
      await saveNow();
      const data = await api('request', null, { action, ...extra });
      item.id = data.request.id;
      item.status = data.request.status;
      watchForUpdates();
    } catch (error) {
      item.status = 'failed';
      item.error = error.message;
    }
    renderWork();
  }

  function generateModules() {
    const pending = pendingModules();
    const order = studyModules().map((s) => s.dataset.module).filter((id) => pending[id]);
    if (!order.length) return;
    requestWork('write-modules', { modules: Object.fromEntries(order.map((id) => [id, pending[id].wanted])) });
  }

  function targetsOf(item) {
    const byId = (id) => modules.find((m) => m.dataset.module === id);
    if (item.action === 'write-modules') return [overview, ...(item.modules || []).map(byId)].filter(Boolean);
    if (item.action === 'help-module') return [byId(item.module)].filter(Boolean);
    if (item.action === 'next-module') return [modules[modules.length - 1]];
    return [modules.find(isSummary) || modules.find(isFinal) || modules[modules.length - 1]];
  }

  function workPanel(item, section) {
    const slow = Date.now() - item.since > RELAY_SLOW_MS;
    const noListener = relay.listening === false && item.status !== 'working';
    const many = item.action === 'write-modules' && (item.modules || []).length > 1;
    const steps = [
      ['Request sent', ['pending', 'working'].includes(item.status)],
      ['Claude picked it up', item.status === 'working'],
      [many ? `Writing ${item.modules.length} modules (this takes a few minutes)` : 'Writing', false],
      ['This page reloads once everything is ready', false],
    ];
    const current = item.status === 'sending' ? 0 : item.status === 'pending' ? 1 : 2;
    const title = item.action === 'write-modules' && section !== overview ? 'Writing this module' : WORK_TITLE[item.action] || 'Working';
    const panel = el('div', { class: `qz-work${noListener || item.status === 'failed' ? ' qz-work-stuck' : ''}`, role: 'status', 'aria-live': 'polite' },
      el('div', { class: 'qz-work-head' }, el('span', { class: 'qz-spinner', 'aria-hidden': 'true' }),
        el('strong', { text: title }),
        el('span', { class: 'qz-dim', text: ` · ${Math.max(1, Math.round((Date.now() - item.since) / 1000))}s` })),
      el('ol', { class: 'qz-work-steps' }, steps.map(([label, done], i) => el('li', {
        class: done ? 'qz-step-done' : i === current ? 'qz-step-now' : '',
      }, label))));
    if (item.status === 'failed') {
      panel.append(el('p', {}, `The request could not be sent: ${item.error}.`));
    } else if (noListener) {
      panel.append(el('p', {}, el('strong', { text: 'No Claude session is listening. ' }),
        'Your request is saved. In Claude Code, run /onboard (or /review-pr) and say "continue"; it will be picked up.'));
    } else if (slow) {
      panel.append(el('p', { class: 'qz-dim', text: 'This is taking a while. Claude may still be reading code; the page updates by itself when it is done.' }));
    }
    if (item.action === 'write-modules' && section === overview) {
      panel.append(el('p', { class: 'qz-dim', text: 'You can keep reading the overview or answering questions in written modules meanwhile.' }));
    }
    return panel;
  }

  function renderWork() {
    document.querySelectorAll('.qz-work').forEach((n) => n.remove());
    document.querySelectorAll('.qz-generating').forEach((n) => n.classList.remove('qz-generating'));
    for (const item of work.values()) {
      for (const section of targetsOf(item)) {
        if (section !== overview) section.classList.add('qz-generating');
        const anchor = section.querySelector(':scope > h2') || section.querySelector(':scope > .qz-eyebrow') || section.firstElementChild;
        const panel = workPanel(item, section);
        if (section === overview && section.querySelector(':scope > .qz-generate')) section.querySelector(':scope > .qz-generate').before(panel);
        else if (anchor) anchor.after(panel); else section.prepend(panel);
      }
    }
    updateModuleNav();
    if (modules[state.current]) renderPager(state.current);
    renderPlanActions();
  }

  const writingSections = () => new Set([...work.values()].filter((i) => i.status !== 'failed').flatMap(targetsOf));

  function watchForUpdates() {
    if (relay.timer || !hasServer) return;
    relay.timer = setInterval(async () => {
      let meta;
      let queue;
      try {
        [meta, queue] = await Promise.all([api('meta'), api('requests')]);
      } catch { return; }
      relay.listening = queue.listening;
      if (relay.baseline === null) relay.baseline = meta.guide_mtime;
      let finished = false;
      for (const req of queue.requests) {
        const item = [...work.values()].find((i) => i.id === req.id);
        if (!item) continue;
        if (req.status === 'done') finished = true;
        else item.status = req.status;
      }
      // Reload once the request is done, so the page never flickers while
      // Claude is still writing. Other edits to the guide just offer a reload.
      if (finished) {
        clearInterval(relay.timer);
        relay.timer = null;
        guideUpdated(true);
        return;
      }
      if (!work.size && meta.guide_mtime !== relay.baseline) guideUpdated(false);
      if (work.size) renderWork();
    }, 2000);
  }

  function guideUpdated(auto) {
    const typing = document.activeElement && /^(TEXTAREA|INPUT)$/.test(document.activeElement.tagName)
      && !['radio', 'checkbox'].includes(document.activeElement.type) && document.activeElement.value;
    const reload = async () => { await saveNow(); location.reload(); };
    if (auto && !typing) {
      reload();
      return;
    }
    if (document.querySelector('.qz-updated')) return;
    topbar.after(el('div', { class: 'qz-updated', role: 'status' },
      el('span', {}, auto ? 'Claude finished. Your new content is ready.' : 'This guide changed on disk.'),
      el('button', { class: 'qz-btn qz-primary', type: 'button', onclick: reload }, 'Reload to see it')));
  }

  // ------------------------------------------------------ help with a module
  const helpBox = el('div', { class: 'qz-help', hidden: true });

  function renderHelp(section) {
    const input = el('textarea', { rows: '3', class: 'qz-reasoning', id: 'qz-help-text', placeholder: 'For example: "I do not follow how the retry loop decides to give up."' });
    const status = el('p', { class: 'qz-feedback', role: 'status', 'aria-live': 'polite' });
    helpBox.replaceChildren(
      el('label', { for: 'qz-help-text', class: 'qz-help-label' }, 'What do you need help with?'),
      input,
      el('p', { class: 'qz-dim' }, 'Claude will rewrite this module around your question, and the page reloads when it is done. ',
        'You do not have to wait: you can move on to the next module and come back to this one later. ',
        'Use this only if you would rather stay on this section.'),
      el('div', { class: 'qz-row' },
        el('button', {
          class: 'qz-btn qz-primary', type: 'button',
          onclick: () => {
            const message = input.value.trim();
            if (!message) { status.textContent = 'Say what you would like explained differently.'; input.focus(); return; }
            requestWork('help-module', { module: section.dataset.module, message });
            helpBox.hidden = true;
          },
        }, 'Send'),
        el('button', { class: 'qz-btn', type: 'button', onclick: () => { helpBox.hidden = true; renderPager(state.current); } }, 'Cancel'),
        status));
  }

  function moduleProgress(section) {
    if (isSpecial(section) || isSkipped(section)) return { answered: 0, total: 0 };
    const questions = [...section.querySelectorAll('[data-q]')]
      .filter((q) => !q.dataset.retired && !q.closest('.qz-depth-hidden'));
    const answered = questions.filter((q) => state.answers[q.dataset.q]).length;
    return { answered, total: questions.length };
  }

  function buildModules() {
    const total = studyModules().length;
    let number = 0;
    modules.forEach((section, index) => {
      section.id = section.id || `module-${section.dataset.module}`;
      section.setAttribute('tabindex', '-1');
      const heading = section.querySelector('h2');
      const isOverview = section === overview;
      const special = isSpecial(section);
      if (!special) number += 1;
      const eyebrow = isOverview ? (round > 1 ? `Round ${round} · overview` : 'Overview')
        : isFinal(section) ? `Round ${round} · final review` : isSummary(section) ? 'Summary' : `Module ${number} of ${total}`;
      if (heading) heading.before(el('p', { class: 'qz-eyebrow', text: eyebrow }));
      const label = section.dataset.title || (heading ? heading.textContent : section.dataset.module);
      const link = el('a', {
        href: `#${section.id}`, 'data-index': index,
        onclick: (event) => { event.preventDefault(); goTo(index, true); },
      }, el('span', { class: 'qz-mod-num' }, isOverview ? '★' : isFinal(section) ? '✓' : isSummary(section) ? '◆' : String(number)),
      el('span', { class: 'qz-mod-label' }, label, special ? '' : el('span', { class: 'qz-mod-intensity' })),
      el('span', { class: 'qz-mod-status' }));
      moduleNav.append(el('li', {}, link));
    });
    guideRoot.append(pager, helpBox);
  }

  function updateModuleNav() {
    moduleNav.querySelectorAll('a').forEach((link) => {
      const index = Number(link.dataset.index);
      const section = modules[index];
      const { answered, total } = moduleProgress(section);
      const skipped = isSkipped(section);
      const writing = writingSections().has(section) && section !== overview;
      link.classList.toggle('qz-writing', writing);
      link.querySelector('.qz-mod-status').textContent = writing ? 'writing…' : skipped ? 'skipped'
        : isSkeleton(section) ? 'outline' : total ? `${answered}/${total}` : '';
      link.classList.toggle('qz-outline', !skipped && isSkeleton(section));
      link.classList.toggle('qz-done', total > 0 && answered === total);
      link.classList.toggle('qz-skipped', skipped);
      const tag = link.querySelector('.qz-mod-intensity');
      if (tag && overview) tag.textContent = skipped ? '' : intensityOf(section.dataset.module);
      if (index === state.current) link.setAttribute('aria-current', 'step');
      else link.removeAttribute('aria-current');
    });
  }

  // Previous/Next step over skipped modules; the nav can still open them.
  function neighbour(index, step) {
    for (let i = index + step; i >= 0 && i < modules.length; i += step) {
      if (!isSkipped(modules[i])) return i;
    }
    return -1;
  }

  function renderPager(index) {
    const prev = neighbour(index, -1);
    const next = neighbour(index, 1);
    const section = modules[index];
    const position = section === overview ? 'Overview' : isFinal(section) ? 'Final review' : isSummary(section) ? 'Summary'
      : `Module ${studyModules().indexOf(section) + 1} of ${studyModules().length}`;
    // Review guides grow one module at a time: at the last written module,
    // Next asks Claude for the next one instead.
    const askNext = isReview && next < 0 && hasServer;
    const nextAsked = work.has('next-module');
    const nextButton = askNext
      ? el('button', { class: 'qz-btn qz-primary', type: 'button', disabled: nextAsked, onclick: () => requestWork('next-module') },
        nextAsked ? 'Next module requested…' : 'Ask Claude for the next module →')
      : el('button', { class: 'qz-btn qz-primary', type: 'button', disabled: next < 0, onclick: () => goTo(next, true) }, 'Next →');
    const canHelp = !isReview && hasServer && !isSpecial(section) && !isSkipped(section) && !isSkeleton(section);
    const helping = work.has(workKey('help-module', section.dataset.module));
    pager.replaceChildren(
      el('button', { class: 'qz-btn', type: 'button', disabled: prev < 0, onclick: () => goTo(prev, true) }, '← Previous'),
      el('span', { class: 'qz-pager-pos' }, position),
      canHelp ? el('button', {
        class: 'qz-btn', type: 'button', disabled: helping, 'aria-expanded': String(!helpBox.hidden),
        onclick: () => {
          helpBox.hidden = !helpBox.hidden;
          if (!helpBox.hidden) { renderHelp(section); helpBox.querySelector('textarea').focus(); }
        },
      }, helping ? 'Help requested…' : 'Help with this module') : '',
      nextButton,
    );
  }

  function goTo(index, focus) {
    if (!modules.length) return;
    index = Math.max(0, Math.min(modules.length - 1, index));
    state.current = index;
    modules.forEach((section, i) => { section.hidden = i !== index; });
    if (modules[index].dataset.stub !== undefined) renderFinalStub(modules[index]);
    else if (isFinal(modules[index])) modules[index].querySelector('.qz-breakdown')?.replaceWith(questionBreakdown());
    helpBox.hidden = true;
    renderPager(index);
    updateModuleNav();
    if (history.replaceState) history.replaceState(null, '', `#${modules[index].id}`);
    if (focus) {
      modules[index].focus({ preventScroll: true });
      views.guide.scrollIntoView({ block: 'start' });
      window.scrollTo({ top: 0 });
    }
    shell.classList.remove('qz-side-open');
    lastAuto = null;
    queueFollow();
    scheduleSave();
  }

  const moduleOf = (node) => {
    const section = node.closest('section.module[data-module]');
    return section ? section.dataset.module : null;
  };

  // ------------------------------------------------------------ questions
  function buildQuestion(question) {
    const id = question.dataset.q;
    const answer = question.dataset.answer;
    question.classList.add('qz-question');
    question.prepend(el('p', { class: 'qz-eyebrow', text: 'Check your understanding' }));
    if (question.dataset.retired) {
      const note = el('p', { class: 'qz-retired-note' }, `Retired (${question.dataset.retired}). You already showed you know this.`);
      const details = el('details', { class: 'qz-retired' }, el('summary', { text: 'Show retired question' }));
      while (question.firstChild) details.append(question.firstChild);
      question.append(note, details);
      return;
    }
    const list = question.querySelector('.choices') || question;
    const items = [...question.querySelectorAll('[data-choice]')];
    const explain = question.querySelector('.explain');
    if (explain) explain.hidden = true;
    const name = nextId(`q-${id}`);
    const prompt = question.querySelector('.prompt');
    const promptId = prompt ? (prompt.id ||= nextId('prompt')) : null;
    const choices = el('div', { class: 'qz-choices', role: 'radiogroup', 'aria-labelledby': promptId });
    for (const item of items) {
      const input = el('input', { type: 'radio', name, value: item.dataset.choice });
      const label = el('label', { class: 'qz-choice', 'data-choice': item.dataset.choice }, input, ' ');
      const body = el('span', { class: 'qz-choice-body' });
      while (item.firstChild) body.append(item.firstChild);
      label.append(body);
      choices.append(label);
    }
    if (list !== question) list.replaceWith(choices); else question.append(choices);
    items.forEach((item) => item.remove());
    const confName = `${name}-conf`;
    const reasoning = el('textarea', { rows: '2', class: 'qz-reasoning', 'aria-label': 'Your reasoning (optional)', placeholder: 'Why? (optional)' });
    const feedback = el('p', { class: 'qz-feedback', role: 'status', 'aria-live': 'polite' });
    const form = el('div', { class: 'qz-q-form' },
      radioGroup(confName, 'How confident are you? (optional, helps tailor what comes next)', CONFIDENCE, ''),
      reasoning, feedback);
    choices.after(form);
    // Checked by the module's "Check answers" button. Returns 'checked',
    // 'already' or 'unanswered'.
    question.qzCheck = () => {
      if (state.answers[id]) return 'already';
      const choice = selectedValue(choices, name);
      if (!choice) {
        feedback.textContent = 'No answer picked yet.';
        feedback.dataset.level = 'none';
        return 'unanswered';
      }
      state.answers[id] = {
        module: moduleOf(question), choice, confidence: selectedValue(form, confName), correct: choice === answer,
        reasoning: reasoning.value.trim(),
      };
      renderQuestion();
      return 'checked';
    };

    function renderQuestion() {
      const record = state.answers[id];
      if (!record) return;
      question.classList.add('qz-answered');
      choices.querySelectorAll('input').forEach((input) => {
        input.checked = input.value === record.choice;
        input.disabled = true;
      });
      form.querySelectorAll(`input[name="${CSS.escape(confName)}"]`).forEach((input) => {
        input.checked = input.value === record.confidence;
        input.disabled = true;
      });
      reasoning.value = record.reasoning || '';
      reasoning.readOnly = true;
      choices.querySelectorAll('.qz-choice').forEach((label) => {
        label.classList.toggle('qz-correct', label.dataset.choice === answer);
        label.classList.toggle('qz-wrong', label.dataset.choice === record.choice && record.choice !== answer);
      });
      const correct = record.choice === answer;
      const level = classify(correct, record.confidence);
      feedback.textContent = `${correct ? 'Correct.' : 'Not quite.'} ${CLASS_LABEL[level]}`;
      feedback.dataset.level = level;
      if (explain) explain.hidden = false;
    }
    renderQuestion();
  }

  // One "Check answers" button after each module's last question.
  function buildQuizzes() {
    for (const section of studyModules()) {
      const questions = [...section.querySelectorAll('[data-q]')].filter((q) => !q.dataset.retired && q.qzCheck);
      if (!questions.length) continue;
      const status = el('p', { class: 'qz-feedback', role: 'status', 'aria-live': 'polite' });
      const button = el('button', { class: 'qz-btn qz-primary', type: 'button' }, 'Check answers');
      const bar = el('div', { class: 'qz-quiz-actions' }, button, status);
      const refresh = () => {
        const open = questions.filter((q) => !state.answers[q.dataset.q] && !q.closest('.qz-depth-hidden'));
        bar.hidden = !open.length;
      };
      button.addEventListener('click', () => {
        const results = questions.filter((q) => !q.closest('.qz-depth-hidden')).map((q) => q.qzCheck());
        const checked = results.filter((r) => r === 'checked').length;
        const missing = results.filter((r) => r === 'unanswered').length;
        status.textContent = missing
          ? `Checked ${checked}. ${missing} question${missing === 1 ? ' has' : 's have'} no answer yet.`
          : `Checked ${checked}.`;
        if (checked) onProgress(section);
        refresh();
      });
      questions[questions.length - 1].after(bar);
      refresh();
    }
  }

  function classify(correct, confidence) {
    if (correct) return confidence === 'sure' ? 'mastered' : 'fragile';
    return confidence === 'sure' ? 'misconception' : 'gap';
  }

  function onProgress(node) {
    updateModuleNav();
    const section = node.closest('section.module');
    if (section) updateFindingLock(section);
    scheduleSave();
  }

  // ------------------------------------------------------------ exercises
  // The PREDICT: docstring is prose; show it as a callout and break numbered
  // items "(1) ... (2) ..." onto their own lines.
  function renderPredict(node, text) {
    const clean = (text || 'Predict what this exercise prints.').replace(/^\s*PREDICT:\s*/, '').replace(/\s+/g, ' ').trim();
    const parts = clean.split(/\s*\((\d+)\)\s*/);
    node.replaceChildren();
    if (parts.length < 3) { node.textContent = clean; return; }
    if (parts[0]) node.append(el('p', {}, parts[0]));
    const list = el('ol', { class: 'qz-predict-list' });
    for (let i = 1; i < parts.length; i += 2) list.append(el('li', {}, parts[i + 1]));
    node.append(list);
  }

  function buildExercise(box) {
    const name = box.dataset.exercise;
    const record = () => (state.exercises[name] ||= { prediction: '', ran: false, matched: null });
    const predictText = el('div', { class: 'qz-predict' }, hasServer ? 'Loading the exercise…' : 'Exercises run through the local guide server (serve.py).');
    const prediction = el('textarea', { rows: '4', class: 'qz-prediction', 'aria-label': `Your predicted output for ${name}`, placeholder: 'Write the output you expect before running it.' });
    const run = el('button', { class: 'qz-btn qz-primary', type: 'button', disabled: !hasServer }, 'Run exercise');
    const status = el('p', { class: 'qz-feedback', role: 'status', 'aria-live': 'polite' });
    const output = el('pre', { class: 'qz-output', hidden: true });
    box.classList.add('qz-exercise');
    // Step 1 is the exercise's own code. A hand-written .code-listing inside
    // the box is kept (check.py verifies it against the file); otherwise the
    // code comes straight from the exercise file, so it cannot drift.
    const authored = box.querySelector('.code-listing');
    const listingSlot = authored || el('figure', { class: 'code-listing' },
      el('figcaption', {}, el('span', { text: `${name}.py` }), el('span', { class: 'tag', text: 'Python · highlighted lines print' })),
      el('pre', {}, el('code', { text: hasServer ? 'Loading…' : 'The code is shown when the guide runs through serve.py.' })));
    const intro = [...box.childNodes].filter((n) => n !== authored);
    box.replaceChildren(
      el('div', { class: 'qz-ex-head' }, el('span', { class: 'qz-eyebrow', text: 'Exercise' }), el('h3', { class: 'qz-ex-title' }, el('code', { text: name }))),
      ...intro,
      el('h4', { class: 'qz-step' }, el('span', { class: 'qz-step-n' }, '1'), 'Read the code'), listingSlot,
      el('h4', { class: 'qz-step' }, el('span', { class: 'qz-step-n' }, '2'), 'Predict the output'),
      predictText, prediction,
      el('h4', { class: 'qz-step' }, el('span', { class: 'qz-step-n' }, '3'), 'Run it and compare'),
      el('div', { class: 'qz-row' }, run, status), output);
    if (authored) enhanceListing(authored);
    prediction.value = (state.exercises[name] || {}).prediction || '';
    prediction.addEventListener('input', () => { record().prediction = prediction.value; scheduleSave(); });
    if (hasServer) {
      api('exercise', { name }).then((data) => {
        renderPredict(predictText, data.predict);
        if (!authored) {
          const prints = new Set(data.prints || []);
          const code = el('code', {}, data.lines.map((text, i) => el('span', {
            class: prints.has(data.first_line + i) ? 'l out' : 'l', 'data-n': data.first_line + i,
          }, text || ' ')));
          listingSlot.querySelector('pre').replaceChildren(code);
          listingSlot.classList.add('qz-numbered-from');
          colourListing(listingSlot, 'py');
        }
      }).catch((error) => { predictText.textContent = error.message; run.disabled = true; });
    }
    run.addEventListener('click', async () => {
      if (!prediction.value.trim()) {
        status.textContent = 'Write a prediction first. Guessing is fine; it is what you learn from.';
        prediction.focus();
        return;
      }
      run.disabled = true;
      status.textContent = 'Running…';
      try {
        const result = await api('exercise/run', null, { name });
        const rec = record();
        rec.ran = true;
        rec.matched = normalize(result.stdout) === normalize(prediction.value);
        rec.exit_code = result.exit_code;
        output.hidden = false;
        output.textContent = result.stdout + (result.stderr ? `\n--- stderr ---\n${result.stderr}` : '');
        const notes = [];
        if (result.timed_out) notes.push('timed out');
        if (result.truncated) notes.push('output truncated');
        if (result.exit_code) notes.push(`exit code ${result.exit_code}`);
        status.textContent = `${rec.matched ? 'Your prediction matched exactly.' : 'Compare your prediction with the real output.'}${notes.length ? ` (${notes.join(', ')})` : ''}`;
        onProgress(box);
      } catch (error) {
        status.textContent = error.message;
      } finally {
        run.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------- findings
  function buildFindings(section) {
    const groups = section.querySelectorAll('.findings');
    groups.forEach((group) => {
      group.classList.add('qz-findings');
      const lock = el('div', { class: 'qz-lock' },
        el('p', {}, 'Answer this module\'s questions first, then compare your view with the proposed findings.'),
        el('button', {
          class: 'qz-btn', type: 'button',
          onclick: () => { state.revealed[section.dataset.module] = 'early'; updateFindingLock(section); scheduleSave(); },
        }, 'Reveal findings now'));
      group.prepend(lock);
      group.querySelectorAll('[data-finding]').forEach(buildFinding);
    });
    updateFindingLock(section);
  }

  function updateFindingLock(section) {
    const { answered, total } = moduleProgress(section);
    const id = section.dataset.module;
    if (total === 0 || answered === total) state.revealed[id] ||= 'answered';
    const open = Boolean(state.revealed[id]);
    section.querySelectorAll('.qz-findings').forEach((group) => {
      group.classList.toggle('qz-locked', !open);
      group.querySelector('.qz-lock').hidden = open;
      group.querySelectorAll('[data-finding]').forEach((card) => { card.hidden = !open; });
    });
  }

  function findingRecord(card) {
    const id = card.dataset.finding;
    const draft = card.querySelector('.comment-draft');
    return (state.findings[id] ||= {
      module: moduleOf(card), response: '', confidence: '', reasoning: '',
      comment: draft ? draft.textContent.trim() : '', state: 'needs-another-look',
      path: card.dataset.src || '', lines: card.dataset.lines || '',
    });
  }

  function buildFinding(card) {
    const id = card.dataset.finding;
    const rec = findingRecord(card);
    card.classList.add('qz-finding');
    if (card.dataset.severity) {
      card.prepend(el('span', { class: `qz-sev qz-sev-${card.dataset.severity}` }, card.dataset.severity));
    }
    if (card.dataset.src) {
      const where = `${card.dataset.src}${card.dataset.lines ? `:${card.dataset.lines}` : ''}`;
      const link = el('button', { class: 'qz-ref qz-where', type: 'button' }, where);
      link.addEventListener('click', () => openSource({ path: card.dataset.src, lines: card.dataset.lines }, link));
      card.querySelector('h3, h4')?.after(link);
    }
    const draft = card.querySelector('.comment-draft');
    if (draft) draft.hidden = true;
    const base = nextId(`f-${id}`);
    const comment = el('textarea', { rows: '4', class: 'qz-comment', 'aria-label': 'Comment draft' });
    comment.value = rec.comment;
    const reasoning = el('textarea', { rows: '2', class: 'qz-reasoning', 'aria-label': 'Your reasoning', placeholder: 'Why do you agree or disagree?' });
    reasoning.value = rec.reasoning;
    const stateSelect = el('select', { 'aria-label': 'Comment state', class: 'qz-state-select' },
      STATES.map(([value, label]) => el('option', { value }, label)));
    stateSelect.value = rec.state;
    const controls = el('div', { class: 'qz-finding-controls' },
      radioGroup(`${base}-resp`, 'Your view', RESPONSES, rec.response, (value) => {
        rec.response = value;
        if (value === 'disagree' && rec.state === 'ready') { rec.state = 'not-posting'; stateSelect.value = rec.state; }
        scheduleSave();
      }),
      radioGroup(`${base}-conf`, 'Confidence', CONFIDENCE, rec.confidence, (value) => { rec.confidence = value; scheduleSave(); }),
      el('label', { class: 'qz-block' }, 'Reasoning', reasoning),
      el('label', { class: 'qz-block' }, 'Comment draft (edit freely; nothing is posted from here)', comment),
      el('label', { class: 'qz-inline' }, 'State ', stateSelect));
    reasoning.addEventListener('input', () => { rec.reasoning = reasoning.value; scheduleSave(); });
    comment.addEventListener('input', () => { rec.comment = comment.value; scheduleSave(); });
    stateSelect.addEventListener('change', () => { rec.state = stateSelect.value; scheduleSave(); });
    card.append(controls);
  }

  // ------------------------------------------------------------- anchors
  function buildAnchors() {
    guideRoot.querySelectorAll('[data-src]').forEach((node) => {
      if (node.matches('[data-finding], .exercise, figure')) return;
      node.classList.add('qz-ref');
      if (node.tagName === 'CODE') node.classList.add('qz-ref-code');
      if (node.tagName !== 'A' && node.tagName !== 'BUTTON') {
        node.setAttribute('role', 'button');
        node.setAttribute('tabindex', '0');
      } else if (node.tagName === 'A') {
        node.setAttribute('href', '#');
      }
      node.title = `Show in the code pane: ${node.dataset.src}${node.dataset.sym ? ` · ${node.dataset.sym}` : ''}`;
      const open = (event) => {
        event.preventDefault();
        markActive(node);
        openSource(targetOf(node), node);
      };
      node.addEventListener('click', open);
      node.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') open(event); });
    });
  }

  const targetOf = (node) => ({
    path: node.dataset.src, ref: node.dataset.ref, sym: node.dataset.sym,
    occurrence: node.dataset.occurrence, lines: node.dataset.lines,
    label: node.matches('figure') ? '' : node.textContent.trim().slice(0, 60),
    focus: focusFor(node), keys: keysFor(node),
  });

  // The reference whose code is in the pane is outlined in the guide, so it
  // is always clear which sentence the pane is explaining.
  let activeRef = null;
  function markActive(node) {
    if (activeRef) activeRef.classList.remove('qz-ref-active');
    activeRef = node;
    if (node) node.classList.add('qz-ref-active');
  }

  // ------------------------------------------------------ syntax colours
  // A small dependency-free tokenizer: good enough to make code readable,
  // not a parser. Each language lists its comment and string delimiters and
  // keywords; state carries block comments and multi-line strings across
  // lines.
  const KW = {
    py: 'and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case',
    js: 'async await break case catch class const continue debugger default delete do else export extends finally for from function get if import in instanceof let new of return set static super switch this throw try typeof var void while with yield interface type enum implements private public protected readonly declare namespace abstract as',
    c: 'auto break case char const continue default do double else enum extern float for goto if int long register return short signed sizeof static struct switch typedef union unsigned void volatile while class public private protected new delete this throw try catch namespace using template typename virtual override final package import func go defer chan select range map struct interface type var fn let mut impl trait pub use mod match loop where self Self crate dyn move ref unsafe async await val fun object when is in out companion data sealed lateinit',
    sh: 'if then else elif fi for while do done case esac in function return local export source exit echo set unset readonly shift',
    rb: 'def end if elsif else unless while until for in do return class module self nil true false and or not begin rescue ensure yield then when case require attr_reader attr_accessor',
    sql: 'select from where and or not insert into values update set delete create table index view drop alter add join left right inner outer on group by order having limit offset as distinct union all null is in like between primary key foreign references default',
    css: '',
  };
  const CONST = {
    py: 'True False None self cls __init__ __name__',
    js: 'true false null undefined NaN Infinity this',
    c: 'true false null nil NULL nullptr None iota',
    sh: 'true false', rb: 'nil true false self', sql: 'true false null', css: '',
  };
  const DEF_WORDS = new Set(['def', 'class', 'function', 'func', 'fn', 'interface', 'struct', 'trait', 'enum', 'type', 'module', 'impl']);
  const LANG_OF_EXT = {
    py: 'py', pyi: 'py', js: 'js', mjs: 'js', cjs: 'js', jsx: 'js', ts: 'js', tsx: 'js', vue: 'js', svelte: 'js',
    go: 'c', rs: 'c', java: 'c', kt: 'c', kts: 'c', scala: 'c', swift: 'c', c: 'c', h: 'c', cc: 'c', cpp: 'c', hpp: 'c', cs: 'c', php: 'c', proto: 'c', graphql: 'c', gql: 'c', gradle: 'c',
    sh: 'sh', bash: 'sh', zsh: 'sh', yaml: 'sh', yml: 'sh', toml: 'sh', ini: 'sh', cfg: 'sh', conf: 'sh', mk: 'sh', tf: 'sh', hcl: 'sh', dockerfile: 'sh', r: 'sh', ex: 'sh', exs: 'sh',
    rb: 'rb', sql: 'sql', css: 'css', scss: 'css', less: 'css', json: 'json', lock: 'json', html: 'html', htm: 'html', xml: 'html', svg: 'html',
  };
  const SYNTAX = {
    py: { line: ['#'], block: null, strings: ['"""', "'''", '"', "'"] },
    js: { line: ['//'], block: ['/*', '*/'], strings: ['`', '"', "'"] },
    c: { line: ['//'], block: ['/*', '*/'], strings: ['"', "'", '`'] },
    sh: { line: ['#'], block: null, strings: ['"', "'"] },
    rb: { line: ['#'], block: null, strings: ['"', "'"] },
    sql: { line: ['--'], block: ['/*', '*/'], strings: ["'", '"'] },
    css: { line: [], block: ['/*', '*/'], strings: ['"', "'"] },
    json: { line: [], block: null, strings: ['"'] },
    html: { line: [], block: ['<!--', '-->'], strings: ['"', "'"] },
  };
  const wordSets = {};
  const words = (table, lang) => {
    const key = `${lang}:${table === KW ? 'kw' : 'c'}`;
    return (wordSets[key] ||= new Set((table[lang] || '').split(/\s+/).filter(Boolean)));
  };

  function langOf(path) {
    const name = (path || '').split('/').pop().toLowerCase();
    if (name === 'makefile' || name === 'dockerfile' || name === 'justfile') return 'sh';
    return LANG_OF_EXT[name.includes('.') ? name.split('.').pop() : ''] || null;
  }

  // Returns a tokenizer: call it once per line, in order; it remembers open
  // block comments and multi-line strings. Tokens are [className, text];
  // identifiers have a class starting with 'id' so callers can decorate them.
  function tokenizer(lang) {
    const syn = SYNTAX[lang];
    let open = null;  // { cls, end } carried across lines
    return (line) => {
      const out = [];
      const push = (cls, text) => { if (text) out.push([cls, text]); };
      if (!syn) { push('', line); return out; }
      let i = 0;
      let prevWord = '';
      while (i < line.length) {
        if (open) {
          const end = line.indexOf(open.end, i);
          if (end === -1) { push(open.cls, line.slice(i)); return out; }
          push(open.cls, line.slice(i, end + open.end.length));
          i = end + open.end.length;
          open = null;
          continue;
        }
        const rest = line.slice(i);
        if (syn.line.some((m) => rest.startsWith(m))) { push('tk-com', rest); break; }
        if (syn.block && rest.startsWith(syn.block[0])) {
          open = { cls: 'tk-com', end: syn.block[1] };
          push('tk-com', syn.block[0]);
          i += syn.block[0].length;
          continue;
        }
        const q = syn.strings.find((d) => rest.startsWith(d));
        if (q) {
          let j = i + q.length;
          while (j < line.length) {
            if (line[j] === '\\') { j += 2; continue; }
            if (line.startsWith(q, j)) break;
            j += 1;
          }
          if (j >= line.length) {
            push('tk-str', rest);
            if (q.length === 3 || q === '`') open = { cls: 'tk-str', end: q };
            break;
          }
          push('tk-str', line.slice(i, j + q.length));
          i = j + q.length;
          prevWord = '';
          continue;
        }
        if (lang === 'py' && /^@[\w.]+/.test(rest)) {
          const m = /^@[\w.]+/.exec(rest)[0];
          push('tk-dec', m); i += m.length; continue;
        }
        const num = /^(?:0[xXbBoO][\da-fA-F_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)[jJlLuUfF]?/.exec(rest);
        if (num && !/[\w$]/.test(line[i - 1] || '')) { push('tk-num', num[0]); i += num[0].length; continue; }
        const word = /^[A-Za-z_$][\w$]*/.exec(rest);
        if (word) {
          const w = word[0];
          const after = /^\s*(\S)/.exec(line.slice(i + w.length));
          let cls = 'id';
          if (lang === 'json') cls = /^(true|false|null)$/.test(w) ? 'tk-const' : 'id';
          else if (words(KW, lang).has(lang === 'sql' ? w.toLowerCase() : w)) cls = 'tk-kw';
          else if (words(CONST, lang).has(w)) cls = 'tk-const';
          else if (DEF_WORDS.has(prevWord)) cls = 'id tk-def';
          else if (after && after[1] === '(') cls = 'id tk-call';
          else if (/^[A-Z][A-Za-z0-9]*[a-z]/.test(w) && lang !== 'sql') cls = 'id tk-type';
          push(cls, w);
          prevWord = w;
          i += w.length;
          continue;
        }
        const ws = /^\s+/.exec(rest);
        if (ws) { push('', ws[0]); i += ws[0].length; continue; }
        push('tk-op', line[i]);
        i += 1;
      }
      return out;
    };
  }

  // Render one line's tokens; identifiers in `focus` get the focus marker.
  function renderTokens(tokens, focus) {
    return tokens.map(([cls, text]) => {
      const isId = cls.startsWith('id');
      const classes = cls.split(' ').filter((c) => c && c !== 'id');
      if (isId && focus && focus.has(text)) classes.push('tk-focus');
      if (!classes.length) return document.createTextNode(text);
      return el('span', { class: classes.join(' '), 'data-tok': isId ? text : null }, text);
    });
  }

  // ------------------------------------------------------- concept focus
  // Which identifiers matter for the reference being shown: inline <code>
  // in the same sentence or bullet, the symbol's own name, and any explicit
  // data-vars. Authors can also name key lines with data-key="lit|lit".
  const FOCUS_STOP = new Set(['self', 'cls', 'this', 'None', 'True', 'False', 'null', 'true', 'false', 'return', 'def', 'class', 'import', 'from', 'if', 'else', 'for', 'in', 'and', 'or', 'not']);

  function focusFor(node) {
    const names = new Set();
    const add = (text) => {
      text = (text || '').trim();
      const m = /^@?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)/.exec(text);
      if (!m || text.includes('/')) return;
      const last = m[1].split('.').pop();
      if (last.length >= 2 && !FOCUS_STOP.has(last)) names.add(last);
    };
    if (node.dataset.sym) add(node.dataset.sym);
    (node.dataset.vars || '').split(/[\s,]+/).forEach(add);
    const block = node.closest('li, p, td, dd, .explain, figcaption, .note, .warning');
    if (block) block.querySelectorAll('code').forEach((c) => { if (!c.closest('figure')) add(c.textContent); });
    if (node.tagName === 'CODE' && !node.dataset.ref) add(node.textContent);
    return names;
  }

  const keysFor = (node) => (node.dataset.key || '').split('|').map((k) => k.trim()).filter(Boolean);

  // --------------------------------------------------------- code listings
  // A listing is <figure class="code-listing"> with a <pre>. Lines become
  // numbered rows; data-src (+ data-lines) adds a button to open the real file,
  // and check.py verifies the listing text against that file. A bare <pre>
  // in the guide is wrapped as an unlabelled listing so code always looks
  // like code.
  function enhanceListing(figure) {
    const pre = figure.querySelector('pre');
    if (!pre) return;
    if (!pre.querySelector('.l')) {
      const text = pre.textContent.replace(/\n$/, '');
      pre.replaceChildren(el('code', {}, text.split('\n').map((line) => el('span', { class: 'l' }, line || ' '))));
    }
    const range = /^(\d+)/.exec(figure.dataset.lines || '');
    if (range) {
      figure.classList.add('qz-numbered-from');
      pre.querySelectorAll('.l').forEach((line, i) => { line.dataset.n = Number(range[1]) + i; });
    }
    colourListing(figure, langOf(figure.dataset.src || figure.querySelector('figcaption span')?.textContent || ''));
    let caption = figure.querySelector('figcaption');
    if (!caption) {
      caption = el('figcaption', {}, el('span', { text: figure.dataset.src || 'Code' }));
      figure.prepend(caption);
    }
    if (figure.dataset.src) {
      figure.classList.add('qz-ref-listing');
      caption.append(el('button', {
        class: 'qz-listing-open', type: 'button',
        onclick: (event) => { markActive(figure); openSource(targetOf(figure), event.currentTarget); },
      }, 'Show in file ↗'));
    }
  }

  function colourListing(figure, lang) {
    const next = tokenizer(lang);
    const keys = keysFor(figure);
    figure.querySelectorAll('.l').forEach((line) => {
      const text = line.textContent === ' ' ? '' : line.textContent;
      const nodes = renderTokens(next(text), null);
      if (nodes.length) line.replaceChildren(...nodes); else line.textContent = ' ';
      if (keys.some((k) => text.includes(k))) line.classList.add('qz-key');
    });
  }

  function buildListings() {
    guideRoot.querySelectorAll('pre').forEach((pre) => {
      if (pre.closest('.code-listing, .qz-exercise, .explain')) return;
      const figure = el('figure', { class: 'code-listing' });
      pre.replaceWith(figure);
      figure.append(pre);
    });
    guideRoot.querySelectorAll('figure.code-listing').forEach((figure) => {
      if (!figure.closest('.qz-exercise')) enhanceListing(figure);
    });
  }

  // ----------------------------------------------------------- code pane
  const code = {
    path: null, start: 1, end: 0, total: 0, highlight: null, changed: new Set(),
    selStart: null, selEnd: null, returnFocus: null,
  };
  const codeTitle = el('h2', { class: 'qz-code-title', tabindex: '-1' }, 'Source');
  const codeInfo = el('p', { class: 'qz-code-info', role: 'status', 'aria-live': 'polite' });
  const codeBody = el('div', { class: 'qz-code-body' });
  const commentBox = el('div', { class: 'qz-comment-box', hidden: true });

  const mapBack = el('button', { class: 'qz-btn', type: 'button', hidden: true, onclick: () => showRepoMap() }, '← Repository map');

  function buildCodePane() {
    codePane.append(
      el('div', { class: 'qz-code-head' }, codeTitle,
        el('div', { class: 'qz-row qz-code-head-actions' }, mapBack,
          el('button', { class: 'qz-btn', type: 'button', 'aria-label': 'Close source', onclick: closeSource }, 'Close'))),
      codeInfo, focusStrip, commentBox, codeBody);
    codePane.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeSource(); });
  }

  const wide = () => window.matchMedia('(min-width: 1100px)').matches;

  let openSeq = 0;

  // opts.quiet: used by auto-follow. Keeps focus where it is and leaves the page scroll alone.
  async function openSource(target, trigger, opts = {}) {
    const quiet = Boolean(opts.quiet);
    const seq = ++openSeq;
    if (!quiet) {
      code.returnFocus = trigger || document.activeElement;
      autoFollow = true;
      code.userOpened = true;
      // What the reader clicked stays put until they scroll on a fair way.
      code.pinnedAt = window.scrollY;
      // On the overview the pane belongs to the repo map; a file opened from
      // there keeps a way back instead of being replaced by auto-follow.
      if (overview && modules[state.current] === overview) {
        if (!target) { showRepoMap(); return; }
        code.mode = 'map-source';
      }
    }
    codePane.hidden = false;
    shell.classList.add('qz-code-open');
    backdrop.hidden = wide();
    if (!target) {
      if (!code.path) {
        codeTitle.textContent = 'Source';
        codeInfo.textContent = 'Pick a file from the source outline, or follow a reference in the guide.';
        codeBody.replaceChildren();
      }
      if (!quiet) codeTitle.focus();
      return;
    }
    codeTitle.textContent = target.path;
    codeInfo.textContent = 'Loading…';
    if (!quiet) codeTitle.focus();
    try {
      let line = 1;
      let end = 1;
      if (target.ref) {
        const found = await api('anchor', { path: target.path, ref: target.ref, occurrence: target.occurrence });
        line = found.line; end = found.end_line;
      } else if (target.sym) {
        const found = await api('symbol', { path: target.path, name: target.sym });
        line = found.line; end = found.end_line || found.line;
      } else if (target.lines) {
        const match = /^(\d+)(?:-(\d+))?$/.exec(target.lines.trim());
        if (match) { line = Number(match[1]); end = Number(match[2] || match[1]); }
      }
      if (seq !== openSeq) return;
      code.highlight = target.ref || target.sym || target.lines ? [line, end] : null;
      code.label = target.label || target.sym || '';
      code.focus = target.focus || new Set();
      code.keys = target.keys || [];
      await loadSource(target.path, { around: line });
      if (seq !== openSeq) return;
      const row = codeBody.querySelector(`[data-line="${line}"]`);
      if (row) {
        // Scroll only the code pane. scrollIntoView would also move the guide.
        const top = row.getBoundingClientRect().top - codeBody.getBoundingClientRect().top + codeBody.scrollTop;
        codeBody.scrollTop = Math.max(0, top - codeBody.clientHeight / 3);
      }
    } catch (error) {
      if (seq !== openSeq) return;
      codeInfo.textContent = error.message;
      codeBody.replaceChildren();
    }
  }

  async function loadSource(path, query) {
    if (code.mode !== 'map-source') code.mode = 'source';
    codeTitle.classList.remove('qz-title-plain');
    mapBack.hidden = !(overview && modules[state.current] === overview);
    const data = await api('source', { path, base: changesState.base, ...query });
    code.path = data.path;
    code.start = data.start; code.end = data.end; code.total = data.total;
    code.changed = new Set(data.changed || []);
    code.selStart = code.selEnd = null;
    commentBox.hidden = true;
    codeTitle.textContent = data.path;
    const parts = [`Lines ${data.start}–${data.end} of ${data.total}`];
    if (data.status) parts.push(`changed in this PR (${data.status})`);
    if (code.highlight) {
      const span = `${code.highlight[0]}${code.highlight[1] !== code.highlight[0] ? `–${code.highlight[1]}` : ''}`;
      parts.unshift(code.label ? `Showing “${code.label}” · line${span.includes('–') ? 's' : ''} ${span}` : `Highlighting ${span}`);
    }
    codeInfo.textContent = parts.join(' · ');
    const list = el('ol', { class: 'qz-lines', start: String(data.start) });
    const next = tokenizer(langOf(data.path));
    const focus = code.highlight ? (code.focus || new Set()) : new Set();
    const keys = code.highlight ? (code.keys || []) : [];
    const inRange = (n) => Boolean(code.highlight) && n >= code.highlight[0] && n <= code.highlight[1];
    const seen = new Set();
    data.lines.forEach((text, i) => {
      const n = data.start + i;
      const classes = ['qz-line'];
      const tokens = next(text);
      if (inRange(n)) {
        classes.push('qz-hl');
        if (n === code.highlight[0]) classes.push('qz-hl-first');
        if (n === code.highlight[1]) classes.push('qz-hl-last');
        const usesFocus = tokens.some(([cls, t]) => cls.startsWith('id') && focus.has(t));
        if (keys.some((k) => text.includes(k))) classes.push('qz-key');
        else if (!keys.length && usesFocus && code.highlight[1] > code.highlight[0]) classes.push('qz-key-soft');
        tokens.forEach(([cls, t]) => { if (cls.startsWith('id') && focus.has(t)) seen.add(t); });
      }
      if (code.changed.has(n)) classes.push('qz-added');
      const number = el('button', {
        class: 'qz-ln', type: 'button', 'aria-label': `Line ${n}${isReview ? ', select for a comment' : ''}`,
        onclick: (event) => selectLine(n, event.shiftKey),
      }, String(n));
      const body = el('code', {}, renderTokens(tokens, inRange(n) ? focus : null));
      if (!text) body.textContent = ' ';
      list.append(el('li', { class: classes.join(' '), 'data-line': n }, number, body));
    });
    renderFocusStrip([...focus].filter((f) => seen.has(f)), list);
    const controls = [];
    if (data.start > 1) {
      controls.push(el('button', { class: 'qz-btn', type: 'button', onclick: () => loadSource(path, { start: Math.max(1, data.start - 400), end: data.start - 1 }) }, 'Show earlier lines'));
    }
    if (data.end < data.total) {
      controls.push(el('button', { class: 'qz-btn', type: 'button', onclick: () => loadSource(path, { start: data.end + 1 }) }, 'Show later lines'));
    }
    codeBody.replaceChildren(list, el('div', { class: 'qz-row' }, controls));
  }
  // "Look for" chips: the concept's identifiers found in the shown range.
  // Clicking a chip steps through its occurrences.
  const focusStrip = el('div', { class: 'qz-focus-strip', hidden: true });
  function renderFocusStrip(names, list) {
    focusStrip.hidden = !names.length;
    if (!names.length) { focusStrip.replaceChildren(); return; }
    const cursor = {};
    focusStrip.replaceChildren(el('span', { class: 'qz-focus-label', text: 'Look for:' }),
      ...names.map((name) => el('button', {
        class: 'qz-focus-chip', type: 'button', title: `Jump to the next “${name}”`,
        onclick: () => {
          const hits = [...list.querySelectorAll('.tk-focus')].filter((n) => n.dataset.tok === name);
          if (!hits.length) return;
          cursor[name] = ((cursor[name] ?? -1) + 1) % hits.length;
          const hit = hits[cursor[name]];
          list.querySelectorAll('.tk-flash').forEach((n) => n.classList.remove('tk-flash'));
          hit.classList.add('tk-flash');
          const top = hit.getBoundingClientRect().top - codeBody.getBoundingClientRect().top + codeBody.scrollTop;
          codeBody.scrollTop = Math.max(0, top - codeBody.clientHeight / 3);
        },
      }, name)));
  }

  function selectLine(n, extend) {
    if (!isReview) return;
    if (extend && code.selStart !== null) {
      code.selEnd = n;
    } else {
      code.selStart = code.selEnd = n;
    }
    const [a, b] = [Math.min(code.selStart, code.selEnd), Math.max(code.selStart, code.selEnd)];
    codeBody.querySelectorAll('.qz-line').forEach((row) => {
      const line = Number(row.dataset.line);
      row.classList.toggle('qz-sel', line >= a && line <= b);
    });
    const body = el('textarea', { rows: '3', 'aria-label': 'Your comment', placeholder: 'Your review comment' });
    const select = el('select', { 'aria-label': 'Comment state' }, STATES.map(([value, label]) => el('option', { value }, label)));
    select.value = 'needs-another-look';
    commentBox.hidden = false;
    commentBox.replaceChildren(
      el('p', {}, `Comment on ${code.path}:${a}${b !== a ? `-${b}` : ''} `, el('span', { class: 'qz-hint' }, '(Shift-click a line number to extend)')),
      body,
      el('div', { class: 'qz-row' }, select,
        el('button', {
          class: 'qz-btn qz-primary', type: 'button',
          onclick: () => {
            if (!body.value.trim()) { body.focus(); return; }
            state.comments.push({
              id: `c${Date.now().toString(36)}${state.comments.length}`, path: code.path, start: a, end: b,
              body: body.value.trim(), state: select.value, confidence: '', reasoning: '',
            });
            commentBox.hidden = true;
            codeInfo.textContent = `Comment added to the review (${state.comments.length} reviewer comment${state.comments.length === 1 ? '' : 's'}).`;
            scheduleSave();
          },
        }, 'Add comment'),
        el('button', { class: 'qz-btn', type: 'button', onclick: () => { commentBox.hidden = true; } }, 'Cancel')));
    body.focus();
  }

  // Auto-follow: on wide screens the pane shows the code behind whatever claim
  // is near the top of the guide. Closing the pane turns it off; the Source
  // button or any reference click turns it back on.
  let autoFollow = true;
  let lastAuto = null;
  let followQueued = false;

  function followScroll() {
    if (!autoFollow || !wide() || !hasServer || views.guide.hidden) return;
    const section = modules[state.current];
    if (!section) return;
    if (section === overview) {
      if (code.mode !== 'map' && code.mode !== 'map-source') showRepoMap({ quiet: true });
      return;
    }
    if (code.mode === 'map' || code.mode === 'map-source') { code.mode = null; lastAuto = null; }
    if (code.pinnedAt != null) {
      if (Math.abs(window.scrollY - code.pinnedAt) < window.innerHeight * 0.35) return;
      code.pinnedAt = null;
    }
    const refs = [...section.querySelectorAll('.qz-ref[data-src], .qz-ref-listing')].filter((n) => n.offsetParent !== null);
    if (!refs.length) return;
    const line = window.innerHeight * 0.4;
    let pick = refs[0];
    for (const node of refs) {
      if (node.getBoundingClientRect().top <= line) pick = node;
    }
    if (pick === lastAuto) return;
    lastAuto = pick;
    markActive(pick);
    openSource(targetOf(pick), null, { quiet: true });
  }

  function queueFollow() {
    if (followQueued) return;
    followQueued = true;
    requestAnimationFrame(() => { followQueued = false; followScroll(); });
  }
  window.addEventListener('scroll', queueFollow, { passive: true });
  window.addEventListener('resize', () => {
    // Shrinking to a narrow layout turns a pane the reader never asked for
    // into an overlay; put it away instead.
    if (!wide() && !codePane.hidden && !code.userOpened) {
      codePane.hidden = true;
      shell.classList.remove('qz-code-open');
      markActive(null);
    }
    lastAuto = null;
    queueFollow();
  });

  function closeSource() {
    autoFollow = false;
    code.userOpened = false;
    markActive(null);
    codePane.hidden = true;
    backdrop.hidden = true;
    shell.classList.remove('qz-code-open');
    if (code.returnFocus && document.contains(code.returnFocus)) code.returnFocus.focus();
  }

  // -------------------------------------------------------- repo map
  // On the overview the pane shows the repository's folders and what lives
  // in them. Descriptions come from overview elements with data-area="dir/"
  // (usually rows of a "where things live" table); hovering or clicking
  // such a row highlights its folder, and the map links back to the row.
  let outlineReady = null;
  const areaRows = () => (overview ? [...overview.querySelectorAll('[data-area]')] : []);
  const areaKey = (path) => path.replace(/\/+$/, '');

  function areaText(row) {
    if (row.dataset.areaDesc) return row.dataset.areaDesc;
    const cells = [...row.children];
    if (row.tagName === 'TR' && cells.length > 1) return cells.slice(1).map((c) => c.textContent.trim()).join(' ').trim();
    return row.textContent.trim();
  }

  function buildTree(files) {
    const root = { name: '', path: '', dirs: new Map(), files: [], count: 0 };
    for (const file of files) {
      const parts = file.split('/');
      let node = root;
      node.count += 1;
      for (const part of parts.slice(0, -1)) {
        const path = node.path ? `${node.path}/${part}` : part;
        if (!node.dirs.has(part)) node.dirs.set(part, { name: part, path, dirs: new Map(), files: [], count: 0 });
        node = node.dirs.get(part);
        node.count += 1;
      }
      node.files.push(file);
    }
    return root;
  }

  async function showRepoMap(opts = {}) {
    code.mode = 'map';
    code.path = null;
    mapBack.hidden = true;
    markActive(null);
    codePane.hidden = false;
    shell.classList.add('qz-code-open');
    backdrop.hidden = wide();
    commentBox.hidden = true;
    codeTitle.textContent = 'Repository map';
    focusStrip.hidden = true;
    codeTitle.classList.add('qz-title-plain');
    codeInfo.textContent = 'Folders and what lives in them. Click a folder to expand it, or a file to open it.';
    await outlineReady;
    if (code.mode !== 'map') return;
    const areas = new Map(areaRows().map((row) => [areaKey(row.dataset.area), row]));
    const tree = buildTree(outlineFiles);
    const filter = el('input', { type: 'search', class: 'qz-filter qz-map-filter', placeholder: 'Filter folders and files', 'aria-label': 'Filter the repository map' });
    const list = el('ul', { class: 'qz-map', role: 'tree' });

    const fileItem = (file) => el('li', { role: 'treeitem', 'data-path': file, class: 'qz-map-file' },
      el('button', { class: 'qz-linkish', type: 'button', onclick: (e) => openSource({ path: file }, e.currentTarget) },
        file.split('/').pop()),
      areas.has(file) ? el('div', { class: 'qz-map-desc', text: areaText(areas.get(file)) }) : '');

    const dirItem = (node, depth) => {
      const row = areas.get(node.path);
      const details = el('details', { open: depth === 0 || Boolean(row) && depth < 2 },
        el('summary', {},
          el('span', { class: 'qz-map-name' }, `${node.name}/`),
          el('span', { class: 'qz-dim', text: ` ${node.count} file${node.count === 1 ? '' : 's'}` }),
          row ? el('div', { class: 'qz-map-desc', text: areaText(row) }) : ''),
        el('ul', { role: 'group' },
          [...node.dirs.values()].sort((a, b) => a.name.localeCompare(b.name)).map((d) => dirItem(d, depth + 1)),
          node.files.map(fileItem)));
      const item = el('li', { role: 'treeitem', 'data-path': node.path, class: row ? 'qz-map-dir qz-map-known' : 'qz-map-dir' }, details);
      if (row) {
        details.querySelector('summary').addEventListener('click', () => flashArea(row));
      }
      return item;
    };

    [...tree.dirs.values()].sort((a, b) => a.name.localeCompare(b.name)).forEach((d) => list.append(dirItem(d, 0)));
    tree.files.forEach((f) => list.append(fileItem(f)));
    filter.addEventListener('input', () => {
      const q = filter.value.trim().toLowerCase();
      list.querySelectorAll('li').forEach((li) => {
        const match = !q || li.dataset.path.toLowerCase().includes(q) || li.textContent.toLowerCase().includes(q);
        li.hidden = !match;
        if (q && match) li.querySelectorAll(':scope > details').forEach((d) => { d.open = true; });
      });
    });
    const known = areas.size;
    codeBody.replaceChildren(el('div', { class: 'qz-map-wrap' },
      filter,
      known ? '' : el('p', { class: 'qz-dim', text: 'This guide has no folder descriptions yet; showing the plain tree.' }),
      list));
    if (!opts.quiet) codeTitle.focus();
  }

  function highlightArea(path, scroll) {
    if (code.mode !== 'map') return;
    codeBody.querySelectorAll('.qz-map-hl').forEach((n) => n.classList.remove('qz-map-hl'));
    const item = codeBody.querySelector(`li[data-path="${CSS.escape(areaKey(path))}"]`);
    if (!item) return;
    item.classList.add('qz-map-hl');
    for (let n = item; n && n !== codeBody; n = n.parentElement) if (n.tagName === 'DETAILS') n.open = true;
    item.querySelector(':scope > details')?.setAttribute('open', '');
    if (scroll) {
      const top = item.getBoundingClientRect().top - codeBody.getBoundingClientRect().top + codeBody.scrollTop;
      codeBody.scrollTop = Math.max(0, top - 40);
    }
  }

  function flashArea(row) {
    row.classList.add('qz-area-flash');
    row.scrollIntoView({ block: 'nearest' });
    setTimeout(() => row.classList.remove('qz-area-flash'), 1200);
  }

  function buildAreas() {
    for (const row of areaRows()) {
      row.classList.add('qz-area');
      row.title = `Show ${row.dataset.area} in the repository map`;
      row.addEventListener('mouseenter', () => highlightArea(row.dataset.area, false));
      row.addEventListener('click', (event) => {
        if (event.target.closest('.qz-ref, a, button')) return;
        if (code.mode !== 'map') showRepoMap({ quiet: true }).then(() => highlightArea(row.dataset.area, true));
        else highlightArea(row.dataset.area, true);
      });
    }
  }

  // ------------------------------------------------------ outline/search
  const filterInput = el('input', { type: 'search', class: 'qz-filter', id: 'qz-filter', placeholder: 'Filter  ( / )', 'aria-label': 'Filter modules, references and files' });
  const refList = el('ul', { class: 'qz-reflist' });
  const fileList = el('ul', { class: 'qz-files' });
  let outlineFiles = [];
  let changedFiles = new Set();

  function buildSide() {
    side.append(
      el('div', { class: 'qz-side-search' }, filterInput),
      el('h2', { class: 'qz-side-h' }, 'Modules'), moduleNav,
      el('h2', { class: 'qz-side-h' }, 'In this guide'), refList,
      el('h2', { class: 'qz-side-h' }, 'Source files'), fileList);
    const seen = new Set();
    guideRoot.querySelectorAll('.qz-ref[data-src]').forEach((node) => {
      const label = node.dataset.sym || node.textContent.trim().slice(0, 60) || node.dataset.src;
      const key = `${node.dataset.src}|${label}`;
      if (seen.has(key)) return;
      seen.add(key);
      refList.append(el('li', { 'data-filter': `${label} ${node.dataset.src}`.toLowerCase() },
        el('button', { class: 'qz-linkish', type: 'button', onclick: () => node.click() },
          el('span', { text: label }), el('span', { class: 'qz-dim', text: node.dataset.src }))));
    });
    filterInput.addEventListener('input', applyFilter);
    document.addEventListener('keydown', (event) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
      if (event.key === '/' && !typing && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        shell.classList.add('qz-side-open');
        filterInput.focus();
      } else if (event.key === 'Escape' && document.activeElement === filterInput) {
        filterInput.value = '';
        applyFilter();
        filterInput.blur();
      }
    });
    loadOutline();
  }

  function loadOutline() {
    outlineReady = fetchOutline();
    return outlineReady;
  }

  async function fetchOutline() {
    if (!hasServer) {
      outlineFiles = [...new Set([...guideRoot.querySelectorAll('[data-src]')].map((n) => n.dataset.src))].sort();
    } else {
      try {
        const data = await api('outline');
        outlineFiles = data.files;
        changedFiles = new Set(data.changed || []);
      } catch (error) {
        fileList.replaceChildren(el('li', { class: 'qz-dim', text: error.message }));
        return;
      }
    }
    renderFiles();
  }

  function renderFiles() {
    const query = filterInput.value.trim().toLowerCase();
    fileList.replaceChildren();
    const files = query ? outlineFiles.filter((f) => f.toLowerCase().includes(query)) : outlineFiles;
    if (query) {
      files.slice(0, 200).forEach((file) => fileList.append(fileItem(file, file)));
      if (!files.length) fileList.append(el('li', { class: 'qz-dim', text: 'No matching files' }));
      return;
    }
    // Group by top-level directory using <details> so large trees stay compact.
    const groups = new Map();
    for (const file of files) {
      const slash = file.indexOf('/');
      const dir = slash === -1 ? '' : file.slice(0, slash);
      if (!groups.has(dir)) groups.set(dir, []);
      groups.get(dir).push(file);
    }
    for (const [dir, items] of groups) {
      if (!dir) { items.forEach((file) => fileList.append(fileItem(file, file))); continue; }
      const hasChange = items.some((f) => changedFiles.has(f));
      const inner = el('ul', {}, items.map((file) => fileItem(file, file.slice(dir.length + 1))));
      const details = el('details', { open: hasChange || groups.size < 4 }, el('summary', {}, `${dir}/`, hasChange ? el('span', { class: 'qz-chg', text: ' ●', title: 'changed' }) : ''), inner);
      fileList.append(el('li', {}, details));
    }
  }

  function fileItem(file, label) {
    const symbols = el('ul', { class: 'qz-symbols', hidden: true });
    const toggle = el('button', {
      class: 'qz-sym-toggle', type: 'button', 'aria-label': `Symbols in ${file}`, 'aria-expanded': 'false', disabled: !hasServer,
      onclick: async () => {
        const open = symbols.hidden;
        symbols.hidden = !open;
        toggle.setAttribute('aria-expanded', String(open));
        if (open && !symbols.childElementCount) {
          try {
            const data = await api('symbols', { path: file });
            if (!data.symbols.length) symbols.append(el('li', { class: 'qz-dim', text: 'No symbols found' }));
            data.symbols.forEach((s) => symbols.append(el('li', {}, el('button', {
              class: 'qz-linkish', type: 'button', onclick: (e) => openSource({ path: file, sym: s.name }, e.currentTarget),
            }, `${s.name}`, el('span', { class: 'qz-dim', text: ` ${s.kind} · ${s.line}` })))));
          } catch (error) {
            symbols.append(el('li', { class: 'qz-dim', text: error.message }));
          }
        }
      },
    }, '▸');
    return el('li', {}, toggle,
      el('button', { class: `qz-linkish${changedFiles.has(file) ? ' qz-changed' : ''}`, type: 'button', title: file, onclick: (e) => openSource({ path: file }, e.currentTarget) }, label),
      symbols);
  }

  function applyFilter() {
    const query = filterInput.value.trim().toLowerCase();
    refList.querySelectorAll('li').forEach((li) => { li.hidden = query && !li.dataset.filter.includes(query); });
    moduleNav.querySelectorAll('li').forEach((li) => { li.hidden = query && !li.textContent.toLowerCase().includes(query); });
    renderFiles();
  }

  // -------------------------------------------------------------- changes
  const changesState = { base: null, loaded: false };

  async function renderChanges() {
    const view = views.changes;
    if (!view) return;
    if (!hasServer || !serverMeta || !serverMeta.review) {
      view.replaceChildren(el('div', { class: 'qz-panel' }, el('h2', { text: 'Changes' }),
        el('p', { text: 'The Changes view needs a prepared review served with serve.py --review N.' })));
      return;
    }
    const review = serverMeta.review;
    const baseSelect = el('select', { id: 'qz-base', onchange: () => { changesState.base = baseSelect.value; renderChanges(); } },
      Object.entries(review.bases).map(([key, sha]) => el('option', { value: key }, `${key} (${sha.slice(0, 9)})`)));
    baseSelect.value = changesState.base || review.diff_base;
    const list = el('ul', { class: 'qz-change-list' });
    const diffBox = el('div', { class: 'qz-diff', 'aria-live': 'polite' });
    view.replaceChildren(el('div', { class: 'qz-panel' },
      el('h2', { text: 'Changes' }),
      el('p', { class: 'qz-dim' }, `Head ${review.head_sha.slice(0, 9)} compared with `, el('label', { for: 'qz-base', class: 'qz-sr' }, 'Diff base'), baseSelect),
      el('div', { class: 'qz-changes' }, list, diffBox)));
    try {
      const data = await api('changes', { base: changesState.base });
      if (!data.changes.length) list.append(el('li', { class: 'qz-dim', text: 'No file changes between these commits.' }));
      for (const change of data.changes) {
        const label = change.old_path ? `${change.old_path} → ${change.path}` : change.path;
        const stats = change.binary ? 'binary' : `+${change.additions} −${change.deletions}`;
        list.append(el('li', {}, el('button', {
          class: 'qz-linkish', type: 'button', disabled: !change.viewable,
          title: change.viewable ? label : 'Hidden by the source policy',
          onclick: (e) => showDiff(change, diffBox, e.currentTarget),
        }, el('span', { class: `qz-status qz-status-${change.status}`, text: change.status }), ' ', label,
        el('span', { class: 'qz-dim', text: ` ${change.viewable ? stats : 'hidden'}` }))));
      }
    } catch (error) {
      list.append(el('li', { class: 'qz-dim', text: error.message }));
    }
  }

  async function showDiff(change, box, trigger) {
    box.replaceChildren(el('p', { class: 'qz-dim', text: 'Loading diff…' }));
    try {
      const data = await api('diff', { path: change.path, base: changesState.base });
      const head = el('div', { class: 'qz-row' }, el('h3', { text: change.path }));
      if (change.status !== 'D') {
        head.append(el('button', { class: 'qz-btn', type: 'button', onclick: () => openSource({ path: change.path }, trigger) }, 'Open in source pane'));
      }
      if (data.binary) {
        box.replaceChildren(head, el('p', { text: 'Binary file; no text diff to show.' }));
        return;
      }
      const table = el('table', { class: 'qz-diff-table' });
      let oldLine = 0;
      let newLine = 0;
      for (const line of data.diff.split('\n')) {
        if (/^(diff --git|index |--- |\+\+\+ |similarity|rename |new file|deleted file|old mode|new mode)/.test(line)) continue;
        const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/.exec(line);
        if (hunk) {
          oldLine = Number(hunk[1]); newLine = Number(hunk[2]);
          table.append(el('tr', { class: 'qz-d-hunk' }, el('td', { colspan: '3', text: line })));
          continue;
        }
        if (line === '' || line.startsWith('\\')) continue;
        const sign = line[0];
        const text = line.slice(1) || ' ';
        if (sign === '+') {
          table.append(el('tr', { class: 'qz-d-add' }, el('td', { class: 'qz-dn' }), el('td', { class: 'qz-dn', text: newLine++ }), el('td', {}, el('span', { class: 'qz-sr', text: 'added ' }), el('code', { text }))));
        } else if (sign === '-') {
          table.append(el('tr', { class: 'qz-d-del' }, el('td', { class: 'qz-dn', text: oldLine++ }), el('td', { class: 'qz-dn' }), el('td', {}, el('span', { class: 'qz-sr', text: 'removed ' }), el('code', { text }))));
        } else {
          table.append(el('tr', {}, el('td', { class: 'qz-dn', text: oldLine++ }), el('td', { class: 'qz-dn', text: newLine++ }), el('td', {}, el('code', { text }))));
        }
      }
      if (!table.rows.length) table.append(el('tr', {}, el('td', { colspan: '3', text: change.old_path ? 'Renamed without content changes.' : 'No textual changes.' })));
      box.replaceChildren(head, el('div', { class: 'qz-table-scroll' }, table));
    } catch (error) {
      box.replaceChildren(el('p', { text: error.message }));
    }
  }

  // --------------------------------------------------------------- review
  function renderReview() {
    const view = views.review;
    if (!view) return;
    const panel = el('div', { class: 'qz-panel' }, el('h2', { text: 'Review summary' }),
      el('p', {}, 'Every proposed finding you have revealed and every comment you added, grouped by state. ',
        'Nothing is posted from this page. Export the results and ask Claude for a preview; posting needs your separate, explicit instruction.'));
    if (serverMeta && serverMeta.review && serverMeta.review.stale) {
      panel.append(el('p', { class: 'qz-warn', role: 'alert' }, 'The review worktree no longer matches the reviewed head SHA. Refresh the review before relying on line numbers.'));
    }
    const items = [];
    guideRoot.querySelectorAll('[data-finding]').forEach((card) => {
      const section = card.closest('section.module');
      if (section && !state.revealed[section.dataset.module]) return;
      const rec = findingRecord(card);
      items.push({ kind: 'finding', id: card.dataset.finding, title: card.querySelector('h3, h4')?.textContent || card.dataset.finding, rec, card });
    });
    state.comments.forEach((rec) => items.push({ kind: 'comment', id: rec.id, title: `Your comment on ${rec.path}:${rec.start}${rec.end !== rec.start ? `-${rec.end}` : ''}`, rec }));
    for (const [value, label] of STATES) {
      const group = items.filter((item) => item.rec.state === value);
      panel.append(el('h3', {}, `${label} (${group.length})`));
      if (!group.length) { panel.append(el('p', { class: 'qz-dim', text: 'None.' })); continue; }
      const list = el('ul', { class: 'qz-review-list' });
      for (const item of group) {
        const select = el('select', { 'aria-label': `State for ${item.title}` }, STATES.map(([v, l]) => el('option', { value: v }, l)));
        select.value = item.rec.state;
        select.addEventListener('change', () => {
          item.rec.state = select.value;
          const cardSelect = item.card?.querySelector('.qz-state-select');
          if (cardSelect) cardSelect.value = select.value;
          scheduleSave();
          renderReview();
        });
        const where = item.rec.path ? `${item.rec.path}${item.rec.lines ? `:${item.rec.lines}` : item.rec.start ? `:${item.rec.start}${item.rec.end !== item.rec.start ? `-${item.rec.end}` : ''}` : ''}` : '';
        const actions = [select];
        if (item.kind === 'comment') {
          actions.push(el('button', {
            class: 'qz-btn', type: 'button',
            onclick: () => { state.comments = state.comments.filter((c) => c.id !== item.id); scheduleSave(); renderReview(); },
          }, 'Delete'));
        }
        list.append(el('li', { class: 'qz-review-item' },
          el('div', {}, el('strong', { text: item.title }), where ? el('span', { class: 'qz-dim', text: ` ${where}` }) : '',
            item.kind === 'finding' ? el('span', { class: 'qz-dim', text: item.rec.response ? ` · you ${item.rec.response}${item.rec.confidence ? ` (${item.rec.confidence})` : ''}` : ' · no response yet' }) : ''),
          el('pre', { class: 'qz-comment-preview', text: (item.kind === 'finding' ? item.rec.comment : item.rec.body) || '(empty comment)' }),
          el('div', { class: 'qz-row' }, actions)));
      }
      panel.append(list);
    }
    panel.append(el('div', { class: 'qz-row' }, el('button', { class: 'qz-btn qz-primary', type: 'button', onclick: openExport }, 'Export results')));
    view.replaceChildren(panel);
  }

  // --------------------------------------------------------------- export
  const exportDialog = el('dialog', { class: 'qz-dialog', 'aria-labelledby': 'qz-export-title' });

  function openExport() {
    const text = `${PREFIX} ${JSON.stringify(resultsPayload())}`;
    const area = el('textarea', { rows: '8', readonly: true, 'aria-label': 'Results code', class: 'qz-code-text' });
    area.value = text;
    const status = el('p', { role: 'status', 'aria-live': 'polite', class: 'qz-dim' });
    exportDialog.replaceChildren(
      el('h2', { id: 'qz-export-title', text: 'Results code' }),
      el('p', {}, 'Paste this into your Claude session so the next guide version can adapt to your answers. ',
        hasServer ? 'Progress is also saved under docs/onboarding/local/, which Git ignores.' : ''),
      area,
      el('div', { class: 'qz-row' },
        el('button', {
          class: 'qz-btn qz-primary', type: 'button',
          onclick: async () => {
            try { await navigator.clipboard.writeText(text); status.textContent = 'Copied.'; } catch { area.select(); status.textContent = 'Press Ctrl+C to copy.'; }
          },
        }, 'Copy'),
        el('button', { class: 'qz-btn', type: 'button', onclick: () => exportDialog.close() }, 'Close'),
        status));
    if (!exportDialog.isConnected) document.body.append(exportDialog);
    exportDialog.showModal();
    area.select();
  }

  // ----------------------------------------------------------------- init
  async function init() {
    buildShell();
    buildModules();
    buildPlan();
    buildCodePane();
    await loadSaved();
    if (state.prefs.theme) applyTheme(state.prefs.theme);
    guideRoot.querySelectorAll('[data-q]').forEach(buildQuestion);
    buildQuizzes();
    guideRoot.querySelectorAll('.exercise[data-exercise]').forEach(buildExercise);
    syncPlanSelects();
    applyPlan();
    buildFinal();
    modules.forEach(buildFindings);
    buildListings();
    buildAnchors();
    buildAreas();
    buildSide();
    if (hasServer) {
      try {
        serverMeta = await api('meta');
        relay.baseline = serverMeta.guide_mtime;
        const queue = await api('requests');
        relay.listening = queue.listening;
        for (const req of queue.requests.filter((r) => r.status !== 'done')) {
          work.set(workKey(req.action, req.module), {
            id: req.id, action: req.action, module: req.module, modules: req.modules ? Object.keys(req.modules) : null,
            status: req.status, since: Date.parse(req.created_at) || Date.now(),
          });
        }
        if (work.size) { watchForUpdates(); renderWork(); }
        if (serverMeta.review) {
          changesState.base = serverMeta.review.diff_base;
          Object.assign(state, { head: serverMeta.review.head_sha, pr: String(serverMeta.review.pr ?? state.pr) });
          if (serverMeta.review.stale) {
            guideRoot.prepend(el('p', { class: 'qz-warn', role: 'alert' }, 'This review is stale: the worktree has moved away from the reviewed head SHA.'));
          }
        }
      } catch (error) {
        saveStatus.textContent = error.message;
      }
    }
    const hashIndex = modules.findIndex((m) => `#${m.id}` === location.hash);
    goTo(hashIndex >= 0 ? hashIndex : state.current, false);
    // Wide screens: the code pane is open from the start and follows the
    // reference you are reading. Narrow screens: it stays an on-demand drawer.
    if (wide() && hasServer) openSource(null, null, { quiet: true });
    queueFollow();
    document.documentElement.classList.add('qz-ready');
    if (params.has('layoutcheck')) {
      document.body.append(el('script', { src: new URL('layout_check.js', appBase).href }));
    }
  }

  init();
})();
