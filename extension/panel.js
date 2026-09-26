/* Steuerwerte einsetzen — eine Leiste neben ZHprivateTax.
 *
 * Der Grundsatz: die Erweiterung kennt die Felder des Formulars NICHT. Sie
 * muss es auch nicht. Du klickst in ein Eingabefeld, dann in der Leiste auf
 * „einsetzen" — der Wert landet in dem Feld, das zuletzt den Fokus hatte.
 * Damit funktioniert sie auch, wenn das Steueramt die Oberfläche umbaut, und
 * sie kann nichts an der falschen Stelle eintragen: du bestimmst die Stelle.
 *
 * Sie sendet nichts. Kein fetch, kein XHR, keine Berechtigung ausser
 * `storage`. Die Werte kommen aus einer Datei, die du einmal auswählst, und
 * liegen danach im lokalen Speicher des Browsers.
 *
 * Erzeugt wird die Datei mit `make extension-daten`.
 */
(() => {
  'use strict';

  const SCHLUESSEL_DATEN = 'stx_daten';
  const SCHLUESSEL_ERLEDIGT = 'stx_erledigt';
  const SCHLUESSEL_LAGE = 'stx_lage';

  let daten = null;
  let erledigt = {};      // id -> true
  let letztesFeld = null; // das Eingabefeld mit dem letzten Fokus
  let filter = '';

  // --- Das Feld merken, bevor der Klick in die Leiste den Fokus wegnimmt ---
  //
  // `focusin` steigt auf, `blur` nicht — deshalb dieser Weg. Die Leiste selbst
  // wird ausgenommen, sonst merkt sie sich ihr eigenes Suchfeld.
  function merken(e) {
    const el = e.target;
    if (!el || typeof el.closest !== 'function') return;
    if (el.closest('#stx-panel')) return;
    if (el.matches('input, textarea, [contenteditable="true"]')) {
      letztesFeld = el;
    }
  }
  document.addEventListener('focusin', merken, true);
  // Zusaetzlich beim Zeigen: `focusin` bleibt aus, wenn das Fenster den Fokus
  // nicht hat — und ein Feld, in das man klickt, ist gemeint, auch dann.
  document.addEventListener('pointerdown', merken, true);

  /* Einen Wert in ein Feld schreiben, so dass die Seite es merkt.
   *
   * Moderne Oberflaechen (Angular, React) lesen nicht das Attribut, sondern
   * horchen auf Ereignisse. Ein blosses `el.value = …` traegt den Wert zwar
   * sichtbar ein, aber das Formular kennt ihn nicht — beim Speichern waere er
   * wieder weg. Deshalb der native Setter plus `input` und `change`.
   */
  function einsetzen(el, text) {
    if (!el) return false;
    if (el.isContentEditable) {
      el.textContent = text;
      el.dispatchEvent(new InputEvent('input', { bubbles: true }));
      return true;
    }
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, text); else el.value = text;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.focus();
    return true;
  }

  function speichern(schluessel, wert) {
    try { chrome.storage.local.set({ [schluessel]: wert }); } catch (e) { /* egal */ }
  }

  function lesen(schluessel) {
    return new Promise((fertig) => {
      try {
        chrome.storage.local.get([schluessel], (r) => fertig(r?.[schluessel]));
      } catch (e) { fertig(undefined); }
    });
  }

  // --- Aufbau der Leiste ---------------------------------------------------

  function panel() {
    let el = document.getElementById('stx-panel');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'stx-panel';
    el.innerHTML = `
      <div class="kopf">
        <b>Steuerwerte</b>
        <span class="fort"></span>
        <button class="klapp" title="Ein- und ausklappen">▾</button>
      </div>
      <div class="suche"><input type="search" placeholder="suchen …"></div>
      <div class="inhalt"></div>
      <div class="fuss">Klick ins Formularfeld, dann „einsetzen“. Nichts verlässt den Rechner.</div>`;
    document.documentElement.appendChild(el);

    el.querySelector('.klapp').addEventListener('click', () => {
      el.classList.toggle('zu');
    });
    el.querySelector('.suche input').addEventListener('input', (e) => {
      filter = e.target.value.trim().toLowerCase();
      zeichnen();
    });
    ziehbar(el, el.querySelector('.kopf'));
    return el;
  }

  /* Die Leiste laesst sich verschieben — sie soll kein Feld verdecken. */
  function ziehbar(el, griff) {
    let start = null;
    griff.addEventListener('mousedown', (e) => {
      if (e.target.closest('button')) return;
      const r = el.getBoundingClientRect();
      start = { x: e.clientX, y: e.clientY, top: r.top, left: r.left };
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!start) return;
      el.style.top = `${Math.max(0, start.top + e.clientY - start.y)}px`;
      el.style.left = `${Math.max(0, start.left + e.clientX - start.x)}px`;
      el.style.right = 'auto';
    });
    document.addEventListener('mouseup', () => {
      if (!start) return;
      start = null;
      speichern(SCHLUESSEL_LAGE, { top: el.style.top, left: el.style.left });
    });
  }

  /* Text so maskieren, dass er auch INNERHALB eines Attributs sicher ist.
   *
   * `textContent` -> `innerHTML` maskiert `&`, `<` und `>` — aber nicht das
   * Anfuehrungszeichen. Ein Wert wie `Bank "Zum Löwen"` haette damit das
   * Attribut `data-wert="..."` gesprengt und beliebiges Markup eingeschleust.
   * Die Werte stammen zwar aus der eigenen Ablage, aber Institutsnamen
   * kommen aus PDFs (260923-dua, Code-Review).
   */
  function text(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function passt(w, block, formular) {
    if (!filter) return true;
    return [w.bezeichnung, w.unterscheidung, w.ziffer, w.ziffername, w.feld,
            block.titel, formular].join(' ').toLowerCase().includes(filter);
  }

  function zeichnen() {
    const el = panel();
    const inhalt = el.querySelector('.inhalt');

    if (!daten) {
      inhalt.innerHTML = `
        <div class="hinweis">
          Noch keine Werte geladen.<br>
          Wähle die Datei <code>steuerwerte.json</code> aus
          <code>output/latest/</code>.
          <br><button class="laden">Datei wählen …</button>
        </div>`;
      inhalt.querySelector('.laden').addEventListener('click', dateiWaehlen);
      el.querySelector('.fort').textContent = '';
      return;
    }

    // Drei Zahlen, nicht eine: einzutragen, davon erledigt, und die, die das
    // Formular selbst rechnet. „3 von 3 gesetzt" war irrefuehrend, wenn zwei
    // davon gar nicht eingetragen werden duerfen.
    let einzutragen = 0, gesetzt = 0, rechnet = 0;
    const teile = [];
    for (const f of daten.formulare || []) {
      const bloecke = [];
      for (const b of f.bloecke || []) {
        const werte = (b.werte || []).filter((w) => passt(w, b, f.formular));
        if (!werte.length) continue;
        // Ein Konto im Wertschriftenverzeichnis ist eine ZEILE mit mehreren
        // Spalten — Bezeichnung, Steuerwert, Bruttoertrag A, Bruttoertrag B.
        // Wer nur den Saldo einsetzt, hat die Zeile nicht ausgefuellt: die
        // Bezeichnung fehlt und der Ertrag steht in der falschen Kolonne
        // (260923-dua). Liegt eine solche Zeile vor, zeigt die Leiste sie in
        // der Reihenfolge, in der das Formular sie abfragt.
        if (Array.isArray(b.zeile) && b.zeile.length && !filter) {
          const spalten = b.zeile.map((sp) => {
            const hat = String(sp.wert || '').trim() !== '';
            const fertig = sp.id ? !!erledigt[sp.id] : false;
            // Auch die Bezeichnung wird eingetippt — sie zaehlt mit.
            if (hat) { einzutragen++; if (fertig) gesetzt++; }
            return `
              <div class="wert ${fertig ? 'fertig' : ''} ${hat ? '' : 'leer'}"
                   ${sp.id ? `data-id="${text(sp.id)}"` : ''}>
                <span class="bez">${text(sp.titel)}</span>
                <span class="betrag">${hat ? text(sp.wert) : '—'}</span>
                ${hat ? `<button class="setzen" data-wert="${text(sp.wert)}">einsetzen</button>`
                      : '<span class="warn">kein Wert</span>'}
              </div>`;
          }).join('');
          bloecke.push(
            `<div class="block zeile340"><p>${text(b.titel)}` +
            `<span class="zn">eine Zeile im Verzeichnis</span></p>${spalten}</div>`);
          continue;
        }
        const reihen = werte.map((w) => {
          const fertig = !!erledigt[w.id];
          if (w.gerechnet) { rechnet++; }
          else { einzutragen++; if (fertig) gesetzt++; }
          const zusatz = w.unterscheidung ? ` (${w.unterscheidung})` : '';
          const feld = w.feld ? `Feld ${w.feld} · ` : '';
          return `
            <div class="wert ${fertig ? 'fertig' : ''} ${w.gerechnet ? 'rechnet' : ''}"
                 data-id="${text(w.id)}">
              <span class="bez">${text(w.bezeichnung + zusatz)}
                <span>${feld}Ziffer ${text(w.ziffer)}${
                  w.gerechnet ? ' · <i class="warn">rechnet das Formular selbst</i>' : ''}</span>
              </span>
              <span class="betrag">${text(w.betrag)}</span>
              ${w.gerechnet ? '' :
                `<button class="setzen" data-wert="${text(w.betrag)}">einsetzen</button>`}
            </div>`;
        }).join('');
        bloecke.push(`<div class="block"><p>${text(b.titel)}</p>${reihen}</div>`);
      }
      if (bloecke.length) {
        teile.push(`<h4>${text(f.formular)}</h4>${bloecke.join('')}`);
      }
    }

    if (daten.summen && daten.summen.length && !filter) {
      const reihen = daten.summen.map((s) => `
        <div class="wert ${s.gerechnet ? 'rechnet' : ''}">
          <span class="bez">${text(s.ziffername || ('Ziffer ' + s.ziffer))}
            <span>${s.feld ? 'Feld ' + text(s.feld) + ' · ' : ''}Ziffer ${text(s.ziffer)}${
              s.gerechnet ? ' · <i class="warn">Kontrollzahl, nicht eintragen</i>' : ''}</span>
          </span>
          <span class="betrag">${text(s.betrag)}</span>
          ${s.gerechnet ? '' :
            `<button class="setzen" data-wert="${text(s.betrag)}">einsetzen</button>`}
        </div>`).join('');
      teile.push(`<h4>Summen je Ziffer</h4><div class="block">${reihen}</div>`);
    }

    inhalt.innerHTML = teile.join('')
      || '<div class="hinweis">Nichts gefunden.</div>';
    el.querySelector('.fort').textContent =
      `${gesetzt} von ${einzutragen} gesetzt`
      + (rechnet ? ` · ${rechnet} rechnet das Formular` : '');

    inhalt.querySelectorAll('.setzen').forEach((b) => {
      b.addEventListener('click', () => {
        const wert = b.dataset.wert || '';
        if (!letztesFeld) {
          b.textContent = 'erst ins Feld klicken';
          setTimeout(() => { b.textContent = 'einsetzen'; }, 1600);
          return;
        }
        einsetzen(letztesFeld, wert);
        const zeile = b.closest('.wert');
        const id = zeile?.dataset.id;
        if (id) { erledigt[id] = true; speichern(SCHLUESSEL_ERLEDIGT, erledigt); }
        zeichnen();
      });
    });
  }

  /* Die Datei einmal auswaehlen. Sie wird im Browser gelesen, nicht geladen —
   * kein Netzwerk, kein Dateizugriff der Erweiterung auf die Platte. */
  function dateiWaehlen() {
    const eingabe = document.createElement('input');
    eingabe.type = 'file';
    eingabe.accept = 'application/json,.json';
    eingabe.addEventListener('change', () => {
      const datei = eingabe.files?.[0];
      if (!datei) return;
      const leser = new FileReader();
      leser.onload = () => {
        try {
          const gelesen = JSON.parse(String(leser.result));
          if (!gelesen || !Array.isArray(gelesen.formulare)) {
            throw new Error('unerwarteter Aufbau');
          }
          daten = gelesen;
          speichern(SCHLUESSEL_DATEN, daten);
          zeichnen();
        } catch (e) {
          alert('Die Datei liess sich nicht lesen: ' + e.message);
        }
      };
      leser.readAsText(datei);
    });
    eingabe.click();
  }

  (async function start() {
    daten = (await lesen(SCHLUESSEL_DATEN)) || null;
    erledigt = (await lesen(SCHLUESSEL_ERLEDIGT)) || {};
    const lage = await lesen(SCHLUESSEL_LAGE);
    const el = panel();
    if (lage && lage.top) {
      el.style.top = lage.top; el.style.left = lage.left; el.style.right = 'auto';
    }
    zeichnen();
  })();
})();
