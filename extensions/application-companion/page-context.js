// Non-sensitive form identity shared by confirmation and the final injected fill.
(() => {
  if (globalThis.applicationPageContext) return;
  const sanitizeURL = (value) => {
    try {
      const url = new URL(value, location.href);
      url.username = "";
      url.password = "";
      url.search = "";
      url.hash = "";
      return url.href;
    } catch {
      return "";
    }
  };
  globalThis.applicationPageContext = {
    sanitizeURL,
    identity: () =>
      JSON.stringify({
        title: document.title.slice(0, 300),
        headings: [
          ...document.querySelectorAll(
            "h1,h2,[data-job-id],[data-requisition-id]",
          ),
        ]
          .slice(0, 30)
          .map((el) => [
            el.textContent.trim().slice(0, 180),
            el.getAttribute("data-job-id"),
            el.getAttribute("data-requisition-id"),
          ]),
        forms: [...document.forms]
          .slice(0, 10)
          .map((form) => [
            sanitizeURL(form.action),
            [...form.querySelectorAll("input,textarea,select")]
              .slice(0, 100)
              .map((el) => [el.tagName, el.type, el.name, el.id]),
          ]),
      }),
  };
})();
