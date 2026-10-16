// Browser file I/O for saves (docs/INTEGRATION-PLAN.md 3.3): Blob download and FileReader import. The only DOM in persistence.
// Every DOM object comes from an injectable `env` ({document, URL, Blob, FileReader, setTimeout}) so tests run headless.
// Nothing here throws or writes a save slot: results are {ok:true,...} | {ok:false, code, error}.
(function (RPG) {
  'use strict';
  var P = RPG.persistence = RPG.persistence || {};
  var MAX_IMPORT_BYTES = P.MAX_BYTES || 2 * 1024 * 1024; // SAVE-SCHEMA import cap

  function fail(code, error) { return { ok: false, code: code, error: error }; }
  function msg(e) { return e && e.message ? e.message : String(e); }
  function envOf(env) {
    env = env || {};
    var g = globalThis;
    return {
      document: env.document || g.document, URL: env.URL || g.URL, Blob: env.Blob || g.Blob,
      FileReader: env.FileReader || g.FileReader, setTimeout: env.setTimeout || g.setTimeout
    };
  }

  // downloadSave(state, env?, opts?) -> {ok:true, name, bytes} | {ok:false, code, error}. `state` is a save body (snapshot output).
  function downloadSave(state, env, opts) {
    var e = envOf(env), url = null;
    try {
      if (!e.document || !e.URL || !e.Blob) return fail('unsupported', 'file download is not available in this environment');
      var text = P.exportJson(state, opts), name = P.exportFileName(state);
      var blob = new e.Blob([text], { type: 'application/json' });
      url = e.URL.createObjectURL(blob);
      var a = e.document.createElement('a');
      a.href = url; a.download = name; a.style.display = 'none';
      e.document.body.appendChild(a);
      a.click();
      e.document.body.removeChild(a);
      var revoke = url;
      if (typeof e.setTimeout === 'function') e.setTimeout(function () { e.URL.revokeObjectURL(revoke); }, 0);
      else e.URL.revokeObjectURL(revoke);
      return { ok: true, name: name, bytes: text.length };
    } catch (err) {
      if (url) { try { e.URL.revokeObjectURL(url); } catch (ignore) { /* best effort */ } }
      return fail(err && err.code ? err.code : 'download', msg(err));
    }
  }

  // Cheap pre-read checks on a File-like {name, type, size}. Returns an error result or null.
  function checkFile(file, maxBytes) {
    if (!file || typeof file !== 'object') return fail('nofile', 'no file selected');
    var name = String(file.name || ''), type = String(file.type || '');
    var jsonName = /\.json$/i.test(name);
    if (!(type === 'application/json' || (jsonName && (type === '' || type === 'text/plain' || type === 'text/json')))) {
      return fail('type', 'not a JSON save file: ' + (name || 'unnamed file'));
    }
    if (typeof file.size === 'number' && file.size > maxBytes) {
      return fail('size', 'file is too large (' + file.size + ' bytes, limit ' + maxBytes + ')');
    }
    return null;
  }

  // readSaveFile(file, cb, env?, opts?) -> void; cb(result) is called exactly once. On success result is the
  // importJson result ({ok:true, state, warnings, version}); the caller decides whether to write it to a slot.
  function readSaveFile(file, cb, env, opts) {
    var done = false;
    function finish(r) { if (done) return; done = true; cb(r); }
    var maxBytes = opts && opts.maxBytes > 0 ? opts.maxBytes : MAX_IMPORT_BYTES;
    try {
      var e = envOf(env), bad = checkFile(file, maxBytes);
      if (bad) return finish(bad);
      if (!e.FileReader) return finish(fail('unsupported', 'file import is not available in this environment'));
      var reader = new e.FileReader();
      reader.onload = function () {
        try {
          var text = reader.result;
          if (typeof text !== 'string') return finish(fail('read', 'file could not be read as text'));
          if (text.length > maxBytes) return finish(fail('size', 'file is too large (limit ' + maxBytes + ' bytes)'));
          finish(P.importJson(text));
        } catch (err) { finish(fail(err && err.code ? err.code : 'corrupt', msg(err))); }
      };
      reader.onerror = function () { finish(fail('read', 'could not read file' + (reader.error ? ': ' + msg(reader.error) : ''))); };
      reader.onabort = function () { finish(fail('read', 'file read was cancelled')); };
      reader.readAsText(file);
    } catch (err) {
      finish(fail('read', msg(err)));
    }
  }

  P.files = { MAX_IMPORT_BYTES: MAX_IMPORT_BYTES, downloadSave: downloadSave, readSaveFile: readSaveFile };
})(globalThis.RPG = globalThis.RPG || {});
