// Service worker: its only job is to make the toolbar icon open the side panel.
// All the real work (native messaging port, chat UI, page tools) lives in the
// side panel page itself (sidepanel.js), which is an extension context with the
// nativeMessaging / scripting / tabs permissions it needs.

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((e) => console.error("setPanelBehavior failed", e));
});
