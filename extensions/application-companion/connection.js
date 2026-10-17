import { validateSettings } from "./vault.js";
const status = document.querySelector("#radius-status");
let connected;
const show = (text, error = false) => {
  status.textContent = text;
  status.classList.toggle("error", error);
};
async function send(type, extra = {}) {
  const response = await chrome.runtime.sendMessage({ type, ...extra });
  if (!response?.ok)
    throw new Error(response?.error || "Radius did not respond.");
  return response.result;
}
function render(state) {
  connected = state.settings;
  document.querySelector("#radius-origin").value =
    connected?.origin || document.querySelector("#radius-origin").value;
  document.querySelector("#radius-api").value =
    connected?.api_origin && connected.api_origin !== connected.origin
      ? connected.api_origin
      : "";
  document.querySelector("#radius-connect").textContent = connected
    ? "Reconnect Radius"
    : "Connect Radius";
  document.querySelector("#radius-refresh").hidden = !connected;
  document.querySelector("#radius-disconnect").hidden = !connected;
  const picker = document.querySelector("#radius-profile");
  picker.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Choose your profile";
  picker.append(placeholder);
  for (const profile of state.profiles || []) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent =
      profile.name + (profile.has_cv ? "" : " · Save CV text in Radius");
    picker.append(option);
  }
  picker.value = String(connected?.profile_id || "");
  document.querySelector("#radius-profile-label").hidden = !connected;
  if (connected)
    show(
      `${state.total ?? state.jobs?.length ?? 0} discovered jobs synced${state.truncated ? " (catalog limit reached; prepare an unlisted job from its Radius page)" : ""}. ` +
        (connected.profile_id
          ? connected.cv_updated_at
            ? "Original CV connected. Prepared CVs will sync when you return from ChatGPT."
            : "Open a job in Radius, choose Prepare application, and save your CV text for this profile."
          : "Choose a Radius profile to continue."),
    );
}
document.querySelector("#radius-connect").onclick = async () => {
  const button = document.querySelector("#radius-connect");
  button.disabled = true;
  try {
    const settings = validateSettings({
      origin: document.querySelector("#radius-origin").value,
      api_origin:
        document.querySelector("#radius-api").value ||
        document.querySelector("#radius-origin").value,
      profile_id: connected?.profile_id || null,
    });
    // Request only the chosen origins, during the user's click gesture.
    const granted = await chrome.permissions.request({
      origins: [...new Set([settings.origin, settings.api_origin])].map(
        (origin) => origin + "/*",
      ),
    });
    if (!granted)
      throw new Error(
        "Radius access was not granted. Use Connect Radius to try again.",
      );
    show("Connecting and syncing your discovered jobs…");
    render(await send("connect-radius", { settings }));
  } catch (error) {
    show(error.message, true);
  } finally {
    button.disabled = false;
  }
};
document.querySelector("#radius-refresh").onclick = async () => {
  show("Syncing discovered jobs…");
  try {
    render(await send("refresh-radius"));
  } catch (error) {
    show(error.message, true);
  }
};
document.querySelector("#radius-profile").onchange = async (event) => {
  if (!event.target.value) return;
  try {
    render(
      await send("select-profile", { profileId: Number(event.target.value) }),
    );
  } catch (error) {
    show(error.message, true);
  }
};
document.querySelector("#radius-disconnect").onclick = async () => {
  try {
    const previous = connected;
    await send("disconnect-radius");
    if (previous)
      await chrome.permissions.remove({
        origins: [...new Set([previous.origin, previous.api_origin])].map(
          (o) => o + "/*",
        ),
      });
    render(await send("overview"));
    show("Radius disconnected.");
  } catch (error) {
    show(error.message, true);
  }
};
try {
  render(await send("overview"));
} catch (error) {
  show(error.message, true);
}
