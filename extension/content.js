(() => {
  const BUTTON_CLASS = "weibo-inbox-save";
  const STATUS_LINK_RE = /^https?:\/\/(?:www\.)?weibo\.com\/(?:u\/)?\d+\/[A-Za-z0-9]+(?:[?#].*)?$/;
  const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

  function normalizeSpace(value) {
    return String(value || "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n[ \t]+/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function absoluteUrl(value) {
    try { return new URL(value, location.href).href; } catch (_error) { return ""; }
  }

  function largestImageUrl(value) {
    const url = absoluteUrl(value);
    if (!url) return "";
    return url
      .replace(/\/((?:thumbnail)|(?:thumb\d+)|(?:bmiddle)|(?:mw\d+)|(?:orj\d+)|(?:small)|(?:square))\//, "/large/")
      .replace(/([?&])size=[^&]+/, "$1size=large");
  }

  function findSourceUrl(article) {
    for (const link of article.querySelectorAll("a[href]")) {
      const url = absoluteUrl(link.getAttribute("href"));
      if (STATUS_LINK_RE.test(url)) return url.split("?")[0].split("#")[0];
    }
    const pageUrl = location.href.split("?")[0].split("#")[0];
    return STATUS_LINK_RE.test(pageUrl) ? pageUrl : "";
  }

  function findAuthor(article) {
    for (const link of article.querySelectorAll('a[href*="/u/"], a[href*="weibo.com/"]')) {
      const text = normalizeSpace(link.textContent);
      if (text && text.length <= 40 && !/^\d/.test(text)) return text;
    }
    return "微博作者";
  }

  function findPublishedText(article, sourceUrl) {
    const link = [...article.querySelectorAll("a[href]")]
      .find(item => absoluteUrl(item.getAttribute("href")).split("?")[0] === sourceUrl);
    return normalizeSpace(link?.getAttribute("title") || link?.textContent || "");
  }

  function collectImages(article) {
    const result = new Set();
    const add = value => {
      const url = largestImageUrl(value);
      if (!url || !/^https?:/.test(url) || /face\.t\.sinajs\.cn|\/avatar\//i.test(url)) return;
      result.add(url);
    };
    for (const image of article.querySelectorAll("img")) {
      const source = image.currentSrc || image.src || image.getAttribute("data-src");
      const width = image.naturalWidth || image.width || Number(image.getAttribute("width")) || 0;
      const height = image.naturalHeight || image.height || Number(image.getAttribute("height")) || 0;
      if (/sinaimg\.cn|wx\d+\.sinaimg/i.test(source || "") && (width >= 80 || height >= 80 || !width || !height)) add(source);
    }
    for (const link of article.querySelectorAll('a[href*="sinaimg.cn"]')) add(link.href);
    for (const node of article.querySelectorAll('[style*="background-image"]')) {
      const match = node.style.backgroundImage.match(/url\(["']?(.*?)["']?\)/i);
      if (match) add(match[1]);
    }
    return [...result].slice(0, 18);
  }

  function collectVideos(article) {
    const directUrls = new Set();
    const pageUrls = new Set();
    const addDirect = value => {
      const url = absoluteUrl(value);
      if (/^https?:/i.test(url) && /\.mp4(?:[?#]|$)/i.test(url)) directUrls.add(url);
    };
    for (const video of article.querySelectorAll("video")) {
      addDirect(video.currentSrc || video.src);
      for (const source of video.querySelectorAll("source")) addDirect(source.src);
    }
    for (const link of article.querySelectorAll('a[href*="video.weibo.com/show"]')) {
      const url = absoluteUrl(link.getAttribute("href"));
      if (url) pageUrls.add(url);
    }
    const videos = [
      ...[...directUrls].map(url => ({ url, pageUrl: "" })),
      ...[...pageUrls].map(pageUrl => ({ url: "", pageUrl }))
    ].slice(0, 8);
    const hasVideo = Boolean(
      videos.length ||
      article.querySelector("video, [class*='_videoBox_'], [class*='_videobox_'], [aria-label='视频播放器']")
    );
    return { hasVideo, videos };
  }

  function textFromContainer(container) {
    if (!container) return "";
    const clone = container.cloneNode(true);
    clone.querySelectorAll([
      `.${BUTTON_CLASS}`, "script", "style", "svg", "video", "audio", "canvas", "input", "textarea",
      "button", '[role="button"]', '[title="更多"]', '[aria-label="更多"]',
      'a[href*="video.weibo.com/show"]', '[class*="_videoBox_"]', '[class*="_videobox_"]'
    ].join(",")).forEach(node => node.remove());
    for (const image of clone.querySelectorAll("img")) {
      const alt = normalizeSpace(image.getAttribute("alt"));
      if (/^\[[^\]]+\]$/.test(alt)) image.replaceWith(document.createTextNode(alt));
      else image.remove();
    }
    const unwanted = /^(?:Download|Download Setting|添加|更多|展开|展開|转发|轉發|評論|评论|讚|赞|播放视频|播放影片|\d+(?:\.\d+)?万?次观看)$/i;
    return normalizeSpace(clone.innerText)
      .replace(/[\u200b-\u200d\u2060\ufeff]+/g, "")
      .split("\n")
      .map(line => line.trim())
      .filter(line => line && !unwanted.test(line))
      .join("\n")
      .trim();
  }

  function extractCleanText(article) {
    const original = textFromContainer(article.querySelector(".wbpro-feed-ogText"));
    const repost = textFromContainer(article.querySelector(".wbpro-feed-reText"));
    const parts = [];
    if (original) parts.push(original);
    if (repost) parts.push(`轉發內容：\n${repost}`);
    if (parts.length) return parts.join("\n\n");
    const fallback = textFromContainer(
      article.querySelector(".wbpro-feed-content") || article.querySelector('[class*="_wbtext_"]')
    );
    if (fallback) return fallback;
    if (collectVideos(article).hasVideo) return "（此微博只有影片，沒有可提取的文字內容。）";
    if (collectImages(article).length) return "（此微博只有圖片，沒有可提取的文字內容。）";
    return "";
  }

  function makeTitle(author, text) {
    const lines = text.split("\n").map(item => item.trim()).filter(Boolean);
    const first = lines.find(line => line !== author && !/^\d{1,4}[-年]/.test(line)) || "微博";
    const title = `${author}：${first}`;
    return title.length > 90 ? `${title.slice(0, 89)}…` : title;
  }

  async function maybeExpand(article) {
    const expander = [...article.querySelectorAll("a, span, div")].find(node => {
      const text = normalizeSpace(node.textContent);
      return text.length <= 12 && /^(?:\.\.\.|…)?展开(?:全文)?$/.test(text);
    });
    if (expander) { expander.click(); await wait(500); }
  }

  async function extractClip(article) {
    await maybeExpand(article);
    const sourceUrl = findSourceUrl(article);
    const author = findAuthor(article);
    const text = extractCleanText(article);
    const video = collectVideos(article);
    return {
      schemaVersion: 2,
      id: sourceUrl.match(/\/([A-Za-z0-9]+)$/)?.[1] || sourceUrl,
      title: makeTitle(author, text),
      author,
      published: findPublishedText(article, sourceUrl),
      sourceUrl,
      text,
      images: collectImages(article),
      hasVideo: video.hasVideo,
      videos: video.videos,
      capturedAt: new Date().toISOString()
    };
  }

  function showToast(message, kind = "info") {
    document.querySelector(".weibo-inbox-toast")?.remove();
    const toast = document.createElement("div");
    toast.className = `weibo-inbox-toast weibo-inbox-toast-${kind}`;
    toast.textContent = message;
    document.documentElement.appendChild(toast);
    setTimeout(() => toast.remove(), kind === "error" ? 9000 : 4500);
  }

  function sendMessage(message) {
    return new Promise(resolve => chrome.runtime.sendMessage(message, resolve));
  }

  async function saveArticle(article, button) {
    if (button.dataset.busy === "1") return;
    button.dataset.busy = "1";
    button.textContent = "暫存中…";
    try {
      const clip = await extractClip(article);
      if (!clip.sourceUrl || !clip.text) throw new Error("沒有取得可保存的微博內容");
      const response = await sendMessage({ type: "CAPTURE_WEIBO", clip });
      if (!response?.ok) throw new Error(response?.error || "暫存失敗");
      const duplicate = Boolean(response.payload?.duplicate);
      button.textContent = duplicate ? "已在收件匣" : "已暫存";
      button.classList.add("weibo-inbox-saved");
      const videoMessage = response.payload?.hasVideo
        ? (response.payload?.videoCount ? `影片已下載 ${response.payload.videoCount} 個。` : "已標記影片；若無法取得直連，請從原始連結觀看。")
        : "";
      showToast(
        duplicate ? "這條微博已在本機收件匣，不會重複保存。" : `已保存到本機。${videoMessage}\n需要時從擴充功能面板同步到 Evernote。`,
        "success"
      );
    } catch (error) {
      button.textContent = "存入收件匣";
      showToast(error?.message || String(error), "error");
    } finally {
      button.dataset.busy = "0";
    }
  }

  function bindArticle(article) {
    if (!(article instanceof HTMLElement)) return;
    const sourceUrl = findSourceUrl(article);
    const existing = article.querySelector(`:scope > .${BUTTON_CLASS}`);
    if (existing) {
      if (existing.dataset.sourceUrl !== sourceUrl) {
        existing.dataset.sourceUrl = sourceUrl;
        existing.textContent = "存入收件匣";
        existing.classList.remove("weibo-inbox-saved");
      }
      return;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = BUTTON_CLASS;
    button.dataset.sourceUrl = sourceUrl;
    button.textContent = "存入收件匣";
    button.title = "保存乾淨文字、時間、原始連結、圖片與可下載影片；稍後增量匯入 Evernote";
    button.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      saveArticle(article, button);
    });
    article.appendChild(button);
  }

  function scan() { document.querySelectorAll("article").forEach(bindArticle); }
  let scanTimer = 0;
  new MutationObserver(() => {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, 120);
  }).observe(document.documentElement, { childList: true, subtree: true });
  scan();
})();
