// SPDX-License-Identifier: Apache-2.0
/* Layout probe loaded by guide.js when the URL has ?layoutcheck=1.
 * Shows every module, measures the rendered page, and writes a JSON report
 * into <pre id="layout-check-result"> for check_layout.py to read.
 */
(() => {
  'use strict';
  const TOLERANCE = 1;
  const issues = [];
  const describe = (node) => {
    const parts = [];
    for (let n = node; n && n !== document.body && parts.length < 4; n = n.parentElement) {
      let part = n.tagName.toLowerCase();
      if (n.id) part += `#${n.id}`;
      else if (n.classList.length) part += `.${[...n.classList].slice(0, 2).join('.')}`;
      if (n.dataset && n.dataset.module) part += `[data-module=${n.dataset.module}]`;
      parts.unshift(part);
    }
    return parts.join(' > ');
  };
  const add = (kind, node, detail) => issues.push({ kind, where: describe(node), detail });
  const overlap = (a, b) => {
    const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    return w > 2 && h > 2 ? w * h : 0;
  };
  const scrollsX = (node) => {
    for (let n = node.parentElement; n && n !== document.body; n = n.parentElement) {
      const style = getComputedStyle(n);
      if (['auto', 'scroll'].includes(style.overflowX)) return true;
    }
    return false;
  };

  function measure() {
    document.querySelectorAll('section.module[data-module]').forEach((s) => { s.hidden = false; });
    document.querySelectorAll('.qz-findings [data-finding]').forEach((c) => { c.hidden = false; });
    document.querySelectorAll('details').forEach((d) => { d.open = true; });

    const viewport = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth > viewport + TOLERANCE) {
      add('page-overflow', document.body, `page is ${document.documentElement.scrollWidth}px wide in a ${viewport}px viewport`);
    }

    // Diagram labels: clipped by the SVG box or overlapping each other.
    document.querySelectorAll('figure.diagram svg, svg.diagram').forEach((svg) => {
      const box = svg.getBoundingClientRect();
      if (box.width < 1) return;
      const labels = [...svg.querySelectorAll('text')].map((t) => ({ t, r: t.getBoundingClientRect() }))
        .filter(({ r }) => r.width > 0);
      for (const { t, r } of labels) {
        if (r.left < box.left - TOLERANCE || r.right > box.right + TOLERANCE ||
            r.top < box.top - TOLERANCE || r.bottom > box.bottom + TOLERANCE) {
          add('diagram-label-clipped', svg, `"${t.textContent.trim()}" extends outside the diagram`);
        }
        if (r.height > 0 && r.height < 8) {
          add('diagram-label-tiny', svg, `"${t.textContent.trim()}" renders ${r.height.toFixed(1)}px tall`);
        }
      }
      for (let i = 0; i < labels.length; i++) {
        for (let j = i + 1; j < labels.length; j++) {
          if (overlap(labels[i].r, labels[j].r) > 4) {
            add('diagram-label-overlap', svg, `"${labels[i].t.textContent.trim()}" overlaps "${labels[j].t.textContent.trim()}"`);
          }
        }
      }
    });

    // Tables wider than their column must sit in a horizontal scroller.
    document.querySelectorAll('main[data-guide] table').forEach((table) => {
      const container = table.closest('section, main, .qz-panel') || document.body;
      const tr = table.getBoundingClientRect();
      const cr = container.getBoundingClientRect();
      if (tr.right > cr.right + TOLERANCE && !scrollsX(table)) {
        add('table-overflow', table, `table is ${Math.round(tr.width)}px in a ${Math.round(cr.width)}px column without a scroll wrapper`);
      }
    });

    // Text clipped by overflow:hidden containers inside the guide.
    document.querySelectorAll('main[data-guide] *').forEach((node) => {
      const style = getComputedStyle(node);
      if (style.overflowX === 'hidden' && style.textOverflow !== 'ellipsis' && node.scrollWidth > node.clientWidth + TOLERANCE) {
        add('clipped-content', node, `${node.scrollWidth}px of content in ${node.clientWidth}px`);
      }
    });

    // Guide content must keep a readable measure and stay inside the viewport.
    const main = document.querySelector('main[data-guide]');
    if (main) {
      const r = main.getBoundingClientRect();
      if (r.width < 260) add('narrow-content', main, `guide column is only ${Math.round(r.width)}px wide`);
      if (r.right > viewport + TOLERANCE || r.left < -TOLERANCE) add('content-offscreen', main, 'guide column extends past the viewport');
    }

    // Interactive controls must be on screen and big enough to hit.
    document.querySelectorAll('.qz-top button, .qz-pager button, .qz-q-form button, .qz-exercise button').forEach((button) => {
      if (button.closest('[hidden]') || getComputedStyle(button).display === 'none') return;
      const r = button.getBoundingClientRect();
      if (r.width === 0) return;
      if (r.right > viewport + TOLERANCE) add('control-offscreen', button, `"${button.textContent.trim()}" is cut off`);
      if (r.height < 24) add('control-too-small', button, `"${button.textContent.trim()}" is ${Math.round(r.height)}px tall`);
    });

    const out = document.createElement('pre');
    out.id = 'layout-check-result';
    out.hidden = true;
    out.textContent = JSON.stringify({ viewport, issues });
    document.body.append(out);
  }

  const ready = () => document.documentElement.classList.contains('qz-ready');
  const start = () => (document.fonts ? document.fonts.ready : Promise.resolve())
    .then(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    .then(measure);
  if (ready()) start();
  else {
    const timer = setInterval(() => { if (ready()) { clearInterval(timer); start(); } }, 50);
  }
})();
