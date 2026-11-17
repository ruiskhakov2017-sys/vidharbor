import type Hls from "hls.js";
import {
  computed,
  defineComponent,
  h,
  onBeforeUnmount,
  onMounted,
  ref,
} from "vue";

import { api, ApiError } from "./api";
import "./public.css";

type IconName =
  | "play"
  | "arrow"
  | "search"
  | "back"
  | "next"
  | "close"
  | "share"
  | "screen"
  | "menu"
  | "home"
  | "grid"
  | "plus"
  | "nature"
  | "city"
  | "travel"
  | "architecture"
  | "film"
  | "brand"
  | "largeGrid"
  | "speed"
  | "quality"
  | "bookmark"
  | "headphones"
  | "music"
  | "sport"
  | "food";
function icon(kind: IconName, className = "") {
  const paths = {
    brand: "M3 8v8M7 4v16M12 5l9 7-9 7z",
    bookmark: "M6 3h12v18l-6-4-6 4z",
    headphones: "M4 14v-3a8 8 0 0 1 16 0v3M4 12h3v8H4zM17 12h3v8h-3z",
    music:
      "M9 18V5l12-2v13M9 5v5l12-2M9 18a3 3 0 1 1-3-3c2 0 3 1 3 3ZM21 16a3 3 0 1 1-3-3c2 0 3 1 3 3Z",
    sport: "m4 7 3-3 13 13-3 3zM2 10l8-8M14 22l8-8",
    food: "M5 3v6c0 3 6 3 6 0V3M8 3v18M19 3c-4 3-4 9 0 10V3Zm0 10v8",
    largeGrid: "M3 4h18v6H3zM3 14h18v6H3z",
    speed: "M4 17a9 9 0 1 1 16 0M12 13l5-5M5 11l2 1m4-7v2m8 5 2-1",
    quality: "m12 3 8 4v5c0 4-4 7-8 9-4-2-8-5-8-9V7zM8 12l3 3 5-6",
    menu: "M4 6h16M4 12h16M4 18h16",
    home: "m3 10 9-7 9 7v11h-6v-7H9v7H3z",
    grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
    plus: "M12 5v14M5 12h14",
    nature: "M20 3C10 2 3 6 4 14c1 7 13 10 16-11ZM4 21 15 9",
    city: "M3 21V9h6V3h7v10h5v8M7 13h1m4-6h1m-1 4h1m-1 4h1m4 2h1",
    travel: "M3 9h18v12H3zM8 9V4h8v5M7 9v12M17 9v12",
    architecture: "m3 9 9-6 9 6M3 21h18M5 9v12M10 9v12M14 9v12M19 9v12",
    film: "M3 3h18v18H3zM7 3v18M17 3v18M3 8h4m10 0h4M3 16h4m10 0h4",
    play: "M9 5v14l11-7z",
    arrow: "M6 18 18 6M6 6h12v12",
    search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
    back: "M15 5l-7 7 7 7",
    next: "m9 5 7 7-7 7",
    close: "m6 6 12 12M6 18 18 6",
    share: "M12 16V3m-5 5 5-5 5 5M5 14v7h14v-7",
    screen: "M3 7V3h4m10 0h4v4M3 17v4h4m14-4v4h-4",
  };
  return h(
    "svg",
    {
      class: className,
      viewBox: "0 0 24 24",
      width: 20,
      height: 20,
      "aria-hidden": "true",
      fill: kind === "play" ? "currentColor" : "none",
      stroke: kind === "play" ? "none" : "currentColor",
      "stroke-width": 1.5,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    },
    [h("path", { d: paths[kind] })],
  );
}
type PublicVideo = {
  id: string;
  title: string;
  watch_url: string;
  playlist_url: string;
  thumbnail_url?: string;
  category?: string;
  creator?: string;
  duration_seconds?: number;
  description?: string;
  demo?: boolean;
};
function duration(seconds?: number) {
  if (!seconds) return "";
  return (
    Math.floor(seconds / 60) +
    ":" +
    String(Math.floor(seconds % 60)).padStart(2, "0")
  );
}

const Player = defineComponent({
  props: { source: { type: String, required: true }, poster: String },
  setup(props) {
    const element = ref<HTMLVideoElement>();
    const levels = ref<string[]>([]);
    const selection = ref(-1);
    const speed = ref(1);
    const posterFailed = ref(false);
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
            return shortSide ? shortSide + "p" : width + "×" + height;
          });
        });
        engine.on(Engine.Events.LEVEL_SWITCHED, (_, data) => {
          active.value = data.level;
        });
        engine.on(Engine.Events.ERROR, (_, data) => {
          if (data.fatal)
            error.value = "Не удалось воспроизвести видео. Обновите страницу.";
        });
        engine.on(Engine.Events.MEDIA_ATTACHED, () =>
          engine?.loadSource(props.source),
        );
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

    function changeSpeed(event: Event) {
      speed.value = Number((event.target as HTMLSelectElement).value);
      if (element.value) element.value.playbackRate = speed.value;
    }
    return () =>
      h("div", { class: "screening" }, [
        props.poster && !posterFailed.value
          ? h("div", { class: "player-ambient", "aria-hidden": "true" }, [
              h("img", {
                src: props.poster,
                alt: "",
                onError: () => {
                  posterFailed.value = true;
                },
              }),
            ])
          : null,
        h("video", {
          class: "player",
          ref: element,
          controls: true,
          playsinline: true,
          preload: "metadata",
          poster: props.poster,
          "aria-label": "Видеоплеер",
          "data-active-level": active.value,
          onRatechange: () => {
            speed.value = element.value?.playbackRate ?? 1;
          },
        }),
        levels.value.length
          ? h("div", { class: "player-toolbar" }, [
              h("label", { class: "playback-speed" }, [
                icon("speed"),
                h("span", "Скорость"),
                h(
                  "select",
                  {
                    value: speed.value,
                    onChange: changeSpeed,
                    "aria-label": "Скорость воспроизведения",
                  },
                  [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2].map((rate) =>
                    h("option", { value: rate, key: rate }, rate + "×"),
                  ),
                ),
              ]),
              h("label", { class: "quality" }, [
                icon("quality"),
                h("span", "Качество"),
                h(
                  "select",
                  {
                    value: selection.value,
                    onChange: changeQuality,
                    "aria-label": "Качество воспроизведения",
                  },
                  [
                    h(
                      "option",
                      { value: -1 },
                      active.value >= 0
                        ? "Auto · " + levels.value[active.value]
                        : "Auto · автоматически",
                    ),
                    ...levels.value.map((label, index) =>
                      h("option", { value: index, key: index }, label),
                    ),
                  ],
                ),
              ]),
            ])
          : null,
        error.value ? h("p", { role: "alert" }, error.value) : null,
      ]);
  },
});

const Preview = defineComponent({
  props: { source: { type: String, required: true } },
  setup(props) {
    const element = ref<HTMLVideoElement>();
    const playing = ref(false);
    let engine: Hls | undefined;
    let disposed = false;
    onMounted(async () => {
      try {
        const { default: Engine } = await import("hls.js");
        if (disposed || !element.value || !Engine.isSupported()) return;
        engine = new Engine({ maxBufferLength: 6, capLevelToPlayerSize: true });
        engine.on(Engine.Events.MEDIA_ATTACHED, () =>
          engine?.loadSource(props.source),
        );
        engine.on(Engine.Events.MANIFEST_PARSED, () => {
          void element.value?.play().catch(() => {});
        });
        engine.on(Engine.Events.ERROR, (_, data) => {
          if (data.fatal) {
            playing.value = false;
            engine?.destroy();
          }
        });
        engine.attachMedia(element.value);
      } catch {
        /* A failed optional preview leaves the thumbnail and link usable. */
      }
    });
    onBeforeUnmount(() => {
      disposed = true;
      engine?.destroy();
    });
    return () =>
      h(
        "div",
        { class: ["thumbnail-preview", { playing }], "aria-hidden": "true" },
        [
          h("video", {
            ref: element,
            muted: true,
            loop: true,
            playsinline: true,
            onPlaying: () => {
              playing.value = true;
            },
          }),
          h("span", "Без звука"),
        ],
      );
  },
});

export const PublicVideos = defineComponent({
  props: { videoId: { type: String, required: true } },
  setup(props) {
    const isWatch = !!props.videoId;
    const initialParams = new URLSearchParams(window.location.search);
    const query = ref(initialParams.get("q") ?? "");
    const searched = ref(query.value.trim());
    const category = ref(initialParams.get("topic") || "Все видео");
    const items = ref<PublicVideo[]>([]);
    const video = ref<PublicVideo>();
    type FeedView = "feed" | "saved" | "topics";
    const initialView = initialParams.get("view");
    const feedView = ref<FeedView>(
      initialView === "saved" || initialView === "topics"
        ? initialView
        : "feed",
    );
    const saved = ref<string[]>([]);
    const activePreview = ref("");
    const previewsEnabled = ref(true);
    const pointerAllowsPreview = window.matchMedia(
      "(hover: hover) and (pointer: fine)",
    );
    let previewTimer: number | undefined;
    try {
      const value: unknown = JSON.parse(
        localStorage.getItem("vidharbor.public.saved") || "[]",
      );
      if (Array.isArray(value))
        saved.value = value
          .filter((id): id is string => typeof id === "string")
          .slice(0, 500);
    } catch {
      /* Browsing still works with storage disabled. */
    }
    function toggleSaved(item: PublicVideo) {
      saved.value = saved.value.includes(item.id)
        ? saved.value.filter((id) => id !== item.id)
        : [...saved.value.slice(-499), item.id];
      try {
        localStorage.setItem(
          "vidharbor.public.saved",
          JSON.stringify(saved.value),
        );
      } catch {
        /* Keep the current session usable. */
      }
    }
    function stopPreview() {
      window.clearTimeout(previewTimer);
      activePreview.value = "";
    }
    function startPreview(item: PublicVideo) {
      stopPreview();
      if (
        !previewsEnabled.value ||
        !pointerAllowsPreview.matches ||
        window.matchMedia("(prefers-reduced-motion: reduce)").matches
      )
        return;
      previewTimer = window.setTimeout(() => {
        activePreview.value = item.id;
      }, 350);
    }
    function chooseView(view: FeedView) {
      if (isWatch) {
        window.location.assign(catalogUrl("", "Все видео", view));
        return;
      }
      feedView.value = view;
      category.value = "Все видео";
      query.value = "";
      stopPreview();
      if (!desktop.matches) closeMenu();
      void load();
    }
    function saveButton(item: PublicVideo, withText = false) {
      const selected = saved.value.includes(item.id);
      return h(
        "button",
        {
          class: withText ? "action-button save-action" : "card-save",
          "aria-label": selected
            ? "Убрать из сохранённого: " + item.title
            : "Смотреть позже: " + item.title,
          "aria-pressed": selected,
          title: selected ? "Убрать из сохранённого" : "Смотреть позже",
          onClick: () => toggleSaved(item),
        },
        [
          icon("bookmark"),
          withText ? (selected ? "Сохранено" : "Смотреть позже") : null,
        ],
      );
    }
    const topics = ref<string[]>([]);
    const busy = ref(true);
    const error = ref("");
    const failedImages = ref(new Set<string>());
    const desktop = window.matchMedia("(min-width: 1100px)");
    const menuOpen = ref(desktop.matches && !isWatch);
    const menuButton = ref<HTMLButtonElement>();
    const searchInput = ref<HTMLInputElement>();
    const largeCards = ref(false);
    try {
      largeCards.value =
        localStorage.getItem("vidharbor.public.signal.largeCards") === "true";
    } catch {
      /* Storage can be unavailable. */
    }
    function setCardSize(large: boolean) {
      largeCards.value = large;
      try {
        localStorage.setItem(
          "vidharbor.public.signal.largeCards",
          String(large),
        );
      } catch {
        /* The view still works without persistence. */
      }
    }
    const cinema = ref(false);
    const copied = ref("");
    const relatedTopic = ref(false);
    let requestId = 0;
    let copyTimer: number | undefined;
    const categories = computed(() => ["Все видео", ...topics.value]);
    const filtered = computed(() =>
      items.value.filter(
        (item) =>
          (category.value === "Все видео" ||
            item.category === category.value) &&
          (feedView.value !== "saved" || saved.value.includes(item.id)),
      ),
    );
    const recommendations = computed(() =>
      items.value.filter(
        (item) =>
          item.id !== props.videoId &&
          (!relatedTopic.value || item.category === video.value?.category),
      ),
    );
    function catalogUrl(
      q = "",
      topic = "Все видео",
      view: FeedView = feedView.value,
    ) {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (view !== "feed") params.set("view", view);
      if (topic !== "Все видео") params.set("topic", topic);
      return "/catalog" + (params.size ? "?" + params.toString() : "");
    }
    function rememberTopics(videos: PublicVideo[]) {
      topics.value = [
        ...new Set([
          ...topics.value,
          ...videos
            .map((item) => item.category)
            .filter((name): name is string => !!name),
        ]),
      ];
    }
    async function load() {
      const request = ++requestId;
      const requestedQuery = query.value.trim();
      busy.value = true;
      error.value = "";
      try {
        if (isWatch) {
          video.value = await api<PublicVideo>(
            "/api/catalog/" + encodeURIComponent(props.videoId),
          );
          void api<{ videos: PublicVideo[] }>("/api/catalog")
            .then((result) => {
              if (request !== requestId) return;
              items.value = result.videos;
              rememberTopics(result.videos);
            })
            .catch(() => {});
        } else {
          const result = await api<{ videos: PublicVideo[] }>(
            "/api/catalog?" + new URLSearchParams({ q: requestedQuery }),
          );
          if (request !== requestId) return;
          items.value = result.videos;
          rememberTopics(result.videos);
          searched.value = requestedQuery;
          window.history.replaceState(
            null,
            "",
            catalogUrl(requestedQuery, category.value),
          );
        }
      } catch (failure) {
        if (request !== requestId) return;
        error.value =
          failure instanceof ApiError && failure.status === 404
            ? "Видео недоступно."
            : "Не удалось загрузить видео. Попробуйте ещё раз.";
      } finally {
        if (request === requestId) busy.value = false;
      }
    }
    function viewportChanged() {
      menuOpen.value = desktop.matches && !isWatch;
    }
    function closeMenu() {
      menuOpen.value = false;
    }
    function escapeMenu(event: KeyboardEvent) {
      const editing =
        event.target instanceof Element &&
        event.target.closest("input,textarea,select,[contenteditable]");
      if (
        event.key === "/" &&
        !editing &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey
      ) {
        event.preventDefault();
        searchInput.value?.focus();
        return;
      }
      if (event.key === "Escape" && menuOpen.value) {
        menuOpen.value = false;
        menuButton.value?.focus();
      }
    }
    onMounted(() => {
      void load();
      desktop.addEventListener("change", viewportChanged);
      window.addEventListener("keydown", escapeMenu);
    });
    onBeforeUnmount(() => {
      ++requestId;
      window.clearTimeout(copyTimer);
      stopPreview();
      desktop.removeEventListener("change", viewportChanged);
      window.removeEventListener("keydown", escapeMenu);
    });
    function submitSearch(event: Event) {
      event.preventDefault();
      if (isWatch) {
        window.location.assign(
          catalogUrl(query.value.trim(), "Все видео", "feed"),
        );
        return;
      }
      category.value = "Все видео";
      feedView.value = "feed";
      stopPreview();
      void load();
    }
    function reset() {
      query.value = "";
      category.value = "Все видео";
      feedView.value = "feed";
      stopPreview();
      void load();
    }
    function clearSearch() {
      query.value = "";
      if (!isWatch) reset();
      searchInput.value?.focus();
    }
    function selectCategory(name: string) {
      if (isWatch) {
        window.location.assign(catalogUrl("", name, "feed"));
        return;
      }
      feedView.value = "feed";
      category.value = name;
      stopPreview();
      window.history.replaceState(null, "", catalogUrl(searched.value, name));
      if (!desktop.matches) closeMenu();
    }
    async function share() {
      try {
        await navigator.clipboard.writeText(window.location.href);
        copied.value = "Ссылка скопирована";
      } catch {
        copied.value = "Скопируйте адрес из строки браузера";
      }
      window.clearTimeout(copyTimer);
      copyTimer = window.setTimeout(() => {
        copied.value = "";
      }, 4000);
    }
    function cover(item: PublicVideo) {
      return item.thumbnail_url && !failedImages.value.has(item.id)
        ? h("img", {
            src: item.thumbnail_url,
            alt: "",
            loading: "lazy",
            decoding: "async",
            onError: () => {
              failedImages.value = new Set([...failedImages.value, item.id]);
            },
          })
        : h("div", { class: "cover-fallback", "aria-hidden": "true" }, [
            icon("brand"),
            h("span", "Видео без обложки"),
          ]);
    }
    function avatar(item: PublicVideo) {
      return h(
        "span",
        { class: "creator-avatar", "aria-hidden": "true" },
        item.creator ? item.creator.slice(0, 1).toUpperCase() : "V",
      );
    }
    function card(item: PublicVideo, compact = false) {
      return h(
        "li",
        {
          key: item.id,
          class: ["card-entry", { "compact-entry": compact }],
          onMouseenter: () => startPreview(item),
          onMouseleave: stopPreview,
        },
        [
          h(
            "a",
            {
              href: item.watch_url,
              class: ["video-card", { compact }],
              "data-topic": item.category,
            },
            [
              h("div", { class: "card-frame" }, [
                cover(item),
                activePreview.value === item.id
                  ? h(Preview, { source: item.playlist_url })
                  : null,
                duration(item.duration_seconds)
                  ? h(
                      "span",
                      { class: "duration" },
                      duration(item.duration_seconds),
                    )
                  : null,
                h("span", { class: "hover-play", "aria-hidden": "true" }, [
                  h("span", { class: "card-play-action" }, [
                    icon("play"),
                    h("span", "Смотреть"),
                  ]),
                ]),
              ]),
              h("div", { class: "card-info" }, [
                avatar(item),
                h("div", { class: "card-text" }, [
                  h("h3", item.title),
                  h(
                    "p",
                    { class: "card-creator" },
                    item.creator || "VidHarbor",
                  ),
                  h("p", { class: "card-meta" }, [
                    item.category || "Видео",
                    item.demo
                      ? h("span", { class: "demo-label" }, "Демо")
                      : null,
                  ]),
                ]),
              ]),
            ],
          ),
          !compact ? saveButton(item) : null,
        ],
      );
    }
    function topicIcon(name: string): IconName {
      const icons: Record<string, IconName> = {
        "Все видео": "home",
        Природа: "nature",
        Город: "city",
        Путешествия: "travel",
        Архитектура: "architecture",
        Кино: "film",
        Подкасты: "headphones",
        Музыка: "music",
        Спорт: "sport",
        Еда: "food",
        Кулинария: "food",
      };
      return icons[name] || "grid";
    }
    function sidebar() {
      return h(
        "aside",
        {
          id: "portal-navigation",
          class: "portal-sidebar",
          hidden: !menuOpen.value,
          "aria-label": "Навигация по видео",
        },
        [
          h("nav", { "aria-label": "Разделы" }, [
            ...(
              [
                ["feed", "home", "Обзор"],
                ["saved", "bookmark", "Смотреть позже"],
                ["topics", "grid", "Все темы"],
              ] as const
            ).map(([view, symbol, label]) =>
              h(
                "button",
                {
                  class: [
                    "sidebar-item",
                    {
                      selected:
                        !isWatch &&
                        feedView.value === view &&
                        category.value === "Все видео",
                    },
                  ],
                  "aria-pressed":
                    !isWatch &&
                    feedView.value === view &&
                    category.value === "Все видео",
                  onClick: () => chooseView(view),
                },
                [
                  icon(symbol),
                  h("span", label),
                  view === "saved" && saved.value.length
                    ? h("span", { class: "topic-count" }, saved.value.length)
                    : null,
                ],
              ),
            ),
            h("div", { class: "sidebar-divider" }),
            topics.value.length
              ? [
                  h("p", { class: "sidebar-label" }, "ОТКРЫВАЙТЕ"),
                  ...topics.value.slice(0, 7).map((name) =>
                    h(
                      "button",
                      {
                        class: [
                          "sidebar-item",
                          { selected: !isWatch && category.value === name },
                        ],
                        "aria-pressed": !isWatch && category.value === name,
                        onClick: () => selectCategory(name),
                      },
                      [
                        icon(topicIcon(name)),
                        name,
                        h(
                          "span",
                          { class: "topic-count", "aria-hidden": "true" },
                          items.value.filter((item) => item.category === name)
                            .length,
                        ),
                      ],
                    ),
                  ),
                ]
              : null,
          ]),
          h("div", { class: "sidebar-bottom" }, [
            h("a", { href: "/", class: "sidebar-item" }, [
              icon("plus"),
              h("span", ["Кабинет автора", h("small", "Управление видео")]),
            ]),
            h("p", ["VidHarbor", h("span", "Видео. Люди. Идеи.")]),
          ]),
        ],
      );
    }
    function search() {
      return h(
        "form",
        { class: "portal-search", role: "search", onSubmit: submitSearch },
        [
          h("label", { for: "video-search", class: "sr-only" }, "Поиск видео"),
          h("input", {
            id: "video-search",
            ref: searchInput,
            type: "search",
            placeholder: "Найти видео",
            maxlength: 200,
            value: query.value,
            onInput: (event: Event) => {
              query.value = (event.target as HTMLInputElement).value;
            },
          }),
          query.value
            ? h(
                "button",
                {
                  class: "search-clear",
                  type: "button",
                  "aria-label": "Очистить поиск",
                  onClick: clearSearch,
                },
                [icon("close")],
              )
            : h(
                "kbd",
                { class: "search-shortcut", "aria-hidden": "true" },
                "/",
              ),
          h(
            "button",
            {
              type: "submit",
              "aria-label": "Найти видео",
              disabled: busy.value && !isWatch,
            },
            [icon("search")],
          ),
        ],
      );
    }
    function topicChips() {
      return h(
        "div",
        { class: "topic-chips", "aria-label": "Темы видео" },
        categories.value.map((name) =>
          h(
            "button",
            {
              "aria-pressed": category.value === name,
              onClick: () => selectCategory(name),
            },
            name,
          ),
        ),
      );
    }
    function topicGrid() {
      return h(
        "div",
        { class: "topic-grid" },
        topics.value.map((name) => {
          const videos = items.value.filter((item) => item.category === name);
          const sample =
            videos.find(
              (item) => item.thumbnail_url && !failedImages.value.has(item.id),
            ) || videos[0];
          return h(
            "button",
            { class: "topic-tile", onClick: () => selectCategory(name) },
            [
              sample
                ? h("span", { class: "topic-image" }, [cover(sample)])
                : null,
              h("span", { class: "topic-copy" }, [
                h("strong", name),
                h("small", videos.length + " видео"),
              ]),
              icon("arrow"),
            ],
          );
        }),
      );
    }
    function feed() {
      const videos = filtered.value;
      const firstBatch = largeCards.value ? 6 : 8;
      const showTopics =
        feedView.value === "feed" &&
        !searched.value &&
        category.value === "Все видео" &&
        topics.value.length > 2 &&
        videos.length > firstBatch;
      return showTopics
        ? [
            h(
              "ul",
              { class: "video-grid" },
              videos.slice(0, firstBatch).map((item) => card(item)),
            ),
            h(
              "section",
              { class: "discover-strip", "aria-label": "Откройте новые темы" },
              [
                h("div", { class: "section-heading" }, [
                  h("h2", "У каждого — свой интерес"),
                  h("button", { onClick: () => chooseView("topics") }, [
                    "Все темы",
                    icon("next"),
                  ]),
                ]),
                topicGrid(),
              ],
            ),
            h("div", { class: "section-heading feed-heading" }, [
              h("h2", "Ещё видео"),
              h("span", videos.length - firstBatch + " видео"),
            ]),
            h(
              "ul",
              { class: "video-grid" },
              videos.slice(firstBatch).map((item) => card(item)),
            ),
          ]
        : h(
            "ul",
            { class: "video-grid" },
            videos.map((item) => card(item)),
          );
    }
    function skeleton() {
      return h("div", { class: "catalog-loading", role: "status" }, [
        h("span", { class: "sr-only" }, "Загружаем видео…"),
        h(
          "div",
          { class: "skeleton-grid", "aria-hidden": "true" },
          Array.from({ length: 9 }, (_, index) =>
            h("div", { key: index, class: "skeleton-card" }, [
              h("div", { class: "skeleton-image" }),
              h("div", { class: "skeleton-info" }, [
                h("i"),
                h("div", [h("span"), h("span")]),
              ]),
            ]),
          ),
        ),
      ]);
    }
    return () =>
      h(
        "div",
        {
          class: [
            "public-site",
            isWatch ? "watch-view" : "catalog-view",
            {
              "nav-open": menuOpen.value,
              "cinema-mode": cinema.value,
              "large-cards": largeCards.value,
            },
          ],
        },
        [
          h(
            "a",
            { href: "#public-content", class: "skip-link" },
            "К содержимому",
          ),
          h("header", { class: "portal-header" }, [
            h("div", { class: "header-brand" }, [
              h(
                "button",
                {
                  ref: menuButton,
                  class: "icon-button menu-button",
                  "aria-label": menuOpen.value ? "Скрыть меню" : "Открыть меню",
                  "aria-controls": "portal-navigation",
                  "aria-expanded": menuOpen.value,
                  onClick: () => {
                    menuOpen.value = !menuOpen.value;
                  },
                },
                [icon("menu")],
              ),
              h(
                "a",
                {
                  href: "/catalog",
                  class: "brand",
                  "aria-label": "VidHarbor — каталог",
                },
                [
                  h("span", { class: "brand-mark" }, [icon("brand")]),
                  h("span", "vidharbor"),
                ],
              ),
            ]),
            search(),
            h(
              "a",
              {
                href: "/",
                class: "upload-link",
                "aria-label": "Загрузить видео",
              },
              [icon("plus"), h("span", "Загрузить видео")],
            ),
          ]),
          sidebar(),
          menuOpen.value
            ? h("button", {
                class: "navigation-scrim",
                onClick: closeMenu,
                "aria-label": "Закрыть меню",
                tabindex: -1,
              })
            : null,
          h(
            "main",
            { id: "public-content", class: "portal-main" },
            isWatch
              ? [
                  h("div", { class: "watch-topline" }, [
                    h("a", { href: "/catalog", class: "back-link" }, [
                      icon("back"),
                      "В каталог",
                    ]),
                    recommendations.value[0]
                      ? h(
                          "a",
                          {
                            class: "next-video-link",
                            href: recommendations.value[0].watch_url,
                          },
                          ["Следующее видео", icon("next")],
                        )
                      : null,
                  ]),
                  video.value
                    ? h("div", { class: "watch-layout" }, [
                        h(
                          "section",
                          {
                            class: "watch-primary",
                            "aria-labelledby": "watch-title",
                          },
                          [
                            h(Player, {
                              key: video.value.id,
                              source: video.value.playlist_url,
                              poster: video.value.thumbnail_url,
                            }),
                            h("h1", { id: "watch-title" }, video.value.title),
                            h("div", { class: "watch-metadata" }, [
                              h("div", { class: "watch-creator" }, [
                                avatar(video.value),
                                h("div", [
                                  h(
                                    "strong",
                                    video.value.creator || "VidHarbor",
                                  ),
                                  h(
                                    "span",
                                    video.value.demo
                                      ? "Демонстрационный ролик"
                                      : video.value.category || "Видео",
                                  ),
                                ]),
                              ]),
                              h("div", { class: "watch-actions" }, [
                                saveButton(video.value, true),
                                h(
                                  "button",
                                  { class: "action-button", onClick: share },
                                  [icon("share"), "Поделиться"],
                                ),
                                h(
                                  "button",
                                  {
                                    class: "icon-button cinema-button",
                                    "aria-label": "Режим кинотеатра",
                                    "aria-pressed": cinema.value,
                                    onClick: () => {
                                      cinema.value = !cinema.value;
                                    },
                                  },
                                  [icon("screen")],
                                ),
                              ]),
                            ]),
                            h(
                              "p",
                              { class: "share-status", role: "status" },
                              copied.value,
                            ),
                            h("div", { class: "video-description" }, [
                              h("div", { class: "description-meta" }, [
                                h("span", video.value.category || "Видео"),
                                duration(video.value.duration_seconds)
                                  ? h(
                                      "span",
                                      duration(video.value.duration_seconds),
                                    )
                                  : null,
                                video.value.demo
                                  ? h("span", { class: "demo-label" }, "Демо")
                                  : null,
                              ]),
                              h(
                                "p",
                                video.value.description ||
                                  "Смотрите видео и делитесь прямой ссылкой.",
                              ),
                            ]),
                          ],
                        ),
                        recommendations.value.length || relatedTopic.value
                          ? h(
                              "aside",
                              {
                                class: "watch-related",
                                "aria-labelledby": "related-title",
                              },
                              [
                                h("h2", { id: "related-title" }, [
                                  "Далее",
                                  h(
                                    "span",
                                    { class: "related-count" },
                                    recommendations.value.length + " видео",
                                  ),
                                ]),
                                video.value.category
                                  ? h("div", { class: "related-chips" }, [
                                      h(
                                        "button",
                                        {
                                          "aria-pressed": !relatedTopic.value,
                                          onClick: () => {
                                            relatedTopic.value = false;
                                          },
                                        },
                                        "Все видео",
                                      ),
                                      h(
                                        "button",
                                        {
                                          "aria-pressed": relatedTopic.value,
                                          onClick: () => {
                                            relatedTopic.value = true;
                                          },
                                        },
                                        video.value.category,
                                      ),
                                    ])
                                  : null,
                                recommendations.value.length
                                  ? h(
                                      "ul",
                                      { class: "related-list" },
                                      recommendations.value.map((item) =>
                                        card(item, true),
                                      ),
                                    )
                                  : h(
                                      "p",
                                      { class: "related-empty" },
                                      "Других видео на эту тему пока нет.",
                                    ),
                              ],
                            )
                          : null,
                      ])
                    : null,
                  busy.value
                    ? h(
                        "p",
                        { role: "status", class: "loading-state" },
                        "Загружаем видео…",
                      )
                    : null,
                  error.value
                    ? h("div", { class: "error-state" }, [
                        h("p", { role: "alert" }, error.value),
                        h(
                          "button",
                          { class: "action-button", onClick: load },
                          "Повторить",
                        ),
                      ])
                    : null,
                ]
              : [
                  h(
                    "section",
                    {
                      class: "catalog-section",
                      "aria-labelledby": "catalog-title",
                      "aria-busy": busy.value,
                    },
                    [
                      h("div", { class: "catalog-heading" }, [
                        h("div", { class: "catalog-title-group" }, [
                          h(
                            "h1",
                            { id: "catalog-title" },
                            searched.value
                              ? "Результаты поиска"
                              : feedView.value === "saved"
                                ? "Смотреть позже"
                                : feedView.value === "topics"
                                  ? "Откройте своё"
                                  : category.value === "Все видео"
                                    ? "Обзор"
                                    : category.value,
                          ),
                          !busy.value
                            ? h(
                                "span",
                                { class: "video-count" },
                                filtered.value.length + " видео",
                              )
                            : null,
                        ]),
                        h(
                          "div",
                          {
                            class: "view-controls",
                            role: "group",
                            "aria-label": "Размер карточек",
                          },
                          [
                            h(
                              "button",
                              {
                                class: "preview-toggle",
                                "aria-label": "Превью при наведении",
                                "aria-pressed": previewsEnabled.value,
                                title: "Превью при наведении",
                                onClick: () => {
                                  previewsEnabled.value =
                                    !previewsEnabled.value;
                                  stopPreview();
                                },
                              },
                              [icon("play"), h("span", "Превью")],
                            ),
                            h(
                              "button",
                              {
                                "aria-label": "Обычные карточки",
                                "aria-pressed": !largeCards.value,
                                title: "Обычные карточки",
                                onClick: () => setCardSize(false),
                              },
                              [icon("grid")],
                            ),
                            h(
                              "button",
                              {
                                "aria-label": "Крупные карточки",
                                "aria-pressed": largeCards.value,
                                title: "Крупные карточки",
                                onClick: () => setCardSize(true),
                              },
                              [icon("largeGrid")],
                            ),
                          ],
                        ),
                      ]),
                      feedView.value === "saved"
                        ? h(
                            "p",
                            { class: "feed-note" },
                            "Видео, к которым хочется вернуться. Сохранены в этом браузере.",
                          )
                        : null,
                      feedView.value === "topics"
                        ? h(
                            "p",
                            { class: "feed-note" },
                            "От музыки до новых мест. Выберите то, что интересно вам.",
                          )
                        : null,
                      feedView.value === "feed" && topics.value.length
                        ? topicChips()
                        : null,
                      searched.value
                        ? h("div", { class: "search-summary" }, [
                            h("p", [
                              "По запросу ",
                              h("strong", "«" + searched.value + "»"),
                            ]),
                            h("button", { onClick: reset }, [
                              icon("close"),
                              "Сбросить",
                            ]),
                          ])
                        : null,
                      busy.value
                        ? skeleton()
                        : error.value
                          ? h("div", { class: "error-state" }, [
                              h("p", { role: "alert" }, error.value),
                              h(
                                "button",
                                { class: "action-button", onClick: load },
                                "Повторить",
                              ),
                            ])
                          : feedView.value === "topics" && topics.value.length
                            ? topicGrid()
                            : filtered.value.length
                              ? feed()
                              : h("div", { class: "empty-state" }, [
                                  h("span", { class: "empty-icon" }, [
                                    icon("search"),
                                  ]),
                                  h(
                                    "h2",
                                    searched.value
                                      ? "Ничего не найдено"
                                      : feedView.value === "saved"
                                        ? "Сохраните что-нибудь интересное"
                                        : "Здесь пока нет видео",
                                  ),
                                  h(
                                    "p",
                                    searched.value
                                      ? "Попробуйте другое название или вернитесь ко всем видео."
                                      : feedView.value === "saved"
                                        ? "Нажмите на закладку у ролика — он появится здесь."
                                        : "Опубликованные ролики появятся здесь.",
                                  ),
                                  searched.value ||
                                  category.value !== "Все видео" ||
                                  feedView.value === "saved"
                                    ? h(
                                        "button",
                                        {
                                          class: "action-button primary-button",
                                          onClick: reset,
                                        },
                                        "Показать все видео",
                                      )
                                    : null,
                                ]),
                    ],
                  ),
                ],
          ),
        ],
      );
  },
});
