async function load(name) {
  return import(`./plugins/${name}.js`);
}
