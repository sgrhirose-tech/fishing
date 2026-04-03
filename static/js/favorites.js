(function () {
  var _KEY = 'tsuricast_favorites';
  var _MAX = 20;

  function _load() {
    try { return JSON.parse(localStorage.getItem(_KEY) || '[]'); }
    catch (e) { return []; }
  }
  function _save(arr) {
    try { localStorage.setItem(_KEY, JSON.stringify(arr)); return true; }
    catch (e) { return false; }
  }
  function isFavorite(slug) { return _load().indexOf(slug) !== -1; }
  function addFavorite(slug) {
    var arr = _load().filter(function (s) { return s !== slug; });
    arr.unshift(slug);
    _save(arr.slice(0, _MAX));
  }
  function removeFavorite(slug) { _save(_load().filter(function (s) { return s !== slug; })); }

  /* ---- スポットページ用 ---- */
  window.initFavoriteButton = function (slug) {
    var container = document.getElementById('fav-btn-container');
    if (!container) return;
    try { localStorage.getItem(_KEY); } catch (e) { return; } // 利用不可なら何もしない

    var btn = document.createElement('button');
    btn.id = 'fav-btn';

    function update() {
      if (isFavorite(slug)) {
        btn.textContent = '★ お気に入り済み';
        btn.classList.add('is-favorite');
      } else {
        btn.textContent = '☆ お気に入りに追加';
        btn.classList.remove('is-favorite');
      }
    }

    btn.addEventListener('click', function () {
      if (isFavorite(slug)) { removeFavorite(slug); } else { addFavorite(slug); }
      update();
    });

    update();
    container.appendChild(btn);
  };

  /* ---- TOPページ用（地図上パネル） ---- */
  window.renderFavoritesSection = function (allSpots) {
    var slugs = _load();
    if (!slugs.length) return;

    var panel = document.getElementById('favorites-panel');
    var list  = document.getElementById('favorites-list');
    if (!panel || !list) return;

    var shown = 0;
    slugs.forEach(function (slug) {
      if (shown >= 5) return;
      var s = allSpots.find ? allSpots.find(function (x) { return x.slug === slug; })
                            : (function () { for (var i = 0; i < allSpots.length; i++) { if (allSpots[i].slug === slug) return allSpots[i]; } })();
      if (!s) return;
      var a = s.area || {};
      if (!a.pref_slug || !a.area_slug || !a.city_slug) return;
      var url = '/' + a.pref_slug + '/' + a.area_slug + '/' + a.city_slug + '/' + s.slug;
      var li = document.createElement('li');
      var anchor = document.createElement('a');
      anchor.href = url;
      anchor.textContent = s.name || slug;
      li.appendChild(anchor);
      list.appendChild(li);
      shown++;
    });

    if (shown > 0) panel.style.display = '';
  };
})();
