// Clean fixture: local code only, SVG namespace string is allowed.
(function (RPG) {
  const NS = 'http://www.w3.org/2000/svg';
  const canvas = document.createElementNS(NS, 'svg');
  RPG.render = { canvas };
})(window.RPG = window.RPG || {});
