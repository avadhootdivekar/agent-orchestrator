/* Colour tokens (ARCHITECTURE §4, render/palette): one muted scheme, referenced by name from the drawers. */
(function (RPG) {
  'use strict';

  RPG.render = RPG.render || {};

  RPG.render.palette = Object.freeze({
    bg: '#14161c',
    floor: '#2a2f3a',
    floorAlt: '#2d323e',
    wall: '#3b4252',
    wallTop: '#4c566a',
    wallEdge: '#20242d',
    water: '#2b4a5e',
    door: '#7a5a3a',
    portal: '#7d6fb8',
    decor: '#3e5a46',
    player: '#6fb6c9',
    playerOutline: '#1b2a30',
    enemy: '#c46a5a',
    hazard: '#e0a458',
    shadow: '#000000',
    ui: '#d8dee9'
  });
})(globalThis.RPG = globalThis.RPG || {});
