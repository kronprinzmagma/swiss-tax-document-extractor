/* Steuerwerte einsetzen — die Leiste im Seitenteil des Browsers (rechts).
 *
 * Der Grundsatz: die Erweiterung kennt die Felder des Formulars NICHT. Sie
 * muss es auch nicht. Du klickst in ein Eingabefeld, dann in der Leiste auf
 * „einsetzen" — der Wert landet in dem Feld, das zuletzt den Fokus hatte.
 * Damit funktioniert sie auch, wenn das Steueramt die Oberfläche umbaut, und
 * sie kann nichts an der falschen Stelle eintragen: du bestimmst die Stelle.
 *
 * Sie sendet nichts nach aussen. Kein fetch, kein XHR; Berechtigungen nur
 * `storage` und `sidePanel`. Den Wert reicht sie per Nachricht an das eigene
 * Seiten-Skript (seite.js) im offenen Tab. Die Werte kommen aus einer Datei, die du einmal auswählst, und
 * liegen danach im lokalen Speicher des Browsers.
 *
 * Erzeugt wird die Datei mit `make extension-daten`.
 */
(() => {
  'use strict';

  const SCHLUESSEL_DATEN = 'stx_daten';
  const SCHLUESSEL_ERLEDIGT = 'stx_erledigt';
  const SCHLUESSEL_OFFEN = 'stx_offen';

  let daten = null;
  let erledigt = {};      // id -> true
  let filter = '';
  // Welche Kategorie offen ist — immer nur eine (der Anwender 261028: „nur
  // Krankenkassenangaben auf einmal offen sehen"). Leer = alle zu.
  let offen = '';

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
  //
  // Die Leiste ist das Seitenteil des Browsers (rechts). Vorher lag sie auf
  // der Seite — erst darüber, dann daneben; beides liess das Formular nicht
  // schmaler werden, und man musste seitwärts scrollen (der Anwender 261028).

  function panel() {
    let el = document.getElementById('stx-panel');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'stx-panel';
    el.innerHTML = `
      <div class="kopf">
        <b>Steuerwerte</b>
        <span class="fort"></span>
        <button class="neu" title="steuerwerte.json neu wählen — nach make extension-daten">⟳</button>
        <button class="schliessen" title="Leiste ausblenden — das Symbol der Erweiterung öffnet sie wieder">✕</button>
      </div>
      <div class="suche"><input type="search" placeholder="suchen …"></div>
      <div class="meldung" hidden></div>
      <div class="inhalt"></div>
      <div class="fuss">Klick ins Formularfeld, dann „einsetzen“. Nichts verlässt den Rechner.</div>`;
    document.body.appendChild(el);
    el.querySelector('.neu').addEventListener('click', dateiWaehlen);
    // Das Seitenteil schliesst sich mit seinem Fenster; die Seite bekommt
    // die volle Breite zurück.
    el.querySelector('.schliessen').addEventListener('click', () => window.close());
    el.querySelector('.suche input').addEventListener('input', (e) => {
      filter = e.target.value.trim().toLowerCase();
      zeichnen();
    });
    return el;
  }

  function melden(text_) {
    const m = panel().querySelector('.meldung');
    m.textContent = text_;
    m.hidden = !text_;
    clearTimeout(melden.uhr);
    if (text_) melden.uhr = setTimeout(() => { m.hidden = true; }, 4000);
  }

  /* Den Wert an die offene Seite schicken. Das Seiten-Skript läuft nur auf
   * den ZH-Seiten; anderswo kommt keine Antwort — das wird gesagt. */
  async function senden(wert) {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab) return { ok: false, grund: 'kein_tab' };
    try {
      return await chrome.tabs.sendMessage(tab.id, { art: 'einsetzen', wert });
    } catch (e) {
      return { ok: false, grund: 'keine_seite' };
    }
  }

  const GRUND = {
    kein_feld: 'Erst ins Formularfeld klicken, dann „einsetzen“.',
    keine_seite: 'Keine ZHprivateTax-Seite offen (oder Seite nach dem Update neu laden).',
    kein_tab: 'Kein Tab gefunden.',
  };

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

  /* Beträge immer als 64'613.00 — auch aus einer älteren Datei, in der sie
   * noch ohne Apostroph standen (der Anwender 261028). Nur blosse Zahlen; alles
   * andere bleibt, wie es ist. */
  function chf(s) {
    const t = String(s == null ? '' : s).trim();
    if (!/^-?\d+(\.\d+)?$/.test(t)) return s;
    const [ganz, rest] = Number(t).toFixed(2).split('.');
    return ganz.replace(/\B(?=(\d{3})+(?!\d))/g, "'") + '.' + rest;
  }

  // Kennungen bleiben, wie sie sind — auch wenn sie nur aus Ziffern bestehen.
  const KEIN_BETRAG = /Nr\b|Nr\.|Bezeichnung|Staat|Währung|Zinssatz|Gläubiger|Zugehörigkeit|Bank|Arbeitgeber|Anstellung|Jahr/i;
  const feldwert = (x, v) => (KEIN_BETRAG.test(x.titel || '') ? v : chf(v));

  // Woher ein Feld kommt — in Worten, nicht als Farbe allein.
  const HERKUNFT = {
    ablage: 'in der Durchsicht geprüft',
    beleg: 'aus dem Beleg gelesen',
    regel: 'nach Regel aus dem Beleg gelesen',
    modell_bestaetigt: 'vom Modell gelesen, Zeile im Beleg gefunden',
    berechnet: 'berechnet',
    annahme: 'Annahme',
    leer: '',
  };

  function passt(w, block, formular) {
    if (!filter) return true;
    return [w.bezeichnung, w.unterscheidung, w.ziffer, w.ziffername, w.feld,
            block.titel, formular].join(' ').toLowerCase().includes(filter);
  }

  /* Eine Kategorie (Formular) als aufklappbarer Abschnitt mit eigenem
   * Zähler. Beim Suchen sind alle Treffer offen. */
  function kategorie(name, inhalt_, gesetzt_, einzutragen_) {
    const zahl = einzutragen_ ? `${gesetzt_}/${einzutragen_}` : '';
    const fertig = einzutragen_ && gesetzt_ === einzutragen_ ? ' fertig' : '';
    return `<details class="kat${fertig}" data-kat="${text(name)}"`
      + `${filter || offen === name ? ' open' : ''}>`
      + `<summary><span class="kname">${text(name)}</span>`
      + `<span class="kzahl">${zahl}</span></summary>${inhalt_}</details>`;
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
      const vorher = { einzutragen, gesetzt };
      const bloecke = [];
      for (const b of f.bloecke || []) {
        // Ein Eintrag der Online-Maske (Konto, Schuld): die Felder in der
        // Reihenfolge, in der ZHprivateTax sie abfragt, jedes mit Herkunft.
        // Lose Zeilen ohne Zusammenhang waren nicht zu gebrauchen — welcher
        // Zins zu welcher Hypothek, welcher Ertrag zu welchem Konto
        // (261024-jga, vom Nutzer gemeldet).
        if (Array.isArray(b.felder) && b.felder.length) {
          const text_ = [b.titel, b.person, b.maske, f.formular,
                         ...b.felder.map((x) => x.wert)].join(' ').toLowerCase();
          if (filter && !text_.includes(filter)) continue;
          const reihen = b.felder.map((x) => {
            const id = `feld|${b.maske}|${b.titel}|${b.person}|${x.titel}`;
            const fertig = !!erledigt[id];
            if (x.einsetzen) { einzutragen++; if (fertig) gesetzt++; }
            const warnung = !!x.warnung;
            // Ein Hinweis sagt mehr als die allgemeine Herkunft — dann nur er.
            const zeile = x.hinweis || HERKUNFT[x.herkunft] || '';
            return `
              <div class="wert ${fertig ? 'fertig' : ''} ${x.wert ? '' : 'leer'}"
                   data-id="${text(id)}">
                <span class="bez">${text(x.titel)}
                  <span>${warnung ? `<i class="warn">${text(zeile)}</i>` : text(zeile)}</span>
                </span>
                <span class="betrag">${x.wert ? text(feldwert(x, x.wert)) : '—'}</span>
                ${x.einsetzen ? `<button class="setzen" data-wert="${text(feldwert(x, x.einsetzen))}">einsetzen</button>` : ''}
              </div>`;
          }).join('');
          bloecke.push(
            `<div class="block eintrag"><p>${text(b.titel)}` +
            `<span class="zn">${text(b.maske)}${b.person ? ' · ' + text(b.person) : ''}</span></p>` +
            `${reihen}</div>`);
          continue;
        }
        const werte = (b.werte || []).filter((w) => passt(w, b, f.formular));
        if (!werte.length) continue;
        // Was das Lohnformular neben dem Betrag abfragt: Arbeitgeber und
        // Dauer der Anstellung. Die Daten sind nicht in der Durchsicht
        // bestaetigt, deshalb sichtbar gekennzeichnet — „im Beleg" heisst
        // woertlich verankert (261024-jga).
        const angaben = filter ? '' : (b.angaben || []).map((a) =>
          (a.werte || []).map((g) => {
            const hat = String(g.wert || '').trim() !== '';
            const id = `angabe|${a.quelle}|${g.titel}`;
            const fertig = !!erledigt[id];
            if (hat) { einzutragen++; if (fertig) gesetzt++; }
            return `
              <div class="wert ${fertig ? 'fertig' : ''} ${hat ? '' : 'leer'}"
                   data-id="${text(id)}">
                <span class="bez">${text(g.titel)}
                  <span>${g.herkunft === 'tabelle' ? 'Aussteller wie in der Tabelle'
                    : g.herkunft === 'belegt' ? 'im Beleg verankert'
                    : '<i class="warn">nicht verankert — im Beleg prüfen</i>'}</span>
                </span>
                <span class="betrag">${hat ? text(g.wert) : '—'}</span>
                ${hat ? `<button class="setzen" data-wert="${text(g.wert)}">einsetzen</button>`
                      : '<span class="warn">im Beleg nachsehen</span>'}
              </div>`;
          }).join('')).join('');
        const reihen = werte.map((w) => {
          const fertig = !!erledigt[w.id];
          // Kontrollzahl (ALV-Taggelder, Familienzulagen): aus dem Beleg,
          // aber nicht einzutragen — wie eine gerechnete Summe ohne Knopf.
          // Vorher fiel der Knopf auf den Betrag zurück (261025-hbv, B6).
          // Im Total oben enthalten (Prämien, Krankheitskosten — 261028):
          // Aufschlüsselung, kein eigener Eintrag.
          const ohneKnopf = w.gerechnet || w.kontrolle || w.im_total;
          if (w.gerechnet) { rechnet++; }
          else if (!w.kontrolle && !w.im_total) { einzutragen++; if (fertig) gesetzt++; }
          const zusatz = w.unterscheidung ? ` (${w.unterscheidung})` : '';
          const feld = w.feld ? `Feld ${w.feld} · ` : '';
          return `
            <div class="wert ${fertig ? 'fertig' : ''} ${w.im_total ? 'teil' : ohneKnopf ? 'rechnet' : ''}"
                 data-id="${text(w.id)}">
              <span class="bez">${text(w.bezeichnung + zusatz)}
                <span>${feld}Ziffer ${text(w.ziffer)}${
                  w.gerechnet ? ' · <i class="warn">rechnet das Formular selbst</i>'
                  : w.im_total ? ' · im Total oben enthalten'
                  : w.kontrolle ? ' · <i class="warn">nur zur Kontrolle, nicht eintragen</i>' : ''}</span>
                ${w.hinweis ? `<span class="${w.warnung ? 'warn' : ''}">${text(w.hinweis)}</span>` : ''}
                ${w.info ? `<span>${text(w.info)}</span>` : ''}
              </span>
              <span class="betrag">${text(chf(w.betrag))}</span>
              ${w.kontrolle || w.im_total ? '' : w.gerechnet || !(w.einsetzen || w.betrag) ? '' :
                `<button class="setzen" data-wert="${text(chf(w.einsetzen || w.betrag))}">einsetzen</button>`}
            </div>`;
        }).join('');
        bloecke.push(`<div class="block"><p>${text(b.titel)}</p>${angaben}${reihen}</div>`);
      }
      if (bloecke.length) {
        teile.push(kategorie(f.formular, bloecke.join(''),
                             gesetzt - vorher.gesetzt, einzutragen - vorher.einzutragen));
      }
    }

    if (daten.summen && daten.summen.length && !filter) {
      const reihen = daten.summen.map((s) => `
        <div class="wert ${s.gerechnet ? 'rechnet' : ''}">
          <span class="bez">${text(s.ziffername || ('Ziffer ' + s.ziffer))}
            <span>${s.feld ? 'Feld ' + text(s.feld) + ' · ' : ''}Ziffer ${text(s.ziffer)}${
              s.gerechnet ? ' · <i class="warn">Kontrollzahl, nicht eintragen</i>'
              : s.kontrolle ? ' · Total steht oben in der Kategorie' : ''}</span>
          </span>
          <span class="betrag">${text(chf(s.betrag))}</span>
          ${s.gerechnet || s.kontrolle ? '' :
            `<button class="setzen" data-wert="${text(chf(s.einsetzen || s.betrag))}">einsetzen</button>`}
        </div>`).join('');
      teile.push(kategorie('Summen je Ziffer', `<div class="block">${reihen}</div>`));
    }

    inhalt.innerHTML = teile.join('')
      || '<div class="hinweis">Nichts gefunden.</div>';
    // Eine Kategorie öffnen schliesst die anderen.
    inhalt.querySelectorAll('details.kat').forEach((d) => {
      d.addEventListener('toggle', () => {
        if (filter) return;            // beim Suchen bleiben alle Treffer offen
        if (d.open) {
          offen = d.dataset.kat;
          inhalt.querySelectorAll('details.kat').forEach((x) => { if (x !== d) x.open = false; });
        } else if (offen === d.dataset.kat) {
          offen = '';
        }
        speichern(SCHLUESSEL_OFFEN, offen);
      });
    });
    el.querySelector('.fort').textContent =
      `${gesetzt} von ${einzutragen} gesetzt`
      + (rechnet ? ` · ${rechnet} rechnet das Formular` : '');

    inhalt.querySelectorAll('.setzen').forEach((b) => {
      b.addEventListener('click', async () => {
        const wert = b.dataset.wert || '';
        const r = await senden(wert);
        if (!r || !r.ok) {
          melden(GRUND[r?.grund] || 'Nicht eingesetzt.');
          return;
        }
        melden('');
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
          const stand = gelesen.erzeugt ? ` (Stand ${gelesen.erzeugt})` : '';
          panel().querySelector('.fort').textContent += ` · neu geladen${stand}`;
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
    offen = (await lesen(SCHLUESSEL_OFFEN)) || '';
    panel();
    zeichnen();
  })();
})();
