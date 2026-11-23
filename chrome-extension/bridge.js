// Runs only on the Job Hunter page (127.0.0.1:8765). Lets the Batch apply page open job tabs
// in this Chrome window through the extension (avoids the popup blocker).
document.documentElement.setAttribute("data-jh-ext", "1");
window.addEventListener("message", e => {
  if (e.source !== window || !e.data) return;
  if (e.data.jh === "ping") window.postMessage({ jh: "ready" }, "*");
  if (e.data.jh === "open") chrome.runtime.sendMessage({ type: "open", id: e.data.id, url: e.data.url });
});
window.postMessage({ jh: "ready" }, "*");
