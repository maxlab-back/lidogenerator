"use strict";
/* Лидогенератор — интерфейс. Вкладки: Поиск · База · Автопоиск; карточка компании; вход. */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const PAGE = 150;
const COUNTRY = {RU: "Россия", BY: "Беларусь"};
const SOCIAL = {telegram: "TG", whatsapp: "WA", viber: "Viber", vk: "VK", instagram: "IG", ok: "OK"};
const LS_FORM = "leadgen.form.v2", LS_JOB = "leadgen.job.v1", LS_TAB = "leadgen.tab.v1", LS_THEME = "leadgen.theme";

const S = {
  started: false, meta: null, caps: {}, user: "", auth: false, balance: null,
  countries: new Set(["RU"]), regions: new Set(), cities: new Set(), openRegs: new Set(), coverage: "centers",
  job: null, logFrom: 0, version: -1, lastLeadsAt: 0, pollTimer: null,
  rows: [], view: [], final: false, threshold: 0.5, title: "", runId: null,
  sort: {k: "score", dir: -1}, limit: PAGE,
  base: {rows: [], total: 0, sort: "score", dir: "desc", status: "", runId: null, runTitle: "", sel: new Set(), counts: {}},
};

/* ===================== утилиты ===================== */
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function safeUrl(u){ return /^https?:\/\//i.test(u || "") ? u : ""; }
function safeSocial(u){ return /^(https?:\/\/|viber:\/\/|whatsapp:\/\/)/i.test(u || "") ? u : ""; }
function toast(msg){ const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2800); }
function lsGet(k){ try { return JSON.parse(localStorage.getItem(k) || "null"); } catch { return null; } }
function lsSet(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }
function lines(s, commas){ return (commas ? s.replace(/,/g, "\n") : s).split("\n").map(x => x.trim()).filter(Boolean); }
function fmtPhone(p){
  let m = p.match(/^\+7(\d{3})(\d{3})(\d{2})(\d{2})$/); if (m) return `+7 (${m[1]}) ${m[2]}-${m[3]}-${m[4]}`;
  m = p.match(/^\+375(\d{2})(\d{3})(\d{2})(\d{2})$/); if (m) return `+375 (${m[1]}) ${m[2]}-${m[3]}-${m[4]}`;
  return p;
}
function phoneKind(p){
  if (p.startsWith("+7")){ const d = p.slice(2); return d[0] === "9" ? "моб" : d.startsWith("800") ? "8-800" : "гор"; }
  if (p.startsWith("+375")){ const d = p.slice(4); return ["25","29","33","44"].includes(d.slice(0, 2)) ? "моб" : d.startsWith("80") ? "8-80x" : "гор"; }
  return "";
}
function plural(n, a, b, c){ n = Math.abs(n) % 100; const d = n % 10; return (n > 10 && n < 20) ? c : d === 1 ? a : (d >= 2 && d <= 4) ? b : c; }
function fmtTime(sec){ const m = Math.floor(sec / 60), s = sec % 60; return m ? `${m} мин ${s} с` : `${s} с`; }
function fmtDate(s){ return (s || "").replace("T", " ").slice(0, 16); }
function fmtTs(ts){ if (!ts) return "—"; const d = new Date(ts * 1000), p = n => String(n).padStart(2, "0"); return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`; }
function money(v){ return v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(3)}`; }
function debounce(fn, ms){ let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function statusName(k){ return (S.meta && S.meta.statuses[k]) || k || "—"; }

async function api(path, body){
  const opt = body === undefined ? {credentials: "same-origin"}
    : {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  if (r.status === 401 && path !== "/api/login"){ showLogin(); throw new Error("Нужно войти"); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}

/* ===================== вход ===================== */
function showLogin(msg){
  $("#appRoot").hidden = true; $("#login").hidden = false;
  $("#lErr").textContent = msg || ""; setTimeout(() => $("#lUser").focus(), 50);
}
$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault();
  try { await api("/api/login", {user: $("#lUser").value, password: $("#lPass").value}); $("#lPass").value = ""; $("#login").hidden = true; start(); }
  catch (err) { $("#lErr").textContent = err.message; }
});

async function boot(){
  initTheme();
  let me;
  try { me = await api("/api/me"); }
  catch (err) { document.body.innerHTML = `<p style="padding:20px">Сервер не отвечает: ${esc(err.message)}. Запусти <code>python app.py</code>.</p>`; return; }
  if (me.auth && !me.user) return showLogin();
  start();
}

async function start(){
  S.meta = await api("/api/meta");
  S.caps = S.meta.caps; S.user = S.meta.user; S.auth = S.meta.auth;
  $("#appRoot").hidden = false;
  if (!S.started){
    S.started = true;
    renderHeader(); initForm(); bindResults(); bindBase(); bindAuto(); bindHistory(); bindDrawer();
    $("#tabs").addEventListener("click", e => { const b = e.target.closest("button[data-tab]"); if (b) switchTab(b.dataset.tab); });
  }
  switchTab(new URLSearchParams(location.search).get("tab") || lsGet(LS_TAB) || "search");
  loadBalance(); refreshBaseCount();
  const cur = await api("/api/jobs/current").catch(() => ({}));
  const last = cur.id || lsGet(LS_JOB);
  if (last) attach(last, true);
}

/* Тема: «Авто» следует за системой, выбор запоминается. Первичная установка —
   в <head>, здесь только переключение и подсветка активной кнопки. */
function applyTheme(mode){
  const dark = mode === "dark" || (mode !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  for (const b of $$("#theme button")) b.classList.toggle("on", b.dataset.themeSet === mode);
}

function initTheme(){
  let mode = "auto";
  try { mode = new URLSearchParams(location.search).get("theme") || localStorage.getItem(LS_THEME) || "auto"; } catch {}
  applyTheme(mode);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    let m = "auto";
    try { m = localStorage.getItem(LS_THEME) || "auto"; } catch {}
    if (m === "auto") applyTheme("auto");
  });
  $("#theme").addEventListener("click", e => {
    const b = e.target.closest("[data-theme-set]");
    if (!b) return;
    try { localStorage.setItem(LS_THEME, b.dataset.themeSet); } catch {}
    applyTheme(b.dataset.themeSet);
  });
}

function renderHeader(){
  const c = S.caps;
  const chip = (on, label, title) => `<span class="hchip ${on ? "" : "off"}" title="${esc(title)}">${label}</span>`;
  $("#chips").innerHTML = [
    chip(true, `Поиск: <b>${esc(c.backend)}</b>`, "Бэкенд поиска сайтов Google"),
    chip(c.serper, "Google Карты", "SERPER_API_KEY"),
    chip(c.yandex, "Яндекс", "YANDEX_SEARCH_API_KEY + YANDEX_FOLDER_ID"),
    chip(c.apify, "Apify", "APIFY_TOKEN — Яндекс Карты и 2ГИС"),
    chip(c.ai, "ИИ", `ANTHROPIC_API_KEY · ${S.meta.ai_model}`),
    chip(c.dadata, "DaData", "DADATA_API_KEY"),
    chip(c.crm.length > 0, `CRM${c.crm.length ? ": <b>" + esc(c.crm.join(", ")) + "</b>" : ""}`, "BITRIX24_WEBHOOK или AMOCRM_HOST + AMOCRM_TOKEN"),
    `<span class="hchip" id="chipBalance">Serper: <b>…</b></span>`,
  ].join("");
  $("#userBox").innerHTML = S.auth ? `<span class="muted">${esc(S.user)}</span> <button class="linkbtn" id="btnLogout">выйти</button>` : "";
  if (S.auth) $("#btnLogout").addEventListener("click", async () => { await api("/api/logout", {}).catch(() => {}); location.reload(); });
  // золотая вкладка «Ключи», пока не подключено главное: без Serper нет карт, без Claude — ИИ-проверки
  const keysTab = $("#tabs button[data-tab=keys]");
  const missing = [!c.serper && "Serper", !c.ai && "Claude"].filter(Boolean);
  keysTab.classList.toggle("hot", missing.length > 0);
  keysTab.title = missing.length ? `Не подключено: ${missing.join(", ")} — зайди сюда первым делом`
                                 : "Ключи API подключены";
  for (const el of $$("[data-auth]")) el.hidden = !S.auth;
  for (const el of $$("[data-crm]")) el.hidden = !c.crm.length;
}

async function loadBalance(){
  const b = await api("/api/balance").catch(() => ({balance: null}));
  S.balance = b.balance;
  const chip = $("#chipBalance");
  chip.innerHTML = `Serper: <b>${b.balance == null ? "—" : b.balance}</b>`;
  const low = b.balance != null && b.balance < 200;
  chip.classList.toggle("low", low);
  chip.title = b.balance === 0 ? "Кредиты Serper закончились: сайты ищутся через DuckDuckGo, Google Карты пропускаются"
    : low ? "Кредиты Serper заканчиваются — пополни на serper.dev" : "Остаток кредитов Serper";
  estimate();
}

function switchTab(t){
  if (!["search", "base", "auto", "keys"].includes(t)) t = "search";
  for (const b of $$("#tabs button")) b.classList.toggle("on", b.dataset.tab === t);
  for (const name of ["search", "base", "auto", "keys"]) $("#tab-" + name).hidden = t !== name;
  lsSet(LS_TAB, t);
  if (t === "base") loadBase();
  if (t === "auto") loadAuto();
  if (t === "keys") loadKeys();
}

/* ===================== ключи API ===================== */
function capsKey(c){ return [c.serper, c.ai, c.apify, c.dadata, c.yandex, c.telegram, c.google_cse, c.crm.join()].join("|"); }

async function loadKeys(){
  let d;
  try { d = await api("/api/keys"); } catch (err) { return toast(err.message); }
  const notReady = d.services.filter(s => !s.ready).length;
  $("#keysCount").textContent = notReady ? `${d.services.length - notReady}/${d.services.length}` : "";
  $("#keysList").innerHTML = d.services.map(keyCard).join("");
}

function keyCard(s){
  const fields = s.fields.map(f => `<div>
    <label class="f">${esc(f.label)}</label>
    <input type="${f.secret ? "password" : "text"}" data-key="${esc(f.env)}" autocomplete="off"
           placeholder="${f.set ? esc(f.value) + " — сохранён, вставь новый чтобы заменить" : "не задан"}"></div>`).join("");
  return `<div class="panel keycard ${s.ready ? "on" : ""}" data-svc="${esc(s.id)}">
    <div class="keyhead"><span class="t">${esc(s.title)}</span>
      <span class="badge">${s.ready ? "подключён" : "не подключён"}</span></div>
    <p>${esc(s.gives)}</p>
    <p class="sub">${esc(s.price)} · <a href="${esc(s.url)}" target="_blank" rel="noopener">где взять</a></p>
    <div class="keyfields">${fields}</div>
    <div class="acts">
      <button class="btn sm primary" data-act="save">Сохранить и проверить</button>
      ${s.ready ? `<button class="btn sm" data-act="test">Проверить</button>
                   <button class="btn sm danger" data-act="clear">Убрать</button>` : ""}
      <span class="keymsg"></span>
    </div></div>`;
}

async function keyAction(card, act){
  const id = card.dataset.svc;
  const msg = card.querySelector(".keymsg");
  const show = (cls, text) => { msg.className = "keymsg " + cls; msg.textContent = text; };
  const before = capsKey(S.caps);
  try {
    if (act === "clear"){
      if (!confirm("Убрать ключи этого сервиса? Источник перестанет работать.")) return;
      const values = {};
      for (const inp of card.querySelectorAll("input[data-key]")) values[inp.dataset.key] = "";
      show("wait", "убираю…");
      S.caps = (await api("/api/keys", {values})).caps;
    } else if (act === "save"){
      const values = {};
      for (const inp of card.querySelectorAll("input[data-key]")) if (inp.value.trim()) values[inp.dataset.key] = inp.value.trim();
      if (!Object.keys(values).length) return show("bad", "поля пустые — вставь ключ");
      show("wait", "сохраняю…");
      S.caps = (await api("/api/keys", {values})).caps;
      for (const inp of card.querySelectorAll("input[data-key]")) inp.value = "";
    }
    if (act !== "clear"){
      show("wait", "проверяю ключ живым запросом…");
      const r = await api("/api/keys/test", {id});
      S.caps = r.caps;
      show(r.ok === true ? "ok" : r.ok === null ? "wait" : "bad", (r.ok === true ? "✓ " : r.ok === null ? "" : "✗ ") + r.message);
    }
  } catch (err) { return show("bad", err.message); }
  if (capsKey(S.caps) !== before){
    show(msg.className.includes("bad") ? "bad" : "ok", msg.textContent + " · обновляю страницу…");
    setTimeout(() => location.reload(), 1500);
  } else {
    loadKeys().then(() => {});
  }
}

async function refreshBaseCount(){
  const d = await api("/api/companies/query", {limit: 1}).catch(() => null);
  if (d) $("#baseCount").textContent = d.total || "";
}

/* ===================== форма поиска ===================== */
const CHECKS = ["src_maps_google", "src_maps_yandex", "src_maps_2gis", "src_web_google", "src_web_yandex", "src_web_telegram",
  "opt_crawl", "opt_ai", "opt_legal", "opt_check_email", "opt_use_cache", "opt_notify", "stdMinus"];
const FIELDS = ["sphere", "keywords", "context", "minus", "custom", "perQuery", "mapsPages", "apifyMax", "aiMax", "maxSites", "threshold", "creditLimit"];
const chk = id => { const el = $("#" + id); return el.checked && !el.disabled; };

function spec(){
  const minus = lines($("#minus").value, true);
  if ($("#stdMinus").checked) for (const m of S.meta.common_minus) if (!minus.includes(m)) minus.push(m);
  const sph = $("#sphere");
  return {
    sphere: sph.value ? sph.selectedOptions[0].textContent : "",
    keywords: lines($("#keywords").value, false),
    context: $("#context").value.trim(),
    minus,
    countries: [...S.countries],
    regions: [...S.regions].filter(r => S.countries.has(r.split(":")[0])),
    coverage: S.coverage,
    custom_cities: [...new Set([...S.cities, ...lines($("#custom").value, true)])],
    maps_google: chk("src_maps_google"), maps_yandex: chk("src_maps_yandex"), maps_2gis: chk("src_maps_2gis"),
    web_google: chk("src_web_google"), web_yandex: chk("src_web_yandex"), web_telegram: chk("src_web_telegram"),
    crawl: chk("opt_crawl"), ai: chk("opt_ai"), legal: chk("opt_legal"), check_email: chk("opt_check_email"),
    use_cache: chk("opt_use_cache"), notify: chk("opt_notify"),
    per_query: +$("#perQuery").value, maps_pages: +$("#mapsPages").value, apify_max: +$("#apifyMax").value,
    ai_max: +$("#aiMax").value, max_sites: +$("#maxSites").value || 300, threshold: +$("#threshold").value,
    credit_limit: +$("#creditLimit").value || 0,
  };
}

function saveForm(){
  const f = {countries: [...S.countries], regions: [...S.regions], cities: [...S.cities], coverage: S.coverage};
  for (const id of FIELDS) f[id] = $("#" + id).value;
  for (const id of CHECKS) f[id] = $("#" + id).checked;
  lsSet(LS_FORM, f);
}

function loadForm(f){
  if (!f) return;
  for (const id of FIELDS) if (f[id] !== undefined && f[id] !== null) $("#" + id).value = f[id];
  for (const id of CHECKS) if (typeof f[id] === "boolean" && !$("#" + id).disabled) $("#" + id).checked = f[id];
  if (Array.isArray(f.countries) && f.countries.length) S.countries = new Set(f.countries.filter(c => S.meta.countries[c]));
  if (Array.isArray(f.regions)) S.regions = new Set(f.regions);
  if (Array.isArray(f.cities)) S.cities = new Set(f.cities);
  if (f.coverage) S.coverage = f.coverage;
}

function isKnownCity(name){
  return Object.values(S.meta.countries).some(c => c.regions.some(r => r.cities.includes(name)));
}

/* параметры прошлого поиска / автопоиска (spec с сервера) -> форма */
function specToForm(sp){
  const std = S.meta.common_minus;
  const sphere = S.meta.spheres.find(s => s.title === sp.sphere);
  const f = {
    sphere: sphere ? sphere.id : "", keywords: (sp.keywords || []).join("\n"), context: sp.context || "",
    minus: (sp.minus || []).filter(m => !std.includes(m)).join(", "), stdMinus: std.every(m => (sp.minus || []).includes(m)),
    countries: sp.countries, regions: sp.regions, coverage: sp.coverage,
    cities: (sp.custom_cities || []).filter(isKnownCity),
    custom: (sp.custom_cities || []).filter(x => !isKnownCity(x)).join(", "),
    perQuery: String(sp.per_query ?? 20), mapsPages: String(sp.maps_pages ?? 1), apifyMax: String(sp.apify_max ?? 50),
    aiMax: String(sp.ai_max ?? 200), maxSites: String(sp.max_sites ?? 300), threshold: String(sp.threshold ?? 0.5),
  };
  const map = {src_maps_google: "maps_google", src_maps_yandex: "maps_yandex", src_maps_2gis: "maps_2gis",
    src_web_google: "web_google", src_web_yandex: "web_yandex", src_web_telegram: "web_telegram", opt_crawl: "crawl", opt_ai: "ai", opt_legal: "legal",
    opt_check_email: "check_email", opt_use_cache: "use_cache", opt_notify: "notify"};
  for (const [id, k] of Object.entries(map)) if (typeof sp[k] === "boolean") f[id] = sp[k];
  loadForm(f);
  renderCountries(); renderRegions(); renderCoverage(); saveForm(); estimate();
}

function initForm(){
  const c = S.caps;
  $("#sphere").insertAdjacentHTML("beforeend", S.meta.spheres.map(s => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join(""));
  $("#stdMinusList").textContent = "(" + S.meta.common_minus.join(", ") + ")";
  $("#backendHint").textContent = c.backend === "serper" ? " — Serper, 1 кредит за 10 ссылок" : ` — ${c.backend}, бесплатно`;
  $("#aiHint").textContent = ` — ${S.meta.ai_model}; отсекает каталоги и «не тех»`;
  const need = {serper: "нет SERPER_API_KEY", apify: "нет APIFY_TOKEN", yandex: "нет ключа Яндекса", ai: "нет ANTHROPIC_API_KEY",
    dadata: "нет DADATA_API_KEY", telegram: "нет TELEGRAM_BOT_TOKEN"};
  for (const el of $$("label.check[data-cap]")){
    const cap = el.dataset.cap;
    if (!c[cap]){
      el.classList.add("disabled"); const inp = el.querySelector("input"); inp.checked = false; inp.disabled = true;
      el.querySelector(".t").insertAdjacentHTML("afterend", `<span class="nokey">${need[cap] || "нет ключа"}</span>`);
    }
  }
  if (!c.ai){ $("#btnSuggest").disabled = true; $("#btnSuggest").title = need.ai; }
  loadForm(lsGet(LS_FORM));

  $("#sphere").addEventListener("change", () => {
    const p = S.meta.spheres.find(s => s.id === $("#sphere").value);
    if (p){ $("#keywords").value = p.keywords.join("\n"); $("#context").value = p.context; $("#minus").value = p.minus.join(", "); }
    onFormChange();
  });
  for (const id of ["keywords", "context", "minus", "custom", "maxSites"]) $("#" + id).addEventListener("input", onFormChange);
  for (const id of [...CHECKS, "perQuery", "mapsPages", "apifyMax", "aiMax", "threshold"]) $("#" + id).addEventListener("change", onFormChange);
  $("#countries").addEventListener("click", e => {
    const b = e.target.closest(".country"); if (!b) return;
    const cc = b.dataset.cc;
    if (S.countries.has(cc)){ if (S.countries.size === 1) return toast("Нужна хотя бы одна страна"); S.countries.delete(cc); }
    else S.countries.add(cc);
    renderCountries(); renderRegions(); onFormChange();
  });
  $("#regSearch").addEventListener("input", renderRegions);
  $("#reglist").addEventListener("change", e => {
    if (e.target.type !== "checkbox") return;
    const city = e.target.dataset.city;
    if (city){
      // город и «регион целиком» — разные режимы, поэтому не складываем их
      e.target.checked ? S.cities.add(city) : S.cities.delete(city);
      for (const [cc, c] of Object.entries(S.meta.countries))
        for (const r of c.regions)
          if (r.cities.includes(city)) S.regions.delete(`${cc}:${r.name}`);
    } else {
      const key = e.target.value;
      if (e.target.checked){
        S.regions.add(key);
        const [cc, name] = [key.split(":")[0], key.slice(key.indexOf(":") + 1)];
        const reg = (S.meta.countries[cc].regions || []).find(r => r.name === name);
        for (const x of (reg ? reg.cities : [])) S.cities.delete(x);
      } else S.regions.delete(key);
    }
    renderRegions(); onFormChange();
  });
  $("#reglist").addEventListener("click", e => {
    const open = e.target.closest("[data-open]");
    if (open){
      S.openRegs.has(open.dataset.open) ? S.openRegs.delete(open.dataset.open) : S.openRegs.add(open.dataset.open);
      return renderRegions();
    }
    const all = e.target.dataset.all, none = e.target.dataset.none;
    if (!all && !none) return;
    e.preventDefault();
    const cc = all || none, q = normQ($("#regSearch").value);
    for (const r of S.meta.countries[cc].regions){
      if (q && !normQ(r.name + " " + r.cities.join(" ")).includes(q)) continue;
      all ? S.regions.add(`${cc}:${r.name}`) : S.regions.delete(`${cc}:${r.name}`);
    }
    renderRegions(); onFormChange();
  });
  $("#regClear").addEventListener("click", () => { S.regions.clear(); S.cities.clear(); renderRegions(); onFormChange(); });
  $("#coverage").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; S.coverage = b.dataset.v; renderCoverage(); onFormChange(); });
  $("#btnStart").addEventListener("click", startJob);
  $("#btnStop").addEventListener("click", stopJob);
  $("#btnSuggest").addEventListener("click", suggest);
  $("#btnSchedule").addEventListener("click", () => {
    const f = $("#schedForm"); f.hidden = !f.hidden;
    if (!f.hidden && !$("#schedTitle").value) $("#schedTitle").value = lines($("#keywords").value, false)[0] || "";
  });
  $("#btnSchedSave").addEventListener("click", saveSchedule);
  renderCountries(); renderRegions(); renderCoverage();
}

const normQ = s => s.trim().toLowerCase().replace(/ё/g, "е");

function renderCountries(){
  $("#countries").innerHTML = Object.entries(S.meta.countries).map(([cc, c]) =>
    `<button type="button" class="country ${S.countries.has(cc) ? "on" : ""}" data-cc="${cc}">${esc(c.name)}</button>`).join("");
}

function renderRegions(){
  const q = normQ($("#regSearch").value);
  let html = "";
  for (const [cc, c] of Object.entries(S.meta.countries)){
    if (!S.countries.has(cc)) continue;
    const regs = c.regions.filter(r => !q || normQ(r.name + " " + r.cities.join(" ")).includes(q));
    if (!regs.length) continue;
    html += `<div class="reggroup">${esc(c.name)} · ${regs.length}<span>
      <button class="linkbtn" type="button" data-all="${cc}">все</button> · <button class="linkbtn" type="button" data-none="${cc}">снять</button></span></div>`;
    for (const r of regs){
      const key = `${cc}:${r.name}`;
      const whole = S.regions.has(key);
      const picked = r.cities.filter(x => S.cities.has(x));
      const open = S.openRegs.has(key);
      const sub = picked.length
        ? `выбрано: ${esc(picked.join(", "))}`
        : esc(r.cities.slice(0, 4).join(", ") + (r.cities.length > 4 ? ` +${r.cities.length - 4}` : ""));
      html += `<div class="regrow">
        <label class="reg"><input type="checkbox" value="${esc(key)}" ${whole ? "checked" : ""}>
          <span><span class="n">${esc(r.name)}</span><br><span class="c ${picked.length ? "picked" : ""}">${sub}</span></span></label>
        ${r.cities.length > 1 ? `<button class="cityx ${open ? "on" : ""}" type="button" data-open="${esc(key)}"
            title="Выбрать отдельные города">${r.cities.length} ${plural(r.cities.length, "город", "города", "городов")} ${open ? "▾" : "▸"}</button>` : ""}
      </div>`;
      if (open) html += `<div class="citylist">` + r.cities.map(x =>
        `<label class="city ${whole ? "off" : ""}"><input type="checkbox" data-city="${esc(x)}"
           ${S.cities.has(x) ? "checked" : ""} ${whole ? "disabled" : ""}><span>${esc(x)}</span></label>`).join("") + `</div>`;
    }
  }
  $("#reglist").innerHTML = html || `<div class="reg muted">Ничего не нашлось — впиши город в «Свои города»</div>`;
  renderRegCount();
}

function renderRegCount(){
  const n = [...S.regions].filter(r => S.countries.has(r.split(":")[0])).length;
  const c = S.cities.size;
  const parts = [];
  if (n) parts.push(`${n} ${plural(n, "регион", "региона", "регионов")} целиком`);
  if (c) parts.push(`${c} ${plural(c, "город", "города", "городов")} отдельно`);
  $("#regCount").textContent = parts.length ? "Выбрано: " + parts.join(" · ")
    : "Ничего не выбрано — поиск по стране целиком";
}
function renderCoverage(){ for (const b of $("#coverage").children) b.classList.toggle("on", b.dataset.v === S.coverage); }
function onFormChange(){ saveForm(); estimate(); }

let estTimer = null;
function estimate(){
  clearTimeout(estTimer);
  estTimer = setTimeout(async () => {
    if (!S.meta) return;
    const sp = spec();
    if (!sp.keywords.length){ $("#estimate").innerHTML = `<span class="muted">Впиши ключевые слова, выбери сферу или нажми «Подобрать ИИ»</span>`; return; }
    try {
      const e = await api("/api/estimate", sp);
      const parts = [`<b>${e.tasks}</b> ${plural(e.tasks, "запрос", "запроса", "запросов")} · ${e.cities} ${plural(e.cities, "город", "города", "городов")}`];
      const bal = e.balance != null ? e.balance : S.balance;
      const short = e.credits && bal != null && e.credits > bal;
      if (e.credits) parts.push(`Serper до <b class="${short ? "warn" : ""}">${e.credits}</b> кр.` +
        (bal != null ? ` <span class="muted">(есть ${bal})</span>` : ""));
      if (e.yandex_calls) parts.push(`Яндекс до <b>${e.yandex_calls}</b> запр.`);
      if (e.apify_usd) parts.push(`Apify до <b>${money(e.apify_usd)}</b>`);
      if (e.ai_usd) parts.push(`ИИ до <b>${money(e.ai_usd)}</b>`);
      if (sp.use_cache) parts.push(`<span class="muted">повторы из кеша бесплатно</span>`);
      let html = parts.join(" · ");
      if (short){
        const covered = e.covered_tasks != null ? e.covered_tasks : 0;
        html += `<div class="estwarn">Кредитов не хватит: хватит примерно на <b>${covered}</b> ${plural(covered, "запрос", "запроса", "запросов")}` +
          ` из ${e.tasks}. Дальше Google Карты отключатся, а сайты пойдут через ${e.free_backend || "DuckDuckGo"} —` +
          ` лидов и телефонов будет заметно меньше. Пополнить: <a href="https://serper.dev" target="_blank" rel="noopener">serper.dev</a></div>`;
      } else if (e.credits && bal != null && bal - e.credits < 100){
        html += `<div class="estwarn">После поиска останется около <b>${bal - e.credits}</b> кр. — пора пополнять.</div>`;
      } else if (e.limited_tasks != null){
        html += `<div class="hint">Потолок ${sp.credit_limit} кр.: на Serper пройдут первые ~<b>${e.limited_tasks}</b> из ${e.tasks} запросов, дальше — ${e.free_backend || "DuckDuckGo"} без Google Карт.</div>`;
      }
      $("#estimate").innerHTML = html;
    } catch (err) { $("#estimate").textContent = err.message; }
  }, 250);
}

async function suggest(){
  const btn = $("#btnSuggest");
  const sph = $("#sphere");
  const description = $("#context").value.trim() || $("#keywords").value.trim() || (sph.value ? sph.selectedOptions[0].textContent : "");
  if (!description) return toast("Опиши в «Контексте», кого ищем, или выбери сферу");
  btn.disabled = true; btn.textContent = "ИИ думает…";
  try {
    const r = await api("/api/ai/suggest", {description, keywords: lines($("#keywords").value, false),
      sphere: sph.value ? sph.selectedOptions[0].textContent : "", countries: [...S.countries].map(c => COUNTRY[c])});
    const cur = lines($("#keywords").value, false);
    const merged = [...new Set([...cur, ...r.queries])];
    $("#keywords").value = merged.join("\n");
    if (!$("#context").value.trim() && r.context) $("#context").value = r.context;
    $("#minus").value = [...new Set([...lines($("#minus").value, true), ...r.minus])].join(", ");
    onFormChange();
    toast(`ИИ добавил ${merged.length - cur.length} запросов и ${r.minus.length} минус-слов · ${money(r.cost)}`);
  } catch (err) { toast(err.message); }
  finally { btn.disabled = false; btn.textContent = "✨ Подобрать ИИ"; }
}

async function saveSchedule(){
  const sp = spec();
  if (!sp.keywords.length) return toast("В поиске нет ключевых слов");
  try {
    await api("/api/schedules", {title: $("#schedTitle").value.trim() || sp.keywords[0], spec: sp, every_hours: +$("#schedEvery").value});
    $("#schedForm").hidden = true; toast("Автопоиск сохранён"); switchTab("auto");
  } catch (err) { toast(err.message); }
}

/* ===================== задача ===================== */
async function startJob(){
  const sp = spec();
  if (!sp.keywords.length) return toast("Впиши хотя бы одну ключевую фразу");
  if (!(sp.maps_google || sp.maps_yandex || sp.maps_2gis || sp.web_google || sp.web_yandex || sp.web_telegram)) return toast("Включи хотя бы один источник");
  try { const {id} = await api("/api/jobs", sp); S.title = sp.keywords[0]; attach(id, true); }
  catch (err) { toast(err.message); }
}

async function stopJob(){
  if (!S.job) return;
  try { await api(`/api/jobs/${S.job}/cancel`, {}); toast("Останавливаю… сохраню то, что уже найдено"); }
  catch (err) { toast(err.message); }
}

function showResults(){
  $("#empty").hidden = true; $("#stats").hidden = false; $("#results").hidden = false;
}

function attach(id, fresh){
  S.job = id; S.logFrom = 0; S.version = -1; S.lastLeadsAt = 0; S.runId = id;
  if (fresh){ S.rows = []; S.limit = PAGE; $("#log").textContent = ""; }
  lsSet(LS_JOB, id);
  $("#viewBanner").hidden = true; $("#progress").hidden = false; $("#pFiles").textContent = "";
  showResults(); poll();
}

async function poll(){
  clearTimeout(S.pollTimer);
  if (!S.job) return;
  let st;
  try { st = await api(`/api/jobs/${S.job}?log_from=${S.logFrom}`); }
  catch (err) {
    lsSet(LS_JOB, null); S.job = null; setRunning(false);
    $("#progress").hidden = true;
    if (!S.rows.length){ $("#empty").hidden = false; $("#stats").hidden = true; $("#results").hidden = true; }
    return;
  }
  S.threshold = st.threshold; S.title = st.title;
  const running = st.status === "running";
  setRunning(running);
  $("#pStage").textContent = st.stage + (st.total && running ? ` — ${st.done} из ${st.total}` : "");
  const c = st.costs, meta = [fmtTime(st.elapsed)];
  if (c.serper) meta.push(`Serper ${c.serper} кр.`);
  if (c.yandex) meta.push(`Яндекс ${c.yandex} запр.`);
  if (c.apify_usd) meta.push(`Apify ${money(c.apify_usd)}`);
  if (c.ai_usd) meta.push(`ИИ ${money(c.ai_usd)}`);
  if (c.cache_hits) meta.push(`из кеша ${c.cache_hits}`);
  if (st.user) meta.push(st.user);
  $("#pMeta").textContent = meta.join(" · ");
  const pct = st.total ? Math.round(st.done / st.total * 100) : 3;
  const bar = $("#pBar"); bar.querySelector("i").style.width = (running ? pct : 100) + "%";
  bar.classList.toggle("done", st.status === "done"); bar.classList.toggle("err", st.status === "error");
  if (st.log.length){
    const pre = $("#log"), atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 30;
    pre.textContent += (pre.textContent ? "\n" : "") + st.log.join("\n");
    if (atBottom) pre.scrollTop = pre.scrollHeight;
    S.logFrom = st.log_next;
  }
  if (st.error) $("#pFiles").textContent = "Ошибка: " + st.error;
  else if (st.files.length) $("#pFiles").textContent = "Сохранено: " + st.files[0];
  const now = Date.now();
  if (st.version !== S.version && (!running || now - S.lastLeadsAt > 2500)){
    S.lastLeadsAt = now;
    const d = await api(`/api/jobs/${S.job}/leads`).catch(() => null);
    if (d){ S.version = d.version; S.rows = d.leads; S.final = d.final; renderResults(); }
  }
  if (running) S.pollTimer = setTimeout(poll, 1000);
  else { refreshBaseCount(); loadBalance(); }
}

function setRunning(on){ $("#btnStart").hidden = on; $("#btnStop").hidden = !on; }

/* ===================== таблица результатов ===================== */
function bucket(s){ return s >= S.threshold ? "s-hi" : s >= S.threshold - 0.15 ? "s-mid" : "s-lo"; }
function socialKey(k, u){
  let s = u || ""; try { s = decodeURIComponent(s); } catch {}
  s = s.toLowerCase();
  if (k === "whatsapp" || k === "viber") return k + ":" + s.replace(/\D/g, "").slice(-10);
  return k + ":" + s.replace(/^https?:\/\/(www\.|m\.)?/, "").split(/[?#]/)[0].replace(/\/$/, "").replace("vk.ru/", "vk.com/").replace("telegram.me/", "t.me/");
}
function socialsHtml(soc){
  const seen = new Set();
  return Object.entries(soc || {}).flatMap(([k, links]) => (links || []).filter(u => {
    const key = socialKey(k, u); if (seen.has(key)) return false; seen.add(key); return true;
  }).slice(0, 2).map(u => {
    const h = safeSocial(u); return h ? `<a class="soc soc-${esc(k)}" href="${esc(h)}" target="_blank" rel="noopener noreferrer" title="${esc(u)}">${esc(SOCIAL[k] || k)}</a>` : "";
  })).join("") || `<span class="muted">—</span>`;
}
function hostOf(u){ try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } }
function phonesHtml(ph){
  return (ph || []).slice(0, 4).map(p => `<a href="tel:${esc(p)}">${esc(fmtPhone(p))}</a> <span class="pk">${phoneKind(p)}</span>`).join("<br>")
    + ((ph || []).length > 4 ? `<div class="sub">+${ph.length - 4}</div>` : "") || `<span class="muted">—</span>`;
}
function emailsHtml(em){ return (em || []).slice(0, 3).map(e => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join("<br>") || `<span class="muted">—</span>`; }
function aiBadge(r){
  if (!r.ai_verdict) return "";
  const ok = r.ai_verdict === "yes";
  return `<span class="ai ${ok ? "ai-yes" : "ai-no"}" title="${esc((r.ai_type || "") + ": " + (r.ai_reason || ""))}">ИИ ${ok ? "✓" : "✗"} ${esc(r.ai_type || "")}</span>`;
}
function statusSelect(r){
  if (!r.id) return `<span class="muted small">после поиска</span>`;
  return `<select class="st st-${esc(r.status)}" data-id="${r.id}">` +
    Object.entries(S.meta.statuses).map(([k, v]) => `<option value="${k}" ${k === r.status ? "selected" : ""}>${esc(v)}</option>`).join("") + `</select>`;
}
function srcHtml(r){ return (r.source || "").split("+").filter(Boolean).map(s => `<span class="badge">${esc(S.meta.sources[s] || s)}</span>`).join(""); }
function nameCell(r){
  const site = safeUrl(r.website), dom = site ? hostOf(site) : "";
  return `<div class="nm">${esc(r.name)}${r.is_new ? '<span class="new">новая</span>' : ""}${r.crm_id ? '<span class="crm">CRM</span>' : ""}</div>
    ${dom ? `<a class="site" href="${esc(site)}" target="_blank" rel="noopener noreferrer">${esc(dom)}</a>` : ""}
    ${r.description ? `<div class="desc" title="${esc(r.description)}">${esc(r.description)}</div>` : ""}`;
}
function whereCell(r){
  const where = [r.region, COUNTRY[r.country]].filter(Boolean).join(" · ");
  return `${esc(r.city || "—")}${where ? `<div class="sub">${esc(where)}</div>` : ""}${r.address ? `<div class="sub addr">${esc(r.address)}</div>` : ""}`;
}

function filtered(){
  const q = $("#fText").value.trim().toLowerCase();
  const min = +$("#fScore").value, cc = $("#fCountry").value, reg = $("#fRegion").value, src = $("#fSource").value;
  const ph = $("#fPhone").checked, em = $("#fEmail").checked, nw = $("#fNew").checked;
  const out = S.rows.filter(r =>
    (r.score || 0) >= min - 1e-9 && (!cc || r.country === cc) && (!reg || r.region === reg) &&
    (!src || (r.source || "").split("+").includes(src)) && (!ph || r.phones.length) && (!em || r.emails.length) && (!nw || r.is_new) &&
    (!q || [r.name, r.city, r.region, r.website, r.address, (r.rubrics || []).join(" "), r.emails.join(" "), r.description, r.comment].join(" ").toLowerCase().includes(q)));
  const {k, dir} = S.sort;
  out.sort((a, b) => {
    const x = a[k] ?? "", y = b[k] ?? "";
    const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y), "ru");
    return c * dir || (b.score || 0) - (a.score || 0);
  });
  return out;
}

function fillSelect(el, values, first, label){
  const cur = el.value;
  el.innerHTML = `<option value="">${first}</option>` + values.map(v => `<option value="${esc(v)}">${esc(label ? label(v) : v)}</option>`).join("");
  if (values.includes(cur)) el.value = cur;
}

function renderResults(){
  const R = S.rows;
  $("#stAll").textContent = R.length;
  $("#stTarget").textContent = R.filter(r => (r.score || 0) >= S.threshold).length;
  $("#stNew").textContent = S.final ? R.filter(r => r.is_new).length : "—";
  $("#stPhones").textContent = R.filter(r => r.phones.length).length;
  $("#stEmails").textContent = R.filter(r => r.emails.length).length;
  fillSelect($("#fCountry"), [...new Set(R.map(r => r.country).filter(Boolean))].sort(), "Все страны", v => COUNTRY[v] || v);
  fillSelect($("#fRegion"), [...new Set(R.map(r => r.region).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru")), "Все регионы");
  fillSelect($("#fSource"), [...new Set(R.flatMap(r => (r.source || "").split("+")).filter(Boolean))], "Все источники", v => S.meta.sources[v] || v);
  $("#crmButtons").innerHTML = S.final ? S.caps.crm.map(c => `<button class="btn sm" data-crm-push="${esc(c)}">В ${c === "bitrix24" ? "Битрикс24" : "amoCRM"}</button>`).join(" ") : "";
  $("#btnToBase").hidden = !S.final;
  renderTable();
}

function renderTable(){
  const rows = filtered();
  S.view = rows.slice(0, S.limit);
  $("#tbody").innerHTML = S.view.map((r, i) => `<tr data-i="${i}">
    <td class="c-name">${nameCell(r)}</td>
    <td><span class="score ${bucket(r.score || 0)}">${Math.round((r.score || 0) * 100)}</span>${aiBadge(r)}</td>
    <td class="where">${whereCell(r)}</td>
    <td class="ph">${phonesHtml(r.phones)}</td>
    <td class="em">${emailsHtml(r.emails)}</td>
    <td>${socialsHtml(r.socials)}</td>
    <td>${statusSelect(r)}</td>
    <td>${srcHtml(r)}</td></tr>`).join("");
  $("#none").hidden = rows.length > 0 || !S.rows.length;
  $("#more").hidden = rows.length <= S.limit;
  $("#shown").textContent = `${rows.length} из ${S.rows.length}`;
  for (const th of $$("#results th.sort")) th.querySelector(".arr").textContent = th.dataset.k === S.sort.k ? (S.sort.dir < 0 ? "↓" : "↑") : "";
}

function bindResults(){
  const rerender = () => { S.limit = PAGE; renderTable(); };
  $("#fScore").addEventListener("input", () => { $("#fScoreV").textContent = Math.round($("#fScore").value * 100); rerender(); });
  $("#fText").addEventListener("input", rerender);
  for (const id of ["#fCountry", "#fRegion", "#fSource", "#fPhone", "#fEmail", "#fNew"]) $(id).addEventListener("change", rerender);
  $("#btnMore").addEventListener("click", () => { S.limit += PAGE; renderTable(); });
  $("#results thead").addEventListener("click", e => {
    const th = e.target.closest("th.sort"); if (!th) return;
    const k = th.dataset.k;
    S.sort = {k, dir: S.sort.k === k ? -S.sort.dir : (k === "score" ? -1 : 1)};
    renderTable();
  });
  $("#tbody").addEventListener("change", e => {
    const sel = e.target.closest("select.st"); if (!sel) return;
    setStatus([+sel.dataset.id], sel.value);
  });
  $("#tbody").addEventListener("click", e => {
    if (e.target.closest("a,select,input,button")) return;
    const tr = e.target.closest("tr[data-i]"); if (tr) openDrawer(S.view[+tr.dataset.i]);
  });
  $("#btnCsv").addEventListener("click", () => exportCsv(filtered()));
  $("#btnXlsx").addEventListener("click", () => exportXlsx(filtered()));
  $("#btnCopy").addEventListener("click", async () => {
    const phones = [...new Set(filtered().flatMap(r => r.phones))];
    if (!phones.length) return toast("В текущем фильтре нет телефонов");
    try { await navigator.clipboard.writeText(phones.map(fmtPhone).join("\n")); toast(`Скопировано ${phones.length} ${plural(phones.length, "телефон", "телефона", "телефонов")}`); }
    catch { toast("Браузер не дал доступ к буферу"); }
  });
  $("#crmButtons").addEventListener("click", e => {
    const b = e.target.closest("[data-crm-push]"); if (!b) return;
    const ids = filtered().filter(r => r.id && !r.crm_id).map(r => r.id);
    pushCrm(ids, b.dataset.crmPush);
  });
  $("#btnToBase").addEventListener("click", () => { S.base.runId = S.runId; S.base.runTitle = S.title; switchTab("base"); });
}

async function setStatus(ids, status, extra){
  try {
    await api("/api/companies/update", {ids, status, ...(extra || {})});
    patchRows(ids, {status, ...(extra || {})});
    toast(ids.length > 1 ? `Статус «${statusName(status)}» — ${ids.length} шт.` : `Статус: ${statusName(status)}`);
  } catch (err) { toast(err.message); }
}

function patchRows(ids, fields){
  const set = new Set(ids);
  for (const list of [S.rows, S.base.rows]) for (const r of list) if (set.has(r.id)) Object.assign(r, fields);
  if (!$("#tab-search").hidden) renderTable();
  if (!$("#tab-base").hidden) renderBase();
}

async function pushCrm(ids, crm){
  if (!ids.length) return toast("Нечего выгружать — всё уже в CRM или нет сохранённых компаний");
  if (!confirm(`Выгрузить ${ids.length} ${plural(ids.length, "компанию", "компании", "компаний")} в ${crm === "bitrix24" ? "Битрикс24" : "amoCRM"}?`)) return;
  try {
    const r = await api("/api/crm/push", {ids, crm});
    toast(`В CRM: ${r.ok}` + (r.skipped ? `, уже были: ${r.skipped}` : "") + (r.failed ? `, ошибок: ${r.failed} (${r.errors[0] || ""})` : ""));
    if (S.runId && !$("#tab-search").hidden) reloadRun();
    if (!$("#tab-base").hidden) loadBase();
  } catch (err) { toast(err.message); }
}

async function reloadRun(){
  const d = await api(`/api/runs/${S.runId}`).catch(() => null);
  if (d){ S.rows = d.rows; renderResults(); }
}

/* ===================== выгрузки ===================== */
function fileBase(title){
  const t = (title || S.title || "leads").replace(/[^\wа-яё\- ]/gi, "").trim().replace(/\s+/g, "_").slice(0, 40);
  const d = new Date(), p = n => String(n).padStart(2, "0");
  return `leads_${t}_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`;
}
function download(blob, name){
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
function exportCsv(rows){
  if (!rows.length) return toast("Нечего выгружать");
  const head = ["Название", "Релевантность", "Статус", "Менеджер", "Страна", "Регион", "Город", "Адрес", "Телефоны", "Почты", "Мессенджеры", "Сайт", "Рубрика", "Рейтинг", "Тип (ИИ)", "Вывод ИИ", "ИНН/УНП", "Комментарий", "Источник", "Ссылка"];
  const cell = v => { const s = String(v ?? ""); return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const body = rows.map(r => [r.name, r.score, r.id ? statusName(r.status) : "", r.owner, COUNTRY[r.country] || r.country, r.region, r.city, r.address,
    r.phones.map(fmtPhone).join(", "), r.emails.join(", "), Object.entries(r.socials || {}).map(([k, v]) => `${k}: ${v.join(", ")}`).join("; "),
    r.website, (r.rubrics || []).join(", "), r.rating, r.ai_type, r.ai_reason, r.inn, r.comment,
    (r.source || "").split("+").map(s => S.meta.sources[s] || s).join("+"), r.source_url].map(cell).join(";"));
  download(new Blob(["﻿" + [head.join(";"), ...body].join("\r\n")], {type: "text/csv;charset=utf-8"}), fileBase() + ".csv");
}
async function exportXlsx(rows, title){
  if (!rows.length) return toast("Нечего выгружать");
  try {
    const r = await fetch("/api/xlsx", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({rows: rows.map(x => ({...x, phones: x.phones.map(fmtPhone)})), threshold: S.threshold, name: fileBase(title)})});
    if (!r.ok) throw new Error("HTTP " + r.status);
    download(await r.blob(), fileBase(title) + ".xlsx");
  } catch (err) { toast("Не удалось выгрузить: " + err.message); }
}

/* ===================== история ===================== */
async function loadHistoryList(){
  const pop = $("#historyPop");
  try {
    const {runs} = await api("/api/runs");
    pop.innerHTML = runs.length ? runs.map(r => `<div class="it" data-run="${esc(r.id)}">
        <div><div class="t">${esc(r.title || "поиск")}</div><div class="sub">${esc(r.user || "")}${r.schedule_id ? " · автопоиск" : ""}${r.status !== "done" ? " · " + esc(r.status) : ""}</div></div>
        <div class="d">${esc(fmtDate(r.created))}<br>${r.total} комп. · новых ${r.new}</div></div>`).join("")
      : `<div class="it muted">Пока пусто — здесь появятся завершённые поиски</div>`;
  } catch (err) { pop.innerHTML = `<div class="it muted">${esc(err.message)}</div>`; }
}
function bindHistory(){
  const pop = $("#historyPop");
  $("#btnHistory").addEventListener("click", async e => {
    e.stopPropagation();
    if (!pop.hidden){ pop.hidden = true; return; }
    await loadHistoryList(); pop.hidden = false;
  });
  document.addEventListener("click", e => { if (!pop.hidden && !pop.contains(e.target)) pop.hidden = true; });
  pop.addEventListener("click", e => { const it = e.target.closest(".it[data-run]"); if (!it) return; pop.hidden = true; openRun(it.dataset.run); });
}
async function openRun(id){
  let d;
  try { d = await api(`/api/runs/${encodeURIComponent(id)}`); } catch (err) { return toast(err.message); }
  clearTimeout(S.pollTimer);
  if (S.job && S.job !== id){ S.job = null; lsSet(LS_JOB, null); }
  setRunning(false); switchTab("search");
  const run = d.run;
  S.rows = d.rows; S.final = true; S.runId = run.id; S.threshold = (run.spec || {}).threshold ?? 0.5; S.title = run.title || ""; S.limit = PAGE;
  $("#fScore").value = S.threshold; $("#fScoreV").textContent = Math.round(S.threshold * 100);
  $("#progress").hidden = true; showResults();
  const b = $("#viewBanner");
  b.innerHTML = `<span>Поиск: <b>${esc(run.title || "")}</b> · ${esc(fmtDate(run.created))}${run.user ? " · " + esc(run.user) : ""} · ${run.total} комп., новых ${run.new}</span>
    <span class="acts"><button class="btn sm" id="btnReuse">Подставить параметры</button></span>`;
  b.hidden = false;
  $("#btnReuse").addEventListener("click", () => { specToForm(run.spec || {}); toast("Параметры подставлены в форму"); });
  renderResults();
}

/* ===================== база ===================== */
function baseFilters(offset){
  return {q: $("#bText").value.trim(), status: S.base.status ? [S.base.status] : [], owner: $("#bOwner").value,
    country: $("#bCountry").value, region: $("#bRegion").value, min_score: +$("#bScore").value || 0,
    has_phone: $("#bPhone").checked, has_email: $("#bEmail").checked, crm: $("#bNoCrm").checked ? "no" : "",
    run_id: S.base.runId, sort: S.base.sort, dir: S.base.dir, limit: 200, offset: offset || 0};
}
async function loadBase(append){
  const off = append ? S.base.rows.length : 0;
  let d;
  try { d = await api("/api/companies/query", baseFilters(off)); } catch (err) { return toast(err.message); }
  S.base.rows = append ? S.base.rows.concat(d.rows) : d.rows;
  S.base.total = d.total; S.base.counts = d.status_counts;
  if (!append) S.base.sel.clear();
  fillSelect($("#bRegion"), d.regions, "Все регионы");
  const own = $("#bOwner"), cur = own.value;
  own.innerHTML = `<option value="">Все менеджеры</option><option value="__me">Мои</option><option value="__none">Без менеджера</option>` +
    d.owners.map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join("");
  own.value = cur;
  renderBase();
}
function renderBase(){
  const B = S.base, counts = B.counts, all = Object.values(counts).reduce((a, b) => a + b, 0);
  $("#bStatus").innerHTML = (B.runId ? `<button class="fchip run" data-run-clear>Поиск: ${esc(B.runTitle || B.runId)} ✕</button>` : "") +
    `<button class="fchip ${!B.status ? "on" : ""}" data-st="">Все<b>${all}</b></button>` +
    Object.entries(S.meta.statuses).map(([k, v]) => `<button class="fchip ${B.status === k ? "on" : ""}" data-st="${k}">${esc(v)}<b>${counts[k] || 0}</b></button>`).join("");
  $("#btbody").innerHTML = B.rows.map((r, i) => `<tr data-i="${i}" class="${B.sel.has(r.id) ? "sel" : ""}">
    <td class="cb"><input type="checkbox" data-sel="${r.id}" ${B.sel.has(r.id) ? "checked" : ""}></td>
    <td class="c-name">${nameCell(r)}</td>
    <td><span class="score ${bucket(r.score || 0)}">${Math.round((r.score || 0) * 100)}</span>${aiBadge(r)}</td>
    <td>${statusSelect(r)}${r.comment ? `<div class="sub" title="${esc(r.comment)}">💬 ${esc(r.comment.slice(0, 40))}</div>` : ""}</td>
    ${S.auth ? `<td>${esc(r.owner || "—")}</td>` : ""}
    <td class="where">${whereCell(r)}</td>
    <td class="ph">${phonesHtml(r.phones)}</td>
    <td class="em">${emailsHtml(r.emails)}</td>
    <td>${socialsHtml(r.socials)}</td>
    <td class="sub">${esc(fmtDate(r.first_seen).slice(0, 10))}</td></tr>`).join("");
  $("#bNone").hidden = B.rows.length > 0;
  $("#bMore").hidden = B.rows.length >= B.total;
  $("#bShown").textContent = `${B.rows.length} из ${B.total}`;
  $("#bAll").checked = B.rows.length > 0 && B.rows.every(r => B.sel.has(r.id));
  for (const th of $$("#tab-base th.sort")) th.querySelector(".arr").textContent = th.dataset.k === B.sort ? (B.dir === "desc" ? "↓" : "↑") : "";
  renderBulk();
}
function renderBulk(){
  const n = S.base.sel.size;
  $("#bulk").hidden = !n;
  $("#bulkCount").textContent = `Выбрано: ${n}`;
}
function bindBase(){
  const reload = debounce(() => loadBase(), 250);
  $("#bText").addEventListener("input", reload);
  $("#bScore").addEventListener("input", () => { $("#bScoreV").textContent = Math.round($("#bScore").value * 100); reload(); });
  for (const id of ["#bCountry", "#bRegion", "#bOwner", "#bPhone", "#bEmail", "#bNoCrm"]) $(id).addEventListener("change", () => loadBase());
  $("#bStatus").addEventListener("click", e => {
    if (e.target.closest("[data-run-clear]")){ S.base.runId = null; S.base.runTitle = ""; return loadBase(); }
    const b = e.target.closest("[data-st]"); if (!b) return;
    S.base.status = b.dataset.st; loadBase();
  });
  $("#bMoreBtn").addEventListener("click", () => loadBase(true));
  $("#tab-base thead").addEventListener("click", e => {
    const th = e.target.closest("th.sort"); if (!th) return;
    const k = th.dataset.k;
    S.base.dir = S.base.sort === k ? (S.base.dir === "desc" ? "asc" : "desc") : (["score", "first_seen"].includes(k) ? "desc" : "asc");
    S.base.sort = k; loadBase();
  });
  $("#bAll").addEventListener("change", e => {
    for (const r of S.base.rows) e.target.checked ? S.base.sel.add(r.id) : S.base.sel.delete(r.id);
    renderBase();
  });
  $("#btbody").addEventListener("change", e => {
    const sel = e.target.closest("select.st");
    if (sel) return setStatus([+sel.dataset.id], sel.value);
    const cb = e.target.closest("input[data-sel]");
    if (cb){ const id = +cb.dataset.sel; cb.checked ? S.base.sel.add(id) : S.base.sel.delete(id); cb.closest("tr").classList.toggle("sel", cb.checked); renderBulk(); $("#bAll").checked = S.base.rows.every(r => S.base.sel.has(r.id)); }
  });
  $("#btbody").addEventListener("click", e => {
    if (e.target.closest("a,select,input,button")) return;
    const tr = e.target.closest("tr[data-i]"); if (tr) openDrawer(S.base.rows[+tr.dataset.i]);
  });
  $("#bulkStatus").innerHTML = `<option value="">Сменить статус…</option>` + Object.entries(S.meta.statuses).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#bulkStatus").addEventListener("change", async e => {
    const st = e.target.value; e.target.value = ""; if (!st) return;
    await setStatus([...S.base.sel], st); loadBase();
  });
  $("#bulkMine").addEventListener("click", async () => {
    try { await api("/api/companies/update", {ids: [...S.base.sel], owner: "__me"}); toast("Назначено на тебя"); loadBase(); } catch (err) { toast(err.message); }
  });
  $("#bulkCrm").innerHTML = S.caps.crm.map(c => `<button class="btn sm" data-crm-push="${esc(c)}">В ${c === "bitrix24" ? "Битрикс24" : "amoCRM"}</button>`).join(" ");
  $("#bulkCrm").addEventListener("click", e => { const b = e.target.closest("[data-crm-push]"); if (b) pushCrm([...S.base.sel], b.dataset.crmPush); });
  $("#bulkExport").addEventListener("click", () => exportXlsx(S.base.rows.filter(r => S.base.sel.has(r.id)), "база"));
  $("#bulkClear").addEventListener("click", () => { S.base.sel.clear(); renderBase(); });
  $("#bExport").addEventListener("click", async () => {
    try { const d = await api("/api/companies/query", {...baseFilters(0), limit: 5000}); exportXlsx(d.rows, "база"); }
    catch (err) { toast(err.message); }
  });
}

/* ===================== карточка компании ===================== */
function bindDrawer(){
  $("#drawerBg").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer(); });
}
function closeDrawer(){ $("#drawer").hidden = true; }
async function openDrawer(row){
  if (!row) return;
  let c = row;
  if (row.id){ try { c = {...row, ...(await api(`/api/companies/${row.id}`))}; c.score = row.score ?? c.score; } catch (err) { toast(err.message); } }
  renderDrawer(c);
  $("#drawer").hidden = false;
}
function kv(k, v){ return v ? `<div class="k">${esc(k)}</div><div class="v">${v}</div>` : ""; }
function renderDrawer(c){
  const site = safeUrl(c.website);
  const card = safeUrl(c.source_url);
  const ai = c.ai_verdict ? `<div class="aibox ${c.ai_verdict === "yes" ? "yes" : "no"}"><b>ИИ: ${c.ai_verdict === "yes" ? "подходит" : "не подходит"}</b>
      ${c.ai_confidence != null ? ` · уверенность ${c.ai_confidence}%` : ""}${c.ai_type ? ` · ${esc(c.ai_type)}` : ""}
      <div>${esc(c.ai_reason || "")}</div>${(c.ai_services || []).length ? `<div class="sub">Услуги: ${esc(c.ai_services.join(", "))}</div>` : ""}</div>` : "";
  const legal = [kv("ИНН / УНП", esc(c.inn)), kv("Юрлицо", esc(c.legal_name)), kv("Статус", esc(c.legal_status)), kv("ОКВЭД", esc(c.okved)),
    kv("Руководитель", esc(c.manager)), kv("Сотрудников", esc(c.employees)), kv("Выручка", esc(c.revenue))].join("");
  const owners = [...new Set([...(S.meta.users || []), c.owner].filter(Boolean))];
  const editable = !!c.id;
  $("#drawerBody").innerHTML = `
    <div class="dh"><div><h2>${esc(c.name)}</h2>
      <div class="sub">${srcHtml(c)} ${c.is_new ? '<span class="new">новая</span>' : ""} ${c.crm_id ? `<span class="crm">${esc(c.crm)} #${esc(c.crm_id)}</span>` : ""}</div></div>
      <button class="btn sm" id="dClose">✕</button></div>
    <div class="dsec"><span class="score ${bucket(c.score || 0)}">${Math.round((c.score || 0) * 100)}</span> <span class="muted small">релевантность</span>
      ${(c.matched || []).length ? `<div class="sub" style="margin-top:4px">Совпало: ${esc(c.matched.join(", "))}</div>` : ""}</div>
    ${ai ? `<div class="dsec">${ai}</div>` : ""}
    <div class="dsec"><h4>Контакты</h4><div class="kv">
      ${kv("Телефоны", (c.phones || []).map(p => `<a href="tel:${esc(p)}">${esc(fmtPhone(p))}</a> <span class="pk">${phoneKind(p)}</span>`).join("<br>"))}
      ${kv("Почты", (c.emails || []).map(e => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join("<br>"))}
      ${kv("Мессенджеры", Object.keys(c.socials || {}).length ? socialsHtml(c.socials) : "")}
      ${kv("Сайт", site ? `<a href="${esc(site)}" target="_blank" rel="noopener noreferrer">${esc(site)}</a>` : "")}
      ${kv("Адрес", esc([c.address, c.city, c.region, COUNTRY[c.country]].filter(Boolean).join(", ")))}
      ${kv("Рубрики", esc((c.rubrics || []).join(", ")))}
      ${kv("Рейтинг", c.rating ? "★ " + esc(c.rating) : "")}
      ${kv("Карточка", card ? `<a href="${esc(card)}" target="_blank" rel="noopener noreferrer">открыть ↗</a>` : "")}
    </div></div>
    ${c.description ? `<div class="dsec"><h4>Описание</h4><div class="small">${esc(c.description)}</div></div>` : ""}
    ${legal ? `<div class="dsec"><h4>Юр. данные</h4><div class="kv">${legal}</div></div>` : ""}
    <div class="dsec"><h4>Работа с лидом</h4>
      ${editable ? `<div class="row2">
        <div><label class="f">Статус</label><select id="dStatus">${Object.entries(S.meta.statuses).map(([k, v]) => `<option value="${k}" ${k === c.status ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
        ${S.auth ? `<div><label class="f">Менеджер</label><select id="dOwner"><option value="">—</option>${owners.map(o => `<option ${o === c.owner ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></div>` : "<div></div>"}
      </div>
      <label class="f">Комментарий</label><textarea id="dComment" placeholder="Итог звонка, договорённости…">${esc(c.comment || "")}</textarea>
      <div class="dacts"><button class="btn sm" id="dSave">Сохранить</button>
        ${S.caps.crm.map(x => `<button class="btn sm" data-crm-push="${esc(x)}" ${c.crm_id ? "disabled" : ""}>В ${x === "bitrix24" ? "Битрикс24" : "amoCRM"}</button>`).join("")}</div>`
      : `<div class="muted small">Статусы и комментарии — после окончания поиска, когда компания сохранится в базу.</div>`}
    </div>
    ${(c.history || []).length ? `<div class="dsec"><h4>История</h4><ul class="hist">${c.history.map(h => `<li>${esc(fmtDate(h.ts))} · ${esc(h.user || "")} · ${esc({status: "статус", owner: "менеджер", comment: "комментарий", crm: "CRM"}[h.action] || h.action)}: ${esc(h.detail)}</li>`).join("")}</ul></div>` : ""}
    ${(c.runs || []).length ? `<div class="dsec"><h4>Найдена в поисках</h4><ul class="hist">${c.runs.map(r => `<li>${esc(fmtDate(r.created))} · ${esc(r.title)} · ${Math.round((r.score || 0) * 100)}</li>`).join("")}</ul></div>` : ""}
    ${c.first_seen ? `<div class="dsec sub">Впервые найдена: ${esc(fmtDate(c.first_seen))} · последний раз: ${esc(fmtDate(c.last_seen))}</div>` : ""}`;
  $("#dClose").addEventListener("click", closeDrawer);
  if (editable){
    $("#dSave").addEventListener("click", async () => {
      const fields = {status: $("#dStatus").value, comment: $("#dComment").value.trim()};
      if (S.auth) fields.owner = $("#dOwner").value;
      try { await api("/api/companies/update", {ids: [c.id], ...fields}); patchRows([c.id], fields); toast("Сохранено"); openDrawer({...c, ...fields}); }
      catch (err) { toast(err.message); }
    });
    for (const b of $$("#drawerBody [data-crm-push]")) b.addEventListener("click", async () => { await pushCrm([c.id], b.dataset.crmPush); openDrawer(c); });
  }
}

/* ===================== автопоиск ===================== */
function bindAuto(){
  $("#tgStatus").innerHTML = S.caps.telegram ? "Telegram подключён" : `<span class="muted">Telegram не настроен (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)</span>`;
  $("#btnTgTest").hidden = !S.caps.telegram;
  $("#btnTgTest").addEventListener("click", async () => { try { await api("/api/telegram/test", {}); toast("Отправил тест в Telegram"); } catch (err) { toast(err.message); } });
  $("#autoList").addEventListener("click", async e => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const id = b.dataset.id, act = b.dataset.act;
    try {
      if (act === "run"){ const r = await api(`/api/schedules/${id}/run`, {}); switchTab("search"); attach(r.id, true); return; }
      if (act === "delete"){ if (!confirm("Удалить автопоиск?")) return; await api(`/api/schedules/${id}/delete`, {}); }
      if (act === "toggle") await api(`/api/schedules/${id}`, {enabled: b.dataset.on !== "1"});
      if (act === "form"){ const s = S.schedules.find(x => String(x.id) === id); if (s){ specToForm(s.spec); switchTab("search"); toast("Параметры автопоиска подставлены в форму"); } return; }
      if (act === "last"){ openRun(b.dataset.run); return; }
      loadAuto();
    } catch (err) { toast(err.message); }
  });
  $("#autoList").addEventListener("change", async e => {
    const sel = e.target.closest("select[data-every]"); if (!sel) return;
    try { await api(`/api/schedules/${sel.dataset.every}`, {every_hours: +sel.value}); toast("Интервал изменён"); loadAuto(); } catch (err) { toast(err.message); }
  });
}
const EVERY = {6: "каждые 6 ч", 12: "каждые 12 ч", 24: "раз в день", 72: "раз в 3 дня", 168: "раз в неделю"};
document.addEventListener("click", e => {
  const btn = e.target.closest("#keysList [data-act]");
  if (btn) keyAction(btn.closest(".keycard"), btn.dataset.act);
});

async function loadAuto(){
  let d;
  try { d = await api("/api/schedules"); } catch (err) { return toast(err.message); }
  S.schedules = d.schedules;
  $("#autoEmpty").hidden = d.schedules.length > 0;
  $("#autoList").innerHTML = d.schedules.map(s => {
    const sp = s.spec || {}, regs = (sp.regions || []).length;
    const opts = Object.entries(EVERY).map(([h, t]) => `<option value="${h}" ${+h === s.every_hours ? "selected" : ""}>${t}</option>`).join("")
      + (EVERY[s.every_hours] ? "" : `<option selected value="${s.every_hours}">каждые ${s.every_hours} ч</option>`);
    return `<div class="panel sched ${s.enabled ? "" : "off"}">
      <div><div class="t">${esc(s.title)}</div>
        <div class="sub">${esc((sp.keywords || []).slice(0, 4).join(" · "))}${(sp.keywords || []).length > 4 ? " …" : ""}</div>
        <div class="sub">${regs ? `${regs} ${plural(regs, "регион", "региона", "регионов")}` : esc((sp.countries || []).map(c => COUNTRY[c]).join(", "))}
          · следующий запуск: ${s.enabled ? esc(fmtTs(s.next_run)) : "выключен"} · прошлый: ${esc(fmtTs(s.last_run))}${s.owner ? " · " + esc(s.owner) : ""}</div></div>
      <div class="acts">
        <select data-every="${s.id}">${opts}</select>
        <button class="btn sm" data-act="toggle" data-id="${s.id}" data-on="${s.enabled ? 1 : 0}">${s.enabled ? "Выключить" : "Включить"}</button>
        <button class="btn sm" data-act="run" data-id="${s.id}">Запустить сейчас</button>
        ${s.last_run_id ? `<button class="btn sm" data-act="last" data-id="${s.id}" data-run="${esc(s.last_run_id)}">Результат</button>` : ""}
        <button class="btn sm" data-act="form" data-id="${s.id}">В форму</button>
        <button class="btn sm danger" data-act="delete" data-id="${s.id}">Удалить</button>
      </div></div>`;
  }).join("");
}

boot();
