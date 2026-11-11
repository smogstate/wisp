// Runs tests/wow_stub.lua in the fengari Lua VM, for machines without a Lua
// interpreter. Same arguments. With real Lua 5.1 available, set WISP_LUA to it
// and tests/test_mvp.py uses that instead.
//   npm install   (once)
const path = require("path");
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = require("fengari");

const script = path.join(__dirname, "wow_stub.lua");
const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);
lua.lua_newtable(L);
[script, ...process.argv.slice(2)].forEach((value, i) => {
  lua.lua_pushstring(L, to_luastring(value));
  lua.lua_rawseti(L, -2, i);
});
lua.lua_setglobal(L, to_luastring("arg"));
// This VM has no io library: the one thing the stub needs from the disk besides loading Lua files.
lua.lua_pushcfunction(L, (state) => {
  let bytes = 0;
  try { bytes = require("fs").statSync(to_jsstring(lua.lua_tostring(state, 1))).size; } catch (e) {}
  lua.lua_pushboolean(state, bytes > 0);
  return 1;
});
lua.lua_setglobal(L, to_luastring("FILE_HAS_BYTES"));
if (lauxlib.luaL_loadfile(L, to_luastring(script)) || lua.lua_pcall(L, 0, 0, 0)) {
  console.error("FAIL: " + to_jsstring(lua.lua_tostring(L, -1)));
  process.exit(1);
}
