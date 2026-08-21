async page => {
  const result = await page.evaluate(async () => {
    // Ensure SVG frames exist
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

    // Generate a single TID for the given path
    const urlParams = new URLSearchParams(window.location.search);
    const targetPath = window.__TX_PATH__ || '/i/api/graphql/DuN4Qld4UROZ63wKFX8cfw/GetUserByScreenNameQuery';
    const targetMethod = window.__TX_METHOD__ || 'GET';

    const tid = await new Promise((resolve) => {
      window.webpackChunk_twitter_responsive_web.push([['tid_' + Date.now()], {}, (req) => {
        req.e(59924).then(() => {
          const genFn = req(208932).default();
          Promise.resolve(genFn(targetPath, targetMethod)).then(resolve).catch(() => resolve(null));
        }).catch(() => resolve(null));
      }]);
    });

    return JSON.stringify({ tid, frameCount: document.querySelectorAll('[id^="loading-x-anim"]').length });
  });
  return result;
}