async page => {
  // Generate TID for mobile_user endpoint
  const tid = await new Promise((resolve) => {
    let frameCount = document.querySelectorAll('[id^="loading-x-anim"]').length;
    const ensureFrames = async () => {
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
    
    return ensureFrames().then(() => {
      return new Promise((resolve) => {
        window.webpackChunk_twitter_responsive_web.push([['t' + Date.now()], {}, (req) => {
          req.e(59924).then(() => {
            const genFn = req(208932).default();
            Promise.resolve(genFn('/graphql/DuN4Qld4UROZ63wKFX8cfw/GetUserByScreenNameQuery', 'GET')).then(resolve).catch(() => resolve(null));
          }).catch(() => resolve(null));
        }]);
      });
    });
  });
  return JSON.stringify({ tid });
}