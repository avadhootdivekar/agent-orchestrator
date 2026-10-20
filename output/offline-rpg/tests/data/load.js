'use strict';

// Same loader as the other test areas: runs the real classic scripts in one vm context.
// Re-exported here so tests/data/ follows the tests/<area>/load.js convention.
module.exports = require('../world/load.js');
