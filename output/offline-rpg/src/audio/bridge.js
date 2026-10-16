/**
 * Audio bridge: subscribes to the sim's event bus and plays the matching procedural sound. The mapping lives in
 * TABLE (INTEGRATION-PLAN section 4) so it is data that tests can read. Nothing is played while audio is muted or its
 * context failed. Listener errors are counted by the bus, so the bridge never throws into the sim.
 */
(function (RPG) {
  'use strict';

  RPG.audio = RPG.audio || {};

  const E = RPG.core.events.NAMES;
  const TEAM_MONSTER = 0;
  const TEAM_PLAYER = 1;
  // A critical hit on a monster raises the pitch by a tenth (plan section 4).
  const CRIT_PITCH = 1.1;
  // Player death uses the hurt sound at a low pitch so it reads as the final blow.
  const DEATH_PITCH = 0.6;

  const isPlayerHit = (p) => p.team === TEAM_PLAYER;
  const isMonsterHit = (p) => p.team === TEAM_MONSTER;
  const critPitch = (p) => ({ pitch: p.crit ? CRIT_PITCH : 1 });
  const deathPitch = () => ({ pitch: DEATH_PITCH });
  const soundFromPayload = (p) => p.name;
  const uiSoundOpts = (p) => ({ pitch: p.pitch, vol: p.vol });

  // One row per event. `match` (optional) filters on the payload, `sound` is a name or a function of the payload,
  // and `opts` (optional) returns the play() options. The attack sound has no event of its own: stage 3 emits
  // UI_SOUND {name: 'attack'}, which the UI_SOUND row passes through.
  const TABLE = Object.freeze([
    { event: E.HIT, match: isPlayerHit, sound: 'hurt' },
    { event: E.HIT, match: isMonsterHit, sound: 'hit', opts: critPitch },
    { event: E.KILL, sound: 'kill' },
    { event: E.PLAYER_DIED, sound: 'hurt', opts: deathPitch },
    { event: E.LEVEL_UP, sound: 'levelup' },
    { event: E.PICKUP, sound: 'pickup' },
    { event: E.SKILL_USED, sound: 'skill' },
    { event: E.BOSS_WARN, sound: 'bossWarn' },
    { event: E.BOSS_PHASE, sound: 'bossWarn' },
    { event: E.AREA_CHANGED, sound: 'ui' },
    { event: E.SAVE, sound: 'ui' },
    { event: E.LOAD, sound: 'ui' },
    { event: E.UI_SOUND, sound: soundFromPayload, opts: uiSoundOpts }
  ]);

  function audible(audio) {
    const s = audio.status();
    return !s.muted && !s.failed;
  }

  /** Subscribes every TABLE row to `events` and plays through `audio`. Returns a function that removes them all. */
  function attach(events, audio) {
    const offs = TABLE.map(function (row) {
      return events.on(row.event, function (payload) {
        const p = payload || {};
        if (row.match && !row.match(p)) return;
        if (!audible(audio)) return;
        const name = typeof row.sound === 'function' ? row.sound(p) : row.sound;
        audio.play(name, row.opts ? row.opts(p) : undefined);
      });
    });
    return function detach() {
      offs.forEach(function (off) { off(); });
    };
  }

  RPG.audio.bridge = { TABLE: TABLE, attach: attach };
})(globalThis.RPG);
