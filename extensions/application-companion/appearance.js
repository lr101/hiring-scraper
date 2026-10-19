let preference = "system";
function apply() {
  document.documentElement.dataset.theme =
    preference === "dark" ||
    (preference === "system" &&
      matchMedia("(prefers-color-scheme: dark)").matches)
      ? "dark"
      : "light";
}
async function load() {
  const { radiusVault } = await chrome.storage.local.get("radiusVault");
  preference = radiusVault?.settings?.theme || "system";
  apply();
}
void load();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", apply);
chrome.storage.onChanged.addListener((change, area) => {
  if (area === "local" && change.radiusVault) void load();
});
