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
    answers: {}, exercises: {}, findings: {}, comments: [], revealed: {}, plan: {}, current: 0,
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
    if (overview) payload.plan = Object.fromEntries(studyModules().map((s) => [s.dataset.module, intensityOf(s.dataset.module)]));
    return payload;
  }

  async function loadSaved() {
    if (!hasServer) return;
    try {
      const data = await api('results');
      const saved = data.results;
      if (saved && saved.guide === meta.id) {
        for (const key of ['answers', 'exercises', 'findings', 'revealed', 'plan']) {
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
        el('button', { class: 'qz-btn qz-primary', type: 'button', onclick: openExport }, 'Export results')),
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
  const studyModules = () => modules.filter((s) => s !== overview);
  const intensityOf = (id) => state.plan[id] || recommended[id] || 'standard';
  const isSkipped = (section) => section !== overview && intensityOf(section.dataset.module) === 'skip';

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
    const start = el('div', { class: 'qz-row qz-plan-actions' },
      el('button', { class: 'qz-btn', type: 'button', onclick: () => { state.plan = {}; syncPlanSelects(); applyPlan(); scheduleSave(); } }, 'Reset to recommended'),
      el('button', {
        class: 'qz-btn qz-primary', type: 'button',
        onclick: () => { const next = modules.findIndex((s) => s !== overview && !isSkipped(s)); if (next >= 0) goTo(next, true); },
      }, 'Start the first module →'));
    (table ? table.closest('.table-wrap') || table : overview.lastElementChild).after(start);
    // Rows link to modules; the anchor text is the module name.
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
    }
    updateModuleNav();
    if (modules[state.current]) renderPager(state.current);
  }

  function moduleProgress(section) {
    if (section === overview || isSkipped(section)) return { answered: 0, total: 0 };
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
      if (!isOverview) number += 1;
      if (heading) heading.before(el('p', { class: 'qz-eyebrow', text: isOverview ? 'Overview' : `Module ${number} of ${total}` }));
      const label = section.dataset.title || (heading ? heading.textContent : section.dataset.module);
      const link = el('a', {
        href: `#${section.id}`, 'data-index': index,
        onclick: (event) => { event.preventDefault(); goTo(index, true); },
      }, el('span', { class: 'qz-mod-num' }, isOverview ? '★' : String(number)), el('span', { class: 'qz-mod-label' }, label,
        isOverview ? '' : el('span', { class: 'qz-mod-intensity' })),
      el('span', { class: 'qz-mod-status' }));
      moduleNav.append(el('li', {}, link));
    });
    guideRoot.append(pager);
  }

  function updateModuleNav() {
    moduleNav.querySelectorAll('a').forEach((link) => {
      const index = Number(link.dataset.index);
      const section = modules[index];
      const { answered, total } = moduleProgress(section);
      const skipped = isSkipped(section);
      link.querySelector('.qz-mod-status').textContent = skipped ? 'skipped' : total ? `${answered}/${total}` : '';
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
    const position = section === overview ? 'Overview'
      : `Module ${studyModules().indexOf(section) + 1} of ${studyModules().length}`;
    pager.replaceChildren(
      el('button', { class: 'qz-btn', type: 'button', disabled: prev < 0, onclick: () => goTo(prev, true) }, '← Previous'),
      el('span', { class: 'qz-pager-pos' }, position),
      el('button', { class: 'qz-btn qz-primary', type: 'button', disabled: next < 0, onclick: () => goTo(next, true) }, 'Next →'),
    );
  }

  function goTo(index, focus) {
    if (!modules.length) return;
    index = Math.max(0, Math.min(modules.length - 1, index));
    state.current = index;
    modules.forEach((section, i) => { section.hidden = i !== index; });
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
    const submit = el('button', { class: 'qz-btn qz-primary', type: 'submit' }, 'Check answer');
    const form = el('form', { class: 'qz-q-form' },
      radioGroup(confName, 'How confident are you?', CONFIDENCE, ''),
      reasoning, el('div', { class: 'qz-row' }, submit, feedback));
    choices.after(form);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (state.answers[id]) return;
      const choice = selectedValue(choices, name);
      const confidence = selectedValue(form, confName);
      if (!choice || !confidence) {
        feedback.textContent = 'Pick an answer and how confident you are.';
        return;
      }
      state.answers[id] = {
        module: moduleOf(question), choice, confidence, correct: choice === answer,
        reasoning: reasoning.value.trim(),
      };
      renderQuestion();
      onProgress(question);
    });

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
      submit.disabled = true;
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
  });

  // The reference whose code is in the pane is outlined in the guide, so it
  // is always clear which sentence the pane is explaining.
  let activeRef = null;
  function markActive(node) {
    if (activeRef) activeRef.classList.remove('qz-ref-active');
    activeRef = node;
    if (node) node.classList.add('qz-ref-active');
  }

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

  function buildCodePane() {
    codePane.append(
      el('div', { class: 'qz-code-head' }, codeTitle,
        el('button', { class: 'qz-btn', type: 'button', 'aria-label': 'Close source', onclick: closeSource }, 'Close')),
      codeInfo, commentBox, codeBody);
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
    data.lines.forEach((text, i) => {
      const n = data.start + i;
      const classes = ['qz-line'];
      if (code.highlight && n >= code.highlight[0] && n <= code.highlight[1]) classes.push('qz-hl');
      if (code.changed.has(n)) classes.push('qz-added');
      const number = el('button', {
        class: 'qz-ln', type: 'button', 'aria-label': `Line ${n}${isReview ? ', select for a comment' : ''}`,
        onclick: (event) => selectLine(n, event.shiftKey),
      }, String(n));
      list.append(el('li', { class: classes.join(' '), 'data-line': n }, number, el('code', { text: text || ' ' })));
    });
    const controls = [];
    if (data.start > 1) {
      controls.push(el('button', { class: 'qz-btn', type: 'button', onclick: () => loadSource(path, { start: Math.max(1, data.start - 400), end: data.start - 1 }) }, 'Show earlier lines'));
    }
    if (data.end < data.total) {
      controls.push(el('button', { class: 'qz-btn', type: 'button', onclick: () => loadSource(path, { start: data.end + 1 }) }, 'Show later lines'));
    }
    codeBody.replaceChildren(list, el('div', { class: 'qz-row' }, controls));
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

  async function loadOutline() {
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
    guideRoot.querySelectorAll('[data-q]').forEach(buildQuestion);
    guideRoot.querySelectorAll('.exercise[data-exercise]').forEach(buildExercise);
    syncPlanSelects();
    applyPlan();
    modules.forEach(buildFindings);
    buildListings();
    buildAnchors();
    buildSide();
    if (hasServer) {
      try {
        serverMeta = await api('meta');
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
