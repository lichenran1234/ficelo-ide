/* Home is a saved navigation preference; the server's served root stays fixed. */
import { api } from "./api.js";
import { interpretPath } from "./util.js";

export async function validateHome(value, { root = "/", home = "/" } = {}) {
  const path = interpretPath(value, root, home);
  if (!path) throw new Error("Enter a folder path.");
  const info = await api.info(path);
  if (!info.is_dir) throw new Error("Home must be a folder.");
  // A directory can exist without being readable by this server.
  await api.tree(info.path, false);
  return info.path;
}
