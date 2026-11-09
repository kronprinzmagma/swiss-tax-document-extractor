/* Steuerwerte einsetzen — der Teil IN der ZHprivateTax-Seite.
 *
 * Er zeichnet nichts. Er merkt sich das Eingabefeld, in das du zuletzt
 * geklickt hast, und setzt dort den Wert ein, den das Seitenteil schickt
 * (der Anwender 261028: die Leiste über oder neben der Seite liess das Formular nicht
 * schmaler werden — man musste seitwärts scrollen; das Seitenteil des
 * Browsers macht die Seite wirklich schmaler).
 *
 * Sendet nichts nach aussen; die einzige Nachricht kommt vom Seitenteil der
 * eigenen Erweiterung.
 */
(() => {
  'use strict';

  let letztesFeld = null; // das Eingabefeld mit dem letzten Fokus

  // --- Das Feld merken, bevor der Klick in die Leiste den Fokus wegnimmt ---
  //
  // `focusin` steigt auf, `blur` nicht — deshalb dieser Weg. Die Leiste selbst
  // wird ausgenommen, sonst merkt sie sich ihr eigenes Suchfeld.
  function merken(e) {
    const el = e.target;
    if (!el || typeof el.closest !== 'function') return;
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


  chrome.runtime.onMessage.addListener((nachricht, absender, antwort) => {
    if (absender.id !== chrome.runtime.id || nachricht?.art !== 'einsetzen') return;
    if (!letztesFeld || !letztesFeld.isConnected) {
      antwort({ ok: false, grund: 'kein_feld' });
      return;
    }
    einsetzen(letztesFeld, String(nachricht.wert ?? ''));
    antwort({ ok: true });
  });
})();
