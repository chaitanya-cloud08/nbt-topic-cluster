/*
 * <nbt-topic-carousel>: NBT topic carousels, fed live by the topic-clusters API.
 *
 *   <script src="https://<service>/ui/topic-carousel.js" defer></script>
 *
 *   <nbt-topic-carousel api="https://<service>" scope="home"></nbt-topic-carousel>       app home
 *   <nbt-topic-carousel api="https://<service>" msid="2354729"></nbt-topic-carousel>     section page
 *   <nbt-topic-carousel api="https://<service>" story="135086995"></nbt-topic-carousel>  end of an article
 *   <nbt-topic-carousel api="https://<service>" cluster="tc_..."></nbt-topic-carousel>   one topic
 *
 * Optional: limit, offset (which clusters of the list to show), theme="dark|light" (default: follows the
 * device), topic-url="/topic/{id}" (where the topic title leads).
 * Events (bubble out of the component; call preventDefault() to handle navigation yourself):
 *   nbt-topic-open          detail {item, cluster}  a story card was tapped (default: open item.wu)
 *   nbt-topic-open-cluster  detail {cluster}        a topic title or "all stories" was tapped
 *   nbt-topic-empty                                  nothing to show (the component hides itself)
 */
(() => {
  if (customElements.get("nbt-topic-carousel")) return;

  // Accent per topic: [dark: accent, card, meta, onAccent, placeholder], [light: same]
  const ACCENTS = [
    [["#F2A93B", "#2A1F17", "#E8B272", "#1A1206", "#6E4B33"], ["#A35F00", "#FFF4E6", "#8A5300", "#FFFFFF", "#E9C9A3"]],
    [["#6FD0C0", "#172427", "#7FD0C2", "#08201C", "#2F4C55"], ["#0B7366", "#E8F6F3", "#0B6156", "#FFFFFF", "#A9D8CF"]],
    [["#F28B9B", "#2A1719", "#F2A3B0", "#2A0A10", "#6B3640"], ["#B0304A", "#FDECEF", "#962840", "#FFFFFF", "#EBB5BF"]],
    [["#8AB4F8", "#16202E", "#A7C5F9", "#0A1A33", "#34486B"], ["#1F5FBF", "#EAF1FD", "#1A4FA0", "#FFFFFF", "#B6CCF2"]],
  ];
  const BASE = {
    dark: "--fg:#F2F0EC;--muted:#B5B1AA;--soft:#8F8B85;--surface:#1C1C20;--line:#2C2C31;--chip:#26262A;--chipline:#3A3A40;--sk:#26262A;",
    light: "--fg:#1A1A1C;--muted:#55514B;--soft:#6B675F;--surface:#F4F2EF;--line:#E2DED8;--chip:#FFFFFF;--chipline:#D5D0C9;--sk:#E8E5E0;",
  };
  // One theme block: base colours on the host, accent colours on each topic, under the given host selector.
  const block = (sel, mode) => `${sel}{${BASE[mode]}}` + ACCENTS.map((a, i) => {
    const [acc, card, meta, on, ph] = a[mode === "dark" ? 0 : 1];
    return `${sel} [data-accent="${i}"]{--acc:${acc};--card:${card};--meta:${meta};--on:${on};--ph:${ph};}`;
  }).join("");

  const CSS = `
  :host{display:block;font-family:"Mukta","Noto Sans Devanagari","Hind",system-ui,sans-serif;color:var(--fg);
    -webkit-font-smoothing:antialiased}
  :host([hidden]),:host([data-empty]){display:none}
  ${block(":host", "light")}
  @media (prefers-color-scheme: dark){ ${block(":host(:not([theme=light]))", "dark")} }
  ${block(":host([theme=dark])", "dark")}
  *{box-sizing:border-box}
  button{font:inherit;color:inherit}
  a{color:inherit;text-decoration:none}
  .c{padding:20px 0 24px}
  .c + .c{border-top:1px solid var(--line)}
  .head{display:flex;flex-direction:column;gap:2px;padding:0 16px 14px}
  .eyebrow{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:var(--acc);line-height:1.4}
  .title{margin:0}
  .titlebtn{all:unset;box-sizing:border-box;cursor:pointer;display:flex;width:100%;align-items:center;
    justify-content:space-between;gap:8px;min-height:44px;font-size:28px;font-weight:800;line-height:1.2;color:var(--fg)}
  .titlebtn:focus-visible,.card:focus-visible,.row:focus-visible,.chip:focus-visible,.all:focus-visible{
    outline:2px solid var(--acc);outline-offset:3px;border-radius:12px}
  .sub{margin:2px 0 0;font-size:15px;line-height:1.5;color:var(--muted)}
  .track{display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x mandatory;padding:0 16px;scroll-padding:0 16px;
    scrollbar-width:none;-webkit-overflow-scrolling:touch}
  .track::-webkit-scrollbar{display:none}
  .card{flex:0 0 min(300px,80%);scroll-snap-align:start;border-radius:24px;overflow:hidden;background:var(--card);
    display:flex;flex-direction:column}
  .media{position:relative;height:180px;background:var(--ph)}
  .media img{width:100%;height:100%;object-fit:cover;display:block}
  .badges{position:absolute;inset:12px 12px auto 12px;display:flex;justify-content:space-between;gap:8px}
  .badge{font-size:12px;font-weight:600;line-height:1.5;padding:2px 10px;border-radius:999px;
    background:rgba(0,0,0,.6);color:#F2F0EC}
  .badge.new{background:var(--acc);color:var(--on)}
  .body{padding:14px 18px 20px;display:flex;flex-direction:column;gap:6px}
  .meta{font-size:13px;color:var(--meta)}
  .hl{margin:0;font-size:17px;font-weight:600;line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;
    -webkit-box-orient:vertical;overflow:hidden}
  .dots{display:flex;justify-content:center;gap:6px;padding-top:14px}
  .dot{width:6px;height:6px;border-radius:3px;background:var(--line);transition:width .2s,background .2s}
  .dot.on{width:18px;background:var(--acc)}
  .module{margin:16px;padding:18px 16px 8px;border-radius:24px;background:var(--surface);display:flex;
    flex-direction:column;gap:14px}
  .kicker{font-size:13px;font-weight:600;color:var(--muted)}
  .mtitle{margin:0;font-size:20px;font-weight:700;line-height:1.3}
  .chips{display:flex;gap:8px;flex-wrap:wrap}
  .chip{min-height:44px;padding:8px 16px;border-radius:999px;font-size:15px;font-weight:600;cursor:pointer;
    background:var(--chip);border:1px solid var(--chipline);color:var(--fg)}
  .chip[aria-pressed=true]{background:var(--acc);border-color:var(--acc);color:var(--on)}
  .rows{display:flex;flex-direction:column}
  .row{display:flex;gap:12px;padding:10px 0;border-top:1px solid var(--line)}
  .thumb{flex:0 0 72px;height:72px;border-radius:14px;overflow:hidden;background:var(--ph)}
  .thumb img{width:100%;height:100%;object-fit:cover;display:block}
  .rtext{flex:1;min-width:0;display:flex;flex-direction:column;gap:4px}
  .rhl{margin:0;font-size:15px;font-weight:600;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;
    -webkit-box-orient:vertical;overflow:hidden}
  .rmeta{font-size:12px;color:var(--soft)}
  .rmeta .n{color:var(--meta);font-weight:600}
  .all{all:unset;box-sizing:border-box;cursor:pointer;display:flex;align-items:center;gap:6px;min-height:48px;
    font-size:15px;font-weight:600;color:var(--acc);border-top:1px solid var(--line)}
  .sk{background:var(--sk);border-radius:12px}
  @media (prefers-reduced-motion: no-preference){ .sk{animation:pulse 1.4s ease-in-out infinite} }
  @keyframes pulse{50%{opacity:.55}}
  `;

  const ICON_TREND = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/></svg>';
  const ICON_NEXT = (s) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>`;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function ago(iso, now = Date.now()) {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return "";
    const m = Math.max(0, Math.round((now - t) / 60000));
    if (m < 1) return "अभी";
    if (m < 60) return `${m} मिनट पहले`;
    const h = Math.floor(m / 60);
    if (h < 24) return h === 1 ? "1 घंटा पहले" : `${h} घंटे पहले`;
    const d = Math.floor(h / 24);
    return d === 1 ? "1 दिन पहले" : `${d} दिन पहले`;
  }

  const accentOf = (id) => {
    let h = 0;
    for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return h % ACCENTS.length;
  };

  const img = (src) => src ? `<img src="${esc(src)}" alt="" loading="lazy" decoding="async">` : "";

  class NbtTopicCarousel extends HTMLElement {
    static get observedAttributes() { return ["api", "scope", "msid", "story", "cluster", "limit", "offset"]; }

    constructor() {
      super();
      this.root = this.attachShadow({ mode: "open" });
      this.data = [];
      this.selected = 0;
      this._req = 0;
    }

    connectedCallback() { this.load(); }
    attributeChangedCallback(_n, oldV, newV) { if (this.isConnected && oldV !== newV) this.load(); }

    get mode() { return this.hasAttribute("story") ? "story" : "carousel"; }

    endpoint() {
      const base = (this.getAttribute("api") || "").replace(/\/$/, "");
      const a = (n) => encodeURIComponent(this.getAttribute(n));
      if (this.hasAttribute("story")) return `${base}/topicclusters?story=${a("story")}`;
      if (this.hasAttribute("cluster")) return `${base}/topicclusters?cluster=${a("cluster")}`;
      if (this.hasAttribute("msid")) return `${base}/topicclusters?msid=${a("msid")}`;
      return `${base}/topicclusters?scope=home`;
    }

    async load() {
      const req = ++this._req;
      this.removeAttribute("data-empty");
      this.render(true);
      let clusters = [];
      try {
        const res = await fetch(this.endpoint(), { headers: { Accept: "application/json" } });
        if (res.ok) {
          const body = await res.json();
          clusters = body.clusters || (body.clusterId ? [body] : []);
        }
      } catch (_) { /* network error: show nothing rather than a broken box */ }
      if (req !== this._req) return; // a newer load started
      const offset = parseInt(this.getAttribute("offset") || "0", 10) || 0;
      const limit = parseInt(this.getAttribute("limit") || "0", 10) || clusters.length;
      this.data = clusters.slice(offset, offset + limit).filter((c) => (c.items || []).length);
      const used = new Set();
      this.data.forEach((c) => {
        let a = accentOf(c.clusterId);
        for (let k = 0; k < ACCENTS.length && used.has(a); k++) a = (a + 1) % ACCENTS.length;
        used.add(a);
        c._accent = a;
      });
      this.selected = 0;
      if (!this.data.length) {
        this.setAttribute("data-empty", "");
        this.dispatchEvent(new CustomEvent("nbt-topic-empty", { bubbles: true, composed: true }));
      }
      this.render(false);
    }

    render(loading) {
      const body = loading ? this.skeleton() : this.mode === "story" ? this.storyModule() : this.carousels();
      this.root.innerHTML = `<style>${CSS}</style>${body}`;
      if (!loading) this.wire();
    }

    skeleton() {
      if (this.mode === "story") {
        return `<div class="module" aria-busy="true" aria-label="लोड हो रहा है"><div class="sk" style="height:18px;width:50%"></div>
          <div class="sk" style="height:24px;width:75%"></div><div class="sk" style="height:72px"></div><div class="sk" style="height:72px;margin-bottom:12px"></div></div>`;
      }
      return `<div class="c" aria-busy="true" aria-label="लोड हो रहा है"><div class="head"><div class="sk" style="height:16px;width:45%"></div>
        <div class="sk" style="height:30px;width:65%;margin-top:8px"></div></div>
        <div class="track"><div class="card sk" style="height:310px;border-radius:24px"></div><div class="card sk" style="height:310px;border-radius:24px"></div></div></div>`;
    }

    carousels() {
      const now = Date.now();
      return this.data.map((c, ci) => {
        const items = c.items;
        const label = ci === 0 && !this.hasAttribute("cluster") ? "ट्रेंडिंग टॉपिक" : "टॉपिक";
        const cards = items.map((it, ii) => `
          <a class="card" role="listitem" href="${esc(it.wu)}" data-c="${ci}" data-i="${ii}">
            <div class="media">${img(it.img)}
              <div class="badges"><span class="badge">${esc(it.subsecname || "")}</span>${it.isNew ? '<span class="badge new">नया</span>' : ""}</div>
            </div>
            <div class="body"><div class="meta">${esc(ago(it.published, now))}</div><h3 class="hl">${esc(it.hl)}</h3></div>
          </a>`).join("");
        const dots = items.map((_, i) => `<span class="dot${i === 0 ? " on" : ""}"></span>`).join("");
        return `<section class="c" data-accent="${c._accent}" aria-label="टॉपिक: ${esc(c.title)}">
          <div class="head">
            <div class="eyebrow">${ICON_TREND}${label} · ${items.length} खबरें · ${esc(ago(items[0].published, now))} अपडेट</div>
            <h2 class="title"><button type="button" class="titlebtn" data-open-cluster="${ci}">${esc(c.title)}${ICON_NEXT(24)}</button></h2>
            ${c.subtitle ? `<p class="sub">${esc(c.subtitle)}</p>` : ""}
          </div>
          <div class="track" role="list" data-track="${ci}">${cards}</div>
          ${items.length > 1 ? `<div class="dots" aria-hidden="true" data-dots="${ci}">${dots}</div>` : ""}
        </section>`;
      }).join("");
    }

    storyModule() {
      const now = Date.now();
      const story = this.getAttribute("story");
      const c = this.data[this.selected];
      const others = c.items.filter((it) => it.id !== story).slice(0, 4);
      const chips = this.data.length > 1 ? `<div class="chips" role="group" aria-label="टॉपिक चुनें">${this.data.map((d, i) =>
        `<button type="button" class="chip" data-accent="${d._accent}" data-chip="${i}" aria-pressed="${i === this.selected}">${esc(d.title)} · ${d.items.length}</button>`).join("")}</div>` : "";
      const rows = others.map((it, i) => `
        <a class="row" href="${esc(it.wu)}" data-c="${this.selected}" data-i="${c.items.indexOf(it)}">
          <div class="thumb">${img(it.img)}</div>
          <div class="rtext"><h3 class="rhl">${esc(it.hl)}</h3>
            <div class="rmeta">${esc(it.subsecname || "")} · ${esc(ago(it.published, now))}${it.isNew ? ' · <span class="n">नया</span>' : ""}</div></div>
        </a>`).join("");
      const kicker = this.data.length > 1 ? `यह खबर ${this.data.length} टॉपिक का हिस्सा है` : `टॉपिक: ${esc(c.title)}`;
      return `<section class="module" data-accent="${c._accent}" aria-label="इस खबर से जुड़े टॉपिक">
        <div><div class="kicker">${kicker}</div><h2 class="mtitle">${this.data.length > 1 ? "इन टॉपिक पर और पढ़ें" : "इस टॉपिक की और खबरें"}</h2></div>
        ${chips}
        <div class="rows">${rows}
          <button type="button" class="all" data-open-cluster="${this.selected}">${esc(c.title)} की सभी खबरें ${ICON_NEXT(18)}</button>
        </div>
      </section>`;
    }

    wire() {
      const r = this.root;
      r.querySelectorAll("img").forEach((im) => im.addEventListener("error", () => im.remove(), { once: true }));
      r.querySelectorAll("a[data-c]").forEach((a) => a.addEventListener("click", (e) => {
        const cluster = this.data[+a.dataset.c];
        const item = cluster.items[+a.dataset.i];
        const ev = new CustomEvent("nbt-topic-open", { detail: { item, cluster }, bubbles: true, composed: true, cancelable: true });
        if (!this.dispatchEvent(ev) || !item.wu) e.preventDefault();
      }));
      r.querySelectorAll("[data-open-cluster]").forEach((b) => b.addEventListener("click", () => {
        const cluster = this.data[+b.dataset.openCluster];
        const ev = new CustomEvent("nbt-topic-open-cluster", { detail: { cluster }, bubbles: true, composed: true, cancelable: true });
        const tmpl = this.getAttribute("topic-url");
        if (this.dispatchEvent(ev) && tmpl) location.href = tmpl.replace("{id}", encodeURIComponent(cluster.clusterId));
      }));
      r.querySelectorAll("[data-chip]").forEach((b) => b.addEventListener("click", () => {
        this.selected = +b.dataset.chip;
        this.render(false);
        this.root.querySelector(`[data-chip="${this.selected}"]`)?.focus();
      }));
      r.querySelectorAll("[data-track]").forEach((track) => {
        const dots = r.querySelectorAll(`[data-dots="${track.dataset.track}"] .dot`);
        if (!dots.length) return;
        let raf = 0;
        track.addEventListener("scroll", () => {
          cancelAnimationFrame(raf);
          raf = requestAnimationFrame(() => {
            const card = track.querySelector(".card");
            const step = card ? card.getBoundingClientRect().width + 12 : 1;
            const atEnd = track.scrollLeft + track.clientWidth >= track.scrollWidth - 4;
            const idx = atEnd ? dots.length - 1 : Math.round(track.scrollLeft / step);
            dots.forEach((d, i) => d.classList.toggle("on", i === idx));
          });
        }, { passive: true });
      });
    }
  }

  customElements.define("nbt-topic-carousel", NbtTopicCarousel);
})();
