/* 影序官网脚本：零依赖、零构建。
 *
 * 下载页与版本说明页都从 GitHub Releases API 取数据，**不硬编码版本号**，
 * 避免官网与真实资产不一致。API 不可用或尚无 Release 时显示「暂未发布」，不产生死链。
 */
(function () {
  'use strict';

  var REPO = 'new-yingxu/yingxu';
  var API = 'https://api.github.com/repos/' + REPO + '/releases';

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function fmtBytes(n) {
    if (!n && n !== 0) return '';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = 0;
    var v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return (i === 0 ? v : v.toFixed(1)) + ' ' + units[i];
  }

  function fmtDate(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0');
  }

  function platformOf(name) {
    var lower = name.toLowerCase();
    if (lower.endsWith('.dmg')) return 'macOS';
    if (lower.endsWith('.exe') || lower.endsWith('.msi')) return 'Windows';
    if (lower.endsWith('.appimage')) return 'Linux';
    return null;
  }

  function setStatus(kind, text) {
    var box = document.getElementById('dl-status');
    if (!box) return;
    box.className = 'dl-status ' + kind;
    box.textContent = text;
  }

  /* SHA256SUMS 是发布流程里的附加资产，存在就解析成 name -> hash 映射 */
  function loadSums(release) {
    var sums = release.assets.filter(function (a) { return a.name === 'SHA256SUMS'; })[0];
    if (!sums) return Promise.resolve({});
    return fetch(sums.browser_download_url, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (text) {
        var map = {};
        text.split('\n').forEach(function (line) {
          var m = line.trim().match(/^([a-f0-9]{64})\s+\*?(.+)$/i);
          if (m) map[m[2]] = m[1];
        });
        return map;
      })
      .catch(function () { return {}; });
  }

  function renderDownloads(release) {
    var host = document.getElementById('dl-list');
    if (!host) return;
    host.textContent = '';
    var installers = release.assets.filter(function (a) { return platformOf(a.name); });
    if (!installers.length) {
      setStatus('warn', '该版本尚未附带安装包（可能仍在构建中）。');
      return;
    }
    loadSums(release).then(function (sums) {
      installers.forEach(function (asset) {
        var card = el('div', 'card dl-item');
        var title = el('h3', null, platformOf(asset.name) + ' · ' + asset.name);
        card.appendChild(title);
        var meta = el('div', 'meta');
        meta.appendChild(el('span', null, fmtBytes(asset.size)));
        meta.appendChild(el('span', null, '下载 ' + (asset.download_count || 0) + ' 次'));
        card.appendChild(meta);

        var hash = sums[asset.name];
        if (hash) {
          var box = el('div', 'hash mono', hash);
          var copy = el('button', null, '复制');
          copy.type = 'button';
          copy.addEventListener('click', function () {
            navigator.clipboard && navigator.clipboard.writeText(hash);
            copy.textContent = '已复制';
            setTimeout(function () { copy.textContent = '复制'; }, 1500);
          });
          box.appendChild(copy);
          card.appendChild(box);
        } else {
          card.appendChild(el('p', 'muted', '校验值随 SHA256SUMS 资产一并发布，如缺失表示该版本未生成清单。'));
        }

        var link = el('a', 'btn', '下载');
        link.href = asset.browser_download_url;
        link.rel = 'noopener';
        card.appendChild(link);
        host.appendChild(card);
      });
    });
  }

  function renderChangelog(releases) {
    var host = document.getElementById('release-list');
    if (!host) return;
    host.textContent = '';
    if (!releases.length) {
      host.appendChild(el('p', 'muted', '暂未发布。'));
      return;
    }
    releases.forEach(function (r) {
      var block = el('div', 'release');
      var head = el('h3');
      head.appendChild(document.createTextNode(r.tag_name || r.name));
      if (r.prerelease) head.appendChild(el('span', 'badge warn', '测试版'));
      if (r.draft) head.appendChild(el('span', 'badge', '草稿'));
      head.appendChild(el('span', 'badge', fmtDate(r.published_at || r.created_at)));
      block.appendChild(head);
      var body = el('pre', null, (r.body || '（无发布说明）').trim());
      block.appendChild(body);
      host.appendChild(block);
    });
  }

  function fetchReleases() {
    return fetch(API + '?per_page=10', { headers: { Accept: 'application/vnd.github+json' } })
      .then(function (r) {
        if (r.status === 403 || r.status === 429) throw new Error('rate-limit');
        if (!r.ok) throw new Error('http-' + r.status);
        return r.json();
      });
  }

  function initDownload() {
    if (!document.getElementById('dl-status')) return;
    setStatus('', '正在读取 GitHub Releases 的最新版本…');
    fetchReleases()
      .then(function (list) {
        var visible = list.filter(function (r) { return !r.draft; });
        if (!visible.length) {
          setStatus('warn', '暂未发布：还没有可下载的正式版本。你可以在 GitHub Releases 查看构建草稿状态。');
          return;
        }
        var latest = visible[0];
        setStatus('ok', '最新版本 ' + (latest.tag_name || latest.name) + ' · 发布于 ' +
          fmtDate(latest.published_at || latest.created_at) +
          (latest.prerelease ? ' · 测试版（未签名，安装后需手动放行）' : ''));
        renderDownloads(latest);
      })
      .catch(function (err) {
        setStatus('warn', err && err.message === 'rate-limit'
          ? 'GitHub API 触发频率限制，请直接前往 GitHub Releases 页面查看最新版本。'
          : '暂时无法从 GitHub 读取版本信息，请直接前往 GitHub Releases 页面。');
      });
  }

  function initChangelog() {
    var host = document.getElementById('release-list');
    if (!host) return;
    fetchReleases()
      .then(function (list) { renderChangelog(list.filter(function (r) { return !r.draft; })); })
      .catch(function () { renderChangelog([]); });
  }

  function initNav() {
    var btn = document.querySelector('.nav-toggle');
    var links = document.getElementById('nav-links');
    if (!btn || !links) return;
    btn.addEventListener('click', function () {
      var open = links.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initNav();
    initDownload();
    initChangelog();
  });
})();
