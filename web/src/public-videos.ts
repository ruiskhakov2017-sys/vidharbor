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
  | "film";
function icon(kind: IconName, className = "") {
  const paths = {
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
            return [360, 480, 720].includes(shortSide)
              ? shortSide + "p · " + dimensions
              : dimensions;
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

    return () =>
      h("div", { class: "screening" }, [
        h("video", {
          class: "player",
          ref: element,
          controls: true,
          playsinline: true,
          preload: "metadata",
          poster: props.poster,
          "aria-label": "Видеоплеер",
          "data-active-level": active.value,
        }),
        levels.value.length
          ? h("label", { class: "quality" }, [
              h("span", "Качество"),
              h("select", { value: selection.value, onChange: changeQuality }, [
                h("option", { value: -1 }, "Auto · автоматически"),
                ...levels.value.map((label, index) =>
                  h("option", { value: index, key: index }, label),
                ),
              ]),
            ])
          : null,
        error.value ? h("p", { role: "alert" }, error.value) : null,
      ]);
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
    const topics = ref<string[]>([]);
    const busy = ref(true);
    const error = ref("");
    const failedImages = ref(new Set<string>());
    const desktop = window.matchMedia("(min-width: 1100px)");
    const menuOpen = ref(desktop.matches && !isWatch);
    const menuButton = ref<HTMLButtonElement>();
    const cinema = ref(false);
    const copied = ref("");
    const relatedTopic = ref(false);
    let requestId = 0;
    let copyTimer: number | undefined;
    const categories = computed(() => ["Все видео", ...topics.value]);
    const filtered = computed(() =>
      category.value === "Все видео"
        ? items.value
        : items.value.filter((item) => item.category === category.value),
    );
    const recommendations = computed(() =>
      items.value.filter(
        (item) =>
          item.id !== props.videoId &&
          (!relatedTopic.value || item.category === video.value?.category),
      ),
    );
    function catalogUrl(q = "", topic = "Все видео") {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
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
      desktop.removeEventListener("change", viewportChanged);
      window.removeEventListener("keydown", escapeMenu);
    });
    function submitSearch(event: Event) {
      event.preventDefault();
      if (isWatch) {
        window.location.assign(catalogUrl(query.value.trim()));
        return;
      }
      category.value = "Все видео";
      void load();
    }
    function reset() {
      query.value = "";
      category.value = "Все видео";
      void load();
    }
    function selectCategory(name: string) {
      if (isWatch) {
        window.location.assign(catalogUrl("", name));
        return;
      }
      category.value = name;
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
            icon("play"),
            h("span", "VidHarbor"),
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
      return h("li", { key: item.id }, [
        h("a", { href: item.watch_url, class: ["video-card", { compact }] }, [
          h("div", { class: "card-frame" }, [
            cover(item),
            duration(item.duration_seconds)
              ? h(
                  "span",
                  { class: "duration" },
                  duration(item.duration_seconds),
                )
              : null,
            h("span", { class: "hover-play", "aria-hidden": "true" }, [
              icon("play"),
            ]),
          ]),
          h("div", { class: "card-info" }, [
            avatar(item),
            h("div", { class: "card-text" }, [
              h("h3", item.title),
              h("p", { class: "card-creator" }, item.creator || "VidHarbor"),
              h("p", { class: "card-meta" }, [
                item.category || "Видео",
                item.demo ? h("span", { class: "demo-label" }, "Демо") : null,
              ]),
            ]),
          ]),
        ]),
      ]);
    }
    function topicIcon(name: string): IconName {
      const icons: Record<string, IconName> = {
        "Все видео": "home",
        Природа: "nature",
        Город: "city",
        Путешествия: "travel",
        Архитектура: "architecture",
        Кино: "film",
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
            h(
              "button",
              {
                class: [
                  "sidebar-item",
                  { selected: !isWatch && category.value === "Все видео" },
                ],
                "aria-pressed": !isWatch && category.value === "Все видео",
                onClick: () => selectCategory("Все видео"),
              },
              [icon("home"), "Все видео"],
            ),
            h("div", { class: "sidebar-divider" }),
            topics.value.length
              ? [
                  h("p", { class: "sidebar-label" }, "ТЕМЫ"),
                  ...topics.value.map((name) =>
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
                      [icon(topicIcon(name)), name],
                    ),
                  ),
                ]
              : null,
          ]),
          h("div", { class: "sidebar-bottom" }, [
            h("a", { href: "/", class: "sidebar-item" }, [
              icon("plus"),
              "Кабинет автора",
            ]),
            h("p", ["VidHarbor", h("span", "Видеоплатформа")]),
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
            type: "search",
            placeholder: "Поиск видео",
            maxlength: 200,
            value: query.value,
            onInput: (event: Event) => {
              query.value = (event.target as HTMLInputElement).value;
            },
          }),
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
    return () =>
      h(
        "div",
        {
          class: [
            "public-site",
            isWatch ? "watch-view" : "catalog-view",
            { "nav-open": menuOpen.value, "cinema-mode": cinema.value },
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
                  h("span", { class: "brand-mark" }, [icon("play")]),
                  h("span", "VidHarbor"),
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
                                h(
                                  "h2",
                                  { id: "related-title" },
                                  "Смотрите также",
                                ),
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
                  topicChips(),
                  h(
                    "section",
                    {
                      class: "catalog-section",
                      "aria-labelledby": "catalog-title",
                      "aria-busy": busy.value,
                    },
                    [
                      h("div", { class: "catalog-heading" }, [
                        h(
                          "h1",
                          { id: "catalog-title" },
                          searched.value ? "Результаты поиска" : category.value,
                        ),
                        !busy.value
                          ? h(
                              "span",
                              { class: "video-count" },
                              filtered.value.length + " видео",
                            )
                          : null,
                      ]),
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
                        ? h(
                            "div",
                            { class: "loading-state", role: "status" },
                            "Загружаем видео…",
                          )
                        : error.value
                          ? h("div", { class: "error-state" }, [
                              h("p", { role: "alert" }, error.value),
                              h(
                                "button",
                                { class: "action-button", onClick: load },
                                "Повторить",
                              ),
                            ])
                          : filtered.value.length
                            ? h(
                                "ul",
                                { class: "video-grid" },
                                filtered.value.map((item) => card(item)),
                              )
                            : h("div", { class: "empty-state" }, [
                                h("span", { class: "empty-icon" }, [
                                  icon("search"),
                                ]),
                                h(
                                  "h2",
                                  searched.value
                                    ? "Ничего не найдено"
                                    : "Здесь пока нет видео",
                                ),
                                h(
                                  "p",
                                  searched.value
                                    ? "Попробуйте другое название или вернитесь ко всем видео."
                                    : "Опубликованные ролики появятся здесь.",
                                ),
                                searched.value || category.value !== "Все видео"
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
