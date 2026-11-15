import type Hls from "hls.js";
import { defineComponent, h, onBeforeUnmount, onMounted, ref } from "vue";

import { api, ApiError } from "./api";
import "./public.css";

function icon(kind: "play" | "arrow", className = "") {
  return h("svg", { class: className, viewBox: "0 0 24 24", width: 18, height: 18, "aria-hidden": "true", fill: "none" }, [
    h("path", kind === "play" ? { d: "M8 5v14l11-7z", fill: "currentColor" } : { d: "M6 18 18 6M6 6h12v12", stroke: "currentColor", "stroke-width": 1.5 }),
  ]);
}

type PublicVideo = { id: string; title: string; watch_url: string; playlist_url: string; thumbnail_url?: string };

const Player = defineComponent({
  props: { source: { type: String, required: true }, poster: String },
  setup(props) {
    const element = ref<HTMLVideoElement>();
    const levels = ref<string[]>([]);
    const selection = ref(-1);
    const active = ref(-1);
    const error = ref("");
    let engine: Hls | undefined;
    let disposed = false;

    onMounted(async () => {
      try {
        const { default: Engine } = await import("hls.js");
        if (disposed || !element.value) return;
        if (!Engine.isSupported()) {
          error.value = "В этом браузере HLS недоступен.";
          return;
        }
        engine = new Engine();
        engine.on(Engine.Events.MANIFEST_PARSED, (_, data) => {
          levels.value = data.levels.map(({ width, height }) => {
            const shortSide = Math.min(width, height);
            const dimensions = width + "×" + height;
            return [360, 480, 720].includes(shortSide) ? shortSide + "p · " + dimensions : dimensions;
          });
        });
        engine.on(Engine.Events.LEVEL_SWITCHED, (_, data) => {
          active.value = data.level;
        });
        engine.on(Engine.Events.ERROR, (_, data) => {
          if (data.fatal) error.value = "Не удалось воспроизвести видео. Обновите страницу.";
        });
        engine.on(Engine.Events.MEDIA_ATTACHED, () => engine?.loadSource(props.source));
        engine.attachMedia(element.value);
      } catch {
        error.value = "Не удалось загрузить плеер.";
      }
    });
    onBeforeUnmount(() => {
      disposed = true;
      engine?.destroy();
    });

    function changeQuality(event: Event) {
      const select = event.target as HTMLSelectElement;
      selection.value = Number(select.value);
      if (engine) engine.currentLevel = selection.value;
    }

    return () => h("div", { class: "screening" }, [
      h("video", {
        class: "player", ref: element, controls: true, playsinline: true, preload: "metadata", poster: props.poster,
        "aria-label": "Видеоплеер", "data-active-level": active.value,
      }),
      levels.value.length ? h("label", { class: "quality" }, [
        h("span", "Качество воспроизведения"),
        h("select", { value: selection.value, onChange: changeQuality }, [
          h("option", { value: -1 }, "Auto · автоматически"),
          ...levels.value.map((label, index) => h("option", { value: index, key: index }, label)),
        ]),
      ]) : null,
      error.value ? h("p", { role: "alert" }, error.value) : null,
    ]);
  },
});

export const PublicVideos = defineComponent({
  props: { videoId: { type: String, required: true } },
  setup(props) {
    const items = ref<PublicVideo[]>([]);
    const video = ref<PublicVideo>();
    const query = ref(new URLSearchParams(window.location.search).get("q") ?? "");
    const busy = ref(true);
    const searched = ref(query.value);
    const failedImages = ref(new Set<string>());
    const error = ref("");

    async function load() {
      busy.value = true;
      error.value = "";
      const requestedQuery = query.value;
      try {
        if (props.videoId) {
          video.value = await api<PublicVideo>("/api/catalog/" + encodeURIComponent(props.videoId));
        } else {
          const params = new URLSearchParams({ q: requestedQuery });
          items.value = (await api<{ videos: PublicVideo[] }>("/api/catalog?" + params)).videos;
          searched.value = requestedQuery.trim();
          window.history.replaceState(null, "", "/catalog" + (requestedQuery ? "?" + params : ""));
        }
      } catch (failure) {
        error.value = failure instanceof ApiError && failure.status === 404
          ? "Видео недоступно."
          : "Не удалось загрузить данные. Повторите запрос.";
      } finally {
        busy.value = false;
      }
    }

    onMounted(load);

    const play = () => h("span", { class: "play-mark", "aria-hidden": "true" }, [icon("play")]);
    const resetSearch = () => { query.value = ""; void load(); };

    return () => h("div", { class: "public-site" }, [
      h("a", { class: "skip-link", href: "#public-content" }, "К содержимому"),
      h("header", { class: "site-header" }, [
        h("a", { class: "brand", href: "/catalog", "aria-label": "VidHarbor — каталог" }, [
          h("span", { class: "brand-icon", "aria-hidden": "true" }, [icon("play")]), "VidHarbor",
        ]),
        h("nav", { "aria-label": "Основная навигация" }, [
          h("a", { href: "/catalog", "aria-current": props.videoId ? undefined : "page" }, "Каталог"),
          h("a", { href: "/", class: "author-link" }, ["Кабинет автора ", icon("arrow")]),
        ]),
      ]),
      h("main", { id: "public-content", class: props.videoId ? "public-main watch-main" : "public-main" }, [
        props.videoId
          ? h("div", [
              h("a", { class: "back-link", href: "/catalog" }, "← Все видео"),
              video.value ? h("section", { "aria-labelledby": "watch-title" }, [
                h("div", { class: "watch-heading" }, [
                  h("div", [h("p", { class: "eyebrow" }, "СЕЙЧАС НА ЭКРАНЕ"), h("h1", { id: "watch-title" }, video.value.title)]),
                  h("span", { class: "stream-label" }, "ВИДЕО"),
                ]),
                h(Player, { source: video.value.playlist_url, poster: video.value.thumbnail_url }),
                h("p", { class: "watch-note" }, "Ваш темп. Ваш экран. Качество можно изменить в любой момент."),
              ]) : null,
            ])
          : h("section", { "aria-labelledby": "catalog-title" }, [
              h("div", { class: "catalog-intro" }, [
                h("p", { class: "eyebrow" }, "ВИДЕО, КОТОРЫЕ СТОИТ УВИДЕТЬ"),
                h("h1", { id: "catalog-title" }, ["Найдите своё.", h("br"), h("span", "Нажмите play.")]),
                h("p", { class: "intro-copy" }, "Истории, идеи и новые взгляды. Выберите видео и погрузитесь в просмотр."),
                h("form", { class: "catalog-search", role: "search", onSubmit: (event: Event) => { event.preventDefault(); void load(); } }, [
                  h("label", { class: "sr-only", for: "video-search" }, "Поиск по названию видео"),
                  h("span", { class: "search-icon", "aria-hidden": "true" }, "⌕"),
                  h("input", {
                    id: "video-search", type: "search", placeholder: "Какое видео ищем?", value: query.value, maxlength: 200,
                    onInput: (event: Event) => { query.value = (event.target as HTMLInputElement).value; },
                  }),
                  h("button", { type: "submit", disabled: busy.value }, "Найти →"),
                ]),
              ]),
              h("div", { class: "collection-heading" }, [
                h("h2", searched.value ? "Результаты поиска" : "Все видео"),
                h("span", { role: "status", "aria-live": "polite" }, busy.value ? "Загружаем…" : error.value ? "" : items.value.length + " видео"),
              ]),
              !busy.value && !error.value
                ? items.value.length
                  ? h("ul", { class: "video-grid" }, items.value.map((item, index) =>
                      h("li", { key: item.id }, [h("a", { href: item.watch_url, class: "video-card" }, [
                        h("div", { class: ["card-frame", "frame-" + index % 3] }, [
                          item.thumbnail_url && !failedImages.value.has(item.id)
                            ? h("img", { src: item.thumbnail_url, alt: "", loading: "lazy", onError: () => { failedImages.value = new Set([...failedImages.value, item.id]); } })
                            : h("div", { class: "cover-fallback", "aria-hidden": "true" }, [h("span", { class: "fallback-word" }, "vh"), h("span", { class: "fallback-caption" }, "VIDHARBOR / ВИДЕО")]),
                          play(),
                          h("span", { class: "frame-label" }, "СМОТРЕТЬ"),
                        ]),
                        h("div", { class: "card-caption" }, [h("h3", item.title), icon("arrow", "card-arrow")]),
                        h("p", { class: "card-meta" }, "Видео · Смотреть онлайн"),
                      ])])))
                  : h("div", { class: "empty-state" }, [
                      h("span", { class: "empty-icon", "aria-hidden": "true" }, "⌕"),
                      h("h3", searched.value ? "Ничего не нашлось" : "Скоро здесь будет что посмотреть"),
                      h("p", searched.value ? "Попробуйте другое название или вернитесь ко всем видео." : "Опубликованные видео появятся в каталоге."),
                      searched.value ? h("button", { onClick: resetSearch }, "Показать все видео") : null,
                    ])
                : null,
            ]),
        busy.value && props.videoId ? h("p", { class: "page-message", role: "status" }, "Готовим экран…") : null,
        error.value ? h("div", { class: "page-message" }, [h("p", { role: "alert" }, error.value), h("button", { onClick: load, disabled: busy.value }, "Повторить")]) : null,
      ]),
      h("footer", { class: "site-footer" }, [h("span", "VidHarbor"), h("span", "Место для ваших видео.")]),
    ]);
  },
});
