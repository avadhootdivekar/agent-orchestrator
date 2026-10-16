'use strict';

// A recording stand-in for CanvasRenderingContext2D. Drawers are headless here: they are checked by the calls they
// make and the style active at each fill/stroke, never by pixels. `saveDepth` tracks save/restore balance, and
// `minSaveDepth` records the lowest depth reached, so an extra restore() shows up as a negative value.

/** Returns { ctx, calls } where `calls` is the ordered list of recorded operations. */
function recordingContext() {
  const calls = [];
  const ctx = {
    fillStyle: null, strokeStyle: null, globalAlpha: 1, lineWidth: 1, font: '', textAlign: '', textBaseline: '',
    saveDepth: 0,
    minSaveDepth: 0,
    fillRect(x, y, w, h) { calls.push({ op: 'fillRect', x, y, w, h, style: this.fillStyle, alpha: this.globalAlpha }); },
    strokeRect(x, y, w, h) { calls.push({ op: 'strokeRect', x, y, w, h, style: this.strokeStyle, alpha: this.globalAlpha }); },
    fillText(text) { calls.push({ op: 'fillText', text: String(text) }); },
    arc(cx, cy, r, a0, a1) { calls.push({ op: 'arc', cx, cy, r, a0, a1 }); },
    ellipse(cx, cy, rx, ry, rot, a0, a1) { calls.push({ op: 'ellipse', cx, cy, rx, ry, rot, a0, a1 }); },
    moveTo(x, y) { calls.push({ op: 'moveTo', x, y }); },
    lineTo(x, y) { calls.push({ op: 'lineTo', x, y }); },
    beginPath() { calls.push({ op: 'beginPath' }); },
    closePath() { calls.push({ op: 'closePath' }); },
    fill() { calls.push({ op: 'fill', style: this.fillStyle, alpha: this.globalAlpha }); },
    stroke() { calls.push({ op: 'stroke', style: this.strokeStyle, lineWidth: this.lineWidth, alpha: this.globalAlpha }); },
    createRadialGradient(x0, y0, r0, x1, y1, r1) {
      const stops = [];
      calls.push({ op: 'createRadialGradient', x0, y0, r0, x1, y1, r1, stops });
      return { addColorStop(offset, color) { stops.push({ offset, color }); } };
    },
    save() { this.saveDepth++; calls.push({ op: 'save' }); },
    restore() { this.saveDepth--; this.minSaveDepth = Math.min(this.minSaveDepth, this.saveDepth); calls.push({ op: 'restore' }); }
  };
  return { ctx, calls };
}

module.exports = { recordingContext };
