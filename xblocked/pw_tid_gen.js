async page => {
  // Placeholders replaced by tid_gen.py before execution:
  //   "window.__TX_PATH__" -> JSON.stringify(path)
  //   "window.__TX_METHOD__" -> JSON.stringify(method)
  const tid = await page.evaluate(({ path, method }) => {
    return new Promise((resolve) => {
      const ensureFrames = async () => {
        let frameCount = document.querySelectorAll('[id^="loading-x-anim"]').length;
        if (frameCount < 4) {
          const resp = await fetch('/home', { credentials: 'include' });
          const rawHtml = await resp.text();
          const container = document.createElement('div');
          container.style.display = 'none';
          for (let i = 0; i < 4; i++) {
            const id = `loading-x-anim-${i}`;
            const startIdx = rawHtml.indexOf(`id="${id}"`);
            if (startIdx !== -1) {
              let start = rawHtml.lastIndexOf('<svg', startIdx);
              let end = rawHtml.indexOf('</svg>', startIdx);
              if (end !== -1) end += 6;
              if (start !== -1 && end !== -1) {
                const temp = document.createElement('div');
                temp.innerHTML = rawHtml.substring(start, end);
                if (temp.firstChild) container.appendChild(temp.firstChild);
              }
            }
          }
          document.body.appendChild(container);
        }
      };
      ensureFrames().then(() => {
        window.webpackChunk_twitter_responsive_web.push([['t' + Date.now()], {}, (req) => {
          req.e(59924).then(() => {
            const genFn = req(208932).default();
            Promise.resolve(genFn(path, method)).then(resolve).catch(() => resolve(null));
          }).catch(() => resolve(null));
        }]);
      });
    });
  }, {
    path: window.__TX_PATH__,
    method: window.__TX_METHOD__,
  });

  return JSON.stringify({ tid });
}