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
  | "screen";
function icon(kind: IconName, className = "") {
  const paths = {
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
              h("span", "Качество воспроизведения"),
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
    const items = ref<PublicVideo[]>([]);
    const video = ref<PublicVideo>();
    const query = ref(
      new URLSearchParams(window.location.search).get("q") ?? "",
    );
    const searched = ref(query.value);
    const busy = ref(true);
    const error = ref("");
    const category = ref("Все видео");
    const featuredIndex = ref(0);
    const cinema = ref(false);
    const copied = ref("");
    const failedImages = ref(new Set<string>());
    let requestId = 0;
    let copyTimer: number | undefined;
    const categories = computed(() => [
      "Все видео",
      ...new Set(
        items.value
          .map((item) => item.category)
          .filter((x): x is string => !!x),
      ),
    ]);
    const filtered = computed(() =>
      category.value === "Все видео"
        ? items.value
        : items.value.filter((item) => item.category === category.value),
    );
    const featured = computed(
      () => items.value[featuredIndex.value % Math.max(items.value.length, 1)],
    );
    const recommendations = computed(() =>
      items.value.filter((item) => item.id !== props.videoId).slice(0, 3),
    );

    async function load() {
      const request = ++requestId;
      const requestedQuery = query.value;
      busy.value = true;
      error.value = "";
      try {
        if (props.videoId) {
          video.value = await api<PublicVideo>(
            "/api/catalog/" + encodeURIComponent(props.videoId),
          );
          // Recommendations are optional; a catalogue failure must not hide the player.
          void api<{ videos: PublicVideo[] }>("/api/catalog")
            .then((result) => {
              items.value = result.videos;
            })
            .catch(() => {});
        } else {
          const params = new URLSearchParams({ q: requestedQuery });
          const result = await api<{ videos: PublicVideo[] }>(
            "/api/catalog?" + params,
          );
          if (request !== requestId) return;
          items.value = result.videos;
          searched.value = requestedQuery.trim();
          featuredIndex.value = 0;
          category.value = "Все видео";
          window.history.replaceState(
            null,
            "",
            "/catalog" + (requestedQuery ? "?" + params : ""),
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
    onMounted(load);
    onBeforeUnmount(() => {
      ++requestId;
      window.clearTimeout(copyTimer);
    });
    const reset = () => {
      query.value = "";
      category.value = "Все видео";
      void load();
    };
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
    function cover(item: PublicVideo, hero = false) {
      return item.thumbnail_url && !failedImages.value.has(item.id)
        ? h("img", {
            src: item.thumbnail_url,
            alt: "",
            loading: hero ? "eager" : "lazy",
            decoding: "async",
            onError: () => {
              failedImages.value = new Set([...failedImages.value, item.id]);
            },
          })
        : h("div", { class: "cover-fallback", "aria-hidden": "true" }, [
            h("span", "VH"),
            h("small", "ИСТОРИЯ НАЧИНАЕТСЯ С PLAY"),
          ]);
    }
    function card(item: PublicVideo, index: number) {
      return h("li", { key: item.id }, [
        h("a", { href: item.watch_url, class: "video-card" }, [
          h("div", { class: "card-frame" }, [
            cover(item),
            h("span", { class: "card-play" }, [icon("play")]),
            item.demo ? h("span", { class: "demo-label" }, "ДЕМОЭТЮД") : null,
            duration(item.duration_seconds)
              ? h(
                  "span",
                  { class: "duration" },
                  duration(item.duration_seconds),
                )
              : null,
          ]),
          h("div", { class: "card-caption" }, [
            h(
              "span",
              { class: "card-number" },
              String(index + 1).padStart(2, "0"),
            ),
            h("h3", item.title),
            icon("arrow"),
          ]),
          h("p", { class: "card-meta" }, [
            item.category || "Видео",
            h("span", " / "),
            item.creator || "VidHarbor",
          ]),
        ]),
      ]);
    }
    function search() {
      return h(
        "form",
        {
          class: "catalog-search",
          role: "search",
          onSubmit: (event: Event) => {
            event.preventDefault();
            void load();
          },
        },
        [
          h(
            "label",
            { for: "video-search", class: "sr-only" },
            "Поиск по названию видео",
          ),
          icon("search"),
          h("input", {
            id: "video-search",
            type: "search",
            placeholder: "Найти свою историю",
            value: query.value,
            maxlength: 200,
            onInput: (event: Event) => {
              query.value = (event.target as HTMLInputElement).value;
            },
          }),
          h(
            "button",
            {
              disabled: busy.value,
              "aria-label": "Найти видео",
              type: "submit",
            },
            [icon("arrow")],
          ),
        ],
      );
    }
    return () =>
      h("div", { class: ["public-site", { "cinema-mode": cinema.value }] }, [
        h(
          "a",
          { href: "#public-content", class: "skip-link" },
          "К содержимому",
        ),
        h("header", { class: "site-header" }, [
          h(
            "a",
            {
              class: "brand",
              href: "/catalog",
              "aria-label": "VidHarbor — каталог",
            },
            [h("span", { class: "brand-icon" }, [icon("play")]), "vidharbor"],
          ),
          h("nav", { "aria-label": "Основная навигация" }, [
            h(
              "a",
              {
                href: "/catalog",
                "aria-current": props.videoId ? undefined : "page",
              },
              "Смотреть",
            ),
            h("a", { class: "author-link", href: "/" }, [
              "Авторам",
              icon("arrow"),
            ]),
          ]),
          h("span", { class: "header-note" }, "ХОРОШИЕ ИСТОРИИ ЖИВУТ ЗДЕСЬ"),
        ]),
        h("main", { id: "public-content", class: "public-main" }, [
          props.videoId
            ? [
                h("div", { class: "watch-topline" }, [
                  h("a", { class: "back-link", href: "/catalog" }, [
                    icon("back"),
                    "В коллекцию",
                  ]),
                  h("span", "VIDHARBOR / ПРОСМОТР"),
                ]),
                video.value
                  ? h(
                      "section",
                      {
                        class: "watch-section",
                        "aria-labelledby": "watch-title",
                      },
                      [
                        h(Player, {
                          key: video.value.id,
                          source: video.value.playlist_url,
                          poster: video.value.thumbnail_url,
                        }),
                        h("div", { class: "watch-info" }, [
                          h("div", [
                            h("p", { class: "eyebrow" }, [
                              video.value.category || "ВИДЕО",
                              video.value.demo ? " / ДЕМОЭТЮД" : "",
                            ]),
                            h("h1", { id: "watch-title" }, video.value.title),
                            h("p", { class: "watch-creator" }, [
                              video.value.creator || "VidHarbor",
                              duration(video.value.duration_seconds)
                                ? " · " + duration(video.value.duration_seconds)
                                : "",
                            ]),
                            video.value.description
                              ? h(
                                  "p",
                                  { class: "watch-description" },
                                  video.value.description,
                                )
                              : null,
                          ]),
                          h("div", { class: "watch-actions" }, [
                            h(
                              "button",
                              { class: "outline-button", onClick: share },
                              [icon("share"), "Поделиться"],
                            ),
                            h(
                              "button",
                              {
                                class: "icon-button",
                                "aria-label": "Режим кинотеатра",
                                "aria-pressed": cinema.value,
                                onClick: () => {
                                  cinema.value = !cinema.value;
                                },
                              },
                              [icon("screen")],
                            ),
                            h(
                              "span",
                              { class: "share-status", role: "status" },
                              copied.value,
                            ),
                          ]),
                        ]),
                      ],
                    )
                  : null,
                recommendations.value.length
                  ? h(
                      "section",
                      {
                        class: "recommendations",
                        "aria-labelledby": "next-title",
                      },
                      [
                        h("div", { class: "collection-heading" }, [
                          h("h2", { id: "next-title" }, "Продолжить открывать"),
                          h("a", { href: "/catalog" }, "Вся коллекция ↗"),
                        ]),
                        h(
                          "ul",
                          { class: "video-grid" },
                          recommendations.value.map(card),
                        ),
                      ],
                    )
                  : null,
              ]
            : [
                h(
                  "section",
                  {
                    class: "catalog-masthead",
                    "aria-labelledby": "catalog-title",
                  },
                  [
                    h("div", [
                      h(
                        "p",
                        { class: "eyebrow" },
                        "НЕЗАВИСИМЫЙ ВЗГЛЯД. БЕСКОНЕЧНОЕ ВДОХНОВЕНИЕ.",
                      ),
                      h("h1", { id: "catalog-title" }, [
                        "Смотри ",
                        h("em", "шире."),
                        h(
                          "span",
                          { class: "title-spark", "aria-hidden": "true" },
                          h(
                            "svg",
                            {
                              viewBox: "0 0 24 24",
                              width: "1em",
                              height: "1em",
                              fill: "none",
                              stroke: "currentColor",
                              "stroke-width": 1.8,
                              "stroke-linecap": "round",
                            },
                            [
                              h("path", {
                                d: "M12 2v20M2 12h20M5 5l14 14M5 19 19 5",
                              }),
                            ],
                          ),
                        ),
                      ]),
                    ]),
                    h("div", { class: "masthead-right" }, [
                      h(
                        "p",
                        "Истории, ради которых стоит\nостановиться. И нажать play.",
                      ),
                      search(),
                    ]),
                  ],
                ),
                !searched.value && featured.value && !error.value
                  ? h(
                      "section",
                      { class: "featured", "aria-label": "В фокусе" },
                      [
                        cover(featured.value, true),
                        h("div", { class: "hero-shade" }),
                        h("div", { class: "hero-top" }, [
                          h("span", { class: "hero-badge" }, [
                            h("i"),
                            "В ФОКУСЕ",
                          ]),
                          h("span", "THE VIDHARBOR SELECTION"),
                        ]),
                        h("div", { class: "hero-content" }, [
                          h("p", { class: "eyebrow" }, [
                            featured.value.category || "ВИДЕО",
                            " / ",
                            featured.value.demo
                              ? "ВИЗУАЛЬНЫЙ ЭТЮД"
                              : "СМОТРЕТЬ ОНЛАЙН",
                          ]),
                          h("h2", featured.value.title),
                          h("div", { class: "hero-bottom" }, [
                            h(
                              "a",
                              {
                                href: featured.value.watch_url,
                                class: "primary-button",
                              },
                              [icon("play"), "Смотреть видео"],
                            ),
                            h(
                              "span",
                              { class: "hero-credit" },
                              featured.value.creator || "VidHarbor",
                            ),
                          ]),
                        ]),
                        h("div", { class: "hero-pagination" }, [
                          h("span", [
                            String(featuredIndex.value + 1).padStart(2, "0"),
                            h(
                              "small",
                              " / " +
                                String(
                                  Math.min(items.value.length, 3),
                                ).padStart(2, "0"),
                            ),
                          ]),
                          h(
                            "button",
                            {
                              class: "icon-button",
                              "aria-label": "Предыдущее видео в фокусе",
                              onClick: () => {
                                featuredIndex.value =
                                  (featuredIndex.value +
                                    Math.min(items.value.length, 3) -
                                    1) %
                                  Math.min(items.value.length, 3);
                              },
                            },
                            [icon("back")],
                          ),
                          h(
                            "button",
                            {
                              class: "icon-button",
                              "aria-label": "Следующее видео в фокусе",
                              onClick: () => {
                                featuredIndex.value =
                                  (featuredIndex.value + 1) %
                                  Math.min(items.value.length, 3);
                              },
                            },
                            [icon("next")],
                          ),
                        ]),
                      ],
                    )
                  : null,
                h(
                  "section",
                  {
                    class: "collection",
                    "aria-labelledby": "collection-title",
                    "aria-busy": busy.value,
                  },
                  [
                    h("div", { class: "collection-heading" }, [
                      h("h2", { id: "collection-title" }, [
                        searched.value
                          ? "Найденные истории"
                          : "Стоит вашего времени",
                        h(
                          "sup",
                          String(filtered.value.length).padStart(2, "0"),
                        ),
                      ]),
                      h(
                        "span",
                        { class: "collection-note" },
                        "МЕНЬШЕ ШУМА. БОЛЬШЕ СМЫСЛА.",
                      ),
                    ]),
                    categories.value.length > 1
                      ? h(
                          "div",
                          {
                            class: "category-tabs",
                            "aria-label": "Темы видео",
                          },
                          categories.value.map((name) =>
                            h(
                              "button",
                              {
                                "aria-pressed": category.value === name,
                                onClick: () => {
                                  category.value = name;
                                },
                              },
                              name,
                            ),
                          ),
                        )
                      : null,
                    busy.value
                      ? h(
                          "div",
                          { class: "loading-state", role: "status" },
                          "Собираем истории…",
                        )
                      : !error.value && filtered.value.length
                        ? h(
                            "ul",
                            { class: "video-grid" },
                            filtered.value.map(card),
                          )
                        : !error.value
                          ? h("div", { class: "empty-state" }, [
                              icon("search"),
                              h(
                                "h3",
                                searched.value
                                  ? "У этой истории пока нет кадра."
                                  : "Новые истории — впереди.",
                              ),
                              h(
                                "p",
                                searched.value
                                  ? "Ничего не найдено. Попробуйте другое название."
                                  : "Опубликованные видео появятся здесь.",
                              ),
                              searched.value
                                ? h(
                                    "button",
                                    { class: "primary-button", onClick: reset },
                                    "Вернуться ко всем видео",
                                  )
                                : null,
                            ])
                          : null,
                  ],
                ),
                items.value.length > 0 && !searched.value
                  ? h("div", { class: "closing-line" }, [
                      h("span", "Каждый кадр —"),
                      h("em", "новая точка зрения."),
                      icon("arrow"),
                    ])
                  : null,
              ],
          busy.value && props.videoId
            ? h(
                "p",
                { role: "status", class: "page-message" },
                "Готовим экран…",
              )
            : null,
          error.value
            ? h("div", { class: "page-message" }, [
                h("p", { role: "alert" }, error.value),
                h(
                  "button",
                  { onClick: load, class: "primary-button" },
                  "Повторить",
                ),
              ])
            : null,
        ]),
        h("footer", { class: "site-footer" }, [
          h("a", { class: "brand", href: "/catalog" }, "vidharbor"),
          h("span", "Место, где видео находят своего зрителя."),
          h("span", "PRESS PLAY. FEEL MORE."),
        ]),
      ]);
  },
});
